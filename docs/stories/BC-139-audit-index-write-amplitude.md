---
id: BC-139
title: Audit-index write-amplitude fix — skip filter + batched flush + bounded WAL
status: draft
created: 2026-11-17
branch: feat/BC-139-BC-140-audit-index-live-load-fix
plan: docs/plan/BC-139-audit-index-write-amplitude-plan.md
---

# BC-139: Audit-index write-amplitude fix — skip filter + batched flush + bounded WAL

## Narrative

BC-132 shipped a sidecar SQLite `audit_index` table and wired every
`_audit.append` through a post-append hook that opens a cached WAL
connection and commits one INSERT per event. In-process perf tests
with the deterministic fixture (10 k events, isolated DB, no
concurrent writes) showed sub-10 ms query latency even at 1 M events.
The live fleet behaved very differently.

Within 10 minutes of the BC-132 deploy to hetzner-staging:

- `/audit*` query latency degraded **179 ms → 2 s → 4 s** across two
  5-minute probe windows.
- 14 minutes after deploy, the entire VPS host (not just the hub
  container) fell off the network. TCP ports 8000 and 22 both
  unreachable. The host stayed down for ~30 minutes before auto-
  recovering.
- A fresh `hub_startup` audit event at 04:34 UTC confirmed the host
  came back; the operator's Telegram received the delayed
  `scout_offline` alert for hetzner-staging.

Root cause: `_audit_index_append` commits one INSERT per audit event.
At live scout cadence the hub writes ~15 events/sec (dominated by
`finding_filtered` — 85 % of audit volume), which translates to ~15
fsyncs/sec on the SQLite WAL. A long-running `/audit/integrity?limit=500`
read pins a snapshot, blocking WAL truncation. Combined with the
cached-connection pattern that serializes all writes through one
sqlite handle, the WAL grew unbounded until the VPS filesystem +
sqlite checkpoint stalled into the host-level failure.

The in-process gate couldn't catch this because its fixture seeder
writes events once (no concurrent scout `/scan` posts) and the test
hub has no sustained write pressure.

## Trigger

- Any hub running BC-132 that receives concurrent scout `/scan` posts
  at realistic cadence (each scan = up to 6 700 `finding_filtered`
  events hitting the hook as serial fsyncs).
- Reader-side: any operator CLI (`beaconry audit`, `beaconry verify-
  integrity`) querying while a scan is in flight pins a WAL snapshot,
  preventing checkpoint truncation.
- Observed on hetzner-staging within 14 minutes of BC-132 deploy.

## Expected outcome

- `_audit_index_append` writes drop from 15/sec → ~0.5/sec steady-
  state. 85 % of audit events (`finding_filtered`) skip the index
  entirely — they still land in audit.log (durable truth) but are
  not mirrored into SQLite. `heartbeat_received` (10 %) likewise.
  Operators never filter audit queries by these types in practice
  (`beaconry findings` is the view for `finding_filtered` content;
  heartbeats are structural noise).
- Writes that DO reach the index are **batched**: a module-level
  buffer + threading lock + 2-second background flush loop. A
  single scan burst that previously fired 50 serial fsyncs now
  fires one `executemany` + one commit.
- The SQLite WAL is bounded: `wal_autocheckpoint=100` (vs default
  1 000) + `journal_size_limit=10 MB`. Readers can pin snapshots
  without starving the checkpointer indefinitely.
- No regression in query latency for the operator CLI flows. The
  live `/audit?event=hub_startup&last=10` still resolves via the
  indexed path in <100 ms post-fix.
- The in-process perf gate adds a **concurrent-write shadow load**
  (background task posting /scan at live-fleet cadence during the
  measurement window) so future regressions in this class are
  caught at review time, not post-deploy.

## Out of scope

- The actual asyncio decoupling of the hot path — BC-140 handles
  that. BC-139 keeps the sync-path shape; BC-140 moves it fully off
  the event loop.
- Audit log rotation — separate chore. BC-139 bounds the SQLite
  side; audit.log bounds need their own BC (previously BC-138).
- Changing the sidecar index to a different storage (e.g. per-day
  tables, external DB). The current table + indexes are fine; the
  write pattern was the bug.
