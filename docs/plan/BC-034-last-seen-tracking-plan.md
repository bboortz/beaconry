# BC-034 — Hub persists last_seen_ts per finding

*Branch: `feat/BC-034-last-seen-tracking`*
*Date: 2026-10-31*
*Story: `docs/stories/BC-034-last-seen-tracking.md`*

See the story's `## Breaking changes` matrix for the three-dimension analysis + migration (both inlined there to keep the plan + story tight; this plan references rather than duplicates).

## Scope summary

- `poc/hub/hub.py`: new column + backfill migration + `bump_last_seen` helper + wire-in from `receive_scan`
- `poc/tests/unit/test_last_seen_tracking.py`: 10 Gherkin-mapped unit tests
- `docs/CAPABILITIES.md`: CAP-T077

## Shipping evidence

| Scenario / goal | Pointer | Status |
|---|---|---|
| Column exists after init_db | `test_last_seen_tracking.py::test_init_db_adds_last_seen_ts_column` | ✓ |
| Migration is idempotent | `test_last_seen_tracking.py::test_init_db_migration_is_idempotent` | ✓ |
| Legacy rows backfill from found_at | `test_last_seen_tracking.py::test_legacy_rows_backfill_last_seen_from_found_at` | ✓ |
| Present finding updated | `test_last_seen_tracking.py::test_bump_last_seen_updates_present_findings` | ✓ |
| Absent finding left alone | `test_last_seen_tracking.py::test_bump_last_seen_leaves_absent_findings_alone` | ✓ |
| Scoped to host | `test_last_seen_tracking.py::test_bump_last_seen_scoped_to_host` | ✓ |
| Empty set no-op | `test_last_seen_tracking.py::test_bump_last_seen_empty_set_is_noop` | ✓ |
| Updates all statuses | `test_last_seen_tracking.py::test_bump_last_seen_updates_regardless_of_status` | ✓ |
| Cold-boot safety | `test_last_seen_tracking.py::test_bump_last_seen_tolerates_missing_table` | ✓ |
| New row backfill via init_db | `test_last_seen_tracking.py::test_save_finding_sets_last_seen_on_new_row` | ✓ |
| CAP-T077 registered + bidirectional | verify-traceability | ✓ |
| make check green | `make check` | ✓ |
| Memory drift pattern added if applicable | N/A | ✓ |
