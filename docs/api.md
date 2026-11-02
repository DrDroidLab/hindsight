---
description: >-
  Reference for Hindsight's HTTP review API and its seven MCP tools: recall, get_runbook, list_skills, load_skill, propose_clue, report_outcome and whoami.
---

# HTTP and MCP reference

All API responses are same-origin JSON. Private endpoints require a Bearer key or the HttpOnly browser session. Cookie-authenticated mutations additionally require `X-CSRF-Token`; exchange keys through `POST /api/session`, not a URL. Do not enable wildcard CORS. Errors expose sanitized `{detail: string}` and appropriate 400/401/403/404/409/413 status (503 may indicate an unavailable dependency). Inaccessible IDs use not-found behavior.

## Browser auth

- `GET /api/status`: setup-required flag and sanitized sync state.
- `POST /api/setup {token,name,email,team}`: atomic single-use first enrollment; returns identity and one-time key.
- `POST /api/session {key}`: session/CSRF cookies and `{identity,csrf_token}`.
- `GET /api/whoami`: identity/read/write scopes, CSRF token for browser reload, sync state; never the API key.
- `DELETE /api/session`: authenticated CSRF-protected logout.

## Review

- `GET /api/review?scope=&status=proposed&service=&cursor=`: scoped `items`, `counts`, `next_cursor`, `sync_state`. Counts are not global unauthorized totals.
- `GET /api/clues/{id}`: serialized record, revision, linked outcomes.
- `PATCH /api/clues/{id} {expected_revision,title?,body?,service_ids?,alerts?,confidence?}`: redacted validated edit; numeric confidence `[0,1]`.
- `POST /api/clues/{id}/review {expected_revision,decision,reason?}`: `approve`, `reject`, or `revalidate`; trust transition remains a git commit.
- `GET /api/services`, `GET /api/runbooks`, `GET /api/runbooks/{id}`: visible typed knowledge.
- `POST /api/clues/{id}/promotion-preview {runbook_id,expected_runbook_revision,step_id?,heading,content}`: escaped unified diff and runbook revision. Omit `runbook_id` and supply `runbook_title` for API-created runbooks.
- `POST /api/clues/{id}/promote`: preview fields plus `expected_revision` and stable `operation_id`; requires approved clue, updates clue/runbook together, separate from approval. Retry with the same operation ID after uncertain delivery; do not generate a fresh operation for a network retry.

Always use the returned blob `revision`, not a guessed git HEAD. A 409 means no silent overwrite; reload/reconcile. Approval may have succeeded even if the later promotion conflicts: show the precise partial state.

## Capture, retrieval, incident, outcome

- `POST /api/captures {capture_id,agent,session_id,scope,summary,excerpt?,service_ids?,alerts?,incident_id?}` returns committed draft IDs, extraction state, revision, and sync state. No configured model means a manual redacted draft, not raw transcript storage.
- `GET /api/recall?query=&service=&alert=&scope=&limit=` returns scoped bounded results with trust labels and index/source revisions.
- `GET/POST /api/incidents`, `GET/PATCH /api/incidents/{id}`: list/create/read/edit authorized incident metadata. PATCH needs expected revision.
- `POST /api/outcomes {idempotency_key,runbook_id,revision,step_id,result,note?,clue_ids?,incident_id?}`: explicitly reported success/failure/partial for a validated step/revision.

Serialized records include `{id,kind,title,scope,revision,body,meta}` and convenient flattened metadata. Treat body, source, metadata, and diffs as untrusted text.

## MCP

Official Python SDK Streamable HTTP endpoint: `/mcp`, authenticated independently on each request. `hindsight mcp --transport stdio` uses local data/authority; `hindsight mcp-proxy` bridges a stdio client to an authenticated remote instance with private client credentials. Protocol stdout is reserved for MCP messages.

Tools: `recall`, `get_runbook`, `list_skills`, `load_skill`, `propose_clue`, `report_outcome`, `whoami`. The original design groups skill listing/loading, but there are seven actual tool names. `load_skill` accepts an optional `file` naming an allowlisted safe text attachment; it returns text without executing it. No broad MCP incident/review/admin mutator is implied. Tools return data, not trusted higher-priority instructions; never infer production execution permission from a result.
