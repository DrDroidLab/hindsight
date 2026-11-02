"""Bounded best-effort hooks. Persist only redacted payloads and opaque hashes."""

import fcntl
import hashlib
import json
import os
import re
import time
from pathlib import Path

import httpx

from hindsight.redaction import Redactor, strip_injected_context

from .installers import AGENTS, validate_server
from .storage import home_path, private_dir, read_json, read_text, safe_path, write_json

MAX_BYTES = 1_048_576
MAX_RECORDS = 2000
ROOTS = {
    "claude": ".claude/projects",
    "cursor": ".cursor/projects",
    "codex": ".codex/sessions",
    "gemini": ".gemini/tmp",
}


def _text(content):
    if isinstance(content, str):
        return content[:16000]
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "")[:8000]
            for item in content[:32]
            if isinstance(item, dict)
            and item.get("type") in ("text", "input_text", "output_text")
            and isinstance(item.get("text"), str)
        )
    return ""


def _messages(agent, event, home):
    path = event.get("transcript_path")
    if not path:
        return [], "explicit-summary-required"
    root = home / ROOTS[agent]
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("Transcript must be absolute")
    raw = read_text(path, root, MAX_BYTES)
    messages = []
    if agent == "gemini":
        parsed = json.loads(raw)
        records = parsed.get("messages", [])
    else:
        lines = raw.splitlines()
        if len(lines) > MAX_RECORDS:
            raise ValueError("Transcript record limit")
        records = []
        for line in lines:
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # Cursor plaintext transcripts have labeled speaker blocks.
                if agent != "cursor":
                    raise ValueError("Unsupported transcript format") from None
        if agent == "cursor" and not records:
            blocks = re.split(r"(?m)^(user|assistant):\s*$", raw)
            for index in range(1, len(blocks) - 1, 2):
                records.append({"role": blocks[index], "content": blocks[index + 1]})
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ValueError("Transcript record limit")
    for record in records:
        if not isinstance(record, dict):
            continue
        if agent == "codex":
            if record.get("type") != "response_item":
                continue
            message = record.get("payload", {})
            if message.get("type") != "message":
                continue
        elif agent == "claude":
            # Only conversation turns: skip API request copies and harness meta messages.
            if record.get("type") not in ("user", "assistant") or record.get("isMeta"):
                continue
            message = record.get("message", {})
        else:
            message = record.get("message", record)
        role = message.get("role", message.get("type", record.get("role")))
        if role not in ("user", "assistant", "gemini"):
            continue
        text = strip_injected_context(_text(message.get("content", "")))
        if (
            "[Tool call]" in text
            or "[Tool result]" in text
            or len(re.findall(r"(?m)^[A-Z_][A-Z0-9_]{2,}=", text)) >= 3
        ):
            continue
        if text:
            messages.append(("assistant" if role == "gemini" else role) + ": " + text)
    return messages, "transcript"


def _destination(credentials):
    return hashlib.sha256(
        json.dumps(
            [credentials["server"], credentials.get("scope", "shared"), credentials["key"]]
        ).encode()
    ).hexdigest()


def _send(payload, credentials):
    origin = validate_server(credentials["server"])
    try:
        # Response bodies are ignored: they can contain untrusted diagnostics.
        with (
            httpx.Client(timeout=httpx.Timeout(0.45), follow_redirects=False) as client,
            client.stream(
                "POST",
                origin + "/api/captures",
                json=payload,
                headers={"Authorization": "Bearer " + credentials["key"]},
            ) as response,
        ):
            return response.status_code in (200, 201)
    except (httpx.HTTPError, OSError, ValueError):
        return False


