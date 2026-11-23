.PHONY: help lint format type test check check-docs check-full demo smoke clean cleanup install-hooks verify-traceability verify-dockerfile-health-copy verify-dockerfile-sources verify-pinned-versions verify-base-images verify-memory-drift verify-compose-env-propagation verify-compose-memory-limits verify-docs-drift verify-docs-layout verify-evidence-pointers verify-per-file-coverage verify-variables verify-install-doc verify-version-sync verify-version-bumped verify-changelog verify-feature-complete bump-patch bump-minor bump-major bump-auto changelog findings status audit soak metrics verify-integrity test-e2e live-rebuild live-status scan set-status test-scenarios test-cli dogfood-scan dogfood-sbom test-cold-start perf perf-seed

# Prefer the project venv (`python -m venv .venv`) when it exists so `make
# test` / `make check` work without the user having to `source .venv/bin/activate`
# first. CI uses actions/setup-python which installs deps into the system
# interpreter — no .venv, so this transparently falls back to python3/pytest.
# Running without the venv and without CI's install step is the failure mode
# this guards against (bare `pytest` → system interpreter → ModuleNotFoundError
# for telegram/docker/pytest-asyncio).
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PYTEST ?= $(if $(wildcard .venv/bin/pytest),.venv/bin/pytest,pytest)
RUFF ?= $(if $(wildcard .venv/bin/ruff),.venv/bin/ruff,ruff)
PYRIGHT ?= $(if $(wildcard .venv/bin/pyright),.venv/bin/pyright,pyright)
PYTEST_ARGS ?=
ARGS ?=

# BC-099: BuildKit is REQUIRED (Dockerfiles carry `# syntax=docker/dockerfile:1`
# which only BuildKit accepts). Export at Makefile scope so every subshelled
# `docker build` and `docker compose build` goes through the BuildKit frontend
# — classic builder is incompatible with the BC-099 ABI guard's
# `RUN --mount=from=<stage>` syntax. Docker ≥ 23 defaults to BuildKit via
# buildx shim; these exports are belt-and-braces for older installs + CI.
export DOCKER_BUILDKIT = 1
export COMPOSE_DOCKER_CLI_BUILD = 1

