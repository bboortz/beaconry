---
id: BC-034
title: Hub persists last_seen_ts per finding (prerequisite data for reminder features)
status: shipped
created: 2026-10-31
---

# BC-034: Hub persists last_seen_ts per finding

## Narrative

Operator question 2026-10-31: *"Are we storing when we saw a specific software and version the last time?"* No. Pre-BC-034 the DB had `found_at` (first ingestion) and BC-031's `last_fixed_ts` (fix transition), but nothing tracking continuous observation of a finding across scans. `save_finding` uses `INSERT OR IGNORE` → repeat sightings are no-ops → first-sighting timestamp becomes effectively the only timestamp.

Consequences the question surfaced:
- The heartbeat / digest-refresh IDEAs we just discussed CAN'T fire without this data — "finding has been open for N days" needs continuous-observation tracking, not just first-sighting.
- The regression body can't say "last seen vulnerable on X, re-emerged on Y" — just "fixed on X".
- Database can accumulate ghost rows: a finding sitting at `status=open` could be from a scan 6 months ago OR a scan 1 minute ago; no way to tell.

BC-034 is pure data plumbing — adds the column, backfills legacy rows, writes it on every scan. No operator-visible behavior change. Prerequisite for the heartbeat / digest-refresh / stale-row-detection features that will consume it.

## Expected outcome

- New column `last_seen_ts INTEGER DEFAULT 0` on findings table.
- Every scan: `bump_last_seen(host, scanned_ids)` runs → every finding present in the scan gets `last_seen_ts = NOW()`.
- Legacy rows (last_seen_ts=0): idempotent migration backfills from `found_at`.
- Independent of `BEACONRY_RECONCILE_ENABLED` — this is inventory bookkeeping, not surveillance.
- No operator-visible UI change in this story.

## Acceptance criteria (Gherkin)

```gherkin
Feature: last_seen_ts tracking

  Scenario: Column exists after init_db
    Given a fresh DB
    When hub.init_db() runs
    Then the `findings` table has a `last_seen_ts INTEGER` column

  Scenario: Legacy rows backfilled from found_at
    Given a pre-BC-034 row with found_at=T and last_seen_ts=0
    When init_db runs
    Then last_seen_ts is set to T

  Scenario: Finding in current scan gets last_seen_ts updated
    Given finding X exists in the DB
    When bump_last_seen(host, {X.id}) is called
    Then X.last_seen_ts is set to now

  Scenario: Finding NOT in current scan is left alone
    Given finding Y exists in the DB
    When bump_last_seen(host, {some_other_id}) is called
    Then Y.last_seen_ts is unchanged

  Scenario: Scoped to host
    Given findings X (host-a) and Y (host-b) exist with the same id (collision hypothetical)
    When bump_last_seen("host-a", {X.id, Y.id}) is called
    Then only X.last_seen_ts updates

  Scenario: Updates regardless of status
    Given snoozed / accepted / fixed findings present in scan
    When bump_last_seen runs
    Then all get updated — operator deferral doesn't stop inventory bookkeeping

  Scenario: Cold-boot safety
    Given the findings table doesn't exist
    When bump_last_seen is called
    Then no exception; function is a no-op
```

## Breaking changes

Per `docs/plan/_template.md § Breaking changes`:

### 1. API / return shape
None. `receive_scan` return shape unchanged.

### 2. Data model
New column `last_seen_ts INTEGER DEFAULT 0`. Legacy rows (pre-BC-034) would show `last_seen_ts=0` → downstream features would misinterpret as "last seen in 1970". **Migration (in-branch per discipline):** idempotent `UPDATE findings SET last_seen_ts = found_at WHERE last_seen_ts = 0 AND found_at > 0` runs in `init_db` on every boot. Approximation (last_seen = first_seen for legacy rows) but strictly better than "unknown".

### 3. Behavioral semantics
`receive_scan` does one additional SQL UPDATE per scan (`O(scanned_findings)` rows per scan — typically ≤50 for a self-hosted operator). Negligible performance cost. No operator-visible notification shape, status transition, or button behavior changes.

Independent of `BEACONRY_RECONCILE_ENABLED`: inventory bookkeeping has no surveillance implication so it's not opt-out-configurable. If a future consumer (heartbeat, digest refresh) needs opt-out, that story owns its own flag.

## Capabilities delivered

- **CAP-T077: Hub persists `last_seen_ts` per finding** (technical, active) — prerequisite data for digest-refresh / heartbeat / stale-row-detection stories.

## Scope

- `poc/hub/hub.py` — add column + migration + backfill in `init_db`; new `bump_last_seen` helper; call from `receive_scan` after candidates are built.
- `poc/tests/unit/test_last_seen_tracking.py` — 10 unit tests covering all 7 Gherkin scenarios + migration idempotence + save_finding integration + cold-boot safety.
- `docs/CAPABILITIES.md` — CAP-T077.
- `docs/stories/INDEX.md` — BC-034 row.

## Shipping evidence

| Scenario / goal | Pointer | Status |
|---|---|---|
| Column exists after init_db | `test_last_seen_tracking.py::test_init_db_adds_last_seen_ts_column` | ✓ |
| Migration is idempotent | `test_last_seen_tracking.py::test_init_db_migration_is_idempotent` | ✓ |
| Legacy rows backfill from found_at | `test_last_seen_tracking.py::test_legacy_rows_backfill_last_seen_from_found_at` | ✓ |
| Present finding gets updated | `test_last_seen_tracking.py::test_bump_last_seen_updates_present_findings` | ✓ |
| Absent finding left alone | `test_last_seen_tracking.py::test_bump_last_seen_leaves_absent_findings_alone` | ✓ |
| Scoped to host | `test_last_seen_tracking.py::test_bump_last_seen_scoped_to_host` | ✓ |
| Empty set is no-op | `test_last_seen_tracking.py::test_bump_last_seen_empty_set_is_noop` | ✓ |
| Updates all statuses (snoozed/accepted/fixed) | `test_last_seen_tracking.py::test_bump_last_seen_updates_regardless_of_status` | ✓ |
| Cold-boot safety (missing table) | `test_last_seen_tracking.py::test_bump_last_seen_tolerates_missing_table` | ✓ |
| New row backfill (save_finding integration) | `test_last_seen_tracking.py::test_save_finding_sets_last_seen_on_new_row` | ✓ |
| CAP-T077 registered + bidirectional | verify-traceability | CAPABILITIES.md diff | ✓ |
| make check green | multi-gate | `make check` | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |

## Links

- Enables (future BCs): **BC-035 (heartbeat for open findings)**, **BC-036 (digest refresh schedule)**, **BC-037 (stale-row detection and purge)**.
- Related: BC-031 (reconcile_scan — same data flow), BC-029 (audit log — could emit `last_seen_bumped` events in a future story if needed, but not shipped here).
