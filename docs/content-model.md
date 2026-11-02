---
description: >-
  Schemas for Hindsight's git-stored knowledge: services, runbooks with stable steps, clues, skills, incidents and outcomes, plus team scopes and trust states.
---

# Content model v1

Git Markdown is canonical. A validated regular UTF-8 file has bounded safe YAML frontmatter and a human-editable Markdown body. No YAML aliases/custom tags, duplicate IDs, symlinks, submodules, arbitrary binary attachments, traversal paths, or hidden control paths. Body HTML is untrusted and is not executed by the UI. Skills are loaded as text, never executed.

## Common fields and identity

`schema_version: 1`, `id` (stable UUID), `kind`, `title`, `created_at`, `updated_at` (UTC ISO strings), and `created_by` (nonsecret immutable user ID). Store timestamps in UTC; the browser displays the viewer's locale/time zone. Authoritative human name/email remain in private identity storage and deliberate git author metadata. Imported ecosystem skills may derive missing identity from their validated directory without rewriting the original SKILL.md.

The parser allows extra bounded metadata for human-maintained content. That flexibility does not make arbitrary field names executable or authoritative. Authoritative server mutations validate references and scope. IDs, not names or paths, are used in APIs. Renaming a file does not change its ID. Duplicate IDs are invalid across the active tree.

## Paths and scope

| Kind | Shared path | Additional fields |
| --- | --- | --- |
| service | `services/<slug>.md` | `owner`, `repository_url`, `dashboards`, `alerts`, `dependencies`, `runbook_ids` |
| runbook | `runbooks/<slug>.md` | `service_ids`, `alerts`, stable `steps` |
| clue | `clues/<name>.md` | `service_ids`, `alerts`, optional `incident_id`, numeric `confidence`, `status`, `source`, `fingerprint`, `capture_id`, `review`, `promotion` |
| skill | `skills/<slug>/SKILL.md` | standard ecosystem `name`, `description`; optional service references |
| incident | `incidents/<id>.md` | `service_ids`, `state: open|resolved`, `started_at`, `resolved_at`, `links` |
| outcome | `outcomes/<id>.md` | `idempotency_key`, `runbook_id`, cited `revision`, `step_id`, `result: success|failure|partial`, `clue_ids`, optional `incident_id`, `actor`, `timestamp` |

Prefix any allowed path with `teams/<team>/` for team scope. Shared-root knowledge is readable by every authenticated member; editing it requires shared write scope. A client-supplied `scope` field cannot override the path. Cross-team references must not expose unreadable names/snippets/counts. Access to the git remote reveals all committed scope content and history; deploy separate instances/remotes for stronger isolation.

## Runbook stable steps

```yaml
steps:
  inspect-events: Inspect events and logs
  verify-recovery: Verify recovery
```

The body has matching headings such as `## Inspect events and logs`. Server-generated representations may use a list of `{id, heading}` entries; IDs stay stable when display order changes. Outcome references cite a known runbook revision and stable step ID, not “step 2.” Current promotion adds a new stable step; arbitrary in-place step editing remains a normal git operation subject to schema validation. A runbook and clue promotion link change together in one separate promotion commit.

## Trust, outcomes, and staleness

Proposed clues remain labeled unreviewed in recall. Rejected clues stay in git for audit and exact duplicate suppression but are excluded from normal recall. Approved clues are reviewed evidence, not permission to execute a command. Stale clues remain visible with a warning.

A success linked explicitly to a matching clue fact version may renew confirmation time. Failure/partial are evidence but not confirmations; using a runbook alone does not confirm every related clue. Changing the fact invalidates old confirmations for the new fact version. Revalidation is explicit and does not fabricate an outcome or approve a proposal automatically.

Default staleness is 90 days from matching successful confirmation or approval if never confirmed. Daily marking persists a trust-state transition in git. Incident links are references only: Hindsight does not ingest Slack or ticket timelines. The sample's success is clearly fictional, not live evidence.

See the [CLUE.md spec](clue-spec.md) for an annotated clue and [`examples/sample-knowledge/`](https://github.com/DrDroidLab/hindsight/tree/main/examples/sample-knowledge) for deterministic UUID examples of all six kinds.
