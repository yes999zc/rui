# Security scanning — dependencies and container image

This page covers the automated CVE checks added in P3.5: the Python dependency
audit and the container-image scan. Container identity, volume permissions and
the non-root runtime are covered separately in [security.md](security.md); this
page is only the dependency/image scanning slice.

## 1. What runs, and when

| Check | Tool | Trigger | Fails the job when |
|---|---|---|---|
| Python dependencies | `pip-audit` 2.10.1 over `uv.lock` (all extras) | every push, every PR, weekly schedule, manual dispatch | any reported vulnerability not covered by a valid dated ignore; invalid or expired ignore entry |
| Container image | Trivy v0.76.1 over the `linux/amd64` runtime image | push to `main`, `v*` tags, weekly schedule, manual dispatch | unignored `HIGH`/`CRITICAL`; scanner/setup error |
| SARIF | `github/codeql-action/upload-sarif` | image scan runs | never (upload is best-effort; the SARIF artifact is the fallback) |

The image build is deliberately skipped on pull requests and non-`main` branch
pushes: the cold build compiles FFmpeg, builds the web UI and downloads the seed
models. The dependency audit is cheap and runs everywhere. The weekly schedule is
Monday 03:17 UTC; a canceled/superseded run is handled by the workflow
`concurrency` group. CI pins uv 0.13.0, pip-audit 2.10.1 and Trivy v0.76.1; bump
them deliberately and update this page when the policy changes.

Artifacts are retained for 30 days:

- `pip-audit-report` — `pip-audit.json` plus a one-line-per-finding
  `pip-audit.txt`;
- `trivy-image-report` — `trivy-results.sarif`.

The job summary always shows the pip-audit result, the unignored SARIF count and
the dated Trivy allowlist, so a passing image scan is not a silent pass.

## 2. Severity policy

- **Trivy**: `--severity HIGH,CRITICAL --exit-code 1`, with
  `ignore-unfixed: false`. Unfixed CVEs are *not* hidden globally; they are
  either fixed or listed one-by-one with an expiry date. Only `HIGH` and
  `CRITICAL` are requested, and `limit-severities-for-sarif` keeps the SARIF at
  the same threshold.
- **pip-audit**: the JSON output does not include a CVSS score, so the gate fails
  on **every** known vulnerability, not just `HIGH`/`CRITICAL`. This is stricter
  than the requested threshold and avoids a high-severity finding slipping
  through because no severity metadata exists. Triage the reported ID against
  OSV/PyPI, then fix it or add a dated exception.
- **No auto-fix in CI**: fixes land as a reviewed lockfile change
  (`uv lock --upgrade-package …`), not as a mutation of the runner environment.
- **No blanket suppressions**: `ignore-unfixed: true`, wildcard IDs and
  untriaged `.trivyignore` entries are not accepted. Every exception has a date
  and a reason.

## 3. Exceptions (dated allowlists)

Allowlist edits are security-sensitive: require maintainer review and a linked
advisory/issue, and never add a broad entry just to make a run green. Both files
are reported in the job summary on every run; the image job and the pip-audit
script also emit warning annotations when their allowlist is non-empty, so a
green scan is not a silent pass.

### 3.1 Python: `security/pip-audit-ignore.txt`

One line per accepted vulnerability:

```text
<ID> <expires YYYY-MM-DD> <justification>
```

Example:

```text
CVE-2024-00000 2027-01-23 accepted: no fixed release for the pinned version; not reachable from the request path; tracked in #123
```

`scripts/security-pip-audit.sh` parses this file before running pip-audit:

- a missing ID, expiry or justification is an error;
- the expiry must be `YYYY-MM-DD`, compared against UTC today;
- an expired entry fails the job instead of silently suppressing the finding;
- each valid ID is passed as `--ignore-vuln`.

Keep entries for at most 90 days, name the affected package/version in the
justification, and delete the line as soon as the fixed version is in
`uv.lock`. To test an alternate file, set `PIP_AUDIT_IGNORE_FILE=/path/to/file`.

The file is currently empty of active entries: the locked set was clean on
2026-10-24.

### 3.2 Image: `security/trivy-ignore.yaml`

Trivy's YAML ignore file is used (not the legacy flat `.trivyignore`) because it
carries the statement and expiry next to the ID:

```yaml
vulnerabilities:
  - id: CVE-2026-53613
    statement: "util-linux mount TOCTOU; Debian 12 package is unfixed; not exposed by this service."
    expired_at: 2027-01-23
```

Fields:

