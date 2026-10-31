"""Unit tests for the per-file coverage floor lint.

Covers:
    - CAP-T065: Per-file coverage floor lint
"""

from __future__ import annotations

import json
from pathlib import Path

from poc.scripts.verify_per_file_coverage import ALLOWLIST, check_coverage


def _write_cov(tmp_path: Path, files: dict[str, float]) -> Path:
    """Write a minimal coverage.json shape pytest-cov produces."""
    doc = {"files": {path: {"summary": {"percent_covered": pct}} for path, pct in files.items()}}
    p = tmp_path / "coverage.json"
    p.write_text(json.dumps(doc))
    return p


def test_passes_when_all_files_above_default_floor(tmp_path: Path) -> None:
    cov = _write_cov(tmp_path, {"poc/hub/hub.py": 80.0, "poc/hub/kev.py": 90.0})
    assert check_coverage(cov) == []


def test_fails_when_any_file_below_default_floor(tmp_path: Path) -> None:
    cov = _write_cov(tmp_path, {"poc/hub/hub.py": 60.0, "poc/some_new_file.py": 40.0})
    errors = check_coverage(cov)
    assert len(errors) == 2
    assert any("hub.py" in e for e in errors)
    assert any("some_new_file.py" in e for e in errors)


def test_allowlisted_file_held_to_lower_floor(tmp_path: Path) -> None:
    """scout.py is allowlisted at 30%; 35% passes, 25% fails."""
    assert "poc/scout/scout.py" in ALLOWLIST
    scout_floor, _reason = ALLOWLIST["poc/scout/scout.py"]
    assert scout_floor < 65.0

    cov = _write_cov(tmp_path, {"poc/scout/scout.py": scout_floor + 5})
    assert check_coverage(cov) == []

    cov = _write_cov(tmp_path, {"poc/scout/scout.py": scout_floor - 5})
    errors = check_coverage(cov)
    assert len(errors) == 1
    assert "allowlisted floor" in errors[0]


def test_error_message_is_actionable(tmp_path: Path) -> None:
    cov = _write_cov(tmp_path, {"poc/new.py": 40.0})
    errors = check_coverage(cov)
    assert "add tests" in errors[0] or "allowlist" in errors[0]


def test_missing_coverage_json_fails_fast(tmp_path: Path) -> None:
    errors = check_coverage(tmp_path / "does-not-exist.json")
    assert len(errors) == 1
    assert "could not read" in errors[0]


def test_custom_min_pct_override(tmp_path: Path) -> None:
    cov = _write_cov(tmp_path, {"poc/foo.py": 50.0})
    assert check_coverage(cov, min_pct=40.0) == []
    assert check_coverage(cov, min_pct=60.0) != []
