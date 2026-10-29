.PHONY: help lint format type test check demo smoke clean install-hooks verify-traceability

PYTHON ?= python3
PYTEST_ARGS ?=
ARGS ?=

help:
	@echo "Beaconry — local gates (what CI runs)"
	@echo ""
	@echo "  make lint          ruff check + ruff format --check"
	@echo "  make format        ruff format (fix in place)"
	@echo "  make type          pyright (basic mode)"
	@echo "  make test          pytest + coverage"
	@echo "  make verify-traceability   docs/CAPABILITIES.md ↔ tests bidirectional check"
	@echo "  make check         lint + type + test + verify-traceability (what CI blocks on)"
	@echo "  make demo                    docker compose up (real pipeline, host inventory)"
	@echo "  BEACONRY_MOCK=1 make demo    local Python mock (fast dev loop)"
	@echo "  make demo ARGS=--self-test   Telegram canary (mock mode only)"
	@echo "  make smoke                   bash poc/demo.sh --smoke (non-interactive mock, CI)"
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

verify-traceability:
	$(PYTHON) -m poc.scripts.verify_traceability

check: lint type test verify-traceability
	@echo ""
	@echo "✓ All local gates passed"

demo:
	@if [ "$$BEACONRY_MOCK" = "1" ]; then \
		echo "==> BEACONRY_MOCK=1 — running local Python mock pipeline"; \
		PATH="$(PWD)/.beaconry-cache/bin:$$PATH" $(PYTHON) -m poc.demo $(ARGS); \
	else \
		echo "==> real mode — running Docker Compose stack"; \
		cd poc && docker compose up --build; \
	fi

smoke:
	bash poc/demo.sh --smoke

install-hooks:
	pre-commit install
	pre-commit install --hook-type pre-push

clean:
	rm -rf .pytest_cache .ruff_cache .pyright .coverage coverage.xml htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} +
