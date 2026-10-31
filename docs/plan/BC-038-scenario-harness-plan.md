# BC-038 — Live-stack scenario harness

*Branch: `feat/BC-038-scenario-harness`*
*Start date: 2026-10-31*
*Story: `docs/stories/BC-038-scenario-harness.md`*

---

## Situation

Unit tests mock grype, scout, and Telegram. BC-017's `test-e2e` covers the probe waterfall. Nothing exercises the real hub ingest flow — dedup+cap, save_finding, reconcile, digest throttle, heartbeat — under deterministic state mutations. We discover BC-030-shaped bugs only during manual verification (the "no notification" / "missing NET column" findings this session).

---

## Problems to solve

- Pipeline-behavior verification is ad-hoc and non-reproducible.
- Clock-driven features (heartbeat INTERVAL_S, digest INTERVAL_S) can't be verified without 24h of wall-clock time.
- Real grype matches drift as the CVE DB updates — not deterministic.

---

## Goals

1. One command (`make test-scenarios`) runs 10 scenarios covering every operator-facing promise shipped so far.
2. Deterministic — no reliance on real grype, real clock, or network.
3. Fast — full suite completes under 30 s on dev laptop.
4. Extensible — adding a new scenario is one new file subclassing `Scenario`, zero changes to the runner's dispatch.
5. Safe — test endpoints are env-gated at route registration time; absent in production.

---

## Scope

### Hub refactor (small, invariance-preserving)

- Extract `_ingest_candidates(host, candidates, *, now_ts)` from `receive_scan` — reused by both the real path and `/test/scan`. Preserves the existing dedup+cap+save+notify+reconcile+digest flow verbatim.
- Thread `now_ts` through:
  - `receive_scan` (new param, defaults to `int(time.time())`)
  - `save_finding` (reads `now_ts` for `found_at`)
  - `bump_last_seen` (uses passed `now_ts`)
  - `heartbeat_sweep` (new param)
  - `record_digest_sent` + `should_send_digest` already parameterized
  - Reconcile `set_status` keeps its own `time.time()` (fix timestamps are inherently real-time).

### Test-mode endpoints

- `BEACONRY_TEST_MODE = os.environ.get("BEACONRY_TEST_MODE") == "1"`
- At app-build time, `if TEST_MODE:` block registers three routes:
  - `POST /test/wipe` — delete DB file + audit.log, re-run `init_db`.
  - `POST /test/scan` — body `{host, candidates: [...], port_exposure, at}` → feeds `_ingest_candidates` directly.
  - `POST /test/sweep` — body `{at}` → calls `heartbeat_sweep(now_ts=at)`.
- When TEST_MODE is unset or 0, routes are not registered → requests return 404.

### Scenario harness

`poc/scenarios/`:

- `_context.py` — `ScenarioContext` with `wipe()`, `inject_scan()`, `trigger_sweep()`, `find()`, `audit_events()`, `assert_status()`, `assert_tier()`, `assert_audit_fired()`.
- `_base.py` — abstract `Scenario(ABC)` with `name`, `description`, `order`, `run(ctx)`.
- `s01_fresh_cve.py` … `s10_last_seen_bump.py` — one file per scenario.
- `_registry.py` — explicit list `SCENARIOS = [...]`. Explicit over autodiscovery for debuggability.
- `__main__.py` — CLI entry: `python -m poc.scenarios` or filtered `python -m poc.scenarios --only s01`.

### Lifecycle

- `poc/scenarios/run.sh` — isolated compose project `beaconry-scenarios`, hub-only, `BEACONRY_TEST_MODE=1`, `BEACONRY_MOCK=1` (skip grype DB).
- Health gate: wait for `/readyz` on 127.0.0.1:8004 (different port from main stack).
- Invoke `python -m poc.scenarios`.
- Teardown.

### Makefile

- `make test-scenarios` → calls `poc/scenarios/run.sh`.
- Appended to `check-full` behind `BEACONRY_SKIP_SCENARIOS` env (default run).
- Help entry.

### Tests

- `poc/tests/unit/test_scenario_runner.py` — context primitives work against a tmp SQLite + mocked HTTP client.
- `poc/tests/integration/test_BC_038_scenario_harness.py` — spins up hub via FastAPI TestClient with TEST_MODE=1, runs each scenario end-to-end (same code the make target runs, just in-process).

### Docs

- `docs/CAPABILITIES.md` — CAP-T082, CAP-T083, CAP-B022.
- `docs/plan/IDEAS.md` — append the "/ultrareview linter for test endpoints" idea.

---

## Non-goals

