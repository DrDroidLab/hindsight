# Security policy

Hindsight handles incident evidence and credentials. Use only a dedicated sanitized knowledge remote, keep the instance private behind HTTPS, and maintain encrypted backups of both knowledge and private auth state.

**Redaction is best effort. It cannot guarantee detection of every secret or erase existing git history.** Do not use real secrets in tests or issue reports. Knowledge-reader scopes do not hide history from someone with raw remote access. Hindsight never runs production commands, executes skill text, or treats agent transcripts as privileged instructions.

## Reporting a vulnerability

Do not file a public issue containing exploit details, real transcripts, credentials, private hostnames, or customer data. Use the repository's GitHub **Security → Report a vulnerability** private advisory channel if enabled: https://github.com/DrDroidLab/hindsight/security/advisories/new. If unavailable, open a minimal public issue asking maintainers for a private reporting channel without including the vulnerability or sensitive material. No response-time SLA is promised.

Include an affected revision, synthetic reproduction, expected/actual behavior, and the trust boundary involved. State whether git history, private auth, provider requests, logs, or browser output were exposed. Rotate genuinely exposed credentials using their issuer and follow the [incident recovery guidance](docs/security.md).

This repository is early-stage software. Automated tests reduce risk but are not an independent security audit or a warranty. Review [the deployment threat model and limitations](docs/security.md) before use.
