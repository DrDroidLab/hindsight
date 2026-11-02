# Security model and deployment

## Trust boundaries

Hindsight is operational memory, not a privileged agent runtime. Untrusted inputs include agent events/transcripts, HTTP/MCP bodies, model outputs, git files, Markdown, skill instructions, links, and diffs. A human review can establish usefulness, not make arbitrary text safe to execute. Nothing in a clue grants production access.

Authentication authority is **private local state**, not git. `/data/private/auth.sqlite` contains users, membership, key HMAC digests, sessions, and single-use enrollment records; the private pepper file is equally durable and sensitive. API keys are random secrets shown once. Keep both SQLite and pepper on a mode-restricted encrypted volume. Private state is not disposable and cannot be reconstructed from knowledge. Protect client `~/.config/hindsight/` credentials/spool and any installation backups too.

The first operator runs `hindsight admin bootstrap` locally, enrolls once at `/setup`, and verifies real human name/email and write/read scopes. No anonymous self-registration or client-supplied git author is accepted. All writers can review their writable scope; there is no implied separate product admin hierarchy. Administration is an operator CLI responsibility.

## Browser and network

The browser exchanges a personal key for a short-lived HttpOnly, SameSite session; Secure cookies are used on HTTPS. The key never goes into localStorage, sessionStorage, URLs, or bundled assets. Cookie writes require a CSRF token, and the API checks Host/Origin. UI metadata, evidence, and diffs are escaped; Markdown raw HTML is disabled, remote images are omitted, and outbound links suppress referrers. A restrictive CSP disallows external assets and inline application scripts.

Compose binds `127.0.0.1:8080` by default. Never expose plain HTTP remotely. Configure your TLS proxy's exact hostname and HTTPS origin in `ALLOWED_HOSTS` and `ALLOWED_ORIGINS`, and **set `PUBLIC_ORIGIN=https://knowledge.example.invalid` to your exact external origin**. This trusted configuration determines Secure cookies and same-origin validation behind TLS termination; forwarded headers are deliberately not trusted for those decisions. Set proxy request limits too. Do not enable permissive CORS or trust arbitrary forwarded headers. See `docs/proxy.conf.example`.

Do not log request bodies, Authorization headers, keys, cookies, setup tokens, provider responses, or transcript text. Proxy/APM/error collectors may add logging or disk buffering outside Hindsight's control: disable those explicitly. The proxy example disables capture request buffering; verify all intermediary services too.

## Redaction and no-raw-store boundary

Agent adapters redact bounded supported transcript content in memory before transmission or optional local spool. The server independently redacts free text and metadata before git, index, provider requests, or durable work. Auth credentials are handled separately and must never become “redacted clues.” Verified identity email is an intentional private-auth/git-author attribution exception, not transcript evidence.

Rules cover known token/key/private-key patterns, URL credentials, password assignments, IP addresses, emails, customer-ID patterns, and hostname policy. Redaction uses stable `[REDACTED:type]` markers, normalization, bounded fields, and bounded regex runtime. Customize trusted operator rules, for example:

```yaml
allowed_hosts:
  - metrics.example.invalid
rules:
  - name: internal-account
    pattern: '\bDEMOACC-[0-9]{8}\b'
```

Mount the file read-only and set `REDACTION_RULES` to its container path. Validate custom rules on synthetic fixtures: overly broad rules can break legitimate knowledge, and insufficient rules can miss secrets. Do not put a sensitive hostname in a public rules example.

**No detector can guarantee finding every secret.** Avoid collecting secrets in the first place; inspect redacted evidence before approval, use least privilege, configure provider retention, and assess regulatory requirements yourself. Optional model requests contain redacted input but may still contain operational information. No hosted fallback is configured. Local endpoints can still log/retain prompts; “local” does not automatically mean private.

The no-new-raw-store guarantee covers Hindsight capture/write paths, not an existing remote's complete history: clone/fetch necessarily transfers pre-existing git objects. Start from a sanitized dedicated remote. Unsafe fetched trees are not activated, but quarantine does not erase fetched objects or upstream history. Imported history, backups, agent-owned original transcripts, external proxies, and provider retention require their own controls.

## Git and team isolation

The app validates paths/tree objects before activation; rejects symlinks, submodules, unsupported files, duplicate IDs, and malformed content; and does not execute remote hooks, filters, external diff, or skill instructions. Git credentials should be mounted read-only and least-privileged. Never embed credentials in GIT_REMOTE or mount your entire personal home or Docker socket.

API scopes filter before results, counts, snippets, ranking, references, and diffs. Shared-root records are readable to every member; shared writes require explicit permission. This is application-level isolation, **not encryption**. Anyone who can clone the remote can read all committed scopes and history. Separate security boundaries require separate instances/remotes/private volumes.

## Rotation and incident response

1. Restrict access; retain a safe record of affected revisions and involved systems without copying raw secrets.
2. Revoke affected Hindsight key prefixes/IDs via local `admin revoke-key`, issue a replacement only after verifying the user, and check current session policy. Key revocation alone should not be assumed to revoke an independently issued browser session; sign out/invalidate sessions or rotate private authority as needed.
3. Rotate upstream git/model/production credentials at their issuer if those were genuinely exposed. Do not assume redaction retroactively rotated anything.
4. Sanitize/rewrite affected remote history through your normal reviewed git incident procedure, including clones, derived index, provider caches, spools, proxies, and backups. Hindsight does not automatically rewrite history.
5. Restore validated knowledge and private auth from a consistent encrypted backup, rebuild the disposable index, and verify read/write scopes and audit attribution.

See [SECURITY.md](../SECURITY.md) for private reporting and [operations](operations.md) for backup/recovery.
