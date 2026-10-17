# Documentation

The current product architecture is API-first. Direct async `curl_cffi` owns normal exact reads and mutations; the browser remains accessibility/bootstrap/fallback/oracle infrastructure. Canonical conversation graphs and durable SQLite receipts are the source of truth.

## Start here

- [User guide](user-guide.md) — installation, authentication, CLI, Python and MCP examples for continue/new/branch/regenerate/edit-and-branch.
- [Mutation service](browser-mutation-service.md) — exactly-once write boundary, canonical graph reconciliation and browser fallback policy.
- [Read plane](read-plane.md) — exact-graph acquisition and adapter ordering.
- [Session/protocol resilience](session-protocol-resilience.md) — first-class session status/bootstrap/refresh/doctor plus redacted protocol compatibility fingerprints.
- [Interface contract](interfaces.md) — public product/data surfaces.

## Accessibility / browser tooling

- [Accessibility browser](accessibility-browser.md) — CDP/launch modes, login/bootstrap and fallback/oracle role.
- [Accessibility capture](accessibility-capture.md) — redaction-first bounded protocol capture used to discover and verify direct operations.

## Setup / archive workflows

- [Source install](source-install.md) — editable install and restricted-network notes.
- [Chat context sync log](chat-context-sync-log.md) — retained workflow notes.

## Current milestone

The supported mutation surface is now:

```text
new conversation
continue existing thread
continue + attachments
branch from exact node
regenerate from exact user node
edit-and-branch from exact user node
```

All of those converge on `MutationService -> GraphBroker -> canonical SQLite persistence`. Milestone D1 adds explicit session lifecycle diagnosis and protocol-drift classification without weakening exactly-once semantics. The remaining roadmap is attachment/adapter hardening, archive provenance, and eventual removal of obsolete compatibility internals.
