# BC-143 — tracemalloc toggle + leak playbook

*Branch: `feat/BC-141-BC-144-memory-safety`*
*Start date: 2026-11-17*
*Story: `docs/stories/BC-143-tracemalloc-toggle.md`*

## Situation
BC-141 contains + BC-142 surfaces, but neither identifies the
responsible allocation. 2026-11-17 debug needed tracemalloc; wasn't
available.

## Goals
1. `BEACONRY_TRACEMALLOC=1` enables at startup. (1, 2)
2. `GET /_tracemalloc/top` returns JSON table. (3)
3. `beaconry tracemalloc` CLI renders. (4)
4. Playbook doc. (5)

## Scope
- `poc/hub/hub.py`: lifespan checks env → `tracemalloc.start(25)`
  (25 frame limit). `_tracemalloc_enabled` global mirrored on
  `/healthz`.
- New endpoint `GET /_tracemalloc/top` (require_cli_token) + optional
  `/_tracemalloc/snapshot` (store a labeled snapshot), `/_tracemalloc/diff`
  (compare stored snapshot vs current).
- `poc/scripts/tracemalloc_cli.py`: new CLI; `choose_source` routing.
- `Makefile`: `tracemalloc` target wraps the CLI.
- `docs/operations/memory-leak-hunt.md`: new playbook.
- `poc/tests/unit/test_BC_143_tracemalloc.py`: new.
- `docs/CAPABILITIES.md`: CAP-T239.
- `docs/VARIABLES.md`: `BEACONRY_TRACEMALLOC` dev-only knob.

## Non-goals
Always-on tracemalloc; memray; auto-detection.

## Design decisions
- Stdlib only.
- Underscore endpoint path (`/_tracemalloc`) marks introspection.
- 25-frame limit balances detail + overhead.

## Breaking changes
### 1. API: new `/_tracemalloc/*` endpoints. Additive.
### 2. Data: none (in-memory snapshot buffer).
### 3. Behavior: when BEACONRY_TRACEMALLOC=1, hub pays ~10-20 % CPU overhead. Docs warn operator.

## Merge plan
Co-ships with BC-141/142/144.

## Rollout
Default off → zero impact. Operator opts in only during a leak hunt.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Toggle on via env | unit | `test_BC_143_tracemalloc.py::test_tracemalloc_enabled_via_env` | ✓ |
| Default off | unit | `test_BC_143_tracemalloc.py::test_default_disabled` | ✓ |
| /_tracemalloc/top JSON shape when on | unit | `test_BC_143_tracemalloc.py::test_top_endpoint_json_shape_when_on` | ✓ |
| /_tracemalloc/top JSON shape when off | unit | `test_BC_143_tracemalloc.py::test_top_endpoint_json_shape_when_off` | ✓ |
| Playbook doc exists | manual | `docs/operations/memory-leak-hunt.md` exists + grep for 4 candidate-leak sections | ✓ |
| Live post-deploy toggle | live | enable env on one hub boot, curl /_tracemalloc/top, disable | ❌ pending |
| Memory drift pattern | TOML | N/A | ✓ |

## Testing
Unit tests toggle env + start tracemalloc + take snapshot + assert
endpoint shape. Playbook presence check via file-existence in the
traceability lint.

## Commands
```bash
make check
pytest poc/tests/unit/test_BC_143_tracemalloc.py -v

# Investigation:
# 1. Enable
docker compose exec hub env BEACONRY_TRACEMALLOC=1 kill -HUP 1  # or redeploy
# 2. Capture
beaconry tracemalloc --snapshot t0
# wait a scan cycle
beaconry tracemalloc --diff t0
```
