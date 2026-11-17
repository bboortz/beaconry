---
id: BC-142
title: Memory metrics on /healthz + beaconry status — see the climb before the crash
status: draft
created: 2026-11-17
branch: feat/BC-141-BC-144-memory-safety
plan: docs/plan/BC-142-memory-metrics-on-healthz-plan.md
---

# BC-142: Memory metrics on /healthz + beaconry status — see the climb before the crash

## Narrative

The hetzner-staging OOM on 2026-11-17 was invisible to beaconry's own
observability — `/healthz` surfaced grype-db state, audit-write state,
Telegram state, integrity-mismatch counts, but **nothing about process
memory**. The 4.3 GB climb before the OOM-kill left no trace in any
operator-facing signal. Only `dmesg` after the fact told the story.

BC-142 surfaces process RSS + peak RSS + container memory limit on
`/healthz` and in `beaconry status` DETAILS. An operator sees memory
climbing BEFORE OOM; alerts can reference the metric; the BC-144
perf gate can assert bounded growth.

## Trigger

- Operator runs `beaconry status` and looks at DETAILS. Memory should
  appear alongside `grype_db ok` / `telegram polling` etc.
- Monitoring / dashboard polling `/healthz` sees a new `checks.rss_mb`
  key.
- BC-144's perf gate reads the metric between measurement iterations.

## Expected outcome

- `/healthz` `checks` block adds:
  - `rss_mb`: int — current process resident set size in MB.
  - `rss_peak_mb`: int — max observed RSS since hub boot.
  - `mem_limit_mb`: int | null — docker cgroup limit (parsed from
    `/sys/fs/cgroup/memory.max` on cgroup v2, fallback v1; null when
    unknown).
- `beaconry status` DETAILS column shows e.g.
  `rss: 180MB / 1GB cap`. Operator sees utilization ratio at a glance.
- Metric reads are cheap (<1 ms) via `/proc/self/status` + one
  `open()` on `/sys/fs/cgroup/memory.max`.
- Zero new dependencies — `psutil` is not pulled in; we use pure
  stdlib (proc filesystem reads).

## Out of scope

- Alerting on high RSS (operator / external monitoring responsibility).
- Historical memory time-series storage (not beaconry's job).
- Per-subsystem memory breakdowns (grype RAM, python heap, sqlite
  cache). Out of scope; aggregate RSS is the right signal.
- Container memory limit auto-tuning. BC-141 sets the cap; BC-142
  just reports it.

## Acceptance criteria (Gherkin)

```gherkin
Feature: beaconry hub reports its own memory footprint

  Scenario: /healthz surfaces current + peak RSS
    Given a running hub
    When operator curls /healthz
    Then checks.rss_mb is an int > 0
    And checks.rss_peak_mb >= checks.rss_mb

  Scenario: /healthz surfaces container memory cap
    Given beaconry-hub runs under docker with mem_limit=1g
    When operator curls /healthz
    Then checks.mem_limit_mb == 1024

  Scenario: cgroup absent (bare-metal / tests) → null
    Given hub runs outside a cgroup (e.g. a pytest process)
    When operator curls /healthz
    Then checks.mem_limit_mb is null (not an error)

  Scenario: beaconry status DETAILS shows memory utilization
    Given the hub has RSS 180MB and cap 1024MB
    When operator runs `beaconry status`
    Then the hub's DETAILS column contains `rss: 180MB / 1GB cap`

  Scenario: peak RSS tracks max-observed since boot
    Given a hub that spiked to 400 MB then dropped to 180 MB
    When operator curls /healthz
    Then checks.rss_peak_mb == 400 (approx, within a few MB)

  Scenario: Metric read is cheap (perf-gate friendly)
    Given the perf gate samples /healthz 10 times
    When each read runs through _memory_metrics()
    Then total added latency over 10 reads is <10 ms
```

## Capabilities delivered

- **CAP-T238: Process memory metrics on /healthz + beaconry status
  DETAILS** (type: technical, status: active)

  *New `_memory_metrics()` reads `/proc/self/status` for `VmRSS` +
  `VmPeak` and `/sys/fs/cgroup/memory.max` for the container limit
  (cgroup v2; fallback to v1 path). Returned as `rss_mb`,
  `rss_peak_mb`, `mem_limit_mb` on `/healthz`'s `checks` block —
  additive, no existing keys changed. `poc/scripts/status.py` renders
  them in DETAILS when present. Pure-stdlib; no new deps. Fail-open:
  any read error → fields absent (operator sees no memory data, not
  an error).*

Supports CAP-O042 (BC-141 memory caps — this is the observability
layer). Extends CAP-T147 (BC-013 hub health probe schema).

## Open questions

Resolved during drafting (2026-11-17):

1. psutil or stdlib? **Stdlib.** `/proc/self/status` + one small file
   read is enough; adds no deps; works in scratch-based containers
   that don't even have `/bin/sh`.
2. Peak RSS — process-level or container-level? **Process-level** via
   `VmPeak` from `/proc/self/status`. Container-level peak would need
   cgroup `memory.peak` (cgroup v2 only) which is available in some
   kernels but not universally. Add later if needed.
3. mem_limit — what happens if the container has no cgroup (dev run
   without --memory)? **null.** Operator sees "cap: none" in status.
4. Non-Linux (macOS dev)? **Gracefully return nulls.** `/proc` doesn't
   exist on macOS. All fields absent.
5. Reporting cadence — refresh on every `/healthz` call? **Yes.** The
   reads are <1 ms; no caching needed.
6. Should we also track python heap specifically (tracemalloc
   snapshot)? **No — BC-143 handles that.** BC-142 is the low-overhead
   always-on signal; BC-143 is the diagnostic tool enabled on demand.

## Links

- Direct companion: BC-141 (caps the memory); BC-143 (profiles the
  source); BC-144 (gates the drift).
- Related: CAP-T147 (BC-013 — the health endpoint family that this
  extends).
