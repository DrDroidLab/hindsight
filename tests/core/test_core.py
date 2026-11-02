import os
import sqlite3
import subprocess
import uuid

import pytest

from hindsight.auth import AuthStore
from hindsight.config import Settings
from hindsight.models import Identity, KnowledgeError, dump_document, parse_document
from hindsight.redaction import Redactor
from hindsight.repository import Repository
from hindsight.search import SearchIndex

AUTHOR = Identity(
    "human", "Human Engineer", "human@example.test", ("shared", "alpha"), ("shared", "alpha")
)


def doc(title="Safe investigation", kind="clue", **extra):
    meta = {"id": str(uuid.uuid4()), "kind": kind, "title": title}
    if kind == "clue":
        meta.update(status="proposed", confidence=0.5)
    meta.update(extra)
    return dump_document(meta, "Investigate the memory queue.\n")


def git(path, *args):
    return (
        subprocess.check_output(["git", "-C", str(path), *args], stderr=subprocess.DEVNULL)
        .decode()
        .strip()
    )


def remote(tmp_path):
    path = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(path)],
        check=True,
        capture_output=True,
    )
    return path


def test_parse_and_path_validation():
    record = parse_document("clues/safe.md", doc())
    assert record.kind == "clue"
    for path in (
        "/clues/x.md",
        "../clues/x.md",
        "teams/../clues/x.md",
        "clues//x.md",
        ".git/config",
        "skills/demo/link.sh",
    ):
        with pytest.raises(KnowledgeError):
            parse_document(path, doc())
    with pytest.raises(KnowledgeError):
        parse_document("clues/x.md", "---\na: &a [*a]\n---\ntext")
    skill = parse_document(
        "skills/queue/SKILL.md",
        "---\nname: queue\ndescription: Safe skill\n---\n## Instructions\nRead only.",
    )
    assert skill.kind == "skill"
    with pytest.raises(KnowledgeError):
        parse_document("clues/x.md", doc(confidence=1.2))


def test_redaction_and_idempotence():
    redactor = Redactor()
    text = "human@example.test password=swordfish https://user:pass@host.internal 10.2.3.4 2001:db8::1 ghp_abcdefghijklmnopqrstuv"
    clean = redactor.redact(text)
    for raw in ("human@example.test", "swordfish", "user:pass", "10.2.3.4", "2001:db8::1", "ghp_"):
        assert raw not in clean
    assert redactor.redact(clean) == clean
    customers = "cus_9s8d7f6g5h cust_ab12 customer 12345 tenant id: a1b2c3 customer_id=zz99"
    clean = redactor.redact(customers)
    for raw in ("9s8d7f6g5h", "ab12", "12345", "a1b2c3", "zz99"):
        assert raw not in clean
    assert clean.count("[REDACTED:customer-id]") == 5
    assert redactor.redact(clean) == clean
    prose = "Customer service escalated; the tenant migration is done."
    assert redactor.redact(prose) == prose
    identifier = str(uuid.uuid4())
    assert (
        redactor.clean({"id": identifier, "created_at": "2026-01-01T00:00:00Z"})["id"] == identifier
    )


def test_auth_bootstrap_sessions_revocation(tmp_path):
    auth = AuthStore(tmp_path)
    bootstrap = auth.bootstrap()
    identity, key = auth.enroll(bootstrap, "Verified Human", "human@example.test", "alpha")
    assert auth.authenticate(key) == identity
    assert identity.can_read("shared") and not identity.can_write("beta")
    with pytest.raises(KnowledgeError):
        auth.enroll(bootstrap, "Other", "other@example.test", "beta")
    with pytest.raises(KnowledgeError):
        auth.bootstrap()
    token, csrf, _ = auth.create_session(key)
    assert auth.authenticate_session(token) == identity
    assert auth.check_csrf(token, csrf)
    assert not auth.check_csrf(token, "incorrect")
    auth.revoke_key(key[:12])
    with pytest.raises(KnowledgeError):
        auth.authenticate(key)
    with pytest.raises(KnowledgeError):
        auth.authenticate_session(token)
    database = (tmp_path / "auth.sqlite").read_bytes()
    assert (
        key.encode() not in database
        and token.encode() not in database
        and csrf.encode() not in database
    )
    assert os.stat(tmp_path / "auth.sqlite").st_mode & 0o777 == 0o600