- `id` — required, one CVE per entry, no wildcards;
- `statement` — required by policy (Trivy itself does not use it for filtering);
- `expired_at` — required by policy; Trivy stops applying the entry after this
  date, so the finding reappears and the job fails until review;
- `paths` / `purls` — optional scoping. The current base-OS entries use IDs only
  because the same util-linux source package ships as several binary packages.

Policy: one CVE per entry, rationale and expiry in the file, maximum 90 days,
and prune entries when the rebuilt base image no longer reports them. Do not
renew mechanically; re-check the Debian tracker and the package version first.

Current state (Trivy DB 2026-10-24, `python:3.12-slim-bookworm`, Debian 12.15):
63 HIGH/CRITICAL findings, 61 HIGH and 2 CRITICAL, across 16 unique CVEs. All
had no fixed Debian package at that point; they are grouped in the file as:

- util-linux 2.38.1-5+deb12u3 — 6 CVEs;
- SQLite 3.40.1-2+deb12u2 — 3 CVEs, including CRITICAL CVE-2025-7458;
- gzip, libacl, ncurses, OpenSSL, systemd, perl-base — 1 each;
- zlib 1.2.13 — CRITICAL CVE-2023-45853, marked `will_not_fix` by Debian because
  the vulnerable code is MiniZip, not zlib itself.

The runtime image is non-root and drops capabilities (see
[security.md](security.md)), which reduces the practical impact of several local
privilege-escalation entries, but it does not remove the package. The actual
remediation is a newer base-image digest or a Debian package update; the
allowlist only buys time for that review.

## 4. Local commands

Dependency audit (no Docker, no project environment install):

```bash
make security
# or directly:
./scripts/security-pip-audit.sh
```

The audit reads `uv.lock` through `uv export`; it does not build or install the
native extras (sherpa-onnx, pilk), so a plain runner is enough.

Equivalent manual export + audit:

```bash
uv export --frozen --all-extras --no-hashes --no-emit-project \
  --format requirements.txt -o /tmp/rui-req.txt
uvx --from pip-audit==2.10.1 pip-audit \
  -r /tmp/rui-req.txt --no-deps --disable-pip --strict
```

Image scan (requires Docker and Trivy; the dev Mac in the runbook has no Docker
CLI, so use the NAS or CI for this one):

```bash
docker build --platform linux/amd64 -t rui:security-scan .
trivy image --severity HIGH,CRITICAL --exit-code 1 \
  --ignorefile security/trivy-ignore.yaml rui:security-scan
```

To produce the same SARIF artifact locally:

```bash
trivy image --severity HIGH,CRITICAL --exit-code 1 \
  --ignorefile security/trivy-ignore.yaml \
  --format sarif --output trivy-results.sarif rui:security-scan
```

Install Trivy with `brew install trivy` or the official release; CI pins
v0.76.1. The first Trivy run downloads the vulnerability DB (hundreds of MB);
later runs reuse the cache.

The full workflow is only exercised on GitHub Actions (the dev Mac has no Docker
CLI). Locally, at least validate the YAML and the shell entry point:

```bash
python3 -c 'import yaml; yaml.safe_load(open(".github/workflows/security.yml"))'
bash -n scripts/security-pip-audit.sh
actionlint .github/workflows/security.yml   # optional, if installed
```

## 5. Updating the base image and pins

The runtime stage currently uses mutable tags:

```text
FROM python:3.12-slim-bookworm     # runtime (Debian 12.15 at scan time)
FROM debian:bookworm-slim          # ffmpeg + model seed builders
FROM node:22-alpine                # web builder (not in the final image)
```

The scan is the backstop for mutable tags. Prefer digest pinning when the
Dockerfile owner next touches the file:

```bash
docker pull python:3.12-slim-bookworm
docker image inspect python:3.12-slim-bookworm --format '{{index .RepoDigests 0}}'
# FROM python:3.12-slim-bookworm@sha256:<digest>
```

Bump procedure:

1. Update the relevant `FROM` (coordinate with the Dockerfile/compose owner).
2. Rebuild the amd64 image and run the Trivy command from §4.
3. Remove allowlist entries that no longer appear; add new unfixed CVEs only
   with a fresh statement and a new expiry (≤90 days).
4. If Debian has a fixed package but the base image has not rebuilt, ask the
   Dockerfile owner for a targeted `apt-get upgrade` or a newer base digest.
   Do not reach for `ignore-unfixed: true`.
5. Re-run `make security` if `uv.lock` changed.

