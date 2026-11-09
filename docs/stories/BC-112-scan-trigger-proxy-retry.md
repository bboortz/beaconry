---
id: BC-112
title: Scan trigger proxy retry — survive transient scout unreachability
status: in-progress
created: 2026-11-09
branch: feat/BC-112-scan-trigger-proxy-retry
plan: docs/plan/BC-112-scan-trigger-proxy-retry-plan.md
---

# BC-112: Hub retries the scan-trigger proxy before 503

## Narrative

Operator runs `make scan` → hub `/scan/trigger` → POST
`http://scout:8001/scan/trigger`. Scout happens to be in the middle
of its own scan and hasn't yet released the event loop to accept new
connections, OR compose DNS resolver returns a momentary SERVFAIL
after a container restart. Pre-BC-112: hub catches the `URLError`
and raises `HTTPException(503, "scout unreachable: ...")`. Operator
has to re-run `make scan` manually.

BC-112 retries the proxy POST up to N times with exponential backoff
(1s/2s/4s default — short, because the operator's HTTP request is
blocked while we retry; capped at ~15s absolute including per-attempt
5s timeouts). HTTP 4xx/5xx from scout are NOT retried — those are
genuine responses (e.g. `409 already_running`) that the operator
should see.

## Trigger

Operator hits `POST /scan/trigger` → hub proxies to scout → transport
failure (DNS / ECONNREFUSED / timeout).

## Expected outcome

- First-attempt success: unchanged — single POST, immediate result.
- First-attempt transport failure + eventual success: retry; response
  JSON gains `retry_attempts: N` so operator sees the retry worked.
- 4xx/5xx from scout: returned immediately, no retry (genuine response).
- All-attempts transport-fail: 503 with `"scout unreachable after N
  attempt(s): <transport_error>"`.

## Out of scope

- Buffered replay (scout already has BC-037 idempotency: a concurrent
  trigger returns 409, not a double scan).
- Retry on 5xx body responses from scout — those are hub-side policy
  decisions scout already made; hub shouldn't second-guess.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Scan trigger proxy retries transient scout unreachability

  Scenario: POST fails once then succeeds
    Given scout briefly unreachable (DNS blip)
    When hub proxies /scan/trigger
    Then the first attempt times out
    And the second attempt succeeds after BASE_S × 2^0 seconds
    And response JSON carries retry_attempts: 2

  Scenario: Backoff doubles each attempt
    Given MAX_ATTEMPTS is 4 and BASE_S is 2
    When all attempts transport-fail
    Then sleep gaps are 2s, 4s, 8s
    And the final response is 503 with transport error class

  Scenario: 4xx/5xx from scout is NOT retried
    Given scout responds 409 (another scan in progress)
    When hub proxies /scan/trigger
    Then the first attempt returns 409
    And no retry is attempted
```

## Capabilities delivered

- **CAP-T214: Hub scan-trigger proxy retry with exponential backoff.**
  Operator-facing resilience against transient compose-DNS blips
  and scout-busy responses.

## Links

- BC-055 (introduced the `/scan/trigger` proxy).
- BC-037 (scout's concurrent-scan guard — 409 response path that
  BC-112 must NOT retry).
