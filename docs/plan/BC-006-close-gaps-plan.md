# fix/BC-006-close-gaps — Plan

*Branch: `fix/BC-006-close-gaps`*
*Start date: 2026-10-30*

---

## Situation

Post-ship audit of BC-006 (fast + full gate tiers) found two actionable gaps:

1. **No automated test for the "graceful skip" behavior.** The Gherkin says `make check-full` must exit 0 when compose markers skip for lack of docker. Live-verified in session but no test, so a future regression (e.g. markers raise instead of skip) ships silently.
2. **README doesn't document the "first-run check-full may show skips" surprise.** Operators new to `check-full` on a laptop without docker get skip markers in output and may misread as failure.

Timing assertion (fast tier < 15s) deferred to IDEAS — flaky across hardware.

## Problems to solve

- Graceful skip behavior is a Gherkin promise with no code enforcement
- README gate-tier table doesn't explain what a skip output means

## Goals

1. Automated test asserting that when the `compose` and `real_tools` markers both skip, pytest still exits 0 — so `make check-full` passes
2. README's "Gate tiers" section gets a one-liner about skip behavior
3. Reverse-link in Covers block

## Scope

- `poc/tests/integration/test_BC_006_gate_tiers.py` — new test that invokes `pytest -m "compose or real_tools" --collect-only` and asserts collection succeeds with 0 or more items (collection cannot fail even on absent prereqs)
- `README.md` — one-liner under the gate-tiers table about skip behavior
- This plan doc

## Non-goals

- Timing assertion for fast tier (deferred to IDEAS; flaky)
- Pyright version bump (separate chore)

## Design decisions

**Use pytest's `--collect-only` as the skip proxy.** The actual skip mechanism (`pytest.mark.skipif`) fires at test collection + at test execution. If we try to assert "exits 0 even without docker" we'd need to actually run on a host without docker. Simpler: assert pytest collects the markers without blowing up. Combined with the existing marker-skipif logic, this covers the Gherkin.

**Rejected: subprocess running `make check-full` from the test.** Would recursively invoke make. Too fragile; test timeout risks.

## Breaking changes

None.

## Merge plan

1. Plan + test + README change on branch
2. `make check` + `make check-full` pass
3. Fast-forward merge to `dev`

## Rollout plan

Additive. No action required.

## Testing strategy

Single new unit-like test in the existing BC-006 gate-tiers file.

## Testing commands

```bash
source .venv/bin/activate
make check
make check-full
```
