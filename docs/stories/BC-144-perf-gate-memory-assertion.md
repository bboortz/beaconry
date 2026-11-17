---
id: BC-144
title: Perf gate asserts bounded memory growth — catch leaks before they ship
status: draft
created: 2026-11-17
branch: feat/BC-141-BC-144-memory-safety
plan: docs/plan/BC-144-perf-gate-memory-assertion-plan.md
---

# BC-144: Perf gate asserts bounded memory growth — catch leaks before they ship

## Narrative

BC-132's perf gate measured latency. It missed the 2026-11-17 leak
because the gate ran for ~10 seconds against a seeded fixture — not
long enough for memory growth to be visible. BC-144 extends the gate
with a memory-growth assertion that runs for the full concurrent-write
shadow-load window and fails if RSS grows past a configured bound.

## Trigger

- Every CI run of `make perf` (gated via `make check-full`).
- Every operator-local `make perf` run.
- Any PR that introduces a new memory leak gets flagged at review
  time; no more "shipped, VPS died, debug for hours".

## Expected outcome

- `make perf` samples `rss_mb` from `/healthz` at measurement start
  (`T0_rss`), end (`T1_rss`), and each 10 s during the shadow-load
  phase.
- Gate fails if:
  - `T1_rss - T0_rss > BEACONRY_PERF_MEMORY_GROWTH_MB` (default 100).
  - Monotonic growth detected over ≥3 consecutive samples (catches
    slow leaks that would exceed the bound over a longer window).
- Reported alongside the latency table:
  ```
  === Memory check ===
    T0:         178 MB
    T1:         181 MB
    Max peak:   204 MB (during scan burst)
    Growth:     +3 MB (bound: 100 MB) ✓
  ```
- `BEACONRY_PERF_MEMORY_GROWTH_MB` env knob documented in
  `VARIABLES.md` + `perf-exceptions.toml` treatment for endpoints we
  know legitimately grow (none at ship time).
- The shadow-write-load that BC-139's plan referenced is wired up
  here: a background task posts synthetic `_audit.append` events at
  observed live-fleet rate during the measurement window, exercising
  the same path that killed the VPS.

## Out of scope

- Per-allocator attribution (that's BC-143's tracemalloc endpoint).
- Historical memory baseline tracking (like `perf-baseline.json` for
  latency); growth bound is absolute, not relative.
- Running the gate against a live remote hub (operator diagnostic use
  of BC-142's metric, not a CI gate).

## Acceptance criteria (Gherkin)

```gherkin
Feature: make perf asserts bounded memory growth under shadow load

  Scenario: Clean baseline passes
    Given the perf gate runs with current main
    When the measurement window completes
    Then rss growth is <= 100 MB
    And the gate exits 0

  Scenario: Synthetic leak fails the gate
    Given the perf gate runs with a test fixture that leaks 200 MB
    When the measurement window completes
    Then rss growth is > 100 MB
    And the gate exits 1 with a Memory check FAIL row

  Scenario: Shadow write load exercises the path that killed the VPS
    Given the perf gate starts
    When the shadow-load background task posts audit events at
      15/sec for 60 seconds
    Then audit_index_queue_depth stays <= AUDIT_INDEX_MAX_BUFFER / 2
    And audit_index_dropped_events stays 0 (no backpressure overflow)

  Scenario: Monotonic growth caught at low absolute level
    Given RSS samples (seconds): 100, 105, 112, 118, 125, 130 MB
    When the memory check runs
    Then MONOTONIC_GROWTH flag is raised (30 MB / 60 s is still a leak
      even if the absolute bound is 100 MB)
    And the gate exits 1

  Scenario: BEACONRY_PERF_MEMORY_GROWTH_MB tunes the bound
    Given BEACONRY_PERF_MEMORY_GROWTH_MB=250 in the env
    When the gate runs
    Then the allowed growth is 250 MB (not 100)
```

## Capabilities delivered

- **CAP-T240: Memory-growth assertion in `make perf` ship gate**
  (type: technical, status: active)

  *`poc/scripts/perf_gate.py` samples `/healthz checks.rss_mb` at
  gate start + every 10 s during the shadow-write-load window + at
  end. Asserts `T1_rss - T0_rss <= BEACONRY_PERF_MEMORY_GROWTH_MB`
  (default 100). Also detects monotonic growth over ≥3 consecutive
  samples (slow-leak signature). Shadow-write-load: a background
  threading.Thread posts synthetic `_audit.append` events at
  ~15 events/sec for the duration of the measurement window,
  reproducing the live-fleet write pressure that killed the VPS on
  2026-11-17. Fails `make check-full` on leak. New
  `BEACONRY_PERF_MEMORY_GROWTH_MB` dev-tunable knob documented in
  VARIABLES.md.*

Supports CAP-O042 (BC-141 cap), CAP-T238 (BC-142 metric),
CAP-T239 (BC-143 investigation), CAP-T235 (BC-132 perf gate).
BC-144 closes the "shipped a leak, found out in prod" failure mode.

## Open questions

Resolved during drafting (2026-11-17):

1. Why 100 MB default growth bound? **Measured.** A clean hub boots
   at ~150 MB and settles ~180 MB after one scan cycle. 100 MB
   growth during a 60 s perf-gate window is comfortably above noise
   and comfortably below the BC-141 1 GB cap.
2. Monotonic growth threshold — 3 consecutive samples? **Yes.**
   One bad sample is noise; two is coincidence; three is a trend.
3. Shadow-load rate — 15 events/sec? **Matches live-fleet
   observed rate.** From the 2026-11-17 forensic analysis.
4. Does the gate run against live or in-process hub? **In-process,
   via TestClient — same as BC-132's gate.** Operator-side live-hub
   mode (`BEACONRY_PERF_HUB_URL`) can also run this; useful for
   post-deploy sanity checks.
5. What about tracemalloc output in the gate failure message?
   **No** — that's BC-143's investigation tool. The gate says
   "memory grew"; the operator enables tracemalloc separately to
   find out why.

## Links

- Direct companions: BC-141 (caps the damage), BC-142 (signal),
  BC-143 (investigation tool). BC-144 is the gate that keeps future
  BCs from re-shipping the leak class.
- Extends: BC-132 perf gate (same code path, same TestClient, same
  perf-exceptions.toml).
