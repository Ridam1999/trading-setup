.PHONY: setup check test lint data study backtest paper ui

setup:
	uv sync --extra dev --no-editable --reinstall-package nse-setup-research-engine

check: lint
	uv run --no-sync mypy src
	uv run --no-sync python -m pytest

test:
	uv run --no-sync python -m pytest

lint:
	uv run --no-sync ruff check src tests
	uv run --no-sync ruff format --check src tests

study:
	uv run --no-sync nse-engine study --help

backtest:
	uv run --no-sync nse-engine backtest --help

data paper ui:
	@echo "This target is implemented in its corresponding project phase."
