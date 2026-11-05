# BC-086 — Auto-fix status with counter threshold

*Branch: `feat/BC-086-auto-fix-counter`*
*Start: 2026-11-05*

## Why

BC-031 reconcile covers `open|regressed|fixed|fixed_verified` only — the
SQL at `hub.py:661` excludes `snoozed` and `accepted`. Observed
symptom: `python:3.11-slim-bookworm · libc-bin` on hetzner-staging
stayed `snoozed` indefinitely even after image removal + fresh scan.

Operator requirement set:
1. Distinct `auto_fixed` status (visually + semantically distinct from
   operator-triggered `fixed_verified`).
2. Counter-based auto-close: increment on absence, flip to `auto_fixed`
   only when counter reaches a threshold. Not immediate.
3. Threshold configurable (`BEACONRY_AUTO_FIX_THRESHOLD`, default 3).
4. Auto-fix is **always visible** in `make findings` + `make audit`.
5. Telegram notifications include "Last seen: X ago" line.
6. If an auto-fixed finding reappears, the regression alert is distinct
   ("AUTO-FIX REGRESSED", not just "FIX REGRESSED").

## Schema changes

Three additive columns on `findings` + a schema_version bump. All
non-breaking (ALTER TABLE ADD COLUMN with DEFAULT).

```sql
ALTER TABLE findings ADD COLUMN absent_count INTEGER DEFAULT 0;
ALTER TABLE findings ADD COLUMN auto_fixed_ts INTEGER DEFAULT 0;
ALTER TABLE findings ADD COLUMN last_seen_ts INTEGER DEFAULT 0;
PRAGMA user_version = 5;   -- BC-062 schema version lands at 5
```

Backfill on first boot after BC-086: set
`last_seen_ts = COALESCE(last_seen_ts, found_at, strftime('%s','now'))`
for any pre-BC-086 rows so "Last seen" notifications don't say
"never seen" for historical findings.

## Status transitions

Current (BC-031):

| From | Event | To |
|---|---|---|
| open/regressed | absent in scan | fixed_verified (immediate) |
| fixed/fixed_verified | present in scan | regressed (loud alert) |
| snoozed/accepted | — | never touched |

New (BC-086):

| From | Event | To |
|---|---|---|
| open/regressed/snoozed/accepted | absent, absent_count < threshold | stay as-is, bump counter |
| open/regressed/snoozed/accepted | absent, absent_count ≥ threshold | **auto_fixed** + record prior status + emit `finding_auto_fixed` audit event |
| any status | present in scan | reset counter to 0; update last_seen_ts |
| fixed_verified | present | regressed (BC-031 existing — loud "🔥 FIX REGRESSED") |
| auto_fixed | present | regressed (**distinct** — loud "🔥 AUTO-FIX REGRESSED" + `finding_regressed_from_auto_fix` audit event) |
| fixed/fixed_verified/regressed-from-manual | — | counter untouched (operator-initiated states stay operator-owned) |

## New env var

`BEACONRY_AUTO_FIX_THRESHOLD` — integer ≥ 1, default `3`. Lower values
= more aggressive auto-fix (less tolerant of transient scan glitches).
`1` reproduces BC-031's pre-BC-086 immediate-close behavior.

Surfaced in hub `/healthz` under `checks.auto_fix_threshold` so
`make status` can echo the configured value.

## Telegram message changes

### "Last seen" line

Every finding notification (new alert, reminder, regression) adds a
single line in the body:

```
Last seen: just now        # first alert for a new finding
Last seen: 3h ago          # reminder
Last seen: 2d ago          # regression context
```

Helper: `format_last_seen(f: Finding) -> str`. Uses existing humanize
helper if present; otherwise ad-hoc.

### Auto-fix regression (new)

When `auto_fixed → regressed`:

```
🔥 AUTO-FIX REGRESSED
CVE-ABCD-1234 is back on <host>
Previously auto-closed <X ago> after <N> missed scans.
Last seen (before auto-fix): <ts>
Package: ...
```

Distinct from the BC-031 `🔥 FIX REGRESSED` subject so operators can
visually distinguish which process reclosed the finding.

### No-notification for auto-fix close

The `auto_fixed` transition itself is silent (operator already told us
via [Snooze] / [Accept risk] that they don't want pings, OR the
finding was already open and close-messages would be noise). Visibility
via audit log + `make findings` is enough.

## CLI changes

- `make findings STATUS=auto_fixed` — already works via existing
  status filter; just document the new status value.
