---
id: BC-130
title: Diagnose + fix the hub-shutdown-race source of heartbeat_integrity_mismatch
status: draft
created: 2026-11-16
branch: feat/BC-130-diagnose-heartbeat-mismatch-race
plan: docs/plan/BC-130-diagnose-heartbeat-mismatch-race-plan.md
---

# BC-130: Diagnose + fix the hub-shutdown-race source of heartbeat_integrity_mismatch

## Narrative

Every hub deploy today produces ~1 `heartbeat_integrity_mismatch` audit
event in the next few minutes. By the end of 2026-11-16 (6 deploys
between BC-120 and BC-129's fleet rollout), the
`pipeline_integrity_mismatches` counter sat at 6. Investigating the
audit stream showed **every single event fires within minutes of a hub
restart**, never during steady operation.

Root cause located: `_ingest_candidates` updates the in-memory
`_last_scan_integrity_per_host[host]` dict **before** calling
`_persist_integrity_state(...)` (hub.py:5458-5462). If uvicorn is torn
down in the microseconds between those two statements (SIGTERM during
`make deploy-beaconry`), the new iteration hash is lost from disk but
scout already treats the scan as delivered. On next hub boot, SQLite
reloads the *previous* iteration hash; scout's first heartbeat then
reports a divergence that isn't real transport loss — just a bookkeeping
race the counter can't distinguish from an actionable integrity alarm.

The counter is fine; what's missing is diagnostic context (hub uptime
at mismatch time, cache source, boot ts) to tell deploy-race events
apart from real transport loss, AND a persistence-order tweak so the
race window vanishes.

## Trigger

- Every `docker stop` / `docker compose restart` of the hub during
  deploys.
- Confirmed 7 audit events in the 2026-11-16 forensic window; all
  correlate with known hub restarts.

## Expected outcome

- After BC-130 ships, the `pipeline_integrity_mismatches` counter stays
  **flat across deploys**. Any post-BC-130 event is a real transport-
  loss (silent /scan drop) or a real DB write failure, not a deploy
  race.
- `heartbeat_integrity_mismatch` events carry enough context in the
  audit payload that the operator can tell which class of failure fired
  without re-deriving timing from their shell history:
  - `hub_uptime_s` — seconds since this hub process booted.
  - `last_hub_startup_ts` — unix ts of the current hub's boot.
  - `hub_cache_last_updated_ts` — unix ts the integrity_state row for
    this host was last written to SQLite.
  - `cache_source` — `"sqlite_reload"` if the cached hash came from the
    boot-time reload (BC-123 hydration), `"live_scan"` once the current
    hub process has processed a /scan from this host.
- New `hub_startup` audit event pins each restart boundary into the
  audit stream (currently invisible there).
- `scan_integrity` event gains `cache_persisted_ok: bool` so a failed
  SQLite write (disk full, locked file, schema drift) is visible rather
  than silently leaking an in-memory-only cache delta.

## Out of scope

- Persisting scout's `_last_delivered_*` to `/data/scout-state.db` so
  scout restart also forgets correctly. Scout restart currently resets
  those to None (next heartbeat omits the claim; no mismatch possible).
  That's a separate story (and arguably a loss of cross-check coverage
  worth addressing — but not in this branch).
- Graceful uvicorn shutdown that drains in-flight /scan requests on
  SIGTERM before exit. The reorder closes the specific race BC-130
  targets without needing shutdown-draining complexity.
- Telegram alerting on mismatch (BEACONRY_PIPELINE_INTEGRITY_ALERT=1
  already opts into that for transfer_hash_mismatch; same gate applies
  if we want to add heartbeat alerts later).
