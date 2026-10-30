# Release notes

Curated, publish-ready notes for each GitHub Release live in `vX.Y.Z.md`.
They follow the shape used by mature self-hosted projects (Immich, Frigate, Ollama):
a one-paragraph summary, a handful of highlights, upgrade notes, image tags/digests,
and a verification section.

`vX.Y.Z.generated.md` is a **machine draft** produced from Conventional Commits. It is
useful input, never a finished release note — always curate it.

## Release checklist

Nine steps, in this order: **bump** (1–3), **write** (4–6), **tag** (7–8), **verify the
published image** (9). Run from the repo root, on `main`, with a clean tree, after every
commit that belongs in the release is already on `main`.

```bash
VERSION=X.Y.Z                 # e.g. 0.8.12
TAG="v$VERSION"               # the tag; the release-note file is "docs/releases/$TAG.md"
```

### 1. `__version__` in `src/rui/__init__.py`

```bash
sed -i.bak -E 's/^__version__ = ".*"/__version__ = "X.Y.Z"/' src/rui/__init__.py \
  && rm src/rui/__init__.py.bak
grep -n '^__version__' src/rui/__init__.py        # __version__ = "X.Y.Z"
```

### 2. `[project].version` in `pyproject.toml`

The `version = "…"` line directly under `[project]` (the first `version =` in the file).

```bash
sed -i.bak -E 's/^version = ".*"/version = "X.Y.Z"/' pyproject.toml \
  && rm pyproject.toml.bak
grep -n -m1 '^version = ' pyproject.toml          # version = "X.Y.Z"
```

Same shape as step 1, and for the same reason: 0.8.12 shipped with `__version__` bumped
and `[project].version` still one release behind — the grep alone only ever verifies, it
never writes. The pattern is anchored at column 0, and `[project].version` is the only
line in the file that starts there (dependency entries are indented), so this cannot
touch a pinned dependency.

`tests/test_version.py` compares this value with `rui.__version__`, so steps 1 and 2 are
the only pair the test suite actually guards.

### 3. `version` in `uv.lock` — **no test guards this step**

`uv.lock` carries the project's own version inside the `[[package]] name = "rui"` block
(`source = { editable = "." }`). Bumping steps 1–2 and stopping
here leaves the lock one version behind and **nothing fails**: the version test only
looks at `__init__.py` and `pyproject.toml`, `uv sync` on CI happily installs the
editable package, and the drift only surfaces later as a lockfile that no longer matches
the release. Regenerate the lock — don't hand-edit it:

```bash
uv lock                                # prints "Updated rui v<old> -> v<new>"
uv lock --check                        # non-zero exit if the lock is stale
grep -n -A1 '^name = "rui"' uv.lock    # name = "rui" / version = "X.Y.Z"
```

`uv lock` after a version bump touches exactly that one line. If it rewrites anything
else, stop: unrelated resolution changes do not belong in a release commit.

### 4. Write `docs/releases/vX.Y.Z.md`

The filename is the tag **verbatim**, `v` and all: `docs/releases/$TAG.md` — `v0.8.12` →
`docs/releases/v0.8.12.md`. `TAG` already carries the `v` (see the two lines at the top of
this checklist), and every one of the 16 notes in this directory is `vX.Y.Z.md`; there is
no `0.8.12.md`.

`make release` seeds `vX.Y.Z.generated.md` from Conventional Commits (`VERSION` defaults to
the `pyproject.toml` value bumped in step 2; override with `VERSION=… TAG=…`); curate it
into `vX.Y.Z.md` using [`v0.4.0.md`](v0.4.0.md) as the model.

