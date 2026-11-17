# BC-140 — Audit-index async decoupling

*Branch: `feat/BC-139-BC-140-audit-index-live-load-fix`*
*Start date: 2026-11-17*
*Story: `docs/stories/BC-140-audit-index-async-decoupling.md`*

---

## Situation

BC-139 drops write amplitude 20× and bounds the WAL. It eliminates
the specific failure chain that killed hetzner-staging. But the
`_audit_index_append` hot path still touches a module-level buffer
guarded by `threading.Lock`; if a future bug reintroduces sync
sqlite calls into the write path, the same class of event-loop
blockage returns.

BC-140 removes the coupling for good: the hot path writes to a
`collections.deque(maxlen=N)` and nothing else. A dedicated
background task is the ONLY code that opens the sqlite connection.
On buffer overflow, oldest entries drop (counter exposed via
`/healthz`); JSONL audit.log remains lossless.

---

## Problems to solve

- **P1 — residual coupling**: BC-139's hot-path `threading.Lock`
  could in principle contend with a slow flush (flush holds lock,
  producer waits). Worst-case: a few ms stall. Not catastrophic
  but fragile.
- **P2 — no overflow visibility**: unbounded buffer growth was BC-139's
  escape valve. If flushing stalls indefinitely, memory could grow
  unbounded. We need a bounded buffer + a visible signal when it
  overflows.
- **P3 — shutdown correctness**: BC-139 drains on shutdown via the
  same lock. If shutdown is killed before drain completes, events
  are lost. BC-140 bounds the drain window so docker-stop grace
  isn't exceeded.

---

## Goals

1. Replace `list[tuple]` + lock with `collections.deque(maxlen=
   AUDIT_INDEX_MAX_BUFFER)` + short-held lock. Producer never
   waits on IO. (Gherkin scenario 1)
2. Overflow (buffer full) drops oldest entry; module counter
   `_audit_index_dropped_events: int` increments. (scenario 2)
3. Lifespan shutdown drains the buffer in <2 s before closing the
   sqlite connection. (scenario 3)
4. New `/healthz` fields: `audit_index_queue_depth` (current len),
   `audit_index_dropped_events` (cumulative). (scenario 4)
5. Flush-failure doesn't lose buffered events; preserves them for
   next retry. (scenario 5)
6. Append under all load scenarios returns in <1 ms; event loop
   stays responsive. (scenario 6)
7. Rate-limited `audit_index_dropped` audit event: 1/minute on
   overflow so forensics has a trail but doesn't cascade.

---

## Scope

- `poc/hub/hub.py` (layered on top of BC-139):
  - `_audit_index_buffer: list[tuple]` → `collections.deque(maxlen=
    AUDIT_INDEX_MAX_BUFFER)`. Default 10 000.
  - New module state: `_audit_index_dropped_events: int`,
    `_audit_index_last_dropped_audit_ts: int`.
  - `_audit_index_append`: append under short-held lock; if deque
    was full, the maxlen mechanism dropped oldest — detect by
    sampling `len()` and comparing against maxlen, increment
    counter, emit the rate-limited `audit_index_dropped` event via
    `_audit.append` (not through our own hook, to avoid recursion).
  - `_flush_audit_index_batch_locked`: drain via `list(deque)` +
    `deque.clear()`; executemany + commit. On failure, re-prepend
    drained events to the deque so retry can pick them up. (If
    deque is still bounded, the oldest of the retry-list drops —
    acceptable; metric captures it.)
  - Shutdown: `_audit_index_flush_task.cancel()` with a 2-second
    `asyncio.wait_for` + final drain + `_reset_audit_index_conn`.
- `/healthz`:
  - `audit_index_queue_depth` and `audit_index_dropped_events`
    added to the `checks` block.
- `poc/scripts/perf_gate.py`:
  - After the shadow-load run, assert `audit_index_queue_depth <
    AUDIT_INDEX_MAX_BUFFER / 2` (flusher keeping up).
- `poc/tests/unit/test_BC_139_BC_140_audit_index_live_load.py` — new.
- `docs/CAPABILITIES.md`: CAP-T237.
- `docs/VARIABLES.md`: `BEACONRY_AUDIT_INDEX_MAX_BUFFER` dev-tunable.

No scout changes. No HTTP surface changes beyond the two new
`checks` fields. No DB schema changes.

---

## Non-goals

- Redis / NATS / external queue. In-process deque is enough.
- Backpressure to `_audit.append` callers.
- Multi-writer / multi-flusher parallelism. One flusher.
- Changing BC-139's batching cadence (2 s / 50 events).
- Durability guarantees for buffered events beyond JSONL. Audit.log
  is the durability contract; the index is the query path.

---

## Design decisions

