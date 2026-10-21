# ADR 0002 — one registry, domain-level exposure

Status: accepted (2026-10-21)

## Context

Home Assistant exposes 423 entities in the reference deployment. Mapping entities
1:1 to tools produces a tool list that destroys the prompt and the model's
selection accuracy.

## Decision

- One `ToolSpec` shape for local code, MCP servers and HA entities.
- Namespaced names (`ha.light_turn_on`, `fs.read_file`).
- Exposure defaults to **domain-level templates with entity arguments**, plus an
  explicit whitelist. Hard cap: 50 tools, read-only ranked first.
- Dangerous tools require confirmation and are never auto-exposed.

## Consequences

- Adding a device adds capability without adding tools.
- Discovery is automatic (`tools/list` for MCP, `/api/states` for HA); the user
  only ticks what to expose.
- Beyond ~50 tools we switch to two-stage selection (domain, then action) instead
  of raising the cap.
