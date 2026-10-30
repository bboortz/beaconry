# Beaconry capabilities

Traceability matrix. Business capabilities first (what the operator / key user can rely on), then technical capabilities (how business ones are built). Every row links to origin story, design doc(s), and the tests that verify it. Reverse links live in each test module's top-of-file `Covers:` docstring.

Enforcement: `poc/scripts/verify_traceability.py` runs as part of `make check`. Any dangling link fails the gate.

**IDs are flat** (`CAP-NNN`). Type is a field. Section grouping is for readability.

**Status:**
- `active` — in production code, verified by ≥ 1 test
- `aspirational` — in a story that hasn't shipped yet; exempt from unverified check
- `deprecated` — kept for compat; exempt from unverified check

---

## Business capabilities

### CAP-B001: Operator previews the full pipeline with no external dependencies
- **type:** business
- **status:** active
- **origin:** BC-001
- **design:** DESIGN.md § Solving strategy, memory/workflow.md § Testing strategy
- **description:** Running `BEACONRY_MOCK=1 make demo` (or `make smoke`) walks the whole pipeline — scan, deliver, urgency decision, auto-fix path, operator-confirm path, verify — against fixture data. No network, no Docker daemon, no Telegram token required. Operator sees a 90-second narrated run that proves the design in-the-small.
- **verified by:**
  - `poc/tests/integration/test_BC_001_mocked_e2e.py`

### CAP-B002: Operator receives a real Telegram notification for a new CVE finding
- **type:** business
- **status:** active
- **origin:** BC-002
- **design:** DESIGN.md § Solving strategy, memory/telegram-patterns.md
- **description:** When `make demo` runs with Telegram credentials in `poc/.env`, scout generates an SBOM via real syft, hub runs real grype against the latest DB, and findings above `MIN_CVSS` are rendered as HTML Telegram messages. Each message carries CVE id, severity, CVSS, host, tier, and three inline buttons (Fix now, Snooze 4h, Accept risk). Deduplicated across runs and across packages that share a CVE id.
- **verified by:**
  - `poc/tests/integration/test_BC_002_real_pipeline.py`
  - `poc/tests/unit/test_demo_dispatch.py`

### CAP-B003: Operator can disable Fix-execution with a feature flag
- **type:** business
- **status:** active
- **origin:** BC-002 (default on in BC-009)
- **design:** memory/workflow.md
- **description:** `BEACONRY_FIX_ENABLED` controls whether tapping [🔧 Fix now] runs the real executor. Since BC-009 the default is `on`; operators can opt out with `BEACONRY_FIX_ENABLED=0`, in which case the button edits to "⚙️ Fix execution disabled (BEACONRY_FIX_ENABLED=0) — <cve> left as `open`" without touching Docker. Used for demos, dry-runs, and safety-conscious exploration.
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-B004: Operator is not flooded on noisy scans
- **type:** business
- **status:** active
- **origin:** BC-002
- **design:** docs/plan/IDEAS.md § Interactive behavior / UX
- **description:** The per-run cap `BEACONRY_MAX_FINDINGS_PER_RUN` (default 5) ranks findings by (tier, -cvss) and sends only the top-N individually; the remainder is summarised in a single digest message. CVE ids shared across packages are deduplicated, keeping the highest-CVSS variant. Applies to both the local-Python path (`run_real_pipeline_once`) and the Compose/HTTP path (`receive_scan`).
- **verified by:**
  - `poc/tests/unit/test_demo_dispatch.py`
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-B005: Operator can self-test the Telegram wiring without a scan
- **type:** business
- **status:** active
- **origin:** BC-002
- **description:** `make demo ARGS=--self-test` sends a single canary message ("beaconry is alive") and exits 0. Lets the operator verify bot token + chat id without triggering a real scan.
- **verified by:**
  - `poc/tests/unit/test_demo_dispatch.py`

### CAP-B009: Operator runs the full demo pipeline with Docker only (no Python toolchain)
- **type:** business
- **status:** active
- **origin:** BC-005
- **design:** DESIGN.md § Architecture, memory/docker-standard.md
- **description:** `make demo` boots the whole beaconry stack via `docker compose up --build` under `poc/`. Scout + hub run as containers; the host needs only Docker + Docker Compose. The local-Python path remains available via `BEACONRY_MOCK=1 make demo` for the dev inner loop.
- **verified by:**
  - `poc/tests/integration/test_BC_005_compose.py`
  - `poc/tests/unit/test_scout_filter.py`

### CAP-B010: Scout surveys the full Docker inventory with glob allow/deny filtering
- **type:** business
- **status:** active
- **origin:** BC-005
- **design:** DESIGN.md § Architecture
- **description:** By default, scout scans images matching `SCOUT_INCLUDE="nginx*,redis*,postgres*"` — a demo-safe allowlist that excludes dev/test clutter. Operators can broaden (`SCOUT_INCLUDE="*"`) or narrow further (`SCOUT_EXCLUDE="*-test,*-dev"`). Scout logs name every tag scanned and skipped so the operator understands scope.
- **verified by:**
  - `poc/tests/unit/test_scout_filter.py`

### CAP-B011: Operator trusts the gate — green `make check` means "smoke-ready"
- **type:** business
- **status:** active
- **origin:** BC-006
- **design:** memory/workflow.md § Blocking gates (must pass to merge to `dev`)
- **description:** Two documented tiers: `make check` (fast, ~7s, pre-push + merge-ready) runs lint + type + test + verify-traceability + smoke. `make check-full` (full, ~1–2m, pre-merge sanity + what CI will run) adds opt-in integration tests (compose + real_tools markers). Documented in README, CLAUDE.md, memory/workflow.md so operators know what each tier catches.
- **verified by:**
  - `poc/tests/integration/test_BC_006_gate_tiers.py`

### CAP-B012: Operator reviews current findings state without re-running the pipeline
- **type:** business
- **status:** active
- **origin:** BC-007
- **design:** DESIGN.md § Architecture
- **description:** `make findings` prints a formatted table from the dev SQLite DB — CVE, severity, CVSS, host, target image, package, status. Sorted by (status rank, tier, -cvss) so tier-0 KEV findings surface before tier-2 even if tier-2 has higher CVSS. `make findings STATUS=accepted` filters. Pure read, no network or Docker. Honors `DATA_DIR` env; README documents the one-liner to query the Compose named volume.
- **verified by:**
  - `poc/tests/integration/test_BC_007_findings_cli.py`

### CAP-B013: Developer trusts that `shipped` means every acceptance has evidence
- **type:** business
- **status:** active
- **origin:** BC-008
- **design:** memory/workflow.md
- **description:** DD-TGD gains step 6a "Pre-ship audit". Each plan doc carries a `Shipping evidence` table listing every Gherkin scenario + plan goal with a pointer (test name, log line, or dated manual verification). A story moves to `shipped` only when every row has non-empty evidence. Three audit lenses: Gherkin coverage, data-model symmetry, self-consistency.
- **verified by:**
  - `poc/tests/integration/test_BC_008_pre_ship_audit.py`

