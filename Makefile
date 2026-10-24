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
