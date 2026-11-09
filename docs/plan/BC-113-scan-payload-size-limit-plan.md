# BC-113 — /scan payload size limit

*Branch: `feat/BC-113-scan-payload-size-limit`*
*Start date: 2026-11-09*
*Story: `docs/stories/BC-113-scan-payload-size-limit.md`*

---

## Situation

`poc/hub/hub.py::receive_scan` accepts `payload: dict` — FastAPI
parses the body into memory before the handler is invoked. No upfront
size check; FastAPI's default limit is ~100MB. A hostile client can
burn hub RAM by posting 100MB of valid-but-huge SBOM JSON.

---

## Problems to solve

- DoS surface: 100MB × grype match-RAM-amplification = OOM on hub.
- Scout `depends_on: service_healthy` blocks downstream after OOM.
- Pre-BC-113 operator has no way to tune the limit without forking.

---

## Goals

1. Oversize POST → 413 before body parsing (maps to Scenario
   "Oversize POST → 413").
2. Small POST unaffected (maps to Scenario "Small POST passes
   through").
3. Other endpoints never trigger the check (maps to Scenario
   "Other endpoints are unaffected").

---

## Scope

- `poc/hub/hub.py`:
  - New env var: `BEACONRY_SCAN_MAX_PAYLOAD_BYTES` (default
    50 MB = 52 428 800).
  - New middleware `_scan_payload_size_middleware` attached via
    `@api.middleware("http")`. Only trips on `request.url.path ==
    "/scan"` AND declared Content-Length > limit.
- `poc/.env.example`, `poc/docker-compose.yml`, `docs/VARIABLES.md`:
  one new env row.
- `docs/CAPABILITIES.md`: CAP-T215.
- Unit tests: `poc/tests/unit/test_BC_113_scan_payload_size.py`.
- `CHANGELOG.md` + `VERSION` → `0.20.6` (patch — security hardening).

---

## Non-goals

- Streaming chunk-wise size enforcement.
- Rate limiting (per-scout, per-IP).
- Same guard on `/heartbeat`, `/scan/trigger`, etc. — their
  payloads are tiny by design.

---

## Design decisions

- **Middleware over route-level dependency**: the middleware runs
  BEFORE FastAPI parses the body. A route-level `Depends(...)` would
  fire after body ingestion — too late.
- **Content-Length inspection only, no streaming check**:
  `Content-Length` is a cheap O(1) header lookup. Streaming would
  require reading the body ourselves, defeating the purpose. The
  downstream `payload: dict` parser is a backstop for chunked
  attacks (crashes with a clear "payload too large" from json
  parser rather than silent OOM).
- **Path-scoped**: `/scan` only. Avoids false-positives on tiny
  endpoints and leaves room for per-endpoint limits later if
  needed.
- **Default 50MB**: typical scout post is 1-5MB/container × ~20
  containers = 20MB. 50MB gives 2.5× headroom for operators with
  bigger fleets.

---

## Breaking changes

### 1. API / return shape

Additive: new 413 response shape for oversize `/scan` POSTs. CLI
clients that didn't previously handle 413 see the error surface for
the first time, but this is strict improvement over OOM.

### 2. Data model

None.

### 3. Behavioral semantics

- Pre-BC-113: 100MB SBOMs accepted + processed (eventually crash
  via OOM).
- Post-BC-113: 50MB+ SBOMs rejected at middleware.
- Operators running huge fleets can bump
  `BEACONRY_SCAN_MAX_PAYLOAD_BYTES`.

---

## Merge plan

1. All work on `feat/BC-113-scan-payload-size-limit`.
2. `make check` passes.
3. PR to `dev`, squash merge.
4. Post-merge: INDEX + story → shipped.

---

## Rollout plan

Additive. Zero operator action unless real SBOMs happen to exceed
50MB (very unlikely at POC scale).

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario "Oversize POST → 413" | unit | `poc/tests/unit/test_BC_113_scan_payload_size.py::test_scan_rejects_oversize_via_content_length` | ✓ |
| Scenario "Small POST passes through" | unit | `test_scan_accepts_small_payload` | ✓ |
| Scenario "Other endpoints are unaffected" | unit | `test_other_endpoints_not_gated` | ✓ |
| Streaming without Content-Length passes | unit | `test_scan_without_content_length_passes_through` | ✓ |
| Malformed Content-Length header handled | unit | `test_malformed_content_length_header_passes_through` | ✓ |
| Env parsing (default + override + invalid) | unit | `test_env_defaults_to_50mb`, `test_env_override`, `test_env_invalid_falls_back` | ✓ |
| Memory drift pattern added | N/A | N/A — no documented behavior deprecated | ✓ |

---

## Testing strategy

Unit tests use `fastapi.testclient.TestClient` to hit the middleware
end-to-end. Content-Length is set explicitly via httpx `content=`
raw-body form so the middleware's path is exercised.

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_113_scan_payload_size.py -v
```