### CAP-B007: Operator snoozes/accepts findings and the system respects the decision across runs
- **type:** business
- **status:** active
- **origin:** BC-003
- **design:** memory/telegram-patterns.md
- **description:** Tapping [⏰ Snooze 4h] updates the finding's status in the dev DB to `snoozed`; tapping [✅ Accept risk] sets it to `accepted`. Subsequent `make demo` runs skip these findings via `save_finding`'s dedup (status != open ⇒ already in DB with final status). Automated via callback-data validation + idempotence (CAP-T028/T029); live-verified via the Phase 3 tap session in BC-003.
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-B008: Fix-now button is honest about its disabled state
- **type:** business
- **status:** active
- **origin:** BC-003 (superseded by CAP-B014 in BC-009)
- **design:** memory/telegram-patterns.md
- **description:** With `BEACONRY_FIX_ENABLED=0` (opt-out), tapping [🔧 Fix now] edits the message to "⚙️ Fix execution disabled (BEACONRY_FIX_ENABLED=0) — <cve> left as `open`". DB status unchanged. Operator never sees a silent no-op. Live-verified via the Phase 3 tap session in BC-003 (then as the default stub); still live by `BEACONRY_FIX_ENABLED=0 make demo`.
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-B014: Operator closes a CVE from Telegram — tap Fix, image pulled, re-scanned, verified
- **type:** business
- **status:** active
- **origin:** BC-009 (same-track pull refinement landed in BC-014)
- **design:** DESIGN.md § Remediation as a first-class citizen, memory/design-decisions.md § Auto-fix trust model
- **description:** Tapping [🔧 Fix now] on a Telegram notification runs the DockerAdapter end-to-end: tags `<base>:rollback` for safety, `docker pull <source_ref>` (same track as the running image — BC-014 preserves tag like `postgres:16-alpine` instead of silently jumping to `:latest`), re-scans the new image with syft + grype. Three terminal states, each a distinct message: `✅ Fixed and verified` (DB → `fixed`), `⚠️ Pulled, but CVE still present upstream` (DB stays `open`), `❌ Fix failed: <reason>` (DB stays `open`, rollback tag intact). Non-docker findings fall through to ManualAdapter and show "⚙️ Manual action required — <command>". Live-verified via Phase 3 tap session in BC-009 against nginx:1.24; BC-014 live-verified against postgres:16-alpine.
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-B015: Operator always knows if beaconry itself is working
- **type:** business
- **status:** active
- **origin:** BC-010
- **design:** memory/health-checks.md
- **description:** `make status` prints a one-line summary of every beaconry service's health, naming any degraded check explicitly. Operators who ran `docker compose up` and walked away can tell at a glance whether scanning is actually happening or something's quietly broken (grype DB missing, Telegram token revoked, scout stalled). Exit 0 all-healthy, non-zero otherwise. Sibling CLI to `make findings` (CAP-B012); both are read-only, docker-free views of live state. Future surfaces (web dashboard, Prometheus metrics) will also deliver this capability without needing a new business CAP.
- **verified by:**
  - `poc/tests/unit/test_status_cli.py`

### CAP-B016: Developer adds a new beaconry container's health surface in <10 lines
- **type:** business
- **status:** active
- **origin:** BC-012
- **design:** memory/health-checks.md
- **description:** The four-endpoint convention from BC-010 is now a dependency, not a reimplementation. New containers run `COPY beaconry_health/ ./beaconry_health/` in their Dockerfile and `api.include_router(build_router(my_checks))` in code — the Dockerfile lint rule catches forgotten COPY at merge time. Dropping the barrier to adding a container means BC-011's target-probe worker and future adapters (ansible, nix, k8s) can land without ~120 LOC of reinvented endpoint wiring.
- **verified by:**
  - `poc/tests/integration/test_BC_012_dockerfile_copy_lint.py`

### CAP-B017: Operator sees beaconry stack status at a glance in a Docker-compose-ps-like table
- **type:** business
- **status:** active
- **origin:** BC-013
- **design:** memory/health-checks.md
- **description:** `make status` output is a column-aligned table — NAME, STATE, HEALTH, LAST SCAN, DETAILS — with one row per beaconry container. Replaces BC-010's single-line `hub: healthy ... · scout: healthy ...` summary. Human phrasing for every state: "running / down / healthy / degraded"; raw Python exception class names (URLError, ConnectionError, Traceback) never appear in operator-facing output. Exit codes 0 (all healthy) / 1 (any degraded) / 2 (any down) so operators can script around it.
- **verified by:**
  - `poc/tests/unit/test_status_cli.py`

### CAP-B018: Operator confirms a fix actually brought the service back up
- **type:** business
- **status:** active
- **origin:** BC-011
- **design:** memory/design-decisions.md § Auto-fix trust model
- **description:** Promotes BC-009's "✅ Fixed and verified" from "CVE gone from the image file" to "CVE gone AND probe-confirmed service health". Operator sees a `🔍 Verify service health` button attached to the Fixed message when a probe policy exists for the image. After the operator restarts their container themselves, they tap the button; beaconry runs the configured probe (TCP connect, HTTP GET, or Docker HEALTHCHECK read) and emits one of three terminal messages: `✅ Service healthy — probe passed`, `⚠️ Probe failed: <reason>` with a rollback command, or `⏭️ No probe configured`. DB status transitions to `fixed_verified` on pass, `verification_failed` on probe failure. Closes the "beaconry can lie about fix success" gap (same-track pull in BC-014 reduced the risk; BC-011 is the explicit after-check).
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-B006: Operator has a clear development process to contribute
- **type:** business
- **status:** active
- **origin:** BC-000
- **design:** CLAUDE.md, memory/workflow.md
- **description:** Fresh clone + `make check` either passes or fails with named gate + fix hint. DD-TGD process is documented. Branch policy, PR flow, gate matrix, story template, 8-question clarification set — all discoverable from README + CLAUDE.md.
- **verified by:**
  - `poc/tests/unit/test_sanity.py`

---

## Technical capabilities

