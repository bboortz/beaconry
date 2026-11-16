---
id: BC-127
title: `beaconry findings --severity` filter (Critical/High/Medium/Low)
status: shipped
created: 2026-11-16
branch: feat/BC-127-findings-severity-filter
plan: docs/plan/BC-127-findings-severity-filter-plan.md
---

# BC-127: Filter findings by severity without `| grep Critical`

## Narrative

Operator wants to focus triage on only critical findings:

```
beaconry findings --status open --host hetzner-staging | grep -i Critical
```

The grep workaround is fragile: it misses rows whose other columns
happen to contain "Critical" as a substring (e.g. a package name),
loses the header, and depends on terminal width for the column
layout. BC-123 added `--cve`; this story follows the same pattern
for `severity`.

Post-BC-127: `beaconry findings --severity Critical` filters in
the DB layer (`WHERE severity = ?`), supports comma-separated
multi-select (`--severity Critical,High`), works over HTTP, and is
exposed via `make findings SEVERITY=Critical`.

## Trigger

- Operator triaging high-tier findings during an incident or weekly
  sweep.
- Dashboards / scripts that want programmatic severity-filtered
  feeds.

## Expected outcome

- `beaconry findings --severity Critical` returns only
  severity=Critical rows.
- `beaconry findings --severity Critical,High` returns both.
- Case-insensitive match (`critical` == `Critical`).
- Combines with existing filters: `--status open --host X --cve Y --severity Critical`.
- HTTP endpoint `GET /findings?severity=Critical`.
- `make findings SEVERITY=Critical` Makefile variable.
- No-match returns "No findings with severity='Critical' …" (same
  shape as other no-match messages).

## Out of scope

- Fuzzy ranking by tier (Critical > High > Medium > Low). The
  existing sort already does this; severity filter is a hard
  include-only gate.
- Numeric CVSS filter (`--cvss-min 9.0`). Separate story if needed
  — severity string filter is the common case.

## Acceptance criteria (Gherkin)

```gherkin
Feature: `beaconry findings --severity` filter

  Scenario: Single-severity filter returns matching rows only
    Given findings DB has Critical + High + Medium rows
    When operator runs `beaconry findings --severity Critical`
    Then only Critical rows are returned

  Scenario: Comma-separated multi-select
    When operator runs `beaconry findings --severity Critical,High`
    Then Critical AND High rows are returned (not Medium)

  Scenario: Case-insensitive
    When operator runs `beaconry findings --severity critical`
    Then same Critical rows returned as with the capitalized form

  Scenario: Combines with --status, --host, --cve
    When operator runs `beaconry findings --status open --host X --severity Critical`
    Then WHERE clause filters by all three

  Scenario: HTTP endpoint respects the filter
    When GET /findings?severity=Critical
    Then only Critical rows in the JSON response

  Scenario: Makefile SEVERITY= variable
    When operator runs `make findings SEVERITY=Critical`
    Then the CLI receives --severity Critical

  Scenario: Invalid severity string returns zero rows (not error)
    When operator runs `beaconry findings --severity Catastrophic`
    Then exit code 0 + "No findings with severity='Catastrophic'" message
```

## Capabilities delivered

- **CAP-T230: `beaconry findings --severity` filter**
  (type: technical, status: active)

  *New `--severity` argparse flag + `severity` HTTP query param, both
  accepting comma-separated case-insensitive values that map to
  SQL `WHERE severity IN (?, ?, …)`. Threaded through the local-DB
  path (`load_findings`), the HTTP path (`_findings_via_http` →
  `http_findings` endpoint), and the local-compose path
  (`_run_in_container` + inline script's `##SEVERITY_FILTER##`
  placeholder). `make findings SEVERITY=Critical,High` passes
  through from the Makefile. Normalization: inputs are
  `.strip().title()`'d (so `critical` → `Critical`, matching the
  DB's canonical casing from grype output).*

Extends CAP-T207 (findings CLI) and CAP-T076 (CLI remote routing).

## Open questions

Resolved during drafting (2026-11-16):

1. Single-value only or comma-separated?
   **Comma-separated.** Matches existing `--event` behavior in
   `beaconry audit`; operators often want `Critical,High`.
2. Case sensitivity?
   **Case-insensitive input, canonical casing in SQL.** Match
   grype's `Critical`/`High`/`Medium`/`Low`/`Negligible`/`Unknown`
   convention.
3. Validate the input against the known set?
   **No.** An unknown severity returns zero rows cleanly — don't
   error. Operators can type anything; the no-match message tells
   them what they searched.
4. Interaction with `--cvss-min`?
   **Not in this story.** Severity filter is the common case.

## Links

- Related: BC-105 (findings CLI shape), BC-123 (`--cve` filter — same
  pattern), BC-007 (findings CLI origin).
