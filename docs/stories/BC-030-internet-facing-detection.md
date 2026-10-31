---
id: BC-030
title: internet_facing detection → Tier 1 fires
status: ready
created: 2026-10-31
---

# BC-030: internet_facing detection → Tier 1 fires

## Narrative

The urgency ladder from `memory/design-decisions.md` has four tiers:
- **Tier 0** — CISA KEV
- **Tier 1** — CVSS ≥ 9.0 AND internet-facing
- **Tier 2** — CVSS 7–8 OR LAN-only
- **Tier 3** — CVSS < 7

Today `urgency.classify_tier(f, kev=..., internet_facing=False)` is called with `internet_facing=False` hard-coded in `process_sbom_entry`. So **Tier 1 is unreachable** — a CVSS 9.8 CVE on an exposed service gets Tier 2 (same as a LAN-only one), silently defeating the "internet-facing exploits are more urgent" differentiation the design promises.

## Expected outcome

- Scout emits per-container port-publication map alongside the SBOM (which host ports map to which images via `docker inspect`).
- Hub joins finding `source_ref` ↔ port map at ingest time. If any port on the container is published to `0.0.0.0` (or non-loopback), set `finding.internet_facing=True`.
- `classify_tier` receives the real value → Tier 1 findings surface.
- `format_notification` + `make findings` surface the exposure dimension visibly (e.g. a 🌐 marker next to Tier 1 rows).

## Out of scope

- Firewall rule introspection (iptables/nftables) — containers can be published to 0.0.0.0 but blocked at the host firewall. Treating published-to-0.0.0.0 as internet-facing is the honest conservative default.
- Reverse-proxy-aware detection (nginx-proxy container exposes 80/443 which fronts another container's internal port) — beaconry treats each container independently; same conservative default.
- Non-Docker exposure (host-level services). V1 is Docker containers only.

## Acceptance criteria (Gherkin)

```gherkin
Feature: internet_facing detection

  Scenario: Container publishes 80:80 → finding is internet_facing=True → tier=1 if CVSS≥9
    Given scout detects container nginx:1.24 publishes 0.0.0.0:80 → 80
    And hub matches CVE-X at CVSS 9.5 in that image
    When process_sbom_entry runs
    Then finding.internet_facing is True
    And finding.tier == 1 (not 2)

  Scenario: Container with no published ports → internet_facing=False
    Given scout detects container has no port mappings
    And hub matches CVE-Y at CVSS 9.5
    Then finding.internet_facing is False
    And finding.tier == 2 (CVSS≥7 fall-through)

  Scenario: Container publishes 127.0.0.1:80 → internet_facing=False
    Given scout detects container publishes loopback only
    Then finding.internet_facing is False

  Scenario: KEV overrides internet_facing (regression guard for BC-020)
    Given a KEV CVE in a non-exposed container
    Then finding.tier == 0 (KEV wins)

  Scenario: `make findings` surfaces exposure column
    Given 3 findings — tier 0 KEV, tier 1 exposed, tier 2 LAN-only
    When make findings runs
    Then the output has an exposure column
    And tier 1 row shows 🌐 (exposed)
    And tier 2 row shows — (LAN-only)
```

## Capabilities delivered

- **CAP-T068: Scout emits per-container port-publication map**
- **CAP-T069: Hub joins finding to port map at ingest; sets Finding.internet_facing**
- **CAP-T070: `make findings` + Telegram notification surface exposure dimension**
- **CAP-B020: Operator sees internet-facing CVEs ranked above LAN-only ones (Tier 1 differentiation)**

## Scope

- `poc/scout/scout.py` — new `collect_container_ports()`; include in POST payload
- `poc/hub/hub.py` — Finding dataclass gains `internet_facing: bool = False`; DB migration adds column; ingest joins port map; classify_tier gets real value
- `poc/scripts/findings.py` — add exposure column (🌐 / —) + sort key already handles tier
- `poc/hub/audit.py` — events gain `internet_facing` field (free via **details)
- Tests: scout port-collection unit test; hub ingest integration test for the 4 Gherkin scenarios; findings CLI render test
- CAPABILITIES: 4 new CAPs; test Covers blocks

## Estimated effort

~1 day. Not safety-critical (observational only), but DB schema change + two-service coordination makes it bigger than a drop-in feature.

## Links

- Design: `memory/design-decisions.md § Urgency tiers`
- Related: BC-020 (classify_tier wired, internet_facing deferred), BC-023 (MIN_CVSS bypass preserves KEV priority — this story preserves it over internet_facing too)
