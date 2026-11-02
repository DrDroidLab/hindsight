"""Offline schema/payload contracts, NOT real-agent certification.

References verified 2026-11-02: code.claude.com/docs/en/hooks,
cursor.com/docs/hooks, learn.chatgpt.com/docs/hooks (official redirect),
geminicli.com/docs/hooks/reference/. No real agent executables installed.
"""

import json
import os
import subprocess
import sys
import tomllib

import pytest

from hindsight.integrations import capture, capture_hook, install, retry_spool, store_credentials
from hindsight.integrations.installers import _hook


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home with spaces"
    home.mkdir()
    store_credentials("http://localhost:8123", "synthetic-private-key", home=home)
    return home


@pytest.mark.parametrize("agent", ["claude", "cursor", "codex", "gemini"])
def test_install_contract(agent, home):
    agent_dir = home / ("." + agent)
    agent_dir.mkdir()
    if agent == "codex":
        config_path = agent_dir / "config.toml"
        config_path.write_text('model = "unrelated"\n')
    else:
        config_path = (
            home / ".claude.json"
            if agent == "claude"
            else agent_dir / ("mcp.json" if agent == "cursor" else "settings.json")
        )
        config_path.write_text(
            json.dumps({"unrelated": True, "mcpServers": {"existing": {"command": "other"}}})
        )
    hook_path = agent_dir / ("hooks.json" if agent in ("cursor", "codex") else "settings.json")
    if hook_path != config_path:
        hook_path.write_text(
            json.dumps({"unrelated": True, "hooks": {"custom": [{"command": "other"}]}})
        )
    original = config_path.read_text()
    dry = install(agent, "http://localhost:8123", home, dry_run=True)
    assert dry["changes"] and config_path.read_text() == original
    install(agent, "http://localhost:8123", home)
    assert not install(agent, "http://localhost:8123", home, check=True)["changes"]
    assert not install(agent, "http://localhost:8123", home)["changes"]
    if agent == "codex":
        config = tomllib.loads(config_path.read_text())
        assert config["model"] == "unrelated"
        assert config["mcp_servers"]["hindsight-sre"]["args"] == ["mcp-proxy"]
    else:
        config = json.loads(config_path.read_text())
        assert config["unrelated"] is True
        assert config["mcpServers"]["existing"]["command"] == "other"
        assert config["mcpServers"]["hindsight-sre"]["args"] == ["mcp-proxy"]
    hooks = json.loads(hook_path.read_text())
    for event, groups in _hook(agent).items():
        assert groups[0] in hooks["hooks"][event]
    if agent == "cursor":
        assert hooks["version"] == 1
        assert set(_hook(agent)) == {"stop", "sessionEnd"}
    if agent == "gemini":
        assert hooks["hooks"]["AfterAgent"][0]["hooks"][0]["timeout"] == 1000
    for path in home.rglob("*"):
        if path.is_file() and ".config/hindsight" not in str(path):
            assert "synthetic-private-key" not in path.read_text()
    install(agent, "http://localhost:8123", home, uninstall=True)
    if agent == "codex":
        assert tomllib.loads(config_path.read_text()) == {"model": "unrelated"}
    else:
        assert "hindsight-sre" not in json.loads(config_path.read_text()).get("mcpServers", {})
    assert (home / ".config/hindsight/credentials.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("agent", ["claude", "cursor", "codex", "gemini"])
def test_capture_redacted_replay(agent, home, monkeypatch):
    transcript_dir = home / capture.ROOTS[agent]
    transcript_dir.mkdir(parents=True, exist_ok=True)
    path = transcript_dir / "synthetic-session.jsonl"
    message = {
        "role": "assistant",
        "content": "Investigated customer alice@example.com on 192.168.1.7; safe fact.",
    }
    if agent == "claude":
        data = json.dumps({"type": "assistant", "message": message})
    elif agent == "gemini":
        data = json.dumps({"messages": [dict(message, type="gemini")]})
    elif agent == "codex":
        data = json.dumps({"type": "response_item", "payload": dict(message, type="message")})
    else:
        data = "assistant:\n" + message["content"]
    path.write_text(data)
    event = {
        "session_id": "synthetic-session",
        "conversation_id": "synthetic-session",
        "transcript_path": str(path),
    }
    submitted = []
    monkeypatch.setattr(capture, "_send", lambda payload, credentials: False)
    response = capture_hook(agent, event, home)
    assert response["status"] == "spooled"
    spool = list((home / ".config/hindsight/spool").glob("*.json"))
    assert len(spool) == 1
    assert "alice@example.com" not in spool[0].read_text()
    assert "192.168.1.7" not in spool[0].read_text()
    assert str(path) not in spool[0].read_text()
    assert capture_hook(agent, event, home)["status"] in ("duplicate", "duplicate-or-empty")
    monkeypatch.setattr(
        capture, "_send", lambda payload, credentials: submitted.append(payload) or True
    )
    assert retry_spool(home)["submitted"] == 1
    assert submitted[0]["capture_id"] == response["capture_id"]
    assert retry_spool(home)["submitted"] == 0


def test_missing_transcript_and_security(home, tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "_send", lambda *args: True)
    assert (
        capture_hook("cursor", {"conversation_id": "a", "transcript_path": None}, home)["status"]
        == "explicit-summary-required"
    )
    assert (
        capture_hook("claude", {"session_id": "a", "last_assistant_message": "safe fact"}, home)[
            "status"
        ]
        == "submitted"
    )
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"role":"assistant","content":"must not read"}')
    root = home / ".claude/projects"
    root.mkdir(parents=True)
    link = root / "link.jsonl"
    link.symlink_to(outside)
    for path in (outside, link):
        assert (
            capture_hook("claude", {"session_id": "a", "transcript_path": str(path)}, home)[
                "status"
            ]
            == "capture-unavailable"
        )
    large = root / "large.jsonl"
    large.write_text("x" * (capture.MAX_BYTES + 1))
    assert (
        capture_hook("claude", {"session_id": "a", "transcript_path": str(large)}, home)["status"]
        == "capture-unavailable"
    )


