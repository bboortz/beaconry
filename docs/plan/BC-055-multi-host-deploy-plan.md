# BC-055 — HTTP-only CLI → hub

*Branch: `feat/BC-055-multi-host-deploy`*
*Start: 2026-11-01*

## Scope

### Hub (`poc/hub/hub.py`)

- `require_bearer_token` FastAPI dependency. Reads `HUB_AUTH_TOKEN` at
  request time (not import — tests monkeypatch). Empty = pass. Non-empty
  → `Authorization: Bearer <token>` compared with `hmac.compare_digest`;
  mismatch → 401.
- `GET /findings` with query params `status`, `host`, `limit` (default
  500, max 10000). Projects the same columns the CLI table renders.
- `GET /audit` with `last` (default 50), `since`, `until`, `event`,
  `cve`, `host`, `finding`. Newest-first, enriched with `cve_id` /
  `host` / `cve_alias` from a point-in-time DB lookup.
- `GET /fleet` returning `[{host, last_seen_ts, open_findings,
  total_findings}, …]` sorted by `last_seen_ts` desc. Derived from the
  findings table.
- `POST /scan/trigger` proxies to the local scout's
  `http://scout:8001/scan/trigger` via `httpx`. 202/409/503 passed
  through.
- `PATCH /findings/{finding_id}` with JSON body `{"status": "snoozed"
  | "accepted" | "fixed"}`. Updates the DB row, appends a
  `status_change` audit event, returns the new row. 404 if the id
  doesn't exist; 400 for a status not in the operator-allowed set.
- All five endpoints `dependencies=[Depends(require_bearer_token)]`.

### Scout (`poc/scout/scout.py`)

Already exposes `/scan/trigger` from BC-037. Hub reaches it via the
Docker-network name `scout` on the shared compose network — no new
scout-side change.

### CLI (`poc/scripts/`)

- New `poc/scripts/cli_config.py` — `CliConfig` dataclass (`hub_url`,
  `hub_token`, `source_path`) + `load_cli_config(path, env)`. Default
  path: `$XDG_CONFIG_HOME/beaconry/cli.yml` → `~/.config/beaconry/cli.yml`.
  Env override: `HUB_URL` + `HUB_TOKEN`. Empty-env values skip (don't
  clobber YAML).
- New `poc/scripts/cli_http.py` — `choose_source(cfg, compose_hub_running,
  force_local)` returning `"http" | "local-compose" | "local-file"`.
  `http_get(url, token)` + `http_patch(url, body, token)` helpers.
- `findings.py` — new `_findings_via_http(cfg, status, host)`; main()
  dispatches on `choose_source`.
- `audit.py` — new `_audit_via_http(cfg, args)`; main() dispatches.
  FOLLOW stays on compose path (streaming is a follow-up).
- `status.py` — fetch `/healthz` via `cli_config.hub_url` instead of
  the hard-coded localhost URL. Append the FLEET section from `/fleet`.
- New `poc/scripts/set_status.py` — `make set-status FINDING=<id>
  STATUS=<s>` → `PATCH /findings/<id>`. Dry-run-safe (no local DB write
  path; this one is always HTTP).

### Makefile

- `make scan` — if `HUB_URL` is non-localhost (CliConfig), curl the
  hub's `/scan/trigger` instead of the scout's. No SSH branch.
- `make set-status FINDING=X STATUS=Y` — new target invoking
  `poc.scripts.set_status`.
- Allowlist `FINDING` and `STATUS` as Makefile per-invocation CLI args
  (already allowlisted in `verify_variables_documented.CLI_ONLY_ALLOWLIST`).

### Docker Compose

- `HUB_PUBLISH_ADDR` already added by the operator; wire the env var
  through the `environment:` list of the hub service so hub.py can log
  the "unauthenticated exposure" warning when it binds publicly without
  a token.
- `HUB_AUTH_TOKEN` passed through from `.env`.

### Docs

- `docs/VARIABLES.md` — new rows for `HUB_AUTH_TOKEN`, `HUB_TOKEN` (CLI
  side), `HUB_PUBLISH_ADDR`.
- `poc/.env.example` — new placeholder lines.

### Tests

Integration test file `poc/tests/integration/test_BC_055_http_cli.py`
covering:
- Every endpoint: shape + filters + auth-gated + auth-passes.
- CLI routing: localhost → local-compose; non-localhost → http; empty
  env doesn't clobber YAML.
- `set_status` happy-path + 404 + invalid status.

## Design decisions

### Why `/scan/trigger` on the hub (not direct scout access)

Operator should need exactly one URL + one token. The hub-side proxy
lets us keep the scout bound to Docker-network-only (no exposed port
on the host for scouts). Future: fan out to N scouts via `/fleet` lookup.

### Why `PATCH /findings/{id}` not `POST /findings/{id}/{action}`

The surface the operator mutates is the status column. Treating it as
a resource with a single mutable field keeps the API shape small and
mirrors the DB.

### Why `HUB_AUTH_TOKEN` empty = auth off

Zero-change path for every operator today who runs locally. Opt-in to
auth when exposing to the LAN.

