# BC-006 — Tighten the shipping gate

*Branch: `feat/BC-006-tighten-gate`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-006-tighten-gate.md`*

---

## Situation

BC-005's smoke regression lived in `dev` for a day because `make check` doesn't invoke smoke. The gate claims readiness while hiding broken state. The fix is structural, not a one-off: define the fast tier (what every commit runs) vs the full tier (what CI/merge runs) and make both explicit.

Current gate composition:

| Target | What it runs | Cost | Where today |
|--------|--------------|------|-------------|
| `make lint` | ruff + format | <2s | pre-commit |
| `make type` | pyright | ~1s | pre-commit |
| `make test` | pytest + coverage | ~3s | pre-push + `make check` |
| `make verify-traceability` | script | <1s | `make check` + pre-push |
| `make check` | lint + type + test + verify-traceability | ~5s | **merge gate** |
| `make smoke` | bash demo.sh --smoke | ~2s | **manual only** |
| `pytest -m compose` | compose config tests | <1s but opt-in | manual |
| `pytest -m real_tools` | real syft+grype test | ~30s + prereqs | manual |

## Problems to solve

- `make smoke` isn't in `make check` → broken smoke doesn't fail the gate
- Opt-in markers (`compose`, `real_tools`) have no `make` entry point → nobody runs them systematically
- No tier definition → "green" is undefined

## Goals

1. `make check` grows a `smoke` dependency; a broken smoke fails the gate
2. New `make check-full` = `make check` + `pytest -m "compose or real_tools"`
3. README has a "Gate tiers" table with cost + when-to-run
4. CLAUDE.md DD-TGD step 6 renamed: pre-push = `make check`, pre-merge = `make check-full`
5. `memory/workflow.md` gate matrix annotated with tier
6. New capabilities registered: CAP-B011, CAP-T023, CAP-T024
7. Breaking-change note: operators now may see `pytest -m "compose or real_tools"` run on `make check-full` and get skip markers if deps missing (not a failure)

## Scope

- `Makefile` — `check` depends on `smoke`; new `check-full` target
- `README.md` — new "Gate tiers" section
- `CLAUDE.md` — DD-TGD step 6 wording
- `memory/workflow.md` — gate matrix table with tier column
- `docs/CAPABILITIES.md` — CAP-B011, CAP-T023, CAP-T024
- `poc/tests/integration/test_BC_006_gate_tiers.py` — small test suite that asserts:
  - `make check` target depends on `smoke` (via a Makefile parse)
  - `make check-full` target depends on `check`
  - Opt-in-markers invocation in `check-full` is correctly formed
- This plan doc

## Non-goals

- Any new unit test coverage of behavior
- A separate CI YAML (we don't have CI yet; `check-full` is just the entry point)
- Automating compose up/down inside `make check-full` — too heavy; opt-in tests stay as marker-skipped unless deps present
- Introducing a third tier (e.g. `make check-ci-release`)
- `pip-audit` / `bandit` / supply-chain checks — own story

## Design decisions

**Two tiers, not three.** Fast vs full. More tiers = more cognitive load. Current needs are satisfied by two.

**`make check-full` is a superset.** Runs `make check` first, then the opt-in markers. If fast tier fails, full tier aborts early. Rejected: parallel tiers — complicates failure signalling.

**Opt-in markers invoked by name, not auto-discovered.** `pytest -m "compose or real_tools"` is explicit — new markers must be added here when they land. Rejected: `-m "not slow"` style negative selection — too easy to accidentally skip things.

**Fast tier must stay under 15s.** `make smoke` is ~2s on a warm venv. Fast tier currently ~5s. Adding smoke brings it to ~7s. Comfortable margin.

**Gate tier documentation lives in three places.** README (operator-facing, first contact), CLAUDE.md (contributor-facing, part of DD-TGD), memory/workflow.md (reference, deep). Each repeats the tier table briefly; the authoritative source is memory/workflow.md.

**Markdown table parse for the Makefile test.** The integration test opens `Makefile` as text and asserts the `check:` target line contains `smoke`, and `check-full:` exists and depends on `check`. Robust against formatting but tied to the current structure. Keeps the test lightweight.

## Breaking changes

- `make check` now runs `make smoke` → longer by ~2s; broken smoke blocks the gate (intended).
- `make check-full` is new; nobody has muscle memory for it yet. Documented in README.

## Merge plan

1. Plan + story + red test on this branch
2. Implement Makefile targets + docs
3. `make check` must pass (including the new smoke dep); `make check-full` must also pass
4. CAPs registered; verify-traceability clean
5. Fast-forward merge to `dev`
6. Delete branch

## Rollout plan

Additive. One-time: new `make check-full` target is documented; developers can start using it. No migration.

## Testing strategy

- Integration test parses `Makefile` + asserts the tier dependencies
- `make check` and `make check-full` both exercised during the branch's own verification pass
- Manual verification: operator runs `make check-full` to confirm compose markers execute

## Testing commands

```bash
source .venv/bin/activate
make check         # fast tier — must include smoke
make check-full    # full tier — invokes markers
pytest poc/tests/integration/test_BC_006_gate_tiers.py -v
```
