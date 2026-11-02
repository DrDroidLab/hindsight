---
seo_title: "Hindsight FAQ: self-hosted operational memory for coding agents"
description: >-
  Frequently asked questions about Hindsight: supported coding agents, LLM
  requirements, redaction, git hosting, telemetry, licensing and how it
  differs from AI SRE agents.
faq:
  - q: What is Hindsight?
    a: >-
      Hindsight is an open-source, self-hosted operational memory for coding
      agents. It stores services, runbooks and reviewed incident learnings
      (clues) as Markdown in git, and serves them to Claude Code, Cursor,
      Codex, Gemini CLI or any MCP client.
  - q: Is Hindsight an AI SRE agent?
    a: >-
      No. Hindsight never gets paged, runs commands or investigates incidents
      on its own. It is the knowledge layer underneath the agent each engineer
      already uses, and an AI SRE agent could be one of its clients.
  - q: Which coding agents does Hindsight support?
    a: >-
      The installer configures Claude Code, Cursor, Codex and Gemini CLI, and
      any other MCP client can connect over Streamable HTTP or through the
      stdio proxy. Automatic capture through lifecycle hooks depends on each
      client's version and hook support; explicit capture through the CLI or
      the propose_clue MCP tool always works.
  - q: Do I need an LLM API key?
    a: >-
      No. Capture, review and full-text recall all work without a model. If
      you configure your own LLM endpoint or a local model, Hindsight uses it
      to extract candidate clues from session summaries. There is no hosted
      fallback.
  - q: How does Hindsight keep secrets out of the knowledge base?
    a: >-
      Captured text is redacted in the agent hook and again on the server
      before it is written to git, indexed or sent to a model. Tokens, keys,
      IP addresses, emails and customer IDs become REDACTED markers, and no
      raw transcript is stored. Redaction is best effort, so reviewers should
      read the evidence before approving.
  - q: Where is the data stored?
    a: >-
      In a git repository you control: GitHub, GitLab, Gitea or a bare repo.
      The server keeps a local clone, commits on each write with the real
      engineer as author, and pushes on a timer. The search index is SQLite
      and can be rebuilt from git at any time.
  - q: Does Hindsight collect telemetry?
    a: >-
      No. Hindsight sends no usage data anywhere, and the web UI loads no
      external fonts, scripts or analytics.
  - q: Can we review clues as pull requests instead of in the UI?
    a: >-
      Yes. With REVIEW_MODE=git, captured clues are committed to a proposal
      branch and you review and merge them with your normal git workflow and
      branch protection.
  - q: How is it licensed?
    a: Hindsight is MIT licensed.
  - q: Is this the same project as Vectorize Hindsight?
    a: >-
      No. This project is published as hindsight-sre and registers its MCP
      server as hindsight-sre to avoid colliding with the unrelated Vectorize
      project of the same name.
---

# Frequently asked questions

Short answers to the questions platform and SRE leads ask first. For deeper detail, see [How it works](how-it-works.md) and the [Security model](security.md).
