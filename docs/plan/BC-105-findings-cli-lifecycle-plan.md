# BC-105 — `make findings` lifecycle columns

*Branch: `feat/BC-105-findings-cli-lifecycle`*
*Story: `docs/stories/BC-105-findings-cli-lifecycle.md`*

## Scope

- `poc/hub/hub.py`:
  - Schema v9 → v10 on `findings`: add `close_reason TEXT` +
    `last_status_change_ts INTEGER NOT NULL DEFAULT 0`.
  - Three write-site updates:
    - BC-086 `reconcile_scan` auto_fixed branch → `close_reason='post_absent'`.
    - BC-102 `_stale_scout_reconcile_tick` → `close_reason='stale_scout'`.
    - `POST /findings/{id}` status-change endpoint → `close_reason='operator'`
      when status flips to a closed state.
  - All three also stamp `last_status_change_ts = now`.
- `poc/scripts/findings.py`:
  - Add `found_at` + `last_status_change_ts` + `close_reason` to the
    SELECT columns.
  - New display columns FIRST SEEN + LAST CHANGE, age-formatted via
    the existing BC-064 `_format_age` helper in `status.py`
    (reuse — don't duplicate).
  - Status column widened to 25 cols to fit `auto_fixed (stale-scout)`;
    when `close_reason` is set, render `{status} ({reason_slug})`.
- `docs/CAPABILITIES.md`: CAP-T207.
- `CHANGELOG.md`: v0.19.1 patch entry.
- Sibling schema-version assertions in BC-066/BC-086/BC-092/BC-104
  bumped to 10.
- Unit tests under `poc/tests/unit/test_BC_105_*.py`.
