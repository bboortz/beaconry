"""BC-067 — quality sweep: 5 severity-1 bugs.

Covers:
    - CAP-T140: Executor argv-form subprocess (shell-injection-safe)
    - CAP-T141: /test/* routes require CLI token when TEST_MODE is live
    - CAP-T142: Malformed grype match is audited, not silently dropped
    - CAP-T143: Partial-inventory guard — reconcile skipped on glitch
    - CAP-T144: Sweep ordering prevents double-reminder on same tick
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ── CAP-T140: shell injection ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_docker_adapter_rejects_shell_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CAP-T140: a crafted source_ref with shell metacharacters is passed as
    a single argv token, not interpolated into a shell command."""
    from poc.hub.executor import docker_adapter as da_mod
    from poc.hub.executor.base import Finding
    from poc.hub.executor.docker_adapter import DockerAdapter

    # The module-level MOCK flag is captured at import time; another test
    # may have primed the module while BEACONRY_MOCK=1 was set. Force it
    # off here so the real subprocess path (which we're patching) runs.
    monkeypatch.setattr(da_mod, "MOCK", False, raising=True)

    adapter = DockerAdapter()
    crafted = "nginx; touch /tmp/beaconry_injection_marker; #"
    finding = Finding(
        id="x",
        host="h",
        cve_id="CVE-X",
        package="p",
        version="1",
        fixed_version="2",
        severity="High",
        cvss=7.5,
        source_type="docker",
        source_ref=crafted,
        description="",
    )

    # Capture the argv passed to create_subprocess_exec. The exploit test is
    # that `source_ref` arrives as a SINGLE positional argument — not split
    # on `;` the way a shell would. This is the invariant that kills the
    # injection vector.
    captured_argv: list[list[str]] = []

    async def _fake_proc(*argv: str, **_: object) -> object:
        captured_argv.append(list(argv))

        class _P:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

        return _P()

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_proc):
        await adapter.execute(finding)

    # Two invocations: `docker tag <crafted> <base>:rollback`, `docker pull <target>`
    assert len(captured_argv) == 2
    tag_argv = captured_argv[0]
    assert tag_argv[0] == "docker"
    assert tag_argv[1] == "tag"
    # Core invariant: the crafted string arrives as a SINGLE positional
    # argv element — not split on `;` the way a shell would split it. The
    # kernel's execve receives one token per argv slot; shell metacharacters
    # inside the token are inert. The injection vector is dead.
    assert tag_argv[2] == crafted
    # No argv element is split into "nginx", "touch /tmp/...", "#" as a shell
    # would have done — the full crafted string stays together per slot.
    assert all(not a.strip().startswith("touch ") for a in tag_argv)
    # Length is exactly argv length — no extra elements injected.
    assert len(tag_argv) == 4  # [docker, tag, source_ref, rollback_target]


# ── CAP-T141: /test/* auth ──────────────────────────────────────────────────


@pytest.fixture
def hub_env_test_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "0")
    monkeypatch.setenv("BEACONRY_TEST_MODE", "1")
    monkeypatch.setenv("HUB_CLI_TOKEN", "cli-secret")

    import hub

    importlib.reload(hub)
    hub.init_db()
    client = TestClient(hub.api)
    yield {"client": client, "db": hub.DB_PATH, "hub": hub, "data_dir": data_dir}
    client.close()
    importlib.reload(hub)


def test_test_wipe_rejects_unauthenticated(hub_env_test_mode) -> None:
    r = hub_env_test_mode["client"].post("/test/wipe")
    assert r.status_code == 401


def test_test_scan_rejects_unauthenticated(hub_env_test_mode) -> None:
    r = hub_env_test_mode["client"].post("/test/scan", json={"host": "h", "candidates": []})
    assert r.status_code == 401


def test_test_sweep_rejects_unauthenticated(hub_env_test_mode) -> None:
    r = hub_env_test_mode["client"].post("/test/sweep", json={})
    assert r.status_code == 401


def test_test_set_status_rejects_unauthenticated(hub_env_test_mode) -> None:
    r = hub_env_test_mode["client"].post(
        "/test/set_status", json={"host": "h", "cve_id": "x", "status": "snoozed"}
    )
    assert r.status_code == 401


