.PHONY: help lint format type test check check-full demo smoke clean install-hooks verify-traceability findings

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
	@echo "  make check         FAST TIER — lint + type + test + verify-traceability + smoke (~7s)"
	@echo "  make check-full    FULL TIER — check + pytest -m 'compose or real_tools' (~1-2m)"
	@echo "  make findings                read-only CLI view of findings DB (STATUS=open|snoozed|accepted|fixed)"
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

check: lint type test verify-traceability smoke
	@echo ""
	@echo "✓ Fast tier (make check) passed"

check-full: check
	@echo ""
	@echo "==> Full tier: running opt-in markers (compose + real_tools)"
	pytest -m "compose or real_tools" --no-cov
	@echo ""
	@echo "✓ Full tier (make check-full) passed"

findings:
	@$(PYTHON) -m poc.scripts.findings $(if $(STATUS),--status $(STATUS))

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
