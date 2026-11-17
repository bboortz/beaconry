# BC-142 — Memory metrics on /healthz + beaconry status

*Branch: `feat/BC-141-BC-144-memory-safety`*
*Start date: 2026-11-17*
*Story: `docs/stories/BC-142-memory-metrics-on-healthz.md`*

## Situation

OOM on 2026-11-17 had no beaconry-side signal. `/healthz` reports 20
different health dials but nothing about memory. Operator needs
visibility.

## Goals

1. `/healthz checks` surfaces `rss_mb`, `rss_peak_mb`, `mem_limit_mb`.
   (Gherkin 1, 2)
2. Non-Linux or cgroup-less environments → `null`, not error. (3)
3. `beaconry status` DETAILS renders memory utilization. (4)
4. Peak tracks since-boot max. (5)
5. Cheap read (<1 ms per call). (6)

## Scope

- `poc/hub/hub.py`: new `_memory_metrics()` reads
  `/proc/self/status` (VmRSS, VmPeak) + `/sys/fs/cgroup/memory.max`
  (cgroup v2; fallback `memory.limit_in_bytes` for v1). Returns dict
  with the three fields or empty dict on error.
- `/healthz` checks block `**_memory_metrics()` (additive).
- `poc/scripts/status.py`: if any of the three keys present, append
  `rss: <N>MB / <cap>MB cap` to DETAILS column.
- `poc/tests/unit/test_BC_142_memory_metrics.py`: new.
- `docs/CAPABILITIES.md`: CAP-T238.

## Non-goals
- Per-subsystem breakdown; psutil; alerting; time-series storage.

## Design decisions
- Stdlib only.
- Fail-open: any read fails → fields absent.
- No caching: reads are <1ms; staleness not a real problem.

## Breaking changes
### 1. API: `/healthz` grows 3 optional fields. Additive.
### 2. Data: none.
### 3. Behavior: `beaconry status` DETAILS grows a memory suffix on hub row.

## Merge plan
Co-ships with BC-141/143/144.

## Rollout
Zero migration. First post-deploy curl /healthz shows the fields.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| /healthz surfaces rss_mb | unit | `test_BC_142_memory_metrics.py::test_healthz_includes_rss_mb` | ✓ |
| /healthz surfaces peak | unit | `test_BC_142_memory_metrics.py::test_healthz_includes_rss_peak_mb` | ✓ |
| cgroup absent → null | unit | `test_BC_142_memory_metrics.py::test_cgroup_absent_returns_null_mem_limit` | ✓ |
| beaconry status renders memory | unit | `test_BC_142_memory_metrics.py::test_status_cli_renders_memory_details` | ✓ |
| _memory_metrics is cheap | unit | `test_BC_142_memory_metrics.py::test_metrics_read_is_cheap` | ✓ |
| Live post-deploy curl | live | `curl /healthz | jq .checks.rss_mb` returns int > 0 | ❌ pending |
| Memory drift pattern | TOML | N/A | ✓ |

## Testing
Unit tests write synthetic `/proc/self/status` + cgroup files via
monkeypatch; assert parse + render.

## Commands
```bash
make check
pytest poc/tests/unit/test_BC_142_memory_metrics.py -v
curl -s http://100.77.182.73:8000/healthz | jq '.checks | {rss_mb, rss_peak_mb, mem_limit_mb}'
```
