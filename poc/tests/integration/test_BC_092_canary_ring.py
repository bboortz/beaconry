"""BC-092 — Canary ring (scout role + hub production-fix gate).

Covers:
    - CAP-T188: scout advertises SCOUT_HOST_ROLE on heartbeat + /scan
    - CAP-T189: hub canary-ring gate on production fixes
    - CAP-T190: /fleet exposes role + `make status` renders ROLE column
"""

from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import hub
import pytest

from poc.hub.executor.base import Finding

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setattr(hub, "CANARY_RING_ENABLED", True)
    monkeypatch.setattr(hub, "CANARY_PRODUCTION_FORCE_ALLOW", False)
    monkeypatch.setattr(hub, "FIX_ENABLED", True)
    hub.init_db()
    return data_dir


def _finding(host: str = "prod1", cve_id: str = "CVE-2026-X") -> Finding:
    return Finding(
        id=f"{host}-{cve_id}".encode().hex()[:16],
        host=host,
        cve_id=cve_id,
        package="libc",
        version="1.0",
        fixed_version="1.1",
        severity="High",
        cvss=8.1,
        source_type="docker",
        source_ref="nginx:1.24",
        description="fixture",
        tier=2,
        kev=False,
    )


def _seed_scout(db_path: Path, host: str, role: str = "workstation") -> None:
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO scouts (host, first_seen_ts, last_posted_ts, last_sbom_count, role) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(host) DO UPDATE SET role = excluded.role",
        (host, 1000, 1000, 1, role),
    )
    con.commit()
    con.close()


def _seed_finding(db_path: Path, fid: str, host: str, cve_id: str, status: str = "open") -> None:
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO findings (id, host, cve_id, package, version, fixed_version, "
        "severity, cvss, source_type, source_ref, description, tier, kev, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            fid,
            host,
            cve_id,
            "libc",
            "1.0",
            "1.1",
            "High",
            8.1,
            "docker",
            "nginx:1.24",
            "fixture",
            2,
            0,
            status,
        ),
    )
    con.commit()
    con.close()


# ── Schema migration ─────────────────────────────────────────────────────────


def test_schema_v7_adds_role_column(db_env: Path) -> None:
    con = sqlite3.connect(db_env / "beaconry.db")
    cols = {row[1] for row in con.execute("PRAGMA table_info(scouts)").fetchall()}
    user_version = con.execute("PRAGMA user_version").fetchone()[0]
    con.close()
    assert "role" in cols
    assert user_version == 9  # BC-104 bumped 8 → 9 (pending_notifications table)


# ── _normalize_role ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "workstation"),
        ("", "workstation"),
        ("workstation", "workstation"),
        ("STAGING", "staging"),  # normalised to lowercase
        ("production", "production"),
        ("bogus", "workstation"),  # invalid → clamp
        ("  staging  ", "staging"),  # whitespace stripped
    ],
)
def test_normalize_role(raw: str | None, expected: str) -> None:
    assert hub._normalize_role(raw) == expected


def test_invalid_role_clamps_workstation(db_env: Path) -> None:
    """Scenario: invalid role from scout clamps to workstation on persistence."""
    hub.record_scout_post("hostA", now=1000, sbom_count=1, role="nonsense")
    con = sqlite3.connect(db_env / "beaconry.db")
    role = con.execute("SELECT role FROM scouts WHERE host='hostA'").fetchone()[0]
    con.close()
    assert role == "workstation"


