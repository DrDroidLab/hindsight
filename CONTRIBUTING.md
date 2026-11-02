# Contributing

Use Python 3.12+, git, Node 22+, and an isolated virtual environment. Install `pip install -e '.[dev]'`, run `npm ci --prefix web`, then `npm run build --prefix web`. The bundled assets are generated from `web/`; submit source changes and rebuilt assets together. Keep the MIT license.

Run `pytest`, `ruff check src tests scripts`, `mypy src/hindsight`, `npm test --prefix web`, and an image/demo smoke before proposing a release. Never use real transcripts, customer identifiers, keys, or private repositories in fixtures. Use temporary bare remotes, fake agent homes, synthetic secrets, and mocked provider servers. Live proprietary client checks are opt-in, not ordinary CI assertions.

## Boundaries

HTTP, MCP, and CLI must call the same authorization-aware services. Filter scopes before counts, snippets, scoring, relationship expansion, and diffs. Git is canonical; never add a mutable second knowledge authority. Do not put authentication state in knowledge or claim the private auth database is rebuildable. Preserve verified human git authors and use a distinct service committer.

## Adding an agent hook

Read that client's current official schema and document the exact fixture/version source. Do not copy another client's field names or timeout units. Preserve unrelated settings; test install twice, dry-run, check, collision refusal, uninstall, and private backup behavior. Bound reads to owned nonsymlink files in known transcript roots. Drop tool payloads/environment dumps, redact locally before transmission or spool, use deterministic capture IDs/checkpoints, and honor hook time budgets. Forced exits are not guaranteed delivery. Document explicit-summary fallback and never force an extra agent turn.

## Adding redaction rules

Use synthetic positive, negative, Unicode, repeated-redaction, size-bound, and timeout fixtures. Test metadata, body, errors, reviewer edits, imported changed content, provider outputs, and agent spool. Regex matching must be bounded. Rules run before any durable or provider side effect; never log failing text. Avoid false precision: passing tests is not proof that every secret format is covered.

## UI and documentation

Preserve the review-queue split pane and narrow list-to-detail layout, accessible labels/focus, explicit unreviewed/stale status, real revision conflicts, and distinct approval/promotion commits. Escape metadata/evidence/diffs; raw Markdown HTML and remote images stay disabled. Do not add external fonts/CDNs/analytics. Document actual limitations rather than promising provider or client certification. See `docs/demo.md` for screenshot steps.
