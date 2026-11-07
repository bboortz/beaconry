# BC-087 — Wire `Last seen` into notify() + BEACONRY_LOG_LEVEL

*Branch: `feat/BC-087-last-seen-wire-and-log-level`*
*Start: 2026-11-05*

## Why

Two gaps from BC-086:

1. The `format_last_seen` helper + the `last_seen_ts` kwarg on
   format_* exist and are tested in isolation, but production
   `notify(f)` / `notify_regression(f, ts)` / `notify_heartbeat(f,
   ts)` / `notify_auto_fix_regression(f, ts)` don't pass
   `last_seen_ts` into format_*. Kwarg defaults to None → line
   omitted. Operator saw this in the first real Telegram alerts.

2. BC-086's diagnostic `log.info` lines are permanently noisy on
   production (hub logs ~60 lines per minute across the fleet).
   Need a way to toggle them without a ship cycle.

## Scope

### 1. Wire `last_seen_ts` through `notify*()` call sites

New helper in hub.py:

```python
def _load_last_seen(finding_id: str) -> int | None:
    """BC-087: pull the current last_seen_ts for a finding (one-col SELECT).
    Returns None if row is missing OR last_seen_ts is 0; format_last_seen
    treats both as 'no info — skip the line'."""
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT last_seen_ts FROM findings WHERE id=?", (finding_id,)).fetchone()
    con.close()
    if row is None or not row[0]:
        return None
    return int(row[0])
```

Then in each `notify*()` function, before calling format_*:

```python
last_seen = _load_last_seen(finding.id)
_mock_print("Notification", format_notification(finding, last_seen_ts=last_seen))
# same pattern inside the real _dispatch_* branch
```

Same pattern for all four:
- `notify(f)` → `format_notification(f, last_seen_ts=...)`
- `notify_heartbeat(f, open_since_ts)` → `format_heartbeat(f, open_since_ts, last_seen_ts=...)`
- `notify_regression(f, last_fixed_ts)` → `format_regression(f, last_fixed_ts, last_seen_ts=...)`
- `notify_auto_fix_regression(f, auto_fixed_ts)` → `format_auto_fix_regression(f, auto_fixed_ts, last_seen_ts=...)`

Dispatch adapters (telegram_adapter, etc.) may need to accept and
forward the kwarg too; check + update if so. If adapters call
format_* themselves, pass it there.

### 2. `BEACONRY_LOG_LEVEL` env

Hub and scout both have a `logging.basicConfig(format=..., level=...)`
near the top. Change the level arg to read from env:

```python
LOG_LEVEL = os.environ.get("BEACONRY_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="...",
)
```

Guard against invalid values: fall back to INFO silently + log a
warning.

Compose + .env.example + VARIABLES.md: document the new var with
default INFO.

### 3. Downgrade BC-086 diag logs

Lines affected in hub.py (BC-086 close-gaps):
- `log.info("BC-086 reconcile ENTER ...")` → `log.debug(...)`
- `log.info("BC-086 reconcile fid=... ")` → `log.debug(...)`
- `log.info("BC-086 bump_last_seen ...")` → `log.debug(...)`

Operator can re-enable with `BEACONRY_LOG_LEVEL=DEBUG`.

Monitor stream: will go silent until operator sets DEBUG. Document
in the BC-087 CHANGELOG entry so next reader knows the knob.

## Breaking changes

- **None at default** — current behaviour (INFO level, no last_seen
  line) remains the default. The wire-up fix adds the line to
  Telegram alerts (visible to operator) but it's additive.
- Operators who were watching the BC-086 diag stream must set
  `BEACONRY_LOG_LEVEL=DEBUG` to keep it.

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| `_load_last_seen` returns ts for known finding | unit | `test_BC_087_last_seen_and_log_level.py::test_load_last_seen_returns_ts` | ✓ |
| `_load_last_seen` returns None for missing finding | unit | `test_BC_087_last_seen_and_log_level.py::test_load_last_seen_missing_row` | ✓ |
| `_load_last_seen` returns None when ts is 0 | unit | `test_BC_087_last_seen_and_log_level.py::test_load_last_seen_zero_ts` | ✓ |
| `_load_last_seen` returns None when DB is missing/not-inited | unit | `test_BC_087_last_seen_and_log_level.py::test_load_last_seen_no_db` | ✓ |
| `notify()` passes last_seen_ts to format_notification | unit | `test_BC_087_last_seen_and_log_level.py::test_notify_passes_last_seen` | ✓ |
| `notify_regression()` passes it | unit | `test_BC_087_last_seen_and_log_level.py::test_notify_regression_passes_last_seen` | ✓ |
| `notify_heartbeat()` passes it | unit | `test_BC_087_last_seen_and_log_level.py::test_notify_heartbeat_passes_last_seen` | ✓ |
| `notify_auto_fix_regression()` passes it | unit | `test_BC_087_last_seen_and_log_level.py::test_notify_auto_fix_regression_passes_last_seen` | ✓ |
| BEACONRY_LOG_LEVEL env controls logging level (parametrize DEBUG/INFO/WARNING/ERROR) | unit | `test_BC_087_last_seen_and_log_level.py::test_log_level_env_parsed[DEBUG-10]` | ✓ |
| Default log level is INFO when env unset | unit | `test_BC_087_last_seen_and_log_level.py::test_log_level_default_info` | ✓ |
| Invalid LOG_LEVEL falls back to INFO | unit | `test_BC_087_last_seen_and_log_level.py::test_log_level_invalid_falls_back_to_info` | ✓ |
| BC-086 diag logs are now at DEBUG level | integration | `test_BC_087_last_seen_and_log_level.py::test_bc086_diag_at_debug_not_info` | ✓ |
| Compose + .env.example + VARIABLES.md updated | lint | `make verify-variables` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |
| Live verify: next Telegram alert carries "Last seen" line | post-release | operator confirms | ✓ |

## Version

0.11.1 → **0.12.0** (minor — new env var + fixes a visible-to-operator
regression in BC-086 output).