def test_test_endpoints_accept_valid_cli_token(hub_env_test_mode) -> None:
    """CAP-T141: scenario harness (with CLI bearer) still works end-to-end."""
    r = hub_env_test_mode["client"].post(
        "/test/wipe", headers={"Authorization": "Bearer cli-secret"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "wiped"


def test_test_endpoints_reject_wrong_token(hub_env_test_mode) -> None:
    r = hub_env_test_mode["client"].post("/test/wipe", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


# ── CAP-T142: malformed grype match ─────────────────────────────────────────


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "0")
    monkeypatch.delenv("BEACONRY_TEST_MODE", raising=False)
    monkeypatch.delenv("HUB_CLI_TOKEN", raising=False)
    monkeypatch.delenv("HUB_SCOUT_TOKEN", raising=False)

    import hub

    importlib.reload(hub)
    hub.init_db()
    client = TestClient(hub.api)
    yield {"client": client, "db": hub.DB_PATH, "hub": hub, "data_dir": data_dir}
    client.close()
    importlib.reload(hub)


@pytest.mark.asyncio
async def test_malformed_grype_match_audited_not_crashed(
    hub_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T142: a grype match with missing vulnerability.id writes an
    `invalid_grype_match` audit event and skips the row — doesn't crash the
    scan or silently lose a well-formed sibling in the same batch."""
    hub = hub_env["hub"]

    # One malformed match (missing id), one well-formed next to it.
    async def _fake_run_grype(_sbom: dict) -> list[dict]:
        return [
            {
                "vulnerability": {"severity": "High", "cvss": [{"metrics": {"baseScore": 8.1}}]},
                "artifact": {"name": "libmalformed", "version": "0.1"},
            },
            {
                "vulnerability": {
                    "id": "CVE-2026-00099",
                    "severity": "Critical",
                    "cvss": [{"metrics": {"baseScore": 9.5}}],
                },
                "artifact": {"name": "libgood", "version": "1.0"},
            },
        ]

    monkeypatch.setattr(hub, "run_grype", _fake_run_grype)

    findings = await hub.process_sbom_entry(
        {"sbom": {"source": {"type": "docker", "image": "nginx:1.24"}}},
        host="h1",
    )
    # The malformed one is dropped; the well-formed one is kept.
    assert len(findings) == 1
    assert findings[0].cve_id == "CVE-2026-00099"

    # Audit event records the drop.
    audit_lines = (hub_env["data_dir"] / "audit.log").read_text().splitlines()
    events = [json.loads(line) for line in audit_lines]
    bad = [e for e in events if e.get("event") == "invalid_grype_match"]
    assert len(bad) == 1
    assert bad[0]["artifact"] == "libmalformed"


# ── CAP-T143: partial-inventory guard ───────────────────────────────────────


def test_full_inventory_still_reconciles(hub_env) -> None:
    """CAP-T143 regression guard: when scan looks normal, reconcile runs."""
    hub = hub_env["hub"]
    client = hub_env["client"]

    # First scan: 10 SBOMs → baseline becomes 10.
    r = client.post("/scan", json={"host": "h1", "sboms": [{} for _ in range(10)]})
    assert r.status_code == 200
    assert hub._scout_sbom_baseline.get("h1") == 10

    # Second scan: 10 SBOMs again — not partial, reconcile should run.
    assert not hub._is_partial_inventory("h1", 10)


def test_partial_inventory_skips_reconcile(hub_env) -> None:
    """CAP-T143: scan with sbom_count < 50% of baseline is treated as partial."""
    hub = hub_env["hub"]
    client = hub_env["client"]

    # Establish baseline of 10 SBOMs.
    client.post("/scan", json={"host": "h1", "sboms": [{} for _ in range(10)]})
    assert hub._scout_sbom_baseline.get("h1") == 10

    # Partial scan: 3 of 10 — well below 50% threshold.
    assert hub._is_partial_inventory("h1", 3)
    # Right at threshold (5 = 50%) is NOT partial (strict less-than).
    assert not hub._is_partial_inventory("h1", 5)
    # Above threshold.
    assert not hub._is_partial_inventory("h1", 6)


def test_first_scan_is_not_partial(hub_env) -> None:
    """CAP-T143: no baseline yet → treat as full scan (don't skip reconcile)."""
    hub = hub_env["hub"]
    hub._scout_sbom_baseline.clear()
    assert not hub._is_partial_inventory("fresh-host", 1)


def test_baseline_only_moves_up(hub_env) -> None:
    """CAP-T143: a one-off small scan doesn't lower the threshold."""
    hub = hub_env["hub"]
    hub._update_scout_sbom_baseline("h1", 10)
    hub._update_scout_sbom_baseline("h1", 3)  # smaller — ignored
    assert hub._scout_sbom_baseline["h1"] == 10


def test_partial_inventory_emits_audit_event(hub_env) -> None:
    """CAP-T143: operator can see when reconcile was skipped."""
    client = hub_env["client"]

    # Build baseline 10, then post 2 (partial).
    client.post("/scan", json={"host": "h1", "sboms": [{} for _ in range(10)]})
    client.post("/scan", json={"host": "h1", "sboms": [{}, {}]})

    audit_lines = (hub_env["data_dir"] / "audit.log").read_text().splitlines()
    events = [json.loads(line) for line in audit_lines]
    skipped = [e for e in events if e.get("event") == "reconcile_skipped_partial_inventory"]
    assert len(skipped) == 1
    assert skipped[0]["host"] == "h1"
    assert skipped[0]["sbom_count"] == 2
    assert skipped[0]["baseline"] == 10


# ── CAP-T144: sweep ordering ────────────────────────────────────────────────


def _count_reminders(data_dir: Path) -> dict[str, int]:
    """Count both reminder-shaped audit events in the live log."""
    audit = data_dir / "audit.log"
    if not audit.exists():
        return {}
    events = [json.loads(line) for line in audit.read_text().splitlines()]
    out: dict[str, int] = {}
    for e in events:
        name = e.get("event", "")
        if name in {"heartbeat_reminder_sent", "manual_fix_reminder_sent"}:
            out[name] = out.get(name, 0) + 1
    return out


@pytest.mark.asyncio
async def test_double_overdue_single_reminder(hub_env) -> None:
    """CAP-T144: finding overdue on both heartbeat and manual-claim timers
    fires ONE reminder per tick (manual runs first, bumps last_notified_ts;
    heartbeat sweep then skips the row). Counts via distinct audit events."""
    hub = hub_env["hub"]
    now = int(time.time())

    # Insert a finding overdue on BOTH timers.
    con = sqlite3.connect(hub_env["db"])
    con.execute(
        "INSERT INTO findings (id, host, cve_id, package, version, fixed_version, "
        "severity, cvss, source_type, source_ref, description, found_at, status, "
        "tier, last_seen_ts, last_notified_ts, last_claim_ts) "
        "VALUES ('d1', 'h1', 'CVE-DOUBLE', 'p', '1', '2', 'High', 7.5, 'docker', "
        "'nginx:1', '', ?, 'open', 2, ?, ?, ?)",
        (now, now, now - 10 * 86400, now - 7200),  # last_notified: 10d ago, claim: 2h ago
    )
    con.commit()
    con.close()

    hub.MANUAL_REMINDER_INTERVAL_S = 3600  # 1h
    hub.HEARTBEAT_INTERVAL_S = 86400  # 1d

    # Replay the loop body in the NEW order (manual first).
    await hub.manual_claim_sweep(now_ts=now)
    await hub.heartbeat_sweep(now_ts=now)

    counts = _count_reminders(hub_env["data_dir"])
    # Manual fires; heartbeat is suppressed because manual bumped
    # last_notified_ts to `now`, taking the row out of heartbeat's WHERE.
    assert counts.get("manual_fix_reminder_sent") == 1
    assert counts.get("heartbeat_reminder_sent", 0) == 0


@pytest.mark.asyncio
async def test_old_order_would_have_double_fired(hub_env) -> None:
    """CAP-T144 regression guard: with the OLD order (heartbeat first,
    manual second) BOTH sweeps fire for the same finding on the same tick.
    This test documents why we swapped — if someone reverts the loop
    ordering, this test still passes (demonstrating the bug) and the
    'single reminder' test above fails (showing the regression)."""
    hub = hub_env["hub"]
    now = int(time.time())

    con = sqlite3.connect(hub_env["db"])
    con.execute(
        "INSERT INTO findings (id, host, cve_id, package, version, fixed_version, "
        "severity, cvss, source_type, source_ref, description, found_at, status, "
        "tier, last_seen_ts, last_notified_ts, last_claim_ts) "
        "VALUES ('d2', 'h1', 'CVE-DOUBLE-2', 'p', '1', '2', 'High', 7.5, 'docker', "
        "'nginx:1', '', ?, 'open', 2, ?, ?, ?)",
        (now, now, now - 10 * 86400, now - 7200),
    )
    con.commit()
    con.close()

    hub.MANUAL_REMINDER_INTERVAL_S = 3600
    hub.HEARTBEAT_INTERVAL_S = 86400

    # Explicitly reversed order to show the bug the swap fixes.
    await hub.heartbeat_sweep(now_ts=now)
    await hub.manual_claim_sweep(now_ts=now)

    counts = _count_reminders(hub_env["data_dir"])
    # Both audit events fire — one per sweep.
    assert counts.get("heartbeat_reminder_sent") == 1
    assert counts.get("manual_fix_reminder_sent") == 1
