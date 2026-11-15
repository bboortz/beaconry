# BC-122 — Pipeline integrity hashes + runtime verification

*Branch: `feat/BC-122-pipeline-integrity-hashes`*
*Start date: 2026-11-15*
*Story: `docs/stories/BC-122-pipeline-integrity-hashes.md`*

---

## Situation

Current scout → hub pipeline has no machine-checkable evidence that
every SBOM scout generated made it to the hub, and every candidate
finding produced by grype got persisted. Audit log has per-event
records but no "stitch" across them. Silent drops (BC-107 retry
queue broken pre-BC-120, hub filter edge case, body truncation at
network middleware) look identical to healthy cycles.

## Problems to solve

- No per-iteration identity → can't correlate "scout logged N SBOMs
  sent" with "hub logged M candidates received".
- No transfer-integrity check → scout-sent payload could be truncated
  / corrupted and hub would just parse less than intended.
- No processing-drop accounting → `n_candidates` doesn't equal
  `n_persisted` in healthy operation (MIN_CVSS filter + dedupe), so
  "found 10, kept 5" is ambiguous without a reason breakdown.
- No verifier → operator has no single-command answer to "is the
  pipeline running losslessly?".

## Goals

1. Scout computes `iteration_hash` + `transfer_hash` per cycle,
   embeds both in body + logs them (Scenarios 1, 3).
2. Hub verifies `transfer_hash` on receipt, computes `processing_hash`
   after ingest, emits `scan_integrity` audit event with full delta
   breakdown (Scenarios 1, 2, 6).
3. Legacy scouts degrade cleanly — hub records null hashes, verifier
   flags gaps (Scenario 3).
4. `beaconry verify-integrity` walks audit log, reports clean/dirty
   iterations, exits non-zero on any mismatch or unexplained drop
   (Scenarios 4, 5, 6).
5. `/healthz` surfaces monotonic `pipeline_integrity_mismatches`
   counter AND `beaconry status` DETAILS cell renders
   `⚠ integrity: N mismatches` when the counter > 0 — operator
   sees the problem in the first command they run, no need to
   grep logs.

## Scope

- `poc/scout/scout.py`:
  - Add `_compute_iteration_hash(host, ts, image_refs)` +
    `_compute_transfer_hash(body: str)` helpers.
  - `collect_and_post`: compute both, embed in payload, log.
- `poc/hub/hub.py`:
  - On `receive_scan`: recompute `transfer_hash` from the raw body,
    verify against `payload.get("transfer_hash")`, increment
    `_pipeline_integrity_mismatches` on mismatch.
  - After `_ingest_candidates`: compute `processing_hash` over sorted
    persisted finding IDs; emit `scan_integrity` audit event with:
    `iteration_hash, transfer_hash_claimed, transfer_hash_computed,
    transfer_match, n_sboms_received, n_candidates, n_persisted,
    n_filtered_min_cvss, n_dedup, n_filtered_other, processing_hash`.
  - `_scout_healthz_summary` → `_healthz_summary`: add
    `pipeline_integrity_mismatches` key.
  - On mismatch + `BEACONRY_PIPELINE_INTEGRITY_ALERT=1`: fire a
    Telegram alert via existing notifier adapter (one-liner).
- `poc/scripts/status.py`:
  - `_hub_details`: when `checks.pipeline_integrity_mismatches > 0`,
    append `⚠ integrity: N mismatches` to the DETAILS cell so operators
    see the problem the moment they run `beaconry status`. Pattern
    mirrors BC-120's `state_db degraded` surfacing on scout.
- `poc/scripts/verify_integrity.py` (new):
  - CLI reading `audit.log`, filtering `scan_integrity` events.
  - Sum the filter reasons, verify `n_candidates == n_persisted +
    sum(filters)`. If ≠, flag.
  - Exit 0 iff every event has `transfer_match=true` (or null for
    legacy) AND every drop is accounted for.
- `poc/scripts/cli.py` — dispatch `beaconry verify-integrity`.
- `Makefile` — `make verify-integrity` target.
- `poc/.env.example` — `BEACONRY_PIPELINE_INTEGRITY_ALERT=0` line.
- `poc/docker-compose.yml` — new env propagation line.
- `docs/VARIABLES.md` — new row.
- `docs/CAPABILITIES.md` — CAP-T223 entry.
- `poc/tests/unit/test_BC_122_pipeline_integrity.py` — unit tests.
- `poc/tests/integration/test_BC_122_pipeline_integrity.py` — end-to-end.

## Non-goals

- Replace `finding.id` — `finding.id` IS the finding_hash per story
  question 1; no new field.
- SBOM content integrity (syft owns that).
- Signing / HMAC — this is accidental-loss detection, not tamper
  protection.
- Blocking scans on mismatch (fail-open).

## Design decisions

- **Hash width: 16 hex chars** (truncated sha256). Matches BC-005's
  `finding.id` precedent. Collision risk ≈ 2^32 iterations.
- **iteration_hash canonicalization**: `sha256(host + "\n" +
  str(iteration_ts) + "\n" + "\n".join(sorted(image_refs)))`.
  Sorted so two scouts seeing the same set in different order emit
  the same hash (bad for identity; good for cross-scout dedup).
  Combined with `host` + `iteration_ts`, collision across scouts
  requires same-host same-second — acceptable.
