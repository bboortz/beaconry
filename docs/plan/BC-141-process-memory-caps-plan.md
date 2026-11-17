# BC-141 — Container + systemd memory/CPU caps

*Branch: `feat/BC-141-BC-144-memory-safety`*
*Start date: 2026-11-17*
*Story: `docs/stories/BC-141-process-memory-caps.md`*

## Situation

Three host-level OOM-induced outages on 2026-11-17. Python RSS
reached 4.3 GB per `dmesg`. Host-wide OOM-killer took down sshd +
tailscale + hub for 15-30 min each. BC-132/139/140 did not prevent
host-level reach of the failure. Need process-level caps.

## Goals

1. `poc/docker-compose.yml`: `mem_limit`, `memswap_limit`, `cpus` on
   every beaconry service. (Gherkin scenarios 1, 2)
2. `poc/scripts/verify_compose_memory_limits.py` lint added to
   `make check`. (scenario 5)
3. Homelab systemd units ship with `MemoryMax`, `MemorySwapMax=0`,
   `CPUQuota`. (scenario 3) — the systemd unit files live in the
   homelab ansible repo; **BC-141 ships the beaconry-side requirement +
   the lint**; the ansible/systemd template update is a homelab-side
   follow-up (documented in the plan).
4. Operator verification: `docker inspect` shows the enforced caps;
   simulated OOM kills just the container. (scenario 4)

## Scope

- `poc/docker-compose.yml`:
  - `hub`: `mem_limit: 1g`, `mem_reservation: 512m`,
    `memswap_limit: 1g`, `cpus: "2"`.
  - `scout`: `mem_limit: 768m`, `mem_reservation: 384m`,
    `memswap_limit: 768m`, `cpus: "1"`.
  - `telegram-mock`: `mem_limit: 64m`, `memswap_limit: 64m`,
    `cpus: "0.25"`.
- `poc/scripts/verify_compose_memory_limits.py`: new lint. Parses
  `docker-compose.yml` services; each `beaconry-*` or scanner service
  must have `mem_limit` + `memswap_limit`; `memswap_limit == mem_limit`
  (swap off); `cpus` set.
- `Makefile`: add `verify-compose-memory-limits` to the `check` chain.
- `poc/tests/unit/test_BC_141_memory_limits.py`: new; parses the
  real compose file + asserts the required caps + swap-off invariant.
- `docs/CAPABILITIES.md`: CAP-O042.
- `docs/VARIABLES.md`: no new vars (caps are in compose, not env).
- Homelab-side systemd units: documented in this plan but not
  implemented here (lives in bboortz/homelab).

## Non-goals

- Fixing the leak (BC-143 playbook).
- Memory metrics (BC-142).
- Perf-gate memory assertion (BC-144).
- Tuning limits based on load tests — initial numbers are measured +
  margin'd; future BC can retune.

## Design decisions

- **Hard caps + swap disabled**: BC-131/132/139 showed that soft
  limits + swap let the hub thrash for minutes. Hard cap + no swap =
  fast fail, fast restart.
- **`cpus` quota too**: unlimited CPU during the memory climb would
  starve other containers (scout, system services). Bounded.
- **`restart: unless-stopped`** stays as-is on the compose stack.
  Container OOM → auto-restart → clean slate within 15 s.
- **Lint at check-time, not just docs**: catches "I added a new
  service and forgot the cap" at review, not after the next outage.

## Breaking changes

### 1. API / return shape
None. Pure compose config change + new lint.

### 2. Data model
None.

### 3. Behavioral semantics
- Hub process is now killed by Docker at 1 GB RSS (was: unbounded,
  reached 4.3 GB before host-OOM). Operator-visible: container
  restarts if the leak returns; service degrades gracefully.
- Scout syft spikes above 768 MB will also trigger container OOM.
  Observed peak was ~400 MB; cap has 2× headroom. If a future syft
  release bloats, we re-tune.

## Merge plan
Co-ships with BC-142/143/144 on `feat/BC-141-BC-144-memory-safety`.
Release as v0.22.18.

## Rollout plan
- No migration. New compose caps apply on next `make deploy-beaconry`.
- Operators running the stack via `docker compose up -d` locally
  pick up caps on next restart.
- Forge rebuild not strictly required (compose change only), but we
  rebuild anyway for a clean release tag.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| hub mem_limit in compose | unit | `test_BC_141_memory_limits.py::test_hub_has_1gb_cap` | ✓ |
| scout mem_limit in compose | unit | `test_BC_141_memory_limits.py::test_scout_has_768mb_cap` | ✓ |
| swap disabled per service | unit | `test_BC_141_memory_limits.py::test_swap_disabled_per_service` | ✓ |
| cpus set per service | unit | `test_BC_141_memory_limits.py::test_cpus_set_per_service` | ✓ |
| Lint catches drift | unit | `test_BC_141_memory_limits.py::test_lint_flags_missing_mem_limit` | ✓ |
| Live post-deploy: `docker inspect` shows caps | live | post-deploy `docker inspect beaconry-hub` HostConfig.Memory field | ❌ pending |
| Memory drift pattern | TOML | N/A — additive compose config | ✓ |

## Testing

Unit tests parse `poc/docker-compose.yml` + assert the required
keys + values; the lint test writes synthetic compose files with
missing caps + asserts the lint flags them.

## Commands

```bash
make check
pytest poc/tests/unit/test_BC_141_memory_limits.py -v
# Post-deploy:
ssh hetzner-staging "sudo docker inspect beaconry-hub --format '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.NanoCpus}}'"
# Expected: 1073741824 1073741824 2000000000
```
