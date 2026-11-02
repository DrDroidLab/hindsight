"""Best-effort bounded redaction, not a guarantee of detecting all secrets."""

import hashlib
import ipaddress
import json
import unicodedata
from pathlib import Path

import regex
import yaml

from .models import KnowledgeError


class Redactor:
    VERSION = "1"

    def __init__(self, rules_path=None):
        self.allowed_hosts = set()
        rules = [
            (
                "private-key",
                r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
            ),
            (
                "token",
                r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|hsk_[A-Za-z0-9_-]{20,})\b",
            ),
            ("credential", r"(?i)\b(?:bearer\s+)[A-Za-z0-9_.~+/=-]{12,}"),
            (
                "password",
                r"""(?i)\b(?:password|passwd|api[_-]?key|secret|access[_-]?token)\s*[:=]\s*["']?[^\s"',;]{3,}""",
            ),
            ("url-credentials", r"(?i)(?<=://)[^\s/@:]+:[^\s/@]+@"),
            ("email", r"\b[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
            ("customer-id", r"(?i)\b(?:customer|cust|tenant)[_-](?:id[_:= -]*)?[A-Za-z0-9]{4,}\b"),
            ("ip", r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"),
            ("ipv6", r"(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:]{0,39}(?![\w:])"),
        ]
        if rules_path:
            path = Path(rules_path)
            if path.stat().st_size > 65536:
                raise KnowledgeError("Redaction configuration too large")
            try:
                config = yaml.safe_load(path.read_text()) or {}
                self.allowed_hosts = set(config.get("allowed_hosts", []))
                for rule in config.get("rules", []):
                    if (
                        not isinstance(rule, dict)
                        or len(rule.get("pattern", "")) > 1024
                        or not regex.fullmatch(r"[a-z-]{1,32}", rule.get("name", ""))
                    ):
                        raise ValueError()
                    rules.append((rule["name"], rule["pattern"]))
            except (ValueError, TypeError, yaml.YAMLError) as exc:
                raise KnowledgeError("Invalid redaction configuration") from exc
        try:
            self.rules = [(name, regex.compile(pattern)) for name, pattern in rules]
        except regex.error as exc:
            raise KnowledgeError("Invalid redaction pattern") from exc
        self.hostname = regex.compile(
            r"(?i)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:internal|local|corp|com|net|org|io|dev|cloud)\b"
        )
        self.version = hashlib.sha256(
            json.dumps([self.VERSION, rules, sorted(self.allowed_hosts)]).encode()
        ).hexdigest()

    def redact(self, text):
        if not isinstance(text, str) or len(text.encode("utf-8")) > 262144:
            raise KnowledgeError("Text exceeds redaction limit", 413)
        text = unicodedata.normalize("NFKC", text)
        try:
            for name, rule in self.rules:

                def replace(match, name=name):
                    value = match.group()
                    if name in ("ip", "ipv6"):
                        try:
                            ipaddress.ip_address(value)
                        except ValueError:
                            return value
                        if value in self.allowed_hosts:
                            return value
                    return f"[REDACTED:{name}]"

                text = rule.sub(replace, text, timeout=0.05)
            text = self.hostname.sub(
                lambda m: (
                    m.group() if m.group().lower() in self.allowed_hosts else "[REDACTED:hostname]"
                ),
                text,
                timeout=0.05,
            )
        except TimeoutError as exc:
            raise KnowledgeError("Redaction rule exceeded safe runtime") from exc
        return text

    def clean(self, value):
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, dict):
            return {self.redact(k): self.clean(v) for k, v in value.items()}
        return value

    __call__ = redact
