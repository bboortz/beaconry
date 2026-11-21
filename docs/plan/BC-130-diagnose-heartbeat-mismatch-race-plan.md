# BC-130 — Diagnose + fix the hub-shutdown-race source of heartbeat_integrity_mismatch

*Branch: `feat/BC-130-diagnose-heartbeat-mismatch-race`*
*Start date: 2026-11-16*
*Story: `docs/stories/BC-130-diagnose-heartbeat-mismatch-race.md`*

---

## Situation

`pipeline_integrity_mismatches` counter grew from 0 → 6 across six
hub deploys on 2026-11-16 (BC-120 fast-iteration day culminating in
BC-129 fleet rollout). Investigation of all 7 audit events showed
every single one correlates with a hub restart — zero correlate with
normal operation. Current ordering in `_ingest_candidates`
(hub.py:5457-5462):

```python
_last_scan_integrity_per_host[host] = {...}  # in-memory first
_persist_integrity_state(host, ...)  # SQLite second
```

If uvicorn gets SIGTERM between these two statements (cheap, ~µs
window but hit reliably by daily deploys), the in-memory cache has a
new value that never made it to disk. On next boot, SQLite reloads the
previous value; scout's next heartbeat carries the *actual* latest
hash it delivered; hub says "I don't know that hash" → mismatch counter
++.

The counter is doing its job (BC-122 tripwire for silent transport
loss), but it can't distinguish deploy-race from real transport loss.
Audit payload has four hashes and no timing context, so forensics
means correlating event ts against a shell history of `docker
compose up` commands.

---

## Problems to solve

- **P1 — the race itself**: in-memory cache can advance ahead of DB.
  Durability inversion.
- **P2 — forensic cost**: each `heartbeat_integrity_mismatch` event
  requires manual correlation (event ts vs. deploy timestamps + hub
  boot times) to classify. Not scalable past a handful of events.
- **P3 — invisible restarts**: hub boot has no audit footprint.
  Deploy boundaries only exist in `docker logs` / CI records, not in
  the audit stream that `beaconry audit` + verify-integrity consume.
- **P4 — silent DB write failures**: `_persist_integrity_state` logs a
  warning on `sqlite3.OperationalError` but otherwise swallows it —
  nothing surfaces in the audit event stream. A disk-full on the hub
  would leak in-memory-only deltas without trace.

---

## Goals

1. Reorder `_ingest_candidates` so `_persist_integrity_state` runs
   BEFORE `_last_scan_integrity_per_host[host] = ...`. Capture the
   persist bool. (addresses P1; maps to Gherkin scenario 1)
2. Emit a `hub_startup` audit event at the end of
   `load_integrity_persistence()` carrying `software_version`,
   `cache_reload_ok`, `hosts_in_cache`, `mismatch_counter_at_boot`.
   (addresses P3; maps to Gherkin scenario 2)
3. Enrich `heartbeat_integrity_mismatch` + `transfer_hash_mismatch`
   events with four correlation fields: `hub_uptime_s`,
   `last_hub_startup_ts`, `hub_cache_last_updated_ts`, `cache_source`.
   (addresses P2; maps to Gherkin scenarios 3 + 4)
4. Add `cache_persisted_ok: bool` to the `scan_integrity` audit
   event. (addresses P4; maps to Gherkin scenario 5)
5. Live verification path: next deploy, counter stays flat. (maps to
   Gherkin scenario 6)

---

## Scope

- `poc/hub/hub.py`:
  - Module-level: `_hub_boot_ts: int`, `_cache_source_per_host: dict[str,
    str]`.
  - `load_integrity_persistence()`: populate
    `_cache_source_per_host[host] = "sqlite_reload"` for every row;
    set `_hub_boot_ts = int(time.time())`; return the hosts count +
    reload-ok flag.
  - Hub lifespan: after `load_integrity_persistence()`, emit
    `hub_startup` audit event with the captured context.
  - `_persist_integrity_state`: change return type `None → bool`
    (True on commit, False on `OperationalError`).
  - Add `_get_cache_last_updated_ts(host)` reading from
    `integrity_state.last_updated_ts`.
  - `_ingest_candidates`: reorder — call `_persist_integrity_state`
    first, capture bool; then assign
    `_last_scan_integrity_per_host[host]`; then set
    `_cache_source_per_host[host] = "live_scan"`; fold
    `cache_persisted_ok` into the `scan_integrity` event.
  - `_record_heartbeat_mismatch`: accept + emit the four new
    correlation fields; call sites in `_check_heartbeat_integrity`
    pass them. For the NO_RECORD path, cache_source is `"no_record"`.
  - `transfer_hash_mismatch` inline audit call in `receive_scan`:
    add the same four correlation fields for symmetry (same counter
    bump, same forensic needs).
