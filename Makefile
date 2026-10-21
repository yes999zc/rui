.PHONY: install dev doctor serve audio measure test lint web docker

install:
	uv sync --extra dev

doctor:
	uv run house-ear doctor

serve:
	uv run house-ear serve

audio:
	uv run house-ear audio --config $${HOUSE_EAR_CONFIG:-$$HOME/.config/house-ear/config.yaml} --device $${DEVICE:-living}

measure:
	uv run python scripts/measure_plane.py --config $${HOUSE_EAR_CONFIG:-$$HOME/.config/house-ear/config.yaml} --device $${DEVICE:-living} --seconds $${SECONDS:-300}

test:
	uv run pytest

lint:
	uv run ruff check src tests

web:
	cd web && npm install && npm run build

docker:
	docker build -t house-ear:dev .
