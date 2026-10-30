---
id: BC-007
title: `make findings` — persistent CLI view of current CVE state
status: shipped
created: 2026-10-30
branch: feat/BC-007-findings-cli
plan: docs/plan/BC-007-findings-cli-plan.md
---

# BC-007: `make findings` — persistent CLI view of current CVE state

## Narrative

The operator ran `make demo` yesterday, got 5 Telegram messages, tapped Accept on three and Snooze on two. Today their phone is scrolled past — they want to know "what did I accept and when" without re-scanning or re-reading Telegram history. They type `make findings` from the repo root. One command prints a formatted table — CVE id, severity, CVSS, host, image, package, status, fix command, when found — pulled directly from `.beaconry-cache/beaconry.db`. Nothing new runs; it's a pure read of state the operator already created. If they want only one slice, `make findings STATUS=accepted` filters.

## Trigger

Operator wants to review the current state of findings after one or more scans. Could be hours or days after the Telegram taps.

## Expected outcome

- `make findings` prints a table (CVE, severity, CVSS, host, image/source, package, status, fix command, found_at) sorted by (status, tier, -cvss)
- `make findings STATUS=open|snoozed|accepted|fixed` filters by status
- Empty DB → friendly "No findings in the dev DB yet. Run `make demo` first." (not an empty table)
- Works against the local-Python dev DB (`.beaconry-cache/beaconry.db`) OR the Compose named volume mount if the stack is running (defer to env `DATA_DIR` same way hub does)
- No network, no Docker, no Telegram — pure SQLite read
- Terminal-friendly output: aligned columns, no color dependency, truncates long fields so the table doesn't wrap on 100-column terminals
- Exit 0 always (reading state can't "fail" in a way that should fail CI)

## Out of scope

- Modifying findings (set status, delete) — read-only
- Web UI (deferred — may become BC-008 if demand shows)
- Pagination or interactive scrolling — just a plain print; use `less` if you want paging
- Export to CSV / JSON — can add via flag later
- Showing findings from multiple hosts — single-DB view for the POC
- Color / terminal styling with external deps

## Acceptance criteria (Gherkin)

```gherkin
Feature: Persistent CLI view of current findings

  Background:
    Given `make demo` has been run at least once (or findings exist in the dev DB)

  Scenario: `make findings` prints all findings
    When the operator runs "make findings"
    Then stdout contains a header row with columns: CVE, Severity, CVSS, Host, Target, Package, Status
    And at least one data row appears per finding in the DB
    And exit code is 0

  Scenario: Empty DB → friendly message, exit 0
    Given the dev DB has no findings (or doesn't exist)
    When the operator runs "make findings"
    Then stdout contains "No findings"
    And stdout points to `make demo`
    And exit code is 0

  Scenario: STATUS filter narrows the output
    Given findings exist with different statuses
    When the operator runs "make findings STATUS=accepted"
    Then every row shown has status "accepted"
    And no "open"/"snoozed"/"fixed" rows appear

  Scenario: Rows are sorted by (status, tier, -cvss)
    Given multiple findings with mixed status + severity
    When the operator runs "make findings"
    Then open findings appear before snoozed/accepted/fixed
    And within each status group higher-CVSS findings come first

  Scenario: Long fields truncate cleanly
    Given a finding has a very long package or image name
    When the operator runs "make findings"
    Then no row wraps to a second visual line on a 100-char terminal
    And truncated cells end with "…" so the operator knows there's more

  Scenario: Pure read — no side effects
    When the operator runs "make findings"
    Then no HTTP calls, no docker commands, no subprocesses besides SQLite reads happen
    And the dev DB file timestamp is unchanged (no writes)
```

## Capabilities delivered

- **CAP-B012: Operator reviews current findings state without re-running the pipeline** (business, aspirational → active) — new.
- **CAP-T025: Findings reporter reads SQLite, formats a table, honors STATUS filter** (technical, active) — new.

Extends: CAP-B002 (notifications arrive) — now operator has a persistent view beyond Telegram scrollback.

## Open questions

- Should `make findings` work against the Compose named volume (hub's `/data`) too, not just `.beaconry-cache/`? → Default: honor `DATA_DIR` env, so both paths resolve. Settle in plan doc.
- Color on tty vs plain on pipe? → Plain for now, add color flag later if operators ask.

## Links

- Design: `DESIGN.md § Architecture` (data model)
- Related: BC-002 (SQLite dedup — the data this reads), BC-005 (Compose demo — alternate DB path)
