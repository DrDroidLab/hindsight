---
seo_title: "Hindsight vs OpenSRE, Krimto and other agent memory tools"
description: >-
  How Hindsight compares with OpenSRE, Krimto, Glen, Bhived and Vectorize
  Hindsight: an SRE-typed, git-backed memory with a review gate, rather than
  an AI SRE agent or a general-purpose memory store.
---

# How Hindsight compares

Hindsight is the memory an engineer's existing agent pulls from, not the agent itself. Projects called OpenSRE *are* the agent. General team-memory tools store notes; Hindsight stores runbooks, services and incident learnings, and knows what an alert is.

> If you want an agent that gets paged, use OpenSRE. If you want your existing Claude Code or Cursor sessions to share what the team knows, use Hindsight. They are complementary: an OpenSRE agent could be a client of a Hindsight server.

| Project | What it is | Where it overlaps | Where Hindsight differs |
| --- | --- | --- | --- |
| [OpenSRE (Tracer Cloud)](https://github.com/Tracer-Cloud/opensre) | Apache-2.0 framework for building AI SRE agents that investigate incidents on your infrastructure | Redacts sensitive data in LLM calls; can monitor local Claude Code, Cursor and Codex sessions | Not an agent and has no runtime or telemetry; SRE-typed memory with a review gate instead of an investigation loop |
| [OpenSRE (opensre.in)](https://github.com/swapnildahiphale/OpenSRE) | Apache-2.0 self-hosted AI SRE agent with episodic memory, a Neo4j knowledge graph and chat bots | "Learns from every investigation" is the same instinct as clues | Memory lives in git as reviewable Markdown, not in a graph database owned by one agent; works with any coding agent |
| [Krimto](https://glama.ai/mcp/servers/rrnd4nuegn) | Apache-2.0 team memory for coding agents: Markdown in git, scoped, cross-vendor MCP | Closest architecture: git-backed, MCP, cross-vendor | Typed services, runbooks and clues instead of generic notes, plus redaction and an SRE-shaped review queue |
| [Glen](https://composio.dev/toolkits/glen_mcp/framework/atomic-agent.md), [Bhived](https://github.com/artkeyai/bhived-mcp) | Hosted organisation memory and skills over MCP | Shared memory, skills, session import | Self-hosted only; knows what an alert, a runbook and a service are |
| [Hindsight (Vectorize)](https://github.com/vectorize-io/hindsight) | MIT agent memory server with retain, recall and reflect, and installers for many agent harnesses | Same name; similar hook, MCP and skill install pattern | General-purpose memory for any agent; no SRE object model, review gate or git-as-store |

## About the name

This project's package is **`hindsight-sre`** and its MCP server is registered as **`hindsight-sre`** so it does not collide with the Vectorize Hindsight project in agent configs. The two projects are unrelated.

## When Hindsight is the wrong tool

- You want an autonomous agent that responds to pages. Use an AI SRE agent; Hindsight can sit underneath it.
- You need a hosted service. Hindsight is self-hosted only.
- You need per-document RBAC or encryption between teams. Team scopes are application-level filtering; anyone with access to the git remote can read every scope.
