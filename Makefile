.PHONY: install dev doctor serve test lint web docker

install:
	uv sync --extra dev

doctor:
	uv run house-ear doctor

serve:
	uv run house-ear serve

test:
	uv run pytest

lint:
	uv run ruff check src tests

web:
	cd web && npm install && npm run build

docker:
	docker build -t house-ear:dev .
