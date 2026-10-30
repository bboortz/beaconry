# BC-020 — KEV ingestion + wire urgency.classify_tier into the hub ingestion path

*Branch: `feat/BC-020-kev-ingest`*
*Start date: 2026-10-31*
*Story: `docs/stories/BC-020-kev-ingest.md`*

---

## Situation

`memory/design-decisions.md` declares a four-tier urgency ladder with Tier 0 (CISA KEV) at the top: "15-min target, auto-fix if safe class." The product pitch is "exploits in the wild in minutes, not mailing lists."

Grepping the implementation shows the ladder is **dead code with passing tests**:
- `poc/hub/urgency.py::classify_tier(f, *, kev, internet_facing)` → returns 0/1/2/3. Unit-tested.
- `poc/tests/unit/test_urgency.py` covers the full matrix.
- `grep -rn classify_tier poc/hub/` returns zero call sites outside the module.
- Every finding is stored with the default `tier=3`.
- Hub never fetches the CISA KEV catalog.
- `notify()` has no tier-aware branching.
- `make findings` has no KEV column.

Operators are told to treat beaconry as a KEV-driven urgency tool but the implementation does not differentiate KEV findings from CVSS findings. The core product-value promise is unshipped.

## Problems to solve

- CISA KEV catalog not consumed → cannot score Tier 0.
- `classify_tier` not wired into ingestion → every finding is Tier 3 regardless of actual urgency.
- Notification format is tier-agnostic → operator cannot tell KEV from non-KEV at a glance.
- `make findings` is tier-agnostic → operator browsing the DB sees no urgency signal.
- No `/healthz` surfacing of KEV cache freshness → degraded urgency scoring is silent.

## Goals

Each maps 1:1 to a Gherkin scenario in the story.

1. Fetch + cache CISA KEV catalog on boot; refresh hourly. (Scenario 1)
2. `process_sbom_entry` calls `classify_tier(f, kev=kev.is_known_exploited(f.cve_id), internet_facing=False)` and stores the result + a boolean `kev` column. (Scenarios 2, 3)
3. `format_notification(f)` prepends a `🚨 KEV — EXPLOITED IN THE WILD` header when `f.tier == 0`. (Scenario 4)
4. KEV fetch failure is non-fatal — stale cache survives; `/healthz` surfaces freshness. (Scenarios 5, 7)
5. `make findings` surfaces a `KEV` column and sorts by `(tier, -cvss)`. (Scenario 6)

## Scope

- **New:** `poc/hub/kev.py` — fetch + cache + lookup + /healthz-shape helpers
- **New:** `poc/tests/unit/test_kev.py` — fetch, parse, cache, stale, lookup
- **New:** `poc/tests/integration/test_BC_020_kev_ingest.py` — Gherkin encoding, red integration test with mocked HTTP + file cache
- **Modified:** `poc/hub/hub.py`:
  - DB schema: add `kev INTEGER DEFAULT 0` column + idempotent migration
  - `lifespan` starts a `_kev_refresh_task` that calls `kev.fetch_or_fallback` on boot + every 3600s
  - `process_sbom_entry` passes `tier` and `kev` into Finding construction via `classify_tier`
  - `format_notification` branches on `f.tier == 0`
  - `/healthz` summary includes `kev` + `kev_last_fetch_s` + `kev_count`
  - `save_finding` / `load_finding` carry the new `kev` column
- **Modified:** `poc/hub/executor/base.py` — add `kev: bool = False` to Finding dataclass
- **Modified:** `poc/scripts/findings.py` — add KEV column, sort by `(tier, -cvss)`
- **Modified:** `poc/scripts/verify_compose_env_propagation.py` — add `BEACONRY_KEV_FEED_URL` (optional override) and `BEACONRY_KEV_CACHE_PATH` to the allowlist OR propagate them explicitly (prefer explicit — operator should be able to override feed URL in air-gapped setups)
- **Modified:** `poc/docker-compose.yml` — propagate the two env vars with sensible defaults
- **Modified:** `docs/CAPABILITIES.md` — register CAP-B019, CAP-T058, CAP-T059, CAP-T060
- **Modified:** `docs/stories/INDEX.md` — BC-020 row
- **Modified:** `memory/design-decisions.md` — flag `## Urgency tiers` as "implemented in BC-020"
- **Modified:** `README.md` — one-line mention of KEV ingestion if help/README drift lint demands

