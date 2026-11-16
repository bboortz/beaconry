"""BC-125 — EXECUTOR env default hygiene.

Covers:
    - CAP-T228: `BEACONRY_EXECUTOR` unset OR empty-string both resolve
      to `"manual"`, not the empty string that compose's `:-` fallback
      emits. `poc/docker-compose.yml` hub service uses `:-manual`
      instead of `:-`. Invalid values still fall through to the
      ManualAdapter but preserve the operator's string so the
      POLICIES line surfaces the typo.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from poc.hub import hub

# ── Python-level default (hub.py) ────────────────────────────────────────


def test_unset_env_defaults_to_manual(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_EXECUTOR", raising=False)
    importlib.reload(hub)
    assert hub.EXECUTOR_NAME == "manual"


def test_empty_string_env_defaults_to_manual(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose's `:-` fallback emits an empty string, not an absent key.
    `os.environ.get(..., default)` ignores the Python default in that
    case — BC-125 fixes it with `.get() or 'manual'`."""
    monkeypatch.setenv("BEACONRY_EXECUTOR", "")
    importlib.reload(hub)
    assert hub.EXECUTOR_NAME == "manual"


def test_whitespace_env_defaults_to_manual(monkeypatch: pytest.MonkeyPatch) -> None:
    """Belt-and-braces: a single space (from a sloppy compose file)
    should also fall back to manual, not be treated as literal
    whitespace. The `.strip()` happens in the lookup; if no entry
    matches, ManualAdapter is the fallback and the display string
    reflects the raw operator input."""
    monkeypatch.setenv("BEACONRY_EXECUTOR", "   ")
    importlib.reload(hub)
    # The display label mirrors the (stripped) operator intent.
    assert hub.EXECUTOR_NAME == "manual"


def test_explicit_docker_env_wires_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_EXECUTOR", "docker")
    importlib.reload(hub)
    assert hub.EXECUTOR_NAME == "docker"


def test_case_insensitive_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_EXECUTOR", "Manual")
    importlib.reload(hub)
    assert hub.EXECUTOR_NAME == "manual"


# ── Invalid value stays defensive (registry fallback) ──────────────────


def test_invalid_value_falls_through_to_manual_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator typo (`BEACONRY_EXECUTOR=dokcer`) still runs manual —
    don't raise, don't silently correct. The POLICIES line will show
    `EXECUTOR=dokcer` so the operator can see their mistake."""
    monkeypatch.setenv("BEACONRY_EXECUTOR", "dokcer")
    importlib.reload(hub)
    assert hub.EXECUTOR_NAME == "dokcer"
    # Fallback is the registry's second arg (ManualAdapter). Name-check
    # instead of isinstance to avoid import-path flakiness between the
    # host test context (poc.hub.executor.*) and the container context
    # (executor.*).
    assert type(hub.EXECUTOR).__name__ == "ManualAdapter"


# ── /healthz surfaces the normalized value ─────────────────────────────


def test_healthz_executor_key_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_EXECUTOR", raising=False)
    importlib.reload(hub)
    summary = hub._hub_healthz_summary()
    assert summary["checks"]["executor"] == "manual"


# ── docker-compose.yml default ─────────────────────────────────────────


def test_compose_yaml_defaults_executor_to_manual() -> None:
    """Regression lock: compose file must default EXECUTOR to 'manual',
    not an empty string. The empty-string default (pre-BC-125) was the
    root cause of the ambiguous POLICIES line."""
    compose_path = Path(__file__).resolve().parents[3] / "poc" / "docker-compose.yml"
    body = compose_path.read_text()
    # The hub service must have the manual default (and NOT the empty one).
    assert "BEACONRY_EXECUTOR=${BEACONRY_EXECUTOR:-manual}" in body
    assert "BEACONRY_EXECUTOR=${BEACONRY_EXECUTOR:-}" not in body
