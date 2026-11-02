# BC-063 — Fleet health visibility

*Branch: `feat/BC-063-fleet-health-visibility`*
*Start: 2026-11-02*

## Scope

### Scout: POST metadata + initial scan + heartbeat loop

- `poc/scout/scout.py::collect_and_post` — payload gains three top-level
  fields:
  ```python
  payload = {
      "host": HOST_NAME,
      "timestamp": int(time.time()),
      "software_version": SOFTWARE_VERSION,
      "api_version": CLIENT_API_VERSION,
      "health": "ok" if _docker_socket_ok else "degraded",
      "sboms": sboms,
      …
  }
  ```
- `poc/scout/scout.py::lifespan` — fire the first `collect_and_post()`
  via `asyncio.to_thread` *immediately* after `start_period`-free HTTP
  startup, then settle into the `SCAN_INTERVAL` loop. No sleep before
  scan #1.
- `poc/scout/scout.py::heartbeat_loop` — new async task; if
  `SCOUT_HEARTBEAT_INTERVAL_S > 0`, POST `/heartbeat` with
  `{host, timestamp, software_version, api_version, health}` every
  `SCOUT_HEARTBEAT_INTERVAL_S` seconds. Fires once *immediately* on
  startup (same reasoning as initial scan). Interval=0 disables the
  loop entirely.
- `poc/scout/scout.py` env: `SCOUT_HEARTBEAT_INTERVAL_S` (default 60).

### Hub: schema v3 + fleet enrichment + heartbeat endpoint

- `poc/hub/hub.py::EXPECTED_SCHEMA_VERSION` bumps to `3`.
- `poc/hub/hub.py::init_db`:
  - If PRAGMA user_version < 3: use `PRAGMA table_info(scouts)` to
    check existing columns, then `ALTER TABLE scouts ADD COLUMN` for
    each of `software_version TEXT`, `api_version INTEGER`,
    `last_health TEXT`, `last_scan_ts INTEGER`. Idempotent-safe.
- `poc/hub/hub.py::record_scout_post(host, now, sbom_count, *,
  software_version=None, api_version=None, health=None,
  bump_scan_ts=True)` — writes the four new columns on upsert.
  Heartbeat callers pass `bump_scan_ts=False`.
- `poc/hub/hub.py::receive_scan` — pulls `software_version`,
  `api_version`, `health` out of the payload and passes to
  `record_scout_post` with `bump_scan_ts=True`.
- `poc/hub/hub.py::http_heartbeat` — new `POST /heartbeat` endpoint;
  requires scout bearer token (same auth as `/scan`). Writes
  metadata via `record_scout_post(..., bump_scan_ts=False)`.
- `poc/hub/hub.py::http_fleet` — SELECT adds the four columns; each
  row's JSON gains `software_version`, `api_version`, `last_health`,
  `last_scan_ts`.

### Status CLI

- `poc/scripts/status.py::format_fleet` — adds three columns (VERSION,
  HEALTH, LAST SCAN) between the existing OPEN/TOTAL/STATUS. LAST SCAN
  reads `last_scan_ts` (set only by `/scan`; falls back to `-` if
  never scanned). LAST SEEN keeps reading `last_posted_ts` (any POST
  including heartbeats).
- VERSION cell: `0.1.0 · api1` + trailing `⚠` if the scout's
  `api_version` differs from the hub's.
- HEALTH cell: `● healthy` (ok) / `⚠ degraded` / `? unknown`.
- Pre-BC-063 rows render `-` cells gracefully.

## Design decisions

### Why ALTER TABLE instead of DROP+CREATE for migration

Existing scouts rows carry `first_seen_ts` + `last_posted_ts` that
operators have — rebuilding destroys that history. `ALTER TABLE ADD
COLUMN` is cheap, atomic, idempotent-guardable via
`PRAGMA table_info`.

### Why reuse last_posted_ts for LAST SCAN column (no separate column)

Scouts' only outbound protocol is `/scan` POST. Every POST **is** a
scan completion. If we ever add a pure heartbeat endpoint, we'll
revisit. YAGNI for now; one column, one semantic.

### Why "? unknown" not "-" for pre-BC-063 scouts

