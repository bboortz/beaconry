.PHONY: help lint format type test check check-full demo smoke clean cleanup install-hooks verify-traceability verify-dockerfile-health-copy verify-dockerfile-sources verify-pinned-versions verify-base-images verify-memory-drift verify-compose-env-propagation verify-docs-drift verify-evidence-pointers verify-per-file-coverage verify-variables verify-install-doc verify-version-sync verify-version-bumped verify-changelog verify-feature-complete bump-patch bump-minor bump-major bump-auto changelog findings status audit soak metrics test-e2e live-rebuild live-status scan set-status test-scenarios test-cli dogfood-scan test-cold-start

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
	@echo "  make verify-variables           VARIABLES.md ↔ code ↔ .env.example ↔ docker-compose.yml drift (BC-039 + BC-040)"
	@echo "  make verify-install-doc         docs/INSTALL.md env vars + .PHONY entries + file paths exist (BC-056)"
	@echo "  make verify-version-sync        /VERSION ↔ /poc/VERSION + hardcoded-literal drift (BC-062)"
	@echo "  make verify-version-bumped      pre-push: VERSION must bump relative to origin/dev (BC-062)"
	@echo "  make verify-changelog           pre-push: CHANGELOG.md has entry for current VERSION (BC-069)"
	@echo "  make verify-feature-complete    shipped story must have fully-closed plan (no ❌/⚠️) (BC-074)"
	@echo "  make smoke                      bash poc/demo.sh --smoke (non-interactive mock, CI)"
	@echo ""
	@echo "Version management (BC-062, BC-069):"
	@echo "  make bump-patch                 X.Y.Z -> X.Y.(Z+1); also regenerates CHANGELOG.md"
	@echo "  make bump-minor                 X.Y.Z -> X.(Y+1).0"
	@echo "  make bump-major                 X.Y.Z -> (X+1).0.0"
	@echo "  make bump-auto                  infer kind from `git log origin/dev..HEAD` (feat → minor, ! → major, else patch)"
	@echo "  make changelog                  regenerate CHANGELOG.md for current VERSION (BC-069)"
	@echo "  make soak                       audit.log scan over last 24h; non-zero if errors OR anomaly thresholds (BC-069/BC-075)"
	@echo "                                  SOAK_WINDOW_S=1800 for 30m; DATA_DIR=/data for alt path"
	@echo "  make metrics                    usability + feature-usage report from audit.log (BC-075)"
	@echo "                                  ARGS=\"usability|features|all --since 7d --json\" to drill in"
	@echo ""
	@echo "Tiers:"
	@echo "  make check         FAST TIER — all gates above (~7s)"
	@echo "  make check-full    FULL TIER — check + opt-in markers + make test-e2e + make test-scenarios + make test-cli (~1-2m)"
	@echo ""
	@echo "Operator CLIs:"
	@: # BC-044 BEGIN OPERATOR_CLI_TARGETS — authoritative list the live-smoke harness autodetects.
	@echo "  make status                     one-line-per-service health table (BC-013)"
	@echo "  make findings                   read-only CLI view of findings DB"
	@echo "                                  STATUS=regressed|open|verification_failed|snoozed|accepted|fixed|fixed_verified|auto_fixed"
	@echo "                                  HOST=<label>  filter to a single scout's findings (BC-057)"
	@echo "                                  BEACONRY_FINDINGS_LOCAL=1 forces local .beaconry-cache DB over compose volume"
	@echo "  make audit                      operator CLI view of /data/audit.log (BC-042)"
	@echo "                                  LAST=N SINCE=1h EVENT=a,b CVE=X HOST=X FINDING=X COUNT=1 FOLLOW=1"
	@echo "  make scan                       trigger an immediate scout scan (BC-037/BC-055)"
	@echo "                                  bypasses SCAN_INTERVAL — local scout or remote hub /scan/trigger"
	@echo "  make set-status                 mutate finding status over HTTP (BC-055)"
	@echo "                                  FINDING=<id> STATUS=snoozed|accepted|fixed|open"
	@: # BC-044 END OPERATOR_CLI_TARGETS
	@echo "  make test-scenarios             run live-stack behavior scenarios (BC-038)"
	@echo "                                  spins up isolated hub w/ BEACONRY_TEST_MODE=1 and runs 10 scenarios"
	@echo "  make test-cli                   live-smoke operator CLIs against the running stack (BC-044)"
	@echo "                                  requires live-rebuild first; set BEACONRY_SKIP_CLI_SMOKE=1 to opt out"
	@echo "  make test-cold-start            fresh beaconry stack w/ real grype DB download (BC-082)"
	@echo "                                  the "will homelab install work?" probe"
	@echo "  make dogfood-scan               grype rescan beaconry's own images; fails on High+ fixable (BC-082)"
	@echo "                                  BEACONRY_SKIP_DOGFOOD=1 to opt out (dev-only emergency)"
	@echo "  make verify-pinned-versions     Dockerfile pinned-version floor lint (BC-082)"
	@echo "                                  DOCKER_CLI_VERSION ≥ 29.9.2 (BC-081), builder base python:3.11+, distroless runtime"
	@echo "  make verify-base-images         Anonymous-pull preflight for every FROM in Dockerfiles (BC-083)"
	@echo "                                  24h cached; BEACONRY_SKIP_BASE_IMAGES=1 for air-gapped CI"
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
	@echo "  make cleanup                    tear down beaconry test stacks + remove leaked test images (BC-080)"

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

