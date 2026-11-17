---
id: BC-141
title: Container + systemd memory/CPU caps — contain OOM to the container, never the host
status: draft
created: 2026-11-17
branch: feat/BC-141-BC-144-memory-safety
plan: docs/plan/BC-141-process-memory-caps-plan.md
---

# BC-141: Container + systemd memory/CPU caps — contain OOM to the container, never the host

## Narrative

Three hetzner-staging outages on 2026-11-17 (04:04, 05:30, ~07:00 UTC)
all followed the same chain: hub python process RSS climbed past the
VPS's physical memory → kernel OOM-killer picked a load-bearing process
(sshd / systemd-journald / tailscale) → host dropped off the network
for 15-30 min. Confirmed by `dmesg -T`:

```
Out of memory: Killed process 13640 (python3)
  total-vm:4742520kB anon-rss:4329452kB
```

**BC-141 makes host-wide OOM impossible by cgroup-capping every
beaconry process.** If python ever climbs past its cap, docker's
container-level OOM-killer kills the container (not the host);
`restart: unless-stopped` bounces it; scouts auto-reconnect. Host
stays healthy.

BC-141 does NOT fix the leak — it bounds the blast radius. BC-142
surfaces memory metrics; BC-143 hunts the leak; BC-144 gates future
regressions.

## Trigger

Any future beaconry process (hub, scout, telegram-mock) that leaks
memory, retains SBOM payloads, or otherwise balloons past its working
set. BC-141 ensures that failure mode stays container-scoped.

## Expected outcome

- `poc/docker-compose.yml`: every beaconry service has explicit
  `mem_limit`, `mem_reservation`, `memswap_limit=mem_limit` (swap off),
  and `cpus=N` quota.
- Service limits picked to fit ~2 GB VPS comfortably with headroom for
  grype subprocess spikes + ssh + system load:
  - **hub**: 1 GB mem, 2 CPUs. Baseline is ~150 MB; cap is 6× to absorb
    grype RAM spike + scan burst; still well under VPS physical memory.
  - **scout**: 768 MB mem, 1 CPU. syft spikes up to ~400 MB per image.
  - **telegram-mock**: 64 MB, 0.25 CPU. Tiny HTTP server.
- `systemd` beaconry-hub.service + beaconry-scout.service (fleet
  deploy path, not docker-compose dev) gain `MemoryMax=1G` /
  `MemorySwapMax=0` / `CPUQuota=200%` so even if someone runs
  `docker run` outside compose, the systemd slice enforces the cap.
- Container-level OOM kills are **visible** — they appear as
  `OOMKilled: true` in `docker inspect`. The Docker healthcheck + the
  existing `scout_offline` alert will still fire if a crashloop
  develops — but on bounded scope.
- Operator verification: `docker stats` shows real-time per-container
  memory; `docker inspect beaconry-hub --format '{{.State.OOMKilled}}'`
  is True after any recent OOM.

## Out of scope

- Fixing the leak itself (BC-143).
- Memory metrics in `/healthz` / `beaconry status` (BC-142).
- Perf-gate memory assertion (BC-144).
- Swap-file provisioning (we explicitly disable swap for beaconry
  containers — swap thrashing hid the original OOM for longer than it
  should have).
- Host-level cgroup tuning beyond what systemd + docker provide.

## Acceptance criteria (Gherkin)

```gherkin
Feature: beaconry processes can never exhaust the host

  Scenario: hub mem_limit enforced by Docker
    Given beaconry-hub starts under docker compose
    When `docker inspect beaconry-hub` is read
    Then HostConfig.Memory == 1073741824 (1 GB)
    And HostConfig.MemorySwap == 1073741824 (swap off)
    And HostConfig.NanoCpus == 2000000000 (2 cores)

  Scenario: scout mem_limit enforced by Docker
    Given beaconry-scout starts under docker compose
    When `docker inspect beaconry-scout` is read
    Then HostConfig.Memory == 805306368 (768 MB)
    And HostConfig.NanoCpus == 1000000000 (1 core)

  Scenario: systemd unit also enforces (defense-in-depth)
    Given the fleet-deploy systemd unit is installed
    When `systemctl show beaconry-hub` is read
    Then MemoryMax == 1073741824
    And MemorySwapMax == 0
    And CPUQuota is set (200% == 2 cores)

  Scenario: container OOM restarts, host stays healthy
    Given a simulated 2 GB Python allocation inside beaconry-hub
    When the allocation exceeds the cap
    Then Docker OOM-kills the container (not the host)
    And `State.OOMKilled == true` in docker inspect
    And `restart: unless-stopped` triggers a restart within 15s
    And the host's TCP:22 remains reachable throughout

  Scenario: Makefile lint catches drift
    Given a developer edits docker-compose.yml and omits mem_limit
    When `make verify-compose-memory-limits` runs
    Then the gate fails with a message naming the offending service
```

## Capabilities delivered

- **CAP-O042: beaconry container memory + CPU caps enforced at both
  docker + systemd layers** (type: operational, status: active)

  *`poc/docker-compose.yml` sets `mem_limit`, `memswap_limit` (swap off),
  and `cpus` on every beaconry service. Fleet systemd units (installed
  by homelab ansible for prod deployment) additionally set `MemoryMax`
  + `MemorySwapMax=0` + `CPUQuota` so the limit holds even if an
  operator ever runs the container outside compose. New
  `poc/scripts/verify_compose_memory_limits.py` lint (hooked into
  `make check`) blocks a future PR that adds a new service without a
  memory cap. Host-wide OOM-kill is no longer reachable from a
  beaconry memory leak — the container dies, the host doesn't.*

Supports CAP-B023 (homelab operator reliability). Extends no existing
CAP; new operational guarantee.

## Open questions

Resolved during drafting (2026-11-17):

1. hub cap of 1 GB — enough headroom? **Yes, with margin.** Baseline
   is ~150 MB; peak during a bb-82jq 6700-candidate scan + grype
   subprocess is ~600 MB observed; 1 GB gives 40% headroom. If
   legitimate future features need more, bump + document.
2. scout cap of 768 MB — syft RAM? **Measured ~400 MB spike on
   large images.** 768 MB holds 2 concurrent syft invocations.
   Scouts run syft serially per image so single-process bound.
3. Hard cap vs soft cap? **Hard.** `mem_limit` is the ceiling;
   `mem_reservation` is the soft floor (50% of ceiling). Docker will
   OOM-kill on hard-cap breach.
4. Disable swap specifically? **Yes.** Swap thrashing is what
   extended the first outage window — the kernel kept the Python
   process alive while it paged frantically, holding the CPU hostage
   for minutes before the OOM-kill finally landed. `memswap_limit ==
   mem_limit` means no swap for the container.
5. Add `oom_kill_disable: false`? **No.** The default is false already
   — i.e., let the OOM-killer run. Setting `true` would hang the
   container instead of killing it; worse outcome.
6. Should bb-82jq (operator laptop) also apply the systemd caps?
   **No.** Laptop is dev workstation; the compose caps are
   sufficient. Fleet-only systemd caps.

## Links

- Direct precursor: BC-132 outage sequence (2026-11-17).
- Follow-ups: BC-142 (memory metrics), BC-143 (leak investigation),
  BC-144 (perf-gate memory assertion).
- Design: `memory/design-decisions.md § Separation of concerns` —
  BC-141 extends the "beaconry never takes down its host" invariant
  from behavior (don't delete operator rows, don't kill other
  containers) to resource (don't exhaust host RAM).