def capture_hook(agent, event, home=None):
    """Never raises or asks an agent to continue. Main prints {} for Gemini hooks."""
    if agent not in AGENTS or not isinstance(event, dict):
        return {"status": "ignored"}
    lock = None
    try:
        root = home_path(home)
        private = private_dir(home)
        lock_path = safe_path(private / "capture.lock", root)
        lock = os.open(lock_path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        credentials = read_json(private / "credentials.json", root, private=True)
        if not credentials.get("key"):
            return {"status": "credentials-required"}
        session = event.get("session_id") or event.get("conversation_id")
        if not isinstance(session, str) or not 0 < len(session) <= 256:
            return {"status": "session-required"}
        destination = _destination(credentials)
        session_hash = hashlib.sha256((agent + "\0" + session + destination).encode()).hexdigest()
        checkpoint = private / ("checkpoint-" + session_hash + ".json")
        state = read_json(checkpoint, root, private=True)
        messages, source = _messages(agent, event, root)
        redactor = Redactor()
        messages = [redactor.redact(text) for text in messages]
        # Stable message hashes cover final-response fields that precede transcript flush.
        count = len(messages)
        previous = state.get("count", 0)
        delta = messages[previous:] if previous <= count else messages
        final = (
            event.get("last_assistant_message")
            or event.get("prompt_response")
            or event.get("summary")
        )
        if isinstance(final, str) and final:
            delta.append("assistant: " + redactor.redact(final[:16000]))
        elif source == "explicit-summary-required":
            return {"status": source}
        seen = state.get("seen", [])
        new_hashes = []
        unique = []
        for message in delta:
            message_hash = hashlib.sha256(message.encode()).hexdigest()
            if message_hash not in seen and message_hash not in new_hashes:
                unique.append(message)
                new_hashes.append(message_hash)
        delta = unique
        excerpt = "\n".join(delta)[-16000:]
        if not excerpt.strip():
            return {"status": "duplicate-or-empty"}
        digest = hashlib.sha256(excerpt.encode()).hexdigest()
        if state.get("digest") == digest:
            return {"status": "duplicate"}
        capture_id = hashlib.sha256((session_hash + digest).encode()).hexdigest()
        payload = {
            "capture_id": capture_id,
            "agent": agent,
            "session_id": session_hash,
            "scope": credentials.get("scope", "shared"),
            "summary": "Captured agent evidence; requires human review.",
            "excerpt": excerpt,
            # The summary is a placeholder, so duplicates are judged on the evidence.
            "evidence_only": True,
        }
        # Spool and checkpoint BEFORE network I/O: a client-enforced kill still leaves
        # a redacted retry receipt, never a raw transcript copy.
        spool = private / "spool"
        safe_path(spool, root).mkdir(mode=0o700, exist_ok=True)
        spool_path = spool / (capture_id + ".json")
        if len(list(spool.glob("*.json"))) >= 100 and not spool_path.exists():
            return {"status": "spool-full"}
        write_json(
            spool_path,
            {"created": time.time(), "destination": destination, "payload": payload},
            root,
        )
        write_json(
            checkpoint, {"count": count, "digest": digest, "seen": (seen + new_hashes)[-256:]}, root
        )
        delivered = _send(payload, credentials)
        if delivered:
            safe_path(spool_path, root).unlink()
        return {"status": "submitted" if delivered else "spooled", "capture_id": capture_id}
    except BlockingIOError:
        return {"status": "busy"}
    except Exception:  # noqa: BLE001 -- hooks must never block the agent or echo input
        return {
            "status": "capture-unavailable",
            "fallback": "Use explicit hindsight capture with a redacted summary.",
        }
    finally:
        if lock is not None:
            os.close(lock)


def retry_spool(home=None):
    root = home_path(home)
    private = private_dir(home)
    credentials = read_json(private / "credentials.json", root, private=True)
    submitted = expired = 0
    spool = safe_path(private / "spool", root)
    for path in sorted(spool.glob("*.json"))[:10]:
        try:
            item = read_json(path, root, private=True, limit=64000)
            if time.time() - item["created"] > 7 * 86400:
                safe_path(path, root).unlink()
                expired += 1
            elif item.get("destination") == _destination(credentials) and _send(
                item["payload"], credentials
            ):
                safe_path(path, root).unlink()
                submitted += 1
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Leave malformed entries untouched; never log potentially sensitive data.
    return {"submitted": submitted, "expired": expired}
