"""BC-141 — container + systemd memory/CPU caps.

Covers:
    - CAP-O042: `poc/docker-compose.yml` + `docker-compose.scout-only.yml`
      + ghcr overrides carry `mem_limit`, `memswap_limit` (= mem_limit;
      swap off), and `cpus` on every beaconry service. Lint gates PRs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from poc.scripts import verify_compose_memory_limits as lint

REPO_ROOT = Path(__file__).resolve().parents[3]
POC = REPO_ROOT / "poc"


def _load(name: str) -> dict:
    with (POC / name).open() as f:
        return yaml.safe_load(f) or {}


# ── BC-141: hub + scout caps on dev-compose ────────────────────────────


def test_hub_has_1gb_cap() -> None:
    compose = _load("docker-compose.yml")
    hub = compose["services"]["hub"]
    assert hub["mem_limit"] == "1g"
    assert hub["memswap_limit"] == "1g"
    assert hub["cpus"] == "2"


def test_scout_has_768mb_cap() -> None:
    compose = _load("docker-compose.yml")
    scout = compose["services"]["scout"]
    assert scout["mem_limit"] == "768m"
    assert scout["memswap_limit"] == "768m"
    assert scout["cpus"] == "1"


def test_swap_disabled_per_service() -> None:
    """memswap_limit must equal mem_limit (swap disabled)."""
    for fname in (
        "docker-compose.yml",
        "docker-compose.scout-only.yml",
        "docker-compose.hub-ghcr.yml",
        "docker-compose.scout-ghcr.yml",
    ):
        compose = _load(fname)
        for name in ("hub", "scout"):
            svc = compose.get("services", {}).get(name)
            if svc is None:
                continue
            assert svc.get("mem_limit") == svc.get("memswap_limit"), (
                f"{fname} / {name}: mem_limit {svc.get('mem_limit')} != "
                f"memswap_limit {svc.get('memswap_limit')}"
            )


def test_cpus_set_per_service() -> None:
    for fname in (
        "docker-compose.yml",
        "docker-compose.scout-only.yml",
        "docker-compose.hub-ghcr.yml",
        "docker-compose.scout-ghcr.yml",
    ):
        compose = _load(fname)
        for name in ("hub", "scout"):
            svc = compose.get("services", {}).get(name)
            if svc is None:
                continue
            assert "cpus" in svc, f"{fname} / {name}: missing `cpus`"


# ── BC-141: scout-only (operator systemd stack) ────────────────────────


def test_scout_only_has_768mb_cap() -> None:
    compose = _load("docker-compose.scout-only.yml")
    scout = compose["services"]["scout"]
    assert scout["mem_limit"] == "768m"
    assert scout["memswap_limit"] == "768m"
    assert scout["cpus"] == "1"


# ── BC-141: lint catches drift ─────────────────────────────────────────


def test_lint_passes_on_shipped_repo() -> None:
    """Live repo stays clean under the shipping lint."""
    assert lint.check(REPO_ROOT) == []


def test_lint_flags_missing_mem_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A synthetic compose file missing mem_limit is caught."""
    poc = tmp_path / "poc"
    poc.mkdir()
    (poc / "docker-compose.yml").write_text(
        "services:\n  hub:\n    image: beaconry-hub:latest\n    cpus: '2'\n",
    )
    violations = lint.check(tmp_path)
    assert any("mem_limit" in v for v in violations)


def test_lint_flags_swap_mismatch(tmp_path: Path) -> None:
    """memswap_limit != mem_limit (swap enabled) triggers a violation."""
    poc = tmp_path / "poc"
    poc.mkdir()
    (poc / "docker-compose.yml").write_text(
        "services:\n"
        "  hub:\n"
        "    image: beaconry-hub:latest\n"
        "    mem_limit: 1g\n"
        "    memswap_limit: 2g\n"  # swap left on
        "    cpus: '2'\n",
    )
    violations = lint.check(tmp_path)
    assert any("swap must be disabled" in v for v in violations)
