---
id: BC-022
title: Digest shows KEV markers for suppressed findings
status: shipped
created: 2026-10-31
branch: feat/BC-022-digest-kev-awareness
plan: docs/plan/BC-022-digest-kev-awareness-plan.md
---

# BC-022: Digest shows KEV markers for suppressed findings

## Narrative

Operator opens Telegram, sees the suppressed-findings digest ("📋 42 additional finding(s) suppressed this run"). Each row shows CVE / severity / CVSS / package — no KEV indicator. BC-020 gave individual notifications a 🚨 KEV header, but `send_digest` is tier-blind. Usually the suppressed set is non-KEV by construction (dedup_and_cap sorts by `(tier, -cvss)`, so KEV wins top-N slots over any CVSS-9.8 non-KEV). But when `#KEV > BEACONRY_MAX_FINDINGS_PER_RUN`, some KEV findings land in the suppressed set with no visual marker — exploits in the wild hidden in a prose list. BC-022 closes that invariant break.

Urgency: N/A — correctness bug in notification UX.

## Trigger

A scan produces more KEV findings than the per-run cap (`BEACONRY_MAX_FINDINGS_PER_RUN`, default 5). The overflow lands in `send_digest`.

## Expected outcome

Two observable changes in the digest message:

1. **KEV prefix on every row:** rows for KEV findings start with `🚨 ` so the operator sees them at a glance.
2. **Loud banner if any suppressed finding is KEV:** inserted as the second line (after the "N suppressed" header). Example:
   > ⚠️ **3 suppressed finding(s) are KEV** — raise BEACONRY_MAX_FINDINGS_PER_RUN to see them individually.

## Out of scope

- Not changing the dedup/cap sort order. The invariant stays `(tier, -cvss)` — KEVs still win top-N; this story only patches the digest to communicate what already happens when #KEV > cap.
- Not changing cap default. Operator still owns the knob.
- Not changing the digest's "top 10 rows" truncation.
- Not re-sorting the digest. It inherits `dedup_and_cap`'s order, which already puts KEV findings first within the suppressed set.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Digest shows KEV markers for suppressed findings

  Scenario: Digest row for a KEV finding gets a 🚨 prefix
    Given send_digest is called with a suppressed list that includes one KEV finding
    When the message is rendered
    Then the row for the KEV finding starts with "🚨 "
    And rows for non-KEV findings do NOT start with "🚨 "

  Scenario: Suppressed set with ≥1 KEV emits the loud banner
    Given send_digest is called with 3 suppressed findings, 1 of them KEV
    When the message is rendered
    Then the body contains "1 suppressed finding(s) are KEV"
    And the banner is above the per-finding bullet list

  Scenario: Suppressed set with no KEV omits the banner
    Given send_digest is called with 3 suppressed findings, 0 of them KEV
    When the message is rendered
    Then the body does not contain "are KEV"

  Scenario: Empty suppressed list returns without sending
    Given send_digest is called with []
    When invoked
    Then no Telegram message is sent (unchanged from pre-BC-022)
```

## Capabilities delivered

- **CAP-T062: Digest format surfaces KEV markers for suppressed findings** (type: technical, status: active)
  *`send_digest` renders `🚨 ` prefix on KEV rows and inserts a count banner when `any(f.kev for f in suppressed)`. Closes BC-020's invariant break: when #KEV > cap, Tier 0 findings would be invisibly listed in the digest.*

Depends on / extends:
- CAP-T059, CAP-T060 (BC-020's tier + notification wiring)
- CAP-T015 (BC-004's per-run cap + digest machinery — the thing being patched)

## Open questions

None. Scope pinned by the 4 Gherkin scenarios.

## Links

- Origin: 2026-10-31 user-raised question "This notification doesn't show any KEV. Should it?"
- Related: BC-020 (KEV ingest + Finding.kev field; this story extends it to the digest)
