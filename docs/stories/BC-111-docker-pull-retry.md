---
id: BC-111
title: DockerAdapter pull retry — survive transient registry failures
status: shipped
created: 2026-11-09
branch: feat/BC-111-docker-pull-retry
plan: docs/plan/BC-111-docker-pull-retry-plan.md
---

# BC-111: Fix-now survives transient registry hiccups

## Narrative

Operator taps [🔧 Fix now] on a `CVE-2026-xxxx / nginx:alpine`
finding at 09:14. Docker Hub is in the middle of a brief 503 spike
(not uncommon under heavy global load). Pre-BC-111: the `docker pull
nginx:alpine` subprocess exits non-zero after ~5s, hub returns
`ExecutionResult(success=False, message="docker pull failed: ...")`,
Telegram shows `❌ Fix failed`, operator has to tap again manually.
The retry would almost always work on the second try.

BC-111 wraps the `docker pull` in a retry loop with exponential
backoff (`BASE_S × 2^(attempt-1)` — defaults 5s/10s/20s for 3
attempts). The common registry hiccup clears on retry; genuine
failures (auth missing, tag not found) still bubble up with the
same message shape as pre-BC-111.

## Trigger

Operator taps [🔧 Fix now] → `DockerAdapter.execute` runs the pull.

## Expected outcome

- First-attempt success: unchanged — single `docker pull`, immediate
  result.
- First-attempt failure + eventual success: retry transparently;
  success message gains `(succeeded on attempt N/M)` suffix.
- All-attempts fail: `ExecutionResult(success=False, ...)` with the
  LAST stderr tail — same wire shape as pre-BC-111.

## Out of scope

- Classifying errors (auth vs network vs tag-not-found). We retry
  blindly. Reasoning: parsing stderr strings is brittle (varies per
  registry) and the worst case for retrying auth-fail is 2× latency
  on the final failure, which is acceptable.
- Rollback-tag retry — the `docker tag` step is local; its warning
  is already non-fatal and logged.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Fix-now retries transient docker pull failures

  Scenario: Pull fails once then succeeds
    Given a transient registry 503
    When operator taps Fix now
    Then the first attempt fails
    And the second attempt succeeds after BASE_S × 2^0 seconds
    And the message reads "Pulled <tag> (succeeded on attempt 2/3)"

  Scenario: Backoff doubles each attempt
    Given MAX_ATTEMPTS is 4 and BASE_S is 2
    When all attempts fail
    Then sleep gaps are 2s, 4s, 8s

  Scenario: All attempts fail — same shape as pre-BC-111
    Given the tag is not found in any registry
    When operator taps Fix now
    Then ExecutionResult.success is False
    And the message contains the registry's stderr tail
```

## Capabilities delivered

- **CAP-T213: DockerAdapter pull retry with exponential backoff.**
  Reduces Fix-now false-failure rate on transient registry hiccups.

## Links

- BC-014 (same-track pull — the pull target tag resolution).
- BC-110 (sibling retry pattern — verify probe).