help:
	@echo "Beaconry — local gates (what CI runs)"
	@echo ""
	@echo "Gates (sub-checks that make check runs in order):"
	@echo "  make lint                       ruff check + ruff format --check"
	@echo "  make format                     ruff format (fix in place)"
	@echo "  make type                       pyright (basic mode)"
	@echo "  make test                       pytest + coverage"
	@echo "  make verify-traceability        docs/specs/CAPABILITIES.md ↔ tests bidirectional check"
	@echo "  make verify-dockerfile-health-copy      services importing beaconry_health COPY it (BC-012)"
	@echo "  make verify-dockerfile-sources  every service-local .py is COPY'd by its Dockerfile (BC-021)"
	@echo "  make verify-memory-drift        memory/*.md matches shipped reality (BC-015)"
	@echo "  make verify-compose-env-propagation     every BEACONRY_* env read by code is in docker-compose.yml (BC-011)"
	@echo "  make verify-compose-memory-limits       every beaconry service in compose has hard mem + CPU caps (BC-141)"
	@echo "  make verify-docs-drift          Makefile help + README match code enumerations (BC-016)"
	@echo "  make verify-docs-layout         docs/ three-folder layout — no stray .md at top-level (BC-147)"
	@echo "  make verify-evidence-pointers   plan shipping-evidence pointers resolve to real tests (BC-019)"
	@echo "  make verify-per-file-coverage   every file ≥ 65% coverage (or allowlisted with reason)"
	@echo "  make verify-variables           VARIABLES.md ↔ code ↔ .env.example ↔ docker-compose.yml drift (BC-039 + BC-040)"
	@echo "  make verify-install-doc         docs/specs/INSTALL.md env vars + .PHONY entries + file paths exist (BC-056)"
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
	@echo "  make verify-integrity           verify every scout→hub scan iteration is lossless (BC-122)"
	@echo "                                  ARGS=\"--since 24h\" to narrow window"
	@echo "  make perf                       HTTP latency budget gate — P95 ≤ 300ms per endpoint (BC-132)"
	@echo "                                  BEACONRY_SKIP_PERF=1 to opt out; BEACONRY_PERF_FIXTURE_EVENTS=N to stress"
	@echo "  make perf-seed                  seed a deterministic perf fixture (BC-132)"
	@echo "                                  OUT=/path (default .beaconry-cache/perf); SEED=N"
	@echo ""
	@echo "Tiers:"
	@echo "  make check         FAST TIER — all gates above (~7s)"
	@echo "  make check-fast    SH-042 alias for check (forward-compat; SH-023 will tighten it)"
	@echo "  make check-docs    SH-055 fleet-wide docs/version drift wrapper (delegates to verify-* targets)"
	@echo "  make check-full    FULL TIER — check + opt-in markers + make test-e2e + make test-scenarios + make test-cli (~1-2m)"
	@echo "  make hook-commit   SH-042 fleet tier — commit-stage gate (~15s)"
	@echo "  make hook-push     SH-042 fleet tier — push-stage gate (~2min)"
	@echo ""
	@echo "Operator CLIs:"
	@: # BC-044 BEGIN OPERATOR_CLI_TARGETS — authoritative list the live-smoke harness autodetects.
	@echo "  make status                     one-line-per-service health table (BC-013)"
	@echo "  make findings                   read-only CLI view of findings DB"
	@echo "                                  STATUS=regressed|open|verification_failed|awaiting_upstream|snoozed|accepted|fixed|fixed_verified|auto_fixed"
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
	@echo "  make dogfood-sbom               grype rescan against each image's baked-in /sbom.json (BC-096)"
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
	@echo "Shepherd dev-cycle contract (SH-014 / SH-019):"
	@echo "  make build                      no-op (Python service — no compiled artifact)"
	@echo "  make test-affected              scoped pytest by changed module (BC-149 / SH-023)"
	@echo "  make test-updates               alias of test-affected (local naming parity with homelab)"
	@echo "  make release-artifacts          no-op (release flow is bump-* + CD pipeline)"
	@echo "  make ci-branch                  5-min PR gate: lint + test"
	@echo "  make ci-patch                   post-merge smoke"
	@echo "  make ci-release                 FF dev → main + tag (SH-029); existing bump-* unchanged"
	@echo "  make ci-release-promote         shared FF-promote target (invoked by ci-release)"
	@echo "  make ci-nightly                 nightly full gate (alias for check)"
	@echo "  make ci-nightly-body            nightly tier body (invoked by ci-nightly)"
	@echo ""
	@echo "Housekeeping:"
	@echo "  make install-hooks              pre-commit install + pre-push"
	@echo "  make clean                      remove caches"
	@echo "  make cleanup                    tear down beaconry test stacks + remove leaked test images (BC-080)"

lint:
	$(RUFF) check .
	$(RUFF) format --check .

format:
	$(RUFF) format .
	$(RUFF) check --fix .

type:
	$(PYRIGHT)

test:
	$(PYTEST) $(PYTEST_ARGS)

verify-traceability:
	$(PYTHON) -m poc.scripts.verify_traceability

verify-dockerfile-health-copy:
	$(PYTHON) -m poc.scripts.verify_dockerfile_health_copy

verify-memory-drift:
	$(PYTHON) -m poc.scripts.verify_memory_drift

verify-compose-env-propagation:
	$(PYTHON) -m poc.scripts.verify_compose_env_propagation

# BC-141: every beaconry service in every compose file carries hard
# memory + CPU caps with swap disabled — prevents a future leak from
# taking down the host (as happened 2026-11-17).
verify-compose-memory-limits:
	$(PYTHON) -m poc.scripts.verify_compose_memory_limits

verify-docs-drift:
	$(PYTHON) -m poc.scripts.verify_docs_drift

# BC-147: docs/ three-folder layout lint.
# Refuses stray .md at docs/ top-level and specs placed outside docs/specs/.
verify-docs-layout:
	$(PYTHON) -m poc.scripts.verify_docs_layout

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

