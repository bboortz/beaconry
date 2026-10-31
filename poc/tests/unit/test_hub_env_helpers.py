"""BC-035 — defensive int-env parsing.

Covers:
    - CAP-T078: `_int_env` tolerates compose's `${VAR:-}` empty-string
      fallback so hub boots even when operators haven't exported the
      heartbeat tuning vars.
"""

from __future__ import annotations

import hub
import pytest


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_int_env_treats_empty_as_default(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_TEST_X", value)
    assert hub._int_env("BEACONRY_TEST_X", 42) == 42


def test_int_env_treats_unset_as_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_TEST_X", raising=False)
    assert hub._int_env("BEACONRY_TEST_X", 99) == 99


def test_int_env_parses_valid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_TEST_X", "123")
    assert hub._int_env("BEACONRY_TEST_X", 0) == 123


def test_int_env_strips_surrounding_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_TEST_X", "  7  ")
    assert hub._int_env("BEACONRY_TEST_X", 0) == 7


def test_int_env_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid non-empty values still raise — only empty is the default fallback."""
    monkeypatch.setenv("BEACONRY_TEST_X", "notanumber")
    with pytest.raises(ValueError):
        hub._int_env("BEACONRY_TEST_X", 0)
