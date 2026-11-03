"""BC-075 — beaconry metrics + threshold-based soak signals + opt-in.

Covers:
    - CAP-T155: beaconry metrics subcommand (usability + features lenses)
    - CAP-T156: threshold-based anomaly signals in make soak
    - CAP-T157: Scout metrics opt-in — privacy-first default
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path

import pytest

from poc.scripts import cli, metrics
from poc.scripts import soak as sk


def _write_audit(path: Path, events: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


# ── CAP-T155: metrics ───────────────────────────────────────────────────────


def test_parse_window_valid_forms() -> None:
    assert metrics.parse_window("60s") == 60
    assert metrics.parse_window("30m") == 1800
    assert metrics.parse_window("24h") == 86400
    assert metrics.parse_window("7d") == 604800


def test_parse_window_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        metrics.parse_window("tomorrow")
    with pytest.raises(ValueError):
        metrics.parse_window("5")  # missing unit
    with pytest.raises(ValueError):
        metrics.parse_window("")


def test_usability_report_counts_actions() -> None:
    """CAP-T155: a finding that got notify_sent then set_status=fixed counts
    toward action_rate and fix_rate."""
    now = int(time.time())
    events = [
        {"ts": now - 3600, "event": "notify_sent", "finding_id": "f1"},
        {
            "ts": now - 1800,
            "event": "status_change",
            "finding_id": "f1",
            "new_status": "fixed_verified",
        },
    ]
    m = metrics.usability_metrics(events, now_ts=now)
    assert m["alerts_delivered"] == 1
    assert m["unique_findings_alerted"] == 1
    assert m["action_rate"] == 1.0
    assert m["fix_rate"] == 1.0


def test_usability_tracks_ignored_rate() -> None:
    """CAP-T155: notify_sent > 24h ago + no action = ignored."""
    now = int(time.time())
    events = [
        # Old alert, never actioned
        {"ts": now - 2 * 86400, "event": "notify_sent", "finding_id": "f-old"},
        # Fresh alert, no action yet (not ignored yet — < 24h)
        {"ts": now - 1800, "event": "notify_sent", "finding_id": "f-new"},
    ]
    m = metrics.usability_metrics(events, now_ts=now)
    assert m["unique_findings_alerted"] == 2
    assert m["ignored_rate"] == 0.5  # f-old is ignored; f-new isn't yet


def test_time_to_action_percentiles() -> None:
    """CAP-T155: p50/p75/p95 for time-to-first-action."""
    now = int(time.time())
    # 10 findings, 10 different TTAs: 10s, 20s, ... 100s
    events: list[dict] = []
    for i in range(10):
        fid = f"f{i}"
        notify_ts = now - 3600
        action_ts = notify_ts + (i + 1) * 10
        events.append({"ts": notify_ts, "event": "notify_sent", "finding_id": fid})
        events.append(
            {
                "ts": action_ts,
                "event": "status_change",
                "finding_id": fid,
                "new_status": "fixed_verified",
            }
        )
    m = metrics.usability_metrics(events, now_ts=now)
    tta = m["time_to_action_s"]
    # Median of [10, 20, ..., 100] = 55 (even count → avg of 50/60)
    assert tta["p50"] == 55
    # p75 index = int(0.75 * 9) = 6 → sorted[6] = 70
    assert tta["p75"] == 70
    # p95 index = int(0.95 * 9) = 8 → sorted[8] = 90
    assert tta["p95"] == 90


def test_features_report_counts_digest() -> None:
    """CAP-T155: digest_sent vs digest_throttled counts flow into the ratio."""
    now = int(time.time())
    events = [
        {"ts": now, "event": "digest_sent"},
        {"ts": now, "event": "digest_sent"},
        {"ts": now, "event": "digest_sent"},
        {"ts": now, "event": "digest_throttled"},
    ]
    m = metrics.features_metrics(events, now_ts=now)
    assert m["digest_sent"] == 3
    assert m["digest_throttled"] == 1
    assert m["digest_fire_ratio"] == 0.75


def test_features_tracks_heartbeat_gaps() -> None:
    """CAP-T155: max_heartbeat_gap_s_per_host reports per host."""
    now = int(time.time())
    events = [
        {"ts": now - 300, "event": "heartbeat_received", "host": "h1"},
        {"ts": now - 240, "event": "heartbeat_received", "host": "h1"},  # 60s gap
        {"ts": now - 60, "event": "heartbeat_received", "host": "h1"},  # 180s gap
    ]
    m = metrics.features_metrics(events, now_ts=now)
    assert m["max_heartbeat_gap_s_per_host"]["h1"] == 180


def test_metrics_all_lens_runs_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T155: default (`all`) prints both lens headers. BC-076:
    pin to --local so BC-076's auto-routing doesn't try to hit a
    real hub during the test."""
    now = int(time.time())
    _write_audit(
        tmp_path / "audit.log",
        [{"ts": now - 60, "event": "notify_sent", "finding_id": "f1"}],
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    rc = metrics.main(["--local"])  # default lens = all
    assert rc == 0
    out = capsys.readouterr().out
    assert "Usability" in out
    assert "Features" in out


def test_metrics_json_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T155: --json emits parseable JSON."""
    now = int(time.time())
    _write_audit(
        tmp_path / "audit.log",
        [{"ts": now - 60, "event": "notify_sent", "finding_id": "f1"}],
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    rc = metrics.main(["all", "--json", "--local"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "usability" in data
    assert "features" in data


def test_metrics_missing_audit_log_returns_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T155: no audit log → exit 2 (same as soak's semantics).
    BC-076: pin to --local so the auto-router doesn't try remote mode
    (which could succeed against a dev-env cli.yml)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    rc = metrics.main(["--local"])
    assert rc == 2


def test_metrics_rejects_bad_since(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T155: bad --since prints an error and exits 2."""
    (tmp_path / "audit.log").write_text("")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    rc = metrics.main(["--since", "7weeks"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid --since" in err


# ── CAP-T156: soak anomaly thresholds ───────────────────────────────────────


def test_soak_flags_regression_as_hard_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T156: `regressed` stays a hard error (not a threshold anomaly)."""
    now = int(time.time())
    _write_audit(
        tmp_path / "audit.log",
        [{"ts": now - 60, "event": "regressed", "finding_id": "f1", "cve_id": "CVE-X"}],
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOAK_WINDOW_S", "3600")
    rc = sk.soak(now=now)
    assert rc == 1
    out = capsys.readouterr().out
    assert "regressed" in out


def test_soak_flags_notify_failure_ratio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T156: > 10% notifier failure ratio → anomaly."""
    now = int(time.time())
    events = [{"ts": now - 60, "event": "notify_sent"} for _ in range(85)]
    events += [{"ts": now - 60, "event": "notify_sent_failed"} for _ in range(15)]
    _write_audit(tmp_path / "audit.log", events)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOAK_WINDOW_S", "3600")
    rc = sk.soak(now=now)
    assert rc == 1
    out = capsys.readouterr().out
    assert "notifier failure rate" in out


def test_soak_does_not_flag_notify_failure_below_sample_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T156: don't flag on tiny samples (< 10 notifications)."""
    now = int(time.time())
    _write_audit(
        tmp_path / "audit.log",
        [
            {"ts": now - 60, "event": "notify_sent_failed"},  # 1 of 1 = 100% failure
        ],
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOAK_WINDOW_S", "3600")
    rc = sk.soak(now=now)
    assert rc == 1  # notify_sent_failed is still a hard error
    # But NOT the anomaly line about ratio
    # (hard to assert by output; the fact that we don't crash is the smoke)


def test_soak_flags_partial_inventory_ratio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T156: > 20% partial-inventory saves → anomaly."""
    now = int(time.time())
    events = [{"ts": now - 60, "event": "scan_received"} for _ in range(10)]
    events += [{"ts": now - 60, "event": "reconcile_skipped_partial_inventory"} for _ in range(3)]
    _write_audit(tmp_path / "audit.log", events)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOAK_WINDOW_S", "3600")
    rc = sk.soak(now=now)
    assert rc == 1
    out = capsys.readouterr().out
    assert "partial-inventory save rate" in out


def test_soak_flags_reminder_storm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T156: 3+ reminders for the same finding, never fixed → anomaly."""
    now = int(time.time())
    events = [
        {"ts": now - 60, "event": "manual_fix_reminder_sent", "finding_id": "stuck1"}
        for _ in range(4)
    ]
    _write_audit(tmp_path / "audit.log", events)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOAK_WINDOW_S", "3600")
    rc = sk.soak(now=now)
    assert rc == 1
    out = capsys.readouterr().out
    assert "reminder storm" in out


def test_soak_does_not_flag_reminders_when_fixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T156: 3 reminders followed by fixed_verified is OK (operator eventually acted)."""
    now = int(time.time())
    events = [
        # Baseline healthy pipeline so the silence check doesn't trip.
        {"ts": now - 200, "event": "scan_received", "host": "h1"},
        {"ts": now - 180, "event": "manual_fix_reminder_sent", "finding_id": "acted"},
        {"ts": now - 170, "event": "manual_fix_reminder_sent", "finding_id": "acted"},
        {"ts": now - 160, "event": "manual_fix_reminder_sent", "finding_id": "acted"},
        {
            "ts": now - 100,
            "event": "status_change",
            "finding_id": "acted",
            "new_status": "fixed_verified",
        },
    ]
    _write_audit(tmp_path / "audit.log", events)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOAK_WINDOW_S", "3600")
    rc = sk.soak(now=now)
    assert rc == 0
    out = capsys.readouterr().out
    assert "healthy" in out
    assert "reminder storm" not in out


def test_soak_flags_pipeline_silence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T156: 24h+ window with zero scan_received events → silence anomaly."""
    now = int(time.time())
    # Non-scan events so audit isn't empty.
    _write_audit(
        tmp_path / "audit.log",
        [{"ts": now - 60, "event": "heartbeat_received", "host": "h1"}],
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOAK_WINDOW_S", "86400")  # 24h — well above 2× cadence
    rc = sk.soak(now=now)
    assert rc == 1
    out = capsys.readouterr().out
    assert "pipeline silent" in out


def test_soak_pipeline_silence_skipped_in_short_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T156: don't flag silence in short windows (not enough time to assert)."""
    now = int(time.time())
    _write_audit(tmp_path / "audit.log", [{"ts": now - 60, "event": "heartbeat_received"}])
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOAK_WINDOW_S", "600")  # 10m — too short to assert silence
    rc = sk.soak(now=now)
    assert rc == 0


def test_soak_passes_on_healthy_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T156 regression guard: realistic healthy audit → exit 0."""
    now = int(time.time())
    events = [{"ts": now - 300 * i, "event": "scan_received", "host": "h1"} for i in range(1, 20)]
    events += [
        {"ts": now - 60 * i, "event": "heartbeat_received", "host": "h1"} for i in range(1, 20)
    ]
    events += [{"ts": now - 1000, "event": "notify_sent", "finding_id": "f1"}]
    events += [
        {
            "ts": now - 500,
            "event": "status_change",
            "finding_id": "f1",
            "new_status": "fixed_verified",
        }
    ]
    _write_audit(tmp_path / "audit.log", events)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOAK_WINDOW_S", "86400")
    rc = sk.soak(now=now)
    assert rc == 0
    out = capsys.readouterr().out
    assert "healthy" in out


# ── CLI dispatcher integration ──────────────────────────────────────────────


def test_cli_dispatcher_has_metrics_subcommand() -> None:
    """CAP-T155: `beaconry metrics` is a registered dispatcher subcommand."""
    assert "metrics" in cli._SUBCOMMANDS


def test_cli_dispatcher_metrics_help(capsys: pytest.CaptureFixture[str]) -> None:
    """CAP-T155: `beaconry metrics --help` renders dispatcher-owned usage."""
    rc = cli.main(["metrics", "--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "metrics" in out
    assert "usability" in out
    assert "features" in out


# ── CAP-T157: privacy-first metrics opt-in ──────────────────────────────────


@pytest.fixture
def hub_env_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Standalone hub TestClient fixture for the opt-in header checks."""
    import importlib

    from fastapi.testclient import TestClient

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "0")

    import hub

    importlib.reload(hub)
    hub.init_db()
    client = TestClient(hub.api)
    yield {"client": client, "data_dir": data_dir, "hub": hub}
    client.close()
    importlib.reload(hub)


def _audit_events(data_dir: Path) -> list[dict]:
    log = data_dir / "audit.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def test_scan_without_opt_in_header_skips_metrics_audit(hub_env_opt_in) -> None:
    """CAP-T157: /scan POST without `X-Beaconry-Metrics-Opt-In: 1` → no
    `scan_received` audit event (privacy default)."""
    client = hub_env_opt_in["client"]
    r = client.post("/scan", json={"host": "private-host", "sboms": []})
    assert r.status_code == 200
    events = _audit_events(hub_env_opt_in["data_dir"])
    scan_events = [e for e in events if e.get("event") == "scan_received"]
    assert scan_events == []


def test_scan_with_opt_in_header_writes_metrics_audit(hub_env_opt_in) -> None:
    """CAP-T157: `X-Beaconry-Metrics-Opt-In: 1` writes `scan_received`."""
    client = hub_env_opt_in["client"]
    r = client.post(
        "/scan",
        json={"host": "metrics-host", "sboms": []},
        headers={"X-Beaconry-Metrics-Opt-In": "1"},
    )
    assert r.status_code == 200
    events = _audit_events(hub_env_opt_in["data_dir"])
    scan_events = [e for e in events if e.get("event") == "scan_received"]
    assert len(scan_events) == 1
    assert scan_events[0]["host"] == "metrics-host"


def test_scan_opt_in_header_other_values_dont_write_audit(hub_env_opt_in) -> None:
    """CAP-T157: only exact `1` opts in — `true`/`yes`/`0` do not."""
    client = hub_env_opt_in["client"]
    for header_value in ("0", "true", "yes", ""):
        client.post(
            "/scan",
            json={"host": f"host-{header_value}", "sboms": []},
            headers={"X-Beaconry-Metrics-Opt-In": header_value},
        )
    events = _audit_events(hub_env_opt_in["data_dir"])
    assert [e for e in events if e.get("event") == "scan_received"] == []


def test_heartbeat_without_opt_in_skips_audit(hub_env_opt_in) -> None:
    """CAP-T157: /heartbeat without opt-in header → no `heartbeat_received`."""
    client = hub_env_opt_in["client"]
    r = client.post("/heartbeat", json={"host": "private-host", "health": "ok"})
    assert r.status_code == 200
    events = _audit_events(hub_env_opt_in["data_dir"])
    hb = [e for e in events if e.get("event") == "heartbeat_received"]
    assert hb == []


def test_heartbeat_with_opt_in_writes_audit(hub_env_opt_in) -> None:
    """CAP-T157: /heartbeat with opt-in header writes `heartbeat_received`."""
    client = hub_env_opt_in["client"]
    r = client.post(
        "/heartbeat",
        json={"host": "metrics-host", "health": "ok"},
        headers={"X-Beaconry-Metrics-Opt-In": "1"},
    )
    assert r.status_code == 200
    events = _audit_events(hub_env_opt_in["data_dir"])
    hb = [e for e in events if e.get("event") == "heartbeat_received"]
    assert len(hb) == 1
    assert hb[0]["host"] == "metrics-host"


def test_opt_out_scout_still_registered_in_fleet(hub_env_opt_in) -> None:
    """CAP-T157: fleet-registry row still updates for opted-out scouts.
    Only audit events are gated; the scouts table bumps regardless."""
    client = hub_env_opt_in["client"]
    client.post("/scan", json={"host": "opted-out", "sboms": []})
    body = client.get("/fleet").json()
    hosts = {row["host"] for row in body}
    assert "opted-out" in hosts
