.PHONY: install dev doctor serve audio measure test lint web docker

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
