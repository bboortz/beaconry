---
id: BC-066
title: Manual executor as default + configurable adapter + reminder loop
status: shipped
created: 2026-11-02
branch: feat/BC-066-manual-executor-mode
plan: docs/plan/BC-066-manual-executor-mode-plan.md
---

# BC-066: Manual executor + reminder loop

## Narrative

The POC's auto-execute path (`DockerAdapter.execute` → `docker pull`)
depends on infrastructure beaconry doesn't own: that the pull target
is a tag the operator actually uses, that the service survives a
restart, that no manual rebuild step is required. For the operator's
real stack, auto-pull hasn't been reliable.

Simplify: default to a **manual executor** that logs the operator's
intent (`"I'll fix this now"`), shows the exact command, and gets
out of the way. The actual fix happens on the operator's shell.
If the CVE is still present an hour later, beaconry nudges them.

Executor choice is configurable so operators whose stack *is*
amenable to auto-pull (homogeneous docker-compose; health-checked
services) can opt in.

## Trigger

Operator feedback 2026-11-02: *"Implement a fix adapter which is
relying on operator's manual work who is then doing the work manual.
Beaconry is logging it. But also notifying if we haven't fixed
manually in case the issue is still there. It must be configurable
which execution adapter will be used."*

Reason: *simplified POC with manual fixing because the automatic
fix depends on the infrastructure.*

## Expected outcome

### Executor choice via env

```
BEACONRY_EXECUTOR=manual   # default
BEACONRY_EXECUTOR=docker   # opt-in auto-pull
```

Hub reads once at startup, uses that adapter for every `[Fix now]`
tap. Per-finding dispatch is gone — operators choose one model for
their whole stack.

### [Fix now] with manual mode

Operator taps `[Fix now]`:

1. Hub audits `manual_fix_claimed` with finding id + cve + host +
   command + `claim_ts = now`.
2. Finding's new `last_claim_ts` column is written. Status **stays
   open** (no `fixing`/`fixed` transition — operator hasn't actually
   done anything yet).
3. Telegram message edits to:
   > 📝 **Logged** · CVE-X on host-Y
   > Run: `<exact command>`
   > We'll re-check in 1h.

### Reminder loop

New sweep `manual_claim_sweep` runs on the existing BC-035 sweep
tick. For every open finding with `last_claim_ts > 0` and
`now - last_claim_ts > BEACONRY_MANUAL_REMINDER_INTERVAL_S`
(default 3600s), hub:

1. Sends `⏰ **Still open** — you said you'd fix CVE-X 1h ago.
   Run: `<cmd>`. Reminder in another 1h.`
2. Audits `manual_fix_reminder_sent`.
3. Bumps `last_claim_ts` to now so the next reminder lands one
   interval later.

Set `BEACONRY_MANUAL_REMINDER_INTERVAL_S=0` → reminders disabled;
the claim event is still audited.

### Fix confirmed

Operator actually runs the command on their host. Next scan
re-checks:

- CVE gone → BC-031 reconcile closes the finding automatically.
  Reminder sweep stops firing (status != open).
- CVE still present → nothing changes. The hourly reminder still
  nudges.

Operator can also tap `✅ I fixed it` (existing button) to force a
re-verify immediately.

### Schema v4

New column `findings.last_claim_ts INTEGER` (nullable). Migrated via
the same `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` pattern
BC-063 used.

### Version

`0.2.2 → 0.3.0`. Minor: default behaviour for `[Fix now]` changes.
Not a breaking API change, but a visible behaviour change.

## Out of scope

- Removing `DockerAdapter` — kept, just not the default.
- `ExecutorPlugin.undo()` / auto-rollback — Phase 2.
- Multi-host canary ring enforcement — Phase 2.
- Config per-service (`BEACONRY_EXECUTOR_nginx=manual,
  BEACONRY_EXECUTOR_postgres=docker`). YAGNI.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Manual executor + reminder loop

  Scenario: Default executor is manual
    Given no BEACONRY_EXECUTOR env is set
    When hub starts
    Then the configured executor is ManualAdapter
    And /healthz reports executor="manual"

  Scenario: Operator opts in to docker executor
    Given BEACONRY_EXECUTOR=docker
    When hub starts
    Then the configured executor is DockerAdapter
    And /healthz reports executor="docker"

  Scenario: [Fix now] in manual mode logs intent, no execution
    Given hub in manual mode and a finding CVE-X on host Y
    When operator taps [Fix now]
    Then audit log contains manual_fix_claimed with finding_id + cve_id + host
    And finding.last_claim_ts is set to now
    And finding.status is unchanged ("open")
    And Telegram message shows "📝 Logged" + command + "re-check in 1h"

  Scenario: Reminder fires when claim is overdue
    Given a finding with last_claim_ts set to 2h ago and status=open
    When manual_claim_sweep runs
    Then a reminder notification is sent for that finding
    And audit event manual_fix_reminder_sent is written
    And last_claim_ts is bumped to now

  Scenario: Reminder does not fire if operator meanwhile marked it fixed
    Given a finding with last_claim_ts set to 2h ago but status=fixed_verified
    When manual_claim_sweep runs
    Then no reminder is sent

  Scenario: Reminder disabled when interval=0
    Given BEACONRY_MANUAL_REMINDER_INTERVAL_S=0 and an overdue claim
    When manual_claim_sweep runs
    Then no reminder is sent
    And audit event manual_fix_reminder_sent is NOT written

  Scenario: Schema v3 → v4 migration
    Given a DB at PRAGMA user_version=3
    When init_db runs
    Then PRAGMA user_version becomes 4
    And findings table has last_claim_ts column (nullable)
```

## Capabilities delivered

- **CAP-T137: Configurable executor adapter via BEACONRY_EXECUTOR**
  (technical).
- **CAP-T138: ManualAdapter workflow — log intent, no execution,
  deferred verify** (technical).
- **CAP-T139: Manual-claim reminder sweep + schema v4
  `last_claim_ts` column** (technical).
- **CAP-B047: Operator can run beaconry on infrastructure where
  auto-pull isn't safe or wanted — alerts stay actionable, intent
  is auditable, forgotten fixes are nudged** (business).

## Links

- Supersedes behaviour from: BC-009 (fix execution shipped),
  BC-014 (same-track pull).
- Depends on: BC-063 (schema migration pattern), BC-035
  (heartbeat-sweep infrastructure reused).
- Follow-up: `BEACONRY_EXECUTOR=ansible` / `=webhook` adapters when
  Phase 2 starts.
