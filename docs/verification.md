---
description: >-
  What has and has not been verified in Hindsight: automated test results, Docker smoke tests, and the live agent and provider checks still outstanding.
---

# Verification record

Date: 2 November 2026, IST (UTC+05:30).

## Verified in the implementation session

- `pytest -q`: **85 passed**. This includes 26 core tests, 45 agent/provider tests, and 14 application integration tests.
- `ruff check src tests scripts`: passed.
- `mypy src/hindsight`: passed for 17 source files.
- `npm run build --prefix web`: TypeScript check and bundled frontend build passed.
- `npm test --prefix web`: **4 passed**. JSDOM tests cover setup, key display, sign-in, revision conflicts, approval separate from promotion, read-only policy, and escaping. These are not real-browser tests.
- `python -m build`: source distribution and wheel passed. The wheel includes static assets and synthetic sample knowledge.
- `docker compose -f compose.yaml -f compose.demo.yaml config --quiet`: passed.
- Production Docker image build: passed. Runtime user is `10001:10001`.
- `python scripts/demo-smoke.py --image hindsight-sre:session`: passed inside the final image with network disabled. It covers packaged assets, seeded git, enrollment, session/CSRF, review/conflicts, promotion replay, capture, recall, and logout.
- Separate real container test: the actual `hindsight demo --serve` entrypoint starts with network disabled. Private-volume permissions work. An issued identity/key remains valid after container restart.
- Real official MCP SDK clients exercise Streamable HTTP, direct stdio, and the stdio-to-HTTP proxy. Tests cover scoped identity isolation, malformed argument/envelope sanitization, unsupported prompt/resource requests, and revocation during a live stdio process.
- Git-review mode captures into the proposal branch, exposes unreviewed clues in the queue/recall, deduplicates captures, syncs, and blocks local review mutations.
- Local and public preview HTTP checks returned 200 for the UI shell, JavaScript, CSS, and setup status.

Review fixes have regression coverage for candidate-extraction recursion, atomic extraction batches, stale team clues, context fingerprints, aggregate tree limits, and queued writers after a sync conflict.

## Failed attempts and corrections

An early image smoke test failed because bundled HTML referenced an obsolete asset route. The frontend now builds `/assets/` paths. Both final image smoke and live preview asset checks passed.

Initial lint/type/test runs found development defects. The final complete checks above passed. Pytest reports one upstream Starlette warning about its HTTPX TestClient integration; no application test fails.

## Unverified or blocked

- **Real browser and visual checks:** shared Chrome CDP refused connections at the documented endpoint. No replacement browser/profile was launched. Responsive layouts, native dialog behavior, screenshots, and the narrated demo recording therefore remain unverified. Mockup structural checks and JSDOM tests do not replace those checks.
- **Real agent lifecycles:** Claude Code, Cursor, Codex, and Gemini executables are not installed. Offline schema/config/hook tests pass, but genuine client trust approval, tool discovery, lifecycle capture, and minimum certified versions remain unverified.
- **Live provider endpoints:** extraction/embedding adapters pass local mock tests. No live external or local model service was configured or certified.
- **Remote provider branch policy:** local bare-remote tests cover git mechanics. Operators must verify actual SSH/HTTPS push rights and external reviewer/branch-protection policy before production use.

These limits prevent a production-certification claim. Do not treat the passing automated suite as proof that every agent version, provider, or deployment works.
