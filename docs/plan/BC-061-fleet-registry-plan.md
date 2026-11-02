# BC-061 — Fleet registry with liveness

*Branch: `feat/BC-061-fleet-registry`*
*Start: 2026-11-02*

## Scope

### Schema

New table in `init_db`:

```sql
CREATE TABLE IF NOT EXISTS scouts (
    host             TEXT PRIMARY KEY,
    first_seen_ts    INTEGER NOT NULL,
    last_posted_ts   INTEGER NOT NULL,
    last_sbom_count  INTEGER NOT NULL DEFAULT 0
);
```

### Upsert on `/scan` POST

```python
def record_scout_post(host: str, *, now: int, sbom_count: int) -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "INSERT INTO scouts (host, first_seen_ts, last_posted_ts, last_sbom_count) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(host) DO UPDATE SET "
            "  last_posted_ts = excluded.last_posted_ts, "
            "  last_sbom_count = excluded.last_sbom_count",
            (host, now, now, sbom_count),
        )
        con.commit()
    finally:
        con.close()
```

Called from `receive_scan` right after `_mark_scan_now` + before any
candidate processing.

### Backfill in `init_db`

```python
# BC-061: backfill scouts from findings so pre-BC-061 operators don't
# start with an empty registry. Idempotent — ON CONFLICT skips.
con.execute("""
    INSERT INTO scouts (host, first_seen_ts, last_posted_ts, last_sbom_count)
    SELECT host, COALESCE(MIN(found_at), 0), COALESCE(MAX(last_seen_ts), 0), 0
    FROM findings
    WHERE host IS NOT NULL AND host != ''
    GROUP BY host
    ON CONFLICT(host) DO NOTHING
""")
```

### Liveness thresholds + env knobs

```python
# BC-061: fleet-status thresholds.
# active   → posted within 2× SCAN_INTERVAL (default 600s)
# stale    → within BEACONRY_FLEET_DEAD_AFTER_S (default 86400s)
# dead     → beyond
FLEET_ACTIVE_WINDOW_S = int(os.environ.get("BEACONRY_FLEET_ACTIVE_WINDOW_S", "600"))
FLEET_DEAD_AFTER_S    = int(os.environ.get("BEACONRY_FLEET_DEAD_AFTER_S", "86400"))

def _fleet_status(last_posted_ts: int, now: int) -> str:
    age = now - last_posted_ts
    if age <= FLEET_ACTIVE_WINDOW_S:
        return "active"
    if age <= FLEET_DEAD_AFTER_S:
        return "stale"
    return "dead"
```

### `/fleet` endpoint reshape

```sql
SELECT s.host,
       s.first_seen_ts,
       s.last_posted_ts,
       s.last_sbom_count,
       COALESCE(MAX(f.last_seen_ts), 0) AS last_finding_ts,
       SUM(CASE WHEN f.status = 'open' THEN 1 ELSE 0 END) AS open_count,
       COUNT(f.id) AS total_count
FROM scouts s
LEFT JOIN findings f ON f.host = s.host
GROUP BY s.host, s.first_seen_ts, s.last_posted_ts, s.last_sbom_count
ORDER BY s.last_posted_ts DESC
```

Response shape adds `last_posted_ts`, `last_sbom_count`, `status`:

```json
[
  {"host": "laptop", "last_seen_ts": 1700, "last_posted_ts": 1700,
   "last_sbom_count": 4, "open_findings": 10, "total_findings": 10,
   "status": "active"},
  {"host": "hetzner-staging", "last_seen_ts": 0, "last_posted_ts": 1690,
   "last_sbom_count": 0, "open_findings": 0, "total_findings": 0,
   "status": "active"}
]
```

### `status.py` STATUS column

Extend `format_fleet` to show the status glyph + label:

```
FLEET:
HOST             LAST SEEN  OPEN  TOTAL  STATUS
laptop           30s ago    10    10     ● active
server2          5h ago     5     5      ⚠ stale
hetzner-staging  2m ago     0     0      ● active
old-laptop       5d ago     2     2      ✗ dead
```

Glyphs: `●` (active) / `⚠` (stale) / `✗` (dead). The LAST SEEN column
continues to show `last_seen_ts` from findings — scouts with 0
findings show `-`.

### Env var docs

`docs/VARIABLES.md` + `poc/.env.example` get rows for
`BEACONRY_FLEET_ACTIVE_WINDOW_S` + `BEACONRY_FLEET_DEAD_AFTER_S`.

## Design decisions

### Why `scouts` table vs. derive from `scan_received` audit

Audit log is append-only and grows unbounded; querying it on every
`/fleet` call is O(n_events). A small `scouts` table (one row per
host) is O(1) per query + bounded in size.

### Why LEFT JOIN (not UNION)

Simpler: one query, scouts drives, findings aggregates coalesce to 0
when absent. UNION would need two SELECTs + de-dup logic.

### Why upsert *before* candidate processing

Even if the handler errors downstream (grype crash, DB write fails),
the scout's "I was alive at T" record lands. Operator sees the scout
is active even when the pipeline is broken.

### Why not emit `scout_registered` audit event

Low signal — the existing `scan_received` audit event already covers
the "first-ever post" case implicitly. Adding another event per scan
would double the audit log size for marginal benefit.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| First POST registers scout | integration | `test_BC_061_fleet_registry.py::test_first_post_registers_scout` | ✓ |
| Repeated POST bumps last_posted_ts | integration | `test_BC_061_fleet_registry.py::test_repeated_post_bumps_last_posted` | ✓ |
| Zero-finding scout appears in /fleet | integration | `test_BC_061_fleet_registry.py::test_zero_finding_scout_in_fleet` | ✓ |
| /fleet status active threshold | integration | `test_BC_061_fleet_registry.py::test_fleet_status_active` | ✓ |
| /fleet status stale threshold | integration | `test_BC_061_fleet_registry.py::test_fleet_status_stale` | ✓ |
| /fleet status dead threshold | integration | `test_BC_061_fleet_registry.py::test_fleet_status_dead` | ✓ |
| /fleet response carries new fields | integration | `test_BC_061_fleet_registry.py::test_fleet_response_shape` | ✓ |
| /fleet sorts by last_posted_ts desc | integration | `test_BC_061_fleet_registry.py::test_fleet_sorted_by_last_posted` | ✓ |
| init_db backfills existing findings | integration | `test_BC_061_fleet_registry.py::test_init_db_backfills_from_findings` | ✓ |
| init_db idempotent (backfill twice doesn't duplicate) | integration | `test_BC_061_fleet_registry.py::test_init_db_backfill_idempotent` | ✓ |
| make status renders STATUS column glyphs | integration | `test_BC_061_fleet_registry.py::test_format_fleet_renders_status_column` | ✓ |
| make status column widths accommodate STATUS | integration | `test_BC_061_fleet_registry.py::test_format_fleet_column_widths` | ✓ |
| make status omits STATUS column for pre-BC-061 hub response (back-compat) | integration | `test_BC_061_fleet_registry.py::test_format_fleet_omits_status_when_absent` | ✓ |
| Env var thresholds respected | integration | `test_BC_061_fleet_registry.py::test_fleet_thresholds_from_env` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

Legend: ✓ covered · ❌ open.
