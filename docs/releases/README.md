# Release notes

Curated, publish-ready notes for each GitHub Release live in `vX.Y.Z.md`.
They follow the shape used by mature self-hosted projects (Immich, Frigate, Ollama):
a one-paragraph summary, a handful of highlights, upgrade notes, image tags/digests,
and a verification section.

`vX.Y.Z.generated.md` is a **machine draft** produced from Conventional Commits. It is
useful input, never a finished release note — always curate it.

## Workflow

1. Bump `version` in `pyproject.toml` and `__version__` in `src/rui/__init__.py`.
2. `make changelog` — review unreleased Conventional Commits since the last tag.
3. `make release` — writes `docs/releases/vX.Y.Z.generated.md` (no tag, push,
   publish or deploy).
4. Curate it into `docs/releases/vX.Y.Z.md` using the current
   [`v0.4.0.md`](v0.4.0.md) as the model.
5. Add the same facts to [`CHANGELOG.md`](../../CHANGELOG.md) under the new version.
6. After CI and the image manifest are green, create the GitHub Release by hand from
   `vX.Y.Z.md` and fill the digest placeholders.

```bash
make changelog                     # unreleased notes -> stdout
make release                       # machine draft -> docs/releases/v0.4.0.generated.md
make release VERSION=0.5.0 TAG=v0.5.0
```

The generator is `scripts/release-notes.sh` (git only, no new dependencies). It maps
`feat` to Added, `fix`/`revert` to Fixed, `perf`/`refactor` (and, with
`--all`, `build`/`ci`/`test`/`docs`/`chore`) to Changed, `security` to
Security, and `!` + `BREAKING CHANGE:` to Upgrade notes.