def test_repository_roundtrip_conflict_sync_and_author(tmp_path):
    origin = remote(tmp_path)
    repo = Repository(Settings(tmp_path / "data", str(origin))).initialize()
    text = doc()
    commit = repo.write({"clues/first.md": text}, AUTHOR, "Capture", {"clues/first.md": ""})
    assert repo.head() == commit
    assert (
        git(repo.path, "show", "-s", "--format=%an <%ae>|%cn", commit)
        == "Human Engineer <human@example.test>|Hindsight"
    )
    with pytest.raises(KnowledgeError) as conflict:
        repo.write({"clues/first.md": doc()}, AUTHOR, "bad", {"clues/first.md": ""})
    assert conflict.value.status == 409
    assert repo.sync()["status"] == "synced"
    second = Repository(Settings(tmp_path / "second", str(origin))).initialize()
    assert second.documents()[0].body == repo.documents()[0].body
    assert second.documents()[0].revision == repo.documents()[0].revision
    assert second.sync_state["status"] == "synced"
    second.settings.read_only = True
    with pytest.raises(KnowledgeError):
        second.write({"clues/second.md": doc()}, AUTHOR, "No")


def test_scope_duplicate_redact_and_untrusted_tree(tmp_path):
    repo = Repository(Settings(tmp_path / "data")).initialize()
    with pytest.raises(KnowledgeError):
        repo.write({"teams/beta/clues/x.md": doc()}, AUTHOR, "Denied")
    text = doc(title="password=swordfish")
    repo.write({"clues/x.md": text}, AUTHOR, "Capture")
    assert "swordfish" not in repo.documents()[0].meta["title"]
    assert b"swordfish" not in subprocess.check_output(
        ["git", "-C", str(repo.path), "show", "HEAD:clues/x.md"]
    )
    with pytest.raises(KnowledgeError):
        repo.write(
            {"clues/y.md": dump_document(repo.documents()[0].meta, "Duplicate")}, AUTHOR, "Bad"
        )
    (repo.path / "clues" / "x.md").unlink()
    (repo.path / "clues" / "x.md").symlink_to("/etc/passwd")
    assert repo.documents()[0].kind == "clue"  # reads git objects, never symlink content


def test_git_review_mode(tmp_path):
    repo = Repository(Settings(tmp_path / "data", review_mode="git")).initialize()
    repo.proposal_write({"clues/x.md": doc()}, AUTHOR, "Proposal")
    assert not repo.documents()
    assert repo.documents(include_proposals=True)[0].meta["status"] == "proposed"
    with pytest.raises(KnowledgeError):
        repo.write({"clues/x.md": doc(status="approved")}, AUTHOR, "Approve")
    with pytest.raises(KnowledgeError):
        repo.proposal_write({"clues/x.md": doc(status="approved")}, AUTHOR, "Approve")


def test_scoped_fts_safe_query_and_rebuild(tmp_path):
    records = [
        parse_document("clues/a.md", doc(title="Queue failure", status="approved"), "rev1"),
        parse_document("teams/beta/clues/b.md", doc(title="Queue secret beta"), "rev2"),
        parse_document("clues/c.md", doc(title="Queue rejected", status="rejected"), "rev3"),
    ]
    index = SearchIndex(tmp_path)
    index.rebuild(records, "head1")
    result = index.recall(AUTHOR, query='queue OR "* : NEAR(')
    assert len(result["results"]) == 1
    assert result["results"][0]["source_revision"] == "rev1"
    assert result["index_revision"] == "head1"
    assert not index.recall(AUTHOR, scope="beta")["results"]
    index.path.unlink()
    index.rebuild(records, "head1")
    assert len(index.recall(AUTHOR)["results"]) == 1