# BC-082: Dockerfile pinned-version floor lint. DOCKER_CLI_VERSION ≥ 29.9.2
# (BC-081 floor), builder base python:3.11+-slim-{bookworm,trixie},
# runtime stage distroless/python3-debian12:nonroot exactly.
verify-pinned-versions:
	$(PYTHON) -m poc.scripts.verify_pinned_versions

# BC-083 CAP-T171: standing preflight. Enumerate every FROM across
# Dockerfiles; anonymous-token + manifest probe against ghcr/gcr/docker.io
# registries; fail on 401/403/404/5xx. 24h cache. Opt out with
# BEACONRY_SKIP_BASE_IMAGES=1 (air-gapped CI).
verify-base-images:
	$(PYTHON) -m poc.scripts.verify_base_images

# BC-039 + BC-040: four-way env variable drift lint.
# Fails if a variable: (a) read in code without a VARIABLES.md row, (b) in
# VARIABLES.md without a reader, (c) in VARIABLES.md but missing from
# .env.example (unless dev-only allowlisted), (d) in .env.example without
# a VARIABLES.md row, (e) in docker-compose.yml `environment:` without a
# VARIABLES.md row, (f) default-literal mismatch between .env.example and
# VARIABLES.md, (g) row has an unfilled `*(placeholder)*` stub.
verify-variables:
	$(PYTHON) -m poc.scripts.verify_variables_documented

# BC-056: docs/INSTALL.md drift lint — env vars, make targets, repo file
# paths referenced from INSTALL.md must still match the real repo.
verify-install-doc:
	$(PYTHON) -m poc.scripts.verify_install_doc

# BC-062: VERSION-file + hardcoded-literal drift lint. /VERSION must byte-equal
# poc/VERSION (compose build-context mirror), and hub/scout/cli must not carry a
# stale `*_VERSION = "x.y.z"` literal that drifts from VERSION.
verify-version-sync:
	$(PYTHON) -m poc.scripts.verify_version_sync

# BC-062: pre-push enforcement — refuse to push unless VERSION bumped.
verify-version-bumped:
	$(PYTHON) -m poc.scripts.verify_version_bumped

# BC-062: SemVer bump helpers. `bump-auto` infers from conventional-commit
# messages since origin/dev. Writes both VERSION + poc/VERSION.
# BC-069: bump_version also regenerates CHANGELOG.md automatically.
bump-patch:
	$(PYTHON) -m poc.scripts.bump_version --patch
bump-minor:
	$(PYTHON) -m poc.scripts.bump_version --minor
bump-major:
	$(PYTHON) -m poc.scripts.bump_version --major
bump-auto:
	$(PYTHON) -m poc.scripts.bump_version --auto

# BC-069: regenerate CHANGELOG.md for the current VERSION. Reads commits
# since the last `## [v*]` header; appends a Keep-A-Changelog section.
changelog:
	$(PYTHON) -m poc.scripts.changelog

# BC-069: pre-push lint — refuse pushes without a CHANGELOG entry for VERSION.
verify-changelog:
	$(PYTHON) -m poc.scripts.verify_changelog

# BC-074: refuse pushes where a shipped story has an incomplete plan.
# Pairs each `docs/stories/BC-NNN-*.md` with status: shipped against its
# plan's shipping-evidence table; fails on open ❌ / ⚠️ rows.
verify-feature-complete:
	$(PYTHON) -m poc.scripts.verify_feature_complete

# BC-069: operator soak check — scan audit.log for error events + anomaly
# threshold signals (BC-075) in the last 24h (BC-074 window). Returns
# non-zero if anything suspicious or threshold-breaching landed.
#   SOAK_WINDOW_S=1800 make soak    # 30-min window
#   DATA_DIR=/data make soak        # override audit log path
soak:
	$(PYTHON) -m poc.scripts.soak

# BC-075: usability + feature-usage metrics from audit.log.
#   make metrics                    # last 7d, both lenses
#   make metrics ARGS="usability"   # key-user lens
#   make metrics ARGS="features --json --since 24h"
metrics:
	$(PYTHON) -m poc.scripts.metrics $(ARGS)

