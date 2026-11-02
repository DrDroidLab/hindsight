---
description: >-
  Run Hindsight in production: storage layout, configuration, health checks, users and API keys, git sync and conflicts, backup, restore and upgrades.
---

# Operations

## Storage and process model

Run **one worker / one active writer per mounted data volume**. No multi-replica writer support or high-availability claim is made. The image runs as UID/GID 10001 and includes Python, git, SSH client, bundled static UI and synthetic seed, with no Node server or build toolchain in the runtime.

| Path | Role | Backup |
| --- | --- | --- |
| `/data/repo` | canonical local knowledge clone, local commits may not yet be pushed | Preserve, especially while pending sync |
| `/data/private` | durable identity/auth database, pepper, lock/operation state | Separate encrypted consistent backup, mandatory |
| `/data/index` | disposable SQLite FTS/derived search state | Rebuildable, not authoritative |
| `/data/demo-remote.git` | demo-only local bare remote | Same volume; **not** an independent disaster-recovery copy |

Compose creates a named `hindsight_data` volume. Do not use `docker compose down -v` on valuable data; it deletes the volume including private authority. Bind mounts must be owned/writable by 10001 and private; never solve permissions by running the app as root or making private state world-readable.

## Configuration

See `.env.example`. Behind TLS termination, set `PUBLIC_ORIGIN=https://knowledge.example.invalid` to the exact external HTTPS origin, plus exact `ALLOWED_HOSTS`/`ALLOWED_ORIGINS`. The app deliberately does not trust forwarded headers to decide origin or cookie security. Normal mode requires `GIT_REMOTE`; empty writable remotes can be initialized. `GIT_BRANCH` defaults to main, sync interval to 60 seconds, and staleness threshold to 90 days. Use `HINDSIGHT_DATA_DIR` for local runs or the CLI global `--data-dir` **before** the subcommand. Container storage is `/data`. Do not change data directories casually: a new private directory means a different auth authority, not an index reset.

`READ_ONLY=true` disables knowledge mutations. A remote you can fetch but cannot push is not shared durability: treat pending state as local-only until credentials permit push. `GIT_AUTH` is a description of the chosen mechanism, not a magic portable token variable.

SSH deployments should mount a dedicated `.ssh` directory at `/home/hindsight/.ssh:ro` with narrowly scoped key, restrictive key permissions, and verified known_hosts. Use a remote hostname alias/config only if the runtime policy supports it; test an actual push, not just clone. HTTPS deployments require an explicitly supported noninteractive credential helper/private runtime config. Do not assume your host helper/global git config is inherited: hardened git execution may deliberately ignore global/system config. Check the configured mechanism in your image before relying on it. Never embed tokens in URLs or tracked files.

Optional `LLM_ENDPOINT`, `LLM_MODEL`, `LLM_KEY` and `EMBEDDINGS_ENDPOINT`, `EMBEDDINGS_MODEL`, `EMBEDDINGS_KEY` configure trusted own-key/local providers. No model is needed for manual capture, review, or FTS. Use your orchestrator's secret injection or protected environment file; no automatic `_FILE` convention is implied. Plain environment variables can be inspected by Docker operators, so restrict operator access. Review provider retention/logging independently.

## Health, readiness, logs

`GET /health` reports process availability. `GET /ready` checks initialized knowledge/index readiness; usable offline clones can remain ready while upstream is down. These are not proof of remote push permission, successful hook capture, or model availability. Observe the UI `sync_state` for synced/pending/conflict/operator-action status.

Keep logs sanitized and minimal; disable proxy body/header logging, request body disk buffering, and debug tracing. Never paste bootstrap token/key output into issue reports or CI artifacts. Enrollment tokens are explicitly generated on operator request, not startup logs.

## Users and keys

```sh
docker compose exec hindsight hindsight admin bootstrap
# After setup, create a verified teammate (a new secret is displayed once):
docker compose exec hindsight hindsight admin create-user \
  --name 'Example Engineer' --email 'engineer@example.invalid' \
  --read-scopes shared,platform --write-scopes platform
# Local CLI: global option comes first.
hindsight --data-dir /private/hindsight admin issue-key USER_UUID
hindsight --data-dir /private/hindsight admin revoke-key KEY_PREFIX_OR_ID
```

Verify the real person's details and scope before issuing a key. Share secrets through your secure organization channel, not chat logs/project config. Browser session lifetime/revocation is separate from a key's raw-secret storage. Private-state loss requires a deliberate recovery/re-enrollment process; it is not fixed by cloning knowledge.