def test_divergence_merge_and_content_conflict(tmp_path):
    origin = remote(tmp_path)
    first = Repository(Settings(tmp_path / "first", str(origin))).initialize()
    first.write({"clues/base.md": doc()}, AUTHOR, "Base")
    first.sync()
    second = Repository(Settings(tmp_path / "second", str(origin))).initialize()
    first.write({"clues/first.md": doc()}, AUTHOR, "First independent")
    second.write({"clues/second.md": doc()}, AUTHOR, "Second independent")
    assert second.sync()["status"] == "synced"
    assert first.sync()["status"] == "synced"
    assert len(first.documents()) == 3
    original = first.documents()[0]
    first.write({original.path: dump_document(original.meta, "First edit")}, AUTHOR, "First edit")
    second.write(
        {original.path: dump_document(original.meta, "Second edit")}, AUTHOR, "Second edit"
    )
    assert second.sync()["status"] == "synced"
    saved = first.head()
    assert first.sync()["status"] == "conflict"
    assert first.head() == saved
    with pytest.raises(KnowledgeError):
        first.write({"clues/blocked.md": doc()}, AUTHOR, "Blocked")


def test_hostile_remote_retains_valid_head(tmp_path):
    origin = remote(tmp_path)
    first = Repository(Settings(tmp_path / "first", str(origin))).initialize()
    first.write({"clues/base.md": doc()}, AUTHOR, "Base")
    first.sync()
    saved = first.head()
    # Deliberately bypass Hindsight to emulate an upstream malicious contributor.
    bad_blob = (
        subprocess.check_output(
            ["git", "-C", str(origin), "hash-object", "-w", "--stdin"], input=b"/etc/passwd"
        )
        .decode()
        .strip()
    )
    bad_tree = (
        subprocess.check_output(
            ["git", "-C", str(origin), "mktree"], input=f"120000 blob {bad_blob}\tlink\n".encode()
        )
        .decode()
        .strip()
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Bad",
        "GIT_AUTHOR_EMAIL": "bad@example.test",
        "GIT_COMMITTER_NAME": "Bad",
        "GIT_COMMITTER_EMAIL": "bad@example.test",
    }
    bad_commit = (
        subprocess.check_output(
            ["git", "-C", str(origin), "commit-tree", bad_tree, "-p", saved],
            input=b"Unsafe remote",
            env=env,
        )
        .decode()
        .strip()
    )
    subprocess.run(
        ["git", "-C", str(origin), "update-ref", "refs/heads/main", bad_commit], check=True
    )
    assert first.sync()["status"] == "conflict"
    assert first.head() == saved
    assert len(first.documents()) == 1
    assert not (first.path / "link").exists()


def test_semantic_scope_prefilter_and_fallback(tmp_path):
    class Embeddings:
        model = "fixture"

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    records = [
        parse_document("clues/a.md", doc(title="Allowed"), "one"),
        parse_document("teams/beta/clues/b.md", doc(title="Hidden"), "two"),
    ]
    index = SearchIndex(tmp_path, embedding_provider=Embeddings())
    index.rebuild(records, "revision")
    assert [r["title"] for r in index.recall(AUTHOR, query="synonym")["results"]] == ["Allowed"]
    index.embedding_provider.embed = lambda _: (_ for _ in ()).throw(RuntimeError("synthetic"))
    assert index.recall(AUTHOR, query="allowed")["results"][0]["title"] == "Allowed"