check: lint type test verify-per-file-coverage verify-traceability verify-dockerfile-health-copy verify-dockerfile-sources verify-pinned-versions verify-base-images verify-memory-drift verify-compose-env-propagation verify-docs-drift verify-evidence-pointers verify-variables verify-install-doc verify-version-sync verify-changelog verify-feature-complete smoke
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
	@echo "==> Full tier: running scenario harness (BC-038; isolated hub + 10 behavior scenarios)"
	$(MAKE) test-scenarios
	@echo ""
	@echo "==> Full tier: running CLI-smoke harness (BC-044; against live stack; BEACONRY_SKIP_CLI_SMOKE=1 to opt out)"
	$(MAKE) test-cli
	@echo ""
	@echo "==> Full tier: cold-start smoke (BC-082; boots fresh beaconry stack w/ real grype DB)"
	$(MAKE) test-cold-start
	@echo ""
	@echo "==> Full tier: dogfood-grype scan (BC-082; BEACONRY_SKIP_DOGFOOD=1 to opt out)"
	$(MAKE) dogfood-scan
	@echo ""
	@echo "==> Full tier: cleanup (BC-080 — remove leaked test images)"
	$(MAKE) cleanup
	@echo ""
	@echo "✓ Full tier (make check-full) passed"

findings:
	@$(PYTHON) -m poc.scripts.findings $(if $(STATUS),--status $(STATUS)) $(if $(HOST),--host $(HOST))

# BC-042: operator view of audit.log with filters + compose-aware routing.
# All filters propagate via env (LAST, SINCE, UNTIL, EVENT, CVE, HOST, FINDING,
# COUNT, FOLLOW) so `make audit SINCE=1h` just works.
audit:
	@$(PYTHON) -m poc.scripts.audit

# BC-037: on-demand scan trigger. Useful after an out-of-band fix
# (`docker compose pull`, manual rebuild) — operator doesn't want to wait
# up to SCAN_INTERVAL for the next cycle to re-verify.
scan:
	@$(PYTHON) -m poc.scripts.scan_cli

# BC-055: mutate finding status on the hub (local or remote).
# make set-status FINDING=<finding_id> STATUS=snoozed|accepted|fixed|open
set-status:
	@$(PYTHON) -m poc.scripts.set_status

# BC-038: live-stack scenario harness. Spins up an isolated hub (test-mode
# enabled, grype skipped), runs 10 behavior scenarios covering the shipped
# BCs, tears down. See `poc/scenarios/run.sh`.
test-scenarios:
	bash poc/scenarios/run.sh; EXIT=$$?; \
	 $(MAKE) --no-print-directory cleanup; \
	 exit $$EXIT

# BC-044: live-smoke operator CLI harness. Non-destructive scenarios against
# the running stack — catches the class of bug (Dockerfile COPY omissions,
# docker-exec inline-script bugs, output buffering) that unit tests miss.
# BEACONRY_SKIP_CLI_SMOKE=1 is the escape hatch for environments without
# a running stack.
test-cli:
	@$(PYTHON) -m poc.scripts.cli_smoke

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
	$(PYTHON) -m pytest -m e2e poc/tests/e2e/ -v --no-cov; EXIT=$$?; \
	 echo "==> tearing down test stack"; \
	 cd poc && docker compose -p beaconry-test -f docker-compose.test.yml down -v; \
	 cd .. && $(MAKE) --no-print-directory cleanup; \
	 exit $$EXIT

# BC-080: tear down beaconry test stacks + remove images beaconry's own tests
# pulled or built. Idempotent. See poc/scripts/cleanup.py for the full
# removable/preserved allowlist. Safety: never touches beaconry-hub:latest,
# beaconry-scout:latest, or base images.
cleanup:
	@$(PYTHON) -m poc.scripts.cleanup

# BC-082: dogfood grype rescan of beaconry's own images. Fails if any
# High/Critical finding has a published fix. Opt out via
# BEACONRY_SKIP_DOGFOOD=1 (dev-only emergency).
dogfood-scan:
	@$(PYTHON) -m poc.scripts.dogfood_scan

# BC-082: cold-start smoke. Brings up poc/docker-compose.cold-start.yml with
# throwaway volumes + real grype DB download, asserts all 8 health endpoints
# (hub + scout × livez/readyz/startupz/healthz) go green within 180s. Tears
# down + cleanup on exit (success or failure). The honest "will `make
# live-rebuild` on a fresh homelab host succeed?" probe.
test-cold-start:
	@echo "==> BC-082: starting isolated cold-start compose stack (project: beaconry-cold-start)"
	cd poc && docker compose -p beaconry-cold-start -f docker-compose.cold-start.yml up -d --build
	@echo "==> running cold-start test"
	$(PYTHON) -m pytest -m cold_start poc/tests/integration/ -v --no-cov; EXIT=$$?; \
	 echo "==> tearing down cold-start stack"; \
	 cd poc && docker compose -p beaconry-cold-start -f docker-compose.cold-start.yml down -v; \
	 cd .. && $(MAKE) --no-print-directory cleanup; \
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
