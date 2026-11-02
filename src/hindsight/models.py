"""Human-editable Markdown records with bounded, safe YAML frontmatter."""

import math
import re
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import yaml


class KnowledgeError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Identity:
    id: str
    name: str
    email: str
    read_scopes: tuple[str, ...]
    write_scopes: tuple[str, ...]

    def can_read(self, scope):
        return scope == "shared" or scope in self.read_scopes

    def can_write(self, scope):
        return scope in self.write_scopes


@dataclass
class Record:
    path: str
    meta: dict
    body: str
    revision: str = ""
    scope: str = "shared"

    @property
    def id(self):
        return self.meta["id"]

    @property
    def kind(self):
        return self.meta["kind"]


SLUG = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")
KINDS = {
    "services": "service",
    "runbooks": "runbook",
    "clues": "clue",
    "skills": "skill",
    "incidents": "incident",
    "outcomes": "outcome",
}


def validate_path(path):
    if (
        not isinstance(path, str)
        or len(path) > 512
        or "\\" in path
        or any(ord(c) < 32 or ord(c) == 127 for c in path)
    ):
        raise KnowledgeError("Unsafe knowledge path")
    parts = path.split("/")
    if PurePosixPath(path).is_absolute() or any(
        p in ("", ".", "..") or p.startswith(".") for p in parts
    ):
        raise KnowledgeError("Unsafe knowledge path")
    scope = "shared"
    if parts[0] == "teams":
        if len(parts) < 4 or not SLUG.fullmatch(parts[1]):
            raise KnowledgeError("Invalid team path")
        scope, parts = parts[1], parts[2:]
    if parts[0] not in KINDS:
        raise KnowledgeError("Unsupported knowledge folder")
    if parts[0] == "skills":
        if len(parts) < 3 or len(parts) > 7 or not SLUG.fullmatch(parts[1]):
            raise KnowledgeError("Unsupported skill attachment")
        if len(parts) == 3 and parts[2] == "SKILL.md":
            return scope, "skill"
        if any(not SLUG.fullmatch(part) for part in parts[2:-1]):
            raise KnowledgeError("Unsafe skill attachment directory")
        filename = parts[-1]
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", filename)
            or ".." in filename
            or PurePosixPath(filename).suffix not in (".md", ".txt", ".json", ".yaml", ".yml")
        ):
            raise KnowledgeError("Unsupported skill attachment type")
        return scope, "skill_attachment"
    elif len(parts) != 2 or not parts[1].endswith(".md") or not SLUG.fullmatch(parts[1][:-3]):
        raise KnowledgeError("Invalid document path")
    return scope, KINDS[parts[0]]


def _bounded(value, depth=0):
    if depth > 12:
        raise KnowledgeError("Frontmatter too deeply nested")
    if isinstance(value, dict):
        if len(value) > 128 or any(not isinstance(k, str) for k in value):
            raise KnowledgeError("Invalid frontmatter mapping")
        for v in value.values():
            _bounded(v, depth + 1)
    elif isinstance(value, list):
        if len(value) > 512:
            raise KnowledgeError("Frontmatter list too large")
        for v in value:
            _bounded(v, depth + 1)
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise KnowledgeError("Frontmatter values must be JSON-compatible")


class _Loader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for k, v in node.value:
        key = loader.construct_object(k)
        if not isinstance(key, str) or key in result:
            raise KnowledgeError("Duplicate or invalid frontmatter key")
        result[key] = loader.construct_object(v)
    return result


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
# Keep ISO timestamps as strings, not datetime objects.
_Loader.yaml_implicit_resolvers = {
    k: [(t, r) for t, r in v if t != "tag:yaml.org,2002:timestamp"]
    for k, v in _Loader.yaml_implicit_resolvers.items()
}


