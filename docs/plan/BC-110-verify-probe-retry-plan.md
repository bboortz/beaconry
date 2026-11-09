# BC-110 — Verify probe retry with backoff

*Branch: `feat/BC-110-verify-probe-retry`*
*Start date: 2026-11-09*
*Story: `docs/stories/BC-110-verify-probe-retry.md`*

---

## Situation

`poc/hub/hub.py::_handle_verify` calls `_waterfall.run_probe(probe)`
once. On any failure (even a 1ms race where the service hasn't
bound its port yet), status flips to `verification_failed` and the
operator sees `⚠️ Probe failed`. Manual retry works but UX is poor.

---

## Problems to solve

- False `verification_failed` on the common post-restart port-binding
  race (service responds to the probe 100-500ms later).
- Operator cognitive load: they have to decide whether to re-tap
  Verify or investigate a real failure.

---

## Goals

1. Hub retries the probe up to N times with exponential backoff
   before marking failed (maps to Scenario "Probe fails once then
   succeeds").
2. Backoff doubles each attempt (maps to Scenario "Backoff doubles").
3. `MAX_ATTEMPTS=1` preserves single-shot behavior for operators
   who want it (maps to Scenario "MAX_ATTEMPTS = 1").

---

## Scope

- `poc/hub/waterfall.py`:
  - New `run_probe_with_retry(probe, max_attempts, base_s)` async
    helper. Wraps `run_probe` with backoff.
- `poc/hub/hub.py`:
  - New env vars: `BEACONRY_VERIFY_PROBE_MAX_ATTEMPTS` (default 3),
    `BEACONRY_VERIFY_PROBE_BASE_S` (default 2).
  - `_handle_verify` swaps `run_probe` → `run_probe_with_retry`.
- `poc/.env.example`, `poc/docker-compose.yml`, `docs/VARIABLES.md`:
  two new env rows.
- `docs/CAPABILITIES.md`: CAP-T212.
- Unit tests: `poc/tests/unit/test_BC_110_verify_probe_retry.py`.
- `CHANGELOG.md` + `VERSION` → `0.20.3` (patch — UX fix).

---

## Non-goals

- Retry on the Fix-now path (already covered by the probe's own
  retry window; BC-110 only touches _handle_verify).
- Mid-probe cancellation.

---

## Design decisions

- **Backoff defaults 2s/4s/8s**: 3 attempts × up to 14s total covers
  typical container startup. Operators with slower services bump
  `BASE_S` to 5 → 5s/10s/20s.
- **Decorated success reason on retry**: `"<reason> (passed on
  attempt N/M)"` — logs + Telegram show the retry worked, which
  helps operators distinguish "flaky service" from "clean fix".
- **First-success-wins semantics**: if attempt 2 passes, we don't
  continue to attempt 3. Return immediately.

---

## Breaking changes

### 1. API / return shape

None. `run_probe` is still exported; `run_probe_with_retry` is
additive.

### 2. Data model (schema + legacy rows)

None.

### 3. Behavioral semantics

- Pre-BC-110: single probe attempt; genuine timing race flagged
  verification_failed.
- Post-BC-110: up to N attempts; genuine failure still flagged
  (same outcome, same wire shape — just after more attempts).
- Operator can opt out by setting `MAX_ATTEMPTS=1`.

---

## Merge plan

1. All work on `feat/BC-110-verify-probe-retry`.
2. `make check` passes.
3. PR to `dev`, squash merge.
4. Post-merge: INDEX + story → shipped.

---

## Rollout plan

Additive. Zero operator action.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario "Probe fails once then succeeds" | unit | `poc/tests/unit/test_BC_110_verify_probe_retry.py::test_run_probe_with_retry_succeeds_on_second_attempt` | ✓ |
| Scenario "Backoff doubles each failed attempt" | unit | `test_backoff_doubles_each_retry` | ✓ |
| Scenario "MAX_ATTEMPTS = 1 preserves single-shot behavior" | unit | `test_max_attempts_1_is_single_shot` | ✓ |
| First-attempt success → no retry | unit | `test_run_probe_with_retry_succeeds_on_first_attempt` | ✓ |
| All-attempts-fail returns last result | unit | `test_run_probe_with_retry_returns_last_failure_after_max` | ✓ |
| Env parsing sensible defaults + invalid fallback | unit | `test_env_defaults_sensible`, `test_env_invalid_falls_back` | ✓ |
| Memory drift pattern added | N/A | N/A — no documented behavior deprecated | ✓ |

---

## Testing strategy

Unit tests monkeypatch `waterfall.run_probe` to control the result
sequence + `asyncio.sleep` to assert the backoff schedule without
real time elapsing.

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_110_verify_probe_retry.py -v
```