## Sync and conflicts

Mutations validate an expected blob revision under the shared write lock, atomically write allowlisted files, commit with the verified human author/service committer, then update the projection. A git commit is the durable receipt. An index error cannot undo it; rebuild from git before retrying a write blindly.

Sync fetches/validates remote trees, fast-forwards where safe, and may merge nonconflicting divergence with a service-authored commit. Conflicts preserve local commits and the last valid working state, block unsafe writes, and require operator reconciliation. No force push, silent ours/theirs, indefinite rebase loop, or automatic conflict loss is acceptable. A git outage keeps local committed work available with explicit pending-sync feedback.

For a conflict: stop the writer, back up repo/private state, inspect both histories using a separate normal-git clone and safe tools, manually reconcile content/schema/IDs, commit with valid attribution, push normally, then restart and verify authorized recall and sync status. Preserve local unsynced commits; do not delete `/data/repo` to “fix” a conflict. Review remote content before activating it. Do not run arbitrary hooks/filters from an untrusted remote.

### External git review ceremony

Set `REVIEW_MODE=git` to commit captures as **proposed** clues on the proposal branch, configured by `PROPOSAL_BRANCH` (or `HINDSIGHT_PROPOSAL_BRANCH`), default `hindsight/proposals`. `GIT_BRANCH` remains the trusted branch (default `main`). Proposal captures, queue/recall visibility, sync, and capture/fingerprint deduplication are supported; proposals remain visibly unreviewed. Self-declared approval on the proposal branch is not treated as trusted approval.

The UI shows a Git review mode banner and disables clue edit/approve/reject/revalidate/promote controls. Review these changes in your existing normal-git workflow:

1. Inspect the redacted proposal and evidence on `hindsight/proposals`, including scope, stable IDs, references, and any runbook diff. Preserve the verified originating author and capture history.
2. An authorized human reviewer records the decision with `review.actor`, decision time/reason and appropriate trust metadata. For approval, merge the reviewed approved fact into the trusted branch through your normal protected-branch ceremony. Approval alone must not silently change a runbook; review a runbook promotion separately and retain the clue's promotion link/stable step ID.
3. For rejection, retain the rejected clue and fingerprint with the review reason/audit metadata on the **trusted branch** so rejection audit and identical-fact suppression survive rebuilds. Do not merely delete a rejected proposal and lose its rejection history.
4. Push normally; let Hindsight fetch and validate both branches, then verify the trusted status and proposal deduplication. Do not bypass branch protections, force push, or treat a client-written frontmatter flag alone as proof of human review.

There are no built-in hosting-provider PR creation/merge APIs or provider tokens. Operators remain responsible for branch protections, reviewer identity verification, and external git review enforcement. Incidents and explicitly reported outcomes still use normal authorized writes to the trusted branch in this mode. `READ_ONLY=true` is different: it disables **all** knowledge mutations, including capture, incidents, and outcomes, while preserving read access.


## Backup and restore

1. Stop the single writer (including any local stdio process), restrict volume access, and record local/remote HEAD plus pending-sync state.
2. Back up `/data/private` **including auth.sqlite and pepper**, and `/data/repo` including unsynced commits; preserve permissions and ownership. Use filesystem snapshots or SQLite's consistent backup facility if taking a live snapshot; copying only the main SQLite file during writes can omit WAL transactions.
3. Encrypt backups with separately managed keys and test restore in an isolated, network-restricted environment. Git remote backups are separate and must cover required history. For demo, the bare remote is in the same volume and can fail with it.
4. Restore private authority and matching knowledge into a private UID/GID-10001 volume while the app is stopped. Never restore an old auth database casually: it can resurrect revoked keys/membership/session state; reconcile revocation and rotate authority as appropriate.
5. Remove/rebuild only `/data/index`, restart one writer, verify identity/scopes, correct canonical revision, recall, outcome links, and pending sync before exposing traffic. Remote outage alone should not destroy readiness of a valid clone.

## Updating

Back up first, stop the old writer, pull the new image (`docker compose pull`, or set `HINDSIGHT_IMAGE` to the new version), and restart with the same persistent volume. Do not run old and new writers against it concurrently. Check release schema notes and test restore; no automatic cross-version migration guarantee is promised. Keep client hooks and provider settings independently versioned and verify `install --check` after client upgrades.