def parse_document(path, text, revision=""):
    scope, kind = validate_path(path)
    if kind == "skill_attachment":
        raise KnowledgeError("Skill attachments are not standalone records")
    if not isinstance(text, str):
        raise KnowledgeError("Document must be UTF-8 text")
    if len(text.encode("utf-8")) > 262144 or any(ord(c) < 32 and c not in "\n\r\t" for c in text):
        raise KnowledgeError("Document is too large or contains binary data")
    meta, body = {}, text
    if text.startswith("---\n"):
        pieces = text.split("\n---\n", 1)
        if len(pieces) != 2 or len(pieces[0]) > 65536:
            raise KnowledgeError("Invalid frontmatter")
        front, body = pieces[0][4:], pieces[1]
        try:
            for token in yaml.scan(front):
                if isinstance(
                    token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken, yaml.tokens.TagToken)
                ):
                    raise KnowledgeError("YAML aliases and custom tags are not supported")
            meta = yaml.load(front, Loader=_Loader)
        except (yaml.YAMLError, RecursionError) as exc:
            raise KnowledgeError("Invalid YAML frontmatter") from exc
    if kind == "skill":
        meta = dict(meta or {})
        meta.setdefault("id", str(uuid.uuid5(uuid.NAMESPACE_URL, "hindsight:" + path)))
        meta.setdefault("kind", "skill")
        meta.setdefault("title", meta.get("name", path.split("/")[-2]))
    if not isinstance(meta, dict):
        raise KnowledgeError("Frontmatter must be a mapping")
    _bounded(meta)
    if (
        meta.get("kind") != kind
        or not isinstance(meta.get("title"), str)
        or not meta["title"].strip()
        or len(meta["title"]) > 500
        or not isinstance(meta.get("id"), str)
    ):
        raise KnowledgeError("Document requires id, matching kind, and title")
    try:
        uuid.UUID(meta["id"])
    except (ValueError, AttributeError) as exc:
        raise KnowledgeError("Document id must be a UUID") from exc
    for key in ("service_ids", "clue_ids", "runbook_ids", "dependencies", "alerts"):
        if key in meta and (
            not isinstance(meta[key], list) or any(not isinstance(x, str) for x in meta[key])
        ):
            raise KnowledgeError("Invalid reference list")
    if "source" in meta:
        source = meta["source"]
        if not isinstance(source, dict) or any(
            "path" in key.lower() or "transcript" in key.lower() for key in source
        ):
            raise KnowledgeError("Source must not include transcript paths")
        session = source.get("session_id", "")
        if not isinstance(session, str) or len(session) > 256 or "/" in session or "\\" in session:
            raise KnowledgeError("Source session identifier must be opaque")
        for key in ("url", "link"):
            if source.get(key):
                _safe_url(source[key])
    links = meta.get("links", [])
    if not isinstance(links, list):
        raise KnowledgeError("External links must be a list")
    for link in links:
        _safe_url(link)
    if kind == "clue":
        if meta.get("status") not in ("proposed", "approved", "rejected", "stale"):
            raise KnowledgeError("Invalid clue status")
        confidence = meta.get("confidence", 0.5)
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (float, int))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise KnowledgeError("Invalid confidence")
    if kind == "incident" and meta.get("state", "open") not in ("open", "resolved"):
        raise KnowledgeError("Invalid incident state")
    if kind == "outcome" and meta.get("result") not in ("success", "failure", "partial"):
        raise KnowledgeError("Invalid outcome result")
    if kind == "runbook" and "steps" in meta:
        steps = meta["steps"]
        if isinstance(steps, list):
            if any(
                not isinstance(s, dict)
                or not isinstance(s.get("id"), str)
                or not isinstance(s.get("heading"), str)
                for s in steps
            ):
                raise KnowledgeError("Invalid runbook steps")
            if len({s["id"] for s in steps}) != len(steps):
                raise KnowledgeError("Duplicate runbook step ID")
            steps = {s["id"]: s["heading"] for s in steps}
        if not isinstance(steps, dict) or any(
            not SLUG.fullmatch(k)
            or not isinstance(v, str)
            or not re.search(r"^#{1,6}\s+" + re.escape(v) + r"\s*$", body, re.M)
            for k, v in steps.items()
        ):
            raise KnowledgeError("Runbook steps must map stable IDs to body headings")
    return Record(path, meta, body, revision, scope)


def _safe_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise KnowledgeError("Invalid external link")
    try:
        parsed = urlsplit(re.sub(r"\[REDACTED:[a-z-]+\]", "redacted.invalid", value))
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError()
    except ValueError as exc:
        raise KnowledgeError("External links must use safe HTTP or HTTPS URLs") from exc


def dump_document(meta, body):
    _bounded(meta)
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n" + body
