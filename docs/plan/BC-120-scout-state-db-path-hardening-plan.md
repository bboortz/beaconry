# BC-120 — Scout state DB path hardening

*Branch: `feat/BC-120-scout-state-db-path-hardening`*
*Start date: 2026-11-15*
*Story: `docs/stories/BC-120-scout-state-db-path-hardening.md`*

---

## Situation

bb-82jq has been logging `BC-107: can't open scout-state.db at
/data/scout-state.db: unable to open database file` continuously
since the 2026-11-15 scout deploy. Scout runs UID 65532; Docker
created the `_data` named-volume dir as `root:root 755` on first
mount; UID 65532 can't write.

Hub avoided this via a homelab `beaconry_hub` ansible task "Fix
beaconry data volume ownership for nonroot hub container (UID 65532)".
`beaconry_scout` doesn't have the equivalent step.

Meanwhile, beaconry's scout handles the fault by catching
`sqlite3.OperationalError`, logging a warning, and returning
quietly — meaning `_queue_pending_scan` silently drops payloads and
`_pending_scans_queue_depth` returns 0 (misleading — it was never
able to count). Operator has no way to see this short of reading
scout stderr.

## Problems to solve

- Unwritable primary path silently loses scans. Operators think the
  retry queue works when it doesn't.
- `/healthz` doesn't surface the fault. `make status` shows "healthy".
- Homelab `beaconry_scout` role leaves new deploys in this broken
  state by default.

## Goals

1. Scout at startup writability-tests `BEACONRY_SCOUT_STATE_DB`; on
   fault, falls back to `/tmp/scout-state-<pid>.db` (ephemeral but
   usable) with a WARN log containing the exact fix recipe. (Scenarios 1-3)
2. `/healthz.checks.state_db_degraded: bool` +
   `/healthz.checks.state_db_effective_path: str` — operator can
   see the degradation via `curl` or `beaconry status`. (Scenarios 1-2)
3. All scout state DB touches (`_init_scout_db`, `_queue_pending_scan`,
   `post_retry_sweep`, `_pending_scans_queue_depth`) route through a
   single `_effective_scout_state_db_path()` helper so the fallback
   applies consistently. (Scenario 3)
4. `make status` DETAILS cell renders `state_db degraded` when the
   flag fires. (Scenario 6)