def test_ambiguous_existing_and_symlink(home, tmp_path):
    config = home / ".claude.json"
    config.write_text('{"mcpServers":{"hindsight-sre":{"command":"unrelated"}}}')
    with pytest.raises(ValueError):
        install("claude", "http://localhost:8123", home)
    config.unlink()
    config.symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError):
        install("claude", "http://localhost:8123", home)


def test_credential_permissions_and_remote_origin(home):
    path = home / ".config/hindsight/credentials.json"
    os.chmod(path, 0o644)
    assert capture_hook("claude", {"session_id": "a"}, home)["status"] == "capture-unavailable"
    with pytest.raises(ValueError):
        store_credentials("http://remote.example", "key", home=home)
    with pytest.raises(ValueError):
        store_credentials("https://key:secret@example.com", "key", home=home)


@pytest.mark.parametrize("agent", ["claude", "cursor", "codex", "gemini"])
def test_symlinked_transcript_root_rejected(agent, home, tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "_send", lambda *args: pytest.fail("Must not submit"))
    root = home / capture.ROOTS[agent]
    root.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "external-transcripts"
    outside.mkdir()
    root.symlink_to(outside, target_is_directory=True)
    path = root / "session.jsonl"
    path.write_text('{"role":"assistant","content":"untrusted"}')
    assert (
        capture_hook(agent, {"session_id": "x", "transcript_path": str(path)}, home)["status"]
        == "capture-unavailable"
    )
    assert not list((home / ".config/hindsight").glob("checkpoint-*.json"))


def test_checkpoint_incremental_and_final_response_flush(home, monkeypatch):
    root = home / ".claude/projects"
    root.mkdir(parents=True)
    path = root / "session.jsonl"
    path.write_text(json.dumps({"message": {"role": "assistant", "content": "first fact"}}) + "\n")
    submissions = []
    monkeypatch.setattr(
        capture, "_send", lambda payload, credentials: submissions.append(payload) or True
    )
    event = {
        "session_id": "x",
        "transcript_path": str(path),
        "last_assistant_message": "second fact",
    }
    assert capture_hook("claude", event, home)["status"] == "submitted"
    with path.open("a") as stream:
        stream.write(
            json.dumps({"message": {"role": "assistant", "content": "second fact"}}) + "\n"
        )
    assert capture_hook("claude", event, home)["status"] == "duplicate-or-empty"
    with path.open("a") as stream:
        stream.write(json.dumps({"message": {"role": "assistant", "content": "third fact"}}) + "\n")
    event.pop("last_assistant_message")
    assert capture_hook("claude", event, home)["status"] == "submitted"
    assert len(submissions) == 2
    assert "third fact" in submissions[1]["excerpt"]
    assert "first fact" not in submissions[1]["excerpt"]


