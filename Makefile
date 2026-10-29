.PHONY: help lint format type test check smoke clean install-hooks

PYTHON ?= python3
PYTEST_ARGS ?=

help:
	@echo "Beaconry — local gates (what CI runs)"
	@echo ""
	@echo "  make lint          ruff check + ruff format --check"
	@echo "  make format        ruff format (fix in place)"
	@echo "  make type          pyright (basic mode)"
	@echo "  make test          pytest + coverage"
	@echo "  make check         lint + type + test (what CI blocks on)"
	@echo "  make smoke         bash poc/demo.sh --smoke"
	@echo "  make install-hooks pre-commit install + pre-push"
	@echo "  make clean         remove caches"

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .
	ruff check --fix .

type:
	pyright

test:
	pytest $(PYTEST_ARGS)

check: lint type test
	@echo ""
	@echo "✓ All local gates passed"

smoke:
	bash poc/demo.sh --smoke

install-hooks:
	pre-commit install
	pre-commit install --hook-type pre-push

clean:
	rm -rf .pytest_cache .ruff_cache .pyright .coverage coverage.xml htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} +