def test_vector_cache_model_scope_and_rules_invalidation(tmp_path):
    class Embeddings:
        model = "model-one"
        endpoint = "http://localhost"
        dimension = None
        calls = []

        def embed(self, texts):
            self.calls.append(texts)
            self.dimension = 2
            return [[1.0, 0.0] for _ in texts]

    provider = Embeddings()
    shared = parse_document("clues/a.md", doc(title="Safe queue"), "one")
    private = parse_document("teams/alpha/clues/b.md", doc(title="Safe queue"), "two")
    index = SearchIndex(tmp_path / "index", embedding_provider=provider)
    index.rebuild([shared, private], "first")
    assert len(provider.calls) == 1
    index.rebuild([shared, private], "second")
    assert len(provider.calls) == 1
    with sqlite3.connect(index.path) as db:
        assert db.execute("SELECT count(*) FROM embedding_cache").fetchone()[0] == 2
    provider.model = "model-two"
    index.rebuild([shared, private], "third")
    assert len(provider.calls) == 2
    rules = tmp_path / "rules.yaml"
    rules.write_text("rules:\n  - name: custom\n    pattern: Safe\n")
    index.redactor = Redactor(rules)
    index.rebuild([shared, private], "fourth")
    assert len(provider.calls) == 3
    assert "Safe" not in str(provider.calls[-1])


def test_configuration_pairs_and_alias(monkeypatch, tmp_path):
    monkeypatch.setenv("HINDSIGHT_DATA_DIR", str(tmp_path))
    assert Settings.from_env().data_dir == tmp_path
    with pytest.raises(ValueError):
        Settings(llm_endpoint="http://localhost:1234")
    with pytest.raises(ValueError):
        Settings(embeddings_endpoint="http://public.test", embeddings_model="model")


def test_model_unsafe_links_sources_and_steps():
    with pytest.raises(KnowledgeError):
        parse_document("clues/a.md", doc(source={"transcript_path": "/private/session"}))
    with pytest.raises(KnowledgeError):
        parse_document("incidents/a.md", doc(kind="incident", links=["javascript:alert(1)"]))
    text = dump_document(
        {
            "id": str(uuid.uuid4()),
            "kind": "runbook",
            "title": "Safe",
            "steps": [{"id": "first", "heading": "Inspect queue"}],
        },
        "## Inspect queue\nInspect only.",
    )
    assert parse_document("runbooks/a.md", text).meta["steps"][0]["id"] == "first"


def test_skill_safe_attachment_listing_and_unchanged_text(tmp_path):
    repo = Repository(Settings(tmp_path / "data")).initialize()
    skill_text = "---\nname: safe-skill\ndescription: Read queue context\n---\n## Instructions\nRead the queue.\n"
    repo.write({"skills/demo/SKILL.md": skill_text}, AUTHOR, "Add skill")
    _, texts = repo._tree(repo.head())
    texts["skills/demo/references/queue.txt"] = "Safe queue reference.\n"
    objects = {
        path: repo._git("hash-object", "-w", "--stdin", input=text.encode()).stdout.decode().strip()
        for path, text in texts.items()
    }
    tree = repo._make_tree(objects)
    commit = (
        repo._git("commit-tree", tree, "-p", repo.head(), input=b"Add safe attachment")
        .stdout.decode()
        .strip()
    )
    repo._git("update-ref", repo._ref(), commit)
    records = repo.documents()
    assert len(records) == 1
    assert records[0].meta["files"] == ["SKILL.md", "references/queue.txt"]
    loaded = repo.skill_file(records[0], "references/queue.txt")
    assert loaded["path"] == "references/queue.txt"
    assert loaded["content"] == "Safe queue reference.\n"
    assert loaded["revision"] == objects["skills/demo/references/queue.txt"]
    assert repo.skill_file(records[0], "SKILL.md")["content"] == skill_text
    for unsafe in (
        "../SKILL.md",
        "/etc/passwd",
        "references/../../SKILL.md",
        "references//queue.txt",
        "references\\queue.txt",
        "missing.txt",
    ):
        with pytest.raises(KnowledgeError) as error:
            repo.skill_file(records[0], unsafe)
        assert error.value.status == 404
    # A malicious materialized link never becomes the source of returned text.
    attachment = repo.path / "skills/demo/references/queue.txt"
    attachment.parent.mkdir(parents=True, exist_ok=True)
    attachment.symlink_to("/etc/passwd")
    assert (
        repo.skill_file(records[0], "references/queue.txt")["content"] == "Safe queue reference.\n"
    )
    assert repo._git("show", commit + ":skills/demo/SKILL.md").stdout.decode() == skill_text
    records[0].meta["title"] = "Mutated in memory"
    assert repo.documents()[0].meta["title"] == "safe-skill"
    with pytest.raises(KnowledgeError):
        parse_document("skills/demo/run.sh", "#!/bin/sh\necho unsafe")


