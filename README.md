# Hindsight

**Operational memory underneath your engineer's agent. Review what it learned before your team relies on it.**

Hindsight turns redacted session learnings into a **review queue**, not another agent dashboard. Inspect evidence, edit a clue without an LLM, approve or reject it, then separately preview and accept a runbook change. Typed services, runbooks, clues, skills, incidents, and outcomes live in human-readable Markdown in git.

- **Your agent stays in charge.** Recall context through MCP in Claude Code, Cursor, Codex, Gemini CLI, or another MCP client. Hindsight is not an AI SRE runtime and does not execute production commands. OpenSRE and other investigation agents are complementary.
- **No telemetry. No required model.** Manual capture, review, and FTS retrieval work offline against a local knowledge repository. Optional extraction and embeddings use your explicitly configured own-key or local OpenAI-compatible endpoint.
- **Trust is explicit.** Proposed means unreviewed. Stale means re-check the evidence. A reported success does not approve a clue, and approval does not silently edit a runbook.
- **Git is knowledge, not authentication.** Each human write preserves verified author attribution. Private identities, key digests, sessions, and pepper need their own protected backup.

> This MIT-licensed **hindsight-sre** package is not the Vectorize project named Hindsight. There is no managed service or hidden hosted fallback. Redaction is best effort, not a guarantee that every secret will be found. Start with a sanitized remote: existing git objects/history are outside the no-new-raw-store guarantee.

## Try the reviewed queue

Prerequisites: Docker with Compose v2, git, a browser, and an available loopback port 8080. The first image build requires network access for dependencies; the running demo has no remote git or model dependency.

```sh
git clone https://github.com/DrDroidLab/hindsight.git
cd hindsight
docker compose -f compose.yaml -f compose.demo.yaml up --build -d
docker compose exec hindsight hindsight admin bootstrap
```

Open **http://localhost:8080/setup**. Paste the single-use enrollment token from your terminal, choose your real human name/email and team, and save the personal key shown once in a password manager. Sign in with that key. Never put a key in a URL, shell history, or project config.

The demo initializes a local bare remote at `/data/demo-remote.git` from `examples/sample-knowledge/`. It contains seven fictional shared-scope documents: payments-api, a CrashLoopBackOff runbook, an approved clue, an unreviewed clue, a skill, a resolved incident, and a successful illustrative outcome. No production connection exists. Re-running does not overwrite an existing remote.

1. Select **Verify a stale mount before restarting** in Proposed.
2. Inspect the explicitly redacted evidence, edit the learned fact, and save.
3. Approve it. This creates a new trust commit, not a runbook edit.
4. On the approved clue, choose **Promote to runbook**, select CrashLoopBackOff, enter a new stable step ID, preview the actual diff, verify it, then commit the separate promotion.
5. Check Git sync status. Local committed changes remain usable during an upstream outage.

The happy path is a roughly two-minute review once prerequisites and the image are ready, not a promise about downloads, machine provisioning, agent/model response time, or automatic hook delivery. See [the reproducible demo and screenshot recipe](docs/demo.md).

## Connect one agent

Install the Python 3.12+ client in an isolated environment (git must also be installed), then use its interactive key prompt:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install .
hindsight login --server http://localhost:8080 --scope shared
hindsight install claude --server http://localhost:8080 --dry-run
hindsight install claude --server http://localhost:8080
hindsight install claude --check
```

Restart/trust the client as prompted, then ask: **“Recall what we know about payments-api CrashLoopBackOff after credential rotation. Distinguish approved knowledge from unreviewed clues.”** Replace `claude` with `cursor`, `codex`, or `gemini`. Installers preserve unrelated settings and use the distinct `hindsight-sre` MCP alias. Lifecycle capture depends on client version, hook trust, transcript access, and hook time budgets; see [agent capabilities and verification limits](docs/agents.md). Explicit capture always has a CLI fallback:

```sh
hindsight capture --scope shared --summary 'A fictional mount lagged after simulated credential rotation.'
hindsight recall 'CrashLoopBackOff' --scope shared
hindsight review --status proposed
```

Do not put sensitive production content in command arguments. Prefer the capture stdin form for operational content; review it locally first. See `hindsight capture --help`.

## Deploy with your knowledge remote

```sh
cp .env.example .env
# Set GIT_REMOTE to a dedicated sanitized repository. Configure git credentials privately.
docker compose up --build -d
docker compose exec hindsight hindsight admin bootstrap
```

`GIT_REMOTE` is the only required normal-mode knowledge setting. Public clone access does **not** grant push permission. The default port is loopback-only; use an operator-managed HTTPS proxy before remote access and set exact `ALLOWED_HOSTS`/`ALLOWED_ORIGINS` and `PUBLIC_ORIGIN=https://your-host`. Run one application worker and one writer per data volume. Knowledge team scopes are application isolation, not git-history encryption.

Read [operations, backup, offline sync, and conflict recovery](docs/operations.md) and [the security model](docs/security.md) before deployment. Do not expose the demo as a public unauthenticated service. `/health` reports process availability and `/ready` reports initialized repository/index readiness; offline git outages do not inherently invalidate a usable clone.

## Interfaces and limits

- HTTP review API and same-origin bundled TypeScript UI; cookie login plus CSRF for browser writes.
- Official Python MCP SDK: authenticated Streamable HTTP at `/mcp`; local stdio and a credential-reading stdio-to-HTTP proxy.
- MCP tools: `recall`, `get_runbook`, `list_skills`, `load_skill`, `propose_clue`, `report_outcome`, `whoami`.
- Incident lifecycle and explicit runbook outcome reporting, not Slack/PagerDuty ingestion, ticket synchronization, or guessed fix success.
- SQLite FTS5 is rebuildable; private auth is not. Optional bounded in-process semantic scoring is not a separate vector database.
- New steps can be promoted to an existing runbook. The API also supports creating a runbook; the queue currently selects an existing target. Arbitrary runbook editing is not a general-purpose CMS feature.
- Offline agent contract tests are not certification of proprietary client UIs. Live clients and provider credentials are opt-in and unverified unless a documented matrix run says otherwise.

## Develop and verify

```sh
pip install -e '.[dev]'
npm ci --prefix web
npm run build --prefix web
npm test --prefix web
pytest
ruff check src tests scripts
mypy src/hindsight
```

Frontend builds into `src/hindsight/static/`; production needs no Node server or external assets. Do not run multiple writers on one data directory. See [CONTRIBUTING.md](CONTRIBUTING.md), [CLUE.md](CLUE.md), [content schemas](docs/content-model.md), and [API reference](docs/api.md). CI builds the image and runs the offline demo smoke in addition to Python and frontend checks. Test results, not this README, are the authority for a particular revision.
