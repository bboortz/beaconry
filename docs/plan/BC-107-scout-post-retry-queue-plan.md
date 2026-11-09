# BC-107 — Scout POST retry queue

*Branch: `feat/BC-107-scout-post-retry-queue`*
*Start date: 2026-11-09*
*Story: `docs/stories/BC-107-scout-post-retry-queue.md`*

---

## Situation

Scout's `collect_and_post` (poc/scout/scout.py:534) does:

```python
try:
    resp = requests.post(f"{HUB_URL}/scan", data=body, headers=headers, timeout=1800)
    resp.raise_for_status()
    ...
except requests.RequestException as e:
    log.error("Failed to post to hub: %s", e)
```

On failure the SBOM is logged and dropped. Pre-BC-107 there is no
persistence, no retry, no operator-visible signal beyond the log
line. This is the scout-side mirror of the gap BC-104 closed on the
hub side (Telegram delivery failures).

---

## Problems to solve

- Transient hub outage → SBOM silently lost for that scan cycle.
- Operator has no at-a-glance way to see a backlog has built up.
- Across scout restart during an outage, any in-memory retry would
  be lost — persistence required.

---

## Goals

1. Failed `/scan` POSTs are persisted to `pending_scans` and later
   replayed (maps to Scenario "Hub is unreachable" + "Hub recovers").
2. After `MAX_ATTEMPTS` failed replays, the payload is dropped with
   an explicit `scan_abandoned` log (maps to Scenario "Max attempts
   exceeded").
3. `/healthz` surfaces `pending_scans_queue_depth` so operators can
   spot a growing backlog.

---

## Scope

- `poc/scout/scout.py`:
  - New module-level constants: `SCOUT_STATE_DB_PATH`,
    `POST_RETRY_MAX_ATTEMPTS`, `POST_RETRY_BASE_S`,
    `POST_RETRY_SWEEP_INTERVAL_S`.
  - New `_init_scout_db()` function; called from lifespan.
  - New `_queue_pending_scan(payload_json, error)` function; called
    from `collect_and_post`'s exception branch.
  - New `post_retry_sweep(now_ts=None)` function (pure-ish, testable).
  - New `_post_retry_loop_forever()` background task; wired in
    lifespan.
  - `_scout_healthz_summary()` grows `pending_scans_queue_depth`.
  - `collect_and_post` refactored so the POST step is a tiny helper
    callable from both the main cycle and `post_retry_sweep` (same
    headers + token logic).
- `poc/docker-compose.yml` + `poc/.env.example` + `docs/VARIABLES.md`:
  three new env vars.
- `docs/CAPABILITIES.md`: CAP-T209.
- `poc/tests/integration/test_BC_107_scout_post_retry.py` — new
  integration test encoding the three Gherkin scenarios.
- `poc/tests/unit/test_BC_107_scout_post_retry_unit.py` — unit tests
  for `_parse_backoff`, `_queue_pending_scan`, `post_retry_sweep` behavior.
- `CHANGELOG.md` + `VERSION` → `0.20.0` (minor bump — new persistent
  state introduced, scout schema initialized for the first time).

---

## Non-goals

- Heartbeat retry queue (next tick recovers; see story "out of scope").
- Syft subprocess retry (next SCAN_INTERVAL recovers).
- Scout-side `audit` table matching hub's (stdlib logging suffices).

---

## Design decisions

- **SQLite over JSON file**: `pending_scans` gets a dedicated SQLite
  file `{DATA_DIR}/scout-state.db`. Stdlib, mirrors hub's
  `pending_notifications` table shape. Rejected: single JSON file
  with whole-file rewrites — harder to reason about with concurrent
  sweep + scan writes.
- **Payload stored as JSON TEXT, not BLOB**: SBOMs are JSON on the
  wire anyway. Readable in `sqlite3` REPL for debugging.
- **Exponential backoff `BASE × 2^attempts`**: identical to BC-104.
  Defaults 30/60/120/240/480s.
- **Queue is append-only (no UNIQUE constraint)**: unlike
  `pending_notifications` which coalesces on `(finding_id, kind)`,
  scan POSTs are strictly ordered events — losing one is losing
  that snapshot. Two failed POSTs from back-to-back scans should
  BOTH replay (even if payloads are similar), because the second
  one's inventory may reflect a change the first didn't.
- **Retry sweep runs the POST exactly as `collect_and_post` does** —
  same headers, same token, same URL. Extracted into `_post_scan(body)`
  helper so both paths share the behavior.

---

## Breaking changes

### 1. API / return shape

None. Scout → hub POST shape is unchanged; retries POST the same
body. Hub needn't know about retries.

### 2. Data model (schema + legacy rows)

New SQLite file created on first run at `{DATA_DIR}/scout-state.db`.
No legacy data — scout had no DB before. Pre-BC-107 scouts simply
don't get the file; upgrade path is "start scout and the file
appears". Volumes already mount `/data`, so nothing new in compose.

### 3. Behavioral semantics

- Pre-BC-107: SBOM lost on POST failure.
- Post-BC-107: SBOM persisted + replayed; eventually succeeds OR
  is explicitly dropped after N attempts.
- Operators who DON'T want retry (e.g. deliberate air-gap sim) can
  set `BEACONRY_SCOUT_POST_RETRY_MAX_ATTEMPTS=1` — one try then drop,
  matching pre-BC-107 behavior.

---

## Merge plan

1. All work on `feat/BC-107-scout-post-retry-queue`.
2. `make check` passes on branch tip.
3. PR to `dev`, squash merge.
4. Post-merge: INDEX + story status → shipped.

---

## Rollout plan

Additive. Scout gets a new file at `/data/scout-state.db` on first
boot. No operator action required unless they want to tune the env
vars.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario "Hub is unreachable" | unit | `poc/tests/unit/test_BC_107_scout_post_retry.py::test_queue_pending_scan_writes_row` | ✓ |
| Scenario "Hub recovers" | unit | `poc/tests/unit/test_BC_107_scout_post_retry.py::test_sweep_delivers_and_deletes_on_success` | ✓ |
| Scenario "Max attempts exceeded" | unit | `poc/tests/unit/test_BC_107_scout_post_retry.py::test_sweep_abandons_row_after_max_attempts` | ✓ |
| `/healthz` surfaces queue depth | unit | `poc/tests/unit/test_BC_107_scout_post_retry.py::test_healthz_surfaces_pending_scans_queue_depth` | ✓ |
| Exponential backoff formula | unit | `poc/tests/unit/test_BC_107_scout_post_retry.py::test_sweep_backoff_doubles_each_attempt` | ✓ |
| Shared POST helper (first-try + retry path) | unit | `poc/tests/unit/test_BC_107_scout_post_retry.py::test_post_scan_body_on_success_returns_data_dict`, `test_post_scan_body_on_network_fail_returns_false` | ✓ |
| Memory drift pattern added | N/A | N/A — no documented behavior deprecated | ✓ |

---

## Testing strategy

- Integration test spins up a fake hub (FastAPI) that returns 503
  first, then 200 on second call; asserts queue depth transitions
  1 → 0 across a sweep.
- Unit tests isolate `_queue_pending_scan`, `post_retry_sweep`, the
  backoff formula.

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_107_scout_post_retry_unit.py -v
pytest poc/tests/integration/test_BC_107_scout_post_retry.py -v
```