### CAP-T001: Finding ID is stable across runs
- **type:** technical
- **status:** active
- **origin:** BC-001
- **design:** memory/telegram-patterns.md § Finding deduplication
- **description:** `make_id(host, cve_id, source_ref, package)` returns a stable 16-char sha256 slice. Same inputs → same id. Different inputs → different ids. SQLite `INSERT OR IGNORE` uses this id as PK, so repeat runs don't re-notify.
- **supports:** CAP-B002, CAP-B004
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T002: CVSS extraction picks the highest score across entries
- **type:** technical
- **status:** active
- **origin:** BC-001
- **description:** grype can return multiple CVSS entries per vulnerability (NVD + vendor); `extract_cvss` picks the maximum.
- **supports:** CAP-B002, CAP-B004
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T003: Notification message is Telegram-HTML-safe for adversarial descriptions
- **type:** technical
- **status:** active
- **origin:** BC-002 (fix branch)
- **description:** `format_notification` renders in `parse_mode="HTML"` and html-escapes every user-controlled field. Raw `<`, `>`, `&` from CVE descriptions become entities; Markdown-ish chars (`[`, `_`, `*`, backtick) pass through unmolested.
- **supports:** CAP-B002
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T004: Urgency tier classification matches design-decisions.md
- **type:** technical
- **status:** active
- **origin:** BC-001
- **design:** memory/design-decisions.md § Urgency tiers
- **description:** `classify_tier(finding, kev, internet_facing)` returns 0 for KEV, 1 for CVSS≥9 + internet-facing, 2 for CVSS 7–8 or LAN-only, 3 for CVSS<7.
- **supports:** CAP-B002
- **verified by:**
  - `poc/tests/unit/test_urgency.py`

### CAP-T005: Safe-class auto-fix rule (KEV + container only)
- **type:** technical
- **status:** active
- **origin:** BC-001
- **design:** memory/design-decisions.md § Auto-fix trust model
- **description:** `is_safe_class(finding, kev)` returns True only when KEV + source_type=="docker" (rollback tag is reversible). Everything else requires operator confirmation.
- **supports:** CAP-B003
- **verified by:**
  - `poc/tests/unit/test_urgency.py`

### CAP-T006: Executor dispatcher picks the first matching adapter
- **type:** technical
- **status:** active
- **origin:** BC-001
- **design:** DESIGN.md § Remediation as a first-class citizen
- **description:** `can_handle(finding)` filters adapters; `next()` picks the first match. DockerAdapter claims `source_type="docker"`; ManualAdapter is the universal fallback.
- **supports:** CAP-B002
- **verified by:**
  - `poc/tests/unit/test_executor_dispatch.py`

### CAP-T007: DockerAdapter in MOCK mode spawns no subprocess
- **type:** technical
- **status:** active
- **origin:** BC-001
- **description:** With `BEACONRY_MOCK=1`, `DockerAdapter.execute` prints `[mock]` lines and returns a success result without touching `subprocess.run` or `asyncio.create_subprocess_shell`.
- **supports:** CAP-B001
- **verified by:**
  - `poc/tests/unit/test_executor_dispatch.py`

### CAP-T008: Hub persists findings to SQLite with idempotent inserts
- **type:** technical
- **status:** active
- **origin:** BC-001
- **description:** `save_finding` uses `INSERT OR IGNORE` keyed by finding id, so duplicate findings return False without raising. `load_finding` and `set_status` round-trip the row.
- **supports:** CAP-B002, CAP-B004
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T009: Demo dispatcher chooses mock vs real based on env
- **type:** technical
- **status:** active
- **origin:** BC-002
- **description:** `choose_mode(env)` returns "mock" when `BEACONRY_MOCK=1`, "real" when `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` are set, and raises `MissingConfigError` otherwise. Error message points to `poc/.env.example` and the `BEACONRY_MOCK=1 make demo` fallback.
- **supports:** CAP-B002, CAP-B001
- **verified by:**
  - `poc/tests/unit/test_demo_dispatch.py`

### CAP-T010: Prereq check surfaces missing syft/grype/image
- **type:** technical
- **status:** active
- **origin:** BC-002
- **description:** `check_prereqs(image)` returns a non-empty list naming each missing dependency; empty list means ready. Called before the pipeline starts in real mode.
- **supports:** CAP-B002
- **verified by:**
  - `poc/tests/unit/test_demo_dispatch.py`

### CAP-T011: Real syft errors are surfaced
- **type:** technical
- **status:** active
- **origin:** BC-002
- **description:** `_run_syft` raises `RuntimeError("syft failed: ...")` on non-zero exit, carrying up to 200 chars of stderr. Caller aborts the pipeline instead of silently continuing.
- **supports:** CAP-B002
- **verified by:**
  - `poc/tests/unit/test_demo_dispatch.py`

### CAP-T012: Build-findings dedupes CVEs across packages
- **type:** technical
- **status:** active
- **origin:** BC-002 (fix branch)
- **description:** `_build_findings_from_matches` groups by cve_id and keeps the highest-CVSS variant. openssl + libssl1.1 sharing CVE-X produce one Finding, not two.
- **supports:** CAP-B004
- **verified by:**
  - `poc/tests/unit/test_demo_dispatch.py`

### CAP-T013: Build-findings filters below MIN_CVSS
- **type:** technical
- **status:** active
- **origin:** BC-002
- **description:** Any grype match whose CVSS falls below `MIN_CVSS` (default 7.0; real-mode demo default 9.0) is excluded from the Finding list before dedup.
- **supports:** CAP-B004
- **verified by:**
  - `poc/tests/unit/test_demo_dispatch.py`

### CAP-T014: Pipeline survives one failing notification
- **type:** technical
- **status:** active
- **origin:** BC-002 (fix branch)
- **description:** `run_real_pipeline_once` wraps each `hub.notify` call in try/except. A failing notify is logged with CVE id + package and the loop continues with the next finding.
- **supports:** CAP-B002
- **verified by:**
  - `poc/tests/unit/test_demo_dispatch.py`

### CAP-T015: Per-run cap caps individual sends and triggers a digest
- **type:** technical
- **status:** active
- **origin:** BC-002 (fix branch)
- **description:** With `BEACONRY_MAX_FINDINGS_PER_RUN=5`, 20 unique CVEs → 5 individual messages + 1 HTML digest message naming the remaining 15 (first 10 as bullets, plus "N more" if any). Shared helper (`dedup_and_cap`) now used by both demo and HTTP /scan paths.
- **supports:** CAP-B004
- **verified by:**
  - `poc/tests/unit/test_demo_dispatch.py`
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T018: Scout filters image tags by include/exclude glob patterns
- **type:** technical
- **status:** active
- **origin:** BC-005
- **design:** DESIGN.md § Architecture
- **description:** `filter_image_tags(tags, include_globs, exclude_globs)` uses `fnmatch.fnmatchcase` so include = "any match" and exclude always overrides include. `parse_globs(env)` splits a comma-separated glob list tolerant of whitespace.
- **supports:** CAP-B010
- **verified by:**
  - `poc/tests/unit/test_scout_filter.py`

