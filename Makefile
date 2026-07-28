.PHONY: install lint format typecheck test check

install:
	poetry install

format:
	poetry run black src tests examples
	poetry run ruff check --fix src tests examples

lint:
	poetry run black --check src tests examples
	poetry run ruff check src tests examples

typecheck:
	poetry run mypy src

test:
	poetry run pytest

check: lint typecheck test
