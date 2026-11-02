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


def _format_fleet_status(row: dict) -> str:
    """BC-061: '● active' / '⚠ stale' / '✗ dead' for the STATUS column.
    Missing status (pre-BC-061 hub response) renders as empty."""
    status = row.get("status")
    if not status:
        return ""
    return f"{_STATUS_GLYPH.get(status, '?')} {status}"


def format_fleet(rows: list[dict]) -> str:
    """BC-055/BC-061: render FLEET section (empty = empty string).

    BC-061: adds a STATUS column when the hub response carries liveness
    (``status`` field). Pre-BC-061 hub responses render the old 4-column
    shape for back-compat.
    """
    if not rows:
        return ""
    has_status = any(r.get("status") for r in rows)
    header = ["HOST", "LAST SEEN", "OPEN", "TOTAL"]
    if has_status:
        header.append("STATUS")
    body = []
    for r in rows:
        cells = [
            r.get("host", "?"),
            _format_age(int(r.get("last_seen_ts") or 0)),
            str(r.get("open_findings") or 0),
            str(r.get("total_findings") or 0),
        ]
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
    return ServiceStatus(
        name="beaconry-hub",
        state=STATE_RUNNING,
        health=HEALTH_HEALTHY if status == "ok" else HEALTH_DEGRADED,
        last_scan=_format_age(int(checks.get("last_scan_ts", 0))),
        details=_hub_details(body),
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
    return ServiceStatus(
        name="beaconry-scout",
        state=STATE_RUNNING,
        health=HEALTH_HEALTHY if status == "ok" else HEALTH_DEGRADED,
        last_scan=_format_age(int(checks.get("last_scan_ts", 0))),
        details=_scout_details(body),
    )


def _format_table(statuses: list[ServiceStatus]) -> str:
    headers = ["NAME", "STATE", "HEALTH", "LAST SCAN", "DETAILS"]
    rows = [[s.name, s.state, s.health, s.last_scan, s.details] for s in statuses]
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

    # BC-055: FLEET section below services table.
    fleet_url = _CLI_CFG.hub_url.rstrip("/") + "/fleet"
    fleet_rows = fetch_fleet(fleet_url, token=HUB_AUTH_TOKEN)
    fleet_text = format_fleet(fleet_rows)
    if fleet_text:
        print(fleet_text)

    if any(s.state == STATE_DOWN for s in statuses):
        return 2
    if any(s.health == HEALTH_DEGRADED for s in statuses):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
