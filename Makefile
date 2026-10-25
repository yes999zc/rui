.PHONY: install dev doctor serve audio measure test lint web docker security security-deps security-image

install:
	uv sync --extra dev

doctor:
	uv run rui doctor

serve:
	uv run rui serve

audio:
	uv run rui audio --config $${RUI_CONFIG:-$$HOME/.config/rui/config.yaml} --device $${DEVICE:-living}

measure:
	uv run python scripts/measure_plane.py --config $${RUI_CONFIG:-$$HOME/.config/rui/config.yaml} --device $${DEVICE:-living} --seconds $${SECONDS:-300}

test:
	uv run pytest

lint:
	uv run ruff check src tests

web:
	cd web && npm install && npm run build

docker:
	docker build -t rui:dev .

# Fast check: audits uv.lock (all extras) with pip-audit; no Docker needed.
security: security-deps

security-deps:
	bash scripts/security-pip-audit.sh

# Local image scan. Requires Docker and Trivy; CI builds the real amd64 image and
# runs the same Trivy policy. See docs/security-scanning.md.
security-image:
	docker build --platform linux/amd64 -t rui:security-scan .
	trivy image --severity HIGH,CRITICAL --exit-code 1 \
		--ignorefile security/trivy-ignore.yaml rui:security-scan

# ---------------------------------------------------------------------------
# Release helpers. None of these targets tag, push, publish or deploy anything.
# Curated release notes live in docs/releases/vX.Y.Z.md; the generated draft is
# written to docs/releases/vX.Y.Z.generated.md. See docs/releases/README.md.

VERSION ?= $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
TAG ?= v$(VERSION)
PREV_TAG ?= $(shell git describe --tags --abbrev=0 $(TAG)^ 2>/dev/null || git describe --tags --abbrev=0 HEAD 2>/dev/null || true)
RELEASE_TO ?= $(shell git rev-parse -q --verify "$(TAG)^{commit}" >/dev/null 2>&1 && echo "$(TAG)" || echo HEAD)

.PHONY: changelog release

# Conventional-commit notes for unreleased changes (latest tag -> HEAD).
changelog:
	@scripts/release-notes.sh --to HEAD

# Machine draft for VERSION; writes docs/releases/vX.Y.Z.generated.md and never publishes.
# Curate the draft into docs/releases/vX.Y.Z.md.
release:
	@scripts/release-notes.sh --version "$(VERSION)" --from "$(PREV_TAG)" --to "$(RELEASE_TO)" \
		--out "docs/releases/v$(VERSION).generated.md"
	@echo "Draft: docs/releases/v$(VERSION).generated.md (not published)."
	@echo "Curate it into docs/releases/v$(VERSION).md, then create the GitHub Release by hand."
