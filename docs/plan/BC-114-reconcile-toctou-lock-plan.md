# BC-114 — Reconcile TOCTOU lock

*Branch: `feat/BC-114-reconcile-toctou-lock`*
*Start date: 2026-11-09*
*Story: `docs/stories/BC-114-reconcile-toctou-lock.md`*

---

## Situation

`reconcile_scan` opens a connection, runs `SELECT ... FROM findings
WHERE host=?`, iterates the rows, UPDATEs per row, then `con.commit()`.
Python sqlite3 defaults to `isolation_level='deferred'` — the first
UPDATE auto-begins a transaction, but the SELECT runs outside any
transaction. A concurrent `save_finding` or `bump_last_seen` INSERT /
UPDATE can interleave between the SELECT and our UPDATEs, corrupting
the state we thought we were acting on.

Concrete race:

1. T=0:  reconcile SELECTs F with status=open, absent_count=THRESHOLD-1
2. T=5ms: scout B's `bump_last_seen` resets F's absent_count=0 (B saw F in its scan)
3. T=10ms: reconcile's per-row UPDATE sets absent_count=THRESHOLD, status=auto_fixed, close_reason=post_absent
4. Result: F silently auto-closed despite being present in B's scan.

---

## Problems to solve

- Reconcile can silently auto-close a finding another scan just observed.
- Operator sees `make findings` showing `auto_fixed` and has no audit trail
  explaining the race — the `finding_auto_fixed` audit event records a
  genuine-looking state transition.

---

## Goals

1. Reconcile acquires a write lock before SELECT (maps to Scenario
   "BEGIN IMMEDIATE wraps SELECT + UPDATEs").
2. Concurrent writers block until reconcile COMMITs (maps to the same
   Scenario via serialization semantics).
3. Lock-unavailable skips the cycle gracefully (maps to Scenario "Lock
   unavailable → skip cycle").
4. Existing reconcile behavior unchanged on success path (maps to
   Scenario "Existing auto-close path still works").

---

## Scope

- `poc/hub/hub.py::reconcile_scan`:
  - Set `con.isolation_level = None` to disable Python's implicit
    transaction.
  - Open with explicit `BEGIN IMMEDIATE` (grabs reserved lock immediately,
    blocks other writers, allows other readers).
  - Wrap existing SELECT + UPDATEs + commit in the transaction.
  - On `sqlite3.OperationalError` during BEGIN: log warning, return empty
    lists, close connection — skip cycle, no partial writes.
  - On exception during iteration: ROLLBACK, re-raise.
  - Final COMMIT becomes explicit (`con.execute("COMMIT")`).
- `docs/CAPABILITIES.md`: CAP-T216.
- Unit tests: `poc/tests/unit/test_BC_114_reconcile_toctou_lock.py`.
- `CHANGELOG.md` + `VERSION` → `0.20.7` (patch — correctness fix).

---

## Non-goals

- Serializing save_finding against itself (single statement — already
  atomic in SQLite).
- WAL mode migration.
- Lock-acquisition retry loop (next reconcile cycle retries naturally
  via the scan cadence).

---

## Design decisions

- **`BEGIN IMMEDIATE` over `BEGIN EXCLUSIVE`**: IMMEDIATE grabs a
  reserved (write) lock — enough to prevent concurrent writers while
  still allowing concurrent readers. EXCLUSIVE would block readers
  too, hurting `/healthz` latency on busy hubs.
- **`isolation_level=None` + explicit BEGIN/COMMIT**: Python's
  implicit transaction logic is quirky under `isolation_level='deferred'`
  — the SELECT is outside any transaction, so even an UPDATE's implicit
  BEGIN doesn't protect the SELECT's snapshot. Explicit control is
  reliable.
- **Skip cycle on lock contention**: next reconcile tick (bound by
  `SCAN_INTERVAL` on scout side) retries. Alternative was a retry
  loop with backoff, but reconcile isn't on the hot operator path
  — skipping is fine.
- **No new env knob**: SQLite's built-in 5s default lock timeout is
  appropriate. Adding `BEACONRY_RECONCILE_LOCK_TIMEOUT` would be
  bikeshedding at POC scale.

---

## Breaking changes

### 1. API / return shape

None. Reconcile's return type is unchanged.

### 2. Data model

None.

### 3. Behavioral semantics

- Pre-BC-114: reconcile races could auto-close findings another scan
  observed (silent false positive).
- Post-BC-114: reconcile serializes against writers; false positives
  closed.
- New behavior under extreme contention: a reconcile cycle may be
  skipped (logged, not silent). Next cycle retries. Visible in
  `make audit` if an operator looks closely; otherwise invisible.

---

## Merge plan

1. All work on `feat/BC-114-reconcile-toctou-lock`.
2. `make check` passes.
3. PR to `dev`, squash merge.
4. Post-merge: INDEX + story → shipped.

---

## Rollout plan

Additive. No operator action. Backwards compatible.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario "Existing auto-close path still works" | unit | `poc/tests/unit/test_BC_114_reconcile_toctou_lock.py::test_reconcile_scan_still_auto_closes_absent_finding` | ✓ |
| Scenario "Lock unavailable → skip cycle" | unit | `test_reconcile_skips_cycle_when_lock_unavailable` | ✓ |
| Scenario "BEGIN IMMEDIATE wraps SELECT + UPDATEs" | unit | `test_reconcile_uses_begin_immediate` | ✓ |
| Baseline bump-below-threshold preserved | unit | `test_reconcile_scan_still_bumps_absent_count_below_threshold` | ✓ |
| Reconcile disabled still returns empty | unit | `test_reconcile_scan_disabled_returns_empty` | ✓ |
| Isolation leak check (no cross-connection pollution) | unit | `test_isolation_level_restored_to_none_within_scope` | ✓ |
| Memory drift pattern added | N/A | N/A — no documented behavior deprecated | ✓ |

---

## Testing strategy

Structural test asserts the mechanism (isolation_level=None + BEGIN
IMMEDIATE + COMMIT). Lock-unavailable test proves the error path by
holding an EXCLUSIVE lock from a second connection. Happy-path tests
confirm existing BC-086 auto-close + bump behavior survives the
transaction wrap.

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_114_reconcile_toctou_lock.py -v
```
