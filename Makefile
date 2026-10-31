.PHONY: help lint format type test check check-full demo smoke clean install-hooks verify-traceability verify-dockerfile-health-copy verify-dockerfile-sources verify-memory-drift verify-compose-env-propagation verify-docs-drift verify-evidence-pointers verify-per-file-coverage findings status test-e2e live-rebuild live-status scan

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
	@echo "  make verify-dockerfile-sources  every service-local .py is COPY'd by its Dockerfile (BC-021)"
	@echo "  make verify-memory-drift        memory/*.md matches shipped reality (BC-015)"
	@echo "  make verify-compose-env-propagation     every BEACONRY_* env read by code is in docker-compose.yml (BC-011)"
	@echo "  make verify-docs-drift          Makefile help + README match code enumerations (BC-016)"
	@echo "  make verify-evidence-pointers   plan shipping-evidence pointers resolve to real tests (BC-019)"
	@echo "  make verify-per-file-coverage   every file ≥ 65% coverage (or allowlisted with reason)"
	@echo "  make smoke                      bash poc/demo.sh --smoke (non-interactive mock, CI)"
	@echo ""
	@echo "Tiers:"
	@echo "  make check         FAST TIER — all gates above (~7s)"
	@echo "  make check-full    FULL TIER — check + pytest -m 'compose or real_tools' + make test-e2e (~1-2m)"
	@echo ""
	@echo "Operator CLIs:"
	@echo "  make status                     one-line-per-service health table (BC-013)"
	@echo "  make findings                   read-only CLI view of findings DB"
	@echo "                                  STATUS=regressed|open|verification_failed|snoozed|accepted|fixed|fixed_verified"
	@echo "                                  BEACONRY_FINDINGS_LOCAL=1 forces local .beaconry-cache DB over compose volume"
	@echo "  make scan                       trigger an immediate scout scan (BC-037)"
	@echo "                                  bypasses SCAN_INTERVAL — useful after an out-of-band fix"
	@echo ""
	@echo "Demo:"
	@echo "  make demo                       docker compose up (real pipeline, host inventory)"
	@echo "  BEACONRY_MOCK=1 make demo       local Python mock (fast dev loop)"
	@echo "  make demo ARGS=--self-test      Telegram canary (mock mode only)"
	@echo "  make test-e2e                   isolated BC-011 probe e2e (BC-017; no Telegram needed)"
	@echo ""
	@echo "Live stack (operator — use these before any real-world verification):"
	@echo "  make live-rebuild               rebuild + restart hub + scout from current source"
	@echo "                                  Always run this BEFORE interpreting /healthz,"
	@echo "                                  make findings, or Telegram screenshots as evidence."
	@echo "                                  Stale containers = false test results."
	@echo "  make live-status                compare running image build-time to source mtime"
	@echo "                                  (hint when rebuild is overdue)"
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

verify-per-file-coverage:
	$(PYTHON) -m poc.scripts.verify_per_file_coverage

verify-dockerfile-sources:
	$(PYTHON) -m poc.scripts.verify_dockerfile_sources

check: lint type test verify-per-file-coverage verify-traceability verify-dockerfile-health-copy verify-dockerfile-sources verify-memory-drift verify-compose-env-propagation verify-docs-drift verify-evidence-pointers smoke
	@echo ""
	@echo "✓ Fast tier (make check) passed"

check-full: check
	@echo ""
	@echo "==> Full tier: running opt-in markers (compose + real_tools)"
	pytest -m "compose or real_tools" --no-cov
	@echo ""
	@echo "==> Full tier: running e2e stack (BC-017; brings up isolated compose project)"
	$(MAKE) test-e2e
	@echo ""
	@echo "✓ Full tier (make check-full) passed"

findings:
	@$(PYTHON) -m poc.scripts.findings $(if $(STATUS),--status $(STATUS))

# BC-037: on-demand scan trigger. Useful after an out-of-band fix
# (`docker compose pull`, manual rebuild) — operator doesn't want to wait
# up to SCAN_INTERVAL for the next cycle to re-verify.
scan:
	@if ! docker inspect --format='{{.State.Running}}' beaconry-scout 2>/dev/null | grep -q true; then \
		echo "✗ scout container not running — start the stack with 'make demo' first"; \
		exit 1; \
	fi
	@echo "==> triggering scan via POST http://127.0.0.1:8001/scan/trigger"
	@response=$$(curl -sS -w '\n%{http_code}' -X POST http://127.0.0.1:8001/scan/trigger); \
	body=$$(echo "$$response" | head -n 1); \
	code=$$(echo "$$response" | tail -n 1); \
	case "$$code" in \
	  202) echo "✓ scan scheduled — $$body"; echo "   tail scout logs to watch: docker logs -f beaconry-scout" ;; \
	  409) echo "⚠️  scan already running — $$body"; exit 2 ;; \
	  503) echo "✗ scout 503 — $$body"; exit 3 ;; \
	  *)   echo "✗ unexpected HTTP $$code — $$body"; exit 4 ;; \
	esac

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

# Rebuild + restart the live stack from current source. Always run before
# any real-world verification against the running hub/scout — stale
# containers are the #1 source of "my change doesn't work" false negatives
# (bit us 3+ times in BC-020 and BC-022). See memory/workflow.md.
live-rebuild:
	@echo "==> Rebuilding beaconry-hub + beaconry-scout from current source…"
	cd poc && docker compose up -d --build
	@echo "==> Waiting for /readyz on hub…"
	@until curl -sS http://localhost:8000/readyz >/dev/null 2>&1; do sleep 1; done
	@echo "==> Live stack now matches source. /healthz:"
	@docker exec beaconry-hub python3 -c "import urllib.request,json; print(json.dumps(json.loads(urllib.request.urlopen('http://localhost:8000/healthz').read())['checks'], indent=2))" 2>&1 | sed 's/^/    /'

# Verdict: is the running stack up-to-date with current source? Prints FRESH
# or STALE and the delta in seconds. Checks hub + scout.
live-status:
	@for svc in hub scout; do \
	   CONTAINER="beaconry-$$svc"; \
	   BUILT_ISO=$$(docker inspect $$CONTAINER --format='{{.Created}}' 2>/dev/null); \
	   if [ -z "$$BUILT_ISO" ]; then \
	     echo "$$CONTAINER: not running (run 'make live-rebuild')"; continue; \
	   fi; \
	   BUILT_EPOCH=$$(date -d "$$BUILT_ISO" +%s); \
	   SRC_EPOCH=$$(find poc/$$svc -name '*.py' -printf '%T@\n' 2>/dev/null | sort -nr | head -1 | cut -d. -f1); \
	   DELTA=$$(( SRC_EPOCH - BUILT_EPOCH )); \
	   if [ "$$DELTA" -gt 0 ]; then \
	     echo "$$CONTAINER: ⚠️ STALE — source is $${DELTA}s newer than container. Run 'make live-rebuild'."; \
	   else \
	     echo "$$CONTAINER: ✓ FRESH — container is $$(( -DELTA ))s newer than source."; \
	   fi; \
	 done
