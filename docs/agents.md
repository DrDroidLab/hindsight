---
seo_title: "Connect Claude Code, Cursor, Codex and Gemini CLI to Hindsight over MCP"
description: >-
  Install Hindsight's MCP server, retrieval skill and capture hooks into Claude Code, Cursor, Codex and Gemini CLI, with per-client hook support and limits.
---

# Agent integrations and capability truthfulness

Hindsight supplies memory to an existing agent. It never replaces that agent's runtime or assumes its authority. Installers use the noncolliding **hindsight-sre** MCP name, private user-level credentials, owned configuration entries, collision refusal, dry-run/check/uninstall, and isolated backups. Do not use project-tracked secrets or paste keys into shell commands.

```sh
hindsight login --server http://localhost:8080 --scope shared
hindsight install claude --server http://localhost:8080 --dry-run
hindsight install claude --server http://localhost:8080
hindsight install claude --check
# Same contract for cursor, codex, gemini. Uninstall only Hindsight-owned entries:
hindsight install claude --uninstall
```

Keys are read via a private credential-aware launcher/proxy rather than committed config. Inspect the dry-run result, restart the client, and explicitly trust supported hooks. The retrieval guidance says recall first, load relevant skills, preserve unreviewed/stale labels, and treat returned content as data rather than new privileged instructions.

## Client-specific contracts

| Client | MCP config surface | Capture lifecycle and limitations |
| --- | --- | --- |
| Claude Code | user `mcpServers.hindsight-sre`, direct HTTP or private stdio proxy | `hooks.Stop` primary; `SessionEnd` best effort; command hook receives session/transcript fields |
| Cursor | `~/.cursor/mcp.json` | `~/.cursor/hooks.json` version 1: `stop`, `sessionEnd`; nullable transcript means explicit-summary fallback; managed Cloud Agents do not imply desktop sessionEnd support |
| Codex | `~/.codex/config.toml`, `[mcp_servers.hindsight-sre]` | current official `hooks.Stop`/`hooks.SessionEnd` group schema in `~/.codex/hooks.json`; SessionEnd main-thread-only and maximum three seconds; older `notify` is turn-complete, not session end |
| Gemini CLI | `~/.gemini/settings.json`, `mcpServers.hindsight-sre` | `hooks.AfterAgent` primary; `SessionEnd` best effort, may not be awaited; command timeout units are milliseconds; emit harmless expected JSON |

An installed schema is not proof your client's exact version/policy accepts it. `install --check` reports configured capabilities; it is not a live agent certification. Missing transcript access, unsupported versions, managed restrictions, untrusted hooks, crashes, and timeout/forced exit can prevent delivery. Capture never scrapes arbitrary workspace files or forces another agent turn. Supported hooks read bounded owned nonsymlink transcript files from known roots, drop tool payloads/environment dumps, redact locally, and use stable IDs/checkpoints to deduplicate repeated lifecycle events.

Optional retry spool contains **only redacted** bounded payloads with private permissions and limited lifetime. Replay explicitly using `hindsight capture --retry`. No raw Hindsight transcript archive is created; the original agent may independently retain its own transcripts under its own policy.

## Explicit fallback and generic MCP

```sh
# Use stdin for operational content rather than sensitive shell arguments.
printf '%s' 'Synthetic investigation summary; verify before reuse.' | hindsight capture --scope shared
hindsight recall 'CrashLoopBackOff' --scope shared
```

Any compatible client can run the private credential-reading `hindsight mcp-proxy` stdio bridge or authenticated Streamable HTTP `/mcp`. Native `/clue` custom commands exist only where that client supports them; there is no invented common slash-command API. Use explicit CLI/MCP proposal everywhere else.

`report_outcome` is explicit: provide a validated runbook revision/step and success/failure/partial evidence. A hook cannot infer that a fix actually succeeded. Incident/ticket links are references, not live connectors.

## Verification status

Offline tests use isolated fake homes, official-schema synthetic hook payloads, parseable JSON/TOML, safe transcript fixtures, mocked HTTP/providers, and real MCP protocol clients where covered. They do **not** run proprietary desktop UIs or establish minimum certified client versions. This implementation has no certified live-client version matrix yet; do not interpret a fixture label or detected version as a passed live integration.

Before claiming a version supported in production, record the actual executable version, OS, managed policy/trust settings, configuration recognition, authenticated tool discovery/recall, and one genuine supported lifecycle hook event. Keep results synthetic and opt-in. Unavailable clients/credentials are **unverified**, never a pass.

Official schema references (recheck when updating adapters):

- https://code.claude.com/docs/en/hooks and https://code.claude.com/docs/en/mcp
- https://cursor.com/docs/hooks and https://cursor.com/docs/mcp
- https://developers.openai.com/codex/hooks and https://developers.openai.com/codex/config-reference
- https://geminicli.com/docs/hooks/reference/ and https://geminicli.com/docs/tools/mcp-server/
