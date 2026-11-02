"""Real local git, HTTP, and domain acceptance tests; no remote services or real secrets."""

import subprocess
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from hindsight.app import build_knowledge, create_app
from hindsight.config import Settings
from hindsight.models import KnowledgeError, dump_document


@pytest.fixture
def system(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("GIT_REMOTE", str(remote))
    monkeypatch.setenv("HINDSIGHT_DATA_DIR", str(tmp_path / "data"))
    settings = Settings.from_env()
    knowledge = build_knowledge(settings)
    user, key = knowledge.auth.create_user(
        "Example Reviewer", "reviewer@localhost", ("shared", "platform"), ("shared", "platform")
    )
    outsider, other_key = knowledge.auth.create_user(
        "Other Reviewer", "other@localhost", ("shared", "other"), ("other",)
    )
    app = create_app(settings, knowledge=knowledge, run_jobs=False)
    with TestClient(app) as client:
        yield knowledge, user, key, outsider, other_key, client


def capture(
    knowledge, user, scope="shared", summary="Restart the worker after rotating its lease", cid=None
):
    receipt = knowledge.capture(
        user,
        {
            "capture_id": cid or str(uuid4()),
            "scope": scope,
            "summary": summary,
            "agent": "generic",
            "session_id": "synthetic",
        },
    )
    return knowledge.get(user, receipt["clue_ids"][0], "clue")


def test_capture_review_reject_rebuild_and_authorship(system):
    knowledge, user, key, _, _, _ = system
    clue = capture(
        knowledge, user, summary="Lease renewals failed at 10.9.8.7; contact example@sample.test"
    )
    assert "10.9.8.7" not in clue.body
    assert "example@sample.test" not in clue.body
    assert "[REDACTED:" in clue.body
    updated = knowledge.review(
        user,
        clue.id,
        {
            "expected_revision": clue.revision,
            "decision": "reject",
            "reason": "This is not a confirmed fact",
        },
    )
    assert updated["status"] == "rejected"
    knowledge.rebuild()
    duplicate = capture(
        knowledge, user, summary="Lease renewals failed at 10.9.8.7; contact example@sample.test"
    )
    assert duplicate.id == clue.id
    assert not any(r["id"] == clue.id for r in knowledge.recall(user, query="Lease")["results"])
    # Verify directly, without depending on a private repository method.
    result = subprocess.run(
        ["git", "-C", str(knowledge.settings.data_dir / "repo"), "log", "-1", "--format=%an"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == user.name


def test_revisions_and_team_isolation(system):
    knowledge, user, key, outsider, other_key, client = system
    clue = capture(knowledge, user, scope="platform", summary="private-platform-fact")
    headers = {"Authorization": "Bearer " + other_key}
    assert client.get(f"/api/clues/{clue.id}", headers=headers).status_code == 404
    assert client.get("/api/review?scope=platform", headers=headers).json()["items"] == []
    assert not knowledge.recall(outsider, query="private-platform-fact")["results"]
    edit = knowledge.edit_clue(
        user, clue.id, {"expected_revision": clue.revision, "body": "Corrected fact"}
    )
    with pytest.raises(KnowledgeError) as error:
        knowledge.review(user, clue.id, {"expected_revision": clue.revision, "decision": "approve"})
    assert error.value.status == 409
    assert edit["revision"] != clue.revision
    request = client.post(
        "/api/captures",
        headers=headers,
        json={"capture_id": "attempt", "scope": "platform", "summary": "no"},
    )
    assert request.status_code == 403


def add_runbook(knowledge, user):
    meta = knowledge._new(
        user,
        "runbook",
        "Worker lease repair",
        "shared",
        steps=[{"id": "check-lease", "heading": "Check lease"}],
        service_ids=[],
        alerts=[],
    )
    path = knowledge._path(meta, "shared")
    knowledge._save(
        user,
        {path: dump_document(meta, "## Check lease\n\nInspect the lease age.\n")},
        "Add test runbook",
        {path: ""},
    )
    return knowledge.get(user, meta["id"], "runbook")


def test_approval_promotion_outcome_and_rebuild(system):
    knowledge, user, _, _, _, _ = system
    clue = capture(knowledge, user)
    approved = knowledge.review(
        user, clue.id, {"expected_revision": clue.revision, "decision": "approve"}
    )
    runbook = add_runbook(knowledge, user)
    payload = {
        "runbook_id": runbook.id,
        "expected_runbook_revision": runbook.revision,
        "expected_revision": approved["revision"],
        "operation_id": "promotion-test",
        "step_id": "restart-worker",
        "heading": "Restart worker",
        "content": "Restart one worker and confirm recovery.",
    }
    preview = knowledge.promotion(user, clue.id, payload)
    assert "+## Restart worker" in preview["diff"]
    promotion = knowledge.promotion(user, clue.id, payload, commit=True)
    replay = knowledge.promotion(user, clue.id, payload, commit=True)
    assert replay["revision"] == promotion["revision"]
    assert promotion["revision"] != approved["revision"]
    book = promotion["runbook"]
    outcome = {
        "idempotency_key": "outcome-one",
        "runbook_id": book["id"],
        "revision": book["revision"],
        "step_id": "restart-worker",
        "result": "success",
        "clue_ids": [clue.id],
        "note": "Recovery confirmed at 192.168.10.20",
    }
    result = knowledge.outcome(user, outcome)
    assert "192.168.10.20" not in result["body"]
    assert knowledge.outcome(user, outcome)["id"] == result["id"]
    knowledge.rebuild()
    assert knowledge.detail(user, clue.id)["outcomes"][0]["id"] == result["id"]
    assert knowledge.stale_check(datetime.now(UTC) + timedelta(days=91)) == 1
    assert knowledge.stale_check(datetime.now(UTC) + timedelta(days=92)) == 0
    stale = knowledge.get(user, clue.id)
    assert stale.meta["status"] == "stale"
    revalidated = knowledge.review(
        user, clue.id, {"expected_revision": stale.revision, "decision": "revalidate"}
    )
    assert revalidated["status"] == "approved"


def test_http_cookie_csrf_limits_and_no_input_echo(system):
    knowledge, user, key, _, _, client = system
    assert client.get("/api/whoami").status_code == 401
    logged = client.post("/api/session", json={"key": key})
    assert logged.status_code == 200
    csrf = logged.json()["csrf_token"]
    assert "HttpOnly" in logged.headers.get("set-cookie", "")
    assert client.get("/api/whoami").json()["id"] == user.id
    payload = {
        "capture_id": "cookie-test",
        "summary": "A safe operational lesson",
        "scope": "shared",
    }
    assert client.post("/api/captures", json=payload).status_code == 403
    created = client.post("/api/captures", json=payload, headers={"X-CSRF-Token": csrf})
    assert created.status_code == 201
    malicious = client.post("/api/captures", content=b"x" * 131073, headers={"X-CSRF-Token": csrf})
    assert malicious.status_code == 413
    assert client.get("/api/whoami", headers={"Host": "evil.test"}).status_code == 400
    assert (
        client.post(
            "/api/session", json={"key": key}, headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    invalid = client.post("/api/captures", content=b'{"secret":', headers={"X-CSRF-Token": csrf})
    assert invalid.status_code == 400 and "secret" not in invalid.text
    assert client.delete("/api/session", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/whoami").status_code == 401


def test_incidents_and_outcomes_require_visible_valid_refs(system):
    knowledge, user, _, outsider, _, _ = system
    incident = knowledge.incident(
        user,
        {
            "title": "Synthetic outage",
            "scope": "platform",
            "links": ["https://example.test/tickets/42"],
        },
    )
    with pytest.raises(KnowledgeError):
        knowledge.get(outsider, incident["id"], "incident")
    resolved = knowledge.incident(
        user, {"expected_revision": incident["revision"], "state": "resolved"}, incident["id"]
    )
    assert resolved["state"] == "resolved"
    runbook = add_runbook(knowledge, user)
    with pytest.raises(KnowledgeError):
        knowledge.outcome(
            user,
            {
                "idempotency_key": "bad-step",
                "runbook_id": runbook.id,
                "revision": runbook.revision,
                "step_id": "missing",
                "result": "success",
            },
        )


def test_mcp_http_real_sdk(system):
    import asyncio
    import socket
    import threading

    import httpx
    import uvicorn
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    knowledge, user, key, outsider, other_key, _ = system
    clue = capture(knowledge, user, summary="MCP test lease fact")
    private = capture(knowledge, user, scope="platform", summary="privateMCPfact")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(knowledge.settings, knowledge, run_jobs=False),
            access_log=False,
            log_level="critical",
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()

    async def check():
        async with httpx.AsyncClient(
            headers={"Authorization": "Bearer " + key}, timeout=10
        ) as http:
            # A bounded readiness condition, not an arbitrary sleep before the test.
            for _ in range(100):
                try:
                    response = await http.get(f"http://127.0.0.1:{port}/health")
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    await asyncio.sleep(0.02)
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {t.name for t in (await session.list_tools()).tools}
                    assert {"recall", "propose_clue", "report_outcome", "whoami"} <= names
                    result = await session.call_tool("recall", {"query": "lease"})
                    assert not result.isError
                    assert clue.id in str(result)
                    from mcp.shared.exceptions import McpError

                    for method, value in (
                        ("prompt", "synthetic-private-prompt-value"),
                        ("resource", "https://synthetic-private-uri.test/token"),
                    ):
                        with pytest.raises(McpError) as error:
                            if method == "prompt":
                                await session.get_prompt(value)
                            else:
                                from pydantic import AnyUrl

                                await session.read_resource(AnyUrl(value))
                        assert value not in str(error.value)
        async with httpx.AsyncClient(
            headers={"Authorization": "Bearer " + other_key}, timeout=10
        ) as http:
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("whoami", {})
                    assert outsider.id in str(result) and user.id not in str(result)
                    result = await session.call_tool("recall", {"query": "privateMCPfact"})
                    assert not result.isError and private.id not in str(result)
        import os
        import sys

        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "hindsight.cli", "mcp-proxy"],
            env={
                **os.environ,
                "HINDSIGHT_API_KEY": key,
                "HINDSIGHT_SERVER": f"http://127.0.0.1:{port}",
            },
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.list_tools()
                result = await session.call_tool(
                    "propose_clue",
                    {
                        "capture_id": "bad-proxy-args",
                        "summary": {"secret": "synthetic-proxy-secret"},
                    },
                )
                assert result.isError
                assert "synthetic-proxy-secret" not in str(result)
                result = await session.call_tool("whoami", {})
                assert not result.isError and user.id in str(result)

    try:
        asyncio.run(check())
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_mcp_stdio_real_sdk(system):
    import asyncio
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    knowledge, user, key, _, _, _ = system
    clue = capture(knowledge, user, summary="Stdio transport retrieves worker clues")

    async def check():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "hindsight.cli", "mcp"],
            env={
                **os.environ,
                "HINDSIGHT_API_KEY": key,
                "PYTHONPATH": str(
                    __import__("pathlib").Path(__file__).resolve().parents[2] / "src"
                ),
            },
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("recall", {"query": "worker"})
                assert not result.isError
                assert clue.id in str(result)
                knowledge.auth.revoke_key(key[:12])
                denied = await session.call_tool("recall", {"query": "worker"})
                assert denied.isError
                assert clue.id not in str(denied)

    asyncio.run(check())


def test_mcp_validation_never_echoes_raw_input(system):
    import asyncio

    from hindsight.mcp_server import create_mcp

    knowledge, user, key, _, _, client = system
    secret = "synthetic-secret-value-never-echo-42"
    result = asyncio.run(
        create_mcp(knowledge, user).call_tool(
            "propose_clue", {"capture_id": "malformed", "summary": {"secret": secret}}
        )
    )
    assert result.isError
    assert secret not in str(result)
    response = client.post(
        "/mcp",
        headers={"Authorization": "Bearer " + key, "Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": {"secret": secret}},
        },
    )
    assert response.status_code == 400
    assert secret not in response.text


def test_extracted_children_do_not_reextract(system, monkeypatch):
    knowledge, user, _, _, _, _ = system
    knowledge.settings.llm_endpoint = "http://localhost:9999/v1"
    knowledge.settings.llm_model = "mock"

    class FakeProvider:
        def __init__(self, *args, **kwargs):
            pass

        def extract(self, payload):
            return [
                {"title": "First", "summary": "First extracted fact", "confidence": 0.7},
                {"title": "Second", "summary": "Second extracted fact", "confidence": 0.8},
            ]

    monkeypatch.setattr("hindsight.providers.ExtractionProvider", FakeProvider)
    clue = capture(knowledge, user)
    batches = []
    original_save = knowledge._save

    def saved_batch(identity, changes, message, expected=None):
        batches.append(len(changes))
        return original_save(identity, changes, message, expected)

    monkeypatch.setattr(knowledge, "_save", saved_batch)
    knowledge.extract(user, clue.id)
    assert batches == [2]
    records = knowledge.records(user, "clue")
    assert len(records) == 2
    assert all(r.meta["extraction_state"] == "complete" for r in records)
    assert any(r.meta.get("parent_clue_id") == clue.id for r in records)


def test_context_edit_updates_fingerprint(system):
    knowledge, user, _, _, _, _ = system
    clue = capture(knowledge, user)
    edited = knowledge.edit_clue(
        user, clue.id, {"expected_revision": clue.revision, "alerts": ["WorkerLeaseExpired"]}
    )
    assert edited["fingerprint"] != clue.meta["fingerprint"]
    result = knowledge.capture(
        user,
        {
            "capture_id": "edited-match",
            "scope": "shared",
            "summary": clue.body,
            "alerts": ["WorkerLeaseExpired"],
        },
    )
    assert result["clue_ids"] == [clue.id]


def test_staleness_includes_team_clues(system):
    knowledge, user, _, _, _, _ = system
    for scope in ("shared", "platform"):
        clue = capture(knowledge, user, scope=scope, summary=scope + " fact")
        knowledge.review(user, clue.id, {"expected_revision": clue.revision, "decision": "approve"})
    assert knowledge.stale_check(datetime.now(UTC) + timedelta(days=91)) == 2


def test_tls_proxy_public_origin_sets_secure_cookie(system):
    knowledge, _, key, _, _, client = system
    knowledge.settings.public_origin = "https://knowledge.example.test"
    response = client.post(
        "/api/session", json={"key": key}, headers={"Origin": "https://knowledge.example.test"}
    )
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_cli_effective_credentials_reject_insecure_override(tmp_path, monkeypatch):
    from hindsight.cli import client_credentials
    from hindsight.integrations import store_credentials

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    store_credentials("https://knowledge.example.test", "synthetic-key", home=home)
    monkeypatch.setenv("HINDSIGHT_SERVER", "http://nonloopback.example.test")
    with pytest.raises(KnowledgeError):
        client_credentials()
    monkeypatch.delenv("HINDSIGHT_SERVER")
    credential = home / ".config/hindsight/credentials.json"
    credential.chmod(0o644)
    with pytest.raises(ValueError):
        client_credentials()


def test_git_review_capture_visible_but_ui_mutations_blocked(system):
    knowledge, user, _, _, _, _ = system
    add_runbook(knowledge, user)
    knowledge.settings.review_mode = "git"
    clue = capture(knowledge, user, cid="git-mode-capture")
    assert not any(r.id == clue.id for r in knowledge.repo.documents())
    assert knowledge.review_queue(user)["items"][0]["id"] == clue.id
    assert any(
        r["id"] == clue.id and r["unreviewed"]
        for r in knowledge.recall(user, query="worker")["results"]
    )
    assert capture(knowledge, user, cid="git-mode-capture").id == clue.id
    with pytest.raises(KnowledgeError):
        knowledge.review(user, clue.id, {"expected_revision": clue.revision, "decision": "approve"})
    with pytest.raises(KnowledgeError):
        knowledge.edit_clue(user, clue.id, {"expected_revision": clue.revision, "body": "Changed"})
    knowledge.repo.sync()
    assert knowledge.repo.sync_state["status"] == "synced"
    incident = knowledge.incident(user, {"title": "Git reviewed incident", "scope": "shared"})
    assert incident["kind"] == "incident"
