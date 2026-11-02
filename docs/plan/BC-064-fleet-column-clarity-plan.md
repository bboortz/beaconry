# BC-064 — FLEET column clarity + stale-health degradation

*Branch: `feat/BC-064-fleet-column-clarity`*
*Start: 2026-11-02*

## Scope

### Hub `/fleet` — surface `last_posted_ts` as the LAST HEARTBEAT

- `http_fleet` adds `last_heartbeat_ts` and `heartbeat_stale` fields
  per row.
  - `last_heartbeat_ts` = `scouts.last_posted_ts` (already in the
    SELECT; just surface it under a clearer name).
  - `heartbeat_stale` = `now - last_posted_ts > threshold`, where
    `threshold = max(2 × SCOUT_HEARTBEAT_INTERVAL_S, 300)`.
    `SCOUT_HEARTBEAT_INTERVAL_S` is read from env at request time so
    operators who tune it get stale detection tracking automatically.
    Floor of 300s protects operators who disabled heartbeat
    (`SCOUT_HEARTBEAT_INTERVAL_S=0`) — fall back to BC-061
    scan-cadence semantics.
- `last_seen_ts` (= findings-derived watermark) **stays in the
  response** so any downstream CLI pinned to the old shape doesn't
  crash. `status.py` just stops surfacing it.

### `scripts/status.py::format_fleet`

- Column header `LAST SEEN` → `LAST HEARTBEAT`.
- Column value sources `row["last_heartbeat_ts"]` (back-compat
  fallback to `last_posted_ts` if the hub didn't send the new field
  — pre-BC-064 hub paired with newer CLI).
- HEALTH cell logic: if `row.get("heartbeat_stale")` → `? unknown`.
  Otherwise the existing `last_health` → glyph lookup wins. The
  `? unknown` rendering also applies when `last_health IS NULL` (as
  before — pre-BC-063 scout).

### Version bump

Patch: `0.2.0 → 0.2.1`. Pure render-side + response-field add; no
schema change, no breaking HTTP contract, no new env var.

## Design decisions

### Why keep `last_seen_ts` in the /fleet response

Removing it would be a breaking HTTP contract change. BC-064 is a
patch bump, so the response shape must stay additive. The field
becomes "dead code" from `status.py`'s perspective but downstream
tools (`gh` queries, operator shell loops) may still read it.

### Why 2× heartbeat interval as the staleness threshold

Default `SCOUT_HEARTBEAT_INTERVAL_S=60` → stale after 120s. That's
long enough for one lost heartbeat (network blip, slow DNS) to not
flap. Longer than that → we really don't know. Operators who set
longer intervals get proportional slack.

### Why the 300s floor when heartbeat is disabled

`SCOUT_HEARTBEAT_INTERVAL_S=0` means scouts only post on scans
(default SCAN_INTERVAL=300s). 2× would say stale after 0s, which is
useless. 300s matches the scan cadence floor and preserves the BC-061
behaviour where "active" status windowed at 10min was the operator
model.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| /fleet row includes last_heartbeat_ts | integration | `test_BC_064_fleet_clarity.py::test_fleet_response_carries_last_heartbeat_ts` | ✓ |
| /fleet row includes heartbeat_stale bool | integration | `test_BC_064_fleet_clarity.py::test_fleet_response_carries_heartbeat_stale_bool` | ✓ |
| /fleet preserves legacy last_seen_ts (back-compat) | integration | `test_BC_064_fleet_clarity.py::test_fleet_preserves_legacy_last_seen_ts_field` | ✓ |
| heartbeat_stale threshold tracks env knob | integration | `test_BC_064_fleet_clarity.py::test_heartbeat_stale_tracks_env_knob` | ✓ |
| heartbeat_stale true when > 2× interval | integration | `test_BC_064_fleet_clarity.py::test_heartbeat_stale_true_when_old` | ✓ |
| heartbeat_stale false when fresh | integration | `test_BC_064_fleet_clarity.py::test_heartbeat_stale_false_when_fresh` | ✓ |
| 300s floor when interval=0 | integration | `test_BC_064_fleet_clarity.py::test_heartbeat_stale_300s_floor_when_disabled` | ✓ |
| make status header reads LAST HEARTBEAT | unit | `test_BC_064_fleet_clarity.py::test_status_header_reads_last_heartbeat` | ✓ |
| make status reads last_heartbeat_ts | unit | `test_BC_064_fleet_clarity.py::test_status_uses_last_heartbeat_ts_not_last_seen` | ✓ |
| Stale heartbeat → HEALTH '? unknown' | unit | `test_BC_064_fleet_clarity.py::test_stale_heartbeat_renders_unknown_health` | ✓ |
| Fresh heartbeat → reported health shown | unit | `test_BC_064_fleet_clarity.py::test_fresh_heartbeat_renders_reported_health` | ✓ |
| Back-compat fallback to last_posted_ts | unit | `test_BC_064_fleet_clarity.py::test_status_falls_back_to_last_posted_ts` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

Legend: ✓ covered · ❌ open.

## Breaking changes

**None.** The HTTP response shape only *adds* fields. The CLI column
header string changes (`LAST SEEN` → `LAST HEARTBEAT`), which is a
display-only change; no automation parses `make status` output.

## Rollout

- `make bump-patch` → 0.2.1.
- Merge to dev.
- `make live-rebuild` on each host — new hub /fleet immediately
  returns `last_heartbeat_ts` + `heartbeat_stale`; new `status.py`
  renders them.
- Any pre-BC-064 CLI still pointed at a BC-064+ hub: just doesn't
  show the new fields. No crash.
