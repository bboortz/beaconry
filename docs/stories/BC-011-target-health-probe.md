---
id: BC-011
title: Target-container health probe — operator-verified service recovery after fix
status: shipped
created: 2026-10-30
branch: feat/BC-011-target-health-probe
plan: docs/plan/BC-011-target-health-probe-plan.md
---

# BC-011: Target-container health probe

## Narrative

Today beaconry can tell the operator "✅ Fixed and verified" after a successful `docker pull` + grype re-scan. That assertion means "the CVE is gone from the image file." It does **not** mean "the service came back up healthy after you restarted it." BC-014's same-track pull made the chance of a service breakage smaller (no silent major-version jumps), but the risk isn't zero — an upstream security update inside a fluid tag can still break config, data format, or startup. Operators can't tell beaconry's "fixed" from "fixed-but-your-service-is-dead" without checking themselves. BC-011 adds a **"🔍 Verify service health"** button that appears on the `✅ Fixed and verified` message when a probe policy is configured for the image. Operator restarts their container the way they always have, then taps the button. Beaconry runs a policy-driven probe (Docker HEALTHCHECK read, TCP connect, or HTTP GET) against the service and reports pass/fail with explicit DB state transitions. Operators can trust the "fixed" label because they chose to confirm it.

## Trigger

Operator sees `✅ Fixed and verified` after BC-009's fix flow. For images with a configured health probe, a new `🔍 Verify service health` inline button is attached. Operator restarts the service themselves (unchanged from BC-009), then taps the button to run the probe.

## Urgency

N/A — safety refinement that closes the "beaconry can lie about fix success" gap.

## Expected outcome

- New env `BEACONRY_HEALTH_PROBES` carries image→probe policy. Syntax: `<image_ref>=<probe_type>:<probe_arg>;<image_ref>=<probe_type>:<probe_arg>`. Examples: `postgres:16-alpine=tcp:postgres:5432`, `nginxproxy/nginx-proxy:alpine=http:http://nginx-proxy/`
- Three probe types for V1:
  - `docker_healthcheck` — read Docker's own HEALTHCHECK status for a container referenced by image name (uses docker.from_env)
  - `tcp` — plain socket connect to `<host>:<port>` (hub reaches target via Compose network name)
  - `http` — HTTP GET to `<url>`; HTTP 200-399 = pass, anything else = fail
- `on_button_click` grows a new action: `verify:<finding_id>` reached by tapping the new inline button
- Three new terminal states on the probe:
  - `✅ Service healthy — probe passed` — DB status flips to `fixed_verified`
  - `⚠️ Probe failed: <reason>` — DB status flips to `verification_failed`; message includes rollback hint
  - `⏭️ No probe configured for <image>` — DB unchanged; emitted when operator taps and no policy matches
- New DB status values `fixed_verified` and `verification_failed` added via idempotent schema migration (mirrors the BC-007 tier-column migration pattern)
- The `🔍 Verify service health` button is attached to the ✅ Fixed message **only** when a policy matches the image — no button means operator doesn't have to think about it
- `make findings` surfaces the new statuses
- Policy parser rejects malformed input with a named error at hub startup, not at probe-time
- memory_drift_bans entry: pre-BC-011 descriptions of "verify_fix only checks CVE presence" are now stale; add a ban pattern

## Out of scope

- Auto-restart of operator's containers after fix — operator still owns the restart (unchanged separation of concerns)
- Systemd stability window (design-decisions.md § waterfall level 3) — containers only in V1; systemd deferred to a host-package follow-up
- Custom operator-defined command probe (waterfall level 4) — parked for operator-asks-for-it
- Auto-rollback execution on probe failure — probe failure flips DB status + emits rollback command text; execution stays operator-triggered (consistent with existing BC-009 fix-execution opt-in model)
- Multi-container probes (one container per image for V1)
- Probe retry logic / exponential backoff — single attempt per tap (operator can tap again)
- Probe metrics / observability dashboard — IDEAS entry
- Policy-per-container (vs policy-per-image) — probes targeted at service names on the Compose network

## Acceptance criteria (Gherkin)

