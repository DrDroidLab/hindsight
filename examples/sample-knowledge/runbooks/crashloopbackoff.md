---
schema_version: 1
id: 68c8d707-3646-521b-91b3-1301becff3a8
kind: runbook
title: CrashLoopBackOff
created_at: '2026-11-02T05:00:00+00:00'
updated_at: '2026-11-02T05:00:00+00:00'
created_by: 6063f577-57fd-54a5-b0f8-062b0c09f97d
service_ids:
- bf256ddd-f980-5c0b-acbd-db5b26239c08
alerts:
- CrashLoopBackOff
steps:
  inspect-events: Inspect events and logs
  verify-recovery: Verify recovery
---
# CrashLoopBackOff

## Inspect events and logs

Inspect pod events and previous container logs. Never paste credentials.

## Verify recovery

Confirm healthy pods and baseline error rate. Escalate if the failure persists.