def test_spool_exists_before_delivery_and_destination_is_bound(home, monkeypatch):
    def delivery(payload, credentials):
        assert len(list((home / ".config/hindsight/spool").glob("*.json"))) == 1
        return False

    monkeypatch.setattr(capture, "_send", delivery)
    event = {"session_id": "x", "summary": "safe fact"}
    result = capture_hook("claude", event, home)
    assert result["status"] == "spooled"
    checkpoint = next((home / ".config/hindsight").glob("checkpoint-*.json"))
    checkpoint.unlink()  # Simulate recovery from a lost checkpoint after durable spool.
    assert capture_hook("claude", event, home)["capture_id"] == result["capture_id"]
    assert len(list((home / ".config/hindsight/spool").glob("*.json"))) == 1
    store_credentials("http://localhost:9999", "other-key", home=home)
    monkeypatch.setattr(capture, "_send", lambda *args: pytest.fail("Wrong destination"))
    assert retry_spool(home)["submitted"] == 0


def test_spool_expiry_and_tool_payload_exclusion(home, monkeypatch):
    monkeypatch.setattr(capture, "_send", lambda *args: False)
    assert (
        capture_hook("cursor", {"conversation_id": "x", "summary": "safe fact"}, home)["status"]
        == "spooled"
    )
    spool_path = next((home / ".config/hindsight/spool").glob("*.json"))
    item = json.loads(spool_path.read_text())
    item["created"] = 0
    spool_path.write_text(json.dumps(item))
    assert retry_spool(home)["expired"] == 1
    root = home / ".cursor/projects"
    root.mkdir(parents=True)
    path = root / "session.txt"
    path.write_text(
        "assistant:\n[Tool call] run_terminal_cmd\nSECRET_TOOL_PAYLOAD\nuser:\nSafe human statement"
    )
    assert (
        capture_hook("cursor", {"conversation_id": "y", "transcript_path": str(path)}, home)[
            "status"
        ]
        == "spooled"
    )
    assert (
        "SECRET_TOOL_PAYLOAD"
        not in next((home / ".config/hindsight/spool").glob("*.json")).read_text()
    )


def test_fifo_transcript_does_not_block(home):
    root = home / ".claude/projects"
    root.mkdir(parents=True)
    path = root / "fifo.jsonl"
    os.mkfifo(path)
    assert (
        capture_hook("claude", {"session_id": "x", "transcript_path": str(path)}, home)["status"]
        == "capture-unavailable"
    )


def test_backup_is_private_sanitized_and_dry_run_has_no_side_effects(home):
    config = home / ".claude.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other": {
                        "command": "unrelated-secret-command",
                        "env": {"CUSTOM": "unstructured-secret"},
                    }
                },
                "theme": "light",
            }
        )
    )
    install("claude", "http://localhost:8123", home, dry_run=True)
    assert not (home / ".config/hindsight/backups").exists()
    install("claude", "http://localhost:8123", home)
    backup = next((home / ".config/hindsight/backups").glob("*.json"))
    assert backup.stat().st_mode & 0o777 == 0o600
    assert "unstructured-secret" not in backup.read_text()
    assert "unrelated-secret-command" not in backup.read_text()
    assert json.loads(backup.read_text())["theme"] == "light"
    assert (
        json.loads(config.read_text())["mcpServers"]["other"]["env"]["CUSTOM"]
        == "unstructured-secret"
    )


@pytest.mark.parametrize("agent", ["claude", "cursor", "codex", "gemini"])
def test_generated_hook_cli_contract(agent, home):
    """Run the actual CLI entry function, not an installed proprietary client."""
    environment = dict(os.environ, HOME=str(home))
    event = {
        "session_id": "cli-session",
        "conversation_id": "cli-session",
        "summary": "Synthetic alice@example.com evidence",
    }
    result = subprocess.run(
        [sys.executable, "-c", "from hindsight.cli import main; main()", "hook", agent],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        env=environment,
        timeout=3,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == ("{}\n" if agent == "gemini" else "")
    assert result.stderr == ""
    assert (
        "alice@example.com"
        not in next((home / ".config/hindsight/spool").glob("*.json")).read_text()
    )


def test_cursor_nested_jsonl_and_tool_payload_filter(home, monkeypatch):
    root = home / ".cursor/projects"
    root.mkdir(parents=True)
    path = root / "session.jsonl"
    path.write_text(
        json.dumps(
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Safe fact"},
                        {"type": "tool_use", "input": "DO_NOT_CAPTURE"},
                        {"type": "tool_result", "content": "DO_NOT_CAPTURE"},
                    ]
                },
            }
        )
    )
    sent = []
    monkeypatch.setattr(capture, "_send", lambda payload, creds: sent.append(payload) or True)
    assert (
        capture_hook("cursor", {"conversation_id": "x", "transcript_path": str(path)}, home)[
            "status"
        ]
        == "submitted"
    )
    assert "Safe fact" in sent[0]["excerpt"]
    assert "DO_NOT_CAPTURE" not in sent[0]["excerpt"]