The verification section must quote **both** test counts, labelled, per
[Test counts](#test-counts-two-environments-one-wording) below, and must spell the
`:sha-…` tag the way the registry does (7 characters).

### 5. Add `## [X.Y.Z]` to `CHANGELOG.md`

Directly under `## [Unreleased]`, Keep-a-Changelog headings (`### Added`, `### Fixed`, …),
and `[Unreleased]` reset to `_Nothing yet._`.

```bash
grep -n -m3 '^## \[' CHANGELOG.md     # [Unreleased] first, then [X.Y.Z]
```

### 6. Update `docs/HANDOFF.md`: the H1 and a new section

The H1 names the shipped version (`# HANDOFF — rui X.Y.Z (…)`), so it moves with every
release: a HANDOFF whose H1 is a version behind sends the next session to the wrong
rollback anchor. Add the release as a new dated section at the end (image digest,
rollback anchor, what the deploy actually verified) and leave older sections untouched.

```bash
head -1 docs/HANDOFF.md               # # HANDOFF — rui X.Y.Z (…)
```

### 7. Tag with `git tag -a` — annotated, never lightweight

```bash
git tag -a "$TAG" -m "rui $VERSION"
git cat-file -t "$TAG"                # must print: tag    (a lightweight tag prints: commit)
```

A lightweight tag is just a name for a commit: no tagger, no date, no message, and
nothing to inspect when a release has to be explained a month later. 0.8.9–0.8.11 were
annotated and 0.8.12 was not — that drift is what this check catches.

### 8. The tag must sit on `main`'s tip, and nothing may land on `main` after it

```bash
git fetch origin
git rev-parse main origin/main        # must print the same sha, and be the commit released
git rev-parse "$TAG^{commit}"         # same sha again; tree clean before tagging
git push origin main
git push origin "$TAG"
```

`:latest` is published by the **tag** job only; later commits on `main` publish `:edge`
and `:sha-<7>`. That separation is deliberate: a docs-only commit landing after the tag
once republished `:latest` from a second build, so `install.sh`'s `:latest` and the
`:X.Y.Z` named in the release note pointed at different images. Keep the tag last — if
wording has to change afterwards, it goes in a follow-up commit (`:edge`, which nobody
installs), not in a retag and not in a re-run of the release job.

### 9. Verify the published image, then write the digest back

```bash
# index digest + per-arch digests
docker buildx imagetools inspect ghcr.io/yes999zc/rui:X.Y.Z
docker buildx imagetools inspect ghcr.io/yes999zc/rui:X.Y.Z | sed -n '3p'   # Digest: sha256:<index>

# label revision must be the released commit, label version must be vX.Y.Z
docker pull -q ghcr.io/yes999zc/rui:X.Y.Z
docker image inspect --format '{{json .Config.Labels}}' ghcr.io/yes999zc/rui:X.Y.Z \
  | jq -r '."org.opencontainers.image.revision", ."org.opencontainers.image.version"'
```

Then:

- fill the digest placeholders in `docs/releases/vX.Y.Z.md` and publish the GitHub Release from it;
- record the index digest and the previous image's digest (rollback anchor) in the new
  `docs/HANDOFF.md` section;
- write `:sha-<7>` with **7** characters (`:sha-22623a1`), which is what the registry publishes.

A green workflow is not the end of the checklist: `:X.Y.Z`, `:X.Y`, `:latest` and
`:sha-<7>` must all report the same digest, and `:latest` is the one that drifts.

## Test counts: two environments, one wording

The suite runs in two places and they do not produce the same numbers. Quote both, each
labelled, in the release note's verification section.

| environment | command | 0.8.12 | why the difference |
|---|---|---|---|
| local `.venv` (macOS, libspeexdsp + ffmpeg present) | `uv run pytest` | **2311 passed / 6 skipped** | the 9 audio cases CI skips actually run here |
| CI (`uv sync --extra dev`, ubuntu) | `gh run view <run-id> -R yes999zc/rui --log \| grep passed` | **2302 passed / 15 skipped** | no libspeexdsp → all 6 in `tests/test_aec.py` plus 2 guarded cases in `tests/test_audio_scale.py`; no ffmpeg → 1 in `tests/test_music_jellyfin.py`. That is the 9-test gap. |

Both rows total 2317 collected, which is how you check a count rather than trusting it. The
6 skips on both sides are the same six optional-model cases (five in `tests/test_ack.py`,
one in `tests/test_turn.py`) that want `RUI_TEST_MODELS`; CI's extra 9 are the audio ones.

- The gate for pushing and tagging is the **CI** number: that is what the tag job runs.
- The **local** number is the only one that exercises the audio paths CI skips; it does
  not replace the CI number and must never be quoted alone.
- `pyproject.toml` already sets `addopts = "-q"`, so `uv run pytest -q` runs as `-qq` and
  prints no tally at all. Run `uv run pytest` for the counts, and pass
  `-o addopts=""` when you need your own flags (`uv run pytest -o addopts="" -rs`).
- A count that matches neither (say `2301 passed / 1 failed`) is the version-mismatch
  signal from steps 1–3, not a flaky test.

## Generator

The generator is `scripts/release-notes.sh` (git only, no new dependencies). It maps
`feat` to Added, `fix`/`revert` to Fixed, `perf`/`refactor` (and, with
`--all`, `build`/`ci`/`test`/`docs`/`chore`) to Changed, `security` to
Security, and `!` + `BREAKING CHANGE:` to Upgrade notes.

```bash
make changelog                                      # unreleased notes -> stdout
make release VERSION=0.8.13 TAG=v0.8.13             # machine draft -> docs/releases/v0.8.13.generated.md
```

Neither target tags, pushes, publishes or deploys.
