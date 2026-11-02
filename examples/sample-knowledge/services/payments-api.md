---
schema_version: 1
id: bf256ddd-f980-5c0b-acbd-db5b26239c08
kind: service
title: payments-api
created_at: '2026-11-02T05:00:00+00:00'
updated_at: '2026-11-02T05:00:00+00:00'
created_by: 6063f577-57fd-54a5-b0f8-062b0c09f97d
owner: platform
repository_url: https://git.example.invalid/demo/payments-api
dashboards:
- https://metrics.example.invalid/payments
alerts:
- CrashLoopBackOff
dependencies: []
runbook_ids:
- 68c8d707-3646-521b-91b3-1301becff3a8
---
A fictional checkout payment service used only for the offline demo.

Known quirk: a mounted database credential may lag after rotation.
