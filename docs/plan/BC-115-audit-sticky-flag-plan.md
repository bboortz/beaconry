# BC-115 — Audit write failure sticky flag

*Branch: `feat/BC-115-audit-sticky-flag`*
*Start date: 2026-11-09*
*Story: `docs/stories/BC-115-audit-sticky-flag.md`*

---

## Situation

`poc/hub/audit.py::append` catches `OSError`, logs a warning, and
returns — fire-and-forget by design. During an incident (disk full)
the audit trail silently loses events; operators have no visibility
into the degradation until they look at the file (or grep logs for
"audit write failed").

---

## Problems to solve

- Silent audit loss during critical windows.
- `/healthz` has no indication the audit layer is degraded.
- Operators have no at-a-glance recovery signal after they clear
  the disk-full / fix the permissions.

---

## Goals

1. Preserve the never-raise contract (maps to Scenario "Write
   failure sets sticky flag + does not raise").
2. Track degraded state in a sticky module-level flag (maps to
   same Scenario).
3. Expose via a `status()` getter that `/healthz` consumes (maps
   to Scenario "Fresh module reports clean state").
4. Successful write after failure clears the flag; cumulative
   counter persists (maps to Scenario "Recovery clears the
   sticky flag").
5. Audit health does NOT force aggregate `/healthz` degraded (maps
   to Scenario "Aggregate /healthz degraded status is NOT forced").

---

## Scope

- `poc/hub/audit.py`:
  - Module globals: `_degraded`, `_last_error`,
    `_write_failures`, `_write_failures_since_degraded`.
  - Helper `_mark_write_succeeded()`, `_mark_write_failed(exc)`.
  - Public `status() -> dict` for `/healthz` consumption.
  - `append()` calls the mark-helpers after the write attempt.
- `poc/hub/hub.py::_hub_healthz_summary`:
  - Merge `_audit.status()` into the `checks` dict.
  - Extend `bool_checks` exclusion to the three new keys so audit
    doesn't force aggregate degraded status.
- `docs/CAPABILITIES.md`: CAP-T217.
- Unit tests: `poc/tests/unit/test_BC_115_audit_sticky_flag.py`.
- `CHANGELOG.md` + `VERSION` → `0.20.8` (patch — observability fix).

---

## Non-goals

- Log rotation / archival.
- Syslog / remote sink.
- Alerting (operator reads `make status`).

---

## Design decisions

- **Sticky flag over per-call status**: a single failed write
  during a 10-minute disk-full window would be invisible if
  `/healthz` only showed the LAST call's status. Sticky means
  "has anything failed since the last success" — the useful
  signal.
- **Cumulative counter kept across recoveries**: operators who
  arrive after the incident want to know "did this ever fail?"
  not just "is it broken right now". `_write_failures_since_degraded`
  resets on recovery so the next incident shows a clean ramp.
- **Audit values excluded from aggregate `degraded`**: reversing
  this would cascade a transient disk full to compose unhealthy
  → scout `depends_on: service_healthy` → fleet outage. Audit is
  observability, not control-flow.
- **No new env knob**: the behavior is entirely automatic.

---

## Breaking changes

### 1. API / return shape

Additive: three new keys in `/healthz.checks`. CLIs tolerating extra
keys see no change. `make status` renders the new values if present,
ignores them otherwise.

### 2. Data model

None.

### 3. Behavioral semantics

- Pre-BC-115: silent audit loss, visible only via logs.
- Post-BC-115: silent audit loss, visible via `/healthz` +
  recoverable signal.
- `audit.append()` contract unchanged (never raises).

---

## Merge plan

1. All work on `feat/BC-115-audit-sticky-flag`.
2. `make check` passes.
3. PR to `dev`, squash merge.
4. Post-merge: INDEX + story → shipped.

---

## Rollout plan

Additive. No operator action.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario "Fresh module reports clean state" | unit | `poc/tests/unit/test_BC_115_audit_sticky_flag.py::test_status_fresh_module_returns_clean_state` | ✓ |
| Scenario "Write failure sets sticky flag + does not raise" | unit | `test_failed_write_sets_sticky_flag`, `test_never_raises_from_caller_perspective` | ✓ |
| Scenario "Recovery clears the sticky flag" | unit | `test_successful_write_after_failure_clears_sticky_flag` | ✓ |
| Scenario "Aggregate /healthz degraded status is NOT forced by audit" | unit | `test_healthz_aggregates_exclude_audit_keys` | ✓ |
| /healthz surfaces audit keys | unit | `test_healthz_surfaces_audit_status` | ✓ |
| Failures accumulate across attempts | unit | `test_failures_accumulate_across_attempts` | ✓ |
| Last-error truncation (200c) | unit | `test_last_error_truncated_to_200_chars` | ✓ |
| Memory drift pattern added | N/A | N/A — no documented behavior deprecated | ✓ |

---

## Testing strategy

Monkeypatch `Path.open` to raise OSError for `audit.log` writes only;
assert the sticky flag transitions + counters + healthz surfacing.

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_115_audit_sticky_flag.py -v
```
