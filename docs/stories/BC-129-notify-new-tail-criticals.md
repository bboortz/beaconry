---
id: BC-129
title: Loud alert for every newly-persisted Tier ≤ 1 finding, even in the suppressed tail
status: draft
created: 2026-11-16
branch: feat/BC-129-notify-new-tail-criticals
plan: docs/plan/BC-129-notify-new-tail-criticals-plan.md
---

# BC-129: Loud alert for every newly-persisted Tier ≤ 1 finding, even in the suppressed tail

## Narrative

BC-128 fixed the DB persistence gap — every unique CVE now gets a
row. But `_notify_top_n` still only fires Telegram for findings in
`to_notify` (top-N by `(tier, -cvss)` rank). On hosts with more than
`BEACONRY_MAX_FINDINGS_PER_RUN` unique CVEs (today: server2 at 318,
bb-82jq at 439), a new Tier 0 KEV or Tier 1 CVSS≥9 internet-facing
Critical can land in the suppressed tail if 100 higher-ranked items
already fill the inbox cap. Pre-BC-128 the problem was invisible
because the tail was lost entirely; post-BC-128 the tail is in the
DB but only surfaces in the Telegram digest body (text snippet,
truncated after 10 bullets, hash-throttled by BC-036). A new KEV
with a 15-min SLA cannot ride a throttled digest.

## Trigger

- Any scan where a newly-persisted finding has `tier ≤ 1` (KEV or
  CVSS ≥ 9 internet-facing) and ranks outside the top-N by
  `(tier, -cvss)`.
- Most likely on server2 and bb-82jq where unique-CVE counts far
  exceed `BEACONRY_MAX_FINDINGS_PER_RUN=100`.

## Expected outcome

- For every newly-persisted finding in `suppressed` where
  `f.tier <= 1`: hub calls `notify(f)` and records the send.
- Operator receives an individual Telegram alert (not just a digest
  bullet) for that CVE within one scan cycle.
- Top-N `notify()` behavior unchanged.
- Digest still renders the full suppressed tail (with the newly-
  loud-notified items still listed — the digest is a summary, the
  individual alert is the action trigger).
- `scan_integrity` audit event gains an `n_tail_criticals_notified`
  field so the operator can verify the escape hatch fired.
- Balance invariant unchanged:
  `n_candidates == n_persisted + n_dedup + n_dropped_in_save + n_filtered_other`.

## Out of scope

- Changing `dedup_and_cap`'s ranking — the cap still exists for
  volume protection; BC-129 just says Tier ≤ 1 is never volume-
  capped.
- Rate-limiting tail notifications differently from top-N — reuse
  the same 0.3 s inter-send sleep.
- Backfilling tail-Criticals missed before BC-129 — the next scan
  after deploy re-persists them (already in DB post-BC-128); if
  they're in the suppressed tail next scan, the new path fires.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Loud alert for every newly-persisted Tier ≤ 1 CVE, including tail

  Scenario: New Critical in the suppressed tail gets an individual notify
    Given 150 unique CVEs on host X, BEACONRY_MAX_FINDINGS_PER_RUN=100
    And ranks 1-100 are all pre-existing (newly-persisted = False)
    And rank 125 is a newly-persisted Tier 1 CVSS 9.4 internet-facing CVE
    When _ingest_candidates runs
    Then save_finding is called on all 150 items (BC-128)
    And notify() is called for rank 125 (the tail Critical)
    And n_tail_criticals_notified == 1 in scan_integrity audit

  Scenario: Tier 2 Highs in the tail stay silent (digest-only)
    Given 150 unique CVEs on host X, BEACONRY_MAX_FINDINGS_PER_RUN=100
    And rank 125 is a newly-persisted Tier 2 CVSS 7.5 CVE
    When _ingest_candidates runs
    Then save_finding is called on all 150 items
    And notify() is NOT called for rank 125 (tier > 1)
    And the rank-125 item appears in the digest body only
    And n_tail_criticals_notified == 0

  Scenario: Tail KEV always fires loud alert
    Given 150 unique CVEs on host X
    And rank 110 is a newly-persisted KEV (Tier 0)
    When _ingest_candidates runs
    Then notify() is called for rank 110
    And n_tail_criticals_notified == 1

  Scenario: Pre-existing tail Critical stays silent (no re-notify)
    Given a Tier 1 CVSS 9.4 CVE already in DB from a previous scan
    And it still ranks in the suppressed tail this scan
    When _ingest_candidates runs
    Then save_finding returns False (dup) for it
    And notify() is NOT called for it
    And n_tail_criticals_notified == 0 for that item

  Scenario: scan_integrity balance invariant holds
    Given any scan on host X
    When _ingest_candidates runs
    Then n_candidates == n_persisted + n_dedup + n_dropped_in_save + n_filtered_other
    And both n_notified and n_tail_criticals_notified are present in the event
```

## Capabilities delivered

- **CAP-T232: Loud alert for newly-persisted Tier ≤ 1 findings
  regardless of rank** (type: technical, status: active)

  *After BC-128 Phase 2 (`_notify_top_n`), hub iterates
  `suppressed` once more and calls `notify()` for any finding where
  `f.id in persisted_set AND f.tier <= 1`. The 0.3 s Telegram rate-
  limit still applies between sends. `scan_integrity` audit event
  grows an `n_tail_criticals_notified` field counting these second-
  pass sends. BC-128's balance invariant is unchanged (both top-N
  and tail notifications are accounting-neutral — persistence
  already covers them).*

Extends CAP-T231 (BC-128 — persistence of every unique CVE),
CAP-T207 (findings CLI surface), CAP-T223 (integrity accounting),
CAP-T224 (POLICIES line — `BEACONRY_MAX_FINDINGS_PER_RUN` now
guaranteed to not silence Tier ≤ 1).

## Open questions

Resolved during drafting (2026-11-16):

1. Should Tier 2 tail items also loud-notify? **No.** Tier 2 has
   a 1-hour SLA; the digest at every scan is acceptable. Tier 0/1
   has a 15-min SLA that a hash-throttled digest can miss.
2. Should the tail notification carry a different prefix
   (`🔔 TAIL CRITICAL …`) to distinguish from top-N? **No.** The
   finding format already carries severity/tier; adding a tail
   marker is operator-cognitive-load for no action change.
3. Should the digest body still list the tail-Critical even after
   loud-notifying it? **Yes.** Digest is a scan summary, not a
   notification queue — removing items from the summary because
   they were also loud-notified would make the digest incoherent
   as a scan artifact.
4. What if `notify(f)` fails for a tail-Critical? Same handling
   as top-N: `notify_failed` audit event, continue the loop. The
   finding is already persisted; operator can check `beaconry
   findings --severity Critical --host <X>`.

## Links

- Direct predecessor: BC-128 (persistence of every unique CVE made
  this gap visible).
- Related: BC-005 (`dedup_and_cap` origin), BC-020 (KEV tier=0),
  BC-022 (digest rendering), BC-036 (digest throttling — the
  reason tail-Criticals need an escape hatch), BC-122 (audit
  balance), BC-123 (persistent integrity counters), BC-030
  (internet_facing detection powers Tier 1).
