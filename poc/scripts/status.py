"""BC-013 `make status` — Docker-compose-ps-like health table for beaconry.

One row per container. Columns: NAME · STATE · HEALTH · LAST SCAN · DETAILS.

Both services queried uniformly via HTTP /healthz (hub on localhost:8000,
scout on localhost:8001 via BC-013). No `docker exec` fallback. All error
cases produce human-readable DETAILS strings — no raw Python exception
class names leak into the operator-facing output.

Exit codes:
  0 — all services healthy
  1 — any service degraded (reachable but reporting internal issues)
  2 — any service down (unreachable; container not up or network broken)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from poc.scripts.cli_config import load_cli_config
from poc.scripts.cli_http import CLIENT_API_VERSION

# BC-055: CLI config (hub_url + hub_token) drives the healthz target.
# `BEACONRY_HUB_HEALTHZ_URL` remains a fully-qualified escape hatch for
# tests + edge cases where the full URL must diverge from the config.
_CLI_CFG = load_cli_config()
HUB_HEALTHZ_URL = os.environ.get(
    "BEACONRY_HUB_HEALTHZ_URL",
    _CLI_CFG.hub_url.rstrip("/") + "/healthz",
)
SCOUT_HEALTHZ_URL = os.environ.get("BEACONRY_SCOUT_HEALTHZ_URL", "http://localhost:8001/healthz")
HUB_AUTH_TOKEN = _CLI_CFG.hub_token

STATE_RUNNING = "running"
STATE_DOWN = "down"
HEALTH_HEALTHY = "healthy"
HEALTH_DEGRADED = "degraded"
HEALTH_NONE = "-"


@dataclass
class ServiceStatus:
    name: str
    state: str
    health: str
    last_scan: str
    details: str
    # BC-062: software · api · schema (schema only on hub). Empty = unknown.
    version: str = ""
    # BC-062: raw api int if parseable, else None (used to detect mismatch
    # against CLIENT_API_VERSION for the trailing warning line).
    api_version: int | None = None


def fetch_healthz(url: str, timeout: float = 2.0, token: str = "") -> dict:
    """Return parsed /healthz JSON; raises urllib/connection errors on failure.

    BC-055: `/healthz` never requires auth, but if the operator put a
    reverse proxy in front of the hub that forces auth on everything,
    the token lets the probe through.
    """
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — operator-configured URL
        return json.loads(resp.read().decode())


def fetch_fleet(url: str, timeout: float = 2.0, token: str = "") -> list[dict]:
    """BC-055: GET /fleet; empty list on any error (never block compose rows)."""
    try:
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — operator-configured URL
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


_STATUS_GLYPH = {"active": "●", "stale": "⚠", "dead": "✗"}
_HEALTH_GLYPH = {"ok": "●", "healthy": "●", "degraded": "⚠", "unknown": "?"}


def _format_fleet_status(row: dict) -> str:
    """BC-061: '● active' / '⚠ stale' / '✗ dead' for the STATUS column.
    Missing status (pre-BC-061 hub response) renders as empty."""
    status = row.get("status")
    if not status:
        return ""
    return f"{_STATUS_GLYPH.get(status, '?')} {status}"


def _format_fleet_version(row: dict, hub_api: int | None) -> str:
    """BC-063: ``'0.1.0 · api1'`` (+ trailing ``⚠`` on hub-API mismatch).

    Pre-BC-063 hub response (software_version=None) → ``'-'``.
    """
    sv = row.get("software_version")
    av = row.get("api_version")
    if not sv and av is None:
        return "-"
    parts: list[str] = []
    if sv:
        parts.append(str(sv))
    if isinstance(av, int):
        parts.append(f"api{av}")
    cell = " · ".join(parts) if parts else "-"
    if isinstance(av, int) and isinstance(hub_api, int) and av != hub_api:
        cell += " ⚠"
    return cell


def _format_fleet_health(row: dict) -> str:
    """BC-063/BC-064: ``'● healthy'`` / ``'⚠ degraded'`` / ``'? unknown'``.

    - Pre-BC-063 scouts (last_health=None): ``'? unknown'`` — scout
      exists but hasn't told us.
    - BC-064: ``'? unknown'`` also when `heartbeat_stale` is true. The
      cached `last_health` label older than ~2× heartbeat interval
      can't be trusted — a scout that stopped heartbeating may well
      have crashed; we don't know its current state.
    - Otherwise: scout's reported value → glyph.
    """
    if row.get("heartbeat_stale"):
        return "? unknown"
    h = row.get("last_health")
    if not h:
        return "? unknown"
    label = "healthy" if h == "ok" else h
    glyph = _HEALTH_GLYPH.get(h, "?")
    return f"{glyph} {label}"


def format_fleet(rows: list[dict], *, hub_api: int | None = None) -> str:
    """BC-055/BC-061/BC-063: render FLEET section (empty = empty string).

    - BC-061: STATUS column (``● active`` / ``⚠ stale`` / ``✗ dead``).
    - BC-063: VERSION, HEALTH, and LAST SCAN columns per scout.

    Pre-BC-061/pre-BC-063 hub responses degrade gracefully:
    missing status → shape without STATUS; missing version/health →
    ``-`` / ``? unknown`` cells.
    """
    if not rows:
        return ""
    has_status = any(r.get("status") for r in rows)
    has_bc063 = any(
        r.get("software_version") or r.get("last_health") or r.get("last_scan_ts") for r in rows
    )
    # BC-064: `LAST SEEN` was a lie — the column read findings.last_seen_ts
    # (watermark of CVE observations), not scouts.last_posted_ts (actual
    # last contact). Column is now `LAST HEARTBEAT`, backed by
    # last_heartbeat_ts (new BC-064 field) with a fallback to
    # last_posted_ts for a pre-BC-064 hub paired with a BC-064 CLI.
    header = ["HOST", "LAST HEARTBEAT"]
    if has_bc063:
        header += ["LAST SCAN", "VERSION", "HEALTH"]
    header += ["OPEN", "TOTAL"]
    if has_status:
        header.append("STATUS")
    body: list[list[str]] = []
    for r in rows:
        heartbeat_ts = r.get("last_heartbeat_ts") or r.get("last_posted_ts") or 0
        cells = [
            r.get("host", "?"),
            _format_age(int(heartbeat_ts)),
        ]
        if has_bc063:
            last_scan_ts = r.get("last_scan_ts")
            cells.append(_format_age(int(last_scan_ts)) if last_scan_ts else "-")
            cells.append(_format_fleet_version(r, hub_api))
            cells.append(_format_fleet_health(r))
        cells.append(str(r.get("open_findings") or 0))
        cells.append(str(r.get("total_findings") or 0))
        if has_status:
            cells.append(_format_fleet_status(r))
        body.append(cells)
    all_rows = [header, *body]
    widths = [max(len(row[i]) for row in all_rows) for i in range(len(header))]
    lines = ["", "FLEET:"]
    for row in all_rows:
        lines.append(
            "  ".join(
                cell.ljust(widths[i]) if i < len(widths) - 1 else cell for i, cell in enumerate(row)
            )
        )
    return "\n".join(lines)


def _format_age(ts: int) -> str:
    if not ts:
        return "-"
    delta = int(time.time()) - int(ts)
    if delta < 0:
        return "-"
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _hub_details(body: dict) -> str:
    checks = body.get("checks", {})
    status = body.get("status", "unknown")
    telegram = checks.get("telegram", "?")
    if status == "ok":
        return f"grype_db ok · telegram {telegram}"
    # Degraded: name the failing checks explicitly.
    failed = [
        f"{k} {v}"
        for k, v in checks.items()
        if k != "last_scan_ts" and v not in ("ok", "polling", "mock")
    ]
    return "degraded: " + ", ".join(failed) if failed else "degraded"


def _scout_details(body: dict) -> str:
    checks = body.get("checks", {})
    status = body.get("status", "unknown")
    images = checks.get("images_seen", 0)
    if status == "ok":
        return f"{images} images · docker_socket ok"
    failed = [
        f"{k} {v}"
        for k, v in checks.items()
        if k not in ("last_scan_ts", "images_seen", "bytes_posted") and v != "ok"
    ]
    return "degraded: " + ", ".join(failed) if failed else "degraded"


def _format_version_cell(body: dict) -> tuple[str, int | None]:
    """BC-062: ``'0.1.0 · api1 · sch2'`` for the VERSION column.

    Accepts both the new (dict) and legacy (string) shapes of
    ``body["version"]``. Returns ``(cell_text, api_int_or_None)``.
    """
    v = body.get("version")
    if isinstance(v, str):
        # Legacy /healthz response — no api/schema split.
        return (v, None)
    if not isinstance(v, dict):
        return ("", None)
    parts: list[str] = []
    software = v.get("software")
    if isinstance(software, str) and software:
        parts.append(software)
    api_raw = v.get("api")
    api_int: int | None = None
    if isinstance(api_raw, int):
        api_int = api_raw
        parts.append(f"api{api_raw}")
    schema = v.get("schema")
    if isinstance(schema, int):
        parts.append(f"sch{schema}")
    return (" · ".join(parts), api_int)


def _down_detail(stack_down_hint: bool) -> str:
    if stack_down_hint:
        return "compose stack not up — try `cd poc && docker compose up -d`"
    return "container not up"


def gather_hub_status(url: str = HUB_HEALTHZ_URL, token: str = HUB_AUTH_TOKEN) -> ServiceStatus:
    try:
        body = fetch_healthz(url, token=token)
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return ServiceStatus(
            name="beaconry-hub",
            state=STATE_DOWN,
            health=HEALTH_NONE,
            last_scan="-",
            details=_down_detail(True),
        )
    status = body.get("status", "unknown")
    checks = body.get("checks", {})
    version_cell, api_int = _format_version_cell(body)
    return ServiceStatus(
        name="beaconry-hub",
        state=STATE_RUNNING,
        health=HEALTH_HEALTHY if status == "ok" else HEALTH_DEGRADED,
        last_scan=_format_age(int(checks.get("last_scan_ts", 0))),
        details=_hub_details(body),
        version=version_cell,
        api_version=api_int,
    )


def gather_scout_status(url: str = SCOUT_HEALTHZ_URL) -> ServiceStatus:
    try:
        body = fetch_healthz(url)
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return ServiceStatus(
            name="beaconry-scout",
            state=STATE_DOWN,
            health=HEALTH_NONE,
            last_scan="-",
            details=_down_detail(False),
        )
    status = body.get("status", "unknown")
    checks = body.get("checks", {})
    version_cell, api_int = _format_version_cell(body)
    return ServiceStatus(
        name="beaconry-scout",
        state=STATE_RUNNING,
        health=HEALTH_HEALTHY if status == "ok" else HEALTH_DEGRADED,
        last_scan=_format_age(int(checks.get("last_scan_ts", 0))),
        details=_scout_details(body),
        version=version_cell,
        api_version=api_int,
    )


def _format_table(statuses: list[ServiceStatus]) -> str:
    # BC-062: VERSION column inserted between HEALTH and LAST SCAN.
    headers = ["NAME", "STATE", "HEALTH", "VERSION", "LAST SCAN", "DETAILS"]
    rows = [[s.name, s.state, s.health, s.version, s.last_scan, s.details] for s in statuses]
    all_rows = [headers, *rows]
    widths = [max(len(row[i]) for row in all_rows) for i in range(len(headers))]
    lines = []
    for row in all_rows:
        lines.append(
            "  ".join(
                cell.ljust(widths[i]) if i < len(widths) - 1 else cell for i, cell in enumerate(row)
            )
        )
    return "\n".join(lines)


def _api_mismatch_line(statuses: list[ServiceStatus]) -> str:
    """BC-062: trailing warning line if any reachable service's api
    differs from CLIENT_API_VERSION. Returns '' when clean."""
    for s in statuses:
        if s.state != STATE_RUNNING or s.api_version is None:
            continue
        if s.api_version != CLIENT_API_VERSION:
            return (
                f"⚠ api mismatch: cli expects api{CLIENT_API_VERSION}, "
                f"{s.name} serves api{s.api_version} — upgrade CLI"
            )
    return ""


def main() -> int:
    hub = gather_hub_status()
    scout = gather_scout_status()
    statuses = [hub, scout]

    # Both down → first row carries the stack-up hint explicitly.
    if hub.state == STATE_DOWN and scout.state == STATE_DOWN:
        hub = ServiceStatus(
            name=hub.name,
            state=hub.state,
            health=hub.health,
            last_scan=hub.last_scan,
            details=_down_detail(True),
        )
        statuses = [hub, scout]

    print(_format_table(statuses))

    # BC-062: API-mismatch warning line (empty when all services agree
    # with the CLI, or when nothing reachable reports a version).
    mismatch = _api_mismatch_line(statuses)
    if mismatch:
        print(mismatch)

    # BC-055: FLEET section below services table.
    # BC-063: pass the hub's own api version so per-scout rows can show a
    # trailing ⚠ when a scout's api_version drifts from the hub's.
    fleet_url = _CLI_CFG.hub_url.rstrip("/") + "/fleet"
    fleet_rows = fetch_fleet(fleet_url, token=HUB_AUTH_TOKEN)
    fleet_text = format_fleet(fleet_rows, hub_api=hub.api_version)
    if fleet_text:
        print(fleet_text)

    if any(s.state == STATE_DOWN for s in statuses):
        return 2
    if any(s.health == HEALTH_DEGRADED for s in statuses):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
