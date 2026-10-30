# BC-007 close-gap — findings CLI compose-volume awareness

*Branch: `fix/BC-007-compose-path`*
*Start date: 2026-10-30*

## Situation

Surfaced during BC-011's live tap session: operator tapped 🔍 Verify service health, hub ran the probe, DB status flipped to `fixed_verified` — but `make findings STATUS=fixed_verified` returned empty. Different databases:

- Compose hub writes `/data/beaconry.db` in `poc_beaconry-data` named volume
- `make findings` reads `.beaconry-cache/beaconry.db` on the host (BC-007 local-dev default)

Operators running the Compose stack couldn't query the actual DB. Same ergonomics mismatch BC-013 solved for `make status` with docker-exec routing.

## Problem to solve

`make findings` is silently useless when the operator uses the Compose path (which is what BC-005 shipped as the primary deployment).

## Goal

Auto-detect whether `beaconry-hub` is running; if yes, route through `docker exec`. Preserve the local-dev path for `BEACONRY_MOCK=1 make demo` workflows. Operator override via `BEACONRY_FINDINGS_LOCAL=1`.

## Scope

- `poc/scripts/findings.py` — `_running_compose_hub()` detector + `_run_in_container()` dispatcher + inline findings script that runs inside the hub container
- `poc/tests/integration/test_BC_007_findings_cli.py` — add `BEACONRY_FINDINGS_LOCAL=1` to the existing test helper so BC-007 unit tests don't accidentally route to a live container; add 2 new tests for detection + local-override
- `docs/CAPABILITIES.md` — CAP-T052 registered

## Non-goals

- A `--compose` / `--local` CLI flag — env var is simpler and matches `make status`'s pattern
- Full `beaconry` CLI wrapper around compose detection — single-service special-case for now
- Refactoring the inline-script duplication — the DRY cost is low; the alternative requires COPYing findings.py into the hub image

## Design decisions

**Inline Python script sent via `docker exec python3 -c`.** Rejected: COPY `scripts/findings.py` into the hub image so operators can `docker exec beaconry-hub python3 -m scripts.findings`. Reason: the hub image is intentionally minimal; adding a scripts dir pulls dependencies that aren't needed for the hub runtime. Inline script duplicates ~30 lines of load/sort/format logic — small, auditable, same STATUS_ORDER as the top-level.

**Opt-out via env var, not flag.** Mirrors `BEACONRY_SCOUT_DATA_DIR` style from `make status`. Operators who script around `make findings` get consistent escape hatches.

**Default to compose when a hub container is up.** Rejected: default to local, require `--compose` to switch. Reason: the vast majority of operators using beaconry run the Compose stack. The local-dev path is for contributors.

## Breaking changes

Operators who ran `make findings` on a host where `beaconry-hub` is up AND were expecting the local-dev `.beaconry-cache/` file would now see the compose-volume output. Correct behavior; was previously silently wrong.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Live: `make findings STATUS=fixed_verified` returns the BC-011 tap result | live manual | 2026-10-30 — ran on host, got `GO-2026-4337 postgres:latest fixed_verified` from the compose volume | ✓ |
| Detection returns False when no beaconry-hub container exists | unit test | `test_running_compose_hub_detection_returns_false_when_container_absent` | ✓ |
| BEACONRY_FINDINGS_LOCAL=1 forces the local path | integration test | `test_beaconry_findings_local_env_forces_local_path` | ✓ |
| Existing BC-007 tests still pass (local path preserved) | integration tests | all BC-007 tests pass; `_run` helper sets `BEACONRY_FINDINGS_LOCAL=1` | ✓ |
| CAP-T052 registered | verify-traceability | make check green | ✓ |
| Memory drift pattern added if this deprecates documented behavior | N/A | N/A — close-gap only | ✓ |

## Testing commands

```bash
source .venv/bin/activate
make check
# live:
make findings STATUS=fixed_verified   # against a running compose hub
BEACONRY_FINDINGS_LOCAL=1 make findings  # forced to local-dev DB
```
