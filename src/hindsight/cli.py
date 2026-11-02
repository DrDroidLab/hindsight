"""Operator CLI and safe agent-side commands."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .models import KnowledgeError


def output(value):
    print(json.dumps(value, indent=2, default=str))


def client_credentials():
    from .integrations.installers import validate_server
    from .integrations.storage import read_json

    root = Path.home()
    path = root / ".config/hindsight/credentials.json"
    value = read_json(path, root, private=True, limit=16384)
    if not isinstance(value, dict):
        raise KnowledgeError("Invalid private credential file")
    server = os.environ.get("HINDSIGHT_SERVER", value.get("server", ""))
    if server:
        try:
            server = validate_server(server)
        except ValueError:
            raise KnowledgeError("Server must use HTTPS or loopback HTTP") from None
    return {
        "server": server,
        "key": os.environ.get("HINDSIGHT_API_KEY", value.get("key", "")),
        "scope": os.environ.get("HINDSIGHT_SCOPE", value.get("scope", "shared")),
    }


def api(method, path, payload=None, params=None):
    import httpx

    credentials = client_credentials()
    if not credentials["server"] or not credentials["key"]:
        raise KnowledgeError("Run hindsight login --server URL first")
    response = httpx.request(
        method,
        credentials["server"].rstrip("/") + path,
        json=payload,
        params=params,
        headers={"Authorization": "Bearer " + credentials["key"]},
        timeout=20,
        follow_redirects=False,
    )
    if response.status_code >= 400:
        raise KnowledgeError(f"Server request failed ({response.status_code})")
    return response.json()


async def proxy():
    """Tool-only stdio proxy. Keys stay in the private credential file, not agent config."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server import Server
    from mcp.types import CallToolResult

    from .mcp_safety import safe_stdio_server

    credentials = client_credentials()
    if not credentials["server"] or not credentials["key"]:
        raise KnowledgeError("Missing private client credentials")
    server = Server("hindsight-sre-proxy")
    from contextlib import asynccontextmanager

    import httpx

    @asynccontextmanager
    async def remote():
        async with httpx.AsyncClient(
            headers={"Authorization": "Bearer " + credentials["key"]}, timeout=20
        ) as http:
            async with streamable_http_client(
                credentials["server"].rstrip("/") + "/mcp", http_client=http
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

    @server.list_tools()
    async def list_tools():
        async with remote() as session:
            return (await session.list_tools()).tools

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments) -> CallToolResult:
        from mcp.types import TextContent

        try:
            async with remote() as session:
                return await session.call_tool(name, arguments)
        except Exception:
            return CallToolResult(
                isError=True,
                content=[
                    TextContent(
                        type="text",
                        text="Remote tool request failed. Check server connectivity and credentials.",
                    )
                ],
            )

    async with safe_stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def demo(data_dir):
    """Create a local bare remote from the shipped synthetic knowledge, once."""
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    remote = root / "demo-remote.git"
    if remote.exists():
        return str(remote.resolve())
    seed = Path(__file__).parent / "sample-knowledge"
    if not seed.exists():
        seed = Path(__file__).resolve().parents[2] / "examples/sample-knowledge"
    if not seed.exists():
        raise KnowledgeError("Sample knowledge is not installed")
    import tempfile

    with tempfile.TemporaryDirectory(dir=root) as temp:
        working = Path(temp) / "seed"
        shutil.copytree(seed, working)
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Hindsight Demo",
            "GIT_AUTHOR_EMAIL": "demo@localhost",
            "GIT_COMMITTER_NAME": "Hindsight Demo",
            "GIT_COMMITTER_EMAIL": "demo@localhost",
        }
        commands = [
            ["git", "init", "--initial-branch=main", str(working)],
            [
                "git",
                "-C",
                str(working),
                "add",
                "--",
                "services",
                "runbooks",
                "clues",
                "skills",
                "incidents",
                "outcomes",
            ],
            ["git", "-C", str(working), "commit", "-m", "Seed synthetic operational knowledge"],
            ["git", "clone", "--bare", str(working), str(remote)],
        ]
        for command in commands:
            subprocess.run(
                command,
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
    return str(remote.resolve())


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="hindsight", description="Self-hosted SRE memory. No telemetry."
    )
    parser.add_argument("--data-dir")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    admin = commands.add_parser("admin")
    admins = admin.add_subparsers(dest="admin_command", required=True)
    admins.add_parser("bootstrap")
    create = admins.add_parser("create-user")
    create.add_argument("--name", required=True)
    create.add_argument("--email", required=True)
    create.add_argument("--read-scopes", default="shared")
    create.add_argument("--write-scopes", default="shared")
    issue = admins.add_parser("issue-key")
    issue.add_argument("user_id")
    revoke = admins.add_parser("revoke-key")
    revoke.add_argument("key_id")
    login = commands.add_parser("login")
    login.add_argument("--server", required=True)
    login.add_argument("--scope", default="shared")
    install_parser = commands.add_parser("install")
    install_parser.add_argument("agent", choices=["claude", "cursor", "codex", "gemini"])
    install_parser.add_argument("--server")
    install_parser.add_argument("--home", type=Path)
    install_parser.add_argument("--dry-run", action="store_true")
    install_parser.add_argument("--uninstall", action="store_true")
    install_parser.add_argument("--check", action="store_true")
    hook = commands.add_parser("hook")
    hook.add_argument("agent", choices=["claude", "cursor", "codex", "gemini"])
    capture = commands.add_parser("capture")
    capture.add_argument("--summary")
    capture.add_argument("--scope")
    capture.add_argument("--retry", action="store_true")
    capture.add_argument("--service-id", action="append", default=[])
    capture.add_argument("--alert", action="append", default=[])
    recall = commands.add_parser("recall")
    recall.add_argument("query", nargs="?", default="")
    recall.add_argument("--service")
    recall.add_argument("--alert")
    recall.add_argument("--scope")
    review = commands.add_parser("review")
    review.add_argument("--status", default="proposed")
    review.add_argument("--scope")
    mcp = commands.add_parser("mcp")
    mcp.add_argument("--transport", choices=["stdio"], default="stdio")
    commands.add_parser("mcp-proxy")
    commands.add_parser("whoami")
    commands.add_parser("sync")
    commands.add_parser("stale-check")
    demo_parser = commands.add_parser("demo")
    demo_parser.add_argument("--serve", action="store_true")
    demo_parser.add_argument("--host", default="0.0.0.0")
    demo_parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.data_dir:
        os.environ["HINDSIGHT_DATA_DIR"] = args.data_dir
    try:
        if args.command == "login":
            from .integrations import store_credentials

            store_credentials(args.server, getpass.getpass("API key: "), args.scope)
            output({"status": "Private client credentials stored"})
        elif args.command == "install":
            from .integrations import install

            output(
                install(
                    args.agent,
                    args.server or client_credentials()["server"],
                    home=args.home,
                    dry_run=args.dry_run,
                    uninstall=args.uninstall,
                    check=args.check,
                )
            )
        elif args.command == "hook":
            from .integrations import capture_hook

            try:
                event = json.loads(sys.stdin.buffer.read(131073))
                capture_hook(args.agent, event)
            except Exception:
                pass
            # Gemini expects harmless JSON, other hooks accept empty output.
            if args.agent == "gemini":
                print("{}")
        elif args.command == "capture":
            from uuid import uuid4

            if args.retry:
                from .integrations import retry_spool

                output(retry_spool())
            else:
                summary = args.summary if args.summary is not None else sys.stdin.read(32001)
                from .redaction import Redactor

                output(
                    api(
                        "POST",
                        "/api/captures",
                        Redactor().clean(
                            {
                                "capture_id": str(uuid4()),
                                "agent": "generic",
                                "session_id": "manual",
                                "summary": summary,
                                "scope": args.scope or client_credentials()["scope"],
                                "service_ids": args.service_id,
                                "alerts": args.alert,
                            }
                        ),
                    )
                )
        elif args.command == "recall":
            output(
                api(
                    "GET",
                    "/api/recall",
                    params={
                        k: v
                        for k, v in {
                            "query": args.query,
                            "service": args.service,
                            "alert": args.alert,
                            "scope": args.scope,
                        }.items()
                        if v is not None
                    },
                )
            )
        elif args.command == "review":
            output(
                api(
                    "GET",
                    "/api/review",
                    params={
                        k: v
                        for k, v in {"status": args.status, "scope": args.scope}.items()
                        if v is not None
                    },
                )
            )
        elif args.command == "whoami":
            output(api("GET", "/api/whoami"))
        elif args.command == "mcp-proxy":
            asyncio.run(proxy())
        else:
            from .config import Settings

            settings = Settings.from_env()
            if args.command == "admin":
                from .auth import AuthStore

                auth = AuthStore(settings.data_dir / "private")
                if args.admin_command == "bootstrap":
                    print(auth.bootstrap())
                elif args.admin_command == "create-user":
                    identity, key = auth.create_user(
                        args.name,
                        args.email,
                        tuple(args.read_scopes.split(",")),
                        tuple(args.write_scopes.split(",")),
                    )
                    output({"id": identity.id, "key": key})
                elif args.admin_command == "issue-key":
                    print(auth.issue_key(args.user_id))
                else:
                    auth.revoke_key(args.key_id)
                    output({"status": "Key revoked"})
            elif args.command == "demo":
                remote = demo(settings.data_dir)
                if args.serve:
                    os.environ["GIT_REMOTE"] = remote
                    import uvicorn

                    from .app import create_app

                    uvicorn.run(
                        create_app(),
                        host=args.host,
                        port=args.port,
                        access_log=False,
                        log_level="critical",
                        proxy_headers=False,
                    )
                else:
                    print(remote)
            elif args.command == "serve":
                import uvicorn

                from .app import create_app

                uvicorn.run(
                    create_app(settings),
                    host=args.host,
                    port=args.port,
                    access_log=False,
                    log_level="critical",
                    proxy_headers=False,
                )
            else:
                from .app import build_knowledge

                knowledge = build_knowledge(settings)
                if args.command == "sync":
                    output(knowledge.repo.sync())
                elif args.command == "stale-check":
                    output({"changed": knowledge.stale_check()})
                elif args.command == "mcp":
                    from .mcp_server import create_mcp

                    key = client_credentials()["key"]
                    identity = knowledge.auth.authenticate(key)
                    create_mcp(knowledge, lambda: knowledge.auth.authenticate(key)).run(
                        transport="stdio"
                    )
    except KnowledgeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print(
            "Command failed. Check configuration and server status. No raw input is logged.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