- Adding per-event-type retention policy. Still 90-day
  `audit_retention_days` horizon future work (also BC-138).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Audit-index write path survives live scout load

  Scenario: finding_filtered events skip the index
    Given _audit.append fires with event="finding_filtered"
    When the post-append hook runs
    Then NO INSERT into audit_index is issued
    And audit.log still receives the JSONL line

  Scenario: heartbeat_received events skip the index
    Given _audit.append fires with event="heartbeat_received"
    When the post-append hook runs
    Then NO INSERT into audit_index is issued

  Scenario: Non-skipped events buffer then flush as a batch
    Given 10 scan_integrity events fire within 500 ms
    When the background flush task runs after 2 s
    Then a single executemany writes all 10 rows in one commit
    And the audit_index table contains exactly 10 rows
    And exactly 1 sqlite commit was issued (not 10)

  Scenario: Buffer is drained on hub shutdown
    Given 5 scan_integrity events are in the buffer unflushed
    When the lifespan shutdown handler runs
    Then the flush-on-exit path drains the buffer
    And the audit_index table contains all 5 rows

  Scenario: WAL file is capped
    Given a burst of 1 000 scan_integrity inserts over a short window
    When the WAL file grows
    Then it never exceeds `journal_size_limit` (10 MB)
    And autocheckpoint fires at 100 pages

  Scenario: Query path unchanged — indexed reads stay fast
    Given a populated audit_index with 100 k events
    When operator runs `/audit?event=hub_startup&last=10`
    Then the response arrives in <100 ms (BC-132's indexed-read path
      is unaffected by the write-side changes)

  Scenario: In-process perf gate catches concurrent-write regression
    Given a hub with BC-139 reverted (one-commit-per-event again)
    When `make perf` runs with the concurrent-write shadow load
    Then at least one /audit* endpoint breaches the 300 ms budget
    And the gate fails
```

## Capabilities delivered

- **CAP-T236: Audit-index write path survives live-load concurrent
  scans** (type: technical, status: active)

  *New `_AUDIT_INDEX_SKIP_EVENTS = {"finding_filtered",
  "heartbeat_received"}` filter at the top of `_audit_index_append`
  drops the 95 % of audit volume operators never query by. Remaining
  events go into a module-level `list[tuple]` buffer guarded by
  `threading.Lock`. A lifespan-spawned background task flushes the
  buffer every 2 s via `executemany` + one commit. The WAL
  connection sets `wal_autocheckpoint=100` and `journal_size_limit=
  10 MB` so a pinned reader-snapshot can't grow the WAL unbounded.
  Shutdown handler drains the buffer before closing the connection.
  The perf gate gains a concurrent-write shadow loader so this class
  of regression (sustained write pressure contending with indexed
  reads) is catchable at review time instead of post-deploy.*

Extends CAP-T234 (BC-131 tail-read), CAP-T235 (BC-132 index +
perf gate). Supersedes BC-132's cached-connection-plus-sync-commit
write path.

## Open questions

Resolved during drafting (2026-11-17):

1. Why skip `finding_filtered` + `heartbeat_received` specifically?
   **Volume + operator query patterns.** Measured live rates:
   finding_filtered = 85 % of all audit events, heartbeat_received
   = 10 %. Combined 95 % of write pressure. Operator CLI flows:
   `beaconry findings` shows finding_filtered content via the
   findings DB, not audit queries; `beaconry audit` defaults filter
   out these types in practice. Documented; future BC can tune the
   set.
2. Batch size + flush interval — why 50 and 2 s? **Conservative
   tradeoff.** 50 events × ~500 bytes = 25 KB per batch; one WAL
   fsync per 2 s = ~0.5 fsync/sec steady state. 10× reduction in
   fsync rate vs BC-132's per-event commit. Tunable via
   `BEACONRY_AUDIT_INDEX_BATCH_SIZE` + `_FLUSH_INTERVAL_S`.
3. Why threading.Lock instead of asyncio.Lock? **Pragmatism.**
   `_audit.append` is called from both sync and async code paths;
   a threading lock works in both. BC-140 migrates to a fully
   asyncio-native queue for the next layer of correctness.
4. Shutdown ordering — flush buffer before closing connection?
   **Yes.** The flush task's cancellation handler drains the buffer
   synchronously before `_reset_audit_index_conn()`. Captured in
   the Gherkin scenario.
5. What if the flush itself fails (disk full, lock timeout)?
   **Log + reset connection, keep the buffer.** On next flush
   interval, retry. Fail-open: never raise from the audit path.
   Consistent with BC-132's current behavior.

## Links

- Direct cause: BC-132 (introduced the write-amplitude regression).
- Related: BC-130 (write-ordering fix — same shape of "sync blocking
  in a hot path hurts scalability"), BC-131 (indexed-read
  foundation).
- Follow-up: BC-140 (asyncio-queue decoupling — fully removes sync
  sqlite from the request path).
- Deferred: BC-138 (audit log rotation — bounds the JSONL side).
