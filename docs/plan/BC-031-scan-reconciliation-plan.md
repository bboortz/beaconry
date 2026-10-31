# BC-031 — Scan reconciliation + regression detection

*Branch: `feat/BC-031-scan-reconciliation`*
*Date: 2026-10-31*
*Story: `docs/stories/BC-031-scan-reconciliation-and-regression-detection.md`*
*Memory: `~/.claude/.../memory/fix_is_not_forget.md`*

## Situation

Pre-BC-031, the Fix button was fire-and-forget. `save_finding` uses `INSERT OR IGNORE` — once a finding row exists with `status=fixed`, subsequent scouts cycles that still match the same CVE are silently ignored. Operator's attack window reopens without any notification. Operator feedback 2026-10-31 after the AVG-2909 real-world fire:

> "I would prefer to have a rescan for everything we pressed fix it to ensure the fix was really applied and it worked as expected without any regression. Plus it might become introduced by other factors. So ensure the fix works also long term. A fix it button should not be a 'forget it'."

## Fix

New function `reconcile_scan(host, scanned_ids: set[str]) -> (regressed, auto_closed)` called from `receive_scan` after `process_sbom_entry`. Applies transition rules:

| Prev status | In current scan? | New status |
|---|---|---|
| `open` | yes | unchanged |
| `open` or `regressed` | no | `fixed_verified` (auto-close) |
| `fixed` / `fixed_verified` | yes | **`regressed`** + 🔥 Telegram alarm |
| `fixed` / `fixed_verified` | no | unchanged (fix holding) |
| `snoozed` / `accepted` | any | unchanged (operator decided) |

## Scope

- **Modified:** `poc/hub/hub.py` — `RECONCILE_ENABLED` env; new DB column `last_fixed_ts` + idempotent migration; `set_status` writes `last_fixed_ts` on fix transitions; new `reconcile_scan`, `format_regression`, `notify_regression`; `receive_scan` calls reconcile after ingest.
- **Modified:** `poc/scripts/findings.py` — add `regressed` to `STATUS_ORDER` as highest priority.
- **Modified:** `poc/docker-compose.yml` — propagate `BEACONRY_RECONCILE_ENABLED`.
- **Modified:** `Makefile` help — add `regressed` to the STATUS enum line (BC-016 docs-drift lint caught it).
- **Modified:** `docs/CAPABILITIES.md` — register CAP-B021, CAP-T071, CAP-T072, CAP-T073.
- **Modified:** `docs/stories/INDEX.md` — flip BC-031 to shipped.
- **New:** `poc/tests/integration/test_BC_031_scan_reconciliation.py` — 11 tests covering all 6 Gherkin scenarios + the "two-container regression" simulated scenario + opt-out + host isolation + notification shape.

## Design decisions

- **Default ON** (`BEACONRY_RECONCILE_ENABLED=1`): "fix is not forget" is the whole point. Operator opts out with `=0` for self-hosted scenarios where the long-term diff feels like surveillance.
- **Scoped per-host**: a scan from `host-a` cannot touch `host-b` findings. Multi-host operators retain independent state.
- **No new DB status in migration path**: `regressed` is a new value in the existing `status TEXT` column — no CHECK constraint, nothing to migrate at schema level. Only the `last_fixed_ts` column is new.
- **`set_status` writes `last_fixed_ts` automatically** on transition to `fixed`/`fixed_verified` so caller can't forget. Audit event + display timestamp both derived from it.
- **🔥 alarm has distinct keyboard**: `[Re-fix]` + `[Accept risk]` only (no Snooze — regression deserves attention, not deferral).
- **Reconciliation runs on EVERY scan, not just scans with new findings**: a regression might be the only interesting thing a scan surfaces. Hooked unconditionally after `process_sbom_entry`.
- **No `fix_held_through_scan` event** shipped in V1 (would create too much audit noise). The Gherkin scenario marks it OPTIONAL; left out for cleanliness.
- **Opt-out is global, not per-host or per-finding**: simplest configuration. If operator wants finer control, they tap Accept risk which is already respected.

## Breaking changes

Enumerated under the three dimensions per `docs/plan/_template.md § Breaking changes`:

