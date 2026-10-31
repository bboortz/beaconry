---
id: BC-028
title: KEV lookup resolves grype's relatedVulnerabilities aliases
status: shipped
created: 2026-10-31
branch: feat/BC-028-kev-alias-resolution
plan: docs/plan/BC-028-kev-alias-resolution-plan.md
---

# BC-028: KEV lookup resolves grype's relatedVulnerabilities aliases

## Narrative

CISA KEV catalog uses `CVE-*` ids only. Grype's primary id for a Go stdlib vuln is `GO-YYYY-NNNN`; the CVE alias lives in `relatedVulnerabilities`. Pre-BC-028, hub only consulted the primary — Go stdlib findings with KEV-listed CVE aliases silently never got tier=0. Real example: CVE-2023-44487 (HTTP/2 Rapid Reset) exists in Go stdlib, grype emits primary `GO-2023-1703` with CVE alias in `related`. Operator never heard it was exploit-in-the-wild.

## Expected outcome

Hub's `process_sbom_entry` now checks the primary id AND every entry in `relatedVulnerabilities` against the KEV catalog. Primary id preserved for display; `finding.kev=True` when any alias matches.

## Acceptance criteria (Gherkin)

```gherkin
Scenario: GO-* primary with CVE alias in KEV
  Given KEV cache contains CVE-2023-44487
  And a scan produces a finding with primary GO-2023-1703 and related=[CVE-2023-44487]
  When process_sbom_entry runs
  Then finding.kev is True
  And finding.tier == 0
  And finding.cve_id == "GO-2023-1703" (primary preserved for display)
```

## Capabilities delivered

- **CAP-T066: KEV lookup resolves grype's `relatedVulnerabilities` aliases** (technical, active)

## Links

- Trigger: 2026-10-31 user raised question about `GO-*` ids in the digest; IDEA logged + shipped same day.
- Related: BC-020 (origin KEV lookup), BC-023 (MIN_CVSS filter order)
