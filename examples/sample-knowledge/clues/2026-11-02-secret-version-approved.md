---
schema_version: 1
id: 24647f3c-c0c9-5d52-bb47-d503d71023c2
kind: clue
title: Check the secret version after credential rotation
created_at: '2026-11-02T05:00:00+00:00'
updated_at: '2026-11-02T05:00:00+00:00'
created_by: 6063f577-57fd-54a5-b0f8-062b0c09f97d
service_ids:
- bf256ddd-f980-5c0b-acbd-db5b26239c08
alerts:
- CrashLoopBackOff
incident_id: d7afd32e-09a6-5556-a80e-61fe7f58dc88
confidence: 0.8
status: approved
source:
  agent: claude
  session_id: demo-approved-session
  excerpt: Mounted version v41; current version v42. Refreshed mount and confirmed
    healthy pods. [REDACTED:token]
fingerprint: 10745108b3abe305620fd90274be6d92069c88dd136072b4c41cda5ab7fe1e87
capture_id: demo-approved-capture
fact_version: 0c8baeb8-8025-595d-891c-525ab6b89be8
review:
  actor: 6063f577-57fd-54a5-b0f8-062b0c09f97d
  decision: approve
  at: '2026-11-02T05:00:00+00:00'
  approved_at: '2026-11-02T05:00:00+00:00'
  reason: Verified against the synthetic incident evidence.
promotion: {}
---
After database credential rotation, compare the mounted and current secret versions before restarting payments-api. Refresh an out-of-date mount, then verify healthy pods and baseline error rate.
