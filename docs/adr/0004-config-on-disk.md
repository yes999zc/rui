# ADR 0004 — config is a file, not application state

Status: accepted (2026-10-21)

## Context

A console that writes settings into memory drifts from the file, and a crash
loses changes. Full-file rewrites also risk resurrecting stale values.

## Decision

- `config.yaml` is the only source of truth; the API reads it, never caches a
  mutable copy.
- Writes are atomic (`tmp` + `os.replace`), preceded by a timestamped backup,
  and validated by the Pydantic schema first.
- Secrets never enter the config: only the *name* of an environment variable.
- The API is read-only in M0; write endpoints land in M2 under the same rules.

## Consequences

- `docker compose down && up` cannot lose settings.
- A bad edit is rejected before it reaches disk; the backup is the rollback.
- No secret can leak through the console, because the payload only carries
  `has_key: true/false`.