## Non-goals

- `internet_facing` detection (needs port enumeration — separate story).
- Auto-fix on safe class (needs trust-model gate — BC-021).
- Different notification cadence per tier (SLA/batching — separate story).
- OSV, EPSS, vendor advisories — V1 is CISA KEV only.
- `knownRansomwareCampaignUse` dimension (ingested but not used in V1).
- Prometheus metrics for KEV ingest latency — maybe later.

## Design decisions

- **Feed source.** CISA JSON at `https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json`. Rejected: GitHub mirror (unofficial), vendor advisories (future).
- **Fetch client.** `urllib.request` from stdlib. Rejected: `httpx` (adds dep weight for a 2MB one-shot GET per hour), `requests` (ditto).
- **Cache format.** JSON file at `${DATA_DIR}/kev_cache.json`: `{"fetched_at": "2026-10-31T12:00:00Z", "catalog_version": "...", "cve_ids": ["CVE-X", ...]}`. Rejected: SQLite table (overkill for a flat ~1500-entry set), in-memory only (loses state across hub restarts, cold-start blind window).
- **Refresh cadence.** Boot + every 3600s. CISA updates daily; hourly polling is cheap and absorbs mid-day catalog adds. Rejected: on-demand per-scan (adds latency, N×scans HTTP load), daily cron (misses sub-day adds).
- **Startup posture.** Fetch runs in a background asyncio task spawned by `lifespan` — does NOT gate `/readyz`. `/startupz` stays bound to grype DB readiness. Rationale: KEV fetch failure must not block scans; stale is better than down. `/healthz` surfaces freshness so operators can see the degradation.
- **Finding.kev storage.** Boolean column on `findings` table. Rationale: `make findings` needs it for the KEV column without re-querying the cache per row.
- **Notification tier branching.** `format_notification` reads `f.tier` directly. Rationale: tier is already computed at ingestion time; re-deriving at notify-time would risk clock drift between cache snapshots.
- **Fetch failure semantics.** Non-fatal: log at WARNING, `/healthz` kev goes `stale` → `unfetched` after boot without ever succeeding. Scans continue. Rationale: operator receiving "notification worked but KEV scoring is stale" is strictly better than silence or crash.
- **Finding dedup under tier changes.** Dedup key stays `(host, cve_id, source_ref, package)`. If an existing finding's CVE enters the KEV catalog on refresh, the next scan recomputes tier → DB row updates on `INSERT OR REPLACE`. Rationale: no re-notify-on-tier-change in V1 (would be chatty); operator sees the new tier next time they look.

## Breaking changes

- `Finding` dataclass gains `kev: bool = False`. Default-false, backward-compat.
- DB schema gains `kev INTEGER DEFAULT 0` column. Idempotent migration added.
- No API, CLI, or compose breaking changes. New env vars are additive with defaults.

## Merge plan

