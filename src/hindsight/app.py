"""Same-origin review API and MCP server with bounded, non-logging request guards."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import AuthStore
from .config import Settings
from .domain import Knowledge, serialized
from .mcp_server import create_mcp, request_identity
from .models import KnowledgeError
from .redaction import Redactor
from .repository import Repository
from .search import SearchIndex


def build_knowledge(settings):
    redactor = Redactor(getattr(settings, "redaction_rules", None))
    repository = Repository(settings, redactor=redactor)
    repository.initialize()
    auth = AuthStore(settings.data_dir / "private")
    from .providers import EmbeddingProvider

    embedding = (
        EmbeddingProvider(
            settings.embeddings_endpoint, settings.embeddings_model, settings.embeddings_key or ""
        )
        if settings.embeddings_endpoint
        else None
    )
    search = SearchIndex(
        settings.data_dir / "index", redactor=redactor, embedding_provider=embedding
    )
    return Knowledge(settings, repository, auth, redactor, search)


class Guard:
    def __init__(self, app, knowledge):
        self.app, self.knowledge = app, knowledge
        self.rates = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        path = scope.get("path", "")
        settings, auth = self.knowledge.settings, self.knowledge.auth
        host = request.headers.get("host", "")
        try:
            hostname = urlsplit("//" + host).hostname
        except ValueError:
            hostname = None
        if not hostname or hostname not in settings.allowed_hosts:
            return await JSONResponse({"detail": "Untrusted Host"}, 400)(scope, receive, send)
        origin = request.headers.get("origin")
        expected_origin = settings.public_origin or f"{request.url.scheme}://{host}"
        if origin and origin != expected_origin and origin not in settings.allowed_origins:
            return await JSONResponse({"detail": "Untrusted Origin"}, 403)(scope, receive, send)
        chunks, size = [], 0
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            size += len(event.get("body", b""))
            if size > 131072:
                return await JSONResponse({"detail": "Request exceeds 128 KiB"}, 413)(
                    scope, receive, send
                )
            chunks.append(event.get("body", b""))
            if not event.get("more_body"):
                break
        body = b"".join(chunks)
        if path.rstrip("/") == "/mcp" and request.method == "POST":
            from .mcp_safety import validated_message

            try:
                validated_message(body)
            except Exception:
                return await JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": "Invalid MCP message"},
                    },
                    400,
                )(scope, receive, send)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        identity, token = None, None
        public = path in ("/api/status", "/api/setup", "/api/session", "/health", "/ready")
        protected = path.startswith("/api/") or path.rstrip("/") == "/mcp"
        try:
            if protected and not public:
                header = request.headers.get("authorization", "")
                if header.startswith("Bearer "):
                    identity = auth.authenticate(header[7:])
                else:
                    cookie = request.cookies.get("hindsight_session", "")
                    identity = auth.authenticate_session(cookie)
                    if request.method not in ("GET", "HEAD", "OPTIONS"):
                        csrf = request.headers.get("x-csrf-token", "")
                        if not auth.check_csrf(cookie, csrf):
                            raise KnowledgeError("CSRF verification failed", 403)
                token = request_identity.set(identity)
            # DELETE session is public only for the key-exchange POST; still requires CSRF.
            if path == "/api/session" and request.method == "DELETE":
                cookie = request.cookies.get("hindsight_session", "")
                auth.authenticate_session(cookie)
                if not auth.check_csrf(cookie, request.headers.get("x-csrf-token", "")):
                    raise KnowledgeError("CSRF verification failed", 403)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                bucket = identity.id if identity else str(scope.get("client", ("unknown",))[0])
                stamp = time.monotonic()
                events = self.rates[bucket]
                while events and events[0] < stamp - 60:
                    events.popleft()
                if len(events) >= 100:
                    raise KnowledgeError("Write rate limit exceeded", 429)
                events.append(stamp)
                if len(self.rates) > 4096:
                    for key in list(self.rates):
                        if not self.rates[key] or self.rates[key][-1] < stamp - 60:
                            del self.rates[key]
            scope.setdefault("state", {})["identity"] = identity

            async def secure_send(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers += [
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"cache-control", b"no-store"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                        ),
                    ]
                    message["headers"] = headers
                await send(message)

            await self.app(scope, replay, secure_send)
        except KnowledgeError as exc:
            await JSONResponse({"detail": str(exc)}, exc.status)(scope, replay, send)
        finally:
            if token is not None:
                request_identity.reset(token)


def create_app(settings=None, knowledge=None, run_jobs=True):
    settings = settings or Settings.from_env()
    knowledge = knowledge or build_knowledge(settings)
    mcp = create_mcp(knowledge)
    mcp_app = mcp.streamable_http_app()

    async def maintenance():
        last_stale = 0.0
        while True:
            await asyncio.sleep(max(1, settings.sync_interval))
            try:
                await asyncio.to_thread(knowledge.repo.sync)
                await asyncio.to_thread(knowledge.rebuild)
                if time.monotonic() - last_stale > 86400:
                    await asyncio.to_thread(knowledge.stale_check)
                    last_stale = time.monotonic()
                for record in knowledge.repo.documents():
                    if record.kind == "clue" and record.meta.get("extraction_state") == "pending":
                        try:
                            identity = knowledge.auth.get_user(record.meta["created_by"])
                            await asyncio.to_thread(knowledge.extract, identity, record.id)
                        except KnowledgeError:
                            continue
            except Exception:
                # Details can include remote URLs, tokens, or provider payloads.
                # The repository exposes only safe sync state to the UI.
                continue

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            task = asyncio.create_task(maintenance()) if run_jobs else None
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    app = FastAPI(
        title="Hindsight", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.knowledge = knowledge
    app.add_middleware(Guard, knowledge=knowledge)

    @app.exception_handler(KnowledgeError)
    async def domain_error(request, exc):
        return JSONResponse({"detail": str(exc)}, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": "Invalid request fields"}, 422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse({"detail": "Request failed. No raw input is logged."}, 500)

    async def payload(request):
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeError):
            raise KnowledgeError("Expected a JSON object") from None

    def user(request):
        return request.state.identity

    def identity_json(identity):
        return {
            "id": identity.id,
            "name": identity.name,
            "email": identity.email,
            "read_scopes": list(identity.read_scopes),
            "write_scopes": list(identity.write_scopes),
        }

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        knowledge.rebuild()
        return {"status": "ready", "revision": knowledge.repo.head()}

    @app.get("/api/status")
    def status():
        return {
            "setup_required": not knowledge.auth.has_users(),
            "sync_state": {"status": knowledge.repo.sync_state.get("status", "local")},
        }

    @app.post("/api/setup", status_code=201)
    async def setup(request: Request):
        data = await payload(request)
        identity, key = knowledge.auth.enroll(
            data.get("token", ""),
            data.get("name", ""),
            data.get("email", ""),
            data.get("team", "shared"),
        )
        return {"identity": identity_json(identity), "key": key}

    @app.post("/api/session")
    async def session(request: Request):
        data = await payload(request)
        token, csrf, identity = knowledge.auth.create_session(data.get("key", ""))
        response = JSONResponse({"identity": identity_json(identity), "csrf_token": csrf})
        secure = settings.public_origin.startswith("https://") or request.url.scheme == "https"
        response.set_cookie(
            "hindsight_session",
            token,
            httponly=True,
            secure=secure,
            samesite="strict",
            max_age=28800,
        )
        response.set_cookie(
            "hindsight_csrf", csrf, httponly=False, secure=secure, samesite="strict", max_age=28800
        )
        return response

    @app.delete("/api/session")
    def logout(request: Request):
        knowledge.auth.delete_session(request.cookies.get("hindsight_session", ""))
        response = JSONResponse({"ok": True})
        response.delete_cookie("hindsight_session")
        response.delete_cookie("hindsight_csrf")
        return response

    @app.get("/api/whoami")
    def whoami(request: Request):
        return {
            **identity_json(user(request)),
            "csrf_token": request.cookies.get("hindsight_csrf"),
            "sync_state": knowledge.repo.sync_state,
            "review_mode": settings.review_mode,
            "read_only": settings.read_only,
        }

    @app.get("/api/review")
    def review_queue(
        request: Request,
        scope: str | None = None,
        status: str = "proposed",
        service: str | None = None,
        cursor: str = "0",
    ):
        return knowledge.review_queue(user(request), scope, status, service, cursor)

    @app.get("/api/clues/{object_id}")
    def clue(request: Request, object_id: str):
        return knowledge.detail(user(request), object_id)

    @app.patch("/api/clues/{object_id}")
    async def edit(request: Request, object_id: str):
        return knowledge.edit_clue(user(request), object_id, await payload(request))

    @app.post("/api/clues/{object_id}/review")
    async def review(request: Request, object_id: str):
        return knowledge.review(user(request), object_id, await payload(request))

    @app.post("/api/clues/{object_id}/promotion-preview")
    async def preview(request: Request, object_id: str):
        return knowledge.promotion(user(request), object_id, await payload(request))

    @app.post("/api/clues/{object_id}/promote")
    async def promote(request: Request, object_id: str):
        return knowledge.promotion(user(request), object_id, await payload(request), commit=True)

    @app.post("/api/captures", status_code=201)
    @app.post("/api/clues", status_code=201)
    async def capture(request: Request, background: BackgroundTasks):
        identity = user(request)
        receipt = knowledge.capture(identity, await payload(request))
        if receipt["extraction_state"] == "pending":
            for clue_id in receipt["clue_ids"]:
                background.add_task(knowledge.extract, identity, clue_id)
        return receipt

    @app.get("/api/runbooks")
    def runbooks(request: Request):
        return {"items": [serialized(r) for r in knowledge.records(user(request), "runbook")]}

    @app.get("/api/runbooks/{object_id}")
    def runbook(request: Request, object_id: str):
        return serialized(knowledge.get(user(request), object_id, "runbook"))

    @app.get("/api/services")
    def services(request: Request):
        return {"items": [serialized(r) for r in knowledge.records(user(request), "service")]}

    @app.get("/api/incidents")
    def incidents(request: Request):
        return {"items": [serialized(r) for r in knowledge.records(user(request), "incident")]}

    @app.get("/api/incidents/{object_id}")
    def incident_detail(request: Request, object_id: str):
        return serialized(knowledge.get(user(request), object_id, "incident"))

    @app.post("/api/incidents", status_code=201)
    async def new_incident(request: Request):
        return knowledge.incident(user(request), await payload(request))

    @app.patch("/api/incidents/{object_id}")
    async def update_incident(request: Request, object_id: str):
        return knowledge.incident(user(request), await payload(request), object_id)

    @app.post("/api/outcomes", status_code=201)
    async def outcome(request: Request):
        return knowledge.outcome(user(request), await payload(request))

    @app.get("/api/recall")
    def recall(
        request: Request,
        query: str = "",
        service: str | None = None,
        alert: str | None = None,
        scope: str | None = None,
        limit: int = 10,
    ):
        return knowledge.recall(
            user(request), query=query, service=service, alert=alert, scope=scope, limit=limit
        )

    static = Path(__file__).parent / "static"
    if static.exists():

        @app.get("/")
        @app.get("/setup")
        def frontend():
            return FileResponse(static / "index.html")

        app.mount("/assets", StaticFiles(directory=static / "assets", check_dir=False))
    app.mount("/", mcp_app)
    return app
