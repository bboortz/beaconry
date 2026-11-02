"""BC-068 — defensive cleanup: reconcile FD leak + fix_version sanitize.

Covers:
    - CAP-T145: reconcile_scan FD-leak guard (try/finally con.close)
    - CAP-T146: fix_version blank-string sanitize
"""

from __future__ import annotations

import importlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
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
    yield {"db": hub.DB_PATH, "hub": hub}
    importlib.reload(hub)


# ── CAP-T145: reconcile_scan close-on-exception ─────────────────────────────


def test_reconcile_scan_closes_connection_on_happy_path(hub_env) -> None:
    """Baseline: happy path doesn't leak FDs."""
    hub = hub_env["hub"]
    # Empty host — no rows → no UPDATEs → closes normally.
    regressed, auto_closed = hub.reconcile_scan("empty-host", set())
    assert regressed == []
    assert auto_closed == []


def test_reconcile_scan_closes_connection_on_update_error(
    hub_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T145: an OperationalError during the UPDATE phase (not the
    opening SELECT) must still close the connection. Pre-BC-068 this
    leaked one FD per failure.

    Approach: wrap `hub.sqlite3.connect` to return a Connection whose
    `execute` raises on UPDATE statements but otherwise behaves normally.
    Track whether `close()` ran on it; the try/finally structure must
    guarantee one close() even when the UPDATE raises.
    """
    hub = hub_env["hub"]

    # Seed one open finding so reconcile has an UPDATE to execute.
    con = sqlite3.connect(hub_env["db"])
    con.execute(
        "INSERT INTO findings (id, host, cve_id, package, version, fixed_version, "
        "severity, cvss, source_type, source_ref, description, found_at, status, tier, "
        "last_seen_ts) VALUES ('f1', 'h1', 'CVE-X', 'p', '1', '2', 'High', 7.5, "
        "'docker', 'nginx:1', '', 100, 'open', 2, 100)"
    )
    con.commit()
    con.close()

    real_connect = sqlite3.connect
    close_calls: list[bool] = []
    update_calls: list[str] = []

    class _ConnWrapper:
        def __init__(self, inner: sqlite3.Connection) -> None:
            self._inner = inner

        def execute(self, sql: str, *a, **kw):
            if sql.strip().upper().startswith("UPDATE"):
                update_calls.append(sql)
                raise sqlite3.OperationalError("simulated lock during UPDATE")
            return self._inner.execute(sql, *a, **kw)

        def commit(self) -> None:
            self._inner.commit()

        def close(self) -> None:
            close_calls.append(True)
            self._inner.close()

    def _wrapped_connect(path, *a, **kw):
        return _ConnWrapper(real_connect(path, *a, **kw))

    monkeypatch.setattr(hub.sqlite3, "connect", _wrapped_connect)

    # Scanned_ids empty → finding is "absent" → UPDATE to fixed_verified
    # is attempted → raises → pre-BC-068 code would leak the connection.
    with pytest.raises(sqlite3.OperationalError):
        hub.reconcile_scan("h1", set())

    assert len(update_calls) == 1, "test must exercise the UPDATE path"
    # The actual regression guard: close() ran even though UPDATE raised.
    assert close_calls == [True], (
        "BC-068 regression: reconcile_scan must close the connection even "
        "when an UPDATE inside the loop raises OperationalError"
    )


# ── CAP-T146: fix_version blank sanitize ────────────────────────────────────


@pytest.mark.asyncio
async def test_fix_versions_blank_strings_filtered(
    hub_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T146: `fix.versions: [""]` → fixed_version='not available'."""
    hub = hub_env["hub"]

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [
            {
                "vulnerability": {
                    "id": "CVE-BLANK",
                    "severity": "High",
                    "cvss": [{"metrics": {"baseScore": 7.5}}],
                    "fix": {"versions": [""]},
                },
                "artifact": {"name": "libblank", "version": "1.0"},
            }
        ]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    findings = await hub.process_sbom_entry(
        {"sbom": {"source": {"type": "docker", "image": "nginx:1.24"}}}, host="h1"
    )
    assert len(findings) == 1
    assert findings[0].fixed_version == "not available"


@pytest.mark.asyncio
async def test_fix_versions_mixed_junk_filtered(hub_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T146: `fix.versions: [None, "", "  "]` all filtered."""
    hub = hub_env["hub"]

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [
            {
                "vulnerability": {
                    "id": "CVE-JUNK",
                    "severity": "High",
                    "cvss": [{"metrics": {"baseScore": 7.5}}],
                    "fix": {"versions": [None, "", "   "]},
                },
                "artifact": {"name": "libjunk", "version": "1.0"},
            }
        ]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    findings = await hub.process_sbom_entry(
        {"sbom": {"source": {"type": "docker", "image": "nginx:1.24"}}}, host="h1"
    )
    assert len(findings) == 1
    assert findings[0].fixed_version == "not available"


@pytest.mark.asyncio
async def test_fix_versions_normal_entry_unchanged(
    hub_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T146 regression guard: a legitimate fix version flows through."""
    hub = hub_env["hub"]

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [
            {
                "vulnerability": {
                    "id": "CVE-GOOD",
                    "severity": "High",
                    "cvss": [{"metrics": {"baseScore": 7.5}}],
                    "fix": {"versions": ["1.2.4"]},
                },
                "artifact": {"name": "libgood", "version": "1.2.3"},
            }
        ]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    findings = await hub.process_sbom_entry(
        {"sbom": {"source": {"type": "docker", "image": "nginx:1.24"}}}, host="h1"
    )
    assert len(findings) == 1
    assert findings[0].fixed_version == "1.2.4"


@pytest.mark.asyncio
async def test_fix_versions_blanks_then_real_entry_prefers_real(
    hub_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T146: `["", "1.2.4"]` → fixed_version='1.2.4', not blank."""
    hub = hub_env["hub"]

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [
            {
                "vulnerability": {
                    "id": "CVE-MIX",
                    "severity": "High",
                    "cvss": [{"metrics": {"baseScore": 7.5}}],
                    "fix": {"versions": ["", "1.2.4"]},
                },
                "artifact": {"name": "libmix", "version": "1.0"},
            }
        ]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    findings = await hub.process_sbom_entry(
        {"sbom": {"source": {"type": "docker", "image": "nginx:1.24"}}}, host="h1"
    )
    assert len(findings) == 1
    assert findings[0].fixed_version == "1.2.4"
