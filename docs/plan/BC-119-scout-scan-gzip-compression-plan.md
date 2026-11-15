# BC-119 — Scout /scan gzip compression

*Branch: `feat/BC-119-scout-scan-gzip-compression`*
*Start date: 2026-11-15*
*Story: `docs/stories/BC-119-scout-scan-gzip-compression.md`*

---

## Situation

Hub's `/scan` endpoint has had a wire-size cap since BC-113:
`BEACONRY_SCAN_MAX_PAYLOAD_BYTES` (default 50MB). On 2026-11-15
bb-82jq's 64-container SBOM grew to ~80MB raw JSON. Every scout POST
413'd for 30+ min; the only truly open beaconry finding
(`GO-2026-6888` brotli 9.8) stayed `open` because no scan arrived to
drive BC-086's absent-reconcile closure.

BC-107's retry queue couldn't help because (separately) the scout's
`/data/scout-state.db` was unwritable, so queued payloads silently
dropped. BC-120 fixes that; BC-119 fixes the root payload-growth
problem so operators don't have to tune the cap as their fleet grows.

## Problems to solve

- Scout POSTs raw JSON. For 64+ containers, each POST exceeds the
  wire cap by 2-3×. Operators have to tune `BEACONRY_SCAN_MAX_PAYLOAD_BYTES`
  per-deploy.
- SBOM JSON is highly repetitive (same PURL schema, same severity
  strings, long package names) — compresses 6-10× with gzip.
- Hub currently refuses any `Content-Encoding: gzip` — body would
  reach FastAPI's `payload: dict` as gzipped bytes and fail JSON parse.

## Goals

1. Scout compresses every /scan POST body with gzip and sets
   `Content-Encoding: gzip` (CAP-T221). Maps to Scenarios 1 + 6.
2. Hub middleware decompresses `Content-Encoding: gzip` bodies in
   place before BC-113's size check runs on the decompressed body
   (second cap). Legacy identity-encoded bodies still work. Maps to
   Scenarios 1 + 2.
3. Zip-bomb defenses: pre-decompression wire-size check (BC-113's
   existing cap) + post-decompression decoded-size check (new
   `BEACONRY_SCAN_MAX_DECOMPRESSED_BYTES`, default 10× wire cap).
   Maps to Scenarios 3 + 4.
4. Graceful fallback: if gzip.compress fails for any reason, scout
   logs and POSTs raw. Maps to Scenario 5.

## Scope

**Files touched**:

- `poc/scout/scout.py`
  - `_post_scan_body()` — wrap `body` in `gzip.compress(..., compresslevel=6)`
  - Add `Content-Encoding: gzip` to headers
  - Try/except around compression → fall back to raw POST on error
  - Log first-time startup line "/scan POST uses Content-Encoding: gzip"
- `poc/hub/hub.py`
  - New `SCAN_MAX_DECOMPRESSED_BYTES` constant (default `10 * SCAN_MAX_PAYLOAD_BYTES`)
  - Modify `_scan_payload_size_middleware` to also handle gzipped bodies:
    - If `Content-Encoding: gzip`: read body, `gzip.decompress()`,
      check `len(decoded) <= SCAN_MAX_DECOMPRESSED_BYTES`, replace
      body with decoded bytes so downstream parser sees plain JSON
  - FastAPI body replacement via custom `Request._body` or rewriting the
    `receive()` ASGI callable to emit decompressed bytes
- `poc/.env.example` — document `BEACONRY_SCAN_MAX_DECOMPRESSED_BYTES`
- `poc/docker-compose.yml` — new env propagation line
- `poc/tests/integration/test_BC_119_scout_scan_gzip_compression.py`
  — red integration test: large SBOM gzipped → accepted; raw → 413
- `poc/tests/unit/test_BC_119_scout_scan_gzip_compression.py`
  — unit tests on compress/decompress path, decompression cap,
  fallback path, legacy no-header path
- `docs/CAPABILITIES.md` — add CAP-T221 entry

## Non-goals

- Streaming decompression. Keep in-memory for simplicity; 500MB RSS
  burst is acceptable for POC scale.
- Compressing responses. Hub→scout responses are tiny.
- Removing BC-113. The wire cap is still the first defense.
- Content-Encoding negotiation. Scout always gzips; hub always
  accepts both.

## Design decisions

- **Always-gzip** (vs. negotiated / conditional): single code path,
  identical retry-queue payloads, no size-threshold tuning. Rejected
  "only compress if raw > N" because the threshold itself is a tuning
  knob we're trying to eliminate.
- **gzip stdlib, level 6**: Python's default level 6 gives ~4-10× on
  JSON with ~2ms CPU per MB — fine for SBOM scale. Rejected `zlib` /
  `brotli`: no wins at this scale, added deps.
- **Decompressed-size cap = 10× wire cap**: rejects zip-bombs
  (ratios routinely > 1000×) while leaving generous headroom for
  SBOM's observed ~7× ratio. Operator can lower (tighter guard) or
  raise (very large fleets) via env.
- **Fallback to raw on gzip error**: `gzip.compress` can't realistically
  fail on well-formed `str.encode()` input, but having the fallback
  means no deploy-time risk of a gzip import-shadowing breaking the
  scan pipeline. Belt-and-braces, cost one `try`/`except`.
- **Middleware modifies ASGI `receive`**: FastAPI's `request.body()`
  is cached in `_body`; overwriting after `await request.body()`
  works but is fragile. Cleaner: wrap `scope["receive"]` so when
  FastAPI calls `receive()` it gets decompressed bytes. Documented
  inline.

## Breaking changes

### 1. API / return shape