### CAP-T021: Hub /scan endpoint applies dedup + cap across the full inventory
- **type:** technical
- **status:** active
- **origin:** BC-005 (fix branch)
- **design:** memory/workflow.md
- **description:** `receive_scan` collects candidate Findings across all SBOMs in the request, then calls `dedup_and_cap(candidates, BEACONRY_MAX_FINDINGS_PER_RUN)` to drop duplicates (by CVE id, keep highest CVSS) and keep only the top-N by (tier, -cvss). Suppressed Findings go to `send_digest`. Previously each SBOM's findings were notified independently, flooding the operator with ~100 messages on a multi-image Compose run.
- **supports:** CAP-B004, CAP-B009
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T019: Compose stack brings up hub + scout with :ro socket mount, healthchecks, env propagation
- **type:** technical
- **status:** active
- **origin:** BC-005
- **design:** memory/docker-standard.md
- **description:** `poc/docker-compose.yml` declares hub + scout services. Hub owns a healthcheck via `/health`, mounts the Docker socket rw (for pulls), and persists the grype DB in the `beaconry-data` named volume. Scout mounts the Docker socket `:ro` and only reads inventory. BC-005 env vars (`SCOUT_INCLUDE`, `SCOUT_EXCLUDE`, `BEACONRY_MAX_FINDINGS_PER_RUN`, `MIN_CVSS`) propagate via `.env` substitution. /health endpoint responds with `{"status":"ok"}` (asserted via FastAPI TestClient in unit tier, via healthcheck config in compose tier).
- **supports:** CAP-B009
- **verified by:**
  - `poc/tests/integration/test_BC_005_compose.py`
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T016: verify-traceability enforces bidirectional links
- **type:** technical
- **status:** active
- **origin:** BC-004
- **description:** `poc/scripts/verify_traceability.py` parses `docs/CAPABILITIES.md` and `poc/tests/**/test_*.py` and errors on: missing Covers block, dangling CAP id, missing test file, unverified active CAP, bidirectional inconsistency. Exits 0 only when the matrix is clean.
- **supports:** CAP-B006
- **verified by:**
  - `poc/tests/integration/test_BC_004_traceability.py`

### CAP-T020: Hub import suppresses noisy HTTP client loggers so bot tokens don't leak
- **type:** technical
- **status:** active
- **origin:** BC-005 (fix branch)
- **description:** On module import, `poc/hub/hub.py` raises `httpx`, `httpcore`, `telegram`, and `telegram.ext` loggers to WARNING. python-telegram-bot's httpx client otherwise logs full request URLs — including the bot token path — at INFO. Covers both the local-Python demo path (poc/demo.py also sets this) and the container runtime path (`docker compose up`) where hub.py is the entry point.
- **supports:** CAP-B002, CAP-B009
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T023: `make check` includes smoke
- **type:** technical
- **status:** active
- **origin:** BC-006
- **design:** memory/workflow.md § Blocking gates (must pass to merge to `dev`)
- **description:** The fast-tier `check` target declares `smoke` as a dependency. If `make smoke` (the mocked BC-001 pipeline) is broken, `make check` fails — closing the loophole that let BC-005's smoke regression ship.
- **supports:** CAP-B011
- **verified by:**
  - `poc/tests/integration/test_BC_006_gate_tiers.py`

### CAP-T024: `make check-full` includes opt-in markers
- **type:** technical
- **status:** active
- **origin:** BC-006
- **design:** memory/workflow.md § Blocking gates (must pass to merge to `dev`)
- **description:** The full-tier `check-full` target depends on `check`, then invokes `pytest -m "compose or real_tools"`. The default pytest addopts exclude both markers, so `check` doesn't run them; `check-full` explicitly selects them. Operators without docker get `compose`-marked tests skipped (not failed); operators without syft/grype get `real_tools`-marked tests skipped.
- **supports:** CAP-B011
- **verified by:**
  - `poc/tests/integration/test_BC_006_gate_tiers.py`

### CAP-T025: Findings reporter reads SQLite, formats a table, honors STATUS filter
- **type:** technical
- **status:** active
- **origin:** BC-007
- **design:** DESIGN.md § Architecture
- **description:** `poc/scripts/findings.py` opens the dev DB in read-only mode (`?mode=ro` SQLite URI), introspects the schema (`PRAGMA table_info`) to decide whether the `tier` column is present, queries findings (optionally filtered by `--status`), sorts by (status rank, tier, -cvss) when tier is available or falls back to (status rank, -cvss) on legacy DBs, and prints via `format_table` — a pure helper that pads/truncates each column with "…". No side effects on the DB file (mtime unchanged after invocation). Exit always 0. Migration to the tier column is handled by `hub.init_db` (idempotent `ALTER TABLE`) the next time the hub starts.
- **supports:** CAP-B012
- **verified by:**
  - `poc/tests/integration/test_BC_007_findings_cli.py`
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T026: Plan template carries a Shipping evidence section used as the pre-ship checklist
- **type:** technical
- **status:** active
- **origin:** BC-008
- **design:** memory/workflow.md
- **description:** `docs/plan/_template.md` includes a `## Shipping evidence` section with a 4-column table (Scenario, Evidence type, Pointer, Status) and a legend. New plan docs inherit this. The pre-ship audit (DD-TGD step 6a) is the discipline of filling every row before marking the story `shipped`. Template-based for now; automated enforcement (parsing Gherkin against the table) is a future follow-up if drift reappears.
- **supports:** CAP-B013
- **verified by:**
  - `poc/tests/integration/test_BC_008_pre_ship_audit.py`

### CAP-T027: Shipped stories must have a completed Shipping evidence table
- **type:** technical
- **status:** active
- **origin:** BC-008 (fix branch automate-audit)
- **design:** memory/workflow.md
- **description:** verify-traceability parses `docs/plan/<slug>-plan.md` for the `## Shipping evidence` section when the matching `docs/stories/<slug>.md` is `status: shipped`. Fails the gate on: template placeholder text remaining (`<copy a` / `<test name` substrings), rows with non-✓ status (⚠️ / ❌), empty table. Draft / in-progress stories are not checked. Plans without the section are grandfathered (pre-BC-008). Also emits a stderr WARNING (advisory, not failure) when the story's Gherkin has more `Scenario:` lines than the evidence table has rows — a hint that scenarios may be missing (though one row can legitimately roll up multiple scenarios). Closes the discipline gap from BC-008 Option A.
- **supports:** CAP-B013
- **verified by:**
  - `poc/tests/integration/test_BC_004_traceability.py`