A legacy scout isn't "unhealthy" — it's "we don't know yet, upgrade it
to find out". Visually distinct from a scout that explicitly reported
degraded.

### Why initial scan on startup (not after SCAN_INTERVAL)

Default SCAN_INTERVAL = 300s = 5min. Operator deploying a scout and
running `make status` 30s later should see it, not after 5min of
tapping their foot. The HTTP server already comes up before the scan
loop; wiring an immediate scan is a one-line change.

### Why not probe scout /healthz directly from hub

Would require network discovery (scout IPs). The push model is
already the architecture; scout tells hub its state on every scan.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| EXPECTED_SCHEMA_VERSION == 3 | integration | `test_BC_063_fleet_health.py::test_expected_schema_version_is_3` | ✓ |
| Fresh DB has BC-063 scouts columns | integration | `test_BC_063_fleet_health.py::test_fresh_db_v3_has_new_scouts_columns` | ✓ |
| Schema v2 → v3 migration adds the four columns + preserves rows | integration | `test_BC_063_fleet_health.py::test_schema_v2_to_v3_migration` | ✓ |
| Schema v3 init_db is idempotent | integration | `test_BC_063_fleet_health.py::test_init_db_v3_idempotent` | ✓ |
| /scan payload persists per-scout metadata | integration | `test_BC_063_fleet_health.py::test_scan_payload_persists_scout_metadata` | ✓ |
| Legacy /scan payload persists NULLs | integration | `test_BC_063_fleet_health.py::test_legacy_scan_payload_persists_nulls` | ✓ |
| /fleet returns per-scout metadata | integration | `test_BC_063_fleet_health.py::test_fleet_returns_scout_metadata` | ✓ |
| /heartbeat updates metadata without bumping last_scan_ts | integration | `test_BC_063_fleet_health.py::test_heartbeat_updates_metadata_without_bumping_scan_ts` | ✓ |
| /heartbeat from unseen scout registers it | integration | `test_BC_063_fleet_health.py::test_heartbeat_from_unseen_scout_registers_it` | ✓ |
| make status renders VERSION + HEALTH + LAST SCAN columns | integration | `test_BC_063_fleet_health.py::test_make_status_renders_new_columns` | ✓ |
| API-mismatch ⚠ glyph on VERSION cell | integration | `test_BC_063_fleet_health.py::test_make_status_renders_api_mismatch_glyph` | ✓ |
| make status renders ⚠ degraded health | integration | `test_BC_063_fleet_health.py::test_make_status_renders_degraded_health` | ✓ |
| make status renders '? unknown' for pre-BC-063 row | integration | `test_BC_063_fleet_health.py::test_make_status_renders_unknown_for_legacy_row` | ✓ |
| Pre-BC-063 response (no fields anywhere) degrades gracefully | integration | `test_BC_063_fleet_health.py::test_make_status_pre_bc063_response_degrades_gracefully` | ✓ |
| /healthz schema version is 3 | integration | `test_BC_063_fleet_health.py::test_healthz_version_schema_is_3` | ✓ |
| /heartbeat emits audit event | integration | `test_BC_063_fleet_health.py::test_heartbeat_emits_audit_event` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

Legend: ✓ covered · ❌ open.

## Breaking changes

**None at the HTTP level** — scouts can still POST without the three
new fields. Hub persists NULL and `make status` renders `-` / `?`.

**Schema v3** is a *forward* migration; hubs rolled back from v3 to v2
would hit `SchemaTooNew` (BC-062's gate), which is the intended
behaviour. Rolling back requires `BEACONRY_FORCE_SCHEMA=1` (not yet
shipped — operator must manually `PRAGMA user_version = 2` to downgrade).

## Rollout

No migration script needed — `init_db` runs on hub startup and
upgrades the live DB in place. Operators doing a rolling upgrade:

1. Bump VERSION (`make bump-patch`), merge BC-063 to dev.
2. `make live-rebuild` on each host that runs hub. On the first
   restart the hub ALTER TABLEs the scouts table (<1ms, no downtime).
3. Scouts auto-pick-up: BC-063 scouts send the extra fields on their
   next scan tick; legacy scouts keep working.
4. `make status` shows the enriched FLEET once the first BC-063 scout
   has posted.
