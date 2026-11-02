"""Git-object-first knowledge storage. No remote checkout, hooks or filters run.

Existing remote objects/history may already contain secrets: quarantining a fetched
unsafe tree does not erase those objects or sanitize upstream history.
"""

import copy
import json
import os
import stat
import subprocess
import threading
import uuid
from contextlib import contextmanager
from typing import Any

from filelock import FileLock

from .models import Identity, KnowledgeError, Record, dump_document, parse_document, validate_path
from .redaction import Redactor

SERVICE = Identity("hindsight", "Hindsight", "hindsight@localhost", ("shared",), ("shared",))
MAX_TREE_FILES = 10_000
MAX_TREE_LISTING_BYTES = 4_000_000
MAX_TREE_TEXT_BYTES = 32 * 1024 * 1024


class Repository:
    def __init__(self, settings, redactor=None):
        self.settings = settings
        self.path = settings.repo_dir
        self.redactor = redactor or Redactor(settings.redaction_rules or None)
        self._thread_lock = threading.RLock()
        settings.private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if settings.private_dir.is_symlink():
            raise KnowledgeError("Unsafe private directory")
        os.chmod(settings.private_dir, 0o700)
        self._file_lock = FileLock(str(settings.private_dir / "knowledge.lock"))
        self._credential_helpers = []
        helper_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        # Inherit only explicitly trusted operator credential helpers, never global
        # aliases, filters, hooks, URL rewrites or repository-local configuration.
        for source in ("--system", "--global"):
            try:
                helpers = subprocess.run(
                    ["git", "config", source, "--get-all", "credential.helper"],
                    cwd=settings.private_dir,
                    env=helper_env,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                if helpers.returncode == 0:
                    self._credential_helpers.extend(helpers.stdout.decode().splitlines())
            except (OSError, UnicodeError, subprocess.TimeoutExpired):
                continue
        self._state = {"status": "initializing", "pending": False, "error": None}
        self._blocked = False
        self._tree_cache = {}
        self._conflict_path = settings.private_dir / "sync-conflict.json"
        if self._conflict_path.exists():
            self._blocked = True
            self._state.update(status="conflict", error="Operator reconciliation required")

    def _private_json(self, path, value):
        temporary = self.settings.private_dir / (".state-" + uuid.uuid4().hex)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(value, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @property
    def sync_state(self):
        return dict(self._state)

    @contextmanager
    def lock(self):
        with self._thread_lock, self._file_lock:
            yield

    def _git(self, *args, input=None, author=None, check=True):
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("GIT_"):
                env.pop(key)
        env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_TERMINAL_PROMPT="0",
            GIT_LFS_SKIP_SMUDGE="1",
            GIT_AUTHOR_NAME=(author or SERVICE).name,
            GIT_AUTHOR_EMAIL=(author or SERVICE).email,
            GIT_COMMITTER_NAME=SERVICE.name,
            GIT_COMMITTER_EMAIL=SERVICE.email,
        )
        command = [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.file.allow=always",
            "-c",
            "protocol.https.allow=always",
            "-c",
            "protocol.ssh.allow=always",
            "-c",
            "submodule.recurse=false",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.sshCommand=ssh -oBatchMode=yes",
            "-c",
            "credential.interactive=false",
            "-C",
            str(self.path),
        ]
        for helper in self._credential_helpers:
            command += ["-c", "credential.helper=" + helper]
        command += args
        try:
            result = subprocess.run(
                command, input=input, capture_output=True, env=env, timeout=30, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise KnowledgeError("Git operation unavailable", 503) from exc
        if check and result.returncode:
            raise KnowledgeError(
                "Git operation failed; operator reconciliation may be required", 409
            )
        return result

    def _ref(self, branch=None):
        return "refs/heads/" + (branch or self.settings.git_branch)

    def _resolve(self, ref):
        result = self._git("rev-parse", "--verify", ref, check=False)
        return result.stdout.decode().strip() if result.returncode == 0 else ""

    def head(self):
        return self._resolve(self._ref())

    def initialize(self):
        with self.lock():
            self.path.mkdir(parents=True, exist_ok=True)
            if self.path.is_symlink():
                raise KnowledgeError("Unsafe repository directory")
            if not (self.path / ".git").exists():
                self._git("init", "--initial-branch=" + self.settings.git_branch)
            elif (self.path / ".git").is_symlink():
                raise KnowledgeError("Unsafe git directory")
            # This clone is exclusively managed by Hindsight; never ingest external local config.
            config = self.path / ".git" / "config"
            if config.is_symlink():
                raise KnowledgeError("Unsafe git config")
            config.write_text(
                "[core]\n\trepositoryformatversion = 0\n\tbare = false\n\tlogallrefupdates = true\n"
            )
            if self.head():
                _, texts = self._tree(self.head())
                self._activate(texts)
                self._state.update(status="local", error=None)
            if self.settings.git_remote:
                self.sync()
                if not self.head() and self._blocked:
                    raise KnowledgeError("No validated knowledge tree; remote quarantined", 409)
                if not self.head() and self._state["status"] == "offline":
                    raise KnowledgeError("Initial knowledge remote is unavailable", 503)
            elif not self.head():
                self._state.update(status="local-only", error=None)
            journal = self.settings.private_dir / "operation.json"
            if journal.exists():
                # Canonical refs decide whether the interrupted transaction committed.
                if self.head():
                    _, texts = self._tree(self.head())
                    self._activate(texts)
                journal.unlink()
        return self

    def _tree(self, revision):
        if not revision:
            return [], {}
        cache_key = (revision, self.redactor.version)
        if cache_key in self._tree_cache:
            return copy.deepcopy(self._tree_cache[cache_key])
        listing = self._git("ls-tree", "-rz", "--full-tree", revision).stdout
        if len(listing) > MAX_TREE_LISTING_BYTES or listing.count(b"\0") > MAX_TREE_FILES:
            raise KnowledgeError("Knowledge tree exceeds limits")
        records, texts, ids = [], {}, set()
        total_size = 0
        for entry in listing.split(b"\0"):
            if not entry:
                continue
            try:
                descriptor, raw_path = entry.split(b"\t", 1)
                mode, kind, oid = descriptor.decode("ascii").split()
                path = raw_path.decode("utf-8")
            except (ValueError, UnicodeError) as exc:
                raise KnowledgeError("Invalid git tree encoding") from exc
            if mode != "100644" or kind != "blob":
                raise KnowledgeError("Only regular nonexecutable knowledge files are allowed")
            _, path_kind = validate_path(path)
            if self.redactor.redact(path) != path:
                raise KnowledgeError("Knowledge paths must not contain sensitive identifiers")
            size = int(self._git("cat-file", "-s", oid).stdout)
            total_size += size
            if total_size > MAX_TREE_TEXT_BYTES:
                raise KnowledgeError("Knowledge tree exceeds total text limit")
            if size > 262144:
                raise KnowledgeError("Knowledge file exceeds limit")
            try:
                text = self._git("cat-file", "blob", oid).stdout.decode("utf-8")
            except UnicodeError as exc:
                raise KnowledgeError("Knowledge content must be UTF-8") from exc
            if path_kind == "skill_attachment":
                if (
                    any(ord(c) < 32 and c not in "\n\r\t" for c in text)
                    or self.redactor.redact(text) != text
                ):
                    raise KnowledgeError("Unsafe skill attachment; fetched tree quarantined")
                texts[path] = text
                continue
            record = parse_document(path, text, oid)
            # Unsafe imports are rejected, never silently rewritten or indexed.
            if (
                self.redactor.clean(record.meta) != record.meta
                or self.redactor.redact(record.body) != record.body
            ):
                raise KnowledgeError("Remote content requires redaction; fetched tree quarantined")
            if record.id in ids:
                raise KnowledgeError("Duplicate knowledge ID")
            ids.add(record.id)
            records.append(record)
            texts[path] = text
        self._validate_references(records)
        self._attach_files(records, texts)
        if len(self._tree_cache) >= 2:
            self._tree_cache.pop(next(iter(self._tree_cache)))
        self._tree_cache[cache_key] = copy.deepcopy((records, texts))
        return records, texts

    @staticmethod
    def _attach_files(records, texts):
        skills = {r.path.rsplit("/", 1)[0]: r for r in records if r.kind == "skill"}
        for record in skills.values():
            record.meta["files"] = ["SKILL.md"]
        for path in texts:
            if validate_path(path)[1] != "skill_attachment":
                continue
            parts = path.split("/")
            skill_root = "/".join(parts[:4] if parts[0] == "teams" else parts[:2])
            if skill_root not in skills:
                raise KnowledgeError("Skill attachment has no SKILL.md")
            skills[skill_root].meta["files"].append(path[len(skill_root) + 1 :])
        for record in skills.values():
            record.meta["files"].sort()

    @staticmethod
    def _validate_references(records):
        by_id = {r.id: r for r in records}
        # Validate typed references recursively, including promotion/review evidence.
        kinds = {
            "service_ids": "service",
            "dependencies": "service",
            "runbook_ids": "runbook",
            "runbook_id": "runbook",
            "clue_ids": "clue",
            "clue_id": "clue",
            "incident_id": "incident",
            "incident_ids": "incident",
            "outcome_id": "outcome",
            "outcome_ids": "outcome",
        }

        def walk(value, record):
            if isinstance(value, list):
                for item in value:
                    walk(item, record)
            elif isinstance(value, dict):
                for key, child in value.items():
                    if key in kinds and child is not None:
                        values = [child] if isinstance(child, str) else child
                        if not isinstance(values, list):
                            raise KnowledgeError("Invalid typed reference")
                        for identifier in values:
                            if not isinstance(identifier, str):
                                raise KnowledgeError("Invalid typed reference")
                            target = by_id.get(identifier)
                            if (
                                not target
                                or target.kind != kinds[key]
                                or target.scope not in ("shared", record.scope)
                            ):
                                raise KnowledgeError("Invalid or cross-scope reference")
                    else:
                        walk(child, record)

        for record in records:
            walk(record.meta, record)

    def _activate(self, texts):
        # Traverse through no-follow directory descriptors, not check-then-open paths.
        for path, text in texts.items():
            directory = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            temporary = ".hindsight-" + uuid.uuid4().hex
            try:
                parts = path.split("/")
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=directory)
                    except FileExistsError:
                        pass
                    child = os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                    )
                    os.close(directory)
                    directory = child
                try:
                    mode = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False).st_mode
                    if not stat.S_ISREG(mode):
                        raise KnowledgeError("Unsafe activation file")
                except FileNotFoundError:
                    pass
                fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                try:
                    with os.fdopen(fd, "w") as stream:
                        stream.write(text)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, parts[-1], src_dir_fd=directory, dst_dir_fd=directory)
                    os.fsync(directory)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=directory)
                    except FileNotFoundError:
                        pass
            except OSError as exc:
                raise KnowledgeError("Unsafe or unavailable activation path", 409) from exc
            finally:
                os.close(directory)
        # Content reads always use validated git blobs, not mutable working files.

    def documents(self, include_proposals=False):
        with self.lock():
            records, _ = self._tree(self.head())
            if include_proposals and self.settings.review_mode == "git":
                proposal = self._resolve(self._ref(self.settings.proposal_branch))
                if proposal:
                    proposals, _ = self._tree(proposal)
                    trusted = {r.id: r for r in records}
                    for record in proposals:
                        if record.id not in trusted:
                            if record.kind != "clue" or record.meta.get("status") != "proposed":
                                raise KnowledgeError("Proposal branch contains untrusted approval")
                            records.append(record)
            return records

    def revision(self, path):
        validate_path(path)
        for record in self.documents(include_proposals=True):
            if record.path == path:
                return record.revision
        return ""

    def skill_file(self, skill: Record, relative_path: str) -> dict:
        """Load allowlisted text from git; caller must authorize the skill first.

        Never consult the materialized filesystem, follow symlinks, or execute a
        skill. Recheck the current record and its listing under the repository lock
        so a stale/forged listing cannot widen the directory boundary.
        """
        with self.lock():
            if (
                not isinstance(relative_path, str)
                or not relative_path
                or relative_path.startswith("/")
                or "\\" in relative_path
                or any(part in ("", ".", "..") for part in relative_path.split("/"))
                or relative_path not in skill.meta.get("files", [])
            ):
                raise KnowledgeError("Skill file not found", 404)
            revision = self.head()
            records, texts = self._tree(revision)
            current = next(
                (
                    r
                    for r in records
                    if r.id == skill.id
                    and r.kind == "skill"
                    and r.path == skill.path
                    and r.scope == skill.scope
                ),
                None,
            )
            if not current or relative_path not in current.meta.get("files", []):
                raise KnowledgeError("Skill file not found", 404)
            if current.revision != skill.revision:
                raise KnowledgeError("Skill revision changed; reload current skill", 409)
            path = current.path.rsplit("/", 1)[0] + "/" + relative_path
            validate_path(path)
            if path not in texts:
                raise KnowledgeError("Skill file not found", 404)
            blob_revision = (
                self._git("rev-parse", "--verify", revision + ":" + path).stdout.decode().strip()
            )
            return {"path": relative_path, "content": texts[path], "revision": blob_revision}

    def write(self, changes, author, message, expected=None):
        if self.settings.review_mode == "git":
            existing = {r.path: r for r in self.documents()}
            for path, text in changes.items():
                record = parse_document(path, text)
                old = existing.get(path)
                stale_transition = (
                    author.id == SERVICE.id
                    and old
                    and old.kind == "clue"
                    and old.meta.get("status") in ("approved", "stale")
                    and record.meta.get("status") == "stale"
                    and old.body == record.body
                )
                if record.kind not in ("incident", "outcome") and not stale_transition:
                    raise KnowledgeError(
                        "Git review mode requires proposal branch and normal-git review", 403
                    )
        return self._write(changes, author, message, expected, self.settings.git_branch)

    def proposal_write(self, changes, author, message, expected=None):
        if self.settings.review_mode != "git":
            return self.write(changes, author, message, expected)
        trusted = {r.id: r for r in self.documents()}
        for path, text in changes.items():
            record = parse_document(path, text)
            if record.kind != "clue" or record.meta.get("status") != "proposed":
                raise KnowledgeError("Only proposed clues may be written to proposal branch", 403)
            if record.id in trusted or path in {r.path for r in trusted.values()}:
                raise KnowledgeError(
                    "Trusted knowledge must be changed through normal-git review", 403
                )
        return self._write(changes, author, message, expected, self.settings.proposal_branch)

    def _write(self, changes, author, message, expected, branch):
        if self.settings.read_only:
            raise KnowledgeError("Knowledge instance is read-only", 403)
        if self._blocked:
            raise KnowledgeError("Knowledge writes suspended pending reconciliation", 409)
        if not changes or len(changes) > 100:
            raise KnowledgeError("Invalid knowledge transaction")
        with self.lock():
            # Sync can discover a conflict while this writer waits for the lock.
            # Never let a queued write clear that state or advance a canonical ref.
            if self._blocked:
                raise KnowledgeError("Knowledge writes suspended pending reconciliation", 409)
            parent = self._resolve(self._ref(branch)) or self.head()
            records, texts = self._tree(parent)
            if branch == self.settings.proposal_branch and self.settings.review_mode == "git":
                # New trusted incidents/services may have arrived since the last capture.
                # Include their authoritative blobs in the proposal snapshot without
                # granting proposal-only changes any trusted status.
                _, trusted_texts = self._tree(self.head())
                texts.update(trusted_texts)
            revisions = {r.path: r.revision for r in records}
            for path, revision in (expected or {}).items():
                if revisions.get(path, "") != revision:
                    raise KnowledgeError("Revision conflict; reload current knowledge", 409)
            for path, text in changes.items():
                record = parse_document(path, text)
                if self.redactor.redact(path) != path:
                    raise KnowledgeError("Knowledge paths must not contain sensitive identifiers")
                if not author.can_write(record.scope):
                    raise KnowledgeError("Write scope not permitted", 403)
                # Identity is authoritative private input; content is redacted independently.
                from .auth import AuthStore

                AuthStore._validate(author.name, author.email, author.write_scopes)
                clean_meta, clean_body = (
                    self.redactor.clean(record.meta),
                    self.redactor.redact(record.body),
                )
                texts[path] = (
                    text
                    if clean_meta == record.meta and clean_body == record.body
                    else dump_document(clean_meta, clean_body)
                )
            oid_length = (
                len(parent)
                if parent
                else len(self._git("hash-object", "--stdin", input=b"").stdout.decode().strip())
            )
            self._validate_tree_limits(texts, oid_length)
            validated = [
                parse_document(p, t)
                for p, t in texts.items()
                if validate_path(p)[1] != "skill_attachment"
            ]
            if len({r.id for r in validated}) != len(validated):
                raise KnowledgeError("Duplicate knowledge ID")
            self._validate_references(validated)
            self._attach_files(validated, texts)
            # Temporary private index; never git add . or execute a worktree filter.
            objects = {}
            for path, text in texts.items():
                objects[path] = (
                    self._git("hash-object", "-w", "--stdin", input=text.encode())
                    .stdout.decode()
                    .strip()
                )
            tree = self._make_tree(objects)
            journal = self.settings.private_dir / "operation.json"
            self._private_json(journal, {"parent": parent, "branch": branch, "phase": "prepared"})
            args = ["commit-tree", tree]
            if parent:
                args += ["-p", parent]
            safe_message = self.redactor.redact(message)[:1024]
            commit = (
                self._git(*args, input=safe_message.encode(), author=author).stdout.decode().strip()
            )
            old = self._resolve(self._ref(branch)) or ("0" * 40)
            self._git("update-ref", self._ref(branch), commit, old)
            # The commit is durable even if activation/indexing later fails.
            self._private_json(journal, {"commit": commit, "branch": branch, "phase": "committed"})
            activation_error = None
            if branch == self.settings.git_branch:
                try:
                    self._activate(texts)
                except (KnowledgeError, OSError):
                    # A durable canonical commit is still saved. Leave recovery journal;
                    # do not invite the caller to retry an already committed operation.
                    self._blocked = True
                    activation_error = "Committed knowledge saved; working files require repair"
            if not activation_error:
                journal.unlink()
            self._state.update(status="pending", pending=True, error=None)
            if activation_error:
                self._state.update(status="conflict", error=activation_error)
            return commit

    @staticmethod
    def _validate_tree_limits(texts, oid_length):
        """Apply reader limits to the complete post-transaction UTF-8 tree.

        All allowed leaves have mode 100644/type blob, so this is the exact size
        of the recursive NUL-delimited ls-tree listing used by the reader.
        Run before writing objects, journals, commits or refs.
        """
        if len(texts) > MAX_TREE_FILES:
            raise KnowledgeError("Knowledge tree exceeds limits")
        listing_size = sum(
            len(path.encode("utf-8")) + oid_length + len(b"100644 blob \t\0") for path in texts
        )
        if listing_size > MAX_TREE_LISTING_BYTES:
            raise KnowledgeError("Knowledge tree exceeds limits")
        if sum(len(text.encode("utf-8")) for text in texts.values()) > MAX_TREE_TEXT_BYTES:
            raise KnowledgeError("Knowledge tree exceeds total text limit")

    def _make_tree(self, objects):
        root: dict[str, Any] = {}
        for path, oid in objects.items():
            node = root
            parts = path.split("/")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = oid

        def build(node):
            entries = []
            for name, value in sorted(node.items()):
                if isinstance(value, dict):
                    entries.append(f"040000 tree {build(value)}\t{name}\0".encode())
                else:
                    entries.append(f"100644 blob {value}\t{name}\0".encode())
            return self._git("mktree", "-z", input=b"".join(entries)).stdout.decode().strip()

        return build(root)

    def _ancestor(self, first, second):
        return self._git("merge-base", "--is-ancestor", first, second, check=False).returncode == 0

    def sync(self):
        with self.lock():
            if not self.settings.git_remote:
                self._state.update(status="local-only", error=None)
                return self.sync_state
            try:
                self._sync_branch(self.settings.git_branch, activate=True)
                if self.settings.review_mode == "git":
                    self._sync_branch(self.settings.proposal_branch, activate=False)
                self._blocked = False
                self._conflict_path.unlink(missing_ok=True)
                self._state.update(status="synced", pending=False, error=None)
            except KnowledgeError as exc:
                if (
                    "conflict" in str(exc).lower()
                    or "quarantined" in str(exc).lower()
                    or "Duplicate" in str(exc)
                    or "Invalid" in str(exc)
                ):
                    self._blocked = True
                    self._private_json(
                        self._conflict_path, {"branch": self.settings.git_branch, "blocked": True}
                    )
                    self._state.update(
                        status="conflict",
                        error="Remote tree or branch requires operator reconciliation",
                    )
                elif "push unavailable" in str(exc):
                    self._state.update(
                        status="local-only",
                        pending=True,
                        error="Push unavailable; local commits pending",
                    )
                elif self._blocked:
                    self._state.update(
                        status="conflict",
                        error="Operator reconciliation required; remote unavailable",
                    )
                else:
                    self._state.update(
                        status="offline",
                        error="Remote unavailable or push not permitted",
                        pending=bool(self.head()),
                    )
            return self.sync_state

    def _sync_branch(self, branch, activate):
        local = self._resolve(self._ref(branch))
        remote_ref = "refs/remotes/hindsight/" + branch
        # Missing branch is normal for a new/empty remote; distinguish via ls-remote.
        listing = self._git("ls-remote", "--heads", self.settings.git_remote, self._ref(branch))
        remote = ""
        if listing.stdout.strip():
            self._git(
                "fetch",
                "--no-tags",
                "--no-recurse-submodules",
                self.settings.git_remote,
                "+" + self._ref(branch) + ":" + remote_ref,
            )
            remote = self._resolve(remote_ref)
            try:
                remote_records, remote_texts = self._tree(remote)
                if not activate:
                    trusted_ids = {r.id for r in self._tree(self.head())[0]}
                    if any(
                        r.id not in trusted_ids
                        and (r.kind != "clue" or r.meta.get("status") != "proposed")
                        for r in remote_records
                    ):
                        raise KnowledgeError("Proposal approval conflict")
            except KnowledgeError as exc:
                raise KnowledgeError(
                    "Remote validation conflict; fetched tree quarantined", 409
                ) from exc
            if not local or self._ancestor(local, remote):
                self._git("update-ref", self._ref(branch), remote, local or "0" * 40)
                local = remote
                if activate:
                    self._activate(remote_texts)
            elif not self._ancestor(remote, local):
                if self.settings.read_only:
                    raise KnowledgeError("Read-only branch divergence conflict", 409)
                base = self._git("merge-base", local, remote, check=False)
                if base.returncode:
                    raise KnowledgeError("Unrelated branch history conflict", 409)
                # merge-tree does not checkout files or invoke merge drivers.
                merged = self._git("merge-tree", "--write-tree", local, remote, check=False)
                if merged.returncode:
                    raise KnowledgeError("Remote content conflict", 409)
                tree = merged.stdout.decode().splitlines()[0]
                _, texts = self._tree(tree)
                merge = (
                    self._git(
                        "commit-tree",
                        tree,
                        "-p",
                        local,
                        "-p",
                        remote,
                        input=b"Merge validated remote knowledge",
                    )
                    .stdout.decode()
                    .strip()
                )
                self._git("update-ref", self._ref(branch), merge, local)
                local = merge
                if activate:
                    self._activate(texts)
        if local and not self.settings.read_only:
            pushed = self._git(
                "push",
                "--porcelain",
                self.settings.git_remote,
                self._ref(branch) + ":" + self._ref(branch),
                check=False,
            )
            if pushed.returncode:
                self._state.update(status="local-only", pending=True)
                raise KnowledgeError("Git push unavailable", 503)