### CAP-T028: Callback_data validation rejects malformed input
- **type:** technical
- **status:** active
- **origin:** BC-003
- **design:** memory/telegram-patterns.md
- **description:** `on_button_click` compares `query.data` against `^(fix|snooze|ack):[a-f0-9]{16}(?::\d+)?$` before any DB access. Non-matching callbacks (crafted payloads, future-format drift) get an "⚠️ Invalid request — malformed callback data." edit, a warning in logs, and no DB mutation.
- **supports:** CAP-B007, CAP-B008
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T029: Button handlers are idempotent on double-tap
- **type:** technical
- **status:** active
- **origin:** BC-003
- **design:** memory/telegram-patterns.md
- **description:** For Snooze and Accept, `on_button_click` queries the current DB status via `get_status` and, if it already matches the intent, edits the message to "ℹ️ Already <status> — <cve>" and returns without re-writing. Prevents redundant SQL updates and matches Telegram's expectation that `edit_message_text` with identical content fails; the "already" variant is distinct from the first-tap edit.
- **supports:** CAP-B007
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T022: Fix/feat branches without a plan doc fail the gate
- **type:** technical
- **status:** active
- **origin:** BC-004 (fix branch enforce-plan-docs)
- **design:** memory/code-quality.md
- **description:** When `verify-traceability` runs on a branch whose name matches `fix/<slug>` or `feat/<slug>`, the script requires `docs/plan/<slug>-plan.md` to exist and errors (naming the branch + expected path + remediation hint) if it doesn't. Other branch types (`main`, `dev`, `chore/*`, `docs/*`, `refactor/*`) and detached HEAD are no-ops. Closes a long-standing slip where fix branches shipped without plan docs because the rule had no automated teeth.
- **supports:** CAP-B006
- **verified by:**
  - `poc/tests/integration/test_BC_004_traceability.py`

