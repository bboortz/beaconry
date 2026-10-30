.PHONY: help lint format type test check check-full demo smoke clean install-hooks verify-traceability verify-dockerfile-health-copy verify-memory-drift verify-compose-env-propagation verify-docs-drift verify-evidence-pointers findings status test-e2e

PYTHON ?= python3
PYTEST_ARGS ?=
ARGS ?=

help:
	@echo "Beaconry — local gates (what CI runs)"
	@echo ""
	@echo "Gates (sub-checks that make check runs in order):"
	@echo "  make lint                       ruff check + ruff format --check"
	@echo "  make format                     ruff format (fix in place)"
	@echo "  make type                       pyright (basic mode)"
	@echo "  make test                       pytest + coverage"
	@echo "  make verify-traceability        docs/CAPABILITIES.md ↔ tests bidirectional check"
	@echo "  make verify-dockerfile-health-copy      services importing beaconry_health COPY it (BC-012)"
	@echo "  make verify-memory-drift        memory/*.md matches shipped reality (BC-015)"
	@echo "  make verify-compose-env-propagation     every BEACONRY_* env read by code is in docker-compose.yml (BC-011)"
	@echo "  make verify-docs-drift          Makefile help + README match code enumerations (BC-016)"
	@echo "  make verify-evidence-pointers   plan shipping-evidence pointers resolve to real tests (BC-019)"
	@echo "  make smoke                      bash poc/demo.sh --smoke (non-interactive mock, CI)"
	@echo ""
	@echo "Tiers:"
	@echo "  make check         FAST TIER — all gates above (~7s)"
	@echo "  make check-full    FULL TIER — check + pytest -m 'compose or real_tools' (~1-2m)"
	@echo ""
	@echo "Operator CLIs:"
	@echo "  make status                     one-line-per-service health table (BC-013)"
	@echo "  make findings                   read-only CLI view of findings DB"
	@echo "                                  STATUS=open|verification_failed|snoozed|accepted|fixed|fixed_verified"
	@echo "                                  BEACONRY_FINDINGS_LOCAL=1 forces local .beaconry-cache DB over compose volume"
	@echo ""
	@echo "Demo:"
	@echo "  make demo                       docker compose up (real pipeline, host inventory)"
	@echo "  BEACONRY_MOCK=1 make demo       local Python mock (fast dev loop)"
	@echo "  make demo ARGS=--self-test      Telegram canary (mock mode only)"
	@echo "  make test-e2e                   isolated BC-011 probe e2e (BC-017; no Telegram needed)"
	@echo ""
	@echo "Housekeeping:"
	@echo "  make install-hooks              pre-commit install + pre-push"
	@echo "  make clean                      remove caches"

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

verify-dockerfile-health-copy:
	$(PYTHON) -m poc.scripts.verify_dockerfile_health_copy

verify-memory-drift:
	$(PYTHON) -m poc.scripts.verify_memory_drift

verify-compose-env-propagation:
	$(PYTHON) -m poc.scripts.verify_compose_env_propagation

verify-docs-drift:
	$(PYTHON) -m poc.scripts.verify_docs_drift

verify-evidence-pointers:
	$(PYTHON) -m poc.scripts.verify_evidence_pointers

check: lint type test verify-traceability verify-dockerfile-health-copy verify-memory-drift verify-compose-env-propagation verify-docs-drift verify-evidence-pointers smoke
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

test-e2e:
	@echo "==> BC-017: starting isolated test compose stack (project: beaconry-test)"
	cd poc && docker compose -p beaconry-test -f docker-compose.test.yml up -d --build
	@echo "==> waiting for hub-test to be ready…"
	@until curl -sS http://localhost:9000/livez >/dev/null 2>&1; do sleep 1; done
	@echo "==> running e2e tests"
	-$(PYTHON) -m pytest -m e2e poc/tests/e2e/ -v --no-cov; EXIT=$$?; \
	 echo "==> tearing down test stack"; \
	 cd poc && docker compose -p beaconry-test -f docker-compose.test.yml down -v; \
	 exit $$EXIT

status:
	@$(PYTHON) -m poc.scripts.status
