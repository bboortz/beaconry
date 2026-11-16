---
id: BC-126
title: /audit/integrity endpoint + beaconry verify-integrity HTTP routing
status: shipped
created: 2026-11-16
branch: feat/BC-126-integrity-endpoint-cli-http
plan: docs/plan/BC-126-integrity-endpoint-cli-http-plan.md
---

# BC-126: `beaconry verify-integrity` works against a remote hub

## Narrative

Operator ran `beaconry verify-integrity --since 1h` on bb-82jq
right after v0.22.9 deployed. Expected: a clean report of every
scan_integrity event on hetzner-staging's hub. Got:

```
✗ audit log not found at .beaconry-cache/audit.log
```

BC-122's `verify-integrity` CLI reads the LOCAL audit log. On the
operator workstation there's no audit log — it lives inside the
hub container on hetzner-staging. `beaconry findings`, `beaconry
audit`, `beaconry status` all route through HTTP to the configured
hub; `verify-integrity` was built as local-only and missed the
remote path.

Post-BC-126: `/audit/integrity` endpoint serves scan_integrity
events (same auth + query shape as `/findings`), and the CLI auto-
picks remote vs local the same way `beaconry findings` does.
Operators on a workstation get the fleet-wide integrity verdict
with one command.

## Trigger

- Operator running `beaconry verify-integrity [--since 24h]` from
  anywhere the CLI config points at a remote hub (the default
  operator path).
- Future dashboards / monitoring scripts that want a programmatic
  integrity feed.

## Expected outcome

- `GET /audit/integrity?since=24h` returns a JSON list of
  `scan_integrity` audit events (host, iteration_hash,
  transfer_match, n_candidates, n_persisted, drop buckets,
  processing_hash).
- `beaconry verify-integrity` uses `choose_source(cfg, …)` the same
  way `beaconry findings` does — HTTP against the configured hub,
  falls back to local file for dev-compose runs.
- Exit codes preserved: 0 clean, 1 mismatch / unexplained drop, 2
  audit unreachable.

## Out of scope

- Dashboards / UIs that consume the endpoint.
- Historical replay beyond what the audit log retention already
  provides.
- Changing the `scan_integrity` event shape.

## Acceptance criteria (Gherkin)

```gherkin
Feature: verify-integrity works against a remote hub

  Scenario: Happy path — HTTP endpoint returns scan_integrity events
    Given a hub with 5 scan_integrity events in the audit log
    When operator GETs /audit/integrity
    Then response is a 200 with a JSON list of 5 events
    And each event has iteration_hash + transfer_match + n_candidates

  Scenario: --since filter narrows to recent events
    Given scan_integrity events spanning 7 days
    When operator GETs /audit/integrity?since=24h
    Then only events within the last 24h are returned

  Scenario: Auth required
    Given HUB_CLI_TOKEN is set
    When operator GETs /audit/integrity without a Bearer token
    Then response is 401

  Scenario: CLI auto-routes remote
    Given CLI config has hub_url = https://hetzner-staging:8000
    When operator runs `beaconry verify-integrity`
    Then CLI fetches /audit/integrity over HTTP
    And NOT from the local `.beaconry-cache/audit.log`

  Scenario: CLI falls back to local on dev-compose
    Given CLI config has hub_url = http://localhost:8000
    And beaconry-dev-hub container is running
    When operator runs `beaconry verify-integrity`
    Then CLI runs the verifier inside the container
    And output matches the local-file path shape

  Scenario: Verifier reports via HTTP same as local
    Given remote hub has 3 verified + 1 mismatch events
    When operator runs `beaconry verify-integrity`
    Then output includes "3 verified · 1 mismatch"
    And exit code is 1

  Scenario: Backward compatibility — legacy hub without the endpoint
    Given hub is pre-BC-126 and returns 404 on /audit/integrity
    When CLI falls back
    Then operator sees a one-line error pointing at the hub upgrade
    And exit code is 2
```

## Capabilities delivered

- **CAP-T229: /audit/integrity HTTP endpoint + verify-integrity remote routing**
  (type: technical, status: active)

  *Hub exposes `GET /audit/integrity?since=24h&host=…` behind the
  existing CLI-token auth (`require_cli_token`). Returns a JSON list
  of `scan_integrity` audit events scanned from `audit.log`,
  filtered by since + host query params; limit capped at 10k rows
  to match /findings. `poc/scripts/verify_integrity.py` gets a
  `_fetch_events_via_http(cfg, since)` helper + `main()` now routes
  via `choose_source(cfg, compose_hub_running=…)` identical to
  `beaconry findings`. The HTTP path calls `build_report()` with
  the fetched rows, preserving every existing exit-code case.
  Legacy pre-BC-126 hubs surface a friendly "upgrade hub to use
  verify-integrity remotely" error with exit 2.*

Supports CAP-T223 (pipeline integrity verifier) and CAP-T076
(remote CLI routing).

## Open questions

Resolved during drafting (2026-11-16):

1. New endpoint name — `/audit/integrity` vs `/integrity` vs
   `/scan-integrity`?
   **`/audit/integrity`.** Namespaces under the existing audit
   surface; keeps room for sibling endpoints (`/audit/findings`,
   `/audit/notify_failed`) if they ever become useful.
2. Same auth as /findings or a scout-only token?
   **CLI token (same as /findings).** This is operator read, not
   scout write.
3. Return `events` as a top-level list vs wrapped?
   **Top-level list** (same shape /findings uses).
4. Response size cap?
   **10k rows**, mirrors `/findings` limit.

## Links

- Related: BC-122 (verify-integrity CLI origin), BC-076 (remote
  routing pattern — `beaconry findings` + `beaconry audit`).