- **transfer_hash canonicalization**: hash over `json.dumps(payload,
  sort_keys=True, separators=(",", ":"))` — the canonical body
  (sort_keys fixes key-order drift in dict iteration; separators
  removes whitespace drift). Hash is computed by scout before POST
  and recomputed by hub on the deserialized body. Does NOT hash the
  wire bytes (gzip-compressed body from BC-119 is not stable across
  compression libs); hashes the canonicalized JSON instead, which
  is stable.
- **Fail-open on mismatch**: processing proceeds. Reasoning: a
  transfer corruption is rare; stopping processing would amplify
  blast radius.
- **`pipeline_integrity_mismatches` is in-memory monotonic**: not
  persisted. On hub restart it resets to 0. The audit log is the
  durable record; `/healthz` just surfaces "something bad since
  this process started".
- **Separate verifier CLI** (vs. merging into `beaconry audit`):
  single-purpose, clear exit codes, scriptable.

## Breaking changes

### 1. API / return shape

- **`/scan` POST body** gains three keys (`iteration_hash`,
  `transfer_hash`, `n_sboms`). Hub tolerates absent keys (legacy).
- **Audit log** gains a new event type `scan_integrity`. Existing
  audit consumers (`beaconry audit`, `make soak`) treat unknown
  event types as opaque — confirmed by grep. New events pass through.
- **`/healthz.checks`** gains `pipeline_integrity_mismatches: int`.
  Pure additive; `.get()` consumers tolerate.

### 2. Data model

**None.** No DB schema change — all information lives in audit log
(append-only JSONL) and in-memory counters.

### 3. Behavioral semantics

- **Scout**: adds a deterministic hash compute step per cycle. Cost
  is negligible (one sha256 over the serialized body, < 1ms per MB).
- **Hub**: adds a second hash compute on receipt + an audit write.
  Both are off the hot ingest path.
- **Legacy scouts**: no behavior change; hub records nulls in audit.
- **Operator**: `make status` may show "pipeline_integrity 3
  mismatches" after this ships — that's data surfacing an existing
  silent problem, not a new regression.

## Merge plan

1. All work on `feat/BC-122-pipeline-integrity-hashes`
2. `make check` passes on branch tip
3. PR to `dev`; Claude opens; user runs `/ultrareview`; Claude runs `/code-review`
4. Squash merge
5. Post-merge: BC-122 status → `shipped`, INDEX updated; release workflow bumps to v0.22.6

## Rollout plan

Additive. On deploy:
- BC-122 scouts start sending hashes immediately.
- BC-122 hub starts recording `scan_integrity` audit events.
- Mixed fleet: pre-BC-122 scouts' entries show `transfer_hash_claimed=null`.
- Operator opts into Telegram alerts with
  `BEACONRY_PIPELINE_INTEGRITY_ALERT=1` once baseline false-positive
  rate is known.

## Shipping evidence

Pre-ship audit (DD-TGD step 6a). Rows filled during implementation.

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario 1: Happy path — hashes match end to end | integration | `poc/tests/integration/test_BC_122_pipeline_integrity.py::test_happy_path_transfer_verifies` | ✓ |
| Scenario 2: Transfer corruption — hash mismatch logged | integration | `test_mutated_body_recorded_as_mismatch` | ✓ |
| Scenario 3: Legacy pre-BC-122 scout — graceful degrade | unit + integration | unit `test_hub_degrades_cleanly_on_null_claimed_hash`; integration `test_legacy_body_without_hashes_degrades_cleanly` | ✓ |
| Scenario 4: verify-integrity CLI reports clean fleet | unit | `test_verifier_clean_fleet_exits_zero` | ✓ |
| Scenario 5: verify-integrity CLI flags a mismatch | unit | `test_verifier_mismatch_exits_nonzero` + `test_verifier_flags_unexplained_drops` | ✓ |
| Scenario 6: Processing drops are fully explained | integration | `test_processing_drops_accounted_for` + unit `test_verifier_accounts_every_drop_cleanly` | ✓ |
| Scenario: `beaconry status` surfaces the mismatch count | unit | `test_status_renders_integrity_mismatches` + `test_status_omits_integrity_when_zero` | ✓ |
| Goal 1: scout emits hashes | unit | `test_scout_builds_integrity_payload_section` + stability/determinism suite | ✓ |
| Goal 2: hub verifies + audits | integration | covered by Scenarios 1 + 6 | ✓ |
| Goal 3: legacy degrade | unit + integration | covered by Scenario 3 | ✓ |
| Goal 4: verifier CLI | unit | covered by Scenarios 4 + 5 + render/parse suite | ✓ |
| Goal 5: /healthz + status surfacing | unit | `test_healthz_surfaces_mismatch_counter` + `test_status_renders_integrity_mismatches` | ✓ |
| Additional ask: `beaconry findings` shows how many runs haven't seen a finding | unit | `test_absent_count_column_present` + `test_hub_findings_endpoint_includes_absent_count` + `test_format_table_renders_absent_count` in `test_findings_timestamp_columns.py` | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | N/A | pure additive, no deprecation | ✓ |

Legend: ✓ covered · ⚠️ partial (notes required) · ❌ open (blocks shipping).

## Testing strategy

- Unit tests cover the hashing helpers (deterministic / stable),
  the audit event shape, the legacy degrade path, and the verifier
  CLI logic in isolation.
- One integration test spins a TestClient hub, POSTs a real body
  through it, asserts the audit log has a well-formed
  `scan_integrity` entry.

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_122_pipeline_integrity.py -v
pytest poc/tests/integration/test_BC_122_pipeline_integrity.py -v
beaconry verify-integrity --since 24h    # live stack
```
