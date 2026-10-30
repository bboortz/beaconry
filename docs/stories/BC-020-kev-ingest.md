---
id: BC-020
title: KEV ingestion + wire urgency.classify_tier into the hub ingestion path
status: shipped
created: 2026-10-31
branch: feat/BC-020-kev-ingest
plan: docs/plan/BC-020-kev-ingest-plan.md
---

# BC-020: KEV ingestion + wire urgency.classify_tier into the hub ingestion path

## Narrative

The operator is a self-hoster at the laptop or on the phone. They installed beaconry because `design-decisions.md § Urgency tiers` promised "CISA KEV: 15-min target, auto-fix if safe class" — the whole product differentiation is "you'll be told about exploits in the wild in minutes, not when you next read a mailing list." Today, pre-BC-020, every notification arrives with the same visual weight. CVSS 9.8 reconnaissance CVEs that may never be weaponized look identical to CVEs CISA listed yesterday with evidence of active exploitation. The operator can't tell which to tap first. Grepping showed why: `poc/hub/urgency.py::classify_tier` and `is_safe_class` exist and are unit-tested, but **are never called by hub.py**. The DB `tier` column defaults to 3 for every finding. The ladder is dead code with passing tests. BC-020 wires the KEV catalog into the ingestion path so the Tier 0 differentiation operators were promised actually fires.

Urgency: this story IS the Tier 0 enablement. Pre-BC-020, "Tier 0 (15-min target)" is aspirational docs; post-BC-020 it's observable operator behavior.

## Trigger

Scan completes → hub ingests findings in `process_sbom_entry`. For each finding, hub consults the local KEV cache (seeded on boot, refreshed hourly). Findings with `cveID ∈ KEV_catalog` get `tier = 0` and a visually distinct notification; others fall through to CVSS-based tiering.

## Expected outcome

Three observable changes:

1. **Visually distinct Tier 0 notifications.** Operator opens Telegram, sees two findings. The KEV one has a red `🚨 KEV — EXPLOITED IN THE WILD` header line above the standard severity row; the non-KEV one has the current `🔴 Critical — CVE-...` header. At-a-glance differentiation.
2. **KEV-first ordering in `make findings`.** The CLI table surfaces a `KEV` column (✓ / —) and sorts rows by `(tier, -cvss)` — Tier 0 rows surface at the top regardless of CVSS score.
3. **`/healthz` surfaces KEV cache freshness.** `curl /healthz` returns `"checks": {"kev": "ok", "kev_last_fetch_s": 1234, "kev_count": 1247}` or `"kev": "stale"` when the last fetch is > 25h old. Operator can see at a glance whether urgency scoring is current.

## Out of scope

- **`internet_facing` detection.** `classify_tier` already accepts this as a parameter; for V1 we hard-code `False`. Needs port enumeration (ss/netstat on scout side) which is a separate story.
- **Auto-fix on safe-class.** `is_safe_class(f, kev=True)` already exists, but wiring it to execute DockerAdapter without operator confirmation is a trust-model story (likely BC-021) with its own gate (`BEACONRY_AUTO_FIX_SAFE_CLASS=1` env opt-in, dry-run mode, canary ring).
- **Different notification cadence per tier.** Tier labels are stored correctly; actual SLA/batching behavior (Tier 0 immediate, Tier 3 nightly digest) is a separate cadence story.
- **Multi-feed sources.** V1 is CISA KEV only. OSV, vendor advisories, EPSS — later.
- **`knownRansomwareCampaignUse` dimension.** CISA emits it per-row; we ingest but don't use it in V1.

## Acceptance criteria (Gherkin)

