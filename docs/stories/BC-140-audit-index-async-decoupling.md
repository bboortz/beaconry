---
id: BC-140
title: Audit-index async decoupling — bounded queue + overflow metrics + graceful drain
status: draft
created: 2026-11-17
branch: feat/BC-139-BC-140-audit-index-live-load-fix
plan: docs/plan/BC-140-audit-index-async-decoupling-plan.md
---

# BC-140: Audit-index async decoupling — bounded queue + overflow metrics + graceful drain

## Narrative

BC-139 reduced the audit-index write amplitude by 20× (skip filter
+ 2 s batch flush) and bounded the WAL file. That's the amplitude
fix. **BC-140 is the coupling fix**: audit writes still flow from
inside FastAPI request handlers through a sync code path that can
touch sqlite. Under normal load that's fine; under pathological
load (sqlite stalls, disk saturation, lock contention) it can still
block the asyncio event loop, which means `/readyz` blocks, which
means Docker healthchecks fail, which means container restart loops.

The 04:04 hetzner-staging outage followed that exact chain: audit
writes stalled → event loop blocked → healthcheck timed out →
container died. BC-139 prevents the stall; BC-140 ensures that
even if a stall somehow recurs, the request-handling event loop
can't be taken hostage by it.

The clean shape: `_audit_index_append` writes to a bounded buffer
non-blockingly (just appends under a cheap threading.Lock, never
waits on sqlite). A dedicated background task is the ONLY code
path that opens the sqlite connection, batches, and commits. Hot
path and IO path are fully decoupled.

Plus: when the buffer overflows (producer faster than consumer,
sustained for the queue window), we count dropped events, expose
the counter on `/healthz`, and log a warning. Audit observability
can safely be the thing that gets lossy under extreme load —
`audit.log` still has every event durably — but the operator
needs to SEE that it happened.

## Trigger

- Any hub where sqlite write latency ever spikes (slow disk,
  checkpoint contention, host-level IO contention).
- Also applies in the normal case: BC-140 is a hardening change,
  not a specific-bug fix. It's the architecture that lets BC-132's
  benefit (indexed queries) survive any future failure mode of
  the write path.

## Expected outcome

- `_audit_index_append` runs in O(1) + O(lock-acquire) time. It
  never opens a sqlite connection, never waits on IO.
- A single long-lived background task owns the sqlite connection
  and does all writes. On bounded-buffer overflow, oldest pending
  events get dropped (not silently; see metric below). JSONL is
  unaffected — those events are still on disk.
- New `/healthz` fields: `audit_index_queue_depth` (current buffer
  size) and `audit_index_dropped_events` (cumulative overflow
  counter). Gives operators "is the audit index falling behind?"
  visibility.
- A new `audit_index_dropped` audit event fires (on the JSONL side)
  the first time the buffer overflows in a given 1-minute window
  — so the forensic trail captures the degradation without
  flooding the log.
- Lifespan shutdown drains the buffer before closing the sqlite
  connection (no data lost on orderly shutdown; only on SIGKILL
  do pending buffer entries get discarded — and they're still in
  JSONL regardless).
- Perf regression: zero (BC-140 preserves BC-139's batching + skip
  + WAL bounds; adds queue bounds + metrics on top).

## Out of scope

- Switching to a real queue broker (Redis, NATS). Keep in-process.
- Making audit.log itself asynchronous — JSONL writes are already
  <1 ms under normal load, and they're the durability contract.
  No decoupling needed there.
- Backpressure on `_audit.append` callers (e.g. return `False` so
  caller can retry). Audit is intentionally fire-and-forget
  (BC-115 contract — "never raise from the audit path"). Overflow
  is the right escape valve, not backpressure.
