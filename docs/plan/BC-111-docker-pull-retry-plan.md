# BC-111 — DockerAdapter pull retry

*Branch: `feat/BC-111-docker-pull-retry`*
*Start date: 2026-11-09*
*Story: `docs/stories/BC-111-docker-pull-retry.md`*

---

## Situation

`poc/hub/executor/docker_adapter.py::DockerAdapter.execute` runs
`docker pull <target>` once. On non-zero exit it returns
`ExecutionResult(success=False, ...)`. A transient registry 503 or
network hiccup immediately fails the operator's Fix-now action.

---

## Problems to solve

- Operator Fix-now fails on transient registry issues; manual
  retry required.
- No distinction between transient failures and genuine ones
  (auth, tag-not-found) without parsing stderr — but blind retry
  is the safe default.

---

## Goals

1. Retry `docker pull` up to N times with exponential backoff
   (maps to Scenario "Pull fails once then succeeds").
2. Backoff doubles each attempt (maps to Scenario "Backoff doubles").
3. All-fail returns the same wire shape as pre-BC-111 (maps to
   Scenario "All attempts fail").

---

## Scope

- `poc/hub/executor/docker_adapter.py`:
  - New env vars: `BEACONRY_DOCKER_PULL_MAX_ATTEMPTS` (default 3),
    `BEACONRY_DOCKER_PULL_BASE_S` (default 5).
  - New `_run_docker_pull_once(pull_argv)` helper — single-attempt
    subprocess wrapper shared by the retry loop.
  - `DockerAdapter.execute` wraps pull in `for attempt in range(...)`
    loop with backoff.
- `poc/.env.example`, `poc/docker-compose.yml`, `docs/VARIABLES.md`:
  two new env rows.
- `docs/CAPABILITIES.md`: CAP-T213.
- Unit tests: `poc/tests/unit/test_BC_111_docker_pull_retry.py`.
- `CHANGELOG.md` + `VERSION` → `0.20.4` (patch — resilience fix).

---

## Non-goals

- Error-class retry logic (retry some kinds, not others). Blind
  retry is acceptable because the worst case is 2-3× latency on a
  terminal failure.
- Rollback-tag retry (local, already non-fatal).

---

## Design decisions

- **Blind retry over error classification**: parsing stderr is
  brittle (varies per registry). The worst case for retrying
  auth-fail is ~45s on an already-failing operation; operator UX
  degrades slightly on true failures but transient failures almost
  always recover.
- **Default MAX_ATTEMPTS=3, BASE_S=5**: 5s/10s/20s between attempts
  = ~35s total wait in the worst case. Fast enough that operators
  on a flaky connection don't notice; slow enough that Docker Hub
  5xx windows close.
- **Shared `_run_docker_pull_once`**: lifespan + retry loop (if we
  ever add one) would call the exact same subprocess invocation.

---

## Breaking changes

### 1. API / return shape

None. `ExecutionResult` wire shape unchanged.

### 2. Data model (schema + legacy rows)

None.

### 3. Behavioral semantics

- Pre-BC-111: single pull attempt; transient failure → user-visible
  failure.
- Post-BC-111: N attempts; transient failure clears on retry; genuine
  failure still user-visible with same message (just delayed).

---

## Merge plan

1. All work on `feat/BC-111-docker-pull-retry`.
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
| Scenario "Pull fails once then succeeds" | unit | `poc/tests/unit/test_BC_111_docker_pull_retry.py::test_execute_succeeds_on_second_attempt` | ✓ |
| Scenario "Backoff doubles each attempt" | unit | `test_backoff_doubles_each_attempt` | ✓ |
| Scenario "All attempts fail" | unit | `test_execute_fails_after_max_attempts` | ✓ |
| Mock mode bypasses retry | unit | `test_mock_mode_bypasses_retry` | ✓ |
| First-attempt success | unit | `test_execute_succeeds_on_first_attempt` | ✓ |
| Spawn failure handled | unit | `test_pull_once_handles_spawn_failure` | ✓ |
| Env parsing sensible defaults + invalid fallback | unit | `test_env_defaults_sensible`, `test_env_invalid_falls_back`, `test_env_rejects_zero` | ✓ |
| Memory drift pattern added | N/A | N/A — no documented behavior deprecated | ✓ |

---

## Testing strategy

Unit tests monkeypatch `_run_docker_pull_once` to control result
sequence + `asyncio.sleep` to assert backoff schedule without real
time elapsing. Rollback-tag subprocess is stubbed.

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_111_docker_pull_retry.py -v
```
