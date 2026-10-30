"""BC-012 — reusable probe primitives in beaconry_health.checks.

Covers:
    - CAP-T040: Shared `beaconry_health/` package provides reusable probes
"""

from __future__ import annotations

from pathlib import Path

import pytest

from poc.beaconry_health import checks


def test_check_db_ok_when_path_writable(tmp_path: Path) -> None:
    db = tmp_path / "beaconry.db"
    assert checks.check_db(db) == "ok"


def test_check_db_unavailable_when_dir_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    def _bomb(*_a: object, **_k: object) -> object:
        raise OSError("disk full")

    monkeypatch.setattr(sqlite3, "connect", _bomb)
    assert checks.check_db(tmp_path / "x.db") == "unavailable"


def test_check_binary_ok_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checks, "_which", lambda _name: "/usr/local/bin/grype")
    assert checks.check_binary("grype") == "ok"


def test_check_binary_missing_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checks, "_which", lambda _name: None)
    assert checks.check_binary("grype") == "missing"


def test_check_dir_populated_ok_when_dir_has_contents(tmp_path: Path) -> None:
    d = tmp_path / "grype-db"
    d.mkdir()
    (d / "vulnerability.db").write_text("x")
    assert checks.check_dir_populated(d) == "ok"


def test_check_dir_populated_missing_when_dir_absent(tmp_path: Path) -> None:
    assert checks.check_dir_populated(tmp_path / "nope") == "missing"


def test_check_dir_populated_empty_when_dir_empty(tmp_path: Path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    assert checks.check_dir_populated(d) == "empty"
