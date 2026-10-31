---
id: BC-023
title: KEV findings bypass MIN_CVSS filter
status: shipped
created: 2026-10-31
branch: feat/BC-023-kev-bypasses-min-cvss
plan: docs/plan/BC-023-kev-bypasses-min-cvss-plan.md
---

# BC-023: KEV findings bypass MIN_CVSS filter

## Narrative

Operator is a self-hoster running beaconry with the default `MIN_CVSS=9.0`. CISA adds CVE-2023-44487 (HTTP/2 Rapid Reset, CVSS 7.5) to the KEV catalog — attackers are actively exploiting it in the wild. Operator's hub scans their nginx:1.24 container, grype matches the CVE… and the operator never hears about it. Why: `process_sbom_entry` filters by raw CVSS **before** consulting the KEV catalog. 7.5 < 9.0 → dropped. The whole product thesis of beaconry says "KEV is empirical exploitation signal; CVSS is theoretical severity" — but the code filters by the theoretical score first, so empirically-exploited CVEs below MIN_CVSS are silently invisible. Found live during BC-022 verification: nginx:1.24 contains CVE-2025-27363 (CVSS 8.1, KEV) and CVE-2023-44487 (CVSS 7.5, KEV) — both dropped by MIN_CVSS=9.0 before KEV check could flag them.

Urgency: N/A — but semantically this is a Tier 0 correctness bug. The product promises operators they'll hear about KEV exploits in minutes; the current code can silently never surface them.

## Trigger

Any hub `/scan` processing where a scanned finding has `cvss < MIN_CVSS` AND the CVE is in the KEV catalog. Pre-BC-023: dropped. Post-BC-023: scored as tier=0 and notified.

## Expected outcome

- KEV CVEs always surface regardless of CVSS score, as long as grype matched them.
- Non-KEV CVEs below MIN_CVSS continue to be filtered (unchanged noise reduction).
- `/healthz` adds a counter: `"kev_bypassed_min_cvss_count"` — how many KEV findings in the current session passed through only because they're KEV (operator diagnostic for "is the bypass actually firing?").

## Out of scope

- Not changing MIN_CVSS default (stays at 9.0 or whatever operator configured).
- Not touching the per-run cap or digest machinery.
- Not changing CVSS 0 / missing-CVSS handling for non-KEV (unchanged: still filtered).
- Not re-processing findings already in the DB — BC-023 affects future ingests only (operators can `rm -rf /data/beaconry.db` + rescan if they want to re-run).

## Acceptance criteria (Gherkin)

```gherkin
Feature: KEV findings bypass MIN_CVSS

  Scenario: KEV CVE below MIN_CVSS still gets ingested
    Given MIN_CVSS = 9.0 and KEV cache contains CVE-2023-44487
    And a scan produces a finding with cve_id=CVE-2023-44487 cvss=7.5
    When process_sbom_entry runs
    Then the finding IS included in the returned list
    And finding.tier == 0 and finding.kev == True

  Scenario: Non-KEV CVE below MIN_CVSS is still filtered (unchanged)
    Given MIN_CVSS = 9.0 and KEV cache does NOT contain CVE-NON-01
    And a scan produces a finding with cve_id=CVE-NON-01 cvss=7.5
    When process_sbom_entry runs
    Then the finding is NOT in the returned list

  Scenario: Non-KEV CVE at/above MIN_CVSS is unchanged
    Given MIN_CVSS = 9.0 and KEV cache does NOT contain CVE-HIGH-01
    And a scan produces a finding with cve_id=CVE-HIGH-01 cvss=9.5
    When process_sbom_entry runs
    Then the finding IS in the returned list with tier=2 (non-KEV + CVSS≥7 branch)

  Scenario: KEV CVE above MIN_CVSS also still works (regression guard)
    Given MIN_CVSS = 9.0 and KEV cache contains CVE-KEV-HIGH
    And a scan produces a finding with cve_id=CVE-KEV-HIGH cvss=9.8
    When process_sbom_entry runs
    Then the finding IS in the returned list with tier=0

  Scenario: Both KEV CVEs in nginx:1.24 (live acceptance) surface post-rescan
    Given a hub with MIN_CVSS=9.0 and KEV cache populated
    When scout rescans nginx:1.24
    Then CVE-2025-27363 (CVSS 8.1) and CVE-2023-44487 (CVSS 7.5) are ingested with tier=0
    And they appear in the top-N notified set (not the digest)
```

## Capabilities delivered

- **CAP-T063: KEV findings bypass MIN_CVSS filter in hub ingest** (type: technical, status: active)
  *`process_sbom_entry` consults `kev.is_known_exploited(cve_id)` before applying the MIN_CVSS gate. KEV CVEs always pass; non-KEV CVEs continue to be filtered as before. Closes BC-020's architectural ordering bug where KEV was checked after a filter that could drop it.*

Depends on / extends:
- CAP-T058 (KEV ingestion)
- CAP-T059 (hub ingest wires classify_tier) — this story fixes an ordering bug inside the same function
- CAP-B019 (operator-visible KEV differentiation) — directly load-bearing on this capability's correctness

## Open questions

None. Scope pinned by 5 Gherkin scenarios + the four out-of-scope exclusions.

## Links

- Trigger: 2026-10-31 live-smoke of BC-022 digest; grype on nginx:1.24 showed 2 KEV CVEs below MIN_CVSS being silently dropped.
- Related: BC-020 (introduced the bug — `MIN_CVSS` check came before KEV check in the same loop), BC-022 (surfaced the symptom via the digest).
