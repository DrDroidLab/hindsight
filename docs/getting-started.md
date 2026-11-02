---
seo_title: "Quickstart: install Hindsight with Docker and connect your coding agent"
description: >-
  Install Hindsight with Docker Compose, run the offline demo, connect Claude
  Code, Cursor, Codex or Gemini CLI over MCP, and deploy against your own git
  remote.
---

# Quickstart

This page gets you from nothing to an agent recalling reviewed knowledge. The [demo walkthrough](demo.md) covers the review flow in more detail.

## Prerequisites

- Docker with Compose v2
- git
- A browser
- A free loopback port. The default is 8080; set `HINDSIGHT_PORT` to use another.
- Python 3.12+ for the client CLI that connects agents

The first image build needs network access for dependencies. The running demo needs no remote git, no model provider and no production access.

## 1. Run the demo server

```sh
git clone https://github.com/DrDroidLab/hindsight.git
cd hindsight
docker compose -f compose.yaml -f compose.demo.yaml up --build -d
docker compose exec hindsight hindsight admin bootstrap
```

If port 8080 is taken, prefix the `up` command with `HINDSIGHT_PORT=8091` (or any free port) and use that port below.

The demo creates a local bare git remote at `/data/demo-remote.git` from `examples/sample-knowledge/`. It holds seven fictional documents: the `payments-api` service, a CrashLoopBackOff runbook, an approved clue, an unreviewed clue, a skill, a resolved incident and an outcome. Running it again does not overwrite an existing remote.

## 2. Enroll and review

1. Open `http://localhost:8080/setup`.
2. Paste the single-use enrollment token printed by `bootstrap`, then enter your name, email and team.
3. Save the personal API key shown once in a password manager, then sign in with it.
4. Open **Verify a stale mount before restarting** in the Proposed tab, read the redacted evidence and approve it.
5. On the approved clue, choose **Promote to runbook**, preview the diff, and commit it.

Approval and promotion are two separate git commits, both authored by you.

!!! warning "Keep keys out of URLs and shell history"
    Never put an API key in a URL, a command argument or project config. The CLI prompts for it interactively.

## 3. Connect an agent

Install the client CLI in a virtual environment, log in, and install the integration for your agent:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install .
hindsight login --server http://localhost:8080 --scope shared
hindsight install claude --server http://localhost:8080 --dry-run
hindsight install claude --server http://localhost:8080
hindsight install claude --check
```

Replace `claude` with `cursor`, `codex` or `gemini`. The installer adds the `hindsight-sre` MCP server, a retrieval skill and a capture hook, and preserves your other settings. Restart the agent and trust the hook if it asks.

Then ask your agent:

> Recall what we know about payments-api CrashLoopBackOff after credential rotation. Distinguish approved knowledge from unreviewed clues.

Hook support differs by client and version; see [Connect your agent](agents.md). You can always capture and recall from the CLI:

```sh
printf '%s' 'A fictional mount lagged after simulated credential rotation.' | hindsight capture --scope shared
hindsight recall 'CrashLoopBackOff' --scope shared
hindsight review --status proposed
```

## 4. Deploy with your own knowledge remote

```sh
cp .env.example .env
# Set GIT_REMOTE to a dedicated, sanitized repository and configure git credentials privately.
docker compose up --build -d
docker compose exec hindsight hindsight admin bootstrap
```

`GIT_REMOTE` is the only required setting. Everything else is optional and unlocks one feature each:

| Setting | Unlocks | Without it |
| --- | --- | --- |
| `GIT_REMOTE` | Storage and versioning | The server will not start |
| Git credentials (SSH key or credential helper) | Push to private remotes | Read-only against a public remote |
| `LLM_ENDPOINT`, `LLM_MODEL`, `LLM_KEY` | Automatic clue extraction with your own key or a local model | Captures arrive as redacted drafts and the reviewer writes the fact |
| `EMBEDDINGS_ENDPOINT`, `EMBEDDINGS_MODEL`, `EMBEDDINGS_KEY` | Semantic ranking on top of full-text search | Plain SQLite FTS5 search |
| `REDACTION_RULES` | Extra patterns, hostname allow-list | Built-in rules for tokens, keys, IPs, emails and customer IDs |
| `REVIEW_MODE=git` | Review captures as git branches with your normal code review | Review in the Hindsight queue |

The server binds to loopback by default. Before exposing it, put it behind an HTTPS proxy and set `PUBLIC_ORIGIN`, `ALLOWED_HOSTS` and `ALLOWED_ORIGINS`. Read [Operations](operations.md) and the [Security model](security.md) first.

## Next steps

- [How it works](how-it-works.md): capture, redaction, review and recall in detail
- [Content model](content-model.md): the Markdown schemas for services, runbooks and clues
- [HTTP and MCP API](api.md): the seven MCP tools and the review API