- **`collections.deque(maxlen=N)` not `queue.Queue`**: deque is a
  simple data structure, cheap to inspect (`len()`), drops oldest
  automatically on `append()` when at maxlen. No native "I was
  full" signal — we detect via `len()` sampling pre/post append.
- **Short-held threading.Lock**: BC-139 pattern preserved. deque
  operations are thread-safe individually but we need atomic
  "check-len + append" to detect the drop.
- **Overflow = drop oldest**: data-loss semantics match the JSONL
  invariant. The newest events are most actionable in a live
  incident; oldest events are already in audit.log.
- **Rate-limited `audit_index_dropped` audit event (1/min)**: writes
  to JSONL only (avoids feeding the stressed index). Operator-
  visible via `beaconry audit --event audit_index_dropped`.
- **Flush retry**: on `sqlite3.OperationalError`, re-queue the
  batch into the deque (front-insert), reset connection, let next
  flush try again. Loss happens only if deque fills during the
  retry → metric increments → operator sees it.
- **Shutdown drain timeout 2 s**: fits inside docker-stop's 10 s
  grace with headroom for Telegram + KEV + healthcheck cleanup.
- **Preserve BC-139's skip filter + WAL PRAGMAs**: unchanged.
- **Preserve `_reset_audit_index_conn()` on error**: unchanged.

---

## Breaking changes

### 1. API / return shape

- `/healthz` adds two new fields in `checks`:
  `audit_index_queue_depth` (int), `audit_index_dropped_events` (int).
  Additive; existing consumers tolerate extra keys.
- New audit event type `audit_index_dropped` — appears in audit.log
  and (since it's NOT in `_AUDIT_INDEX_SKIP_EVENTS`) in audit_index.
  Low-frequency (1/min max) so no volume concern.

### 2. Data model (schema + legacy rows)

- **None.** audit_index table unchanged.

### 3. Behavioral semantics

- **New loss mode**: sustained overflow can drop audit_index entries.
  Operator-facing: `beaconry audit` may return slightly fewer
  recent rows than audit.log actually contains during extreme load.
  Mitigation: audit.log is the durable truth; tail-read fallback
  retrieves what's missing from the index. The `audit_index_dropped`
  counter + event tells the operator when it happened.
- **Shutdown behavior**: SIGKILL loses up to `AUDIT_INDEX_MAX_BUFFER`
  pending events from the index (not audit.log). Expected trade-off
  for bounded shutdown time.
- **No change to audit.log semantics**: all events still durably
  written there, unchanged.

---

## Merge plan

See BC-139's plan — both BCs share one branch + PR.

---

## Rollout plan

Same as BC-139 — co-ships. Post-deploy operator verification: curl
/healthz and confirm `audit_index_queue_depth` + `audit_index_dropped_events`
both appear and are 0 under steady load.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Hot-path append opens no sqlite | unit | `test_BC_139_BC_140_audit_index_live_load.py::test_append_never_opens_sqlite` | ✓ |
| Overflow drops oldest + increments counter | unit | `test_overflow_drops_oldest_bumps_counter` | ✓ |
| Shutdown drains in <2 s | unit | `test_shutdown_drain_bounded_by_2s` | ✓ |
| /healthz surfaces queue state | unit | `test_healthz_surfaces_queue_depth_and_dropped` | ✓ |
| Flush failure preserves events | unit | `test_flush_failure_re_queues_batch` | ✓ |
| Rate-limited dropped audit event | unit | `test_dropped_audit_event_rate_limited_1_per_minute` | ✓ |
| Append under 1000-concurrent load stays fast | unit | `test_append_stays_fast_under_concurrent_producers` | ✓ |
| Live post-deploy: /healthz shows new fields | live | manual curl on hetzner-staging post-deploy | ❌ pending |
| Memory drift pattern | TOML | N/A — additive | ✓ |

---

## Testing strategy

Unit tests use a mocked sqlite3.connect that can be told to raise
or sleep, letting us simulate slow-flush conditions deterministically.
The buffer-overflow test fills the deque to maxlen+100 and asserts
the dropped-events counter equals 100.

The "never opens sqlite" test monkeypatches sqlite3.connect to raise
if called; running `_audit_index_append` must NOT trigger the raise.

The shutdown drain test schedules the flush task, pre-fills the
buffer, triggers shutdown, and asserts the final table count == buffer
size within the 2 s window.

Live verification (post-deploy): with hub running, curl /healthz and
verify both new fields are present + 0. Then trigger a bb-82jq scan
and observe the depth transiently rise + return to 0 within a few
seconds.

---

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_139_BC_140_audit_index_live_load.py -v

# Post-deploy live check:
curl -s http://100.77.182.73:8000/healthz | jq .checks.audit_index_queue_depth
curl -s http://100.77.182.73:8000/healthz | jq .checks.audit_index_dropped_events
# Both should be 0 steady-state, briefly rise during scan bursts.
```
