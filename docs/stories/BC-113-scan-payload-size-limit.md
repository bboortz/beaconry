---
id: BC-113
title: /scan payload size limit — DoS guard
status: shipped
created: 2026-11-09
branch: feat/BC-113-scan-payload-size-limit
plan: docs/plan/BC-113-scan-payload-size-limit-plan.md
---

# BC-113: Hub rejects over-large SBOM payloads before parsing

## Narrative

A compromised scout (via BC-055 token leak, supply-chain shim, or a
`/test/scan` attacker on BEACONRY_TEST_MODE=1 deployments) POSTs a
hand-crafted 1-million-artifact SBOM to `/scan`. Pre-BC-113: FastAPI's
default limit is ~100MB. The body is loaded into memory, parsed into
a dict, then handed to `receive_scan` — hub's RSS balloons, grype's
per-match memory usage follows, OOM kill lands on the hub container.
Scout `depends_on: service_healthy` then can't post for minutes.

BC-113 adds a Content-Length-based middleware guard at the FastAPI
layer. Declared bodies over `BEACONRY_SCAN_MAX_PAYLOAD_BYTES`
(default 50MB) return 413 before the body is read into memory.
Streaming uploads without a Content-Length header pass through (rare
in practice; chunked transfer is uncommon from Python `requests`).

## Trigger

A POST to `/scan` arrives with `Content-Length` > the configured
limit.

## Expected outcome

- 413 Request Entity Too Large response with JSON body:
  `{"detail": "scan payload too large", "declared_bytes": N, "max_bytes": M}`.
- Body never parsed into memory — hub RSS stays flat.
- Other endpoints (`/healthz`, `/heartbeat`, `/scan/trigger`,
  `/findings`) unaffected regardless of their request size.
- Default 50MB is a comfortable floor: a typical scout post is
  ~1-5MB per container × ~20 containers = ~20MB. Operators with
  bigger fleets bump the env var.

## Out of scope

- Streaming chunk-wise size enforcement (would require reading the
  request body ourselves). The downstream `payload: dict` parser
  would still blow up on a genuine chunked attack, but that failure
  mode is a crash, not a silent memory leak.
- Rate limiting per scout (future story if DoS persists after this).
- Request size limits on other endpoints (their payloads are tiny
  by design — BC-063 heartbeat is <200 bytes; scan-trigger has an
  empty body).

## Acceptance criteria (Gherkin)

```gherkin
Feature: /scan rejects oversize bodies at the HTTP layer

  Scenario: Oversize POST → 413
    Given BEACONRY_SCAN_MAX_PAYLOAD_BYTES is 100
    When a client POSTs /scan with Content-Length: 10000
    Then the response status is 413
    And the body is {detail, declared_bytes, max_bytes}

  Scenario: Small POST passes through
    Given BEACONRY_SCAN_MAX_PAYLOAD_BYTES is 10_000_000
    When scout POSTs a 2MB SBOM
    Then the response status is NOT 413

  Scenario: Other endpoints are unaffected
    Given BEACONRY_SCAN_MAX_PAYLOAD_BYTES is 1 (extreme)
    When a client GETs /healthz
    Then the response status is NOT 413
```

## Capabilities delivered

- **CAP-T215: /scan payload size guard middleware.** Hub rejects
  oversize SBOMs before parsing — DoS surface closed for the known
  attack vector.

## Links

- BC-055 (introduced scout → hub auth; the scout compromise
  threat model BC-113 defends against).
- BC-067 security audit (origin of this item — line 287 of IDEAS.md).
