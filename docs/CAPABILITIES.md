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
  - `poc/tests/unit/test_telegram_mock.py`

### CAP-T056: Hub configurable Telegram API base URL via `BEACONRY_TELEGRAM_BASE_URL`
- **type:** technical
- **status:** active
- **origin:** BC-018
- **design:** docs/plan/BC-018-telegram-mock-plan.md
- **description:** `poc/hub/hub.py` reads `BEACONRY_TELEGRAM_BASE_URL` at startup. When set, hub's lifespan calls `Application.builder().base_url(f"{base_url}/bot")` so python-telegram-bot talks to the configured host instead of `api.telegram.org`. Empty = real Telegram (default). Enables (a) e2e tests against telegram-mock, (b) dev-without-real-bot workflows. Also split BC-010's `BEACONRY_MOCK` into independent dials: `BEACONRY_SKIP_GRYPE_DB=1` skips the grype DB update step; Telegram wiring is driven by `TELEGRAM_BOT_TOKEN` presence. `BEACONRY_MOCK=1` kept as back-compat alias that sets `SKIP_GRYPE_DB` + leaves `TELEGRAM_BOT_TOKEN` empty.
- **supports:** CAP-T055
- **verified by:**
  - `poc/tests/e2e/test_BC_011_probe_flow.py`
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T057: Evidence-pointer lint — every ✓ shipping-evidence row with a test-shaped pointer is grep-verified against actual pytest node ids
- **type:** technical
- **status:** active
- **origin:** BC-019
- **design:** docs/stories/BC-019-verify-evidence-pointers.md
- **description:** `poc/scripts/verify_evidence_pointers.py` parses every `docs/plan/BC-*-plan.md § Shipping evidence` table and, for every row whose status cell contains `✓`, extracts test-shaped pointers (`file.py[::test_name][[param-id]]`) and asserts they resolve against the real pytest collection (via `pytest --collect-only -q`). File-only pointers starting with `test_` are also checked for on-disk existence; non-test file refs (`hub.py`, `waterfall.py`, ad-hoc scripts) and prose/make-target/URL pointers are intentionally skipped. Deferred rows (⚠️ ❌) and plans without a `## Shipping evidence` section are grandfathered. Wired into `make check` as a fast-tier blocking gate. Closes the vigilance gap exposed by BC-018 (five ✓ rows pointed at vaporware tests). First retroactive run surfaced 12 historical pointer lies across BC-011/BC-012/BC-013/BC-015 — all fixed in the same branch as free regression data.
- **supports:** CAP-B006
- **verified by:**
  - `poc/tests/integration/test_BC_019_verify_evidence_pointers.py`
  - `poc/tests/unit/test_verify_evidence_pointers.py`

### CAP-B019: Operator sees KEV-listed CVEs visually distinct and ranked first across the UI (Telegram + findings CLI)
- **type:** business
- **status:** active
- **origin:** BC-020 (first implementation of the Tier 0 differentiation promised in `memory/design-decisions.md § Urgency tiers` since BC-000)
- **description:** When a finding's CVE is in the CISA KEV catalog, the Telegram notification prepends `🚨 KEV — EXPLOITED IN THE WILD`, the DB stores `tier=0` + `kev=1`, and `make findings` surfaces a `KEV` column while sorting rows by `(tier, -cvss)` so Tier 0 rows surface first regardless of CVSS score. Operator at a glance distinguishes "exploit in the wild" from "high CVSS but speculative." Pre-BC-020, every finding was effectively Tier 3 (DB default) because `urgency.classify_tier` was dead code.
- **supports:** CAP-B011, CAP-B018
- **verified by:**
  - `poc/tests/integration/test_BC_020_kev_ingest.py`

### CAP-T058: CISA KEV feed ingestion — fetch on boot + hourly refresh + stale fallback + /healthz freshness
- **type:** technical
- **status:** active
- **origin:** BC-020
- **design:** docs/stories/BC-020-kev-ingest.md
- **description:** `poc/hub/kev.py` fetches `https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json` (overridable via `BEACONRY_KEV_FEED_URL` for air-gapped setups), parses `vulnerabilities[].cveID` into a frozenset, caches to `${DATA_DIR}/kev_cache.json`, exposes `is_known_exploited(cve_id)` as an O(1) case-insensitive lookup. Fetch runs in an asyncio background task spawned by `lifespan` — it does NOT gate `/readyz` (restart-loop trap). `/healthz` surfaces freshness: `{"kev": "ok|stale|unfetched", "kev_last_fetch_s": N, "kev_count": N}`. Fetch failure is non-fatal; disk cache survives.
- **supports:** CAP-B019
- **verified by:**
  - `poc/tests/integration/test_BC_020_kev_ingest.py`
  - `poc/tests/unit/test_kev.py`

