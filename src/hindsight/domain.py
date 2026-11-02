"""Authorization-aware application workflows shared by HTTP, MCP, and CLI."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from .models import Identity, KnowledgeError, Record, dump_document, parse_document


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def fingerprint(scope: str, services: list, alerts: list, body: str) -> str:
    value = [scope, sorted(services), sorted(alerts), " ".join(body.lower().split())]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def serialized(record: Record) -> dict:
    return {
        **record.meta,
        "id": record.id,
        "kind": record.kind,
        "path": record.path,
        "scope": record.scope,
        "revision": record.revision,
        "body": record.body,
        "meta": record.meta,
        "unreviewed": record.meta.get("status") == "proposed",
        "stale": record.meta.get("status") == "stale",
    }


class Knowledge:
    def __init__(self, settings, repository, auth, redactor, search):
        self.settings = settings
        self.repo = repository
        self.auth = auth
        self.redactor = redactor
        self.search = search
        self.rebuild()

    def rebuild(self):
        with self.repo.lock():
            records = self.repo.documents(include_proposals=True)
            revision = self.repo.head()
        self.search.rebuild(records, revision)

    def records(self, identity: Identity, kind: str | None = None) -> list[Record]:
        return [
            r
            for r in self.repo.documents(include_proposals=True)
            if identity.can_read(r.scope) and (kind is None or r.kind == kind)
        ]

    def get(
        self, identity: Identity, object_id: str, kind: str | None = None, write: bool = False
    ) -> Record:
        for record in self.records(identity, kind):
            if record.id == object_id:
                if write and not identity.can_write(record.scope):
                    raise KnowledgeError("Object not found", 404)
                return record
        raise KnowledgeError("Object not found", 404)

    def _scope(self, identity, scope):
        if not isinstance(scope, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", scope):
            raise KnowledgeError("Invalid scope")
        if not identity.can_write(scope):
            raise KnowledgeError("Scope is not writable", 403)

    def _references(self, identity, scope, services=None, incident=None):
        for service_id in services or []:
            record = self.get(identity, service_id, "service")
            if record.scope not in ("shared", scope):
                raise KnowledgeError("Reference is outside the write scope")
        if incident:
            record = self.get(identity, incident, "incident")
            if record.scope not in ("shared", scope):
                raise KnowledgeError("Reference is outside the write scope")

    def _new(self, identity, kind, title, scope, **fields):
        self._scope(identity, scope)
        stamp = now_iso()
        return {
            "schema_version": 1,
            "id": str(uuid4()),
            "kind": kind,
            "title": title,
            "created_at": stamp,
            "updated_at": stamp,
            "created_by": identity.id,
            **fields,
        }

    def _path(self, meta, scope):
        prefix = "" if scope == "shared" else f"teams/{scope}/"
        folder = {
            "clue": "clues",
            "incident": "incidents",
            "outcome": "outcomes",
            "runbook": "runbooks",
        }[meta["kind"]]
        return f"{prefix}{folder}/{meta['id']}.md"

    def _save(self, identity, changes, message, expected=None):
        writer = self.repo.write
        if self.settings.review_mode == "git" and all(
            parse_document(path, text).kind == "clue"
            and parse_document(path, text).meta.get("status") == "proposed"
            for path, text in changes.items()
        ):
            writer = self.repo.proposal_write
        revision = writer(changes, identity, message, expected=expected)
        # A committed write remains successful if the disposable index fails.
        # Reads repair mismatches before recall; do not invite duplicate write retries.
        try:
            self.rebuild()
        except (OSError, KnowledgeError):
            pass
        return revision

    def _check_revision(self, record, expected):
        if not expected or record.revision != expected:
            raise KnowledgeError("Revision conflict. Reload the current record.", 409)

    def recall(self, identity, **kwargs):
        # Accept service names as well as stable service IDs, as the product promises.
        service = kwargs.get("service")
        if service:
            for record in self.records(identity, "service"):
                if service in (
                    record.id,
                    record.meta["title"],
                    record.path.rsplit("/", 1)[-1][:-3],
                ):
                    kwargs["service"] = record.id
                    break
        if getattr(self.search, "revision", None) != self.repo.head():
            self.rebuild()
        result = self.search.recall(identity, **kwargs)
        visible = {r.id for r in self.records(identity)}
        for item in result.get("results", []):
            meta = item.get("meta", {})
            item["related_ids"] = [x for x in meta.get("service_ids", []) if x in visible]
        return result

    def review_queue(self, identity, scope=None, status="proposed", service=None, cursor=0):
        if status not in ("all", "proposed", "approved", "rejected", "stale"):
            raise KnowledgeError("Invalid clue status")
        records = [r for r in self.records(identity, "clue") if not scope or r.scope == scope]
        if service:
            records = [r for r in records if service in r.meta.get("service_ids", [])]
        counts = {
            state: sum(r.meta.get("status") == state for r in records)
            for state in ("proposed", "approved", "rejected", "stale")
        }
        records = [r for r in records if status == "all" or r.meta.get("status") == status]
        records.sort(key=lambda r: (str(r.meta.get("created_at", "")), r.id), reverse=True)
        try:
            offset = int(cursor or 0)
        except (ValueError, TypeError):
            raise KnowledgeError("Invalid cursor") from None
        if offset < 0 or offset > 100000:
            raise KnowledgeError("Invalid cursor")
        return {
            "items": [serialized(r) for r in records[offset : offset + 50]],
            "counts": counts,
            "next_cursor": offset + 50 if len(records) > offset + 50 else None,
            "sync_state": self.repo.sync_state,
        }

    def detail(self, identity, object_id, kind="clue"):
        record = self.get(identity, object_id, kind)
        data = serialized(record)
        data["outcomes"] = [
            serialized(r)
            for r in self.records(identity, "outcome")
            if object_id in r.meta.get("clue_ids", [])
        ]
        data["related"] = [
            serialized(r)
            for r in self.records(identity)
            if r.id in record.meta.get("service_ids", [])
        ]
        return data

    def capture(self, identity, payload, *, extraction_complete=False, parent_clue_id=None):
        payload = self.redactor.clean(payload)
        scope = payload.get("scope", "shared")
        self._scope(identity, scope)
        summary = payload.get("summary", "")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 32000:
            raise KnowledgeError("Provide a summary of 1 to 32000 characters")
        capture_id = payload.get("capture_id")
        if not isinstance(capture_id, str) or not 1 <= len(capture_id) <= 200:
            raise KnowledgeError("Provide a bounded capture_id")
        candidate = payload.get("candidate") or {}
        if not isinstance(candidate, dict):
            raise KnowledgeError("Invalid candidate")
        summary = candidate.get("summary", summary)
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 32000:
            raise KnowledgeError("Invalid candidate summary")
        services, alerts = payload.get("service_ids", []), payload.get("alerts", [])
        if not isinstance(services, list) or not isinstance(alerts, list) or len(alerts) > 50:
            raise KnowledgeError("Invalid service or alert references")
        self._references(identity, scope, services, payload.get("incident_id"))
        fp = fingerprint(scope, services, alerts, summary)
        with self.repo.lock():
            for existing in self.records(identity, "clue"):
                if existing.scope == scope and (
                    existing.meta.get("capture_id") == capture_id
                    or existing.meta.get("fingerprint") == fp
                    or fp in existing.meta.get("prior_fingerprints", [])
                ):
                    return {
                        "capture_id": capture_id,
                        "clue_ids": [existing.id],
                        "revision": self.repo.head(),
                        "extraction_state": "duplicate",
                        "sync_state": self.repo.sync_state,
                    }
            confidence = candidate.get("confidence", 0.5)
            if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                raise KnowledgeError("Confidence must be between zero and one")
            title = candidate.get("title", summary.splitlines()[0][:120])
            meta = self._new(
                identity,
                "clue",
                title,
                scope,
                status="proposed",
                service_ids=services,
                alerts=alerts,
                incident_id=payload.get("incident_id"),
                confidence=confidence,
                fingerprint=fp,
                capture_id=capture_id,
                fact_version=str(uuid4()),
                source={
                    "agent": payload.get("agent", "generic"),
                    "session_id": payload.get("session_id", "manual"),
                    "excerpt": str(payload.get("excerpt", ""))[:16000],
                },
                extraction_state=(
                    "complete"
                    if extraction_complete
                    else ("pending" if self.settings.llm_endpoint else "manual")
                ),
                parent_clue_id=parent_clue_id,
                review={},
                promotion={},
            )
            path = self._path(meta, scope)
            revision = self._save(
                identity, {path: dump_document(meta, summary)}, "Capture redacted clue", {path: ""}
            )
        return {
            "capture_id": capture_id,
            "clue_ids": [meta["id"]],
            "extraction_state": meta["extraction_state"],
            "revision": revision,
            "sync_state": self.repo.sync_state,
        }

    def extract(self, identity, clue_id):
        """Model calls run outside the writer lock; only already-redacted drafts enter."""
        from .providers import ExtractionProvider

        draft = self.get(identity, clue_id, "clue", write=True)
        if draft.meta.get("extraction_state") != "pending":
            return
        try:
            provider = ExtractionProvider(
                self.settings.llm_endpoint, self.settings.llm_model, self.settings.llm_key or ""
            )
            candidates = provider.extract(
                {"summary": draft.body, "excerpt": draft.meta.get("source", {}).get("excerpt", "")}
            )
            candidates = self.redactor.clean(candidates)
            if not candidates:
                raise KnowledgeError("No valid extraction candidates")
            # Commit the complete extraction batch atomically. A crash cannot mark the
            # parent complete while losing child candidates, or recursively extract children.
            with self.repo.lock():
                current = self.get(identity, clue_id, "clue", write=True)
                if current.revision != draft.revision or current.meta.get("status") != "proposed":
                    return
                stamp = now_iso()
                changes, expected = {}, {current.path: current.revision}
                seen = {
                    r.meta.get("fingerprint")
                    for r in self.records(identity, "clue")
                    if r.scope == current.scope and r.id != current.id
                }
                for index, candidate in enumerate(candidates[:8]):
                    body = candidate["summary"]
                    fp = fingerprint(
                        current.scope,
                        current.meta.get("service_ids", []),
                        current.meta.get("alerts", []),
                        body,
                    )
                    if index and fp in seen:
                        continue
                    seen.add(fp)
                    meta = {
                        **current.meta,
                        "title": candidate["title"],
                        "confidence": candidate.get("confidence", 0.5),
                        "fingerprint": fp,
                        "fact_version": str(uuid4()),
                        "extraction_state": "complete",
                        "updated_at": stamp,
                    }
                    if index:
                        meta.update(
                            id=str(uuid4()),
                            capture_id=f"{current.meta['capture_id']}:extracted:{index}",
                            parent_clue_id=current.id,
                            created_at=stamp,
                            prior_fingerprints=[],
                            review={},
                            promotion={},
                        )
                        path = self._path(meta, current.scope)
                        expected[path] = ""
                    else:
                        path = current.path
                        meta["prior_fingerprints"] = list(
                            dict.fromkeys(
                                [
                                    *meta.get("prior_fingerprints", []),
                                    current.meta.get("fingerprint", ""),
                                ]
                            )
                        )[-100:]
                    changes[path] = dump_document(meta, body)
                self._save(identity, changes, "Commit redacted extraction candidates", expected)
        except Exception:
            # Never expose provider request/error text, which can contain input or credentials.
            try:
                with self.repo.lock():
                    current = self.get(identity, clue_id, "clue", write=True)
                    if current.revision != draft.revision:
                        return
                    meta = {**current.meta, "extraction_state": "failed", "updated_at": now_iso()}
                    self._save(
                        identity,
                        {current.path: dump_document(meta, current.body)},
                        "Keep redacted draft after extraction failure",
                        {current.path: current.revision},
                    )
            except Exception:
                return

    def edit_clue(self, identity, clue_id, payload, extraction_state=None):
        if self.settings.review_mode == "git" and extraction_state is None:
            raise KnowledgeError("Edit proposals through normal git review", 409)
        payload = self.redactor.clean(payload)
        with self.repo.lock():
            record = self.get(identity, clue_id, "clue", write=True)
            self._check_revision(record, payload.get("expected_revision"))
            if record.meta.get("status") not in ("proposed", "stale"):
                raise KnowledgeError("Only proposed or stale clues can be edited", 409)
            meta = dict(record.meta)
            for key in ("title", "service_ids", "alerts", "confidence"):
                if key in payload:
                    meta[key] = payload[key]
            body = payload.get("body", record.body)
            if not isinstance(body, str) or not body.strip() or len(body) > 32000:
                raise KnowledgeError("Invalid clue body")
            self._references(
                identity, record.scope, meta.get("service_ids"), meta.get("incident_id")
            )
            if (
                body != record.body
                or meta.get("service_ids", []) != record.meta.get("service_ids", [])
                or meta.get("alerts", []) != record.meta.get("alerts", [])
            ):
                meta["fact_version"] = str(uuid4())
                meta["prior_fingerprints"] = list(
                    dict.fromkeys(
                        [*meta.get("prior_fingerprints", []), meta.get("fingerprint", "")]
                    )
                )[-100:]
                meta["fingerprint"] = fingerprint(
                    record.scope, meta.get("service_ids", []), meta.get("alerts", []), body
                )
            meta["updated_at"] = now_iso()
            if extraction_state:
                meta["extraction_state"] = extraction_state
            self._save(
                identity,
                {record.path: dump_document(meta, body)},
                "Edit redacted clue",
                {record.path: record.revision},
            )
            return self.detail(identity, clue_id)

    def review(self, identity, clue_id, payload):
        payload = self.redactor.clean(payload)
        if self.settings.review_mode == "git":
            raise KnowledgeError("Git review mode requires review on the trusted branch", 409)
        decision = payload.get("decision")
        if decision not in ("approve", "reject", "revalidate"):
            raise KnowledgeError("Invalid review decision")
        with self.repo.lock():
            record = self.get(identity, clue_id, "clue", write=True)
            self._check_revision(record, payload.get("expected_revision"))
            if decision == "revalidate" and record.meta.get("status") != "stale":
                raise KnowledgeError("Only stale clues can be revalidated", 409)
            if decision != "revalidate" and record.meta.get("status") != "proposed":
                raise KnowledgeError("Only proposed clues can be approved or rejected", 409)
            status = "rejected" if decision == "reject" else "approved"
            stamp = now_iso()
            previous = dict(record.meta.get("review", {}))
            review = {
                **previous,
                "actor": identity.id,
                "decision": decision,
                "at": stamp,
                "reason": payload.get("reason", ""),
                "fact_version": record.meta.get("fact_version"),
            }
            if status == "approved":
                review["approved_at"] = stamp
            meta = {**record.meta, "status": status, "review": review, "updated_at": stamp}
            self._save(
                identity,
                {record.path: dump_document(meta, record.body)},
                f"Review clue: {decision}",
                {record.path: record.revision},
            )
            return self.detail(identity, clue_id)

    def promotion(self, identity, clue_id, payload, commit=False):
        payload = self.redactor.clean(payload)
        if self.settings.review_mode == "git" and commit:
            raise KnowledgeError("Promote through normal git review", 409)
        with self.repo.lock():
            clue = self.get(identity, clue_id, "clue", write=True)
            operation_id = payload.get("operation_id")
            if commit and (not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 200):
                raise KnowledgeError("Provide a bounded operation_id")
            previous = clue.meta.get("promotion", {})
            if commit and previous.get("operation_id") == operation_id:
                runbook = self.get(identity, previous["runbook_id"], "runbook")
                return {
                    "clue": serialized(clue),
                    "runbook": serialized(runbook),
                    "revision": self.repo.head(),
                }
            if clue.meta.get("status") != "approved":
                raise KnowledgeError("Approve the clue before promotion", 409)
            runbook_id = payload.get("runbook_id")
            if runbook_id:
                runbook = self.get(identity, runbook_id, "runbook", write=True)
                if runbook.scope != clue.scope:
                    raise KnowledgeError("Runbook must use the clue scope")
                self._check_revision(runbook, payload.get("expected_runbook_revision"))
                meta, body, path, revision = (
                    dict(runbook.meta),
                    runbook.body,
                    runbook.path,
                    runbook.revision,
                )
            else:
                meta = self._new(
                    identity,
                    "runbook",
                    payload.get("runbook_title", clue.meta["title"]),
                    clue.scope,
                    service_ids=clue.meta.get("service_ids", []),
                    alerts=clue.meta.get("alerts", []),
                    steps=[],
                )
                body, path, revision = "", self._path(meta, clue.scope), ""
            step_id = payload.get("step_id") or f"step-{len(meta.get('steps', [])) + 1}"
            if not isinstance(step_id, str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9-]{0,62}", step_id
            ):
                raise KnowledgeError("Invalid stable step ID")
            heading, content = payload.get("heading", ""), payload.get("content", "")
            if (
                not isinstance(heading, str)
                or not heading.strip()
                or len(heading) > 200
                or "\n" in heading
            ):
                raise KnowledgeError("Provide a single-line step heading")
            if not isinstance(content, str) or not content.strip() or len(content) > 16000:
                raise KnowledgeError("Provide bounded step instructions")
            steps = meta.get("steps", [])
            if isinstance(steps, dict):
                steps = [{"id": k, "heading": v} for k, v in steps.items()]
            steps = list(steps)
            if any(s.get("id") == step_id for s in steps):
                raise KnowledgeError("Step already exists; choose a new step ID", 409)
            steps.append({"id": step_id, "heading": heading})
            meta.update(steps=steps, updated_at=now_iso())
            new_body = body.rstrip() + f"\n\n## {heading}\n\n{content}\n"
            diff = "".join(
                difflib.unified_diff(
                    body.splitlines(True), new_body.splitlines(True), fromfile=path, tofile=path
                )
            )
            if not commit:
                return {"diff": diff, "runbook_revision": revision, "step_id": step_id}
            self._check_revision(clue, payload.get("expected_revision"))
            clue_meta = {
                **clue.meta,
                "promotion": {
                    "operation_id": operation_id,
                    "runbook_id": meta["id"],
                    "step_id": step_id,
                    "at": now_iso(),
                },
                "updated_at": now_iso(),
            }
            committed = self._save(
                identity,
                {
                    path: dump_document(meta, new_body),
                    clue.path: dump_document(clue_meta, clue.body),
                },
                "Promote approved clue to runbook",
                {path: revision, clue.path: clue.revision},
            )
            return {
                "clue": self.detail(identity, clue_id),
                "runbook": serialized(self.get(identity, meta["id"], "runbook")),
                "revision": committed,
            }

    def incident(self, identity, payload, object_id=None):
        payload = self.redactor.clean(payload)
        with self.repo.lock():
            if object_id:
                existing = self.get(identity, object_id, "incident", write=True)
                self._check_revision(existing, payload.get("expected_revision"))
                meta, body, path = dict(existing.meta), existing.body, existing.path
                expected = {path: existing.revision}
                for key in ("title", "state", "links", "service_ids", "resolved_at"):
                    if key in payload:
                        meta[key] = payload[key]
                body = payload.get("body", body)
            else:
                scope = payload.get("scope", "shared")
                meta = self._new(
                    identity,
                    "incident",
                    payload.get("title", "Incident"),
                    scope,
                    state=payload.get("state", "open"),
                    links=payload.get("links", []),
                    service_ids=payload.get("service_ids", []),
                    started_at=now_iso(),
                )
                body, path = payload.get("body", ""), self._path(meta, scope)
                expected = {path: ""}
            if meta.get("state") not in ("open", "resolved"):
                raise KnowledgeError("Invalid incident state")
            for link in meta.get("links", []):
                if not isinstance(link, str) or not re.match(r"^https?://[^\s]+$", link):
                    raise KnowledgeError("Incident links must use HTTP or HTTPS")
            scope = existing.scope if object_id else payload.get("scope", "shared")
            self._references(identity, scope, meta.get("service_ids"))
            meta["updated_at"] = now_iso()
            self._save(identity, {path: dump_document(meta, body)}, "Record incident", expected)
            return serialized(self.get(identity, meta["id"], "incident"))

    def outcome(self, identity, payload):
        payload = self.redactor.clean(payload)
        key = payload.get("idempotency_key")
        if not isinstance(key, str) or not 1 <= len(key) <= 200:
            raise KnowledgeError("Provide a bounded idempotency key")
        with self.repo.lock():
            runbook = self.get(identity, payload.get("runbook_id"), "runbook", write=True)
            for record in self.records(identity, "outcome"):
                if record.scope == runbook.scope and record.meta.get("idempotency_key") == key:
                    return serialized(record)
            self._check_revision(runbook, payload.get("revision"))
            steps = runbook.meta.get("steps", [])
            step_ids = list(steps) if isinstance(steps, dict) else [s.get("id") for s in steps]
            if payload.get("step_id") not in step_ids:
                raise KnowledgeError("Unknown runbook step")
            if payload.get("result") not in ("success", "failure", "partial"):
                raise KnowledgeError("Invalid outcome result")
            versions = {}
            for clue_id in payload.get("clue_ids", []):
                clue = self.get(identity, clue_id, "clue")
                if clue.scope != runbook.scope:
                    raise KnowledgeError("Outcome clue must use the runbook scope")
                versions[clue.id] = clue.meta.get("fact_version", clue.revision)
            self._references(identity, runbook.scope, incident=payload.get("incident_id"))
            meta = self._new(
                identity,
                "outcome",
                f"Runbook outcome: {payload['result']}",
                runbook.scope,
                idempotency_key=key,
                runbook_id=runbook.id,
                revision=runbook.revision,
                runbook_revision=runbook.revision,
                step_id=payload["step_id"],
                result=payload["result"],
                clue_ids=payload.get("clue_ids", []),
                clue_versions=versions,
                incident_id=payload.get("incident_id"),
                actor=identity.id,
                timestamp=now_iso(),
            )
            path = self._path(meta, runbook.scope)
            self._save(
                identity,
                {path: dump_document(meta, str(payload.get("note", "")))},
                "Record redacted runbook outcome",
                {path: ""},
            )
            return serialized(self.get(identity, meta["id"], "outcome"))

    def stale_check(self, clock: datetime | None = None):
        clock = clock or datetime.now(UTC)
        service = Identity(
            "hindsight", "Hindsight", "hindsight@localhost", ("shared",), ("shared",)
        )
        changed = 0
        with self.repo.lock():
            records = self.repo.documents()
            scopes = tuple(sorted({r.scope for r in records}))
            service = Identity("hindsight", "Hindsight", "hindsight@localhost", scopes, scopes)
            outcomes = [
                r for r in records if r.kind == "outcome" and r.meta.get("result") == "success"
            ]
            changes, expected = {}, {}
            for clue in records:
                if clue.kind != "clue" or clue.meta.get("status") != "approved":
                    continue
                review = clue.meta.get("review", {})
                last = str(
                    review.get(
                        "approved_at", review.get("at", clue.meta.get("updated_at", now_iso()))
                    )
                )
                fact_version = clue.meta.get("fact_version", clue.revision)
                for outcome in outcomes:
                    if outcome.scope != clue.scope:
                        continue
                    if outcome.meta.get("clue_versions", {}).get(clue.id) == fact_version:
                        last = max(
                            last,
                            str(outcome.meta.get("timestamp", outcome.meta.get("created_at", ""))),
                        )
                try:
                    age = clock - datetime.fromisoformat(last)
                except (ValueError, TypeError):
                    continue
                if age >= timedelta(days=self.settings.stale_after_days):
                    meta = {
                        **clue.meta,
                        "status": "stale",
                        "updated_at": clock.isoformat(),
                        "stale_reason": "No recent successful confirmation",
                        "last_confirmed_at": last,
                    }
                    changes[clue.path] = dump_document(meta, clue.body)
                    expected[clue.path] = clue.revision
                    changed += 1
            if changes:
                self._save(service, changes, "Flag unconfirmed clues as stale", expected)
        return changed
