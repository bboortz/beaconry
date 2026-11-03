# BC-076 — `beaconry metrics` remote mode (reads from hub `/audit` over HTTP)

*Branch: `feat/BC-076-metrics-remote-mode`*
*Start: 2026-11-03*

## Why

Operator runs `beaconry metrics` on their workstation, hub lives on
hetzner-staging. BC-075 shipped reading from a local `audit.log` only
→ operator has to SSH to hetzner-staging to measure. `beaconry status`
and `beaconry findings` already auto-route to the remote hub via
`~/.config/beaconry/cli.yml` (BC-055). Metrics should match that
mental model.

## Scope

### Auto-routing (default)

Reuse `cli_http.choose_source()` + `cli_config.load_cli_config()`.
If `hub_url` is non-localhost → fetch from `/audit`; if localhost +
compose running → read from local audit.log via DATA_DIR fallback;
if localhost + no compose → read local file.

### Explicit flags

- `--remote` — force HTTP fetch (useful when hub_url is localhost
  but you want to exercise the HTTP surface)
- `--local` — force local audit.log read (useful when analyzing an
  exported audit log via `DATA_DIR=...`)

Mutually exclusive. Default = auto.

### Fetch strategy

The hub's `/audit` endpoint (BC-055) caps at 10000 events per
request. For `--since 7d` on a quiet homelab, that's plenty.
Documented limitation; `metrics` prints a warning if the response
hits the cap so the operator knows the window may be truncated.

Query: `GET /audit?last=10000&since=<window-start-timestamp>` with
`Authorization: Bearer <hub_token>`.

Transforms the hub's event shape (same as local audit log — hub
reads from the same source) into the list of dicts the existing
`usability_metrics` / `features_metrics` functions already consume.
Zero changes to the aggregation layer.

### Error handling

- Config missing hub_url → clear message pointing to
  `~/.config/beaconry/cli.yml` setup (BC-055 docs)
- 401 bad token → "check hub_token in cli.yml"
- Connection refused → "hub unreachable at {url}"
- Response not a list → "unexpected response shape"

All via `friendly_http_error()`.

### CLI help update

```
Usage: beaconry metrics [usability|features|all] [--since 7d] [--json] [--remote|--local]

Source selection (default: auto):
    (auto)      — HTTP if hub_url is non-localhost; else local file
    --remote    — force HTTP to the configured hub
    --local     — force local audit.log read ($DATA_DIR/audit.log)
```

### Version

0.5.0 → 0.6.0 (minor — new user-visible flag + new HTTP source
mode; auto-routing changes the default behavior for operators
whose cli.yml points at a remote hub).

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| `beaconry metrics --local` reads audit.log (unchanged BC-075 path) | integration | `test_BC_076_metrics_remote.py::test_local_mode_reads_file` | ✓ |
| `beaconry metrics --remote` fetches from `/audit` | integration | `test_BC_076_metrics_remote.py::test_remote_mode_fetches_from_audit_endpoint` | ✓ |
| Default auto-mode picks HTTP for non-localhost hub_url | integration | `test_BC_076_metrics_remote.py::test_auto_mode_picks_remote_for_remote_hub` | ✓ |
| Default auto-mode picks local for localhost hub_url | integration | `test_BC_076_metrics_remote.py::test_auto_mode_picks_local_for_localhost_hub` | ✓ |
| `--remote` + `--local` → usage error | integration | `test_BC_076_metrics_remote.py::test_mutually_exclusive_source_flags` | ✓ |
| Remote mode sends bearer token | integration | `test_BC_076_metrics_remote.py::test_remote_mode_sends_bearer_token` | ✓ |
| Remote mode respects --since (passes as query param) | integration | `test_BC_076_metrics_remote.py::test_remote_mode_passes_since_as_query` | ✓ |
| 401 renders friendly error | integration | `test_BC_076_metrics_remote.py::test_remote_mode_401_renders_friendly_error` | ✓ |
| Connection refused renders friendly error | integration | `test_BC_076_metrics_remote.py::test_remote_mode_connection_error` | ✓ |
| Response at 10000-event cap prints truncation warning | integration | `test_BC_076_metrics_remote.py::test_remote_mode_cap_warning` | ✓ |
| Non-list `/audit` response → clean exit 2 | integration | `test_BC_076_metrics_remote.py::test_remote_mode_rejects_non_list_response` | ✓ |
| --remote with empty hub_url → cli.yml setup message | integration | `test_BC_076_metrics_remote.py::test_remote_mode_fails_when_hub_url_missing` | ✓ |
| Auto-mode falls back to local when hub_url missing | integration | `test_BC_076_metrics_remote.py::test_auto_mode_falls_back_to_local_when_hub_url_missing` | ✓ |
| --remote overrides auto-route (localhost config) | integration | `test_BC_076_metrics_remote.py::test_explicit_remote_flag_wins` | ✓ |
| --local overrides auto-route (remote config) | integration | `test_BC_076_metrics_remote.py::test_explicit_local_flag_wins` | ✓ |
| --remote sends bearer token from cli.yml | integration | `test_BC_076_metrics_remote.py::test_remote_mode_sends_bearer_token` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Breaking changes

None visible to operators who weren't using the local-file metrics
path. The default-auto routing may change behavior for operators who
HAD a remote hub_url + were running `beaconry metrics` locally (they
previously saw local file; now they see hub data) — but that's the
intended fix, not a regression.

## Rollout

- `make bump-minor` → 0.6.0
- Merge via Release workflow
