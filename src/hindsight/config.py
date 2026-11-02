"""Validated operator configuration; private state is never knowledge."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


@dataclass
class Settings:
    data_dir: Path = Path("/data")
    git_remote: str = ""
    git_branch: str = "main"
    sync_interval: int = 60
    stale_after_days: int = 90
    read_only: bool = False
    review_mode: str = "local"
    proposal_branch: str = "hindsight/proposals"
    public_origin: str = ""
    allowed_hosts: list[str] = field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )
    allowed_origins: list[str] = field(default_factory=list)
    llm_endpoint: str = ""
    llm_model: str = ""
    llm_key: str = ""
    embeddings_endpoint: str = ""
    embeddings_model: str = ""
    embeddings_key: str = ""
    redaction_rules: str = ""

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        if self.public_origin:
            origin = urlsplit(self.public_origin)
            if (
                origin.scheme not in ("http", "https")
                or not origin.hostname
                or origin.username
                or origin.password
                or origin.path not in ("", "/")
                or origin.query
                or origin.fragment
                or any(ord(c) < 33 for c in self.public_origin)
            ):
                raise ValueError(
                    "PUBLIC_ORIGIN must be an HTTP(S) origin without credentials or path"
                )
            if origin.scheme == "http" and origin.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("Non-loopback PUBLIC_ORIGIN requires HTTPS")
            if origin.port is not None and not 0 < origin.port <= 65535:
                raise ValueError("Invalid PUBLIC_ORIGIN port")
            self.public_origin = self.public_origin.rstrip("/")
        if self.review_mode not in ("local", "git"):
            raise ValueError("REVIEW_MODE must be local or git")
        for branch in (self.git_branch, self.proposal_branch):
            if (
                not branch
                or branch.startswith(("-", "/"))
                or any(x in branch for x in ("..", "@{", "\\", " ", ":", "~", "^", "?", "*", "["))
            ):
                raise ValueError("Invalid git branch")
        if self.sync_interval < 1 or self.stale_after_days < 1:
            raise ValueError("Intervals must be positive")
        for endpoint, model in (
            (self.llm_endpoint, self.llm_model),
            (self.embeddings_endpoint, self.embeddings_model),
        ):
            if bool(endpoint) != bool(model):
                raise ValueError("Provider endpoint and model must be configured together")
            if endpoint:
                parsed = urlsplit(endpoint)
                if (
                    parsed.scheme not in ("https", "http")
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError("Invalid provider endpoint")
                if parsed.scheme == "http" and parsed.hostname not in (
                    "localhost",
                    "127.0.0.1",
                    "::1",
                ):
                    raise ValueError("Remote provider endpoints require HTTPS")
        if self.git_remote:
            remote = urlsplit(self.git_remote)
            if remote.scheme and remote.scheme not in ("https", "ssh", "file"):
                raise ValueError("Unsupported git remote protocol")
            if remote.password or (remote.scheme == "https" and remote.username):
                raise ValueError("Credentials must not be embedded in GIT_REMOTE")
            if self.git_remote.startswith("-") or "::" in self.git_remote:
                raise ValueError("Unsafe git remote")

    @property
    def private_dir(self):
        return self.data_dir / "private"

    @property
    def repo_dir(self):
        return self.data_dir / "repo"

    @property
    def index_dir(self):
        return self.data_dir / "index"

    @classmethod
    def from_env(cls):
        values: dict[str, Any] = {}
        for name in cls.__dataclass_fields__:
            key = name.upper()
            if "HINDSIGHT_" + key in os.environ:
                key = "HINDSIGHT_" + key
            if key not in os.environ:
                continue
            value: Any = os.environ[key]
            if name in ("sync_interval", "stale_after_days"):
                value = int(value)
            elif name == "read_only":
                if value.lower() not in ("true", "false", "1", "0"):
                    raise ValueError("READ_ONLY must be boolean")
                value = value.lower() in ("true", "1")
            elif name in ("allowed_hosts", "allowed_origins"):
                value = [v.strip() for v in value.split(",") if v.strip()]
            values[name] = value
        return cls(**values)