### Why no SSH

Operator-explicit requirement. SSH-based `make scan HOST=…` from the
earlier branch is explicitly out of scope.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| GET /findings shape + filters | integration | `test_BC_055_http_cli.py::test_findings_endpoint_returns_rows` | ✓ |
| GET /findings status+host filter composes | integration | `test_BC_055_http_cli.py::test_findings_endpoint_filters` | ✓ |
| GET /audit returns enriched events | integration | `test_BC_055_http_cli.py::test_audit_endpoint_returns_events` | ✓ |
| GET /fleet returns per-host summaries | integration | `test_BC_055_http_cli.py::test_fleet_endpoint_returns_per_host` | ✓ |
| POST /scan/trigger proxies to scout | integration | `test_BC_055_http_cli.py::test_scan_trigger_proxies` | ✓ |
| POST /scan/trigger 503 when scout unreachable | integration | `test_BC_055_http_cli.py::test_scan_trigger_503_when_scout_unreachable` | ✓ |
| PATCH /findings/{id} transitions status | integration | `test_BC_055_http_cli.py::test_patch_status_transitions` | ✓ |
| PATCH /findings/{id} 404 on unknown id | integration | `test_BC_055_http_cli.py::test_patch_status_404_on_unknown_id` | ✓ |
| PATCH /findings/{id} 400 on bad status | integration | `test_BC_055_http_cli.py::test_patch_status_rejects_bad_status` | ✓ |
| All 5 endpoints auth-gated when token set | integration | `test_BC_055_http_cli.py::test_all_endpoints_auth_gated` | ✓ |
| Scout + CLI tokens are independent (per-endpoint scope) | integration | `test_BC_055_http_cli.py::test_scout_and_cli_tokens_are_independent` | ✓ |
| Health endpoints bypass auth | integration | `test_BC_055_http_cli.py::test_health_bypasses_auth` | ✓ |
| CliConfig env overrides YAML | integration | `test_BC_055_http_cli.py::test_cli_config_env_overrides_yaml` | ✓ |
| CliConfig empty env doesn't clobber YAML | integration | `test_BC_055_http_cli.py::test_cli_config_empty_env_skipped` | ✓ |
| CliConfig missing file uses defaults | integration | `test_BC_055_http_cli.py::test_cli_config_missing_file_uses_defaults` | ✓ |
| CliConfig malformed YAML raises | integration | `test_BC_055_http_cli.py::test_cli_config_malformed_raises` | ✓ |
| choose_source routing matrix | integration | `test_BC_055_http_cli.py::test_choose_source_routing` | ✓ |
| is_localhost_url variants | integration | `test_BC_055_http_cli.py::test_is_localhost_url_variants` | ✓ |
| findings.py uses HTTP when hub remote | integration | `test_BC_055_http_cli.py::test_findings_cli_uses_http` | ✓ |
| set_status.py PATCHes with Bearer | integration | `test_BC_055_http_cli.py::test_set_status_patches_with_bearer` | ✓ |
| set_status.py rejects bad STATUS | integration | `test_BC_055_http_cli.py::test_set_status_rejects_bad_status` | ✓ |
| set_status.py requires FINDING | integration | `test_BC_055_http_cli.py::test_set_status_requires_finding` | ✓ |
| set_status.py requires STATUS | integration | `test_BC_055_http_cli.py::test_set_status_requires_status` | ✓ |
| set_status.py HTTP error exits cleanly | integration | `test_BC_055_http_cli.py::test_set_status_http_error_exits_cleanly` | ✓ |
| scan_cli uses hub when remote | integration | `test_BC_055_http_cli.py::test_scan_cli_uses_hub_when_remote` | ✓ |
| scan_cli 409 → exit 2 | integration | `test_BC_055_http_cli.py::test_scan_cli_409_already_running` | ✓ |
| scan_cli local without scout → exit 1 | integration | `test_BC_055_http_cli.py::test_scan_cli_local_without_scout` | ✓ |
| scan_cli local scout 202 → exit 0 | integration | `test_BC_055_http_cli.py::test_scan_cli_local_scout_202` | ✓ |
| scan_cli local scout 409 → exit 2 | integration | `test_BC_055_http_cli.py::test_scan_cli_local_scout_409` | ✓ |
| scan_cli local scout unreachable → exit 3 | integration | `test_BC_055_http_cli.py::test_scan_cli_local_scout_unreachable` | ✓ |
| `_scout_running` tolerates missing docker CLI | integration | `test_BC_055_http_cli.py::test_scan_cli_scout_running_without_docker` | ✓ |
| friendly_http_error — HTTPError | integration | `test_BC_055_http_cli.py::test_friendly_http_error_http_error` | ✓ |
| friendly_http_error — URLError | integration | `test_BC_055_http_cli.py::test_friendly_http_error_url_error` | ✓ |
| friendly_http_error — fallback | integration | `test_BC_055_http_cli.py::test_friendly_http_error_fallback` | ✓ |
| http_post wraps non-dict response | integration | `test_BC_055_http_cli.py::test_http_post_wraps_non_dict` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

Legend: ✓ covered · ❌ open.