# ── Gate scenarios ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_production_blocked_without_staging(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 1: production fix blocked when no staging has verified."""
    _seed_scout(db_env / "beaconry.db", "prod1", role="production")
    _seed_finding(db_env / "beaconry.db", "fid1", "prod1", "CVE-2026-X")
    query = AsyncMock()
    # Make fix dispatcher a no-op spy so we can assert it wasn't called.
    exec_mock = AsyncMock()
    monkeypatch.setattr(hub.EXECUTOR, "execute", exec_mock)
    monkeypatch.setattr(hub, "EXECUTOR_NAME", "docker")
    await hub._handle_fix(query, _finding("prod1"), "fid1", parts=["fix", "fid1"])
    exec_mock.assert_not_called()
    # Reply names the block.
    args = query.edit_message_text.call_args.args
    assert "Canary gate" in args[0]
    # Audit event fired.
    audit = db_env / "audit.log"
    events = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
    blocked = [e for e in events if e.get("event") == "canary_gate_blocked"]
    assert len(blocked) == 1
    assert blocked[0]["cve_id"] == "CVE-2026-X"
    assert blocked[0]["host"] == "prod1"


@pytest.mark.asyncio
async def test_production_allowed_with_staging_verified(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 2: production fix proceeds when staging has verified."""
    _seed_scout(db_env / "beaconry.db", "prod1", role="production")
    _seed_scout(db_env / "beaconry.db", "stage1", role="staging")
    _seed_finding(db_env / "beaconry.db", "fid1", "prod1", "CVE-2026-X")
    _seed_finding(db_env / "beaconry.db", "fid2", "stage1", "CVE-2026-X", status="fixed_verified")
    query = AsyncMock()
    monkeypatch.setattr(hub, "EXECUTOR_NAME", "manual")
    monkeypatch.setattr(hub, "_record_manual_claim", lambda *a, **kw: None)
    await hub._handle_fix(query, _finding("prod1"), "fid1", parts=["fix", "fid1"])
    audit = db_env / "audit.log"
    events = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
    passed = [e for e in events if e.get("event") == "canary_gate_passed"]
    assert len(passed) == 1
    assert passed[0]["staging_hosts"] == ["stage1"]


@pytest.mark.asyncio
async def test_workstation_bypasses_gate(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 3: non-production host bypasses the gate entirely."""
    _seed_scout(db_env / "beaconry.db", "lap", role="workstation")
    _seed_finding(db_env / "beaconry.db", "fid1", "lap", "CVE-2026-X")
    query = AsyncMock()
    monkeypatch.setattr(hub, "EXECUTOR_NAME", "manual")
    monkeypatch.setattr(hub, "_record_manual_claim", lambda *a, **kw: None)
    await hub._handle_fix(query, _finding("lap"), "fid1", parts=["fix", "fid1"])
    audit = db_env / "audit.log"
    events = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
    gate_events = [e for e in events if e.get("event", "").startswith("canary_gate_")]
    # No gate events — the bypass path doesn't audit (no gate was involved).
    assert gate_events == []


@pytest.mark.asyncio
async def test_gate_disabled_env(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 4: BEACONRY_CANARY_RING_ENABLED=0 → gate skipped."""
    monkeypatch.setattr(hub, "CANARY_RING_ENABLED", False)
    _seed_scout(db_env / "beaconry.db", "prod1", role="production")
    _seed_finding(db_env / "beaconry.db", "fid1", "prod1", "CVE-2026-X")
    query = AsyncMock()
    monkeypatch.setattr(hub, "EXECUTOR_NAME", "manual")
    monkeypatch.setattr(hub, "_record_manual_claim", lambda *a, **kw: None)
    await hub._handle_fix(query, _finding("prod1"), "fid1", parts=["fix", "fid1"])
    audit = db_env / "audit.log"
    events = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
    gate_events = [e for e in events if e.get("event", "").startswith("canary_gate_")]
    assert gate_events == []


@pytest.mark.asyncio
async def test_force_allow_bypasses_but_audits(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 5: force-allow bypasses the staging check with a distinct audit."""
    monkeypatch.setattr(hub, "CANARY_PRODUCTION_FORCE_ALLOW", True)
    _seed_scout(db_env / "beaconry.db", "prod1", role="production")
    _seed_finding(db_env / "beaconry.db", "fid1", "prod1", "CVE-2026-X")
    query = AsyncMock()
    monkeypatch.setattr(hub, "EXECUTOR_NAME", "manual")
    monkeypatch.setattr(hub, "_record_manual_claim", lambda *a, **kw: None)
    await hub._handle_fix(query, _finding("prod1"), "fid1", parts=["fix", "fid1"])
    audit = db_env / "audit.log"
    events = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
    forced = [e for e in events if e.get("event") == "canary_gate_force_allowed"]
    assert len(forced) == 1
    assert "FORCE_ALLOW" in forced[0]["reason"]


# ── Scout-side heartbeat payload ────────────────────────────────────────────


def test_scout_advertises_role_on_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 6: scout's heartbeat payload includes role.

    Minimal direct unit on the payload-construction half of the contract."""
    monkeypatch.setenv("SCOUT_HOST_ROLE", "staging")
    import poc.scout.scout as scout_mod

    importlib.reload(scout_mod)
    # HOST_ROLE normalised to lowercase on load.
    assert scout_mod.HOST_ROLE == "staging"
    # Reset for subsequent tests.
    monkeypatch.delenv("SCOUT_HOST_ROLE")
    importlib.reload(scout_mod)


def test_legacy_scout_without_role_defaults_workstation(db_env: Path) -> None:
    """Scenario 7: hub receives payload without role → DB falls back to default."""
    # record_scout_post called with role=None — simulates pre-BC-092 scout
    # that omits the key entirely.
    hub.record_scout_post("legacy-host", now=1000, sbom_count=1, role=None)
    con = sqlite3.connect(db_env / "beaconry.db")
    role = con.execute("SELECT role FROM scouts WHERE host='legacy-host'").fetchone()[0]
    con.close()
    assert role == "workstation"


# ── /fleet exposes role + make status renders column ────────────────────────


@pytest.mark.asyncio
async def test_fleet_endpoint_includes_role(db_env: Path) -> None:
    """CAP-T190 half: /fleet rows carry the role field."""
    _seed_scout(db_env / "beaconry.db", "prod1", role="production")
    _seed_scout(db_env / "beaconry.db", "stage1", role="staging")
    # Call via require_cli_token bypass — directly await the handler.
    fleet = await hub.http_fleet()
    roles = {r["host"]: r["role"] for r in fleet}
    assert roles == {"prod1": "production", "stage1": "staging"}


def test_status_renders_role_column(db_env: Path) -> None:
    """CAP-T190: _format_fleet_table renders ROLE column when any non-workstation role exists."""
    from poc.scripts import status as status_mod

    rows = [
        {
            "host": "prod1",
            "role": "production",
            "last_heartbeat_ts": 1000,
            "open_findings": 2,
            "total_findings": 5,
            "status": "active",
        },
        {
            "host": "stage1",
            "role": "staging",
            "last_heartbeat_ts": 1000,
            "open_findings": 0,
            "total_findings": 3,
            "status": "active",
        },
    ]
    out = status_mod.format_fleet(rows, hub_api=1)
    assert "ROLE" in out
    # Glyphs rendered as last cell of each row.
    lines = out.splitlines()
    prod_line = next(ln for ln in lines if ln.startswith("prod1"))
    stage_line = next(ln for ln in lines if ln.startswith("stage1"))
    assert prod_line.rstrip().endswith("P")
    assert stage_line.rstrip().endswith("S")


def test_status_hides_role_column_for_all_workstation(db_env: Path) -> None:
    """CAP-T190: ROLE column hidden when every scout is default workstation."""
    from poc.scripts import status as status_mod

    rows = [
        {
            "host": "lap",
            "role": "workstation",
            "last_heartbeat_ts": 1000,
            "open_findings": 0,
            "total_findings": 0,
            "status": "active",
        }
    ]
    out = status_mod.format_fleet(rows, hub_api=1)
    assert "ROLE" not in out