5. **Scout Dockerfile creates `/data` owned by UID 65532** so
   standalone deploys (`docker-compose.scout-only.yml` — no host
   volume mounted) have a nonroot-writable DB path out of the box.
   This is the root-cause fix for bb-82jq (scout image on
   `docker-compose.scout-only.yml` had no `/data` at all → Nonroot
   UID couldn't create it at `/`). Done via the standard "mkdir in
   builder, `COPY --chown=65532:65532 /stage-data /data` in runtime"
   pattern (distroless runtime has no shell for RUN). (Scenario 5)

Ephemeral fallback semantics (Scenario 4) are a natural consequence
of Goal 1 — no extra work; documented in the plan so the behavior
is intentional, not accidental.

## Scope

**Files touched in beaconry**:

- `poc/scout/scout.py`
  - Add module-level `_state_db_degraded: bool`, `_effective_state_db_path: str` (set once by `_init_scout_db`)
  - Rewrite `_init_scout_db` to:
    - Try to `sqlite3.connect(SCOUT_STATE_DB_PATH)` + create table
    - On failure, set the two flags + fall back to `/tmp/scout-state-<pid>.db`, retry create-table, log WARN with chown recipe
  - Add `_effective_scout_state_db_path()` getter returning the resolved path
  - Update `_queue_pending_scan`, `post_retry_sweep`, `_pending_scans_queue_depth` to call the getter instead of `SCOUT_STATE_DB_PATH`
  - Extend `_scout_healthz_summary` to include `state_db_degraded` + `state_db_effective_path`
- `poc/scripts/status.py`
  - When scout `/healthz.checks.state_db_degraded == true`, append
    `state_db degraded` to the DETAILS cell
- `poc/tests/unit/test_BC_120_scout_state_db_hardening.py`
  - Unit tests for all the above
- `docs/CAPABILITIES.md` — CAP-T222 entry

**Dockerfile fix (same branch)**:

- `poc/scout/Dockerfile`
  - Builder stage adds `RUN mkdir -p /stage-data`
  - Runtime stage adds `COPY --from=builder --chown=65532:65532 /stage-data /data`
  - Fixes the standalone-scout fault at source. Bundled scout
    (`docker-compose.yml` mounts `beaconry-data:/data`) keeps working
    via the ansible chown step the hub role already runs; the
    Dockerfile change is additive (image pre-creates /data; bundled
    scout's mount shadows it with the host volume at run time).

**No homelab change needed** — the real fault was that scout's
Dockerfile never created /data; the standalone scout deploy
(`docker-compose.scout-only.yml` on bb-82jq) mounts no volume, so
the ansible-side chown pattern doesn't apply. Reverted the initial
assumption; Dockerfile is the root-cause fix.

## Non-goals

- Migrating queued rows from fallback → primary when ops is fixed.
- Making fallback durable. `/tmp` is ephemeral by design.
- Adding a second fallback for `/tmp` being unwritable too — if
  `/tmp` can't be written, something far worse is already broken
  and the scout should log + continue scanning (ignoring the queue).
- Fixing the hub side of the same pattern. Hub's audit path has its
  own sticky-flag (BC-115) and doesn't share code.

## Design decisions

- **Degrade over fail-fast**: scout's primary job is scanning. A
  broken retry queue degrades reliability of retries but doesn't
  stop scanning. Silent drop was worse than ephemeral queue.
- **`/tmp/scout-state-<pid>.db`**: `/tmp` is always writable for
  UID 65532 in any sane runtime; `<pid>` prevents ambiguity if
  multiple scouts ever run on one host (shouldn't happen but cheap
  insurance).
- **Single effective-path getter**: previously every DB touch
  referenced `SCOUT_STATE_DB_PATH` directly. After BC-120 those
  references route through `_effective_scout_state_db_path()`.
  Grep lints can enforce "no direct `SCOUT_STATE_DB_PATH` outside
  init" later if we see drift; not adding that lint today — YAGNI.
- **Audit event vs. healthz flag**: healthz-only. `/healthz` is the
  right surface for always-on state; the audit log is for
  transitions. The transition "primary unwritable → fallback" fires
  exactly once at startup and the log WARN is enough.

## Breaking changes

### 1. API / return shape

**Scout `/healthz.checks` gains two new keys** (`state_db_degraded`,
`state_db_effective_path`). `/healthz` consumers (`beaconry status`
in this branch; future dashboards) must tolerate extra keys — they
all do (Python dict access with `.get()` is the convention). Legacy
clients see no behavior change.

### 2. Data model (schema + legacy rows)

**None.** No DB schema change; `pending_scans` table shape unchanged.
`EXPECTED_SCHEMA_VERSION` (hub-side) unchanged — scout has no schema
version concept (BC-062 note in scout code).

### 3. Behavioral semantics

- **Scout with writable primary path**: no change.
- **Scout with unwritable primary path (bb-82jq today)**: previously
  silently dropped queued scans; now degrades to ephemeral `/tmp`
  queue + flags on healthz. Operators who were used to "no retry on
  broken primary" see the retry queue start working — correctly,
  which is what they wanted.
- **`make status` on a degraded scout**: appends `state_db degraded`
  to DETAILS cell. Surface cell is already variable-width — no
  layout breakage.

## Merge plan

1. All work on `feat/BC-120-scout-state-db-path-hardening`
2. `make check` passes on branch tip
3. PR to `dev`; Claude opens; user runs `/ultrareview`; Claude runs `/code-review`
4. Squash merge
5. Post-merge: BC-120 status → `shipped`, INDEX updated
6. Post-merge (optional): homelab PR for the ansible chown step

## Rollout plan

Additive. On deploy of a BC-120 scout:
- Scouts with already-writable primary path see no behavior change.
- Scouts with unwritable primary path (bb-82jq today) start retrying
  via ephemeral queue; next ansible run on homelab (after the
  homelab PR merges) fixes ownership; subsequent scout restart
  returns to durable queue.

## Shipping evidence

Pre-ship audit (DD-TGD step 6a). This story may only move to `shipped`
when every row below has non-empty evidence.

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| **Primary writable — no degradation** | unit + integration | unit `poc/tests/unit/test_BC_120_scout_state_db_hardening.py::test_primary_writable_no_degradation`; integration `poc/tests/integration/test_BC_120_scout_state_db_hardening.py::test_primary_writable_scenario_end_to_end` (full queue→depth→healthz loop) | ✓ |
| **Primary unwritable — scout falls back to /tmp** (WARN + flag) | unit + integration | unit `test_unwritable_primary_falls_back_to_tmp`; integration `test_unwritable_primary_full_fallback_loop` (real read-only dir, no `sqlite3` monkeypatch) | ✓ |
| **Fallback stays usable across pending_scans operations** | unit + integration | unit `test_pending_scans_ops_use_effective_path` (queue→depth); integration `test_unwritable_primary_full_fallback_loop` adds the `post_retry_sweep` call the unit test skips | ✓ |
| **Fallback resets per process (ephemeral is expected)** | unit | `test_fallback_path_includes_pid` + `test_fallback_resets_per_process` (two monkeypatched pids produce distinct fallback paths — the closest a unit test gets to a real fork). Deterministically proves the invariant that drives the semantics. | ✓ |
| **Scout Dockerfile pre-creates /data for UID 65532** | unit (Dockerfile text) + live | unit `test_scout_dockerfile_creates_data_dir_for_nonroot`; live-rebuild on 2026-11-15 reported `state_db_degraded: false` + `/data/scout-state.db` effective path | ✓ |
| **Read-only populated primary DB still falls back** (ultrareview fix #1) | unit + integration | unit `test_probe_detects_readonly_populated_db`; integration `test_readonly_populated_primary_falls_back_end_to_end` | ✓ |
| **Corrupt primary DB falls back, not crash-loop** (ultrareview fix #4) | unit | `test_probe_detects_corrupt_db_file` + `test_init_scout_db_falls_back_on_corrupt_primary` | ✓ |
| **`make status` surfaces the degradation** | unit | `test_status_renders_state_db_degraded` | ✓ |
| **Omit `state_db degraded` when healthy** (symmetry case) | unit | `test_status_omits_state_db_detail_when_healthy` | ✓ |
| /healthz key shape symmetric with status.py consumer (DD-TGD lens #2) | integration | `test_healthz_keys_match_status_py_consumer` | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | N/A | no documented behavior deprecated — pure additive + degrade | ✓ |

Legend: ✓ covered · ⚠️ partial (notes required) · ❌ open (blocks shipping).

## Testing strategy

- Unit tests monkeypatch `sqlite3.connect` to raise `OperationalError`
  for the primary path and verify the fallback is used.
- Monkeypatch `os.getpid()` to a stable value in the "fallback path
  includes pid" test.
- End-to-end via `make live-rebuild` would require shipping an
  intentionally broken permissions setup; skipped — unit-level
  monkeypatching of `sqlite3` is enough confidence since the fault
  injection site is precisely the real-world failure mode.

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_120_scout_state_db_hardening.py -v
```
