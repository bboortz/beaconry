# BC-011 close-gap — findings CLI + button-attachment test coverage

*Branch: `fix/BC-011-close-gaps`*
*Start date: 2026-10-30*

## Situation

Post-ship audit caught two gaps in BC-011 (`e47918b`):

1. **`make findings` rejects the new DB statuses** — `STATUS_ORDER` tuple in `poc/scripts/findings.py` wasn't extended to include `fixed_verified` and `verification_failed`. The `--status` argument uses `choices=STATUS_ORDER`, so `make findings STATUS=fixed_verified` is rejected by argparse. CAP-T050 claims otherwise.
2. **No test for the probe-button-attachment logic** — Scenario 1 ("probe button appears only when a policy matches") was marked ✓ in Shipping evidence but the test didn't exist.

## Problem to solve

CAP-T050's description doesn't match shipped behavior; data-model symmetry fails (new writes have no reader via the CLI). Scenario 1 coverage is nominal, not real.

## Goals

1. Extend `STATUS_ORDER` to include both new values with a sort rank that puts `verification_failed` near the top (actionable) and `fixed_verified` at the bottom (terminal). Widen the Status column from 9 → 19 to fit `verification_failed`
2. Add `make findings` CLI tests: `--status fixed_verified` and `--status verification_failed` both work; sort order places `verification_failed` before `fixed`, `fixed_verified` after `open`
3. Add two hub handler tests: Verify button is attached when policy matches, omitted when it doesn't
4. Update the long-field truncation test's width cap from 110 → 120 to accommodate the wider Status column

## Scope

- `poc/scripts/findings.py` — STATUS_ORDER extended; Status column width 9 → 19
- `poc/tests/integration/test_BC_007_findings_cli.py` — 4 new tests + 1 cap update
- `poc/tests/unit/test_hub_helpers.py` — 2 new tests for the button-attachment branches

## Non-goals

- Any BC-011 behavior change — this is pure gap-close
- Dashboard / web UI for the new statuses

## Design decisions

**Place `verification_failed` early in STATUS_ORDER, `fixed_verified` last.** Rejected: alphabetical. Reason: STATUS_RANK is the sort key — operators want to see verification failures near open findings (actionable), and the fully-settled `fixed_verified` can live at the bottom of the list.

**Widen Status column 9 → 19.** Rejected: use a short code like `fv` / `vf` for display. Reason: operator-readability trumps terminal width; 120 chars still fits a standard terminal.

## Breaking changes

- Row width grows from ≤110 chars to ≤120 chars
- `make findings` no longer rejects `STATUS=fixed_verified` or `STATUS=verification_failed`

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| STATUS_ORDER extended with fixed_verified + verification_failed | code diff | `poc/scripts/findings.py:18-27` | ✓ |
| `--status fixed_verified` accepted | integration test | `test_bc011_status_filter_accepts_fixed_verified` | ✓ |
| `--status verification_failed` accepted | integration test | `test_bc011_status_filter_accepts_verification_failed` | ✓ |
| verification_failed sorts before fixed | integration test | `test_bc011_sort_rank_puts_verification_failed_near_open` | ✓ |
| fixed_verified sorts after open | integration test | `test_bc011_sort_rank_puts_fixed_verified_last` | ✓ |
| Verify button attached when policy matches | unit test | `test_fix_success_attaches_verify_button_when_policy_matches` | ✓ |
| Verify button omitted when no policy | unit test | `test_fix_success_omits_verify_button_when_no_policy` | ✓ |
| Row-width cap updated for widened Status | test diff | `test_long_fields_truncate_with_ellipsis` cap 110 → 120 | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | N/A | N/A — gap-close only, no new convention | ✓ |

## Testing commands

```bash
source .venv/bin/activate
make check
pytest poc/tests/integration/test_BC_007_findings_cli.py poc/tests/unit/test_hub_helpers.py -v --no-cov
```