- Autodiscovery of scenarios. Explicit import list keeps dispatch simple + grep-friendly.
- Running scenarios against a real grype DB. Deterministic > realistic for this harness.
- Chained scenarios (s02 depends on s01's state). Each scenario starts from a wiped DB.
- Covering BCs that have no operator-visible state transition (e.g. BC-004 traceability, BC-010 health-check split). Those are gate-tested already.

---

## Design decisions

- **Clock injection by parameter, not globals.** Every time-reading hub function gains `now_ts: int | None = None` with `now_ts = now_ts or int(time.time())`. Test endpoints pass the fake value. No module-level clock state → no risk of leaking fake time between tests.
- **Scenarios are classes, not functions.** `Scenario.run(ctx)` method with class attrs `name`, `order`, `description`. Lets a scenario carry per-type data (`CANDIDATE_PAYLOAD`) and extend via inheritance when scenarios share setup.
- **Explicit scenario list, not autodiscovery.** `SCENARIOS = [s01, s02, …]` in `_registry.py`. Grep-friendly, debuggable, trivially filterable. If the list grows past 50 we revisit.
- **`ScenarioContext` holds primitives, not business logic.** Scenarios compose assertions; the context never knows about CVEs or statuses specifically. Prevents the dispatch problem of "ctx.verify_fix_scenario()".
- **Hub-only compose stack.** Scout bypassed — scenarios post SBOMs directly. Reduces moving parts and inconsistency windows.
- **Rejected: inline `if scenario == 'xxx'` dispatch.** Would grow linearly with each scenario, breaks on reorder, mixes concerns. Each scenario is one file subclassing one ABC — zero central dispatch.
- **Rejected: pytest-based runner.** pytest's reporting is designed for test files, not operator-facing scenario runs; the harness needs clean one-line-per-scenario output for CI logs. Also decouples the gate from the test gate — operator can run scenarios without the full `make test` tax.

---

## Breaking changes

### 1. API / return shape

**None in production.** `receive_scan`'s external shape is unchanged. The new `_ingest_candidates` helper is private. New `/test/*` endpoints are **absent** when TEST_MODE is off.

### 2. Data model

**None.** No new tables or columns. Test endpoints exercise the existing schema.

### 3. Behavioral semantics

- Operators who do nothing see no change. TEST_MODE defaults to off; routes don't register; `make check` is unaffected.
- `make check-full` gains an additional ~30 s block running `test-scenarios`. Opt-out: `BEACONRY_SKIP_SCENARIOS=1`.
- Operators running real hub binaries now have three new routes *they can enable* via env var. Documented in `.env.example` with a warning about production exposure.

---

## Merge plan

1. All work on `feat/BC-038-scenario-harness`.
2. `make check` + `make check-full` pass.
3. PR to `dev`; Claude opens, user runs `/ultrareview`, Claude runs `/code-review`.
4. Merge strategy: fast-forward.
5. Post-merge: story → `shipped`, INDEX update, `make live-rebuild`, run `make test-scenarios` live.

---

## Rollout plan

Additive. New Makefile target, new env var, no schema change. On upgrade:
- Operators who do nothing: no visible change.
- Operators who want to run scenarios: `make test-scenarios`.
- Operators debugging a pipeline bug: inject a specific candidate via `BEACONRY_TEST_MODE=1` on their running hub + curl `/test/scan`.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| `make test-scenarios` runs 10 scenarios + reports results | integration | `test_BC_038_scenario_harness.py::test_full_suite_passes` | ✓ |
| Test endpoints 404 without flag | integration | `test_BC_038_scenario_harness.py::test_endpoints_404_without_flag` | ✓ |
| Test endpoints 200 with flag | integration | `test_BC_038_scenario_harness.py::test_endpoints_registered_with_flag` | ✓ |
| ScenarioContext primitives (find, assert_status, audit_events) | unit | `test_scenario_runner.py::test_context_assert_status_mismatch_raises` | ✓ |
| ScenarioContext wipe round-trip | unit | `test_scenario_runner.py::test_context_wipe_resets_db_and_audit` | ✓ |
| ScenarioContext audit filter + assert_audit_fired/absent | unit | `test_scenario_runner.py::test_context_audit_events_filter` | ✓ |
| Registry enumerates all 10 scenarios | unit | `test_scenario_runner.py::test_registry_contains_ten_scenarios` | ✓ |
| Scenario base class is abstract | unit | `test_scenario_runner.py::test_scenario_base_is_abstract` | ✓ |
| s01 fresh_cve | scenario | `s01_fresh_cve.FreshCveScenario.run` | ✓ |
| s02 fix_closes | scenario | `s02_fix_closes.FixClosesScenario.run` | ✓ |
| s03 regression | scenario | `s03_regression.RegressionScenario.run` | ✓ |
| s04 snooze_flow | scenario | `s04_snooze_flow.SnoozeFlowScenario.run` | ✓ |
| s05 kev_tier0 | scenario | `s05_kev_tier0.KevTier0Scenario.run` | ✓ |
| s06 internet_facing_tier1 | scenario | `s06_internet_facing_tier1.InternetFacingTier1Scenario.run` | ✓ |
| s07 digest_content_hash | scenario | `s07_digest_content_hash.DigestContentHashScenario.run` | ✓ |
| s08 heartbeat_sweep | scenario | `s08_heartbeat_sweep.HeartbeatSweepScenario.run` | ✓ |
| s09 multi_host_isolation | scenario | `s09_multi_host_isolation.MultiHostIsolationScenario.run` | ✓ |
| s10 last_seen_bump | scenario | `s10_last_seen_bump.LastSeenBumpScenario.run` | ✓ |
| Clock injection: now_ts threads through receive_scan/save_finding/heartbeat_sweep | integration | `test_BC_038_scenario_harness.py::test_full_suite_passes` (s08 + s10 both exercise injected now_ts) | ✓ |
| CAP-T082 + CAP-T083 + CAP-B022 registered + bidirectional | verify-traceability | `docs/CAPABILITIES.md` diff | ✓ |
| make check green | multi-gate | `make check` | ✓ |
| IDEAS.md appended with ultrareview-lint followup | manual | `docs/plan/IDEAS.md` entry | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

---

## Testing commands

```bash
make check
make check-full
make test-scenarios
# Filter one scenario:
BEACONRY_TEST_MODE=1 python -m poc.scenarios --only s01_fresh_cve
```
