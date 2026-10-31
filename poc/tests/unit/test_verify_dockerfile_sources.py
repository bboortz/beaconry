"""Unit tests for the dockerfile-sources lint matchers.

Covers:
    - CAP-T061: Dockerfile-sources lint — COPY-line matcher correctness
"""

from __future__ import annotations

from poc.scripts.verify_dockerfile_sources import (
    dir_copy_matches,
    explicit_copy_matches,
    glob_copy_matches,
)


def test_explicit_copy_matcher() -> None:
    """`COPY hub/kev.py ...` matches basename `kev.py` in service `hub`."""
    assert explicit_copy_matches("hub/kev.py", "hub", "kev.py") is True
    assert explicit_copy_matches("hub/kev.py", "scout", "kev.py") is False
    assert explicit_copy_matches("kev.py", "hub", "kev.py") is True  # bare basename
    assert explicit_copy_matches("hub/kev.py", "hub", "other.py") is False
    assert explicit_copy_matches("hub/", "hub", "kev.py") is False


def test_glob_copy_matcher() -> None:
    """`COPY hub/*.py ...` matches every .py in `hub/`."""
    assert glob_copy_matches("hub/*.py", "hub", "kev.py") is True
    assert glob_copy_matches("hub/*.py", "hub", "urgency.py") is True
    assert glob_copy_matches("*.py", "hub", "kev.py") is True  # bare glob
    assert glob_copy_matches("hub/*.py", "scout", "kev.py") is False  # wrong svc
    assert glob_copy_matches("hub/*.py", "hub", "config.yaml") is False  # not .py
    assert glob_copy_matches("hub/kev.py", "hub", "kev.py") is False  # not a glob


def test_dir_copy_matcher() -> None:
    """`COPY hub/ ...` or `COPY hub ...` matches everything in `hub/`."""
    assert dir_copy_matches("hub/", "hub") is True
    assert dir_copy_matches("hub", "hub") is True
    assert dir_copy_matches("./hub", "hub") is True
    assert dir_copy_matches("./hub/", "hub") is True
    assert dir_copy_matches("hub/*.py", "hub") is False  # glob, not dir
    assert dir_copy_matches("hub/kev.py", "hub") is False  # explicit, not dir
    assert dir_copy_matches("scout/", "hub") is False  # wrong svc