FFmpeg is built from the pinned source tarball (`FFMPEG_VERSION=7.1.5` plus
`FFMPEG_SHA256`). Trivy's OS/package scanners do not attribute CVEs to a
statically linked binary, so FFmpeg advisories must be tracked manually; bump
`FFMPEG_VERSION` and `FFMPEG_SHA256` together, then rebuild. The scan covers the
final runtime image, not the `ffmpeg`, `models` or `web` builder stages.

Python updates:

```bash
uv lock --upgrade-package <package>   # or: uv lock --upgrade
uv lock --check
uv run pytest
```

The image installs `.[kws,channels]`; the audit exports **all** extras
(`dev`, `kws`, `channels`, `weixin-voice`), so it is stricter than the runtime
install. `pip-audit` audits the lock, not the installed container environment;
the image scan covers the packages actually installed in the image.

## 6. Expected first run and tuning

As of 2026-10-24/25:

- **Dependency audit**: green. `uv export --all-extras` produced 48 packages;
  pip-audit 2.10.1 reported no known vulnerabilities.
- **Image scan**: the base image alone had 63 HIGH/CRITICAL findings (16 unique
  CVEs, all unfixed). With `security/trivy-ignore.yaml` those 16 are ignored,
  so the first scan should pass and the summary should list 16 accepted entries.
  The built image adds `libspeexdsp1`, the Python packages and the static FFmpeg
  binary, so the exact count can differ.
- **Runtime**: the first image job has a cold GHA cache: FFmpeg source build,
  npm build and model downloads. Expect roughly 15–25 minutes cold, then several
  minutes when the cache from `docker-publish.yml` is warm. The job timeout is
  50 minutes; the scan itself is typically 1–3 minutes after the DB cache warms.

If the first image run fails:

1. Download `trivy-image-report` (SARIF) and read the job summary.
2. If the CVE has a fixed Debian version, update the base image/packages; do not
   add an ignore.
3. If it is genuinely unfixed in Debian 12 and the component is not exposed,
   add one entry with a ≤90-day expiry and a concrete statement.
4. If Trivy failed to start (DB download, rate limit), re-run; setup/cache is
   usually the cause, not the image.

Tuning knobs:

- Image scan frequency is controlled by the `if:` on `image-scan` and the cron
  in `security.yml`; keep the weekly schedule even if push triggers are reduced.
- The dependency job is intentionally on every push/PR. If it becomes noisy,
  add `paths-ignore` for docs-only changes; never ignore `pyproject.toml`,
  `uv.lock`, `Dockerfile` or `.github/workflows/security.yml`.
- The image build shares the default GHA cache scope with `docker-publish.yml`;
  changing the scope trades cache reuse for isolation.

Coverage gaps this workflow does not close:

- static FFmpeg libraries (manual tracking; see §5);
- builder-stage base images (not part of the shipped runtime);
- secrets/SAST/licensing — the separate `secret-scan` workflow covers
  credential-shaped strings;
- digest pinning of base images (recommendation in §5, owned by the Dockerfile
  owner).

## 7. Files

- `.github/workflows/security.yml` — triggers, jobs, SARIF/artifact upload, gate;
- `scripts/security-pip-audit.sh` — lock export, ignore validation, pip-audit,
  report;
- `security/pip-audit-ignore.txt` — dated Python exceptions;
- `security/trivy-ignore.yaml` — dated image exceptions;
- `Makefile` — `make security`, `make security-deps`, `make security-image`;
- `README.md` — link to this page from “Security and privacy”.

## 8. Troubleshooting

| Symptom | Cause / action |
|---|---|
| `uv export` fails with lock mismatch | `uv lock` is stale; run `uv lock` (or `uv lock --check` to see why) and commit it. |
| pip-audit HTTP/OSV error | Transient service/network; re-run. Manual debugging can add `--vulnerability-service osv`. |
| “ignore … expired” from the script | The exception passed its date. Fix the dependency or renew with a new justification; this failure is intentional. |
| Trivy reports `ignorefile`/YAML error | The file must be `.yaml`/`.yml`; the action input is `trivyignores`, the CLI flag is `--ignorefile`. |
| Trivy DB/setup failure | Action cache/registry hiccup; re-run the job. The gate fails rather than skipping. |
| SARIF upload is skipped/failed | Code scanning may be unavailable (fork/restricted runner). The job does not fail on upload; use the SARIF artifact. |
| `make security` can't find uv | Install uv, or the script falls back to `~/.local/bin/uv`. |
