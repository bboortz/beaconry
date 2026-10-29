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
- **origin:** BC-002
- **design:** memory/workflow.md
- **description:** When `BEACONRY_FIX_ENABLED` is unset (default), tapping [🔧 Fix now] shows "Fix execution coming in BC-003 — <cve> left as open" and does not run any Docker action. BC-003 flips the default to enable real fix execution.
- **verified by:**
  - `poc/tests/unit/test_hub_helpers.py`

### CAP-B004: Operator is not flooded on noisy scans
- **type:** business
- **status:** active
- **origin:** BC-002
- **design:** docs/plan/IDEAS.md § Interactive behavior / UX
- **description:** The per-run cap `BEACONRY_MAX_FINDINGS_PER_RUN` (default 5) ranks findings by (tier, -cvss) and sends only the top-N individually; the remainder is summarised in a single digest message. CVE ids shared across packages are deduplicated, keeping the highest-CVSS variant.
- **verified by:**
  - `poc/tests/unit/test_demo_dispatch.py`

### CAP-B005: Operator can self-test the Telegram wiring without a scan
- **type:** business
- **status:** active
- **origin:** BC-002
- **description:** `make demo ARGS=--self-test` sends a single canary message ("beaconry is alive") and exits 0. Lets the operator verify bot token + chat id without triggering a real scan.
- **verified by:**
  - `poc/tests/unit/test_demo_dispatch.py`

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
- **description:** With `BEACONRY_MAX_FINDINGS_PER_RUN=5`, 20 unique CVEs → 5 individual messages + 1 HTML digest message naming the remaining 15 (first 10 as bullets, plus "N more" if any).
- **supports:** CAP-B004
- **verified by:**
  - `poc/tests/unit/test_demo_dispatch.py`

### CAP-T016: verify-traceability enforces bidirectional links
- **type:** technical
- **status:** active
- **origin:** BC-004
- **description:** `poc/scripts/verify_traceability.py` parses `docs/CAPABILITIES.md` and `poc/tests/**/test_*.py` and errors on: missing Covers block, dangling CAP id, missing test file, unverified active CAP, bidirectional inconsistency. Exits 0 only when the matrix is clean.
- **supports:** CAP-B006
- **verified by:**
  - `poc/tests/integration/test_BC_004_traceability.py`

### CAP-T017: Orphan tests fail the gate with a copy-pasteable template
- **type:** technical
- **status:** active
- **origin:** BC-004
- **description:** When a test file lacks a `Covers:` block, verify-traceability emits a stderr message naming the file and a copy-pasteable `"""... Covers: - CAP-NNN: <name> """` template the developer can paste.
- **supports:** CAP-B006
- **verified by:**
  - `poc/tests/integration/test_BC_004_traceability.py`
