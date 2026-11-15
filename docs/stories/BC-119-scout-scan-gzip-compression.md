---
id: BC-119
title: Scout /scan gzip compression — fit large fleets without operator tuning
status: in-progress
created: 2026-11-15
branch: feat/BC-119-scout-scan-gzip-compression
plan: docs/plan/BC-119-scout-scan-gzip-compression-plan.md
---

# BC-119: Scout gzips its /scan POST body

## Narrative

Operator adds the 64th container on bb-82jq. Next scout cycle fires;
SBOM JSON for 64 containers is ~80MB uncompressed. Pre-BC-119 this POST
hits hub's BC-113 50MB payload cap → 413 Content Too Large on every
retry → finding state goes stale → BC-086 absent-reconcile never fires
→ CVEs stuck `open` even after the operator patches them.

Post-BC-119 the same scout gzip-compresses the body (~80MB JSON → ~12MB
compressed because SBOM JSON is highly repetitive), posts with
`Content-Encoding: gzip`. Hub's middleware inspects the header;
`Content-Length` is checked against the compressed size (still under
the 50MB cap by 4×). Hub decompresses in-memory before parsing; the
downstream request handler sees a normal `payload: dict` exactly as
before. Operator does nothing. Fleet works.

## Trigger

Scout's periodic scan loop (`SCAN_INTERVAL`) OR docker-events-triggered
delta scan, where the SBOM body size approaches or exceeds the hub's
`BEACONRY_SCAN_MAX_PAYLOAD_BYTES` cap.

## Expected outcome

- Scout sends `Content-Encoding: gzip` on every /scan POST (not conditionally
  — every POST, so the retry-queue path stays identical and the hub code
  path is simpler).
- Hub decompresses transparently before `receive_scan` parses the body.
- Operators on large fleets never see a 413 from the hub until the
  **compressed** body exceeds the cap (4-6× headroom in practice).
- A scout that predates BC-119 (sends plain JSON) continues to work —
  the hub decodes `Content-Encoding: identity` and absent header as
  "raw body".
- Hub rejects bodies whose Content-Length is already over the cap
  *before* decompression (zip-bomb guard: a malicious attacker can't
  wrap 10GB as a tiny gzip; the cap gates the wire size, same as
  pre-BC-119).
- Hub rejects decompressed bodies whose actual size exceeds a
  configurable `BEACONRY_SCAN_MAX_DECOMPRESSED_BYTES` (default 10×
  wire cap = 500MB) — second zip-bomb layer.

## Out of scope

- Compression on any other endpoint (`/heartbeat`, `/scan/trigger`,
  `/findings`) — those carry tiny payloads.
- Streaming decompression — in-memory is fine; the 500MB decompressed
  cap keeps peak RSS bounded.
- Removing BC-113's cap — still there; BC-119 just lets more scouts fit
  under it.
- Content negotiation. Hub always accepts both forms; scout always
  sends gzip.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Scout gzips /scan POST bodies so large fleets fit under BEACONRY_SCAN_MAX_PAYLOAD_BYTES

  Scenario: Large SBOM compresses under the hub cap
    Given a scout producing an 80MB SBOM JSON (64 containers)
    And a hub with BEACONRY_SCAN_MAX_PAYLOAD_BYTES=52428800 (50MB)
    When scout POSTs /scan with Content-Encoding: gzip
    Then the compressed body is under 15MB
    And the hub accepts the request
    And receive_scan processes the SBOMs exactly as before

  Scenario: Legacy scout without gzip still works
    Given a pre-BC-119 scout posting raw JSON (no Content-Encoding header)
    And the raw body is under the cap
    When the hub receives the POST
    Then the body is parsed directly
    And the response is 200

  Scenario: Zip-bomb rejected pre-decompression
    Given an attacker sending a POST with Content-Length over the cap
    When the middleware runs
    Then the request is rejected with 413 before the body is read
    And no decompression occurs

  Scenario: Zip-bomb rejected post-decompression
    Given a scout sending a gzipped body under the wire cap but decompressing to >500MB
    When the hub decompresses
    Then the request is rejected with 413
    And the hub logs the decompressed_bytes

  Scenario: Compression failure degrades gracefully
    Given scout encounters an unexpected gzip error (should never happen in practice)
    When _post_scan_body runs
    Then it falls back to raw JSON POST
    And logs a warning
    And still succeeds (if the raw body fits)

  Scenario: Scout startup logs compression state
    Given scout starts
    When the first /scan POST is prepared
    Then scout logs "BC-119: /scan POST uses Content-Encoding: gzip"
```

## Capabilities delivered

- **CAP-T221: Scout /scan gzip compression** (type: technical, status: active)
  *Scout wraps `_post_scan_body`'s JSON payload with `gzip.compress(..., compresslevel=6)` and sets `Content-Encoding: gzip`. Hub's BC-113 middleware learned a decompression branch: if `Content-Encoding: gzip`, read body, `gzip.decompress()`, verify decompressed size ≤ `BEACONRY_SCAN_MAX_DECOMPRESSED_BYTES` (default 10× wire cap), hand the decoded bytes to FastAPI's body parser. Falls back to raw JSON POST if gzip import or compress fails.*

Supports CAP-B023 (observability + reliability of the scan path).
Extends CAP-T113 (BC-113's /scan payload size limit) with pre- AND
post-decompression gates.

## Open questions

Resolved during 2-iteration clarification (2026-11-15):

1. Always-gzip vs. conditional? **Always.** Simplifies both sides and keeps the retry-queue payload identical to the first-try payload.
2. Decompression limit? **10× wire cap.** Rejects classic zip-bomb ratios (>1000×) while leaving room for SBOM's genuine ~7× compression ratio.
3. Fallback to raw if gzip breaks? **Yes.** Defense in depth; gzip is in the Python stdlib so this should never fire.
4. Content negotiation / Accept-Encoding? **No.** The hub always accepts both; the scout always sends gzip. No negotiation surface.

## Links

- Related stories: BC-113 (payload cap), BC-107 (retry queue — path is shared)
- Memory: `memory/docker-standard.md` (scout POST format)
