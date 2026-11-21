"""BC-083 CAP-T171 — unit tests for the base-image anonymous-pullable lint.

Covers:
    - CAP-T171a: `_split_ref` canonicalises Docker Hub shorthand, GHCR, and
      bare names consistently
    - CAP-T171b: `_load_cache` / `_save_cache` roundtrip; corrupt or missing
      cache files degrade gracefully
    - CAP-T171c: `check` honours cache hits (no live probe) and reports
      violations for failed probes
    - CAP-T171d: `main` short-circuits on BEACONRY_SKIP_BASE_IMAGES and when
      no FROM refs are present
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from poc.scripts import verify_base_images as lint

# ── _split_ref: normalisation table ───────────────────────────────────────


def test_split_ref_bare_name_resolves_to_docker_hub_library() -> None:
    assert lint._split_ref("python") == ("docker.io", "library/python", "latest")


def test_split_ref_bare_name_with_tag() -> None:
    assert lint._split_ref("python:3.13-slim") == (
        "docker.io",
        "library/python",
        "3.13-slim",
    )


def test_split_ref_shorthand_library_path() -> None:
    assert lint._split_ref("org/image:1.2.3") == ("docker.io", "org/image", "1.2.3")


def test_split_ref_explicit_registry_with_dot() -> None:
    assert lint._split_ref("ghcr.io/bboortz/beaconry:v1") == (
        "ghcr.io",
        "bboortz/beaconry",
        "v1",
    )


def test_split_ref_explicit_registry_with_port() -> None:
    assert lint._split_ref("localhost:5000/foo:dev") == (
        "localhost:5000",
        "foo",
        "dev",
    )


def test_split_ref_defaults_to_latest_tag_when_missing() -> None:
    assert lint._split_ref("ghcr.io/org/repo") == ("ghcr.io", "org/repo", "latest")


# ── cache round-trip ──────────────────────────────────────────────────────


def test_load_cache_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    assert lint._load_cache(tmp_path / "missing.json") == {}


def test_load_cache_tolerates_corrupt_json(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not valid json")
    assert lint._load_cache(corrupt) == {}


def test_save_and_load_cache_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "cache.json"
    data = {"python:3.13-slim": {"ts": 1700000000, "ok": True}}
    lint._save_cache(path, data)
    assert path.exists()
    assert lint._load_cache(path) == data


# ── check: cache hit avoids probe, live failure surfaces violation ────────


def test_check_uses_cache_hit_and_skips_live_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    cache.write_text('{"python:3.13-slim": {"ts": ' + str(time.time()) + ', "ok": true}}')

    called: list[str] = []

    def never(ref: str) -> bool:  # pragma: no cover - should not run
        called.append(ref)
        return True

    monkeypatch.setattr(lint, "_probe_image_live", never)
    violations = lint.check(["python:3.13-slim"], cache_path=cache)
    assert violations == []
    assert called == []  # cache hit avoided the network call


def test_check_reprobes_stale_cache_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    # Entry from ~25h ago → older than the 24h TTL → must re-probe.
    stale_ts = time.time() - (25 * 60 * 60)
    cache.write_text(f'{{"img:tag": {{"ts": {stale_ts}, "ok": true}}}}')

    calls: list[str] = []

    def probed(ref: str) -> bool:
        calls.append(ref)
        return True

    monkeypatch.setattr(lint, "_probe_image_live", probed)
    violations = lint.check(["img:tag"], cache_path=cache)
    assert violations == []
    assert calls == ["img:tag"]


def test_check_reports_violations_on_live_probe_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(lint, "_probe_image_live", lambda _ref: False)
    violations = lint.check(["ghost.io/dead:tag"], cache_path=cache)
    assert len(violations) == 1
    assert "ghost.io/dead:tag" in violations[0]
    # Cache should still have been written with ok: false.
    assert cache.exists()


# ── main: short-circuits ──────────────────────────────────────────────────


def test_main_short_circuits_when_skip_env_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BEACONRY_SKIP_BASE_IMAGES", "1")
    assert lint.main(["--repo-root", str(tmp_path)]) == 0


def test_main_returns_zero_when_no_from_refs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BEACONRY_SKIP_BASE_IMAGES", raising=False)
    # Empty repo-root has no Dockerfiles → no FROM refs found.
    assert lint.main(["--repo-root", str(tmp_path)]) == 0


def test_main_returns_zero_when_all_refs_pullable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BEACONRY_SKIP_BASE_IMAGES", raising=False)
    # Drop a Dockerfile; override the probe.
    df = tmp_path / "poc" / "svc" / "Dockerfile"
    df.parent.mkdir(parents=True)
    df.write_text("FROM python:3.13-slim\n")
    monkeypatch.setattr(lint, "_probe_image_live", lambda _ref: True)
    rc = lint.main(["--repo-root", str(tmp_path), "--cache-path", str(tmp_path / "cache.json")])
    assert rc == 0


def test_main_returns_one_when_probe_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BEACONRY_SKIP_BASE_IMAGES", raising=False)
    df = tmp_path / "poc" / "svc" / "Dockerfile"
    df.parent.mkdir(parents=True)
    df.write_text("FROM ghost.io/dead:tag\n")
    monkeypatch.setattr(lint, "_probe_image_live", lambda _ref: False)
    rc = lint.main(["--repo-root", str(tmp_path), "--cache-path", str(tmp_path / "cache.json")])
    assert rc == 1
