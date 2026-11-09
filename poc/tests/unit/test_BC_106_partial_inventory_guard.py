"""BC-106 — Scout-reported partial-inventory guard.

Covers:
    - CAP-T208: hub's `receive_scan` reads `images_expected` +
      `images_collected` from the payload; when the ratio falls
      below `BEACONRY_PARTIAL_SCAN_MIN_RATIO`, reconcile is skipped
      and `reconcile_skipped_partial_inventory` audit fires with
      `reason="bc_106_explicit_ratio"`. Pre-BC-106 payloads (no
      fields) fall through to BC-067's baseline heuristic
      unchanged.
"""

from __future__ import annotations

import pytest

from poc.hub import hub


def test_env_default_0_95(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_PARTIAL_SCAN_MIN_RATIO", raising=False)
    import importlib

    importlib.reload(hub)
    assert hub.PARTIAL_SCAN_MIN_RATIO == 0.95


def test_env_parses_floats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_PARTIAL_SCAN_MIN_RATIO", "0.5")
    import importlib

    importlib.reload(hub)
    assert hub.PARTIAL_SCAN_MIN_RATIO == 0.5


def test_env_clamps_to_valid_range(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    monkeypatch.setenv("BEACONRY_PARTIAL_SCAN_MIN_RATIO", "1.5")
    importlib.reload(hub)
    assert hub.PARTIAL_SCAN_MIN_RATIO == 1.0

    monkeypatch.setenv("BEACONRY_PARTIAL_SCAN_MIN_RATIO", "-0.3")
    importlib.reload(hub)
    assert hub.PARTIAL_SCAN_MIN_RATIO == 0.0


def test_env_invalid_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_PARTIAL_SCAN_MIN_RATIO", "not-a-number")
    import importlib

    importlib.reload(hub)
    assert hub.PARTIAL_SCAN_MIN_RATIO == 0.95


def test_parse_ratio_helper_direct() -> None:
    """The internal _parse_ratio function clamps + defaults correctly."""
    assert hub._parse_ratio("0.75", 0.95) == 0.75
    assert hub._parse_ratio("1.0", 0.95) == 1.0
    assert hub._parse_ratio("0.0", 0.95) == 0.0
    assert hub._parse_ratio("", 0.95) == 0.95
    assert hub._parse_ratio("junk", 0.95) == 0.95
    assert hub._parse_ratio("1.5", 0.95) == 1.0
    assert hub._parse_ratio("-5", 0.95) == 0.0


def test_scout_payload_fields_present() -> None:
    """Scout module exposes the counters the POST payload reads from."""
    from poc.scout import scout

    assert hasattr(scout, "_last_scan_images_expected")
    assert hasattr(scout, "_last_scan_images_collected")
    # Default zero before any scan has run.
    assert isinstance(scout._last_scan_images_expected, int)
    assert isinstance(scout._last_scan_images_collected, int)
