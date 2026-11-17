# BC-139 — Audit-index write-amplitude fix

*Branch: `feat/BC-139-BC-140-audit-index-live-load-fix`*
*Start date: 2026-11-17*
*Story: `docs/stories/BC-139-audit-index-write-amplitude.md`*

---

## Situation

BC-132 shipped per-event commits through a shared cached sqlite
connection. In-process tests passed; live fleet killed the VPS host
within 14 minutes. Forensic timeline (UTC):

```
03:50  BC-132 deploy to hetzner-staging
04:00  audit queries at 2-4 s (6× over budget)
04:04  hub + VPS unreachable on TCP:8000 AND TCP:22
04:34  host returns; fresh hub_startup event fires
```

Root cause: 15 fsyncs/sec of sqlite writes on the cached connection,
unbounded WAL growth pinned open by concurrent readers, eventual host-
level resource exhaustion. 95 % of audit volume is two event types
(`finding_filtered` 85 %, `heartbeat_received` 10 %) that operators
never query by.

---

## Problems to solve

- **P1 — unbounded write amplitude**: `_audit_index_append` commits one
  INSERT per event. Big scans produce 500-6 700 serial fsyncs.
- **P2 — unbounded WAL growth**: default `wal_autocheckpoint=1 000` +
  no `journal_size_limit` means a pinned reader snapshot can let the
  WAL grow until disk is full.
- **P3 — in-process gate blind to concurrent writes**: the perf
  fixture writes events once at seed time; no sustained write
  pressure during measurement.

---

## Goals

1. Skip-filter `finding_filtered` + `heartbeat_received` from the
   audit_index post-append hook. Events still in audit.log; just not
   mirrored to SQLite. (Gherkin scenarios 1 + 2)
2. Buffer non-skipped events in a module-level `list[tuple]` guarded
   by `threading.Lock`; flush every 2 s via a background asyncio
   task; one `executemany` + commit per flush window. (scenario 3)
3. Drain buffer on lifespan shutdown. (scenario 4)
4. Set `wal_autocheckpoint=100` + `journal_size_limit=10 MB` on the
   cached connection. (scenario 5)
5. Indexed read path unchanged — preserves BC-132's benefit.
   (scenario 6)
6. Perf gate learns a **concurrent-write shadow load**: during the
   measurement window a background task posts synthetic audit events
   at live-fleet rate. (scenario 7)

---

## Scope

- `poc/hub/hub.py`:
  - Module constants: `_AUDIT_INDEX_SKIP_EVENTS`,
    `_AUDIT_INDEX_BATCH_SIZE` (50, env-overridable),
    `_AUDIT_INDEX_FLUSH_INTERVAL_S` (2.0, env-overridable).
  - Module state: `_audit_index_buffer: list[tuple]`,
    `_audit_index_lock = threading.Lock()`,
    `_audit_index_flush_task: asyncio.Task | None`.
  - Rewrite `_audit_index_append`: skip filter → tuple-ify → append
    under lock. Zero sqlite interaction on the hot path.
  - New `_flush_audit_index_batch_locked()`: consumed buffer →
    `executemany` on the cached connection → clear buffer on
    success. Failure resets the connection (BC-132 behaviour
    preserved).
  - New `async _audit_index_flush_loop_forever()`: `await
    asyncio.sleep(_FLUSH_INTERVAL_S)` + `with _audit_index_lock:`
    + `_flush_audit_index_batch_locked()`.
  - Lifespan startup: spawn the flush task after the post-append
    hook is registered + `_rebuild_audit_index_if_empty()`.
  - Lifespan shutdown: cancel the task, then one final flush, then
    `_reset_audit_index_conn()`.
  - Cached connection's open-time PRAGMAs: add
    `wal_autocheckpoint=100` + `journal_size_limit=10485760`.
