"""BC-013 `make status` — Docker-compose-ps-like health table for beaconry.

One row per container. Columns: NAME · STATE · HEALTH · LAST SCAN · DETAILS.

Both services queried uniformly via HTTP /healthz (hub on localhost:8000,
scout on localhost:18001 since BC-101 — was 8001 pre-BC-101; the operator systemd stack took 8001 so dev scout rebinds). No `docker exec` fallback. All error
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
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

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
SCOUT_HEALTHZ_URL = os.environ.get("BEACONRY_SCOUT_HEALTHZ_URL", "http://localhost:18001/healthz")
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
    # BC-118: the URL that was probed for this row — surfaced in the
    # top table so the operator can tell WHICH hub / WHICH scout the
    # row refers to (dev-compose localhost? remote hetzner? systemd on
    # port 8001?). Always populated at construction time.
    probed_url: str = ""


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


def format_fleet(
    rows: list[dict],
    *,
    hub_api: int | None = None,
    local_hostname: str | None = None,
) -> str:
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
    # BC-092: role column rendered iff any row carries a non-default role.
    # Keeps `make status` clean for single-host operators who never flagged
    # anything; shows up as soon as the first staging/production scout joins.
    has_role = any((r.get("role") or "workstation") != "workstation" for r in rows)
    header = ["HOST", "LAST HEARTBEAT"]
    if has_bc063:
        header += ["LAST SCAN", "VERSION", "HEALTH"]
    header += ["OPEN", "TOTAL"]
    if has_status:
        header.append("STATUS")
    if has_role:
        header.append("ROLE")
    body: list[list[str]] = []
    for r in rows:
        heartbeat_ts = r.get("last_heartbeat_ts") or r.get("last_posted_ts") or 0
        host_label = r.get("host", "?")
        # BC-118: mark the row whose host matches the CLI's running
        # hostname with " (you)" so operators understand the
        # two-table structure as one coherent thing.
        if local_hostname and host_label == local_hostname:
            host_label = f"{host_label} (you)"
        cells = [
            host_label,
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
        if has_role:
            # Single-letter glyph: W/S/P. BC-092 defines three roles; fall
            # back to raw string if a future ring adds a value we don't map.
            role = (r.get("role") or "workstation").lower()
            cells.append({"workstation": "W", "staging": "S", "production": "P"}.get(role, role))
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
    # BC-122: surface the integrity-mismatch counter even on an "ok" hub
    # — a mismatch doesn't flip the top-level status (pipeline is
    # fail-open by design) but operator must see it. Mirrors the
    # BC-120 `state_db degraded` surfacing on the scout side.
    extras = []
    mismatches = int(checks.get("pipeline_integrity_mismatches") or 0)
    if mismatches > 0:
        extras.append(f"⚠ integrity: {mismatches} mismatches")
    if status == "ok":
        base = f"grype_db ok · telegram {telegram}"
        return " · ".join([base, *extras]) if extras else base
    # Degraded: name the failing checks explicitly.
    failed = [
        f"{k} {v}"
        for k, v in checks.items()
        if k
        not in (
            "last_scan_ts",
            # BC-122: counter, surfaced separately in extras above.
            "pipeline_integrity_mismatches",
        )
        and v not in ("ok", "polling", "mock")
    ]
    body_text = "degraded: " + ", ".join(failed) if failed else "degraded"
    return " · ".join([body_text, *extras]) if extras else body_text


def _scout_details(body: dict) -> str:
    checks = body.get("checks", {})
    status = body.get("status", "unknown")
    images = checks.get("images_seen", 0)
    # BC-120: surface state_db_degraded even when the top-level status is
    # "ok" — the scan path still works but the retry queue is ephemeral,
    # which operator must know about to apply the host-side fix.
    extras = []
    if checks.get("state_db_degraded"):
        extras.append("state_db degraded")
    if status == "ok":
        base = f"{images} images · docker_socket ok"
        return " · ".join([base, *extras]) if extras else base
    failed = [
        f"{k} {v}"
        for k, v in checks.items()
        if k
        not in (
            "last_scan_ts",
            "images_seen",
            "bytes_posted",
            # BC-120: these two aren't ok/unavailable strings, drop from the generic failed-check list.
            "state_db_degraded",
            "state_db_effective_path",
            "pending_scans_queue_depth",
            "docker_events_reconnects",
            "docker_events_last_reconnect_ts",
        )
        and v != "ok"
    ]
    body_text = "degraded: " + ", ".join(failed) if failed else "degraded"
    return " · ".join([body_text, *extras]) if extras else body_text


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


# BC-118: Dev-compose detection. On hosts that only run systemd scouts
# (port 8001) + never had the dev-compose stack checked out, the
# `localhost:18001` probe will fail with connection-refused — correctly,
# but confusingly. We distinguish three down causes:
#   1. dev-compose not configured on this host (no docker-compose.yml here,
#      no beaconry-dev-scout container) → inherent, not a problem
#   2. compose stack not up (docker-compose.yml present, container missing)
#      → operator should `docker compose up -d` if they want it
#   3. everything else → genuine down state (unreachable at <url>)


def _repo_root() -> Path | None:
    """BC-118: locate the beaconry repo root (contains poc/docker-compose.yml).

    Walks up from __file__. Returns None if not found. Tests can
    monkeypatch to point elsewhere.
    """
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        if (p / "poc" / "docker-compose.yml").is_file():
            return p
    return None


def _dev_compose_configured() -> bool:
    """BC-118: `poc/docker-compose.yml` exists locally."""
    root = _repo_root()
    return root is not None


def _dev_scout_container_exists() -> bool:
    """BC-118: a container named `beaconry-dev-scout` exists (any state).

    Uses `docker ps -a --filter name=beaconry-dev-scout --format {{.Names}}`.
    Returns False on any docker error (docker not installed, daemon down,
    etc.) — the caller treats "unknown" as "not up" which matches the
    operator's experience.
    """
    try:
        out = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "name=beaconry-dev-scout",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "beaconry-dev-scout" in out.stdout


def _scout_down_detail(url: str) -> str:
    """BC-118: classify a scout probe failure into one of three messages."""
    if not _dev_compose_configured():
        return "down (dev-compose not configured on this host)"
    if not _dev_scout_container_exists():
        return "down (compose stack not up — try `cd poc && docker compose up -d`)"
    return f"down (unreachable at {url})"


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
            probed_url=url,  # BC-118
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
        probed_url=url,  # BC-118
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
            details=_scout_down_detail(url),  # BC-118: contextual message
            probed_url=url,  # BC-118
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
        probed_url=url,  # BC-118
    )


def _format_table(statuses: list[ServiceStatus]) -> str:
    # BC-062: VERSION column inserted between HEALTH and LAST SCAN.
    # BC-118: URL column inserted between NAME and STATE so the
    # operator can see WHICH endpoint each row probed. Rows that
    # never resolved a URL (shouldn't happen post-BC-118) render as
    # empty string in the URL cell.
    headers = ["NAME", "URL", "STATE", "HEALTH", "VERSION", "LAST SCAN", "DETAILS"]
    rows = [
        [s.name, s.probed_url, s.state, s.health, s.version, s.last_scan, s.details]
        for s in statuses
    ]
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
            probed_url=hub.probed_url,  # BC-118: preserve
        )
        statuses = [hub, scout]

    # BC-118: header line so the operator knows WHICH machine the CLI
    # is reporting from. `socket.gethostname()` matches scout's
    # HOST_NAME default — so the FLEET's `(you)` marker lines up.
    local_hostname = socket.gethostname()
    print(f"Running on: {local_hostname}")
    print()
    print(_format_table(statuses))

    # BC-062: API-mismatch warning line (empty when all services agree
    # with the CLI, or when nothing reachable reports a version).
    mismatch = _api_mismatch_line(statuses)
    if mismatch:
        print(mismatch)

    # BC-055: FLEET section below services table.
    # BC-063: pass the hub's own api version so per-scout rows can show a
    # trailing ⚠ when a scout's api_version drifts from the hub's.
    # BC-118: pass local_hostname so the matching row gets a (you) marker.
    fleet_url = _CLI_CFG.hub_url.rstrip("/") + "/fleet"
    fleet_rows = fetch_fleet(fleet_url, token=HUB_AUTH_TOKEN)
    fleet_text = format_fleet(fleet_rows, hub_api=hub.api_version, local_hostname=local_hostname)
    if fleet_text:
        print(fleet_text)

    if any(s.state == STATE_DOWN for s in statuses):
        return 2
    if any(s.health == HEALTH_DEGRADED for s in statuses):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