- `poc/tests/unit/test_BC_130_diagnose_heartbeat_mismatch_race.py`:
  new.
- `docs/CAPABILITIES.md`: CAP-T233 entry.
- `docs/stories/INDEX.md`: BC-130 row (draft → in-progress → shipped).
- `pyproject.toml`, `VERSION`, `poc/VERSION`, `CHANGELOG.md`: version
  bump to 0.22.14.

No scout changes. No DB schema changes (integrity_state already carries
`last_updated_ts`). No new env vars.

---

## Non-goals

- Persistent scout `_last_delivered_*`. Scout restart currently resets
  those to None (no heartbeat claim → no mismatch possible, but also no
  cross-check coverage for the first post-restart scan window). Fixing
  that is a semantically different story — "give us cross-check
  coverage across scout restarts" — and likely needs a tiny on-disk
  scout-state row, not just an audit enrichment.
- Graceful uvicorn SIGTERM handling (drain in-flight requests before
  exit). The reorder makes the race vanish without needing that
  machinery, and shutdown-draining has its own correctness subtleties
  worth a dedicated story.
- Telegram alerting on heartbeat mismatch. BC-122 already added
  `BEACONRY_PIPELINE_INTEGRITY_ALERT=1` for transfer_hash_mismatch;
  extending it to heartbeat would require a separate false-positive
  baseline pass.

---

## Design decisions

- **Reorder vs. transaction wrap**: reorder is zero overhead and
  closes the window completely because SQLite commits are durable by
  default. A transaction wrap (BEGIN/COMMIT around cache + persist)
  wouldn't help because the in-memory dict assignment isn't an SQL
  statement — SQLite can't roll it back. Reorder is the right shape.
- **`cache_source` has three values, not two**: `"sqlite_reload"`,
  `"live_scan"`, `"no_record"`. The no-record case was previously
  modeled as "cached_iteration_hash is None" in the payload; making it
  explicit in `cache_source` means a future tooling consumer can
  switch on a single field.
- **Enrichment lives in `_record_heartbeat_mismatch`'s signature**,
  not fetched from globals inside the function. Keeps the mismatch-
  recording path pure for unit tests (no clock patching needed —
  caller passes in uptime, boot_ts, cache state).
- **`hub_startup` is unconditional**, not gated by
  `X-Beaconry-Metrics-Opt-In`. One event per boot is bounded; the
  correlation trail would have gaps if an operator forgot the flag.
- **`cache_persisted_ok`** reports the return of the single
  `_persist_integrity_state` call. If that call swallowed an
  `OperationalError`, the field is False, the audit log carries the
  signal, operators can grep for `cache_persisted_ok: false`.
- **`_hub_boot_ts` module-level** vs. passed-through-context: picked
  module-level because it's set once at boot and read from many places
  (`hub_startup` emit, every mismatch audit). A pure-function design
  would thread it through, but that costs 4+ signature changes for no
  observability benefit.

---

## Breaking changes

### 1. API / return shape

- `scan_integrity` audit event: NEW `cache_persisted_ok: bool`
  field. Additive. Existing consumers (`verify-integrity`,
  operator-grep) tolerate extra keys.
- `heartbeat_integrity_mismatch` audit event: 4 NEW fields
  (`hub_uptime_s`, `last_hub_startup_ts`,
  `hub_cache_last_updated_ts`, `cache_source`). Additive.
- `transfer_hash_mismatch` audit event: same 4 new fields.
- NEW `hub_startup` audit event. Consumers that filter audit by event
  type ignore unknown types by default (checked in audit-view CLI).
- No `/healthz`, `/scan`, `/audit*`, `/fleet` HTTP surface changes.
- `_persist_integrity_state` return type: `None → bool`. Internal
  function; no public API impact.

### 2. Data model (schema + legacy rows)

- **None.** `integrity_state.last_updated_ts` already exists (BC-123).
- No schema migration, no backfill, no new columns.
- Legacy heartbeat_integrity_mismatch audit events (pre-BC-130) are
  missing the four correlation fields. `beaconry audit --event
  heartbeat_integrity_mismatch` consumers already handle missing keys
  via `.get()` defaults; no reader change needed.

### 3. Behavioral semantics

- **Deploy noise drops to zero** after BC-130. Pre-BC-130, operators
  had been seeing ~1 `pipeline_integrity_mismatches` increment per
  deploy; post-BC-130 that should flatline. If it doesn't, the
  enriched audit event tells them exactly why — not a regression,
  the new signal is actionable.
- **`hub_startup` events appear in audit grep outputs** where
  previously nothing marked the deploy boundary. Operator wiring
  (e.g. an alertmanager rule on audit volume) may notice ~1 extra
  event per restart.