- `poc/scripts/perf_gate.py`:
  - New `--shadow-write-load` flag (default on when running
    in-process; off for `BEACONRY_PERF_HUB_URL` mode).
  - Spawns a background thread posting synthetic `_audit.append`
    at ~15/sec during the measurement. Captures P95 under concurrent
    write load.
  - New exception entry allowed: if the concurrent-write shadow is
    enabled, the gate checks that queue depth ≤ 10 after the final
    probe (reuses metric from BC-140).
- `poc/tests/unit/test_BC_139_BC_140_audit_index_live_load.py` — new.
- `docs/CAPABILITIES.md`: CAP-T236.
- `docs/VARIABLES.md`: `BEACONRY_AUDIT_INDEX_BATCH_SIZE`,
  `BEACONRY_AUDIT_INDEX_FLUSH_INTERVAL_S` dev-tunables.

No scout changes. No DB schema changes. No new HTTP endpoints.

---

## Non-goals

- Switching from threading.Lock to asyncio.Queue — BC-140.
- Overflow handling / dropped-events counter — BC-140.
- Audit log rotation — separate future story.
- Per-event-type retention on the SQLite side — future.
- Changing the skip-filter default via config — future (hard-coded
  for now; env can override).

---

## Design decisions

- **Skip at the top of the hook**, not at the audit.append layer.
  Keeps audit.py free of indexing concerns. The filter is a shared
  constant so BC-140's queue path can reuse it.
- **threading.Lock, not asyncio.Lock**. Callers of `_audit.append`
  are a mix of sync + async; a threading lock works both ways
  without `loop.call_soon_threadsafe` ceremony.
- **Buffer is `list[tuple]`, not `deque`** in BC-139. deque is
  BC-140's bounded-queue choice. In BC-139 the buffer is effectively
  unbounded (2 s flush window × ~0.5 events/sec post-skip ≈ 1
  event waiting at steady state) so no bound is needed yet.
- **Flush interval + batch size are env-tunable**. Default 2 s / 50.
  Operator never touches these in normal operation; dev-only.
- **WAL autocheckpoint 100 pages** = ~400 KB. Aggressive, but
  matches our write pattern (low-frequency bursts). Default 1 000 =
  4 MB is sized for OLTP-like workloads, not our sparse + bursty
  shape.
- **`journal_size_limit = 10 MB` hard cap**. Even if autocheckpoint
  can't fire (reader holds snapshot for an extended query), the WAL
  can't exceed 10 MB. That bounds the disk-space failure mode.
- **Flush on shutdown is best-effort, not transactional**. If
  shutdown kills us mid-flush, the next hub boot's rebuild path
  picks up the un-mirrored audit.log entries.
- **Flush loop is one task, not per-host**. Single consumer keeps
  the sqlite connection busy without lock contention within the
  flusher.
- **Preserve BC-132's `_reset_audit_index_conn()` reset on error**
  — a stuck connection shouldn't poison subsequent flushes.

---

## Breaking changes

### 1. API / return shape

- **None on HTTP surface.** No endpoint signature or response shape
  changes.
- `_audit_index_append` return type stays `None`; callers already
  ignore it.
- `_flush_audit_index_batch_locked` + `_audit_index_flush_loop_forever`
  are new module-private symbols; no external API.

### 2. Data model (schema + legacy rows)

- **None.** audit_index table unchanged. audit.log file unchanged.
- The skip filter means audit_index no longer contains rows for
  `finding_filtered` / `heartbeat_received` on hubs running BC-139+.
  Queries that filter by those event types will return empty via
  the indexed path; the `_tail_audit_events` fallback still finds
  them. **Operator-visible rows returned for those event types may
  take 10 s instead of 10 ms post-BC-139** — but no one queries by
  those types in practice, so this is informational.
- Pre-BC-139 rows for the skipped types stay in the table (no
  retroactive delete).

### 3. Behavioral semantics