### CAP-T030: DockerAdapter.execute pulls same-track tag with a :rollback safety tag before pulling
- **type:** technical
- **status:** active
- **origin:** BC-001 (registered in BC-009, same-track behavior landed in BC-014)
- **design:** memory/design-decisions.md § Auto-fix trust model
- **description:** `DockerAdapter.execute` runs `docker tag <source_ref> <base>:rollback` immediately before `docker pull <source_ref>` (same tag — BC-014 preserves the operator's track). Rollback tag is zero-cost (label on existing image id). Operator can restore manually via `docker tag <base>:rollback <source_ref>` if the new image misbehaves. Tag-failure is logged non-fatal so the pull still proceeds; pull-failure returns `success=False` and leaves the image unchanged. Pre-BC-014 behavior hardcoded `:latest` — see CAP-T046 for the migration.
- **supports:** CAP-B014
- **verified by:**
  - `poc/tests/unit/test_executor_dispatch.py`

### CAP-T031: verify_fix re-runs syft + grype on the new image and detects CVE persistence
- **type:** technical
- **status:** active
- **origin:** BC-001 (registered in BC-009)
- **design:** DESIGN.md § Solving strategy
- **description:** After a successful DockerAdapter pull, `verify_fix(finding)` runs real `syft <base>:latest -o syft-json` on the newly pulled image and pipes the SBOM to `run_grype`. If grype still returns a match for the same (CVE id, package) pair the function returns `False` (upstream not patched); if the match is absent it returns `True`. Non-docker findings and MOCK mode short-circuit to `True`. The handler maps False → "⚠️ Pulled, but CVE still present upstream" (DB stays open), True → "✅ Fixed and verified" (DB → fixed).
- **supports:** CAP-B014
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T032: Fix flow distinguishes pull-failed from pull-OK-but-upstream-still-vulnerable
- **type:** technical
- **status:** active
- **origin:** BC-009
- **design:** memory/design-decisions.md § Auto-fix trust model
- **description:** `on_button_click`'s fix branch emits three distinct terminal messages for three distinct outcomes: `❌ Fix failed: <reason>` when the DockerAdapter returns `success=False` (pull errored, image unchanged, verify_fix is NOT called); `⚠️ Pulled, but CVE still present upstream` when pull succeeded but `verify_fix` returned False (DB status stays `open`); `✅ Fixed and verified` when pull succeeded and verify_fix returned True (DB status flips to `fixed`). Operator can tell beaconry-bug from upstream-not-patched at a glance.
- **supports:** CAP-B014
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T034: Hub container image ships a docker CLI so DockerAdapter.execute works on the Compose path
- **type:** technical
- **status:** active
- **origin:** BC-009 (Phase 3 live gap)
- **design:** memory/docker-standard.md
- **description:** `poc/hub/Dockerfile` installs a pinned static `docker` CLI (currently 27.5.1) at `/usr/local/bin/docker` so `DockerAdapter.execute` can run `docker tag` / `docker pull` shell subprocesses against the host daemon via the mounted socket. Without this, taps on 🔧 Fix now in the Compose path would always fail with "docker: not found". Guarded by `test_hub_image_bundles_docker_cli` (compose-marker tier).
- **supports:** CAP-B014, CAP-B009
- **verified by:**
  - `poc/tests/integration/test_BC_005_compose.py`

### CAP-T033: Operator can opt out of fix execution via BEACONRY_FIX_ENABLED=0
- **type:** technical
- **status:** active
- **origin:** BC-009
- **design:** memory/workflow.md
- **description:** `hub.py` reads `BEACONRY_FIX_ENABLED` with default `"1"`; only an explicit `"0"` turns it off. When off, the fix branch short-circuits to "⚙️ Fix execution disabled (BEACONRY_FIX_ENABLED=0)" without invoking any executor. Preserves the pre-BC-009 stub behavior for demo and safety-conscious operators, and gives developers a quick toggle for local experiments.
- **supports:** CAP-B003, CAP-B014
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T035: Hub implements the four-endpoint health convention
- **type:** technical
- **status:** active
- **origin:** BC-010
- **design:** memory/health-checks.md
- **description:** Hub exposes `/livez`, `/readyz`, `/startupz`, `/healthz` per the k8s-origin convention adopted BC-010. `/livez`: no external deps, trivially 200. `/readyz`: DB writable + grype binary present + grype DB directory populated; 503 with `{"failed":[...]}` body naming the failing check. `/startupz`: 503 before lifespan finishes grype DB update, 200 after. `/healthz`: side-effect-free JSON `{status, version, checks}` always 200, with `status: degraded` when any sub-check fails. `/health` kept as transitional alias to `/readyz`.
- **supports:** CAP-B015, CAP-B009
- **verified by:**
  - `poc/tests/unit/test_hub_health.py`

### CAP-T036: Scout implements the four-endpoint health convention via file-sentinels (superseded in BC-013)
- **type:** technical
- **status:** active
- **origin:** BC-010 (superseded for scout in BC-013)
- **design:** memory/health-checks.md
- **description:** `beaconry_health.sentinels.SentinelWriter` provides the file-sentinel implementation of the four-indicator convention: `scout.live` (mtime-liveness, bumped every loop), `scout.ready` (one-shot after local prereqs succeed), `scout.started` (one-shot after first outbound work completes), and `scout.healthz.json` (per-loop summary). **Scout migrated to HTTP endpoints in BC-013**; `SentinelWriter` remains available for future HTTP-less loop services. Unit tests exercise the writer class directly (behavior-preservation harness).
- **supports:** CAP-B015, CAP-B009
- **verified by:**
  - `poc/tests/unit/test_scout_health.py`

### CAP-T037: Compose startup ordering uses depends_on: service_healthy
- **type:** technical
- **status:** active
- **origin:** BC-010 (wiring existed pre-BC-010; health signal becomes meaningful here)
- **design:** memory/health-checks.md § `depends_on: service_healthy` — compose startup ordering
- **description:** `poc/docker-compose.yml` declares `scout.depends_on.hub.condition: service_healthy` so scout stays in `created` state until hub's `/readyz`-backed healthcheck reports healthy. Combined with CAP-T035 (hub `/readyz` reflects real prereqs, not blind 200), this eliminates the race where scout's first `/scan` POST hit a hub whose grype DB was still downloading. Measured cold-start delta: ~60s on first run (grype DB download), <5s on warm restart.
- **supports:** CAP-B015, CAP-B009
- **verified by:**
  - `poc/tests/integration/test_BC_010_compose_health_chain.py`

### CAP-T038: Hub /livez and /readyz survive degraded external dependencies without restart-looping
- **type:** technical
- **status:** active
- **origin:** BC-010
- **design:** memory/health-checks.md § The restart-loop trap (do not step in it)
- **description:** The classic healthcheck trap: embed DB / Telegram / external deps in liveness → external failure → container restart loop (restart doesn't fix the DB). Explicitly tested: `/livez` returns 200 when `sqlite3.connect` raises `OSError`, when `tg_app` is `None`, when any shell binary is missing. `/readyz` tolerates `tg_app = None` (Telegram failure is degradation, surfaces in `/healthz`, not readiness). Compose healthcheck targets `/readyz`, so Telegram token revocation cannot trigger a container restart.
- **supports:** CAP-T035, CAP-B015
- **verified by:**
  - `poc/tests/unit/test_hub_health.py`

### CAP-T039: `make status` prints an operator-readable summary of all beaconry services
- **type:** technical
- **status:** active
- **origin:** BC-010
- **design:** memory/health-checks.md
- **description:** `poc/scripts/status.py` fetches hub `/healthz` via `urllib.request` (localhost:8000 for local dev, override via `BEACONRY_HUB_HEALTHZ_URL`), reads scout's `scout.healthz.json` from the shared `/data` volume (located via `docker volume inspect` for the Compose path), formats one line per service (`hub: healthy (grype_db ok, telegram polling, last scan 2m ago) · scout: healthy (last scan 2m ago, 11 images)`), and exits 0 all-healthy, non-zero on any degradation. Sibling to CAP-T025 (`make findings`); both are read-only CLIs over the live Compose stack. New Makefile target added to `.PHONY`.
- **supports:** CAP-B015
- **verified by:**
  - `poc/tests/unit/test_status_cli.py`

### CAP-T040: Shared `beaconry_health/` package provides FastAPI router factory, sentinel writer, and reusable probes
- **type:** technical
- **status:** active
- **origin:** BC-012
- **design:** memory/health-checks.md
- **description:** `poc/beaconry_health/` ships four modules: `endpoints.py` (`build_router(livez_provider, readyz_providers, startup_flag_getter, healthz_summary_getter) -> APIRouter`), `sentinels.py` (`SentinelWriter` class for loop-only services), `checks.py` (reusable probes `check_db`, `check_binary`, `check_dir_populated`), and `trap.py` (typed `LivezProvider` / `ReadyzProvider` aliases). Hub consumes `build_router` directly; scout consumes `SentinelWriter`. All BC-010 behavior preserved — `test_hub_health.py` and `test_scout_health.py` pass unchanged after the migration (behavior-preservation evidence).
- **supports:** CAP-B016, CAP-T035, CAP-T036
- **verified by:**
  - `poc/tests/unit/test_beaconry_health_endpoints.py`
  - `poc/tests/unit/test_beaconry_health_sentinels.py`
  - `poc/tests/unit/test_beaconry_health_checks.py`

### CAP-T041: Typed `LivezProvider` / `ReadyzProvider` interfaces make the restart-loop trap a type error
- **type:** technical
- **status:** active
- **origin:** BC-012
- **design:** memory/health-checks.md § The restart-loop trap (do not step in it)
- **description:** `beaconry_health.trap` defines `LivezProvider = Callable[[], Literal["alive"]]` and `ReadyzProvider = Callable[[], dict[str, str]]` — incompatible return types. `build_router` accepts them as keyword-only typed arguments. A caller who wires a readyz-shaped provider to the `/livez` slot (reopening the restart-loop trap) gets a pyright error at import time; the trap is syntactically unreachable, not just "documented but trusted". Enforcement is **actually asserted** by a pyright integration test that writes a misuse fixture and invokes pyright on it (added in post-ship fix branch — the original runtime shadow only caught the trivial case of aliases collapsing to the same object).
- **supports:** CAP-T038, CAP-T040
- **verified by:**
  - `poc/tests/unit/test_beaconry_health_trap.py`
  - `poc/tests/integration/test_BC_012_trap_pyright_enforcement.py`

### CAP-T042: Dockerfile lint catches forgotten `COPY beaconry_health/` at merge time
- **type:** technical
- **status:** active
- **origin:** BC-012
- **design:** memory/health-checks.md § Checklist — adding a new beaconry container
- **description:** `poc/scripts/verify_dockerfile_health_copy.py` walks each service directory under `poc/`, detects whether any `.py` file imports from `beaconry_health`, and asserts the matching Dockerfile has a `COPY beaconry_health/` line. Reported via a named error pointing at the Dockerfile and suggesting the fix. Hooked into `make check` so forgetting the COPY fails the gate before merge rather than failing `docker compose up` at operator-launch time (which would be hard to diagnose from an ImportError traceback).
- **supports:** CAP-B016, CAP-B011
- **verified by:**
  - `poc/tests/integration/test_BC_012_dockerfile_copy_lint.py`

### CAP-T043: Scout serves /livez /readyz /startupz /healthz via FastAPI + beaconry_health.build_router
- **type:** technical
- **status:** active
- **origin:** BC-013
- **design:** memory/health-checks.md
- **description:** `poc/scout/scout.py` runs a uvicorn-hosted FastAPI app on port 8001 with the four endpoints wired via `beaconry_health.build_router`. Scan loop runs as an asyncio background task via `asyncio.to_thread(collect_and_post)`, keeping the HTTP event loop responsive during long syft/grype scans. Scout's `/livez` honors the restart-loop trap: a dead Docker socket surfaces in `/healthz` as degradation but does not fail `/livez` or `/readyz`-trigger a restart loop. Supersedes BC-010's file-sentinel approach for scout; `SentinelWriter` stays in `beaconry_health` for HTTP-less loop services.
- **supports:** CAP-B015, CAP-B017, CAP-T035, CAP-T038
- **verified by:**
  - `poc/tests/unit/test_scout_http_health.py`

### CAP-T044: `make status` queries both services uniformly via HTTP; no docker exec special case
- **type:** technical
- **status:** active
- **origin:** BC-013
- **design:** memory/health-checks.md
- **description:** `poc/scripts/status.py` is now a single code path: `fetch_healthz(url)` + `gather_service_status` for each service. The BC-010 `docker exec beaconry-scout cat scout.healthz.json` fallback is deleted. `SCOUT_LIVE_MAX_AGE_S`, `SCOUT_DATA_DIR`, and `SCOUT_CONTAINER` env knobs removed — no longer needed because scout answers live via HTTP. Test `test_no_docker_exec_called` monkeypatches `subprocess.run` and asserts no `docker exec` invocation during a status run.
- **supports:** CAP-B015, CAP-B017
- **verified by:**
  - `poc/tests/unit/test_status_cli.py`

### CAP-T045: `make status` output follows a column format with human-readable error phrasing
- **type:** technical
- **status:** active
- **origin:** BC-013
- **design:** memory/health-checks.md
- **description:** Output is five columns (NAME, STATE, HEALTH, LAST SCAN, DETAILS) with left-aligned headers and dynamic widths. All error cases produce human DETAILS strings — "compose stack not up — try `cd poc && docker compose up -d`" when both services unreachable; "container not up" for partial-up; "degraded: grype_db missing" for degraded. Raw Python exception class names (URLError, ConnectionError, Traceback) never appear — tested explicitly via `test_output_has_no_raw_exception_names`.
- **supports:** CAP-B017
- **verified by:**
  - `poc/tests/unit/test_status_cli.py`

### CAP-T046: DockerAdapter preserves the source_ref tag on pull (no silent :latest jump)
- **type:** technical
- **status:** active
- **origin:** BC-014
- **design:** memory/design-decisions.md § Auto-fix trust model
- **description:** `DockerAdapter.describe` and `DockerAdapter.execute` resolve the pull target to the exact `source_ref` the operator runs: `postgres:16-alpine` → `docker pull postgres:16-alpine` (not `postgres:latest`). Fluid tags still pick up upstream patch updates via Docker Hub tag-mutability (upstream mutates `:16-alpine` → new image id, local re-pull picks it up). Pinned tags stay pinned (`postgres:16.1.3-alpine` doesn't change on tap-Fix) — operator decides when to bump majors. Edge case: `source_ref` without a tag at all (`nginx`) falls back to `:latest` to match Docker's own default. Rollback-tag mechanism unchanged (`<base>:rollback`). Replaces BC-009's hardcoded `:latest` which could silently jump a postgres from 16 → 18.
- **supports:** CAP-B014, CAP-T030
- **verified by:**
  - `poc/tests/unit/test_executor_dispatch.py`

### CAP-T047: `make verify-memory-drift` catches regressions of shipped documentation-reality changes
- **type:** technical
- **status:** active
- **origin:** BC-015
- **design:** memory/workflow.md § Blocking gates (must pass to merge to `dev`)
- **description:** `poc/scripts/verify_memory_drift.py` reads a growing TOML of banned regex patterns (`poc/scripts/memory_drift_bans.toml`) and scans every `memory/*.md` file for recurrences. Each entry carries `pattern`, `banned_since` (BC id), `reason`, and optional `exception_regex` list for legitimate historical mentions. Seeded with BC-013 (scout-migrated-to-HTTP) + BC-014 (DockerAdapter same-track) drifts. Hooked into `make check` fast tier. Append-only — patterns document historical facts; if an entry becomes over-broad the fix is to add `exception_regex`, not to delete the pattern. Pre-ship audit (DD-TGD step 6a) checklist grows a required row: *if the story deprecates documented behavior, add a `memory_drift_bans.toml` entry in this commit*. Live-proven by Phase 4 planted-regression (git-reverted BC-014's memory close, confirmed the script caught both matching lines with named errors, restored).
- **supports:** CAP-B013, CAP-T026, CAP-T027
- **verified by:**
  - `poc/tests/unit/test_memory_drift_guard.py`

### CAP-T048: Policy-driven probe resolver (BEACONRY_HEALTH_PROBES env var)
- **type:** technical
- **status:** active
- **origin:** BC-011
- **design:** memory/design-decisions.md § Auto-fix trust model
- **description:** `poc/hub/waterfall.py::parse_policies(env)` parses `BEACONRY_HEALTH_PROBES` at hub startup, raising `MalformedPolicyError` on any bad entry so the operator sees the problem before a scan arrives. Syntax: `<image_ref>=<probe_type>:<probe_arg>;...`. `resolve_probe(image_ref, policies)` returns the matching `Probe` or `None`. Resolver drives both the button-attachment decision in notifications and the dispatch in the verify callback.
- **supports:** CAP-B018
- **verified by:**
  - `poc/tests/unit/test_waterfall.py`

### CAP-T049: Three probe types — docker_healthcheck, tcp, http
- **type:** technical
- **status:** active
- **origin:** BC-011
- **design:** memory/design-decisions.md § Auto-fix trust model
- **description:** `waterfall.run_probe(probe)` dispatches to one of three implementations: `_probe_tcp` (asyncio `open_connection` to `<host>:<port>`), `_probe_http` (`urllib.request.urlopen` wrapped in `asyncio.to_thread`, 2xx-3xx pass), `_probe_docker_healthcheck` (`docker.from_env().containers.get(name).attrs['State']['Health']['Status']`). Each returns a `ProbeResult(ok, reason)` with the reason string surfaced in the operator-facing Telegram message. Timeouts: 5s per probe. Levels 3 (systemd stability) + 4 (custom command) from design-decisions.md are deferred to a future story.
- **supports:** CAP-B018, CAP-T048
- **verified by:**
  - `poc/tests/unit/test_waterfall.py`

### CAP-T050: New DB statuses `fixed_verified` + `verification_failed`
- **type:** technical
- **status:** active
- **origin:** BC-011
- **description:** The findings table's `status` column (TEXT, no CHECK constraint) accepts two new values set by `on_button_click`'s verify action: `fixed_verified` after a successful probe, `verification_failed` after a failure. No schema migration required — the TEXT column already permits any value, mirroring the BC-003 pattern. `make findings` display logic sorts them alongside the existing BC-007 status rank; the `--status` filter accepts them unchanged.
- **supports:** CAP-B018, CAP-B012
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T051: Compose env propagation lint catches env vars read by code but missing from docker-compose.yml
- **type:** technical
- **status:** active
- **origin:** BC-011 (fix branch BC-011-env-propagation)
- **design:** memory/workflow.md § Blocking gates (must pass to merge to `dev`)
- **description:** `poc/scripts/verify_compose_env_propagation.py` scans every `.py` file under `poc/hub/` and `poc/scout/` for `os.environ.get("BEACONRY_X")` reads, then checks that `BEACONRY_X` appears in that service's `environment:` block of `poc/docker-compose.yml`. Fails the gate with a named error + a copy-pasteable `- BEACONRY_X=${BEACONRY_X:-}` hint. Closes the class of bug where BC-009 shipped `BEACONRY_FIX_ENABLED` and BC-011 shipped `BEACONRY_HEALTH_PROBES` but neither added the corresponding compose propagation entry — hub booted silently with empty policies, operator saw no effect from `.env` changes, nothing failed until live tap session. Hooked into `make check` fast tier.
- **supports:** CAP-B011, CAP-B018
- **verified by:**
  - `poc/tests/unit/test_compose_env_propagation.py`

### CAP-T052: `make findings` auto-routes to the running Compose hub's volume DB
- **type:** technical
- **status:** active
- **origin:** BC-007 close-gap (post-BC-011 live tap)
- **description:** `poc/scripts/findings.py` detects whether a `beaconry-hub` container is running via `docker inspect`. If yes, routes the query through `docker exec beaconry-hub python3 -c '<inline-script>'` against `/data/beaconry.db` so operators see the live Compose-volume state instead of their stale `.beaconry-cache/beaconry.db` local-dev file. Mirrors BC-013's `make status` compose awareness. Operator override: `BEACONRY_FINDINGS_LOCAL=1 make findings` forces the local path. Closes the gap surfaced during BC-011's live tap session: operator tapped the Verify button, DB flipped to `fixed_verified` in the volume, but `make findings STATUS=fixed_verified` returned empty because it was reading the wrong file.
- **supports:** CAP-B012, CAP-B018
- **verified by:**
  - `poc/tests/integration/test_BC_007_findings_cli.py`

### CAP-T053: `make verify-docs-drift` catches help/README enumerations that no longer match code
- **type:** technical
- **status:** active
- **origin:** BC-016
- **design:** memory/workflow.md § Blocking gates (must pass to merge to `dev`)
- **description:** `poc/scripts/verify_docs_drift.py` cross-checks three enumeration classes between docs (`Makefile` `help:` block + `README.md`) and code: `make <target>` references must exist in `.PHONY` (bidirectional — .PHONY entries also need help lines); `STATUS=foo|bar|...` enumerations must match `STATUS_ORDER` in `poc/scripts/findings.py` (and must be complete when enumerated); `BEACONRY_<NAME>` references must correspond to `os.environ.get("BEACONRY_...")` reads in `poc/`. Each violation includes file source + copy-pasteable fix hint. README matching requires inline-code `` `make foo` `` to avoid prose false positives like "GNU make convention". Hooked into `make check` fast tier.
- **supports:** CAP-B011
- **verified by:**
  - `poc/tests/unit/test_docs_drift_guard.py`

### CAP-T054: BC-017's test-mode `/__test__/*` endpoints (DEPRECATED, superseded by CAP-T055 in BC-018)
- **type:** technical
- **status:** deprecated
- **origin:** BC-017; superseded by CAP-T055 in BC-018
- **design:** memory/workflow.md § Blocking gates (must pass to merge to `dev`)
- **description:** Historical: BC-017 shipped `/__test__/*` endpoints inside hub.py behind a `BEACONRY_TEST_MODE=1` env guard. Audit flagged shipped surface area + misconfig risk. BC-018 replaced this with the telegram-mock service (CAP-T055); hub.py no longer contains the `/__test__/*` block or `BEACONRY_TEST_MODE` env var. Kept here for traceability; no code references.
- **supports:** (none — superseded)
- **verified by:**
  - (none — code deleted; retained as historical marker)

### CAP-T055: telegram-mock service — Telegram Bot API shim + test-driver endpoints
- **type:** technical
- **status:** active
- **origin:** BC-018 (supersedes CAP-T054)
- **design:** docs/stories/BC-018-telegram-mock.md
- **description:** Separate `poc/telegram-mock/` service implements the subset of Telegram Bot API that python-telegram-bot calls (`/bot<token>/getMe`, `/getUpdates`, `/sendMessage`, `/editMessageText`, `/answerCallbackQuery`, `/deleteWebhook`) + three test-driver endpoints (`/__driver__/inject-callback`, `/__driver__/sent`, `/__driver__/reset`). Hub.py has ZERO test-only code; it runs its real production python-telegram-bot client against the mock (pointed via `BEACONRY_TELEGRAM_BASE_URL`). The e2e test seeds findings via `docker exec`, injects fake user taps via telegram-mock's driver, and asserts on captured outbound sendMessage/editMessageText calls. Covers all 4 BC-011 terminal states, all 6 finding statuses, 5 image-tag scenarios. Dividend: operators can `BEACONRY_TELEGRAM_BASE_URL=http://localhost:9080 make demo` for local dev without a real Telegram bot token.
- **supports:** CAP-B018, CAP-B011
- **verified by:**
  - `poc/tests/e2e/test_BC_011_probe_flow.py`

### CAP-T056: Hub configurable Telegram API base URL via `BEACONRY_TELEGRAM_BASE_URL`
- **type:** technical
- **status:** active
- **origin:** BC-018
- **design:** docs/plan/BC-018-telegram-mock-plan.md
- **description:** `poc/hub/hub.py` reads `BEACONRY_TELEGRAM_BASE_URL` at startup. When set, hub's lifespan calls `Application.builder().base_url(f"{base_url}/bot")` so python-telegram-bot talks to the configured host instead of `api.telegram.org`. Empty = real Telegram (default). Enables (a) e2e tests against telegram-mock, (b) dev-without-real-bot workflows. Also split BC-010's `BEACONRY_MOCK` into independent dials: `BEACONRY_SKIP_GRYPE_DB=1` skips the grype DB update step; Telegram wiring is driven by `TELEGRAM_BOT_TOKEN` presence. `BEACONRY_MOCK=1` kept as back-compat alias that sets `SKIP_GRYPE_DB` + leaves `TELEGRAM_BOT_TOKEN` empty.
- **supports:** CAP-T055
- **verified by:**
  - `poc/tests/e2e/test_BC_011_probe_flow.py`

### CAP-T017: Orphan tests fail the gate with a copy-pasteable template
- **type:** technical
- **status:** active
- **origin:** BC-004
- **description:** When a test file lacks a `Covers:` block, verify-traceability emits a stderr message naming the file and a copy-pasteable `"""... Covers: - CAP-NNN: <name> """` template the developer can paste.
- **supports:** CAP-B006
- **verified by:**
  - `poc/tests/integration/test_BC_004_traceability.py`
