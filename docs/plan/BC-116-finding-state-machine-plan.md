# BC-116 — Finding state machine extension

*Branch: `feat/BC-116-finding-state-machine`*
*Start date: 2026-11-09*
*Story: `docs/stories/BC-116-finding-state-machine.md`*

---

## Situation

`set_status(finding_id, status)` writes `status` verbatim. No
validation; a typo like `"accetped"` lands in the DB silently and
confuses every downstream consumer. Statuses in active use today:
`open`, `fixed`, `fixed_verified`, `verification_failed`,
`regressed`, `snoozed`, `accepted`, `auto_fixed`. Missing: `fixing`
(the ~5-30s window during `_handle_fix` is invisible to operators)
and `rolled_back` (needed when `ExecutorPlugin.undo()` ships).

---

## Problems to solve

- No visibility into fixes in flight — operator can't tell "fix
  running right now" from "open, no fix attempt yet".
- Hub crash mid-fix (or operator Ctrl-C the hub) leaves no trace
  of the attempt in the DB.
- `set_status` silently accepts typos — a caller passing
  `"verified_fixed"` writes garbage and no error surfaces.
- When rollback ships (future BC per IDEAS line 128), the terminal
  status needs a reserved name.

---

## Goals

1. Add `fixing` + `rolled_back` to `_VALID_STATUSES` (maps to
   Scenario "fixing + rolled_back are valid statuses").
2. Validate at `set_status`; raise `ValueError` on unknown (maps
   to Scenario "set_status rejects an unknown value").
3. Wire `_handle_fix` to transition `open → fixing → terminal`
   (maps to Scenario "Hub crash during fix leaves a stale fixing
   row" + implicitly the happy path).
4. Stale-fixing sweep reverts stuck rows + emits `fix_timeout`
   audit event (maps to Scenario "Hub crash during fix leaves").
5. Fresh fixing rows must NOT be swept (maps to Scenario "Fresh
   fixing rows are NOT swept").

---

## Scope

- `poc/hub/hub.py`:
  - Module const `_VALID_STATUSES: frozenset[str]` enumerating
    the 10 statuses (8 existing + 2 new).
  - `set_status` validates against `_VALID_STATUSES` (raises
    `ValueError`).
  - `_handle_fix`:
    - `set_status(finding.id, "fixing")` before `EXECUTOR.execute`
    - On executor failure → `set_status(finding.id, "open")`.
    - On non-docker finding → `set_status(finding.id, "open")`
      (manual action required).
    - On verify_fix clean=False → `set_status(finding.id, "open")`.
    - On happy path → existing `set_status("fixed")` lands.
  - New env vars: `BEACONRY_FIXING_TIMEOUT_S` (default 600),
    `BEACONRY_FIXING_SWEEP_INTERVAL_S` (default 60).
  - New `stale_fixing_sweep(now_ts=None)` function.
  - New `_stale_fixing_loop_forever()` background task; wired
    into lifespan.
- `poc/.env.example`, `poc/docker-compose.yml`, `docs/VARIABLES.md`:
  two new env rows.
- `docs/CAPABILITIES.md`: CAP-T218.
- Unit tests: `poc/tests/unit/test_BC_116_finding_state_machine.py`.
- `CHANGELOG.md` + `VERSION` → `0.21.0` (minor — adds statuses).

---

## Non-goals

- `ExecutorPlugin.undo()` + `rolled_back` transition — separate BC.
- Formal transition-table validation (`fixing → open` yes,
  `fixing → regressed` no). Over-engineered for POC.
- `superseded` status (orthogonal data-model change).

---

## Design decisions

- **`frozenset` for valid-statuses**: hashable, immutable, `in` is
  O(1). Rejected enum.Enum — would force every string literal in
  the codebase to change (there are many).
- **Transition via existing `set_status`**: the audit event + DB
  write path is already well-tested. Adding a new transition-aware
  function would duplicate logic.
- **Revert `fixing → open` on failure**: reversing to `open` is
  the only safe default — the fix didn't land; operator needs to
  retry. Alternative (`verification_failed`) implies a probe
  happened, which it didn't.
- **Stale-fixing sweep in background task**: consistent with
  BC-104 / BC-115 pattern. Cadence 60s matches snooze sweep.
- **10-min timeout**: longer than BC-111's docker pull retry
  (35s worst case) + BC-110's verify probe retry (~14s) + any
  reasonable scout-side retry. A 10-min `fixing` row is a crashed
  hub, not a slow fix.
- **No schema migration needed**: `status` column is TEXT with
  no CHECK constraint.

---

## Breaking changes

### 1. API / return shape

Additive: two new valid statuses. `set_status` now raises
`ValueError` on unknown — callers that passed garbage strings
will surface errors they didn't before. This is a GOOD breaking
change (surfacing silent bugs) but in-tree audit found no such
callers (grep for `set_status(` shows only known-good values).

### 2. Data model

None. Column type unchanged.

### 3. Behavioral semantics

- Pre-BC-116: fix-in-flight invisible; crashed hub leaves no trace.
- Post-BC-116: `fixing` visible in `make findings` during the
  window; crashed hub leaves a `fixing` row that the sweep reverts
  within `BEACONRY_FIXING_TIMEOUT_S`.
- `make findings` already handles arbitrary status strings via
  BC-105's formatter — no CLI change needed.

---

## Merge plan

1. All work on `feat/BC-116-finding-state-machine`.
2. `make check` passes.
3. PR to `dev`, squash merge.
4. Post-merge: INDEX + story → shipped.

---

## Rollout plan

Additive. No operator action unless they want to tune the sweep
cadence.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario "fixing + rolled_back are valid statuses" | unit | `poc/tests/unit/test_BC_116_finding_state_machine.py::test_valid_statuses_includes_fixing_and_rolled_back`, `test_set_status_accepts_fixing`, `test_set_status_accepts_rolled_back` | ✓ |
| Scenario "set_status rejects an unknown value" | unit | `test_set_status_rejects_unknown` | ✓ |
| Scenario "Hub crash during fix leaves a stale fixing row" | unit | `test_sweep_reverts_stale_fixing_to_open`, `test_sweep_emits_fix_timeout_audit_event` | ✓ |
| Scenario "Fresh fixing rows are NOT swept" | unit | `test_sweep_skips_recent_fixing` | ✓ |
| Existing statuses still valid | unit | `test_valid_statuses_preserves_existing`, `test_set_status_existing_statuses_still_work` | ✓ |
| Sweep only touches fixing (not other statuses) | unit | `test_sweep_only_touches_fixing_status` | ✓ |
| Cold DB sweep handling | unit | `test_sweep_handles_cold_db_without_crashing` | ✓ |
| Env parsing | unit | `test_env_defaults_sensible`, `test_env_invalid_falls_back` | ✓ |
| Memory drift pattern added | N/A | N/A — no documented behavior deprecated | ✓ |

---

## Testing strategy

Unit tests cover the valid-status set, `set_status` validation,
the sweep's selective-revert behavior (only `fixing`, only old),
and the `fix_timeout` audit emission. Happy-path `_handle_fix`
wiring is covered by code review — the string-literal `set_status`
calls I added follow the same shape as BC-058's existing writes.

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_116_finding_state_machine.py -v
```
