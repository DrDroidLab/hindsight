#!/usr/bin/env python3
"""Offline image acceptance via real ASGI HTTP, with no public server or secrets printed."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4


def inside():
    # Imported only inside the built runtime (or an explicitly prepared dev environment).
    from fastapi.testclient import TestClient

    from hindsight.app import create_app
    from hindsight.auth import AuthStore
    from hindsight.cli import demo
    from hindsight.config import Settings

    with tempfile.TemporaryDirectory() as directory:
        remote = demo(directory)
        settings = Settings(data_dir=Path(directory), git_remote=remote)
        token = AuthStore(settings.private_dir).bootstrap()
        with TestClient(create_app(settings)) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/ready").status_code == 200
            shell = client.get("/")
            assert shell.status_code == 200 and "/assets/app.js" in shell.text
            for asset in ("app.js", "style.css"):
                response = client.get("/assets/" + asset)
                assert response.status_code == 200 and len(response.content) > 100
            enrollment = client.post(
                "/api/setup",
                json={
                    "token": token,
                    "name": "Demo Reviewer",
                    "email": "demo-reviewer@example.invalid",
                    "team": "platform",
                },
            )
            assert enrollment.status_code in (200, 201), enrollment.status_code
            key = enrollment.json()["key"]
            assert (
                client.post(
                    "/api/setup",
                    json={
                        "token": token,
                        "name": "Duplicate",
                        "email": "duplicate@example.invalid",
                        "team": "platform",
                    },
                ).status_code
                >= 400
            )
            session = client.post("/api/session", json={"key": key})
            assert session.status_code == 200
            csrf = session.json()["csrf_token"]
            headers = {"X-CSRF-Token": csrf}
            assert client.get("/api/whoami").json()["name"] == "Demo Reviewer"
            queue = client.get("/api/review?status=proposed").json()
            assert len(queue["items"]) == 1
            proposed = queue["items"][0]
            assert "[REDACTED:token]" in str(proposed)
            assert (
                client.post(
                    f"/api/clues/{proposed['id']}/review",
                    json={"expected_revision": proposed["revision"], "decision": "approve"},
                ).status_code
                == 403
            )
            approved = client.post(
                f"/api/clues/{proposed['id']}/review",
                headers=headers,
                json={
                    "expected_revision": proposed["revision"],
                    "decision": "approve",
                    "reason": "Checked synthetic evidence.",
                },
            )
            assert approved.status_code == 200, approved.status_code
            clue = approved.json()
            conflict = client.patch(
                f"/api/clues/{clue['id']}",
                headers=headers,
                json={"expected_revision": proposed["revision"], "title": "Do not overwrite"},
            )
            assert conflict.status_code == 409
            runbook = client.get("/api/runbooks").json()["items"][0]
            payload = {
                "runbook_id": runbook["id"],
                "expected_runbook_revision": runbook["revision"],
                "step_id": "demo-version-check",
                "heading": "Compare mounted credential versions",
                "content": "Compare version identifiers without disclosing values. Verify recovery.",
            }
            diff = client.post(
                f"/api/clues/{clue['id']}/promotion-preview", headers=headers, json=payload
            )
            assert diff.status_code == 200 and "+" in diff.json()["diff"]
            promotion = {
                **payload,
                "expected_revision": clue["revision"],
                "operation_id": str(uuid4()),
            }
            result = client.post(
                f"/api/clues/{clue['id']}/promote", headers=headers, json=promotion
            )
            assert result.status_code == 200, result.status_code
            assert (
                client.post(
                    f"/api/clues/{clue['id']}/promote", headers=headers, json=promotion
                ).json()["revision"]
                == result.json()["revision"]
            )
            capture = {
                "capture_id": str(uuid4()),
                "agent": "manual",
                "session_id": "demo-smoke",
                "scope": "shared",
                "summary": "A synthetic alert cleared after refreshing an outdated mount.",
            }
            draft = client.post("/api/captures", headers=headers, json=capture)
            assert draft.status_code == 201, draft.status_code
            repeat = client.post("/api/captures", headers=headers, json=capture)
            assert repeat.json()["clue_ids"] == draft.json()["clue_ids"]
            recalls = client.get("/api/recall?query=CrashLoopBackOff").json()["results"]
            assert any(r["kind"] == "runbook" for r in recalls)
            assert client.delete("/api/session", headers=headers).status_code in (200, 204)
            assert client.get("/api/whoami").status_code == 401
    print(
        "PASS: packaged assets, seeded git, enrollment/session/CSRF, review/conflict, promotion replay, capture/recall/logout"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="hindsight-sre:local")
    parser.add_argument("--inside", action="store_true")
    args = parser.parse_args()
    if args.inside:
        inside()
    else:
        script = Path(__file__).resolve()
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "python",
                "--mount",
                f"type=bind,src={script},dst=/tmp/demo-smoke.py,readonly",
                args.image,
                "/tmp/demo-smoke.py",
                "--inside",
            ],
            check=True,
            timeout=120,
        )


if __name__ == "__main__":
    main()
