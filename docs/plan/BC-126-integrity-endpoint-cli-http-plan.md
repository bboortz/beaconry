# BC-126 — /audit/integrity endpoint + verify-integrity HTTP routing

*Branch: `feat/BC-126-integrity-endpoint-cli-http`*
*Start date: 2026-11-16*
*Story: `docs/stories/BC-126-integrity-endpoint-cli-http.md`*

---

## Situation

BC-122 shipped `beaconry verify-integrity` as a local-only CLI. On
the operator workstation that's useless — no audit log exists
locally; the file lives inside the hub container. Every other CLI
subcommand (`findings`, `audit`, `status`) routes via HTTP; the
verifier is the lone exception.

## Problems to solve

- Remote operators can't run `beaconry verify-integrity` without
  SSH'ing to the hub host + `docker exec`.
- No programmatic endpoint for scan_integrity events — monitoring
  / dashboards can't consume.

## Goals

1. `GET /audit/integrity?since=24h&host=…` on the hub returns a
   JSON list of scan_integrity events (same auth as /findings).
2. `beaconry verify-integrity` uses `choose_source(cfg, …)` so
   remote hub → HTTP, dev-compose → docker-exec, else local file.
3. Exit codes unchanged: 0 clean, 1 mismatch, 2 unreachable.
4. Backward-compat path: legacy pre-BC-126 hub returns 404; CLI
   surfaces a one-line "upgrade hub" error.

## Scope

- `poc/hub/hub.py` — new `@api.get("/audit/integrity", ...)` under
  `require_cli_token`. Reads `audit.log`, filters by event name +
  since + host, returns list[dict]. Capped at 10k rows.
- `poc/scripts/verify_integrity.py` — new `_fetch_events_via_http(cfg, since)`
  helper. `main()` wraps with `choose_source(cfg, compose_hub_running=…)`
  to pick HTTP / local-compose / local-file paths. Local-compose
  path exec's the verifier inside `beaconry-dev-hub`.
- `poc/tests/unit/test_BC_126_integrity_endpoint_cli_http.py` — new.
- `docs/CAPABILITIES.md` — CAP-T229.

## Non-goals

- Changing the `scan_integrity` event shape.
- Pagination (10k row cap is sufficient — a hub generating more
  per since-window is a separate concern).
- Dashboards.

## Design decisions

- **Endpoint path `/audit/integrity`** (not `/integrity` or
  `/scan-integrity`) — namespaces under audit surface for future
  siblings.
- **Reuse existing `choose_source` + `http_get`** patterns from
  `poc/scripts/cli_http.py`. Don't invent a new HTTP helper.
- **Local-compose path exec's inside the container** (like
  `beaconry findings` does), so the operator running `beaconry
  verify-integrity` on their dev host hits the dev-compose hub's
  audit log, not a non-existent local file.
- **404 on legacy hub → friendly error + exit 2**. Don't hard-crash.

## Breaking changes

### 1. API / return shape
Pure additive — new endpoint.

### 2. Data model
None.

### 3. Behavioral semantics

- `beaconry verify-integrity` now works from operator workstation
  (previously errored). Pure UX fix.
- Local-file path preserved for dev tests + legacy operators.

## Merge plan

1. `make check` passes on branch tip.
2. PR → dev; auto-merge on green CI.
3. Release workflow bumps v0.22.9 → v0.22.10, forge rebuilds.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Endpoint returns scan_integrity events | unit | `test_BC_126_integrity_endpoint_cli_http.py::test_endpoint_returns_scan_integrity_events` | ✓ |
| --since filter narrows results | unit | `test_endpoint_since_filter_narrows` | ✓ |
| Auth required when CLI token set | unit | `test_endpoint_requires_cli_token` | ✓ |
| --host filter narrows to one scout | unit | `test_endpoint_host_filter_narrows` | ✓ |
| Response capped at 10k | unit | `test_endpoint_limit_cap` | ✓ |
| CLI auto-routes remote | unit | `test_cli_routes_http_when_remote_configured` | ✓ |
| CLI backward-compat on 404 legacy hub | unit | `test_cli_legacy_hub_404_friendly_error` | ✓ |
| Local-file path still works (regression) | unit | `test_cli_local_file_path_preserved` | ✓ |
| Hub `_parse_since_spec` grammar variants | unit | `test_parse_since_spec_variants` | ✓ |
| Memory drift pattern added | N/A | pure additive | ✓ |

## Testing strategy

Unit-only. TestClient hub for the endpoint; monkeypatch
`choose_source` + `http_get` for the CLI routing. Legacy 404 case
asserts the exception path.

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_126_integrity_endpoint_cli_http.py -v
```
