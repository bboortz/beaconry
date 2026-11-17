# BC-144 — perf gate memory-growth assertion

*Branch: `feat/BC-141-BC-144-memory-safety`*
*Start date: 2026-11-17*
*Story: `docs/stories/BC-144-perf-gate-memory-assertion.md`*

## Situation
BC-132's gate catches latency regressions but not memory leaks.
2026-11-17 leak shipped undetected.

## Goals
1. Gate samples RSS at T0 + T1 + every 10s during shadow load.
2. Fails if growth > `BEACONRY_PERF_MEMORY_GROWTH_MB` (default 100).
3. Fails if monotonic growth over ≥3 consecutive samples (slow-leak
   signature).
4. Shadow-write load exercises the audit append path at 15 events/sec.
5. Reports a Memory check row in the gate output.

## Scope
- `poc/scripts/perf_gate.py`:
  - New `_sample_rss()` helper hits `/healthz` or reads local
    `/proc/self/status` depending on gate mode.
  - Measurement loop samples RSS at interval; detects monotonic
    growth + absolute bound.
  - New Verdict field `memory_growth_mb` + `memory_monotonic`.
  - Shadow-write-load as a threading.Thread that fires audit events.
- `poc/tests/unit/test_BC_144_perf_memory.py`: new; synthetic RSS
  sequences assert monotonic detection + bound breach; fixture
  leaks a known amount, asserts gate catches it.
- `docs/CAPABILITIES.md`: CAP-T240.
- `docs/VARIABLES.md`: `BEACONRY_PERF_MEMORY_GROWTH_MB` dev-knob.

## Non-goals
- Per-allocator attribution (BC-143).
- Historical trends.

## Design decisions
- Absolute bound + monotonic check: catches both fast + slow leaks.
- Shadow load reuses BC-132's seeded fixture + an added thread.
- Growth bound defaults to 100 MB — comfortable above noise, below
  BC-141's 1 GB cap.

## Breaking changes
### 1. API: perf_gate output grows a Memory check row.
### 2. Data: none.
### 3. Behavior: `make perf` runtime grows by ~60 s (shadow-load window).

## Merge plan
Co-ships with BC-141/142/143.

## Rollout
`make check-full` runtime grows. CI matrix absorbs it.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Clean baseline passes | unit | `test_BC_144_perf_memory.py::test_clean_hub_passes_memory_check` | ✓ |
| Synthetic leak fails | unit | `test_BC_144_perf_memory.py::test_leak_fixture_fails_gate` | ✓ |
| Monotonic catches slow leak | unit | `test_BC_144_perf_memory.py::test_monotonic_growth_three_samples_fails` | ✓ |
| Noisy samples without leak don't trip monotonic | unit | `test_BC_144_perf_memory.py::test_noisy_samples_without_leak_do_not_trigger_monotonic` | ✓ |
| Env knob tunes bound | unit | `test_BC_144_perf_memory.py::test_env_knob_overrides_default` | ✓ |
| Live post-deploy — perf gate reports Memory row | live | `make perf` output includes Memory check | ❌ pending |
| Memory drift pattern | TOML | N/A | ✓ |

## Testing
Unit tests synthesize RSS sequences through `_evaluate_memory_growth`
and assert verdict. One test fixture deliberately leaks via a
list.append of 200×1MB-strings; assert the gate catches it.

## Commands
```bash
make check
pytest poc/tests/unit/test_BC_144_perf_memory.py -v
make perf
# Output should include a Memory check block near the bottom.
```
