# Clue format, schema version 1

A clue is a durable, redacted operational fact with provenance and an explicit trust state. It is not an instruction from a higher-priority authority. Proposed clues are already committed to git; approval records human trust in a later commit.

```yaml
---
schema_version: 1
id: 768c47c5-b015-4077-8bc5-4c7f27653525
kind: clue
title: Compare mounted and current secret versions after rotation
created_at: '2026-11-02T05:00:00+00:00'
updated_at: '2026-11-02T05:00:00+00:00'
created_by: 228231fa-eed7-4c4a-9a65-97d068426e2c
service_ids: []
alerts: [CrashLoopBackOff]
confidence: 0.6
status: proposed
capture_id: synthetic-session-turn-1
source:
  agent: manual
  session_id: synthetic-session
  excerpt: 'Mounted v41; current v42. Credential omitted: [REDACTED:token].'
fingerprint: server-computed-normalized-redacted-fact-digest
review: {}
promotion: {}
---
## Learned fact
Compare credential versions, not secret values, before choosing a recovery action.

## Evidence
The synthetic deployment mounted an older version after rotation.

## What fired
CrashLoopBackOff immediately after the simulated rotation.

## What fixed it
Refreshing the mount restored healthy pods in this simulation. Verify before reuse.
```

The server computes fingerprints from scope, service IDs, alerts, and normalized redacted body. They are not hashes of raw transcripts. `capture_id` makes capture retries idempotent; a rejected exact fingerprint suppresses the identical fact. Distinct facts must not be merged merely because an alert repeats.

`status` is `proposed`, `approved`, `rejected`, or `stale`. `review` holds actor, decision, timestamp, reason, approval time, and fact-version information. `promotion` links a separately accepted runbook change, stable step ID, operation ID, and time. `fact_version` changes when the fact changes; confirmations for the old fact must not silently renew it.

`source` contains only a bounded redacted excerpt, opaque agent/session identifiers, and optionally a validated safe link. Never store local transcript paths, raw transcripts, environment dumps, credentials, or API keys here. Confidence is a number from 0 to 1, not approval or a probability guarantee.

Storage path: `clues/<date>-<slug>-<short-id>.md` or `teams/<team>/clues/<name>.md`. Server-created filenames may use UUIDs. Scope comes from the validated path, not frontmatter. The stable UUID is the identity even if a file is renamed. See [all content types](docs/content-model.md) and the seven [sample records](examples/sample-knowledge/).
