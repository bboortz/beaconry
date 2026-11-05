---
id: BC-091
title: KEV fetch uses identifiable User-Agent — CISA stops 403'ing
status: in-progress
created: 2026-11-05
branch: feat/BC-091-kev-user-agent
plan: docs/plan/BC-091-kev-user-agent-plan.md
---

# BC-091: KEV User-Agent fix

## Narrative

Operator watches hetzner hub `/healthz` and sees `"kev":
"unfetched", "kev_count": 0`. Monitor stream shows hourly `WARNING
KEV fetch failed: HTTP Error 403: Forbidden — falling back to cache`
at 07:55 UTC, 08:55, 09:55, 10:55, 11:55 (2026-11-05). CISA's
endpoint is 403'ing every anonymous request from this host. Cache
fallback keeps Tier 0 classification working short-term, but if the
25h stale-threshold hits, KEV classification degrades to CVSS-only
— a Tier 0 CVE could go undetected.

Pre-BC-091 User-Agent was `beaconry/0.1 (+kev)` — hardcoded old
version, no repo reference. CISA's documented policy recommends
identifiable User-Agent strings with a contact URL. BC-091 switches
to a dynamic UA that names the actual beaconry version + repo URL.

## Trigger

CISA KEV endpoint returns HTTP 403 for anonymous fetches that look
bot-ish.

## Expected outcome

- Default UA `beaconry/<version> (+https://github.com/bboortz/
  beaconry)` where `<version>` comes from the VERSION file.
- Operator can override via `BEACONRY_KEV_USER_AGENT` for air-
  gapped / proxied setups that need a specific UA to pass through a
  gateway.
- Empty / unset env falls back to the dynamic default.
- Next hourly refresh on hetzner succeeds; `/healthz` moves
  `"kev": "unfetched"` → `"kev": "ok"` with a positive
  `kev_count`.

## Out of scope

- Fallback-to-cache behaviour (unchanged — already graceful).
- Alternate KEV mirrors.
- Retry-with-backoff on 403 (graceful fallback already handles).

## Acceptance criteria (Gherkin)

```gherkin
Feature: KEV fetch uses identifiable User-Agent

  Scenario: Default UA names version + repo
    Given BEACONRY_KEV_USER_AGENT is unset
    When kev._http_fetch is called
    Then the request carries User-Agent
      "beaconry/<SOFTWARE_VERSION> (+https://github.com/bboortz/beaconry)"

  Scenario: Operator can override
    Given BEACONRY_KEV_USER_AGENT="custom-ua/1.0 (+contact@example.com)"
    When kev module is re-imported (compose-friendly)
    Then kev.KEV_USER_AGENT == that exact string

  Scenario: Empty env falls back to default
    Given BEACONRY_KEV_USER_AGENT=""
    Then kev.KEV_USER_AGENT contains "beaconry/"
      And it contains "(+https://github.com/bboortz/beaconry)"

  Scenario: UA version tracks SOFTWARE_VERSION
    Given SOFTWARE_VERSION is "0.14.1"
    Then the default UA contains "beaconry/0.14.1"
```

## Capabilities delivered

- **CAP-T187: KEV fetch User-Agent is identifiable and operator-
  overridable** (type: technical, status: active)
  *Default is `beaconry/<version> (+repo)` with version pulled from
  `SOFTWARE_VERSION`. Override via `BEACONRY_KEV_USER_AGENT`.
  Closes the CISA 403-on-anonymous-UA path flagged in BC-088
  LIFECYCLE §17.3.*

## Links

- `docs/LIFECYCLE.md § 17.3` (bug, BC-088)
- Monitor stream 2026-11-05 07:55 UTC onward (hourly 403 pattern)
- `poc/hub/kev.py:_http_fetch`
