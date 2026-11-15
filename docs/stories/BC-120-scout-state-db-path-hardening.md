---
id: BC-120
title: Scout state DB path hardening — degrade loudly, never drop silently
status: in-progress
created: 2026-11-15
branch: feat/BC-120-scout-state-db-path-hardening
plan: docs/plan/BC-120-scout-state-db-path-hardening-plan.md
---

# BC-120: Unwritable `scout-state.db` is a loud problem, not a silent drop

## Narrative

Operator on bb-82jq: BC-119 landed, hub's wire cap is fine. But
`beaconry audit EVENT=scan_delivered HOST=bb-82jq` is empty; scout
logs `BC-107: can't open scout-state.db at /data/scout-state.db:
unable to open database file` every POST attempt. The scout runs
UID 65532 (nonroot). Docker creates named-volume `_data` dirs as
`root:root 755` on first mount — the hub's ansible role has a
"chown to 65532" step; the scout role doesn't.

Pre-BC-120: every POST that fails gets silently dropped because
`_queue_pending_scan` returns without persisting. `/healthz` shows
`pending_scans_queue_depth: 0` (because the table was never created,
`sqlite3.connect` returns 0 from the except-branch) — looks healthy.
Operator has no way to know the retry queue is broken short of
reading scout stderr.

Post-BC-120: scout at startup tests `BEACONRY_SCOUT_STATE_DB`
writability. If unwritable, scout:
1. Logs a WARN with the exact fix recipe.
2. Falls back to `/tmp/scout-state-<pid>.db` (ephemeral but writable)
   so `_queue_pending_scan` keeps working through the scout process
   lifetime — scans aren't silently dropped.
3. Sets `state_db_degraded: true` + `state_db_effective_path: ...`
   in `/healthz.checks`, which `make status`'s DETAILS cell
   surfaces as `state_db degraded (ephemeral fallback)`.

And on the homelab side: `beaconry_scout` role gains a "chown data
volume to UID 65532" task (mirror of the hub role) so new deploys
never hit this problem in the first place.

## Trigger

- Scout boots (any scout, not just bb-82jq).
- OR Docker named volume for scout data is first mounted without
  explicit ownership, leaving it `root:root`.

## Expected outcome

- Scout boots, runs, keeps scanning, keeps posting, retry queue
  works (just ephemeral if primary path is read-only).
- `make status` shows the degradation.
- Operator sees the fix recipe in scout logs on first boot.
- Homelab ansible role for scout no longer leaves the primary path
  unwritable.

## Out of scope

- Database migration between primary + fallback paths. If a scout
  starts on fallback then the primary becomes writable later, the
  fallback DB's rows don't move. Scout restarts after the ops fix
  land anyway.
- `pending_scans` durability. Fallback is ephemeral by design — this
  story is "degrade loudly", not "make retry queue fully durable
  under all ownership faults".

## Acceptance criteria (Gherkin)

```gherkin
Feature: Unwritable scout-state.db degrades loudly to an ephemeral fallback

  Scenario: Primary path writable — no degradation
    Given a scout with BEACONRY_SCOUT_STATE_DB=/data/scout-state.db
    And /data is writable by UID 65532
    When scout starts
    Then /healthz.checks.state_db_degraded is false
    And /healthz.checks.state_db_effective_path is /data/scout-state.db

  Scenario: Primary unwritable — scout falls back to /tmp
    Given a scout with BEACONRY_SCOUT_STATE_DB=/data/scout-state.db
    And /data is NOT writable by UID 65532
    When scout starts
    Then scout logs "BC-120: scout-state DB path /data/scout-state.db is unwritable; falling back to /tmp/scout-state-<pid>.db — fix: `chown 65532:65532 /data` on the host"
    And /healthz.checks.state_db_degraded is true
    And /healthz.checks.state_db_effective_path is /tmp/scout-state-<pid>.db
    And subsequent _queue_pending_scan calls persist to the fallback path

  Scenario: Fallback stays usable across pending_scans operations
    Given scout is running on the fallback path
    When _queue_pending_scan is called with a failed POST
    Then the row persists to the fallback DB
    And post_retry_sweep reads it

  Scenario: Fallback resets per process (ephemeral is expected)
    Given scout ran on the fallback path and queued 5 scans
    When scout restarts
    Then those 5 rows are gone (different /tmp/scout-state-<pid>.db)
    And no stale ownership error shows up in logs

  Scenario: Scout Dockerfile pre-creates /data owned by UID 65532
    Given a fresh scout image built from poc/scout/Dockerfile
    And no host volume mounted at /data (standalone deploy)
    When scout boots as UID 65532
    Then /data exists in the container FS
    And sqlite3.connect("/data/scout-state.db") succeeds without a chown step
    And state_db_degraded is false on first boot

  Scenario: Read-only populated primary DB still falls back (ultrareview fix)
    Given /data/scout-state.db already contains the pending_scans table
    And the file (or parent dir) is not writable by UID 65532
    When scout starts
    Then the real write probe (INSERT+ROLLBACK) detects the fault
    And scout falls back to /tmp/scout-state-<pid>.db
    And /healthz.checks.state_db_degraded is true

  Scenario: Corrupt primary DB falls back, not crash-loop (ultrareview fix)
    Given /data/scout-state.db exists but is not a valid sqlite file
    When scout starts
    Then sqlite3.Error is caught (DatabaseError is the parent class)
    And scout falls back to /tmp/scout-state-<pid>.db
    And scout does NOT crash-loop

  Scenario: make status surfaces the degradation
    Given a scout running with state_db_degraded=true
    When operator runs `beaconry status`
    Then the scout row's DETAILS cell includes "state_db degraded"
```

## Capabilities delivered

- **CAP-T222: Scout state DB path degradation + /healthz visibility**
  (type: technical, status: active)
  *Scout at startup writability-tests `BEACONRY_SCOUT_STATE_DB`; if
  it can't `sqlite3.connect` + `CREATE TABLE`, falls back to
  `/tmp/scout-state-<pid>.db`, logs a WARN with the host-side chown
  recipe, and reports `state_db_degraded: bool` +
  `state_db_effective_path: str` via `/healthz.checks`.
  `_queue_pending_scan` + `post_retry_sweep` + `_pending_scans_queue_depth`
  read `_effective_scout_state_db_path()` instead of `SCOUT_STATE_DB_PATH`
  so the fallback is used everywhere consistently.
  `make status` DETAILS cell renders `state_db degraded` when the
  flag fires.*

Supports CAP-B023 (observability of degraded modes) and CAP-T215/T221
(storage path symmetric with hub's audit sticky-flag pattern from
BC-115).

## Open questions

Resolved during 2-iteration clarification (2026-11-15):

1. Fail-fast vs. degrade? **Degrade.** Scout's main job is scanning, not
   persisting retry state; silently dropping a scan is worse than
   running in ephemeral mode. Operator sees it in `/healthz`.
2. Fallback path location? **`/tmp/scout-state-<pid>.db`.** `/tmp` is
   always writable for UID 65532. `<pid>` suffix avoids conflicts
   across restarts.
3. Can we just let `/tmp` be the default? **No.** `/tmp` is ephemeral
   — scout restarts lose queued rows. BC-107 explicitly chose
   `/data` to survive restarts. Keep `/data` as default; only fall
   back on fault.
4. Should fallback be configurable too? **No.** YAGNI. Only one
   fallback path; keep the fault handling simple.

## Links

- Related stories: BC-107 (retry queue), BC-115 (audit sticky-flag pattern),
  BC-078 (distroless nonroot)
- Homelab PR for ansible side: will be opened separately against `bb-claw/homelab`.