- Changing the counter's name or `/healthz` surface — the counter is
  still the right tripwire, just with better forensics around each tick.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Hub shutdown race no longer produces spurious heartbeat_integrity_mismatch

  Scenario: Persist-first ordering closes the race
    Given a hub processing a /scan where the in-memory cache update
      and SQLite persist are two separate statements
    When the hub crashes between the two statements
    Then the in-memory cache must NOT be ahead of the SQLite row at any
      point — persist is the first of the two to run

  Scenario: hub_startup event emitted at boot
    Given a hub starting up
    When load_integrity_persistence finishes
    Then an audit event with event="hub_startup" is written containing
      software_version, cache_reload_ok, hosts_in_cache, and
      mismatch_counter_at_boot
    And subsequent audit queries can locate this event to establish the
      restart boundary

  Scenario: heartbeat_integrity_mismatch carries diagnostic fields
    Given a heartbeat mismatch fires (either NO_RECORD or HASH_DIFFERS)
    When _record_heartbeat_mismatch writes the audit event
    Then the event body contains hub_uptime_s, last_hub_startup_ts,
      hub_cache_last_updated_ts, and cache_source ∈ {"sqlite_reload",
      "live_scan", "no_record"}

  Scenario: cache_source distinguishes deploy-race from live divergence
    Given a scout heartbeat fires shortly after a hub restart
    And the cached hash came from the SQLite reload
    When the heartbeat mismatch audit event fires
    Then cache_source == "sqlite_reload" AND hub_uptime_s is small (<120)
      — the deploy-race signature
    Given instead a long-uptime hub with a cache value from a recent
      live /scan
    When a heartbeat mismatch fires
    Then cache_source == "live_scan" AND hub_uptime_s is large —
      the actionable transport-loss signature

  Scenario: scan_integrity carries cache_persisted_ok
    Given a scan is being processed
    When _ingest_candidates emits the scan_integrity audit event
    Then the event body contains cache_persisted_ok: bool reflecting
      whether _persist_integrity_state's SQLite commit succeeded

  Scenario: Counter stays flat across a clean deploy (live verification)
    Given the pipeline_integrity_mismatches counter is N
    When operator runs `make deploy-beaconry` with BC-130 shipped
    Then after the deploy settles, the counter is still N
    And no new heartbeat_integrity_mismatch event fires in the next hour
```

## Capabilities delivered

- **CAP-T233: Shutdown-race-free integrity-state persistence + forensic
  audit enrichment** (type: technical, status: active)

  *`_ingest_candidates` now calls `_persist_integrity_state` BEFORE
  mutating `_last_scan_integrity_per_host[host]`, so SQLite durability
  gates every advance of the in-memory cache — the microsecond
  "in-memory ahead of disk" race window is gone. `hub_startup` audit
  event pins every hub boot into the audit stream.
  `heartbeat_integrity_mismatch` grows four correlation fields
  (`hub_uptime_s`, `last_hub_startup_ts`, `hub_cache_last_updated_ts`,
  `cache_source`) so each future mismatch self-diagnoses as either
  deploy-race (sqlite_reload + small uptime) or real transport loss
  (live_scan + large uptime). `scan_integrity` grows
  `cache_persisted_ok` so a failed DB write isn't silently absorbed.*

Extends CAP-T223 (integrity accounting), CAP-T229 (/audit/integrity +
verify-integrity routing), CAP-T232 (BC-129 loud-alert audit). Does not
change counter semantics; the counter is still the right tripwire.

## Open questions

Resolved during drafting (2026-11-16):

1. Opt-in gate for `hub_startup` audit event? **No.** One event per
   process boot is bounded; always-on makes the correlation lookup in
   `heartbeat_integrity_mismatch` trivially robust (no "metrics opt-in
   required" footgun for future operators).
2. Also enrich `transfer_hash_mismatch` with the same fields? **Yes
   in-branch**, since it's the same `_pipeline_integrity_mismatches++`
   call site semantically. The audit call for transfer_hash_mismatch
   currently lives inline in `receive_scan` though, so it's a small
   secondary patch.
3. Should `cache_source="no_record"` be its own value or an alias of
   `"sqlite_reload"` with a null hash? **Own value.** Tells the
   operator at a glance "scout claims a delivery hub has never heard
   of" — qualitatively different from "scout and hub disagree on which
   hash was latest."
4. Reverse `_persist_integrity_state`'s signature to return `bool`?
   **Yes** — otherwise `cache_persisted_ok` can't be captured. Cheap;
   all existing callers ignore the return.

## Links

- Direct predecessor: BC-122 (introduced the counter + heartbeat
  cross-check), BC-123 (persistent cache + counter across restarts).
- Related: BC-129 (prior ship — deploy race produced the 6th mismatch
  that motivated this investigation), BC-125 (EXECUTOR env hygiene —
  another "deploy-noise from in-flight state" paper cut).
- Design: `memory/design-decisions.md § Separation of concerns`
  (persistence rules), `memory/health-checks.md § The restart-loop
  trap` (adjacent principle: deploy noise must not look like actionable
  failure).
