# BC-092 — Canary ring

*Branch: `feat/BC-092-canary-ring`*
*Start: 2026-11-05*
*Story: `docs/stories/BC-092-canary-ring.md`*

---

## Situation

`memory/design-decisions.md § Multi-host canary ring` describes a
three-ring policy (workstation → staging → production) with a hard
"Ring 2 blocked until Ring 1 verified" gate. Design is detailed; code
has **zero implementation** — no `role` attribute on scout config,
no gate in `_handle_fix`. Surfaced as §16.1 in BC-088 LIFECYCLE.md
missing-implementation inventory.

## Problems to solve

- Operator who accidentally taps [🔧 Fix now] on production first
  discovers the broken fix on production, not on staging.
- Scout has no concept of "what tier is this host", so no downstream
  code can reason about it.
- `make status` fleet view doesn't show role, so operators can't
  check at a glance.

## Goals

1. Scouts advertise `SCOUT_HOST_ROLE` on heartbeat + /scan — Scenario 6.
2. Hub persists role via schema v6→v7 — Scenario 7 back-compat.
3. Production fix dispatch is gated on staging precedent — Scenarios
   1, 2, 3.
4. Operator can disable the gate globally — Scenario 4.
5. Operator can force-allow single fix with audit trail — Scenario 5.
6. `make status` shows role column (CAP-T190, no separate
   scenario — observational).

## Scope

- `poc/scout/scout.py`:
  - `HOST_ROLE = os.environ.get("SCOUT_HOST_ROLE") or "workstation"`,
    clamp to the known set `{workstation, staging, production}`
    (invalid → `workstation` + warning).
  - Include `role` key in heartbeat POST + /scan POST bodies.

- `poc/hub/hub.py`:
  - `EXPECTED_SCHEMA_VERSION = 7`.
  - `ALTER TABLE scouts ADD COLUMN role TEXT NOT NULL DEFAULT 'workstation'`
    (idempotent via contextlib.suppress).
  - `/heartbeat` + `/scan` handlers read `body.get("role")`; validate
    against the set; UPDATE scouts.role.
  - `_handle_fix` runs **before** executor dispatch:
    1. Look up host's role via scouts table.
    2. If role ≠ `production` → proceed (no gate).
    3. If `BEACONRY_CANARY_RING_ENABLED=0` → proceed.
    4. If `BEACONRY_CANARY_PRODUCTION_FORCE_ALLOW=1` → proceed +
       emit `canary_gate_force_allowed` audit.
    5. Else: query `SELECT 1 FROM findings f JOIN scouts s ON
       f.host = s.host WHERE f.cve_id = ? AND f.status =
       'fixed_verified' AND s.role = 'staging' LIMIT 1`.
    6. No match → block, emit `canary_gate_blocked`, edit message
       explaining the gap + available staging hosts.
    7. Match → proceed + emit `canary_gate_passed`.

- `poc/scripts/status.py`: add "ROLE" column with glyph `W/S/P`.

- Env vars (3):
  - `SCOUT_HOST_ROLE` (scout, default `workstation`)
  - `BEACONRY_CANARY_RING_ENABLED` (hub, default `1`)
  - `BEACONRY_CANARY_PRODUCTION_FORCE_ALLOW` (hub, default `0`)

- Docs: VARIABLES.md (3 rows), .env.example (3 entries),
  docker-compose.yml (3 propagations across hub + scout).

- CAPs: T188, T189, T190.

- INDEX.md: BC-092 row.

- CHANGELOG.md: v0.15.0 entry.

- Tests: `poc/tests/integration/test_BC_092_canary_ring.py` — 7
  Gherkin scenarios + schema migration + fallback for missing role
  in legacy heartbeat.

- Version 0.14.1 → **0.15.0** (minor — new schema column + new
  behaviour on fix dispatch).

## Non-goals

