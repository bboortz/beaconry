---
id: BC-009
title: Real fix execution — tap Fix now, image is pulled, re-scanned, verified
status: shipped
created: 2026-10-30
branch: feat/BC-009-real-fix-execution
plan: docs/plan/BC-009-real-fix-execution-plan.md
---

# BC-009: Real fix execution

## Narrative

The homelab operator ran BC-003 and taped the three buttons on their phone — Snooze, Accept, and Fix. The first two did what they said. The third edited the message to "⚙️ Fix execution coming in a later story" and the demo arc ended on a disappointment. BC-009 closes it: tapping 🔧 Fix now now actually runs. Within ~90 seconds the message walks through "⏳ Fixing…" → "🔍 Verifying…" → one of three honest outcomes: `✅ Fixed and verified`, `⚠️ Pulled, but CVE still present upstream`, or `❌ Fix failed: <reason>`. `make findings` the next day shows the finding with `status: fixed` for the clean cases. The product's actual promise — "CVE arrives, you tap, a minute later your image is patched and verified" — is finally visible end-to-end.

## Trigger

Operator sees a Telegram notification for a container CVE and wants to act. Taps 🔧 Fix now.

## Expected outcome

- `BEACONRY_FIX_ENABLED=1` is the new default; opt-out via `BEACONRY_FIX_ENABLED=0`
- Tapping 🔧 Fix now on a `source_type=docker` finding runs the DockerAdapter:
  - `docker tag <current>:<tag> <base>:rollback` (safety snapshot before pull)
  - `docker pull <base>:latest`
  - Message walks "⏳ Fixing…" → "🔍 Verifying…"
  - `verify_fix` runs real syft + real grype on `<base>:latest`, checks if the specific CVE id + package combination is still present
- Three terminal states, each with its own message + DB status:
  1. **Pull succeeded, re-scan clean**: `✅ Fixed and verified — CVE-X on <image>` · DB status → `fixed`
  2. **Pull succeeded, CVE still present upstream**: `⚠️ Pulled, but CVE still present upstream — CVE-X in <package>. Upstream hasn't patched yet.` · DB status unchanged (still `open`)
  3. **Pull failed**: `❌ Fix failed: <truncated error>. Image unchanged. Rollback tag still exists if you need to inspect.` · DB status unchanged
- Operator can still opt into the stub behavior for demo/dry-run purposes via `BEACONRY_FIX_ENABLED=0`
- Live-verified via a tap session with the user on real Telegram against nginx:1.24

## Out of scope

- Health check waterfall from design-decisions.md § Auto-fix trust model (deferred — needs separate story)
- Auto-rollback on health check failure (depends on health check waterfall)
- Automatic container restart (operator does `docker compose up -d` themselves)
- Fix for non-docker source types (pacman, pip) — ManualAdapter stays as the fallback
- Pinning to specific SHA256 digest instead of `:latest` (IDEAS entry "KEV auto-fix is scary" — deferred)
- Silent auto-fix even for Tier 0 safe-class (every fix still requires operator tap for V1)
- Multi-image batch fix
- A `make rollback` CLI (operator does `docker tag <base>:rollback <base>:latest` manually)
- Audit log entry for the fix action (IDEAS "Append-only audit log" — deferred)

## Acceptance criteria (Gherkin)

```gherkin
Feature: Real fix execution via Telegram Fix-now button

  Background:
    Given BEACONRY_FIX_ENABLED is unset (defaults to on in BC-009)
    And a finding with source_type=docker exists in the dev DB
    And a real Telegram notification for that finding has arrived

  Scenario: Pull succeeds + verify-fix clean → status fixed
    When the operator taps [🔧 Fix now]
    Then within 90 seconds the message walks through "⏳ Fixing…" → "🔍 Verifying…"
    And the terminal message is "✅ Fixed and verified"
    And `docker tag <base>:<tag> <base>:rollback` ran before the pull
    And `docker pull <base>:latest` ran
    And `verify_fix` returned True (syft + grype on the new image found no match for the CVE)
    And the dev DB status for that finding is "fixed"

  Scenario: Pull succeeds + verify-fix still present → honest "upstream not patched"
    When the operator taps [🔧 Fix now] on a finding whose CVE is still present in the upstream image
    Then the message is edited to "⚠️ Pulled, but CVE still present upstream"
    And the message names the package: "<package>"
    And the dev DB status for that finding is NOT changed to "fixed"
    And the dev DB status remains "open"

  Scenario: Docker pull fails → honest failure, image unchanged
    When the operator taps [🔧 Fix now] and `docker pull` returns non-zero
    Then the message is edited to "❌ Fix failed: <truncated stderr>"
    And the message says "Image unchanged"
    And the dev DB status remains "open"
    And the rollback tag still exists for inspection

  Scenario: Operator can opt out of fix execution
    Given BEACONRY_FIX_ENABLED=0 is set
    When the operator taps [🔧 Fix now]
    Then the message is edited to "⚙️ Fix execution disabled (BEACONRY_FIX_ENABLED=0)"
    And no docker subprocess runs
    And the dev DB status remains "open"

  Scenario: Non-docker finding → ManualAdapter path, no docker subprocess
    When the operator taps [🔧 Fix now] on a finding with source_type=alpm
    Then the ManualAdapter fallback runs
    And the message names the manual command (e.g. `sudo pacman -Syu <package>`)
    And no docker subprocess runs
    And the dev DB status remains "open" (manual action required)

  Scenario: Rollback tag survives after a successful fix
    Given a successful fix on nginx:1.24
    When "docker image inspect nginx:rollback" is run
    Then it returns the pre-fix image id (operator can restore with `docker tag nginx:rollback nginx:latest`)

  Scenario: Live tap session — operator sees "✅ Fixed and verified" (or honest alternative)
    Given a running `make demo` session + 5 fresh notifications on operator's phone
    When operator taps [🔧 Fix now] on one message
    Then within 90 seconds the message edits to one of the three terminal states
    And `make findings` reflects the correct DB status
```

## Capabilities delivered

- **CAP-B014: Operator closes a CVE from Telegram — tap Fix, image pulled, re-scanned, verified** (business, aspirational → active) — new.
- **CAP-T030: DockerAdapter.execute pulls :latest with a :rollback safety tag before pulling** (technical, active) — new.
- **CAP-T031: verify_fix re-runs syft + grype on the new image and detects CVE absence or persistence** (technical, active) — new.
- **CAP-T032: Fix flow distinguishes "pull failed" from "pull OK but CVE still upstream" with distinct Telegram messages** (technical, active) — new.
- **CAP-T033: Operator can opt out of fix execution via BEACONRY_FIX_ENABLED=0 (stub path preserved for demo/safe exploration)** (business, active) — new.

Extends: CAP-B008 (fix-now stub) now gated to opt-out mode; default behavior is real execution.

## Open questions

- (none — resolved during 2026-10-30 3-iteration virtual-user panel)

## Links

- Design: `memory/design-decisions.md § Auto-fix trust model` (BC-009 implements the "safe to auto-fix when… snapshot (containers) + health check waterfall passes" promise *partially* — snapshot via :rollback tag yes, health check waterfall deferred)
- Related: BC-003 (buttons wired), BC-005 (Compose stack), IDEAS entry "KEV auto-fix is scary" (BC-009 keeps always-confirm; digest pinning deferred)