### 1. API / return shape
`receive_scan` return shape gains `regressed` + `auto_closed` keys. Scout and e2e tests tolerate extra keys. One existing unit test (`test_receive_scan_empty_sbom_list`) updated to assert the new shape.

### 2. Data model (schema + legacy rows)
New column `last_fixed_ts INTEGER DEFAULT 0`. **Legacy fixed/fixed_verified rows written pre-BC-031** default to 0 → regression notifications for them would render "regressed since **unknown**". This would be a bad-UX moment for a key user whose system has prior fix history.

**Migration (in-branch, not a close-gap):** `init_db` runs an idempotent `UPDATE findings SET last_fixed_ts = found_at WHERE status IN ('fixed','fixed_verified') AND last_fixed_ts = 0 AND found_at > 0`. Backfills from `found_at` (approximation — fix landed at or after find). Three test cases guard this: legacy row gets backfilled; modern row with real ts not touched; open rows untouched.

*Discovered during live-stack audit, fixed in this branch, retrospectively planned here per the breaking-changes-in-planning discipline.*

### 3. Behavioral semantics
`set_status(id, "fixed")` and `set_status(id, "fixed_verified")` now implicitly write `last_fixed_ts`. Any external caller that relied on `set_status` as a pure status-only write won't see a bug, just an extra column populated. Scan reconciliation changes the meaning of "status=fixed" from "done forever" to "under surveillance forever" — this is the whole point of BC-031 and is operator-visible via the `BEACONRY_RECONCILE_ENABLED=0` opt-out. Operators who want the pre-BC-031 "status=fixed means done" semantics can set that env.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario 1 — Manual fix absent from scan auto-closes to fixed_verified | integration test | `test_BC_031_scan_reconciliation.py::test_open_finding_absent_from_scan_auto_closes` | ✓ |
| Scenario 2 — fixed finding returns → regressed + loud alarm | integration test | `test_BC_031_scan_reconciliation.py::test_fixed_finding_reappears_triggers_regression` | ✓ |
| fixed_verified also triggers regression | integration test | `test_BC_031_scan_reconciliation.py::test_fixed_verified_reappears_also_triggers_regression` | ✓ |
| Scenario 4 — snoozed untouched | integration test | `test_BC_031_scan_reconciliation.py::test_snoozed_finding_untouched_by_reconciliation` | ✓ |
| Scenario 5 — accepted untouched | integration test | `test_BC_031_scan_reconciliation.py::test_accepted_finding_untouched` | ✓ |
| Scenario 6 — regressed → re-fix held → fixed_verified | integration test | `test_BC_031_scan_reconciliation.py::test_regressed_absent_from_scan_auto_closes_to_fixed_verified` | ✓ |
| Config opt-out works | integration test | `test_BC_031_scan_reconciliation.py::test_reconcile_disabled_is_a_noop` | ✓ |
| Host isolation (multi-host operators) | integration test | `test_BC_031_scan_reconciliation.py::test_reconcile_scoped_to_host` | ✓ |
| Simulated 2-container regression scenario (user asked for this) | integration test | `test_BC_031_scan_reconciliation.py::test_simulated_regression_two_container_scenario` | ✓ |
| 🔥 notification body shape | integration test | `test_BC_031_scan_reconciliation.py::test_format_regression_includes_fix_date_and_fire_header` | ✓ |
| Missing-fix-date rendered gracefully | integration test | `test_BC_031_scan_reconciliation.py::test_format_regression_handles_missing_fix_date_gracefully` | ✓ |
| 4 CAPs registered + bidirectional traceability | verify-traceability | CAPABILITIES.md diff | ✓ |
| make check green | multi-gate | `make check` | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |

## Rollout plan

1. Merge to dev
2. `make live-rebuild` (BC-030-era discipline applies here)
3. First scan after rebuild runs reconcile against existing 8 findings → most should auto-close (we have 3 that are already `fixed` from earlier Telegram fix taps, those stay `fixed`; 2 open that are still vulnerable → stay `open`; etc).

If operator finds the reconciliation surveillance-y: `BEACONRY_RECONCILE_ENABLED=0 docker compose up -d --build hub`.
