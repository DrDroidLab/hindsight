---
description: >-
  Run Hindsight's offline Docker demo with a seeded sample repo, walk through review, approval and runbook promotion, and capture reproducible screenshots.
---

# Reproducible offline demo and screenshot recipe

## Prerequisites and safety

Docker + Compose v2, git, a browser, free loopback port 8080. Initial image downloads/build need the network. Runtime demo knowledge stays in a local bare remote and requires no model provider or production credentials. Use a fresh isolated compose project to avoid screenshots of real team knowledge:

```sh
docker compose -p hindsight-demo -f compose.yaml -f compose.demo.yaml up --build -d
docker compose -p hindsight-demo exec hindsight hindsight admin bootstrap
```

Open http://localhost:8080/setup, enroll with your real local identity and a fictional team, save the one-time key privately, then sign in. Never capture screenshots/recordings of the setup token or API key. The sample documents all have stable UUIDs and are shared-scope; they depict no real customer, incident, or infrastructure.

## Happy path

1. Queue screenshot at 1440×900: Proposed tab, synthetic mount clue selected, redacted excerpt visible, trust warning and separate approval action.
2. Open Edit clue; write a verifiable fact from the excerpt. Save; show local commit/sync feedback.
3. Approve using the confirmation dialog. Verify it moved into Approved; approval alone left the runbook unchanged.
4. Open promotion; target CrashLoopBackOff, new step ID `check-mounted-version`, heading `Check mounted credential version`, content describing version comparison without values.
5. Preview the exact diff; verify evidence checkbox, then commit. Show the separate promotion result and linked clue, not invented production execution.
6. Capture/review another synthetic clue; reject with a reason; replay the same capture ID to inspect deduplication.
7. At 390×844, select the clue from the queue, inspect stacked detail and persistent footer actions; keyboard-tab every control. Use browser locale/time zone intentionally for reproducible timestamp display.
8. Record approved/rejected/stale views only when those states actually exist. To demonstrate stale outcomes deterministically, use a disposable clock-controlled test fixture, not production timestamp edits disguised as real history.

The target is roughly two minutes for the review happy path **after** machine prerequisites/build/enrollment; not a guarantee of model or network latency. Manual editing works with provider settings empty.

## Automation and evidence

CI runs `scripts/demo-smoke.py` against the built container with networking disabled, using real ASGI HTTP requests (no listening server), including enrollment, capture without a model, revision conflict, approval, preview and promotion, and FTS recall. Frontend safety tests are `npm test --prefix web`; build with `npm run build --prefix web`. Browser screenshots are a separate rendered acceptance step, not implied by API tests.

The design source is the nine attached review-queue screens: queue/detail, manual edit, promotion diff, auth, empty, stale/outcomes, approved, rejected, and phone detail. Preserve that information hierarchy rather than adding a dashboard.

## Local reset

```sh
docker compose -p hindsight-demo -f compose.yaml -f compose.demo.yaml down
# Only for this disposable demo; destroys private auth and all local knowledge too:
docker compose -p hindsight-demo -f compose.yaml -f compose.demo.yaml down -v
```

## Launch copy (draft only; not posted)

> We built Hindsight: a self-hosted review queue for operational learnings underneath your engineer's coding agent. Redacted clues become typed, git-backed service/runbook memory only after explicit human review. Approval is separate from runbook promotion; unreviewed and stale evidence stay labeled. Manual capture/retrieval need no model, and there is no telemetry. MIT, Python/Docker, compatible agent MCP integrations. Not the Vectorize project of the same name; not an AI SRE runtime. We'd value feedback on review ergonomics and safe capture boundaries.

For an SRE community, lead with one synthetic CrashLoopBackOff clue and the actual accepted diff. For an agent-tooling community, show scoped MCP recall and honest per-client hook limitations. For self-hosting communities, lead with the local bare-remote demo and separate private-auth backup. Do not claim live client certification, perfect redaction, or guaranteed hook delivery.
