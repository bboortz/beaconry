# BC-044 — Live-smoke CLI harness

*Branch: `feat/BC-044-live-smoke-cli`*
*Start date: 2026-11-01*
*Story: `docs/stories/BC-044-live-smoke-cli.md`*

---

## Situation

Three live-only bugs in two weeks (BC-030 Dockerfile COPY, BC-035 import
scope inside hub container, BC-042 FOLLOW buffering + inline-script
error handling). Each required `make live-rebuild` + manual operator
exercise to surface. BC-038's scenario harness tests hub ingestion
against an isolated hub-only stack — doesn't exercise `make findings`,
`make status`, `make audit`, `make scan` against a dockerized hub, and
doesn't exercise Dockerfile COPY completeness.

---

## Problems to solve

- No gate between "unit tests pass" and "operator runs the CLI".
- The inline docker-exec scripts (findings.py's `_INLINE_FINDINGS_SCRIPT`,
  audit.py's `_INLINE_SCRIPT`) have no automated coverage — bugs there
  only surface on live run.
- Dockerfile `COPY` completeness is linted structurally (BC-012, BC-021)
  but doesn't test that the thing actually works end-to-end.

---

## Goals

1. `poc/scripts/cli_smoke.py` — scenario-based runner, same shape as
   BC-038's harness.
2. ~10 scenarios covering the operator-facing surface (details below).
3. `make test-cli` target — non-destructive, read-only against the live
   stack.
4. Fast-fail when stack isn't up with the actionable hint
   `run \`make live-rebuild\` first`.
5. Added to `make check-full` with `BEACONRY_SKIP_CLI_SMOKE=1` escape
   hatch for environments without Docker (CI without privileged runners,
   dev laptops without the stack booted).
6. Scenarios encode the three production-only bugs we hit as explicit
   regression guards.

---

## Scope

- NEW `poc/scripts/cli_smoke.py` — scenario base class + registry +
  runner + Makefile entry.
- `Makefile` — new `test-cli` target + help entry + `check-full`
  invocation with env-opt-out.
- `poc/tests/unit/test_cli_smoke_scenarios.py` — unit tests for scenario
  parser helpers (output regexes, docker-ps detection).
- `docs/CAPABILITIES.md` — register CAP-T093, CAP-B028.
- `docs/VARIABLES.md` + lint — document `BEACONRY_SKIP_CLI_SMOKE`.

---

## Non-goals

- Scenario harness for `make demo` (long-running, interactive).
- Scenarios that mutate state. Everything is read-only / idempotent
  (`make scan` can safely return 202 OR 409 — both are "the system works").
- Replacing BC-038's scenario harness. Different test surface.
- Pytest-style assertion library. `assert` + text match is enough.
- Dockerfile build-chain tests. Already covered by BC-012/BC-021.

---

## Design decisions

### Scenario class, same pattern as BC-038

```python
class CliScenario(ABC):
    order: int
    name: str
    description: str

    @abstractmethod
    def run(self) -> None:
        """Run the CLI invocation and raise AssertionError on drift."""
```

Each scenario is one file: `s01_make_findings_columns.py`,
`s02_make_status_services.py`, etc. Explicit registry in `_registry.py`.

### Prerequisite check: stack up?

Before any scenario, verify `beaconry-hub` is running + reachable at
`127.0.0.1:8000/healthz`. If not, exit 2 with:
```
✗ beaconry-hub not running — run `make live-rebuild` first
```

Mirrors `make scan`'s pattern.

### Scenario catalog (V1)

| # | Scenario | What it protects |
|---|---|---|
| 01 | `make findings` has all expected columns | BC-030 NET-column inline-script regression |
| 02 | `make status` shows both services | BC-013 status CLI contract |
| 03 | `make audit LAST=5` returns 5 rows + header | BC-042 base operation |
| 04 | `make audit COUNT=1` summary format | BC-042 summary mode |
| 05 | `make audit SINCE=bogus` → exit 2 + "time parse error" | **BC-042 close-gap regression guard** |
| 06 | `make audit FOLLOW=1` emits formatted header, not raw JSON | **BC-042 close-gap regression guard** |
| 07 | `make audit CVE=noexist` → "No events" | BC-042 empty-result UX |
| 08 | `make scan` returns 202 or 409 | BC-037 contract |
| 09 | `make findings STATUS=open` filter works | BC-007 filter |
| 10 | Hub Dockerfile actually serves `notifiers` module (BC-030 regression) | docker-exec a quick import check |

Scenarios 05 + 06 + 10 are the three explicit regression guards for the
production-only bugs this story was motivated by.

### Opt-out

`BEACONRY_SKIP_CLI_SMOKE=1` → `test-cli` prints "skipped per env" and
exits 0. Needed for:
- CI environments without privileged Docker
- Dev laptops with no live stack
- The scenario harness itself (BC-038) which spins up its own compose
  project — don't double-boot

### Rejected alternatives

- **Pytest fixtures that boot the stack**: too slow for the fast-tier
  gate; scenario harness pattern is better for operator-facing output.
- **Bash + grep**: brittle, hard to debug, doesn't compose assertions.
- **Spin up our own compose project**: BC-038 pattern. Here we test
  WHATEVER state the operator chose — includes their custom env vars.

---

## Breaking changes

### 1. API / return shape
**None.** New script, new Make target.

### 2. Data model
**None.** Read-only against the live DB + audit log.

### 3. Behavioral semantics

- Operators who do nothing: see no change until they run
  `make test-cli` or `make check-full` with the live stack up.
- `make check-full` gains a new block. Operators without a live stack
  must set `BEACONRY_SKIP_CLI_SMOKE=1`. Documented in `.env.example`
  + `VARIABLES.md`.
- Three regression guards (05, 06, 10) will catch the specific bugs
  that motivated this story if they re-surface.

---

## Merge plan

1. All work on `feat/BC-044-live-smoke-cli`.
2. `make check` passes (unit tests for the parser helpers).
3. `make check-full` passes with BEACONRY_SKIP_CLI_SMOKE=1 opt-out
   checked in CI-no-docker environments.
4. Live run of `make test-cli` against this session's stack → 10/10.
5. PR to `dev`; FF merge; story → shipped; INDEX update.

---

## Rollout plan

Additive. Operators who already have the stack live see one new
command. Operators who don't can `BEACONRY_SKIP_CLI_SMOKE=1` without
losing anything they had.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| 10 scenarios in registry (ordered, unique names) | unit | `test_cli_smoke_scenarios.py::test_registry_has_ten_scenarios` | ✓ |
| Scenario base class is abstract | unit | `test_cli_smoke_scenarios.py::test_scenario_base_is_abstract` | ✓ |
| `make findings` row counter (data rows only) | unit | `test_cli_smoke_scenarios.py::test_findings_body_row_counter_counts_only_data_rows` | ✓ |
| `make audit` row-count parser | unit | `test_cli_smoke_scenarios.py::test_audit_body_row_counter` | ✓ |
| `make audit` row-count parser (regression: raw JSON ≠ data row) | unit | `test_cli_smoke_scenarios.py::test_audit_body_row_counter_rejects_json_lines` | ✓ |
| Audit header-row detection (formatted path) | unit | `test_cli_smoke_scenarios.py::test_audit_has_header_detects_formatted_output` | ✓ |
| Audit header-row detection (raw-JSON regression) | unit | `test_cli_smoke_scenarios.py::test_audit_has_header_rejects_raw_json_only` | ✓ |
| Stack-detection prerequisite | unit | `test_cli_smoke_scenarios.py::test_stack_check_detects_missing_hub` | ✓ |
| `BEACONRY_SKIP_CLI_SMOKE=1` opt-out | unit | `test_cli_smoke_scenarios.py::test_skip_env_bypasses_scenarios` | ✓ |
| Live-run against this session's stack passes all 10 | manual | `make test-cli` output on the live stack | ✓ |
| BC-042 FOLLOW regression guard (scenario 06) | unit | `test_cli_smoke_scenarios.py::test_follow_scenario_asserts_formatted_output` | ✓ |
| BC-042 bad-SINCE regression guard (scenario 05) | unit | `test_cli_smoke_scenarios.py::test_bad_since_scenario_asserts_exit_2` | ✓ |
| CAP-T093 + CAP-B028 registered | verify-traceability | `docs/CAPABILITIES.md` diff | ✓ |
| `BEACONRY_SKIP_CLI_SMOKE` in VARIABLES.md | verify-variables | `docs/VARIABLES.md` entry | ✓ |

---

## Testing commands

```bash
make check                           # unit tests
make test-cli                        # requires live stack
BEACONRY_SKIP_CLI_SMOKE=1 make test-cli  # opt-out
```