- **Audit index freshness lag: up to 2 seconds.** Previously an
  event was in the index within milliseconds of the audit.append
  call. Now it's within the 2-second flush window. Operators
  running `beaconry audit --last 1` immediately after a scan may
  need to wait a flush interval for the newest event to appear in
  the indexed path (tail-read fallback still returns it instantly
  from audit.log).
- **Faster hub under load.** The visible operator improvement is
  that `/audit*` queries no longer stall during a bb-82jq scan
  (which was the trigger case for the hetzner-staging outage).
- **Perf gate runtime increases** by ~30 s due to the shadow-load
  component; `make check-full` total time grows by that much.

---

## Merge plan

1. Branch: `feat/BC-139-BC-140-audit-index-live-load-fix`.
2. Commit 1: BC-139 (this story).
3. Commit 2: BC-140 (sibling story).
4. `make check` must pass; `BEACONRY_SKIP_DOGFOOD=1 make check-full`
   including the new concurrent-write perf scenario.
5. PR to `dev` includes BOTH BCs; title: `fix(hub): BC-139 + BC-140
   — audit-index live-load regression + async decoupling`.
6. Merge strategy: squash (one squash commit, both BCs in the body).
7. Post-merge: Release workflow → v0.22.17 → forge rebuild → fleet
   deploy → verify audit queries stay <100 ms during an active
   bb-82jq scan.

---

## Rollout plan

- No migration. No new env vars that operators need to set in
  production (dev-tunable env knobs only).
- Hub-only change; scouts unchanged.
- Pre-existing audit_index rows for skipped event types stay
  (not deleted). They're accessible via tail-read for anyone still
  investigating pre-BC-139 incidents.
- Expected observable: `/audit?event=hub_startup&last=10` goes from
  the 2-4 s we saw mid-outage back to <100 ms post-deploy.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| finding_filtered skipped | unit | `test_BC_139_BC_140_audit_index_live_load.py::test_skip_filter_finding_filtered` | ✓ |
| heartbeat_received skipped | unit | `test_skip_filter_heartbeat_received` | ✓ |
| Events batch into one commit | unit | `test_batched_flush_single_commit` | ✓ |
| Flush interval timing | unit | `test_flush_interval_2s_background_task` | ✓ |
| Shutdown drains buffer | unit | `test_shutdown_flush_drains_buffer` | ✓ |
| WAL PRAGMAs applied | unit | `test_wal_pragmas_applied_on_connection_open` | ✓ |
| Indexed read path still fast | integration | `test_BC_131_audit_read_perf.py` suite still green | ✓ |
| Perf gate shadow-load catches regression | unit | `test_perf_gate_shadow_write_load_catches_slow_flush` | ✓ |
| Live post-deploy — audit queries fast during a bb-82jq scan | live | manual: `time curl /audit?last=10` during scan → <100 ms | ❌ pending |
| Memory drift pattern | TOML | N/A — additive perf fix | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

---

## Testing strategy

Unit tests mock sqlite3.connect to count commits/opens + inspect
PRAGMAs. The flush-interval test uses `asyncio.sleep` with a
reduced interval (0.1 s) + `asyncio.wait_for` to assert the
background task flushes within one interval window.

The perf gate test runs the gate against an in-process hub with a
synthetic shadow-write load; asserts P95 stays under 300 ms while
shadow writes are in flight.

Live verification (post-deploy): operator runs `beaconry audit
--last 10` while a bb-82jq scan is in flight (easy to check — scans
run every ~5 min). Expected: response <200 ms.

---

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_139_BC_140_audit_index_live_load.py -v
BEACONRY_SKIP_DOGFOOD=1 make check-full

# Post-deploy live check:
# Trigger a bb-82jq scan then race a query against it:
beaconry scan HOST=bb-82jq
time curl -s http://100.77.182.73:8000/audit/integrity?limit=500 >/dev/null
# Expected: <500 ms even mid-scan
```