# BC-056: docs/specs/INSTALL.md drift lint — env vars, make targets, repo file
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

# BC-122: verify every scan iteration transferred and processed losslessly.
#   make verify-integrity                 # all iterations in the audit log
#   make verify-integrity ARGS="--since 24h"   # last 24h only
verify-integrity:
	$(PYTHON) -m poc.scripts.verify_integrity $(ARGS)

check: lint type test verify-per-file-coverage verify-traceability verify-dockerfile-health-copy verify-dockerfile-sources verify-pinned-versions verify-base-images verify-memory-drift verify-compose-env-propagation verify-compose-memory-limits verify-docs-drift verify-docs-layout verify-evidence-pointers verify-variables verify-install-doc verify-version-sync verify-changelog verify-feature-complete smoke
	@echo ""
	@echo "✓ Fast tier (make check) passed"

check-full: check
	@echo ""
	@echo "==> Full tier: running opt-in markers (compose + real_tools)"
	$(PYTEST) -m "compose or real_tools" --no-cov
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
	@echo "==> Full tier: dogfood-sbom scan (BC-096; BEACONRY_SKIP_DOGFOOD=1 to opt out)"
	$(MAKE) dogfood-sbom
	@echo ""
	@echo "==> Full tier: HTTP latency budget gate (BC-132; BEACONRY_SKIP_PERF=1 to opt out)"
	$(MAKE) perf
	@echo ""
	@echo "==> Full tier: cleanup (BC-080 — remove leaked test images)"
	$(MAKE) cleanup
	@echo ""
	@echo "✓ Full tier (make check-full) passed"

# BC-132: HTTP latency budget gate. Runs an in-process hub with a
# deterministic perf fixture, measures every endpoint, fails on P95
# breach. Opt-out: BEACONRY_SKIP_PERF=1 (dev-only emergency).
perf:
ifeq ($(BEACONRY_SKIP_PERF),1)
	@echo "↷ perf: skipped per BEACONRY_SKIP_PERF"
else
	@$(PYTHON) -m poc.scripts.perf_gate
endif

# BC-132: seed a perf fixture into $(OUT) (default: .beaconry-cache/perf).
# Operator can inspect the fixture state produced by the gate.
perf-seed:
	@$(PYTHON) -m poc.scripts.perf_seeder --out $(or $(OUT),.beaconry-cache/perf)

findings:
	@$(PYTHON) -m poc.scripts.findings $(if $(STATUS),--status $(STATUS)) $(if $(HOST),--host $(HOST)) $(if $(CVE),--cve $(CVE)) $(if $(SEVERITY),--severity $(SEVERITY))

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

# BC-096: dogfood grype rescan against each image's baked-in /sbom.json
# (frozen SBOM; no docker-socket mount). Complements dogfood-scan —
# same severity floor, same skip env. Separates "image regressed" from
# "grype DB learned a new CVE" by giving operators a deterministic
# frozen-SBOM lens.
dogfood-sbom:
	@$(PYTHON) -m poc.scripts.dogfood_sbom

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
	@echo "==> Rebuilding beaconry-dev-hub + beaconry-dev-scout from current source…"
	cd poc && docker compose up -d --build
	@echo "==> Waiting for /readyz on hub…"
	@until curl -sS http://localhost:8000/readyz >/dev/null 2>&1; do sleep 1; done
	@echo "==> Live stack now matches source. /healthz:"
	@docker exec beaconry-dev-hub python3 -c "import urllib.request,json; print(json.dumps(json.loads(urllib.request.urlopen('http://localhost:8000/healthz').read())['checks'], indent=2))" 2>&1 | sed 's/^/    /'

# Verdict: is the running stack up-to-date with current source? Prints FRESH
# or STALE and the delta in seconds. Checks hub + scout.
live-status:
	@for svc in hub scout; do \
	   CONTAINER="beaconry-dev-$$svc"; \
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

