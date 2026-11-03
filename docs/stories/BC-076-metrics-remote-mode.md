---
id: BC-076
title: beaconry metrics --remote reads from hub /audit over HTTP
status: shipped
created: 2026-11-03
branch: feat/BC-076-metrics-remote-mode
plan: docs/plan/BC-076-metrics-remote-mode-plan.md
---

# BC-076: metrics remote mode

Operator asked for `beaconry metrics --remote` that reads from the
hub's `/audit` endpoint over HTTP, following the same pattern as
`beaconry status` and `beaconry findings`.

## Shipped

- `poc/scripts/metrics.py` learns `--remote` / `--local` flags
  (mutually exclusive) + auto-mode via `cli_config` + `cli_http`.
- `fetch_remote_audit_events(hub_url, hub_token, since_ts)` hits
  `GET /audit?last=10000&since=<ts>` with bearer auth.
- Error handling via `friendly_http_error` — 401 / connection
  refused / unexpected shape all route to the same code path as
  `beaconry status`.
- Output includes source label: `Window: last 7.0d, N events from
  remote (/audit on <url>)`.
- Response at the 10000-event cap prints a truncation warning.
- CAP-T158 + CAP-T159, docs/METRICS.md updated.

0.5.0 → 0.6.0.