- Auto-promote across rings. v1 is gate-only.
- Per-CVE ring overrides.
- Multi-staging coordination (e.g. "need 2 of 3 staging hosts
  verified"). v1 is "any one" — simpler and matches design.
- Health-check waterfall integration. Separate orchestration.

## Design decisions

- **Scout advertises role, not CLI config on hub**: single source
  of truth is the scout's env — no risk of hub and scout
  disagreeing. Hub trusts scout's heartbeat.
- **`role` enum validated on both sides**: scout clamps invalid
  values on boot (logs warning); hub's /heartbeat + /scan also
  clamp, defending against a mis-configured scout.
- **"Any one staging verified" quorum**: minimum viable gate. A
  future BC could add "N of M" voting if operators report
  single-staging false positives.
- **Fix-time gate, not ingest-time**: ingest is read-only
  classification; gate lives at the dispatch moment. Matches the
  existing BC-089 opt-out pattern (per-kind, not per-row).
- **Audit three outcomes** (`passed`, `blocked`,
  `force_allowed`): operators can post-hoc reconstruct which
  production fixes went through the gate vs the override.
- **Missing scout role → default `workstation`**: safest fallback.
  A pre-BC-092 scout that doesn't send role would otherwise be
  mis-labelled as production.

## Breaking changes

### API / return shape

- `/heartbeat` + `/scan` now accept optional `role` key. Pre-BC-092
  scouts don't send it — hub falls back to DB default
  `workstation`. No breaking change for callers; new scouts post
  more data.
- `/healthz` unchanged.
- `make status` output gains a column — downstream parsers of
  `make status` output (none today) would need to adapt.

### Data model

- Schema v6 → **v7**. `ALTER TABLE scouts ADD COLUMN role TEXT NOT
  NULL DEFAULT 'workstation'`. Idempotent. Legacy rows get
  `workstation` — operators who want them tagged production/staging
  must explicitly configure `SCOUT_HOST_ROLE` on the scout and let
  the next heartbeat UPDATE the row.

### Behavioral semantics

- **Production fixes now blocked by default when staging hasn't
  verified.** Loud in CHANGELOG. Operators without a staging host
  must either:
  1. Set one scout's role to staging + verify the fix there first.
  2. Set `BEACONRY_CANARY_RING_ENABLED=0` to disable the gate.
  3. Use `BEACONRY_CANARY_PRODUCTION_FORCE_ALLOW=1` for one-off
     emergency.
- Legacy scouts without `SCOUT_HOST_ROLE` set stay at
  `workstation` — gate doesn't apply to them. Operators who today
  have only one host (homelab) are unaffected until they
  explicitly flag a host as production.

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Scenario 1 — production blocked | integration | `test_BC_092_canary_ring.py::test_production_blocked_without_staging` | ✓ |
| Scenario 2 — production allowed after staging | integration | `test_BC_092_canary_ring.py::test_production_allowed_with_staging_verified` | ✓ |
| Scenario 3 — non-production bypasses | integration | `test_BC_092_canary_ring.py::test_workstation_bypasses_gate` | ✓ |
| Scenario 4 — gate disabled | integration | `test_BC_092_canary_ring.py::test_gate_disabled_env` | ✓ |
| Scenario 5 — force allow | integration | `test_BC_092_canary_ring.py::test_force_allow_bypasses_but_audits` | ✓ |
| Scenario 6 — scout advertises role | integration | `test_BC_092_canary_ring.py::test_scout_advertises_role_on_heartbeat` | ✓ |
| Scenario 7 — legacy scout fallback | integration | `test_BC_092_canary_ring.py::test_legacy_scout_without_role_defaults_workstation` | ✓ |
| Schema migration v6→v7 | integration | `test_BC_092_canary_ring.py::test_schema_v7_adds_role_column` | ✓ |
| `_normalize_role` parametrized | integration | `test_BC_092_canary_ring.py::test_normalize_role[None-workstation]` | ✓ |
| Invalid role clamps to workstation | integration | `test_BC_092_canary_ring.py::test_invalid_role_clamps_workstation` | ✓ |
| `/fleet` exposes role | integration | `test_BC_092_canary_ring.py::test_fleet_endpoint_includes_role` | ✓ |
| `make status` hides ROLE for all-workstation | integration | `test_BC_092_canary_ring.py::test_status_hides_role_column_for_all_workstation` | ✓ |
| `make status` renders role column | integration | `test_BC_092_canary_ring.py::test_status_renders_role_column` | ✓ |
| VARIABLES + env + compose sync | lint | `make verify-variables` + `verify-compose-env-propagation` | ✓ |
| CAP-T188 + T189 + T190 registered | lint | `make verify-traceability` | ✓ |
| Fast + full tier green | gate log | `make check` + `make check-full` | ✓ |

## Rollout

1. Hub ships first (gate defaults to active — but zero production
   hosts exist until operators explicitly flag scouts).
2. Operator sets `SCOUT_HOST_ROLE=staging` on hetzner scout,
   restarts. `scouts.role` updates on next heartbeat.
3. Operator sets `SCOUT_HOST_ROLE=production` on server2 scout,
   restarts.
4. Next production fix tap hits the gate; if no staging-verified
   for that CVE → blocked with actionable message.
5. Operator fixes on staging first, re-scan verifies, then
   production tap unblocks.

No fleet-wide migration; each scout rolls its own env change.
