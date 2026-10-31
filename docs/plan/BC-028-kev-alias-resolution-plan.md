# BC-028 — KEV lookup resolves grype's relatedVulnerabilities aliases

*Branch: `feat/BC-028-kev-alias-resolution`*
*Date: 2026-10-31*

---

## Situation

CISA KEV catalog uses CVE-* IDs only. Grype matches emit `vuln["id"]` as the primary identifier, which may be `GO-YYYY-NNNN`, `GHSA-xxxx`, or `CVE-*` depending on the source — and when the primary isn't a CVE, grype typically populates `relatedVulnerabilities` with the CVE alias. Pre-BC-028, `_kev.is_known_exploited(vuln["id"])` only checked the primary → `GO-*` and `GHSA-*` primaries with KEV-listed CVE aliases were silently missed.

Real-world case: CVE-2023-44487 (HTTP/2 Rapid Reset) is in CISA KEV. In Go stdlib scans, grype emits primary `GO-2023-1703` with `relatedVulnerabilities: [{"id": "CVE-2023-44487"}]`. Pre-BC-028, these findings never got tier=0.

## Fix

New function `kev.is_known_exploited_any(ids)` returns True if any id in the collection matches. `process_sbom_entry` calls it with `[vuln["id"], *related_ids]`.

## Scope

- **Modified:** `poc/hub/kev.py` — add `is_known_exploited_any`
- **Modified:** `poc/hub/hub.py` — `process_sbom_entry` extracts related ids, passes them in
- **Modified:** `poc/tests/unit/test_kev.py` — unit test for the new function
- **Modified:** `poc/tests/unit/test_hub_helpers.py` — integration test for the ingest path with aliased finding
- **Modified:** `docs/CAPABILITIES.md` — CAP-T066
- **Modified:** `docs/stories/INDEX.md` — BC-028 row

## Breaking changes

None. `is_known_exploited(cve_id)` kept unchanged for callers that already use it.

## Shipping evidence

| Scenario / goal | Evidence | Status |
|---|---|---|
| `is_known_exploited_any` returns True if any id in the collection is KEV | unit test | `test_kev.py::test_is_known_exploited_any_returns_true_if_any_in_catalog` | ✓ |
| Ingest path: GO-* primary with CVE alias in KEV → tier=0, kev=True, cve_id preserved for display | integration test | `test_hub_helpers.py::test_kev_resolved_via_related_alias` | ✓ |
| CAP-T066 registered + bidirectional | verify-traceability | CAPABILITIES.md diff | ✓ |
| make check green | multi-gate | `make check` | ✓ |
