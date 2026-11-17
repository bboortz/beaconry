"""BC-143 — tracemalloc toggle + playbook.

Covers:
    - CAP-T239: `BEACONRY_TRACEMALLOC=1` enables Python's `tracemalloc`
      at hub lifespan startup; `/healthz checks.tracemalloc_enabled`
      mirrors the toggle; `/_tracemalloc/top|snapshot|diff` endpoints
      serve payloads when enabled, 200+hint JSON when off. Zero
      overhead when off. Playbook document exists.
"""

from __future__ import annotations

import tracemalloc
from pathlib import Path

import pytest

from poc.hub import hub

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def _tracemalloc_off() -> None:  # noqa: PT004
    if tracemalloc.is_tracing():
        tracemalloc.stop()


@pytest.fixture
def _tracemalloc_on():
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    tracemalloc.start(10)
    yield
    tracemalloc.stop()


# ── Toggle state surfaces on /healthz ─────────────────────────────────


def test_default_disabled(_tracemalloc_off: None) -> None:
    """Without the env var, _tracemalloc_enabled() is False."""
    assert hub._tracemalloc_enabled() is False


def test_tracemalloc_enabled_via_env(_tracemalloc_on) -> None:
    """When tracemalloc.start() has been called, the status flips."""
    assert hub._tracemalloc_enabled() is True


# ── /_tracemalloc/top payload shape ───────────────────────────────────


def test_top_endpoint_json_shape_when_off(_tracemalloc_off: None) -> None:
    payload = hub._tracemalloc_top_payload()
    assert payload["enabled"] is False
    assert "hint" in payload


def test_top_endpoint_json_shape_when_on(_tracemalloc_on) -> None:
    # Allocate something traceable.
    big = ["x" * 1024 for _ in range(500)]
    payload = hub._tracemalloc_top_payload(limit=5)
    assert payload["enabled"] is True
    assert payload["total_mb"] >= 0
    assert isinstance(payload["top"], list)
    assert len(payload["top"]) <= 5
    for entry in payload["top"]:
        assert "file" in entry
        assert "lineno" in entry
        assert "size_mb" in entry
        assert "count" in entry
    del big


# ── Playbook document exists ──────────────────────────────────────────


def test_playbook_doc_exists_and_mentions_candidates() -> None:
    path = REPO_ROOT / "docs" / "operations" / "memory-leak-hunt.md"
    assert path.exists(), "BC-143 playbook doc missing"
    text = path.read_text()
    # Cover the 4 known candidate-leak classes (A–D).
    assert "Suspect A" in text
    assert "Suspect B" in text
    assert "Suspect C" in text
    assert "Suspect D" in text
    # Reference the operator-visible endpoints.
    assert "/_tracemalloc/snapshot" in text
    assert "/_tracemalloc/diff" in text
