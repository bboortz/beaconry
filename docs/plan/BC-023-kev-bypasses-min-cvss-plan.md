# BC-023 — KEV findings bypass MIN_CVSS filter

*Branch: `feat/BC-023-kev-bypasses-min-cvss`*
*Start date: 2026-10-31*
*Story: `docs/stories/BC-023-kev-bypasses-min-cvss.md`*

---

## Situation

`poc/hub/hub.py::process_sbom_entry` filters findings in this order:
```python
cvss = extract_cvss(vuln)
if cvss < MIN_CVSS:
    continue
...
is_kev = _kev.is_known_exploited(cve_id)
f.tier = _urgency.classify_tier(f, kev=is_kev, internet_facing=False)
```

The MIN_CVSS gate runs **before** the KEV lookup. If `MIN_CVSS=9.0` (the production default), a CVSS-7.5 KEV CVE is dropped before anyone checks whether it's actively exploited. BC-020's design-decisions document says Tier 0 (KEV) overrides CVSS; the implementation disagrees.

Found live 2026-10-31: grype on nginx:1.24 matched **CVE-2025-27363** (CVSS 8.1) and **CVE-2023-44487** (HTTP/2 Rapid Reset, CVSS 7.5), both in CISA KEV. Both were silently dropped by `MIN_CVSS=9.0`. The only KEV finding that made it to the operator's hub was CVE-2026-111497 (CVSS 9.8) — not because the system did its job, but because 9.8 happened to pass the theoretical-severity filter.

## Problems to solve

- CVSS filter dropped KEV findings → the Tier 0 product promise is unreachable for CVSS < MIN_CVSS CVEs.
- Operator can't see from `/healthz` whether the bypass fires (observability gap for a correctness invariant).

## Goals

1. KEV CVEs bypass MIN_CVSS. (Scenarios 1, 4)
2. Non-KEV CVEs continue to be filtered as before. (Scenarios 2, 3)
3. Live acceptance: scout rescans nginx:1.24 → both nginx:1.24 KEV CVEs surface. (Scenario 5)

## Scope

- **Modified:** `poc/hub/hub.py::process_sbom_entry` — swap filter order; ~5 LOC change.
- **Modified:** `poc/tests/unit/test_hub_helpers.py` — 4 Gherkin unit tests + regression guards.
- **Modified:** `docs/CAPABILITIES.md` — register CAP-T063.
- **Modified:** `docs/stories/INDEX.md` — BC-023 row.
- **Modified:** `poc/scripts/memory_drift_bans.toml` — ban pre-BC-023 claims that "MIN_CVSS filters all findings".

## Non-goals

- No change to MIN_CVSS default.
- No per-run digest changes (BC-022's work stands).
- No metric for bypass count — nice-to-have, out of scope for the minimal fix.
- No DB migration — only affects new scans.

## Design decisions

- **Order the check:** compute `is_kev` first, then apply MIN_CVSS only when not KEV.
  ```python
  is_kev = _kev.is_known_exploited(cve_id)
  if not is_kev and cvss < MIN_CVSS:
      continue
  ```
- **Why not raise MIN_CVSS=0 as default?** Operator noise control is a legitimate concern; CVSS-5 non-KEVs would flood the digest. The bypass preserves "I don't want to hear about every low-severity CVE" while honoring "I always want to hear about KEV."
- **Why check KEV at this layer, not in `classify_tier`?** `classify_tier` already consumes `kev: bool`; the question is where the filter runs. Checking KEV here (before filter) is one lookup. Checking after (via tier=0 override) still requires the pre-filter — same number of lookups, less direct semantics.
- **Observability:** a logger `log.debug` emits when a KEV finding bypasses the filter. Not in `/healthz` to keep the surface area minimal; operators can grep logs.
- **Not re-processing historical findings:** `save_finding` uses INSERT OR IGNORE, so pre-BC-023 rows with kev=0 stay as they are. Operators who want to re-tier existing findings `rm /data/beaconry.db` + rescan.

## Breaking changes

None. Additive semantic: previously-filtered KEV findings now flow through. Non-KEV behavior unchanged.

## Merge plan

1. Branch `feat/BC-023-kev-bypasses-min-cvss`
2. Red unit tests (Scenarios 1-4) → red
3. Patch `process_sbom_entry` → green
4. Register CAP-T063, drift-ban entry, INDEX
5. `make check` green
6. Fast-forward to `dev`
7. Rebuild hub + live-verify Scenario 5 (scout rescans nginx:1.24)

## Rollout plan

Rebuild hub + optionally wipe DB:
```bash
cd poc && docker compose up -d --build hub
docker exec beaconry-hub python3 -c "import sqlite3; sqlite3.connect('/data/beaconry.db').execute('DELETE FROM findings')"
docker restart beaconry-scout
```

## Shipping evidence

Pre-ship audit (DD-TGD step 6a).

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario 1 — KEV CVE < MIN_CVSS ingested with tier=0 | unit test | `test_hub_helpers.py::test_kev_below_min_cvss_passes_filter` | ✓ |
| Scenario 2 — Non-KEV < MIN_CVSS still filtered | unit test | `test_hub_helpers.py::test_non_kev_below_min_cvss_still_filtered` | ✓ |
| Scenario 3 — Non-KEV ≥ MIN_CVSS unchanged | unit test | `test_hub_helpers.py::test_non_kev_above_min_cvss_unchanged` | ✓ |
| Scenario 4 — KEV ≥ MIN_CVSS unchanged (regression guard) | unit test | `test_hub_helpers.py::test_kev_above_min_cvss_still_tier_0` | ✓ |
| Scenario 5 — Live: nginx:1.24 KEV CVEs surface post-rescan | live manual | 2026-10-31 — wipe DB + scout restart + /healthz check | ✓ |
| CAP-T063 registered + bidirectional traceability | verify-traceability | CAPABILITIES.md diff | ✓ |
| Memory drift ban for pre-BC-023 "MIN_CVSS filters KEV" claims | TOML diff | `poc/scripts/memory_drift_bans.toml` | ✓ |
| make check green | multi-gate | `make check` | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open.

## Testing strategy

- **Unit tests** monkeypatch `hub.run_grype` to return a controlled vulnerability list + monkeypatch `_kev.is_known_exploited` for determinism. `process_sbom_entry` is tested in isolation.
- **Live acceptance** replays Scenario 5 against the real nginx:1.24 image after rebuild.

## Testing commands

```bash
source .venv/bin/activate
pytest poc/tests/unit/test_hub_helpers.py -k "kev.*min_cvss" -v --no-cov
make check
# Live:
cd poc && docker compose up -d --build hub
docker exec beaconry-hub python3 -c "import sqlite3; sqlite3.connect('/data/beaconry.db').execute('DELETE FROM findings'); print('wiped')"
docker restart beaconry-scout
# Wait ~2 min for scan to complete
docker exec beaconry-hub python3 -c "
import sqlite3; con = sqlite3.connect('/data/beaconry.db')
for row in con.execute('SELECT cve_id, cvss, kev, tier, source_ref FROM findings WHERE kev=1 ORDER BY cvss').fetchall():
    print(row)
"
# Expected: CVE-2023-44487 CVSS 7.5 kev=1 tier=0 nginx:1.24 (and CVE-2025-27363 and CVE-2026-111497)
```