1. Branch `feat/BC-020-kev-ingest`
2. Red integration test → red (fetch mocked, assert tier=0 for KEV cve, assert header line, assert findings CLI column)
3. Impl in order: `kev.py` → Finding.kev field → DB migration → `process_sbom_entry` wiring → `format_notification` tier branch → `/healthz` KEV summary → `findings.py` column + sort
4. Unit tests for `kev.py` + updated `hub_helpers` + `findings` CLI
5. Register CAPs; INDEX; design-decisions annotation
6. `make check` green (includes BC-019's evidence-pointer lint — ensure plan evidence is honest)
7. Squash fast-forward to `dev`

## Rollout plan

Additive. First boot after merge:
- DB migration auto-adds `kev` column (idempotent)
- Lifespan spawns KEV fetch; cache populates within ~2s
- Next scan tier-scores correctly
- Operator may notice their first Tier 0 notification visually distinct; no action required

## Shipping evidence

Pre-ship audit (DD-TGD step 6a). Story may only move `shipped` when every row is ✓.

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario 1 — KEV fetch on boot populates cache | integration test | `test_BC_020_kev_ingest.py::test_boot_fetch_populates_cache` | ✓ |
| Scenario 2 — KEV CVE gets tier 0 | integration test | `test_BC_020_kev_ingest.py::test_kev_cve_classified_tier_0` | ✓ |
| Scenario 3 — Non-KEV CVE falls through to CVSS tier | integration test | `test_BC_020_kev_ingest.py::test_non_kev_cve_falls_through_to_cvss_tier` | ✓ |
| Scenario 4 — Tier 0 notification has KEV header | integration test | `test_BC_020_kev_ingest.py::test_tier_0_notification_has_kev_header` | ✓ |
| Scenario 5 — Fetch failure → stale cache survives | integration test | `test_BC_020_kev_ingest.py::test_fetch_failure_falls_back_to_cache` | ✓ |
| Scenario 6 — `make findings` surfaces KEV column + tier-first sort | integration test | `test_BC_020_kev_ingest.py::test_findings_cli_surfaces_kev_and_sorts_tier_first` | ✓ |
| Scenario 7 — Cold boot, no cache, fetch fails → tier=3, no crash | integration test | `test_BC_020_kev_ingest.py::test_cold_boot_fetch_fail_graceful` | ✓ |
| KEV JSON parser handles real-world CISA shape | unit test | `test_kev.py::test_parse_cisa_feed_shape` | ✓ |
| KEV cache round-trips to disk | unit test | `test_kev.py::test_cache_round_trip` | ✓ |
| KEV lookup is O(1) and case-insensitive on CVE id | unit test | `test_kev.py::test_lookup_is_fast_and_case_insensitive` | ✓ |
| Stale cache (>25h old) reports `stale`; fresh reports `ok`; unfetched reports `unfetched` | unit test | `test_kev.py::test_freshness_states` | ✓ |
| Finding dataclass carries `kev: bool` + default False | unit test | `test_hub_helpers.py::test_finding_has_kev_field_default_false` | ✓ |
| DB migration idempotently adds `kev` column | unit test | `test_hub_helpers.py::test_init_db_adds_kev_column_idempotent` | ✓ |
| `make findings` with no --status default surfaces Tier 0 KEV row at top | integration test | `test_BC_020_kev_ingest.py::test_findings_cli_surfaces_kev_and_sorts_tier_first` + `test_BC_007_findings_cli.py::test_long_fields_truncate_with_ellipsis` (line-width accommodates KEV col) | ✓ |
| `/healthz` summary includes `kev`, `kev_last_fetch_s`, `kev_count` | unit test | `test_hub_health.py` (updated) | ✓ |
| CAP-B019, CAP-T058, CAP-T059, CAP-T060 registered + bidirectional | verify-traceability | CAPABILITIES.md diff | ✓ |
| make check (fast tier) green including BC-019 evidence-pointer lint | multi-gate | `make check` output | ✓ |
| memory/design-decisions.md § Urgency tiers annotated as implemented | memory diff | `grep "implemented in BC-020"` | ✓ |
| Memory drift pattern added for pre-BC-020 "tier always 3" claims | TOML diff | `poc/scripts/memory_drift_bans.toml` | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open.

## Testing strategy

- **Integration test** (`test_BC_020_kev_ingest.py`) encodes all 7 Gherkin scenarios. Monkeypatches `kev._http_fetch` so the test has deterministic feed contents + simulates fetch failure. Uses `tmp_path` for cache + DB.
- **Unit tests** (`test_kev.py`) exercise fetcher parsing, cache file I/O, lookup performance, freshness-state transitions.
- **Hub helpers updates** (`test_hub_helpers.py`) assert the DB migration, Finding dataclass, and notification header-prepend logic.
- **CLI test updates** (`test_findings_cli.py`) assert KEV column + sort order.

## Testing commands

```bash
source .venv/bin/activate
pytest poc/tests/unit/test_kev.py -v --no-cov
pytest poc/tests/integration/test_BC_020_kev_ingest.py -v --no-cov
make check
```