- `make findings` default view: `auto_fixed` rows render with a
  distinct marker (e.g., `✅` or `AUTO`) so operator scanning the list
  can distinguish from `fixed_verified`.
- `make audit EVENT=finding_auto_fixed` + `EVENT=finding_regressed_from_auto_fix`
  — both become discoverable in the event filter.

## Breaking changes

- **Behavior change**: BC-031's immediate-close on first-absent is now
  threshold-gated. Operators relying on "image gone → finding closes
  in one scan" will notice a ~15 min delay (3 scans × 5 min default).
  Mitigation: can set `BEACONRY_AUTO_FIX_THRESHOLD=1` to reproduce
  pre-BC-086 cadence.
- **New status enum value** (`auto_fixed`): any external script or
  dashboard parsing `beaconry metrics` / `make findings --json` needs
  to handle the new value. No external consumers today.
- **Schema version bump** 4 → 5: `PRAGMA user_version` moves forward.
  Old scouts that strictly check schema version fail-closed. No old
  scouts in the fleet (all on 0.10.1).

## Not addressed

- Per-finding threshold override.
- Daily digest "N findings auto-fixed" summary.
- Un-fix / override Telegram button for operator to flip a false
  auto-fix back to open.

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| New schema columns present | unit | `test_BC_086_auto_fix.py::test_schema_has_absent_count_and_auto_fix_columns` | ✓ |
| Schema version bumped (5 at BC-086 ship; BC-089 → 6; BC-092 → 7) | unit | `test_BC_086_auto_fix.py::test_schema_version_is_7` | ✓ |
| Threshold env var read, defaults to 3 | unit | `test_BC_086_auto_fix.py::test_auto_fix_threshold_default_is_3` | ✓ |
| Open finding: counter increments on absent | unit | `test_BC_086_auto_fix.py::test_counter_increments_when_open_absent` | ✓ |
| Open finding: auto_fixed at threshold | unit | `test_BC_086_auto_fix.py::test_open_flips_to_auto_fixed_at_threshold` | ✓ |
| Snoozed finding: auto_fixed at threshold | unit | `test_BC_086_auto_fix.py::test_snoozed_flips_to_auto_fixed_at_threshold` | ✓ |
| Accepted finding: auto_fixed at threshold | unit | `test_BC_086_auto_fix.py::test_accepted_flips_to_auto_fixed_at_threshold` | ✓ |
| last_seen_ts updates on present | unit | `test_BC_086_auto_fix.py::test_last_seen_updates_on_present` | ✓ |
| Auto-fixed regression uses distinct message | unit | `test_BC_086_auto_fix.py::test_auto_fix_regression_uses_distinct_message` | ✓ |
| Audit event `finding_auto_fixed` emitted | integration | `test_BC_086_auto_fix.py::test_open_flips_to_auto_fixed_at_threshold` | ✓ |
| Audit event `regressed_from_auto_fix` emitted (distinct from `regressed`) | integration | `test_BC_086_auto_fix.py::test_auto_fix_regression_uses_distinct_audit_event` | ✓ |
| /healthz surfaces configured threshold | unit | `test_BC_086_auto_fix.py::test_healthz_includes_auto_fix_threshold` | ✓ |
| `format_last_seen` humanizes just-now / Nm / Nh / Nd | integration | `test_BC_086_auto_fix.py::test_format_last_seen_variants` | ✓ |
| `format_notification` includes Last seen line when provided | integration | `test_BC_086_auto_fix.py::test_notification_includes_last_seen_when_provided` | ✓ |
| `format_regression` body includes Last seen | integration | `test_BC_086_auto_fix.py::test_regression_body_includes_last_seen` | ✓ |
| `format_auto_fix_regression` body includes Last seen | integration | `test_BC_086_auto_fix.py::test_auto_fix_regression_body_includes_last_seen` | ✓ |
| Threshold=1 reproduces pre-BC-086 immediate cadence | integration | `test_BC_086_auto_fix.py::test_threshold_one_closes_immediately` | ✓ |
| Counter resets when finding reappears in scan | integration | `test_BC_086_auto_fix.py::test_counter_resets_when_finding_reappears` | ✓ |
| Snoozed + present in scan is untouched (BC-086 scope preservation) | integration | `test_BC_031_scan_reconciliation.py::test_snoozed_finding_still_in_scan_is_untouched` | ✓ |
| Accepted + present in scan is untouched | integration | `test_BC_031_scan_reconciliation.py::test_accepted_finding_still_in_scan_is_untouched` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Version

0.10.1 → **0.11.0** (minor — new status, new DB column, new env var,
behavior change to auto-close cadence).