### CAP-T059: Hub ingest path wires urgency.classify_tier → DB tier column
- **type:** technical
- **status:** active
- **origin:** BC-020
- **design:** memory/design-decisions.md § Urgency tiers
- **description:** Pre-BC-020, `urgency.classify_tier` existed with passing tests but was never called by hub.py — every finding stored `tier=3` (DB default). BC-020 calls it from `process_sbom_entry` with `kev=kev.is_known_exploited(cve_id)` and `internet_facing=False` (deferred to a separate story). DB schema gains a `kev INTEGER DEFAULT 0` column (idempotent ALTER migration). `Finding` dataclass gains `kev: bool = False`. The urgency ladder is now behavior, not just docs.
- **supports:** CAP-B019
- **verified by:**
  - `poc/tests/integration/test_BC_020_kev_ingest.py`
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T060: Notification format inserts a KEV header line for tier 0 findings
- **type:** technical
- **status:** active
- **origin:** BC-020
- **description:** `format_notification(f)` prepends `🚨 <b>KEV — EXPLOITED IN THE WILD</b>` as the first line when `f.tier == 0`. Operator sees the Tier 0 differentiation at a glance in Telegram without having to parse the severity row.
- **supports:** CAP-B019
- **verified by:**
  - `poc/tests/integration/test_BC_020_kev_ingest.py`
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T061: Dockerfile-sources lint — every service-local .py is COPY'd by its Dockerfile
- **type:** technical
- **status:** active
- **origin:** BC-021 (generalizes BC-012's narrow `verify-dockerfile-health-copy`)
- **design:** docs/stories/BC-021-dockerfile-copy-lint.md
- **description:** `poc/scripts/verify_dockerfile_sources.py` enumerates every non-`__init__.py`, non-`test_*.py` file in `poc/<service>/` and asserts it is matched by at least one COPY source in the service's Dockerfile. Three COPY syntaxes supported: explicit filename, glob (`*.py`), and dir (`service/`). Service dirs without a Dockerfile are skipped. Catches BC-020-class ship-time packaging regressions where a new `.py` is imported by the entrypoint but omitted from the explicit `COPY` list.
- **supports:** CAP-B006
- **verified by:**
  - `poc/tests/integration/test_BC_021_dockerfile_sources.py`
  - `poc/tests/unit/test_verify_dockerfile_sources.py`

### CAP-T062: Digest format surfaces KEV markers for suppressed findings
- **type:** technical
- **status:** active
- **origin:** BC-022
- **design:** docs/stories/BC-022-digest-kev-awareness.md
- **description:** `send_digest` prepends `🚨 ` on each bullet-list row whose finding has `kev=True`, and inserts a loud banner `⚠️ N suppressed finding(s) are KEV — raise BEACONRY_MAX_FINDINGS_PER_RUN to see them individually.` immediately after the header when `any(f.kev for f in suppressed)`. Closes BC-020's invariant break: when `#KEV > BEACONRY_MAX_FINDINGS_PER_RUN`, Tier 0 findings would otherwise be invisibly listed in the digest. The sort order remains `(tier, -cvss)`, so when the cap holds (`#KEV ≤ cap`), the digest contains zero KEV findings and the banner is suppressed.
- **supports:** CAP-B019
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T063: KEV findings bypass MIN_CVSS filter in hub ingest
- **type:** technical
- **status:** active
- **origin:** BC-023 (fixes an ordering bug in BC-020's CAP-T059)
- **design:** docs/stories/BC-023-kev-bypasses-min-cvss.md
- **description:** `process_sbom_entry` computes `is_kev` before applying the MIN_CVSS gate: `if not is_kev and cvss < MIN_CVSS: continue`. KEV CVEs always pass the filter regardless of their theoretical CVSS score; non-KEV CVEs continue to be filtered as before. Fixes BC-020's architectural bug where KEV was checked AFTER a filter that could drop it, making Tier 0 unreachable for CVSS < MIN_CVSS CVEs. A `log.info` records every bypass for operator audit. Live acceptance surfaced two previously-silent KEV CVEs in `nginx:1.24`: CVE-2025-27363 (CVSS 8.1, libfreetype6) and CVE-2023-44487 (CVSS 7.5, nginx HTTP/2 Rapid Reset).
- **supports:** CAP-B019
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T064: verify-traceability enforces reverse direction (CAP → test's Covers block)
- **type:** technical
- **status:** active
- **origin:** BC-024
- **design:** docs/stories/BC-024-traceability-reverse-check.md
- **description:** `verify_traceability.py::main` adds a reverse-direction pass: for every `(cap, verified_by_ref)` pair in CAPABILITIES.md, resolve the verified-by to an on-disk test file and assert the file's `Covers:` block claims the CAP id. The forward direction (test claims CAP → CAP must list test) was already enforced by CAP-T016; this closes the gap where a CAP was added with `verified by:` but the test file's Covers block was forgotten. Pattern surfaced four consecutive times (BC-018/020/022/023) — now caught at `make check` time. Retroactive run surfaced 8 historical drifts, all fixed in this branch as free regression data.
- **supports:** CAP-B006
- **verified by:**
  - `poc/tests/integration/test_BC_004_traceability.py`

### CAP-T065: Per-file coverage floor (65% default, allowlist for integration-shell modules)
- **type:** technical
- **status:** active
- **origin:** chore/complexity-gates (2026-10-31)
- **description:** `poc/scripts/verify_per_file_coverage.py` reads `coverage.json` (produced by `pytest --cov-report=json`) and enforces a per-file 65% coverage floor on top of coverage.py's 85% global floor. Reservoir-style global metrics let one high-coverage file mask many low-coverage ones; the per-file floor blocks silent regressions in individual modules. Explicit allowlist (dict in the script) carves out integration-shell files with a lower floor + prose reason (scout.py at 30% because syft/grype subprocess wrappers are live-tested, not unit-tested). Also raised global `fail_under` from 60% → 85% in pyproject.toml and enabled ruff complexity gates C901 / PLR0912 / PLR0913 / PLR0915 with slightly looser thresholds so existing hub.py / verify_traceability.py land cleanly with explicit `# noqa` + follow-up IDEAs.
- **supports:** CAP-B006
- **verified by:**
  - `poc/tests/unit/test_verify_per_file_coverage.py`

### CAP-T066: KEV lookup resolves grype's `relatedVulnerabilities` aliases
- **type:** technical
- **status:** active
- **origin:** BC-028
- **description:** `kev.is_known_exploited_any(ids)` returns True if any id in the collection is in the KEV catalog. `process_sbom_entry` now passes `[vuln["id"], *related_ids]` so a Go stdlib finding like `GO-2023-1703` (not in KEV) with its CVE alias `CVE-2023-44487` (in KEV, HTTP/2 Rapid Reset) correctly gets tier=0. The primary id is preserved for display. Pre-BC-028, non-CVE primary ids (`GO-*`, `GHSA-*`) silently missed KEV lookups.
- **supports:** CAP-B019
- **verified by:**
  - `poc/tests/unit/test_kev.py`
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T067: Append-only audit log of operator-visible state changes
- **type:** technical
- **status:** active
- **origin:** BC-029
- **description:** `poc/hub/audit.py` writes JSONL events to `${DATA_DIR}/audit.log`. Every status transition (`set_status`) and every new finding (`save_finding`) appends one line. Fire-and-forget: an audit write failure logs a warning but never raises — audit is observability, not control-flow. Append-only by design: never truncates, rewrites, or deletes. `read_events(limit=N)` returns newest-first for operator consumption. Shells grep well: `grep CVE-2023-44487 audit.log` surfaces every event for a specific CVE weeks later.
- **supports:** CAP-B011, CAP-B018
- **verified by:**
  - `poc/tests/unit/test_audit.py`

### CAP-B021: Operator sees regression alerts — a previously-fixed CVE that returns raises a loud alarm
- **type:** business
- **status:** active
- **origin:** BC-031 — operator feedback 2026-10-31: "A fix it button should not be a 'forget it'"
- **description:** Beaconry's Fix action is not fire-and-forget. Every finding marked `fixed` / `fixed_verified` stays under surveillance; subsequent scout cycles reconcile against the DB. A finding that returns to scan after being fixed transitions to a new `regressed` status and triggers a 🔥 FIX REGRESSED Telegram notification distinct from the first-fire. Operator can opt out via `BEACONRY_RECONCILE_ENABLED=0` for self-hosted scenarios where the long-term diff feels like surveillance.
- **supports:** CAP-B018
- **verified by:**
  - `poc/tests/integration/test_BC_031_scan_reconciliation.py`

### CAP-T071: Scan reconciliation diffs current scan against DB open+fixed rows
- **type:** technical
- **status:** active
- **origin:** BC-031
- **description:** `hub.reconcile_scan(host, scanned_ids)` queries findings with status in `{open, fixed, fixed_verified, regressed}` for the given host, applies transition rules (open absent → fixed_verified, fixed present → regressed, regressed absent → fixed_verified, snoozed/accepted untouched), writes audit events, returns `(regressed, auto_closed)` lists. Called from `receive_scan` after `process_sbom_entry`. Scoped per-host so a scan from `host-a` cannot touch `host-b` findings. Opt-out via `BEACONRY_RECONCILE_ENABLED=0`.
- **supports:** CAP-B021
- **verified by:**
  - `poc/tests/integration/test_BC_031_scan_reconciliation.py`

### CAP-T072: `regressed` DB status with distinct Telegram notification format
- **type:** technical
- **status:** active
- **origin:** BC-031
- **description:** New DB status value `regressed` + new `last_fixed_ts` column (idempotent migration). `set_status(..., "fixed"/"fixed_verified")` writes the ts automatically. `format_regression(f, last_fixed_ts)` renders a 🔥 FIX REGRESSED body including the prior-fix date for operator diffing against change logs. Keyboard offers Re-fix + Accept risk. `make findings` surfaces regressed as highest priority in STATUS_ORDER.
- **supports:** CAP-B021
- **verified by:**
  - `poc/tests/integration/test_BC_031_scan_reconciliation.py`

### CAP-T073: Audit log carries `auto_closed`, `regressed` event types
- **type:** technical
- **status:** active
- **origin:** BC-031 (extends CAP-T067)
- **description:** `audit.append()` is called from `reconcile_scan` with two new event types: `auto_closed` (finding absent from scan → fixed_verified; prev_status + cve_id in details) and `regressed` (fixed finding returned; prev_status + last_fixed_ts + regressed_ts). Operators can grep `/data/audit.log` to see every reconciliation decision beaconry has made over the lifetime of the DB.
- **supports:** CAP-B021, CAP-T067
- **verified by:**
  - `poc/tests/integration/test_BC_031_scan_reconciliation.py`

### CAP-T074: Notification body carries a fix-viability indicator (✅ likely / ❌ unlikely / ❓ unknown)
- **type:** technical
- **status:** active
- **origin:** BC-032 — operator screenshot 2026-10-31 of 🔥 FIX REGRESSED for nginx:1.24 flagged that Fix would be a no-op loop with no warning
- **description:** `hub.assess_fix_viability(finding) -> (label, reason)` returns an operator-facing signal about whether tapping Fix is likely to help. Heuristic: docker + frozen semver tag → unlikely (BC-014 same-track pull returns same bits); docker + `latest`/`main`/`stable`/`edge`/`nightly` → likely; alpm + fixed_version present → likely (`sudo pacman -Syu` pulls it); everything else → unknown. Rendered via `_viability_line` into both `format_notification` and `format_regression` so the operator can tell at-a-glance whether the fix button will loop.
- **supports:** CAP-B018
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T075: Regression notification body shows `→ fixed in X` (parity with regular notification)
- **type:** technical
- **status:** active
- **origin:** BC-032 — operator screenshot showed the regression body was missing `fixed in` even though `format_notification` had it since BC-002
- **description:** `format_regression` now includes the `Package: X Y.Z → fixed in A.B.C` line that `format_notification` has always had. Prevents operators from having to cross-reference back to the original notification to see which version carries the fix.
- **supports:** CAP-B021
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T076: verify_fix scans the operator's actual source_ref (not hardcoded `:latest`)
- **type:** technical
- **status:** active
- **origin:** BC-033 — operator 2026-10-31 discovered that tapping Re-fix on a frozen tag would (if the handler had fired) scan the wrong image
- **description:** Pre-BC-033 `verify_fix` hardcoded `syft {base}:latest` for the post-fix re-scan. This was correct for the pre-BC-014 fix flow (which pulled `:latest`) but became wrong when BC-014 switched to same-track pulls. Result: operator who tapped Fix on `nginx:1.24` got verification against `nginx:latest` — potentially telling them "✅ Fixed" when `nginx:1.24` was still vulnerable, or "⚠️ CVE still present" based on the wrong image's contents. Post-BC-033: `syft {finding.source_ref}` scans the actual tag. Pairs with BC-032's viability indicator — for frozen tags where upstream has no fix, verify_fix now reliably returns False, honest signal end-to-end.
- **supports:** CAP-B018
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-T077: Hub persists `last_seen_ts` per finding (prerequisite data for reminder features)
- **type:** technical
- **status:** active
- **origin:** BC-034 — operator question 2026-10-31 "are we storing when we saw a specific software + version the last time?"
- **description:** Every scan calls `bump_last_seen(host, scanned_ids)` which `UPDATE`s `last_seen_ts = NOW()` for every DB finding whose id is in the current scan. Runs independently of `BEACONRY_RECONCILE_ENABLED` — this is pure inventory bookkeeping, not an operator-visible state change. Legacy rows backfilled from `found_at` by idempotent `init_db` migration. Enables future heartbeat / digest-refresh / stale-row-detection features that need "last time we saw this" to differentiate recently-active findings from six-month-old database ghosts.
- **supports:** CAP-B011
- **verified by:**
  - `poc/tests/unit/test_last_seen_tracking.py`

### CAP-T078: Notification adapter architecture with per-channel enable/disable
- **type:** technical
- **status:** active
- **origin:** BC-035 — operator 2026-10-31 "important is that we can enable/disable all kind of notification and the notification types are following an adapter approach"
- **description:** Outbound notifications go through a registry of `NotificationAdapter` subclasses, each owning its own `env_var` enable flag (default on when unset). Hub call sites (`notify_finding`, `notify_regression`, `notify_heartbeat`, `notify_digest`) iterate the registry; one failing adapter is logged and swallowed per-call so it cannot block siblings. First adapter is `TelegramAdapter`; future adapters (email, Reddit, SMS, webhook) subclass `NotificationAdapter` and self-register — no hub.py changes needed. Interactive callbacks (button taps) stay Telegram-specific in `on_button_click` — the adapter abstraction is for OUTBOUND dispatch only.
- **supports:** CAP-B003
- **verified by:**
  - `poc/tests/unit/test_notification_adapters.py`
  - `poc/tests/unit/test_telegram_adapter.py`
  - `poc/tests/unit/test_hub_env_helpers.py`

### CAP-T079: Heartbeat sweep reminds operators about stale open findings
- **type:** technical
- **status:** active
- **origin:** BC-035 — "fix is not forget" principle; silent-forever findings are a bug
- **description:** Periodic `heartbeat_sweep()` scans the DB for rows where `status='open'`, `last_seen_ts > now - BEACONRY_HEARTBEAT_LIVENESS_WINDOW_S` (still in inventory), and `last_notified_ts < now - BEACONRY_HEARTBEAT_INTERVAL_S` (default 7d). Each hit dispatches `notify_heartbeat` through the adapter registry (so disabling Telegram also silences heartbeats) and advances `last_notified_ts` so the next sweep within the interval is a no-op. Snoozed/accepted/fixed/absent findings are skipped — BC-031 reconcile owns those paths. Audit event `heartbeat_reminder_sent` carries `finding_id`, `cve_id`, `days_open`. Operator opt-out: `BEACONRY_HEARTBEAT_ENABLED=0`.
- **supports:** CAP-B003
- **verified by:**
  - `poc/tests/unit/test_heartbeat_sweep.py`

### CAP-B020: Operator sees internet-facing CVEs ranked above LAN-only ones (Tier 1 differentiation)
- **type:** business
- **status:** active
- **origin:** BC-030 — first implementation of Tier 1 (promised in `memory/design-decisions.md § Urgency tiers` since BC-000, defeated by hard-coded `internet_facing=False` in BC-020's wiring)
- **description:** When a finding's `source_ref` matches a running container that publishes any port to a non-loopback host IP, the Telegram notification prepends `🌐 INTERNET-FACING — Tier 1`, the DB stores `tier=1 + internet_facing=1`, and `make findings` surfaces a `NET` column (🌐/—). The sort key `(tier, -cvss)` ranks Tier 1 above Tier 2 so operator at-a-glance distinguishes "exploit reachable from the internet" from "exploit in a LAN-only service." KEV (Tier 0) still wins — regression-guarded by `test_kev_wins_over_internet_facing`. Opt-out: `BEACONRY_INTERNET_FACING_DETECTION_ENABLED=0`.
- **supports:** CAP-B011, CAP-B018
- **verified by:**
  - `poc/tests/integration/test_BC_030_internet_facing_detection.py`

### CAP-T068: Scout emits per-container port-publication map
- **type:** technical
- **status:** active
- **origin:** BC-030
- **description:** Scout's `collect_container_ports()` inspects every running container's `NetworkSettings.Ports`, folds across multiple containers sharing an image, returns `{image_tag: is_internet_facing}` in the hub POST payload under `container_exposure`. The parse helper `is_container_internet_facing(ports)` treats `0.0.0.0`, `::`, empty `HostIp` (Docker's default), and routable host IPs as exposed; `127.0.0.1`, `::1`, and `localhost` as not exposed. Null bindings and missing dicts safely return False.
- **supports:** CAP-B020
- **verified by:**
  - `poc/tests/unit/test_scout_container_ports.py`
  - `poc/tests/integration/test_BC_030_internet_facing_detection.py`

### CAP-T069: Hub joins finding to port map at ingest; sets Finding.internet_facing
- **type:** technical
- **status:** active
- **origin:** BC-030
- **description:** `process_sbom_entry` accepts a `port_exposure: dict[str, bool]` parameter (defaults to empty for backward compatibility), looks up `source_ref` in the map, and passes the real value to `urgency.classify_tier`. Finding dataclass gains `internet_facing: bool = False`; DB schema adds `internet_facing INTEGER DEFAULT 0` via idempotent migration; `save_finding` + `load_finding` round-trip. Toggle `BEACONRY_INTERNET_FACING_DETECTION_ENABLED=0` forces hub to ignore scout's map and classify everything as False (pre-BC-030 behavior). Legacy rows render as not-exposed — honest default since pre-BC-030 we hard-coded that value anyway.
- **supports:** CAP-B020
- **verified by:**
  - `poc/tests/integration/test_BC_030_internet_facing_detection.py`
  - `poc/tests/unit/test_internet_facing_classification.py`

### CAP-B021: Operator verifies out-of-band fixes without waiting on scan cadence
- **type:** business
- **status:** active
- **origin:** BC-037 — homelab operator workflow: manual `docker compose pull` then "is it clean now?" verification
- **description:** `make scan` triggers an immediate full scout cycle via `POST /scan/trigger` on the scout HTTP server. BC-033 covers single-image verification through the Fix button; BC-037 covers the multi-service manual-remediation path. Return within ~1s with 202 Accepted + started_at; a concurrent trigger returns 409 Conflict (lock held); Docker socket unavailable → 503. The scheduled SCAN_INTERVAL loop is unaffected — manual triggers share the mutex, not the schedule.
- **supports:** CAP-B018
- **verified by:**
  - `poc/tests/integration/test_BC_037_on_demand_scan.py`
  - `poc/tests/integration/test_BC_031_scan_reconciliation.py`

### CAP-T081: On-demand scan trigger — POST /scan/trigger with concurrent-run guard
- **type:** technical
- **status:** active
- **origin:** BC-037
- **description:** Scout exposes a POST route that acquires `_scan_in_progress` synchronously in the HTTP handler (so the very next request sees the flag) and schedules the actual `collect_and_post` work via `asyncio.create_task`. The guard lives in `_try_acquire_scan_lock` / `_release_scan_lock` — a bool suffices for scout's single-threaded event loop. Loop + manual trigger share the same guard — one path can never double-scan. Timer independence: manual trigger does NOT shift the next scheduled loop tick.
- **supports:** CAP-B021
- **verified by:**
  - `poc/tests/unit/test_scan_trigger.py`
  - `poc/tests/integration/test_BC_037_on_demand_scan.py`

### CAP-B022: Operator can verify pipeline behavior under simulated mutations
- **type:** business
- **status:** active
- **origin:** BC-038 — "how could we simulate state changes and verify correct scanning?" (2026-10-31)
- **description:** `make test-scenarios` spins up an isolated hub (BEACONRY_TEST_MODE=1) and runs 10 behavior scenarios covering every shipped operator-visible promise: fresh ingest (BC-020), auto-close + regression (BC-031), snooze flow (BC-003), KEV Tier 0 (BC-020), Internet-facing Tier 1 (BC-030), digest content-hash (BC-036), heartbeat sweep with injected clock (BC-035), multi-host isolation, last_seen_ts bump (BC-034). Deterministic — no real grype, no clock drift, no network. Full suite under 30 s.
- **supports:** CAP-B018
- **verified by:**
  - `poc/tests/integration/test_BC_038_scenario_harness.py`
  - `poc/tests/unit/test_scenario_runner.py`

### CAP-T082: Live-stack scenario harness runner
- **type:** technical
- **status:** active
- **origin:** BC-038
- **description:** Each scenario is a class subclassing `Scenario(ABC)` with `name`, `order`, `description`, and `run(ctx)`. Explicit `SCENARIOS = [...]` registry — no autodiscovery. `ScenarioContext` provides mutation (`inject_scan`, `trigger_sweep`, `set_db_status`, `wipe`) and assertion primitives (`assert_status`, `assert_tier`, `assert_internet_facing`, `assert_audit_fired`, `assert_audit_absent`, `assert_count`). Scenarios compose assertions; the context never knows about CVE-specific logic — zero central if-chains. CLI entry `python -m poc.scenarios` + Makefile `test-scenarios` target wrap the shell lifecycle (compose up → health gate → runner → teardown).
- **supports:** CAP-B022
- **verified by:**
  - `poc/tests/unit/test_scenario_runner.py`
  - `poc/tests/integration/test_BC_038_scenario_harness.py`

### CAP-T083: Hub test-mode endpoints under BEACONRY_TEST_MODE=1
- **type:** technical
- **status:** active
- **origin:** BC-038
- **description:** When `BEACONRY_TEST_MODE=1`, hub registers three routes: `POST /test/wipe` (delete DB + audit, re-init), `POST /test/scan` (accept pre-matched candidates + optional `at` timestamp + `port_exposure`, bypass grype, run the real `_ingest_candidates` flow), `POST /test/sweep` (invoke `heartbeat_sweep` with injected `now`). Route registration — not per-request check — is the gate: when the flag is off at import time, routes are not attached, requests return 404. Deliberate design: `grep -n "@api.post" poc/hub/hub.py` enumerates every reachable endpoint. Future lint candidate (IDEAS backlog): extend `/ultrareview` to fail if `/test/*` routes exist without the env guard.
- **supports:** CAP-B022
- **verified by:**
  - `poc/tests/integration/test_BC_038_scenario_harness.py`
  - `poc/tests/unit/test_scenario_runner.py`

### CAP-B023: Developer can review the entire variable surface without greping the codebase
- **type:** business
- **status:** active
- **origin:** BC-039 — developer 2026-10-31: ~30 env vars shipped in two weeks, unmanageable without a single source of truth
- **description:** `docs/VARIABLES.md` is the one authoritative index of every env variable beaconry reads at runtime. Grouped by concern (Telegram, Scanning, Fix execution, Reconcile, Heartbeat, Digest, Notification adapters, Feeds, Paths, Dev-mode, CLI scripts, Scenario harness). Each row carries name, default, type, where-read, reason (the WHY, not just WHAT), and BC origin.
- **supports:** CAP-B018
- **verified by:**
  - `poc/tests/unit/test_verify_variables_documented.py`

### CAP-T084: `docs/VARIABLES.md` — single source of truth for runtime env variables
- **type:** technical
- **status:** active
- **origin:** BC-039
- **description:** Markdown table under `<!-- BC-039 BEGIN VARIABLES TABLE -->` / `<!-- END -->` markers. Columns: `Variable | Default | Type | Where read | Reason | Origin`. Parser matches `^\|\s*` + backtick-wrapped-name + remaining cells. Groups reflect operator mental model, not code module layout.
- **supports:** CAP-B023
- **verified by:**
  - `poc/tests/unit/test_verify_variables_documented.py`

### CAP-T085: `verify-variables` lint catches code↔doc drift bidirectionally
- **type:** technical
- **status:** active
- **origin:** BC-039
- **description:** `poc/scripts/verify_variables_documented.py` scans `poc/<non-tests>/*.py` for three env-read forms (`os.environ.get("X")`, `os.environ["X"]`, `_int_env("X", …)`, class-attribute `env_var = "X"`). Cross-refs with `VARIABLES.md`. Fails on: (1) read without doc row, (2) doc row without reader, (3) row with exact placeholder markers like `*(default)*`, (4) row with fewer than minimum columns, (5) backtick-wrapped names that aren't valid env var identifiers, (6) looks-like-a-var-name text missing backticks. Explicit `TEST_ONLY_ALLOWLIST` for fixture vars. Hooked into `make check` fast tier.
- **supports:** CAP-B023
- **verified by:**
  - `poc/tests/unit/test_verify_variables_documented.py`

### CAP-B024: Operator who copies `.env.example` gets every dial with correct defaults
- **type:** business
- **status:** active
- **origin:** BC-040 — pre-BC-040 `.env.example` carried 7 variables while `VARIABLES.md` had 32; operators never discovered BC-030/035/036 toggles unless they stumbled on a BC story
- **description:** `poc/.env.example` now ships every runtime variable in the agreed format: `VAR=default # default | description`, grouped by concern (same groups as `VARIABLES.md`). Required credentials use angle-bracket placeholders (`<from @BotFather>`). Developer-only variables (`BEACONRY_TEST_MODE`, `BEACONRY_SCENARIO_*`) intentionally omitted via explicit allowlist with reason. Operator copies `.env.example → .env`, has every dial exposed with the shipped default pre-filled.
- **supports:** CAP-B018, CAP-B023
- **verified by:**
  - `poc/tests/unit/test_verify_variables_documented.py`

### CAP-T086: Rewritten `poc/.env.example` as the full operator catalog
- **type:** technical
- **status:** active
- **origin:** BC-040
- **description:** Flat shell-syntax file consumed by Compose + copyable to `.env`. Lines: `VAR=default` with aligned trailing `# default | description` comment. Groups delimited by `# ─── Group ───`. Angle-bracket placeholders (`<label>`) for required-no-default vars signal "operator must fill". Format mirrors operator's existing `.env` so copy/diff is intuitive.
- **supports:** CAP-B024
- **verified by:**
  - `poc/tests/unit/test_verify_variables_documented.py`

### CAP-T087: Three-way lint — VARIABLES.md ↔ .env.example ↔ docker-compose.yml
- **type:** technical
- **status:** active
- **origin:** BC-040
- **description:** Extends `verify_variables_documented.py` with four BC-040 checks: (5) VARIABLES.md row missing from `.env.example` (unless on `ENV_EXAMPLE_DEV_ONLY_ALLOWLIST`), (6) `.env.example` line not in VARIABLES.md, (7) compose `environment:` entry not in VARIABLES.md, (8) default-literal mismatch between VARIABLES.md and `.env.example`. Normalizer: VARIABLES.md defaults inside the FIRST backticked span (`` `604800` (7 d) `` → `604800`) are the only concrete literals compared; prose defaults (`*(empty)*`, `<CISA upstream>`, `uname().nodename`, `derived from BEACONRY_MOCK`) are skipped on equality. Angle-bracket values in `.env.example` are operator-fill, no equality check.
- **supports:** CAP-B024
- **verified by:**
  - `poc/tests/unit/test_verify_variables_documented.py`

### CAP-B025: Pre-ship audit catches partial close-gap fixes
- **type:** business
- **status:** active
- **origin:** BC-041 — repeated BC-031 + BC-040 pattern of close-gap tests landing without plan rows; audit "all green" never caught it
- **description:** The developer running the pre-ship audit sees a mechanical signal (`verify-evidence-pointers` fails on orphan tests) in addition to the Gherkin coverage lens. Catches the specific class of miss where a close-gap commit adds `test_x` to an integration test file but forgets the matching shipping-evidence row.
- **supports:** CAP-B006
- **verified by:**
  - `poc/tests/integration/test_BC_041_reverse_check.py`

### CAP-T088: `verify-evidence-pointers` reverse pass — every integration test referenced by a plan row
- **type:** technical
- **status:** active
- **origin:** BC-041
- **description:** `reverse_check` iterates `poc/tests/integration/test_BC_NNN_*.py`, resolves the matching `docs/plan/BC-NNN-*-plan.md` by `BC-NNN-` prefix, and asserts every top-level `def test_*(...)` function in the file is mentioned somewhere in the plan's shipping-evidence table (plain name match, parametrization-agnostic). Soft-passes when the plan file is missing OR has no `## Shipping evidence` section — BC-019 owns those checks. Empty-but-present table is NOT soft-pass. Explicit `REVERSE_CHECK_ALLOWLIST: dict[str, str]` for intentional helpers / grandfathered pre-BC-041 tests, reason per entry. Error message surfaces a copy-pasteable row template.
- **supports:** CAP-B025
- **verified by:**
  - `poc/tests/integration/test_BC_041_reverse_check.py`

### CAP-B026: Operator can answer every post-hoc troubleshooting question without parsing JSON
- **type:** business
- **status:** active
- **origin:** BC-042 — every debugging conversation this session needed audit data via `docker exec … tail /data/audit.log` + eye-grep
- **description:** `make audit` renders `/data/audit.log` as an aligned timeline, filterable by time window, event type, CVE id, host, finding id. Resolves `finding_id → cve_id + host` via the DB so operators can query by CVE even for `status_change` events that store only `finding_id`. `COUNT=1` provides a one-line-per-type summary; `FOLLOW=1` tails live. Pairs with `make findings` (state) + `make status` (health) + `make scan` (re-check) as the operator-triage quartet.
- **supports:** CAP-B018
- **verified by:**
  - `poc/tests/integration/test_BC_042_make_audit_cli.py`

### CAP-T089: `make audit` CLI with filters + compose-aware routing
- **type:** technical
- **status:** active
- **origin:** BC-042
- **description:** `poc/scripts/audit.py` parses `${DATA_DIR}/audit.log`, applies AND-combined filters (LAST/SINCE/UNTIL/EVENT/CVE/HOST/FINDING, COUNT/FOLLOW mutually with LAST). Time parsing accepts `1h30m` / `2d` relative durations or ISO `YYYY-MM-DDTHH:MM[:SS]`. Compose-aware: docker-exec into `beaconry-hub` when the live stack is up; otherwise local file. Inline-script path for in-container execution so operators don't need `audit.py` COPY'd into the image.
- **supports:** CAP-B026
- **verified by:**
  - `poc/tests/unit/test_audit_cli.py`
  - `poc/tests/integration/test_BC_042_make_audit_cli.py`

### CAP-T090: Hub emits `scan_received` audit event per `/scan` POST
- **type:** technical
- **status:** active
- **origin:** BC-042
- **description:** `receive_scan` emits one `scan_received` entry with `host`, `sbom_count`, `exposed_count` BEFORE processing SBOMs. Closes the blind spot where "scout silently stopped" and "scout posted zero candidates" were indistinguishable in audit — both produced empty audit output. Post-match stats (new findings) continue to live in `finding_ingested` events.
- **supports:** CAP-B026
- **verified by:**
  - `poc/tests/integration/test_BC_042_make_audit_cli.py`

### CAP-B027: Operator always has a CVE paper trail for non-CVE primary findings
- **type:** business
- **status:** active
- **origin:** BC-043 — operator opens a KEV Telegram ping for `GO-2026-6604` and can't tell which CVE id made CISA care; same gap applies to non-KEV findings on `GO-*`/`GHSA-*` primaries that an operator wants to cross-reference against NVD or GitHub advisories
- **description:** Every finding resolves an optional `cve_alias` from grype's `relatedVulnerabilities[]` at ingest. Non-CVE primary ids (`GO-*`, `GHSA-*`, `AVG-*`) display the alias inline (`GO-2026-6604 · CVE-2024-12345`) OR explicitly state `no CVE alias` when none exists. KEV findings with non-CVE primaries additionally render a `via <alias>` line under the 🚨 KEV header so the CISA paper trail is unmissable. Audit event `finding_ingested` carries `cve_alias` for forensic chain.
- **supports:** CAP-B019
- **verified by:**
  - `poc/tests/integration/test_BC_043_kev_cve_alias.py`

### CAP-T091: `Finding.cve_alias` field + DB migration + round-trip
- **type:** technical
- **status:** active
- **origin:** BC-043
- **description:** `Finding.cve_alias: str | None` populated at ingest by `_pick_cve_alias(primary, related_ids)` — returns the first CVE-shaped id in `related_ids` when primary isn't CVE-shaped, None otherwise. Idempotent `ALTER TABLE findings ADD COLUMN cve_alias TEXT` in `init_db`. `save_finding` + `load_finding` round-trip; legacy pre-BC-043 rows load with `cve_alias=None`.
- **supports:** CAP-B027
- **verified by:**
  - `poc/tests/unit/test_kev_alias_persistence.py`
  - `poc/tests/integration/test_BC_043_kev_cve_alias.py`

### CAP-T092: Notification formatters surface the alias on non-CVE primaries
- **type:** technical
- **status:** active
- **origin:** BC-043
- **description:** Shared `_severity_line(finding, emoji, esc)` helper used by `format_notification`, `format_regression`, `format_heartbeat`. Preserves the pre-BC-043 bold `<b>severity — primary</b>` for CVE primaries; appends `· <code>cve_alias</code>` for non-CVE+alias; appends `· <i>no CVE alias</i>` for non-CVE+no-alias. KEV header gains a `via <alias>` line when primary is non-CVE so the paper trail reads top-to-bottom.
- **supports:** CAP-B027
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`
  - `poc/tests/integration/test_BC_043_kev_cve_alias.py`

### CAP-B028: Operator confidence in CLI output after any PR
- **type:** business
- **status:** active
- **origin:** BC-044 — three live-only bugs in two weeks (BC-030 Dockerfile COPY, BC-035 import scope, BC-042 FOLLOW buffering) reached production state before manual exercise caught them
- **description:** `make test-cli` runs 10 non-destructive scenarios against the live stack, each asserting a specific operator-visible contract (column presence, exit code semantics, formatted-vs-raw-JSON output, idempotence). Three scenarios are explicit regression guards for the live-only bugs that motivated the story: scenario 05 (bad SINCE must exit 2 cleanly, no traceback), scenario 06 (FOLLOW emits the TIME header through pipes), scenario 10 (hub container can `import notifiers`).
- **supports:** CAP-B018
- **verified by:**
  - `poc/tests/unit/test_cli_smoke_scenarios.py`

### CAP-T093: `make test-cli` live-smoke harness
- **type:** technical
- **status:** active
- **origin:** BC-044
- **description:** `poc/scripts/cli_smoke.py` mirrors BC-038's scenario pattern — ABC `CliScenario` + explicit registry + Make target. Each scenario invokes a real `make <target>` via subprocess, asserts on stdout/stderr/returncode. Fast-fails with "run `make live-rebuild` first" if `beaconry-hub` isn't up. Opt-out: `BEACONRY_SKIP_CLI_SMOKE=1`. Wired into `make check-full` so CI catches the regression class pre-merge. Scenarios include FOLLOW-formatted-output check, bad-SINCE clean-error check, and in-container `import notifiers` check.
- **supports:** CAP-B028
- **verified by:**
  - `poc/tests/unit/test_cli_smoke_scenarios.py`

### CAP-T080: Digest cadence throttle with content-hash fallback
- **type:** technical
- **status:** active
- **origin:** BC-036 — operator 2026-10-31 asked how to tune digest cadence independently of scan cadence
- **description:** `receive_scan` passes suppressed findings through `should_send_digest(host, suppressed, now)` before dispatch. Policy: empty set → never fire (BC-022 regression guard); `BEACONRY_DIGEST_INTERVAL_S=0` → never throttle (pre-BC-036 behavior); otherwise fire on first-fire, content change (sha256 of sorted finding ids), OR interval elapsed. Per-host state in new `digest_state` table (idempotent CREATE in `init_db`). Audit events `digest_sent` + `digest_throttled` carry host, hash, and reason for forensics. First-fire + regression notifications are unaffected — this gate is strictly digest-local.
- **supports:** CAP-B003
- **verified by:**
  - `poc/tests/unit/test_digest_cadence.py`

### CAP-T070: `make findings` + Telegram notification surface exposure dimension
- **type:** technical
- **status:** active
- **origin:** BC-030
- **description:** `format_notification` prepends `🌐 INTERNET-FACING — Tier 1` for Tier 1 findings (Tier 0 KEV header still wins when both apply — the 🚨 header is the only one rendered). `make findings` CLI adds a 3-char `NET` column rendering 🌐 for exposed, — for LAN-only. Column reads from the schema-aware `load_findings`, so DBs that predate the column still render — gracefully.
- **supports:** CAP-B020
- **verified by:**
  - `poc/tests/integration/test_BC_030_internet_facing_detection.py`
  - `poc/tests/unit/test_findings_cli_internet_facing.py`

### CAP-T017: Orphan tests fail the gate with a copy-pasteable template
- **type:** technical
- **status:** active
- **origin:** BC-004
- **description:** When a test file lacks a `Covers:` block, verify-traceability emits a stderr message naming the file and a copy-pasteable `"""... Covers: - CAP-NNN: <name> """` template the developer can paste.
- **supports:** CAP-B006
- **verified by:**
  - `poc/tests/integration/test_BC_004_traceability.py`

### CAP-B038: Operator manages beaconry on a remote hub over HTTP only
- **type:** business
- **status:** active
- **origin:** BC-055 — homelab key user runs the hub on hetzner-staging (Tailscale) and manages beaconry from their laptop with no SSH.
- **description:** Every operator action (read findings, read audit, see fleet, trigger a scan, change a finding's status) is exposed as a bearer-authed HTTP endpoint on the hub. `~/.config/beaconry/cli.yml` carries `hub_url` + `hub_token`; env (`HUB_URL`/`HUB_TOKEN`) overrides YAML; empty env doesn't clobber YAML. Operator CLIs auto-route: non-localhost `hub_url` → HTTP; localhost + compose up → `docker exec` (unchanged); else local DB file. No SSH anywhere.
- **supports:** CAP-B023
- **verified by:**
  - `poc/tests/integration/test_BC_055_http_cli.py`

### CAP-T109: Hub HTTP management surface
- **type:** technical
- **status:** active
- **origin:** BC-055
- **description:** `GET /findings` (query: `status`, `host`, `limit`), `GET /audit` (query: `last`, `since`, `until`, `event`, `cve`, `host`, `finding`), `GET /fleet` (per-host aggregates, sorted by last_seen_ts desc), `POST /scan/trigger` (proxies to the scout's compose-DNS URL), and `PATCH /findings/{id}` (status mutation) — all gated by `require_cli_token` (reads `HUB_CLI_TOKEN`). The `/scan` ingest endpoint is gated separately by `require_scout_token` (reads `HUB_SCOUT_TOKEN`) so scout-side and CLI-side token leaks have independent blast radius. Health endpoints (`/livez`, `/readyz`, `/healthz`, `/startupz`) never declare any dependency. Startup warning fires when hub is bound non-loopback with either token empty.
- **supports:** CAP-B038
- **verified by:**
  - `poc/tests/integration/test_BC_055_http_cli.py`

### CAP-T110: Operator-CLI config loader + HTTP routing
- **type:** technical
- **status:** active
- **origin:** BC-055
- **description:** `poc/scripts/cli_config.py` exposes `CliConfig` + `load_cli_config`. Default YAML path respects `XDG_CONFIG_HOME` → `~/.config/beaconry/cli.yml`. Env overrides YAML; empty env values are skipped so a shell default of `export HUB_URL=""` can't clobber the config. `poc/scripts/cli_http.py::choose_source()` returns `"http" | "local-compose" | "local-file"` based on `is_localhost_url(hub_url)` + whether the compose hub is running. `findings.py` + `audit.py` + `status.py` + `scan_cli.py` all dispatch on `choose_source`. FOLLOW stays on the compose path (streaming is a follow-up).
- **supports:** CAP-B038
- **verified by:**
  - `poc/tests/integration/test_BC_055_http_cli.py`

### CAP-T111: PATCH /findings/{id} mutation endpoint
- **type:** technical
- **status:** active
- **origin:** BC-055
- **description:** `PATCH /findings/{finding_id}` with `{"status": "snoozed"|"accepted"|"fixed"|"open"}` updates the DB row, appends a `status_change` audit event with `source="http"`, returns the updated row. 404 on unknown id, 400 on a status outside the operator-allowed set (system states `regressed` / `verification_failed` are not operator-initiated). Backs `make set-status FINDING=<id> STATUS=<status>` which always goes through HTTP — no local-DB fallback for mutations.
- **supports:** CAP-B038
- **verified by:**
  - `poc/tests/integration/test_BC_055_http_cli.py`

### CAP-B039: Homelab operator installs beaconry via Ansible with safe credentials handling
- **type:** business
- **status:** active
- **origin:** BC-056 — homelab key user asked for an authoritative install guide covering Ansible + credentials guidance, built on BC-055's short-name + split-token conventions.
- **description:** `docs/INSTALL.md` is the authoritative install guide: Prerequisites → Quick-start single-host → Multi-host (Tailscale) → Operator CLI → Ansible → Credentials → Verification → Troubleshooting. The Ansible section ships a copyable role tree (`beaconry_hub` + `beaconry_scout`), a complete playbook using `community.docker`, and a Vault example that scopes the three secret tokens (`telegram_bot_token`, `hub_scout_token`, `hub_cli_token`). The Credentials section enumerates three safe places (`.env` 0600, Ansible Vault, systemd `EnvironmentFile=`) and explicitly says tokens must never be committed to git. The up-front banner repeats the rule so operators who skim see it before any step.
- **supports:** CAP-B023
- **verified by:**
  - `poc/tests/integration/test_BC_056_install_doc.py`

### CAP-T112: `verify-install-doc` drift lint
- **type:** technical
- **status:** active
- **origin:** BC-056
- **description:** `poc/scripts/verify_install_doc.py` runs three checks against `docs/INSTALL.md`: (1) every backticked uppercase-identifier shaped like an env var must have a row in `docs/VARIABLES.md`; (2) every `make <target>` referenced must appear in Makefile `.PHONY:`; (3) every `poc/…` or `docs/…` path mentioned must exist on disk. Install-destination paths (`/etc/…`, `~/…`) and `roles/…` Ansible-example paths are definitionally not in the repo and are ignored. Wired into `make check`.
- **supports:** CAP-B039
- **verified by:**
  - `poc/tests/integration/test_BC_056_install_doc.py`

### CAP-B040: Operator with multiple scouts knows which host a notification came from
- **type:** business
- **status:** active
- **origin:** BC-057 — operator's real environment runs ≥2 scouts (bb-82jq + server2); "where is this?" becomes the primary triage question.
- **description:** Two paired changes: (1) `make findings HOST=<label>` filters DB rows to a single scout's findings, composing with existing STATUS filter. (2) When the hub has findings from ≥2 distinct hosts (detected at render time via `SELECT COUNT(DISTINCT host) FROM findings`), Telegram notifications prepend `🖥️ <code><host></code>` as the lead line above the KEV / internet-facing / severity rows. Single-host operators see zero change — the hub promotes the prefix automatically when a second scout starts posting.
- **supports:** CAP-B023
- **verified by:**
  - `poc/tests/integration/test_BC_057_multi_host.py`

### CAP-T113: `make findings HOST=` filter
- **type:** technical
- **status:** active
- **origin:** BC-057
- **description:** `poc/scripts/findings.py` grows a `--host` argparse flag; `load_findings(db, status=None, host=None)` composes host + status via AND in the SQL `WHERE`. The compose-container inline-script path accepts a parallel `##HOST_FILTER##` placeholder. The BC-055 HTTP path forwards `host=` as a query-string param. Makefile propagates `HOST=` through to `--host`.
- **supports:** CAP-B040
- **verified by:**
  - `poc/tests/integration/test_BC_057_multi_host.py`

### CAP-T114: Multi-host-aware notification lead line
- **type:** technical
- **status:** active
- **origin:** BC-057
- **description:** `is_multi_host_inventory()` runs a read-only sqlite query at notification time; `format_notification` prepends `🖥️ <code>host</code>` when True. No new env dial — the behavior switches automatically on inventory shape. Single-host operators never see the extra line (zero visual regression).
- **supports:** CAP-B040
- **verified by:**
  - `poc/tests/integration/test_BC_057_multi_host.py`

### CAP-B041: Operator can diagnose stale-notification taps + has consistent audit coverage
- **type:** business
- **status:** active
- **origin:** BC-058 — debugging the "Finding not found or already resolved" Telegram error surfaced four interacting bugs across the mutation + notification paths.
- **description:** Every mutation path now emits the same rich `status_change` audit payload (`{finding_id, host, cve_id, old_status, new_status, source}`), so `make audit CVE=X` enrichment is uniform whether the operator tapped a Telegram button or ran `make set-status`. Every successful notification send emits a `notify_sent` audit event (digest sends emit `notify_digest_sent` with the full suppressed-finding_ids list), so stale-tap debugging is possible after the fact. `set_status` raises `FindingNotFound` instead of silently no-op'ing on missing ids. The Telegram "Finding not found" error now includes the finding_id prefix + a hint about stale notifications — operator can tell at a glance that this is an old message safe to ignore.
- **supports:** CAP-B023
- **verified by:**
  - `poc/tests/integration/test_BC_058_cleanup.py`

### CAP-T115: set_status fail-fast + rich audit shape
- **type:** technical
- **status:** active
- **origin:** BC-058
- **description:** `set_status(finding_id, status, *, source="telegram")` first looks up the row; raises `FindingNotFound` if the id has no row (pre-BC-058 it UPDATE'd 0 rows and emitted a status_change audit event anyway). Loads `host`/`cve_id`/`old_status` from the lookup and emits the audit event with the same shape BC-055's `PATCH /findings/{id}` handler uses: `{finding_id, host, cve_id, old_status, new_status, source}`. All existing callers (Telegram button handlers) pass `source="telegram"`; the HTTP PATCH handler uses `source="http"`.
- **supports:** CAP-B041
- **verified by:**
  - `poc/tests/integration/test_BC_058_cleanup.py`

### CAP-T116: notify_sent + notify_digest_sent audit events
- **type:** technical
- **status:** active
- **origin:** BC-058
- **description:** `_emit_notify_sent(finding, kind)` appends a `notify_sent` audit event with `{finding_id, cve_id, host, kind}` after every successful `_dispatch_finding`/`_dispatch_regression`/`_dispatch_heartbeat`. `record_digest_sent` additionally emits `notify_digest_sent` with the full list of suppressed finding_ids. Drive-by: `format_notification` accepts an optional `multi_host: bool | None` hint so dispatchers rendering a batch don't re-query the DB per message. When the hint is None, falls back to today's `is_multi_host_inventory()` lookup.
- **supports:** CAP-B041
- **verified by:**
  - `poc/tests/integration/test_BC_058_cleanup.py`

### CAP-T117: Diagnosable Telegram "Finding not found" error
- **type:** technical
- **status:** active
- **origin:** BC-058
- **description:** The pre-BC-058 message was "⚠️ Finding not found or already resolved." — the "or already resolved" clause implied a terminal-state check that didn't exist, and the message had no clue which finding failed. Post-BC-058: `⚠️ Snooze failed — finding <code>f0b442fb…</code> is not in the current DB. Likely a stale notification from before a scan reset. Safe to ignore.` Includes the finding_id prefix (first 8 chars) + the action verb + an explicit stale-notification hint.
- **supports:** CAP-B041
- **verified by:**
  - `poc/tests/integration/test_BC_058_cleanup.py`

### CAP-B042: Operator sees the CVE in the Telegram "Finding not found" error
- **type:** business
- **status:** active
- **origin:** BC-059 — finishes BC-058's diagnosable-error story. Operators think in CVE ids, not sha256 prefixes.
- **description:** When a Telegram button tap lands on a `finding_id` not in the current DB, the hub's error message names the CVE from the callback_data instead of just the hex-id prefix. Pre-BC-059 messages still in the chat history (no CVE in their callback_data) fall back to the id-prefix wording via BC-058's existing branch. Operator can tell at a glance whether a stale notification is from an issue they already handled elsewhere.
- **supports:** CAP-B023
- **verified by:**
  - `poc/tests/integration/test_BC_059_callback_data_cve.py`

### CAP-T118: callback_data reshape with CVE suffix + 64-byte guard
- **type:** technical
- **status:** active
- **origin:** BC-059
- **description:** `_cb_data(action, finding, *extra)` builds callback_data as `{action}:{finding.id}[:{extra}]*[:{cve_id}]`. CVE lives at the LAST segment so an on-the-wire parser can `parts[-1]` it; existing positional parsers (snooze hours at index 2) keep working. Defensive 64-byte guard drops the CVE if it would blow Telegram's `callback_data` cap. All five button sites (Fix / Snooze / Accept in `_kb_finding`, Fix + Accept in `_kb_regression`, Verify in the fix flow) go through the helper.
- **supports:** CAP-B042
- **verified by:**
  - `poc/tests/integration/test_BC_059_callback_data_cve.py`

### CAP-T119: Backward-compatible CALLBACK_RE
- **type:** technical
- **status:** active
- **origin:** BC-059
- **description:** `CALLBACK_RE` now matches both pre-BC-059 3-part and post-BC-059 4-part callback_data. The trailing CVE segment is `(?::[A-Z][A-Z0-9-]{2,30})?` — permissive enough for every observed beaconry primary id (CVE / GO / AVG / RUSTSEC-flavored), strict enough to reject arbitrary lowercase or over-length garbage. Messages in operators' chat history from before BC-059 still route to the handler cleanly.
- **supports:** CAP-B042
- **verified by:**
  - `poc/tests/integration/test_BC_059_callback_data_cve.py`

### CAP-B043: Operator can see which notifications failed in the audit log
- **type:** business
- **status:** active
- **origin:** BC-060 — BC-058 recorded every successful send; this closes the gap by recording failures too. Token revoked / network drop / rate limit → operator can grep the audit log instead of fishing in container logs.
- **description:** `make audit EVENT=notify_sent_failed` surfaces every failed dispatch with `{finding_id, cve_id, host, kind, reason}`. Digest failures emit `notify_digest_sent_failed` with the full `finding_ids` list that didn't land. Reasons use the exception class name as the primary signal (`HTTPError: 401 Unauthorized` vs `URLError: Name or service not known`) and truncate the message to 200 chars to bound audit size. Audit is additive — exceptions still propagate to callers' existing error-handling.
- **supports:** CAP-B023
- **verified by:**
  - `poc/tests/integration/test_BC_060_notify_failed.py`

### CAP-T120: notify_sent_failed + notify_digest_sent_failed audit events
- **type:** technical
- **status:** active
- **origin:** BC-060
- **description:** `_emit_notify_sent_failed(finding, kind, exc)` wraps each `_dispatch_*` call in `notify`, `notify_regression`, `notify_heartbeat`. `send_digest` has an analogous wrapper emitting `notify_digest_sent_failed` with the full suppressed finding_ids list. `_format_fail_reason(exc)` composes `ClassName: short_msg[:200]`. All exceptions re-raise after the audit append so caller behavior (log.warning in process_sbom_entry, counter increments in heartbeat_sweep) is unchanged.
- **supports:** CAP-B043
- **verified by:**
  - `poc/tests/integration/test_BC_060_notify_failed.py`