```gherkin
Feature: Operator triggers a post-restart health probe on a fixed service

  Background:
    Given a BEACONRY_HEALTH_PROBES policy exists for the image
    And a finding was already marked ✅ Fixed and verified via BC-009

  Scenario: probe button appears only when a policy matches
    When the hub sends the "✅ Fixed and verified" message
    Then the inline keyboard includes "🔍 Verify service health"
    And it does not include it when no policy matches the finding's image

  Scenario: tcp probe passes → status fixed_verified
    Given policy "postgres:16-alpine=tcp:postgres:5432"
    And a running postgres service answers on TCP port 5432
    When the operator taps "🔍 Verify service health" on a postgres:16-alpine finding
    Then the message edits to "✅ Service healthy — probe passed"
    And the dev DB status for that finding is "fixed_verified"

  Scenario: http probe fails → status verification_failed with rollback hint
    Given policy "nginxproxy/nginx-proxy:alpine=http:http://nginx-proxy/healthz"
    And the nginx-proxy container returns HTTP 503
    When the operator taps "🔍 Verify service health"
    Then the message edits to "⚠️ Probe failed: HTTP 503"
    And the message includes "Rollback with: docker tag"
    And the dev DB status for that finding is "verification_failed"

  Scenario: docker_healthcheck probe reads container Health.Status
    Given policy "redis:7-alpine=docker_healthcheck:redis"
    And the redis container's docker inspect reports Health.Status=unhealthy
    When the operator taps "🔍 Verify service health"
    Then the message edits to "⚠️ Probe failed: Docker reports container unhealthy"
    And the dev DB status is "verification_failed"

  Scenario: operator taps but no policy matches
    Given policy env is empty OR no entry matches the finding's image
    When the operator taps "🔍 Verify service health"
    Then the message edits to "⏭️ No probe configured for <image>"
    And the dev DB status is unchanged

  Scenario: malformed policy fails fast at startup, not at probe time
    Given BEACONRY_HEALTH_PROBES contains "garbage"
    When the hub starts
    Then the lifespan raises a named error during startup
    And no notifications are sent until the policy is corrected

  Scenario: probe button handler is idempotent
    Given the finding's status is already "fixed_verified"
    When the operator taps "🔍 Verify service health" again
    Then the message says "ℹ️ Already fixed_verified — ..." and no re-probe runs

  Scenario: make findings surfaces the new statuses
    When "make findings" runs
    Then its STATUS filter accepts "fixed_verified" and "verification_failed"
    And the output format includes them in the status column
```

## Capabilities delivered

- **CAP-B018: Operator confirms a fix actually brought the service back up** (business, active) — new. Promotes "✅ Fixed and verified" from "CVE gone from image" to "CVE gone + probe-confirmed service health". Closes the one remaining way beaconry can mislead operators.
- **CAP-T048: Policy-driven probe resolver (`BEACONRY_HEALTH_PROBES` env var)** (technical, active) — new. Parses per-image probe policies at startup; fails fast on malformed input; `resolve_probe(image_ref)` returns probe spec or None.
- **CAP-T049: Three probe types — docker_healthcheck, tcp, http** (technical, active) — new. Minimum useful set for the POC homelab stack; waterfall levels 3+4 (systemd, custom command) parked for later.
- **CAP-T050: New DB statuses `fixed_verified` + `verification_failed` with idempotent migration** (technical, active) — new. Schema migration mirrors the BC-007 tier-column pattern.

Updates: CAP-B014 description expands to note the follow-up probe step; CAP-T032 (fix-flow three-state) description notes BC-011's two additional post-probe states.

## Open questions

- (none — resolved by scoping to operator-triggered probe + three probe types)

## Links

- Design: `memory/design-decisions.md § Auto-fix trust model` (BC-011 implements waterfall levels 1–2 per that section's "run strongest available" rule, with the operator-trigger shift)
- Depends on: BC-009 (fix flow + verify_fix), BC-012 (shared beaconry_health primitives for probe implementations)
- Related: BC-014 (same-track pull reduces the class of failures BC-011 catches), BC-015 (memory drift guard — BC-011 adds a new ban entry per the DD-TGD step 6a checklist)