- Multi-writer support. One background flusher is enough; adding
  more would reintroduce the contention BC-132 fell over on.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Audit-index writes are fully decoupled from the FastAPI event loop

  Scenario: Hot-path append never opens a sqlite connection
    Given _audit.append is called from a FastAPI request handler
    When the post-append hook `_audit_index_append` runs
    Then no sqlite3.connect call happens in the calling thread
    And the hook returns in <1 ms even if sqlite is slow

  Scenario: Buffer overflow is bounded and surfaced
    Given a sustained append rate of 10 000 events/sec for 5 seconds
    And the flush loop is paused (simulated backpressure)
    When the buffer reaches `AUDIT_INDEX_MAX_BUFFER` (10 000)
    Then subsequent appends drop oldest buffered events
    And `audit_index_dropped_events` counter increments per drop
    And a single `audit_index_dropped` audit event fires per minute

  Scenario: Shutdown drains the buffer
    Given 50 events are queued in the buffer
    When the lifespan shutdown handler fires
    Then the final flush drains all 50 events to sqlite before close
    And the audit_index table contains all 50 rows

  Scenario: /healthz surfaces queue state
    Given a hub running normally
    When operator curls /healthz
    Then the checks object contains:
      - audit_index_queue_depth: int (current buffer size)
      - audit_index_dropped_events: int (cumulative drops)
    And both are 0 under steady state

  Scenario: Flush failure doesn't lose buffered events
    Given 20 events are in the buffer
    When the background flush hits sqlite3.OperationalError
    Then the connection is reset
    And the buffer is NOT drained (events preserved for next retry)
    And the next successful flush writes all 20

  Scenario: Append never blocks the asyncio event loop
    Given 1 000 concurrent FastAPI requests each triggering an audit event
    When all 1 000 requests complete
    Then no request took >50 ms on the hub side (excluding scan work)
    And /healthz responds in <30 ms throughout
```

## Capabilities delivered

- **CAP-T237: Audit-index writes fully decoupled from the FastAPI
  event loop** (type: technical, status: active)

  *`_audit_index_append` writes to a `collections.deque(maxlen=N)`
  guarded by a short-lived threading.Lock. The hot path is O(1)
  wall-clock with no sqlite interaction. A dedicated background
  task spawned from the lifespan owns the single sqlite connection
  and all INSERTs. Buffer overflow drops oldest entries; counter
  exposed on `/healthz` as `audit_index_dropped_events`; a
  rate-limited `audit_index_dropped` audit event fires so operator
  forensics stays useful. Shutdown drain path flushes remaining
  buffer before closing the connection; shutdown is bounded to 2 s
  so a wedged flush doesn't block container kill. Perf gate learns
  the `audit_index_queue_depth` signal so a stuck-flusher regression
  is catchable in CI without running the live hub.*

Extends CAP-T236 (BC-139 batching + skip + WAL), CAP-T234 (BC-131
tail-read fallback). Supersedes BC-132 + BC-139's sync hot-path
model with a fully asynchronous decoupling.

## Open questions

Resolved during drafting (2026-11-17):

1. `collections.deque(maxlen=N)` vs `queue.Queue(maxsize=N)` vs
   `asyncio.Queue`? **deque with Lock.** Thread-safe-enough with a
   short-held lock; fast; doesn't require event-loop awareness at
   the producer side (which lets us call it from both sync and
   async code paths without `call_soon_threadsafe` footguns).
2. Buffer max size — 10 000 entries? **Yes.** At ~500 bytes per
   serialized event body in the buffer, that's ~5 MB memory
   ceiling. More than enough for a 10 s burst at live rates;
   less than 1 % of a 1 GB VPS's RAM.
3. Shutdown timeout — how long to wait for drain before giving up?
   **2 seconds.** docker stop grace is 10 s; we want to leave room
   for other shutdown hooks (Telegram app shutdown is ~500 ms,
   KEV refresh cancellation, etc.). 2 s covers the 90th-percentile
   sqlite flush time by a wide margin.
4. Rate-limit on the `audit_index_dropped` audit event? **1/minute.**
   Otherwise the overflow itself produces a cascade of audit
   writes — bad shape under already-stressed conditions. The
   counter on /healthz is the real signal; the audit event is
   just the forensic breadcrumb for later investigation.
5. Should the perf gate's shadow-load concurrent-write scenario
   check queue depth? **Yes.** After the shadow writes run, the
   gate asserts `audit_index_queue_depth < AUDIT_INDEX_MAX_BUFFER / 2`
   — i.e. the flusher keeps up. Catches regressions where
   someone makes flushing slower.

## Links

- Direct predecessor: BC-139 (write-rate + batching fix). BC-140
  layers architectural decoupling on top of 139's amplitude fix.
- Related: BC-130 (write-ordering fix — same philosophy of "sync
  sqlite in hot paths is bad"), BC-115 (audit-append degraded
  state — BC-140's overflow counter reuses that pattern).
- Indirect cause: BC-132 (introduced the coupling that forced
  this fix).
- Design: `memory/design-decisions.md § Separation of concerns`
  — BC-140's split between hot path (producer) and flush task
  (consumer) is the textbook application of that principle to the
  audit-write path.