def test_git_mode_allows_incidents_and_outcomes(tmp_path):
    repo = Repository(Settings(tmp_path / "data", review_mode="git")).initialize()
    repo.write({"incidents/demo.md": doc(kind="incident", state="open")}, AUTHOR, "Open incident")
    assert repo.documents()[0].kind == "incident"
    with pytest.raises(KnowledgeError):
        repo.write({"runbooks/demo.md": doc(kind="runbook")}, AUTHOR, "Bypass review")


def test_nested_cross_team_reference_rejected(tmp_path):
    repo = Repository(Settings(tmp_path / "data")).initialize()
    private = parse_document("teams/alpha/runbooks/private.md", doc(kind="runbook"))
    with pytest.raises(KnowledgeError):
        repo.write(
            {
                private.path: dump_document(private.meta, private.body),
                "clues/shared.md": doc(promotion={"runbook_id": private.id}),
            },
            AUTHOR,
            "Cannot leak nested reference",
        )
    assert not repo.head()


def test_persisted_conflict_stays_blocked_when_remote_offline(tmp_path):
    origin = remote(tmp_path)
    data = tmp_path / "data"
    first = Repository(Settings(data, str(origin))).initialize()
    first.write({"clues/x.md": doc()}, AUTHOR, "First")
    first.sync()
    first._private_json(first._conflict_path, {"blocked": True})
    missing = tmp_path / "missing.git"
    restarted = Repository(Settings(data, str(missing))).initialize()
    assert restarted.sync_state["status"] == "conflict"
    with pytest.raises(KnowledgeError):
        restarted.write({"clues/y.md": doc()}, AUTHOR, "Still blocked")


def test_public_origin_validation():
    assert (
        Settings(public_origin="https://knowledge.example.test/").public_origin
        == "https://knowledge.example.test"
    )
    assert Settings(public_origin="http://localhost:8080").public_origin == "http://localhost:8080"
    for origin in (
        "http://public.test",
        "https://user:pass@public.test",
        "https://public.test/path",
        "https://public.test?query=1",
        "https://public.test#fragment",
        "https://public.test\n",
    ):
        with pytest.raises(ValueError):
            Settings(public_origin=origin)


