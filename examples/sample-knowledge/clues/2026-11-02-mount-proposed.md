---
schema_version: 1
id: abaa8619-1eb5-5d4e-9d98-5a693e310662
kind: clue
title: Verify a stale mount before restarting
created_at: '2026-11-02T05:00:00+00:00'
updated_at: '2026-11-02T05:00:00+00:00'
created_by: 6063f577-57fd-54a5-b0f8-062b0c09f97d
service_ids:
- bf256ddd-f980-5c0b-acbd-db5b26239c08
alerts:
- CrashLoopBackOff
incident_id: d7afd32e-09a6-5556-a80e-61fe7f58dc88
confidence: 0.6
status: proposed
source:
  agent: codex
  session_id: demo-proposed-session
  excerpt: 'CrashLoopBackOff followed a simulated rotation. Mounted version v41, current
    version v42. Credential omitted: [REDACTED:token].'
fingerprint: 0144b51de3c50e12841961cb57cbb3b43719e64a4201c86fd3c5fd37b5564e48
capture_id: demo-proposed-capture
extraction_state: manual
review: {}
promotion: {}
---
## Learned fact

Inspect mounted secret freshness when CrashLoopBackOff follows rotation. This draft needs human verification; repetition alone is not proof.

## Evidence

A fictional agent observed a stale mount after a simulated rotation.