```gherkin
Feature: KEV ingestion drives Tier 0 urgency

  Scenario: Hub fetches CISA KEV catalog on boot
    Given hub starts with an empty KEV cache
    When the KEV background fetch task completes
    Then /healthz reports {"kev": "ok", "kev_count": N} with N > 0

  Scenario: Finding whose CVE is in the KEV catalog gets tier 0
    Given CVE-2026-9999 is in the KEV cache
    And a scan surfaces CVE-2026-9999 with CVSS 7.5
    When hub processes the finding
    Then the DB row has tier = 0
    And the stored KEV flag is True

  Scenario: Finding whose CVE is not in KEV falls through to CVSS tiering
    Given CVE-2026-1111 is NOT in the KEV cache
    And it has CVSS 9.5 (not internet-facing — V1 default)
    When hub processes the finding
    Then the DB row has tier >= 2 (CVSS >= 7 branch; falls to tier 2 since internet_facing=False)

  Scenario: Tier 0 notification has a visually distinct KEV header
    Given a finding with tier = 0
    When the notification formats
    Then the body starts with "🚨 KEV — EXPLOITED IN THE WILD" above the severity row

  Scenario: KEV feed unreachable → scan still completes with stale cache
    Given the KEV HTTP fetch raises a connection error
    And a prior cache snapshot exists
    When scan ingestion runs
    Then findings still process (using the stale snapshot)
    And /healthz reports {"kev": "stale", "kev_last_fetch_s": X}

  Scenario: `make findings` surfaces KEV flag + Tier-0-first ordering
    Given 3 findings in the DB — one tier 0 KEV, one tier 2 CVSS 9.1, one tier 3 CVSS 5.0
    When operator runs `make findings`
    Then the output includes a "KEV" column
    And the tier 0 row is first regardless of CVSS

  Scenario: Cold boot with no cache and feed unreachable → tier stays 3, no crash
    Given no KEV cache file exists
    And the HTTP fetch fails
    When scan ingestion runs
    Then findings process with tier = 3 (CVSS fall-through)
    And /healthz reports {"kev": "unfetched"}
```

## Capabilities delivered

- **CAP-B019: Operator sees KEV-listed CVEs visually distinct and ranked first across the UI (Telegram + findings CLI)** (type: business, status: active)
  *The Tier 0 differentiation the product has promised since BC-000. Operator at a glance distinguishes "exploit in the wild" from "high CVSS but speculative."*

- **CAP-T058: CISA KEV feed ingestion — fetch on boot + hourly refresh + stale fallback + /healthz freshness** (type: technical, status: active)
  *`poc/hub/kev.py` fetches `https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json`, caches CVE IDs to disk, surfaces freshness via /healthz. Fetch failure is non-fatal — hub keeps serving with stale data.*

- **CAP-T059: Hub ingest path wires urgency.classify_tier → DB tier column** (type: technical, status: active)
  *Pre-BC-020, `urgency.classify_tier` existed with passing tests but was never called; every finding stored tier=3. BC-020 calls it from `process_sbom_entry` with `kev=kev.is_known_exploited(cve_id)` and `internet_facing=False` (deferred). The ladder exists in behavior, not just docs.*

- **CAP-T060: Notification format inserts a KEV header line for tier 0 findings** (type: technical, status: active)
  *`format_notification(f)` prepends `🚨 KEV — EXPLOITED IN THE WILD` when `f.tier == 0`. Operator sees the differentiation at a glance in Telegram.*

Depends on / extends:
- CAP-B011, CAP-B018 (operator-facing notification path)
- CAP-T019 (hub /scan ingestion path)
- CAP-T025 (notification formatting)

## Open questions

None. Scope pinned by the 7 Gherkin scenarios + the four out-of-scope exclusions.

## Links

- Design: `memory/design-decisions.md § Urgency tiers`, `§ Auto-fix trust model`
- Related stories: BC-000 (urgency ladder designed), BC-021 (future: safe-class auto-fix wiring, depends on this)
- Dead-code grep that triggered this: `poc/hub/urgency.py` (classify_tier never called), `poc/tests/unit/test_urgency.py` (passing but never exercised in product)