@pytest.mark.parametrize("limit", ["files", "text", "listing"])
@pytest.mark.parametrize("review_mode", ["local", "git"])
def test_write_rejects_aggregate_limits_before_mutation(tmp_path, monkeypatch, limit, review_mode):
    import hindsight.repository as repository_module

    repo = Repository(Settings(tmp_path / "data", review_mode=review_mode)).initialize()
    base_path = "incidents/base.md"
    base_text = doc(kind="incident")
    repo.write({base_path: base_text}, AUTHOR, "Existing valid content")
    head = repo.head()
    state = repo.sync_state
    new_path = "clues/second.md"
    new_text = doc(title="Unicode queue café")
    oid_length = len(head)
    if limit == "files":
        monkeypatch.setattr(repository_module, "MAX_TREE_FILES", 1)
    elif limit == "text":
        monkeypatch.setattr(
            repository_module,
            "MAX_TREE_TEXT_BYTES",
            len(base_text.encode()) + len(new_text.encode()) - 1,
        )
    else:
        actual_base_listing = repo._git("ls-tree", "-rz", "--full-tree", head).stdout
        extra_entry_size = len(new_path.encode()) + oid_length + len(b"100644 blob \t\0")
        monkeypatch.setattr(
            repository_module,
            "MAX_TREE_LISTING_BYTES",
            len(actual_base_listing) + extra_entry_size - 1,
        )
    before_objects = repo._git("count-objects", "-v").stdout
    proposal_ref = repo._resolve(repo._ref(repo.settings.proposal_branch))
    writer = repo.proposal_write if review_mode == "git" else repo.write
    with pytest.raises(KnowledgeError, match="tree exceeds"):
        writer({new_path: new_text}, AUTHOR, "Must reject atomically", {new_path: ""})
    assert repo.head() == head
    assert repo._resolve(repo._ref(repo.settings.proposal_branch)) == proposal_ref
    assert repo.sync_state == state
    assert [r.path for r in repo.documents()] == [base_path]
    assert (repo.path / base_path).read_text() == base_text
    assert not (repo.path / new_path).exists()
    assert not (repo.settings.private_dir / "operation.json").exists()
    assert repo._git("count-objects", "-v").stdout == before_objects


def test_queued_writer_rechecks_conflict_after_acquiring_lock(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager

    repo = Repository(Settings(tmp_path / "data")).initialize()
    repo.write({"clues/base.md": doc()}, AUTHOR, "Initial state")
    initial_head = repo.head()
    real_lock = repo.lock
    waiting = threading.Event()

    @contextmanager
    def observed_lock():
        # _write has passed its fast pre-lock checks when it reaches this point.
        waiting.set()
        with real_lock():
            yield

    monkeypatch.setattr(repo, "lock", observed_lock)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with real_lock():
            queued = executor.submit(
                repo.write, {"clues/queued.md": doc()}, AUTHOR, "Queued writer"
            )
            assert waiting.wait(timeout=5)
            # Emulate sync detecting/persisting conflict while holding the same lock.
            repo._blocked = True
            repo._private_json(repo._conflict_path, {"blocked": True})
            repo._state.update(status="conflict", error="Operator reconciliation required")
            conflict_state = repo.sync_state
        with pytest.raises(KnowledgeError) as error:
            queued.result(timeout=5)
        assert error.value.status == 409
    assert repo.head() == initial_head
    assert repo.sync_state == conflict_state
    assert repo._blocked
    assert repo._conflict_path.exists()
    assert not (repo.path / "clues/queued.md").exists()


def test_write_tree_exact_limit_boundaries_remain_readable(tmp_path, monkeypatch):
    import hindsight.repository as repository_module

    repo = Repository(Settings(tmp_path / "data")).initialize()
    first = doc(title="Queue café")
    second = doc(title="Queue résumé")
    repo.write({"clues/first.md": first}, AUTHOR, "First")
    initial_listing = repo._git("ls-tree", "-rz", "--full-tree", repo.head()).stdout
    expected_listing_size = (
        len(initial_listing) + len(b"clues/second.md") + len(repo.head()) + len(b"100644 blob \t\0")
    )
    monkeypatch.setattr(repository_module, "MAX_TREE_FILES", 2)
    monkeypatch.setattr(
        repository_module, "MAX_TREE_TEXT_BYTES", len(first.encode()) + len(second.encode())
    )
    monkeypatch.setattr(repository_module, "MAX_TREE_LISTING_BYTES", expected_listing_size)
    repo.write({"clues/second.md": second}, AUTHOR, "Exact limit")
    assert (
        len(repo._git("ls-tree", "-rz", "--full-tree", repo.head()).stdout) == expected_listing_size
    )
    repo._tree_cache.clear()
    assert len(repo.documents()) == 2