**None.** The scout sends `Content-Encoding: gzip` — a standards-track
HTTP header, transparent to any HTTP client. The hub's `receive_scan`
sees an identical `payload: dict` either way. External tooling that
posts to /scan without gzip (unlikely — only scout does) keeps working
because the hub treats absent Content-Encoding as identity.

### 2. Data model (schema + legacy rows)

**None.** No DB schema change; no new column; no finding-shape change.
`EXPECTED_SCHEMA_VERSION` stays at 11.

### 3. Behavioral semantics

- **Scouts predating BC-119 send raw JSON.** Fine — hub handles
  identity-encoded bodies exactly as before. No noisier or quieter
  notifications. Operators with mixed-version fleets see no change.
- **Scouts on BC-119 send gzipped JSON.** Hub logs compression
  decoded-size at DEBUG, visible in `make live-rebuild` output. Opt-out
  not needed: a hub with a tight `BEACONRY_SCAN_MAX_DECOMPRESSED_BYTES`
  is the only knob, and defaults keep legacy fleets working unchanged.

## Merge plan

1. All work on `feat/BC-119-scout-scan-gzip-compression`
2. `make check` passes on branch tip
3. PR to `dev`; Claude opens; user runs `/ultrareview`; Claude runs `/code-review`
4. Squash merge
5. Post-merge: BC-118 status flipped shipped (done in this branch);
   BC-119 status → `shipped`, INDEX updated

## Rollout plan

Additive. On deploy of a BC-119 hub:
- Pre-BC-119 scouts keep working (identity path).
- BC-119 scouts start sending gzip immediately; hub decodes.
- No migration; no operator action.

## Shipping evidence

Pre-ship audit (DD-TGD step 6a). This story may only move to `shipped`
when every row below has non-empty evidence.

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario 1: Large SBOM gzipped → hub accepts under 50MB cap | unit (TestClient) | `poc/tests/unit/test_BC_119_scout_scan_gzip_compression.py::test_large_sbom_gzipped_accepted` | ✓ |
| Scenario 2: Legacy scout without gzip still works | unit | `poc/tests/unit/test_BC_119_scout_scan_gzip_compression.py::test_identity_encoded_body_still_parses` | ✓ |
| Scenario 3: Zip-bomb rejected pre-decompression (wire cap) | unit | `poc/tests/unit/test_BC_119_scout_scan_gzip_compression.py::test_oversized_content_length_rejected_before_decompress` | ✓ |
| Scenario 4: Zip-bomb rejected post-decompression | unit | `poc/tests/unit/test_BC_119_scout_scan_gzip_compression.py::test_decompressed_over_cap_rejected` | ✓ |
| Scenario 5: Compression failure falls back to raw | unit | `poc/tests/unit/test_BC_119_scout_scan_gzip_compression.py::test_gzip_fallback_to_raw_on_compress_error` | ✓ |
| Scenario 6: Scout startup logs compression state | unit + live | `poc/tests/unit/test_BC_119_scout_scan_gzip_compression.py::test_scout_logs_compression_enabled` + live `docker logs beaconry-dev-scout` on 2026-11-15 shows `BC-119: /scan POST uses Content-Encoding: gzip` | ✓ |
| Scenario: malformed gzip body → 400 (defense in depth, not in Gherkin but implemented) | unit | `poc/tests/unit/test_BC_119_scout_scan_gzip_compression.py::test_malformed_gzip_body_rejected` | ✓ |
| Goal 1: Scout always sends `Content-Encoding: gzip` | unit | `poc/tests/unit/test_BC_119_scout_scan_gzip_compression.py::test_post_scan_body_sets_gzip_header` | ✓ |
| Goal 2: Hub middleware decompresses gzip bodies | unit | `poc/tests/unit/test_BC_119_scout_scan_gzip_compression.py::test_middleware_decompresses_gzip_body` | ✓ |
| Goal 3: Double-cap (wire + decoded) | unit | covered by Scenarios 3 + 4 | ✓ |
| Goal 4: Graceful fallback | unit | covered by Scenario 5 | ✓ |
| Env default resolution: `SCAN_MAX_DECOMPRESSED_BYTES = 10 × wire cap` | unit | `test_env_defaults_decompressed_cap_to_10x_wire` + `test_env_decompressed_cap_override` | ✓ |
| Three-way env docs sync (VARIABLES.md ↔ .env.example ↔ docker-compose.yml) | unit | `poc/tests/unit/test_verify_variables_documented.py::test_shipped_repo_passes` (passes with the new row) | ✓ |
| Live-stack propagation of `BEACONRY_SCAN_MAX_DECOMPRESSED_BYTES` | live | `docker inspect beaconry-dev-hub` on 2026-11-15 shows `BEACONRY_SCAN_MAX_DECOMPRESSED_BYTES=524288000` | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | N/A | no documented behavior deprecated — pure additive | N/A |

Legend: ✓ covered · ⚠️ partial (notes required) · ❌ open (blocks shipping).

## Testing strategy

- Red integration test: build a real ~60MB JSON payload, gzip it,
  POST through TestClient to a live `api` with
  `BEACONRY_SCAN_MAX_PAYLOAD_BYTES=52428800`. Expect 200 (not 413).
- Second integration run: POST the same 60MB raw. Expect 413.
- Unit tests cover the four Gherkin scenarios + the three internal
  guards (header set, decompress path, fallback path).

## Testing commands

```bash
make check
pytest poc/tests/integration/test_BC_119_scout_scan_gzip_compression.py -v
pytest poc/tests/unit/test_BC_119_scout_scan_gzip_compression.py -v
```
