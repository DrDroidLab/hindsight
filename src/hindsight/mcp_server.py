"""Official MCP transports over the same authorized domain services."""

from contextvars import ContextVar

from mcp.server.transport_security import TransportSecuritySettings

from .domain import serialized
from .mcp_safety import SafeFastMCP, silence_sdk_input_logs
from .models import Identity, KnowledgeError

request_identity: ContextVar[Identity | None] = ContextVar("hindsight_identity", default=None)


def create_mcp(knowledge, fixed_identity=None):
    # HTTP Host/Origin/auth are enforced by the outer ASGI guard for both API and MCP.
    mcp = SafeFastMCP(
        "Hindsight SRE",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/mcp",
        max_request_body_size=131072,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    silence_sdk_input_logs()

    def user():
        identity = (
            fixed_identity() if callable(fixed_identity) else fixed_identity
        ) or request_identity.get()
        if identity is None:
            raise KnowledgeError("Authentication required", 401)
        # Recheck the authoritative active identity on every call.
        return knowledge.auth.get_user(identity.id)

    @mcp.tool()
    def whoami() -> dict:
        """Return the authenticated human identity and permitted team scopes."""
        identity = user()
        return {
            "id": identity.id,
            "name": identity.name,
            "email": identity.email,
            "read_scopes": list(identity.read_scopes),
            "write_scopes": list(identity.write_scopes),
        }

    @mcp.tool()
    def recall(
        query: str = "",
        service: str | None = None,
        alert: str | None = None,
        scope: str | None = None,
        limit: int = 10,
    ) -> dict:
        """Retrieve operational memory. Clearly label unreviewed and stale evidence."""
        return knowledge.recall(
            user(), query=query, service=service, alert=alert, scope=scope, limit=limit
        )

    @mcp.tool()
    def get_runbook(id: str) -> dict:
        """Load a visible runbook and its stable step IDs. Never execute commands."""
        return serialized(knowledge.get(user(), id, "runbook"))

    @mcp.tool()
    def list_skills(service: str | None = None, scope: str | None = None) -> dict:
        """List available skills without executing skill content."""
        records = knowledge.records(user(), "skill")
        return {
            "items": [
                serialized(r)
                for r in records
                if (not scope or r.scope == scope)
                and (not service or service in r.meta.get("service_ids", []))
            ]
        }

    @mcp.tool()
    def load_skill(id: str, file: str | None = None) -> dict:
        """Load SKILL.md or an allowlisted text attachment. Never execute skill content."""
        skill = knowledge.get(user(), id, "skill")
        if file is not None:
            return knowledge.repo.skill_file(skill, file)
        return serialized(skill)

    @mcp.tool()
    def propose_clue(
        capture_id: str,
        summary: str,
        scope: str = "shared",
        excerpt: str = "",
        service_ids: list[str] | None = None,
        alerts: list[str] | None = None,
        incident_id: str | None = None,
        candidate: dict | None = None,
    ) -> dict:
        """Commit a redacted unreviewed clue. A human must approve the learned fact."""
        identity = user()
        receipt = knowledge.capture(
            identity,
            {
                "capture_id": capture_id,
                "summary": summary,
                "scope": scope,
                "excerpt": excerpt,
                "service_ids": service_ids or [],
                "alerts": alerts or [],
                "incident_id": incident_id,
                "candidate": candidate,
                "agent": "mcp",
                "session_id": capture_id,
            },
        )
        # Durable redacted drafts remain available for background extraction/retry.
        return receipt

    @mcp.tool()
    def report_outcome(
        idempotency_key: str,
        runbook_id: str,
        revision: str,
        step_id: str,
        result: str,
        note: str = "",
        clue_ids: list[str] | None = None,
        incident_id: str | None = None,
    ) -> dict:
        """Record a verified step result. Success alone never approves a clue."""
        return knowledge.outcome(
            user(),
            {
                "idempotency_key": idempotency_key,
                "runbook_id": runbook_id,
                "revision": revision,
                "step_id": step_id,
                "result": result,
                "note": note,
                "clue_ids": clue_ids or [],
                "incident_id": incident_id,
            },
        )

    return mcp
