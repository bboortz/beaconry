---
id: BC-092
title: Canary ring — production fixes blocked until staging has shipped the fix
status: in-progress
created: 2026-11-05
branch: feat/BC-092-canary-ring
plan: docs/plan/BC-092-canary-ring-plan.md
---

# BC-092: Canary ring

## Narrative

Operator runs beaconry across three hosts: a laptop (Ring 0 — 1-finger
test), hetzner-staging (Ring 1 — the canary), and server2
(Ring 2 — production serving real traffic). Operator sees
CVE-2026-X on server2 at 09:00, taps [🔧 Fix now], the executor
pulls the new image. Runtime breaks something subtle (upstream
changed a default). Production goes down.

BC-092 forces the canary order: production fixes are blocked until
the same CVE is `fixed_verified` on at least one staging host. If
Ring 1 hasn't caught the broken fix yet, Ring 2 can't fire. Operator
with no staging gets an audit record and an actionable error
explaining what to configure. Emergency escape hatch via env for
the "my staging is on fire, let me ship to prod anyway" case.

## Trigger

Operator taps [🔧 Fix now] on a Telegram alert for a host whose
`role` is `production`.

## Expected outcome

- If `BEACONRY_CANARY_RING_ENABLED=1` (default) and the host's role
  is `production`:
  - Hub checks whether any `role=staging` scout has the same CVE in
    status `fixed_verified`.
  - Match → fix proceeds normally, audit `canary_gate_passed`.
  - No match → fix is **blocked**. Telegram reply explains:
    "staging has not yet verified this CVE; ask Ring 1 first".
    Audit `canary_gate_blocked` with cve_id + host + available
    staging hosts.
- `BEACONRY_CANARY_PRODUCTION_FORCE_ALLOW=1` bypasses the gate
  (emergency override); still audits `canary_gate_force_allowed`.
- `BEACONRY_CANARY_RING_ENABLED=0` disables the gate globally (opt
  out).
- Scouts advertise their role via `SCOUT_HOST_ROLE` env (default
  `workstation`). Operator sets it explicitly on staging +
  production.
- `make status` fleet view shows the role column.

## Out of scope

- Automatic ring promotion (Ring 0 → Ring 1 → Ring 2 chain). v1 is
  **block/allow** only; promotion requires separate orchestration.
- Per-CVE ring overrides.
- Health-check waterfall per ring.
- Role-based keyboard changes (operator still sees the normal
  buttons; the gate runs after the tap).

## Acceptance criteria (Gherkin)

```gherkin
Feature: canary ring blocks production fixes without staging

  Scenario: Production fix blocked without staging precedent
    Given server2 (role=production) has CVE-2026-X in status=open
      And no role=staging host has CVE-2026-X in fixed_verified
    When operator taps [🔧 Fix now] on server2's alert
    Then the fix does NOT execute
      And the Telegram reply names the staging gap
      And audit `canary_gate_blocked` fires

  Scenario: Production fix allowed after staging verified
    Given hetzner-staging (role=staging) has CVE-2026-X in fixed_verified
      And server2 (role=production) has CVE-2026-X in status=open
    When operator taps [🔧 Fix now] on server2's alert
    Then the fix executes
      And audit `canary_gate_passed` fires

  Scenario: Non-production host bypasses gate
    Given workstation (role=workstation) has CVE-2026-X in status=open
    When operator taps [🔧 Fix now]
    Then the fix executes immediately (no gate check)

  Scenario: Gate globally disabled
    Given BEACONRY_CANARY_RING_ENABLED=0
    When operator taps [🔧 Fix now] on a production host
    Then fix executes (gate bypassed)

  Scenario: Force allow bypass audits the override
    Given BEACONRY_CANARY_PRODUCTION_FORCE_ALLOW=1
      And production fix would otherwise be blocked
    When operator taps [🔧 Fix now]
    Then fix executes
      And audit `canary_gate_force_allowed` fires (not `canary_gate_passed`)

  Scenario: Scout advertises role on heartbeat
    Given scout boots with SCOUT_HOST_ROLE=staging
    When scout sends heartbeat or /scan
    Then the payload carries role="staging"
      And hub's scouts table UPDATEs that host's role

  Scenario: Missing role on legacy scout
    Given scout pre-BC-092 (no role field in heartbeat)
    When hub receives the heartbeat
    Then host.role stays at its DB default `workstation`
```

## Capabilities delivered

- **CAP-T188: Scout advertises `SCOUT_HOST_ROLE` on heartbeat + /scan**
  (type: technical, status: active)
  *Default `workstation`. Values `workstation | staging | production`.
  Hub persists via `ALTER TABLE scouts ADD COLUMN role` (schema v6→v7).*
- **CAP-T189: Hub canary-ring gate on production fixes**
  (type: technical, status: active)
  *Fix dispatch blocked for `role=production` finding when no
  `role=staging` scout has the same CVE in `fixed_verified`.
  Audit events: `canary_gate_passed`, `canary_gate_blocked`,
  `canary_gate_force_allowed`. Operator opt-out via
  `BEACONRY_CANARY_RING_ENABLED=0`; emergency bypass via
  `BEACONRY_CANARY_PRODUCTION_FORCE_ALLOW=1`.*
- **CAP-T190: `make status` fleet view shows role column**
  (type: technical, status: active)
  *One-char glyph per row (`W/S/P` for workstation/staging/
  production). Operator sees at-a-glance where each host sits in
  the canary ladder.*

## Links

- `memory/design-decisions.md § Multi-host canary ring`
- `docs/LIFECYCLE.md § 16.1` (missing implementation — this closes it)