##@ Shepherd dev-cycle contract (SH-014 / SH-019)
#
# Spec: https://github.com/bboortz/shepherd/blob/main/docs/specs/dev-cycle-contract.md
#
# Dev-cycle doc (2026-11-22) prescription for beaconry: "add `make build`
# (image or no-op)". Done here as a no-op — beaconry is a Python service
# with no compiled artifact. No affected-only mechanism yet; SH-023 will
# add pytest-xdist + check-fast split.

.PHONY: build test-affected release-artifacts \
        ci-branch ci-patch ci-release ci-nightly ci-nightly-body

# Dev-cycle-doc prescription: `make build` was missing.
build: ; @true

# Contract entry points — anchor on beaconry's native idioms.
# BC-149 / SH-023: test-affected runs scripts/test-updates.sh which scopes
# pytest to tests covering changed modules (hub / scout / scripts /
# beaconry_health / telegram_mock / tests/). Falls back to full suite on
# tooling/config touches or >4 modules affected.
.PHONY: test-updates
test-affected: test-updates
test-updates: ; @bash scripts/test-updates.sh
# `smoke` already exists at the top of this Makefile; no override needed.
release-artifacts: ; @true

# Tier bodies.
# SH-055: BRANCH_STEPS sourced from the fleet-tier block below (see
# hook-push / PUSH_STEPS). Declared here with its default so the
# `ci-branch:` prereq expansion sees a non-empty value at parse time.
BRANCH_STEPS ?= lint test check-docs
ci-branch:  $(BRANCH_STEPS)      # 5-min PR gate: lint + test + check-docs
ci-patch:   smoke           # post-merge smoke

# ci-release-promote: fast-forward main to dev + tag from VERSION (SH-029).
# Must run on `dev` branch. Mutates the working tree; CI only.
# Mirrors shepherd's shared contract shape.
.PHONY: ci-release-promote
ci-release-promote:
	@current=$$(git branch --show-current); \
	 [ "$$current" = "dev" ] || { echo "ci-release-promote must run on 'dev', got '$$current'"; exit 1; }
	@[ -f VERSION ] || { echo "VERSION file missing — cannot determine tag"; exit 1; }
	@version=$$(cat VERSION | tr -d '[:space:]'); \
	 echo "SH-029 promote: dev → main as v$$version"; \
	 git fetch origin main && \
	 git checkout main && \
	 git merge --ff-only origin/dev && \
	 git tag "v$$version" HEAD && \
	 git push origin main && \
	 git push origin "v$$version" && \
	 git checkout dev

ci-release: ci-release-promote
	@echo "ci-release: beaconry's additional release flow (bump-* + CD pipeline) stays as-is"

ci-nightly: ci-nightly-body
ci-nightly-body: check      # nightly: full gate (lint + type + 15+ verify scripts + smoke)

# ---------------------------------------------------------------------------
# SH-042 — fleet-wide hook-commit / hook-push tier wrappers.
# See shepherd docs/plan/IDEAS-dev-workflow-contract-v3.md for the mapping.
# Budgets: hook-commit ~15 s, hook-push ~2 min.
#
# `check-fast` is an alias for beaconry's existing `check` target (which
# beaconry treats as its fast tier per BC-148 / its own echo on line 267).
# SH-023 will later split `check-fast` into a smaller subset (pytest-xdist
# + fast-only verify-*); when that lands, this alias becomes an override.
# ---------------------------------------------------------------------------
.PHONY: hook-commit hook-push check-fast
check-fast: check
COMMIT_STEPS ?= lint type
PUSH_STEPS   ?= check-fast check-docs verify-version-bumped verify-changelog verify-feature-complete
# BRANCH_STEPS is declared earlier (next to `ci-branch:`) so its default
# is visible when the rule's prereqs are expanded at parse time.
hook-commit: $(COMMIT_STEPS)
hook-push:   $(PUSH_STEPS)

# SH-055 — fleet-wide check-docs wrapper (shepherd ref: PR #58).
# Thin delegate over beaconry's existing doc/version drift gates. Wired into
# PUSH_STEPS + BRANCH_STEPS so stale docs are caught before a push lands and
# again in CI on PR.
check-docs: verify-docs-drift verify-docs-layout verify-version-sync verify-evidence-pointers verify-traceability verify-install-doc