- **Mismatch counter keeps its meaning**: the tripwire for silent
  transport loss. BC-130 just removes the false-positive source;
  nothing shifts the counter's definition or /healthz surfacing.

---

## Merge plan

1. All work on `feat/BC-130-diagnose-heartbeat-mismatch-race`.
2. `make check` must pass on branch tip; `BEACONRY_SKIP_DOGFOOD=1
   make check-full` before opening the PR (pre-existing Python
   3.14.11 base-image CVE still unresolved).
3. PR to `dev`; Claude opens, user runs `/ultrareview`, Claude runs
   `/code-review`.
4. Merge strategy: squash.
5. Post-merge: Release workflow_dispatch → tag v0.22.14 → forge
   rebuild → `make deploy-beaconry` → verify
   `pipeline_integrity_mismatches` stays flat across the deploy.

---

## Rollout plan

- No migration. No new env vars. No scout-side change — scout can
  stay on 0.22.13 and the enrichment fields will populate correctly
  (they're all computed hub-side).
- Rolling order: hub first, scouts second (scouts need no change but
  the normal deploy playbook rolls them anyway for version parity).
- Observability: first post-deploy heartbeat from each scout fires the
  enrichment fields. Scout heartbeat cadence = 60 s, so within ~1 min
  of hub boot the new shape is visible in `/audit?event=hub_startup
  OR event=heartbeat_integrity_mismatch`.

---

## Shipping evidence

Pre-ship audit (DD-TGD step 6a).

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Persist-first ordering closes the race | unit | `test_BC_130_diagnose_heartbeat_mismatch_race.py::test_persist_runs_before_cache_update` | ✓ |
| hub_startup event emitted at boot | unit | `test_hub_startup_audit_event_fires` | ✓ |
| heartbeat_integrity_mismatch carries diagnostic fields | unit | `test_mismatch_event_carries_enrichment_fields` | ✓ |
| cache_source=sqlite_reload after boot | unit | `test_cache_source_sqlite_reload_for_hydrated_hosts` | ✓ |
| cache_source=live_scan after first scan | unit | `test_cache_source_flips_to_live_scan_on_ingest` | ✓ |
| cache_source=no_record when hub has no cache for this host | unit | `test_cache_source_no_record_when_cache_empty` | ✓ |
| scan_integrity carries cache_persisted_ok=True on normal commit | unit | `test_scan_integrity_cache_persisted_ok_true` | ✓ |
| scan_integrity carries cache_persisted_ok=False on commit failure | unit | `test_scan_integrity_cache_persisted_ok_false_on_db_error` | ✓ |
| transfer_hash_mismatch also carries the four fields | unit | `test_transfer_hash_mismatch_carries_enrichment_fields` | ✓ |
| Counter stays flat across a clean deploy | live | post-deploy verify command (below); counter value at deploy start + 1h later identical | ⏳ pending post-deploy |
| Memory drift pattern added if story deprecates documented behavior | TOML diff / N/A | N/A — purely additive audit enrichment + ordering safety | ✓ |

Legend: ✓ covered · ⚠️ partial (notes required) · ❌ open (blocks shipping).

---

## Testing strategy

Unit tests for every enrichment field + the ordering guarantee. The
ordering test uses `monkeypatch.setattr` to replace
`_persist_integrity_state` with a wrapper that records call order vs
the cache-dict mutation — the test asserts persist's record comes
first.

The `cache_persisted_ok=False` test patches sqlite3.connect to raise
`OperationalError` and asserts the audit event's field reflects the
failure (not silent swallow).

Live verification happens after deploy: snapshot
`pipeline_integrity_mismatches` from `/healthz`, wait for one full
scan cycle on every host, snapshot again, assert equal. If any
post-deploy event does fire, its enriched payload must have
`cache_source="live_scan"` and `hub_uptime_s > 300` — the actionable
silent-transport-loss signature.

---

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_130_diagnose_heartbeat_mismatch_race.py -v

# Post-deploy verification:
# 1. Baseline the counter
BEFORE=$(curl -s http://100.77.182.73:8000/healthz | jq .checks.pipeline_integrity_mismatches)
# 2. Wait ~15 min for a full scan cycle on every scout
sleep 900
# 3. Compare
AFTER=$(curl -s http://100.77.182.73:8000/healthz | jq .checks.pipeline_integrity_mismatches)
echo "pre-deploy=$BEFORE  post-settle=$AFTER  delta=$((AFTER - BEFORE))"
# Expected: delta == 0

# Confirm hub_startup event fired:
curl -sS 'http://100.77.182.73:8000/audit?event=hub_startup&limit=1' | jq .
# Expected: one event with software_version=0.22.14
```
