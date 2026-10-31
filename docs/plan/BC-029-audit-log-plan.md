# BC-029 — Append-only audit log

*Branch: `feat/BC-029-audit-log`*
*Date: 2026-10-31*

## Situation

Only state the DB. Status transitions are destructive: `set_status(..., "fixed")` overwrites the prior status. If an operator asks "when did CVE-X get flagged, when was it fixed, who tapped the button?" there's no answer. IDEAS flagged this "priority: soon" after a user raised it.

## Fix

New module `poc/hub/audit.py` with `append(event_type, **details)` writing JSONL. Hub calls it from `set_status` and `save_finding`. Fire-and-forget semantics (audit failure must not block scans).

## Scope

- **New:** `poc/hub/audit.py` — append + read_events
- **New:** `poc/tests/unit/test_audit.py` — 8 tests (write, append-only, io-failure safe, read newest-first, limit, malformed-lines, no-log, integration)
- **Modified:** `poc/hub/hub.py` — dual-path import; hook into set_status + save_finding
- **Modified:** `poc/hub/Dockerfile` — COPY audit.py (BC-021 lint would catch this)
- **Modified:** `docs/CAPABILITIES.md` — CAP-T067

## Design decisions

- **JSONL (not SQLite)**: streams well with `tail -f`, grep-friendly, no DB locks during long scans.
- **int unix timestamp (not ISO)**: shell-first operators, awk-friendly.
- **Fire-and-forget**: audit is observability. Failed audit writes WARN, never raise.
- **No rotation**: append-only means append-only. Operators manage retention externally (logrotate, backup, archive).
- **Hooked at persistence layer, not call sites**: every `set_status` + `save_finding` → log entry. New callers of those two functions get audit coverage automatically.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| JSONL round-trip | unit test | `test_audit.py::test_append_writes_jsonl_line` | ✓ |
| Append-only (no truncate) | unit test | `test_audit.py::test_append_is_append_only` | ✓ |
| Fire-and-forget on IO failure | unit test | `test_audit.py::test_append_is_fire_and_forget_on_io_failure` | ✓ |
| read_events newest-first + limit | unit test | `test_audit.py::test_read_events_returns_newest_first`, `test_audit.py::test_read_events_respects_limit` | ✓ |
| Malformed line tolerance | unit test | `test_audit.py::test_read_events_tolerates_malformed_lines` | ✓ |
| hub.set_status integration | unit test | `test_audit.py::test_set_status_appends_audit_event` | ✓ |
| Dockerfile COPY (BC-021 lint catches) | lint | `make verify-dockerfile-sources` | ✓ |
| CAP-T067 registered | verify-traceability | CAPABILITIES.md diff | ✓ |
| make check green | multi-gate | `make check` | ✓ |
