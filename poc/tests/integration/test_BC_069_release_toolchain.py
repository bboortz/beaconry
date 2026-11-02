"""BC-069 — release toolchain: CI + make soak + CHANGELOG.

Covers:
    - CAP-T147: GitHub Actions CI with branch-stratified gates
    - CAP-T148: make soak — audit-log health check over last 1h
    - CAP-T149: Automated CHANGELOG.md generation + pre-push gate
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from poc.scripts import changelog as cl
from poc.scripts import soak as sk
from poc.scripts import verify_changelog as vc

# ── CAP-T148: soak ──────────────────────────────────────────────────────────


def _write_audit(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


def test_soak_clean_window_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T148: no error events in window → exit 0."""
    now = int(time.time())
    _write_audit(
        tmp_path / "audit.log",
        [
            {"ts": now - 60, "event": "scan_received", "host": "h1"},
            {"ts": now - 120, "event": "digest_sent", "host": "h1"},
            {"ts": now - 180, "event": "notify_sent", "host": "h1"},
        ],
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOAK_WINDOW_S", "3600")
    rc = sk.soak(now=now)
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 error events" in out


def test_soak_errors_in_window_returns_one_and_prints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T148: error events in window → exit 1 + listed by timestamp."""
    now = int(time.time())
    _write_audit(
        tmp_path / "audit.log",
        [
            {"ts": now - 60, "event": "scan_received", "host": "h1"},
            {
                "ts": now - 120,
                "event": "notify_sent_failed",
                "finding_id": "ab12",
                "cve_id": "CVE-2026-00001",
            },
            {
                "ts": now - 180,
                "event": "regressed",
                "finding_id": "cd34",
                "cve_id": "CVE-2024-3094",
                "host": "server2",
            },
        ],
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOAK_WINDOW_S", "3600")
    rc = sk.soak(now=now)
    assert rc == 1
    out = capsys.readouterr().out
    assert "2 error event(s)" in out
    assert "notify_sent_failed" in out
    assert "regressed" in out
    assert "CVE-2024-3094" in out


def test_soak_missing_audit_log_returns_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T148: missing audit.log → exit 2 (distinct from 'clean')."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    rc = sk.soak(now=int(time.time()))
    assert rc == 2
    err = capsys.readouterr().err
    assert "audit log not found" in err


def test_soak_window_env_controls_cutoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T148: SOAK_WINDOW_S=60 → only events in the last minute count.
    An error event 10 min ago is OUTSIDE the window."""
    now = int(time.time())
    _write_audit(
        tmp_path / "audit.log",
        [
            {
                "ts": now - 600,  # 10 min ago, outside 60s window
                "event": "notify_sent_failed",
                "finding_id": "old",
            },
        ],
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOAK_WINDOW_S", "60")
    rc = sk.soak(now=now)
    assert rc == 0  # error is outside window → treated as clean


def test_soak_malformed_lines_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T148: a truncated/bad JSON line doesn't crash the sweep."""
    now = int(time.time())
    path = tmp_path / "audit.log"
    path.write_text(
        "not-json-line\n"
        + json.dumps({"ts": now - 60, "event": "notify_sent_failed", "finding_id": "x"})
        + "\n"
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOAK_WINDOW_S", "3600")
    rc = sk.soak(now=now)
    assert rc == 1  # the one valid error event still counts


# ── CAP-T149: changelog generation ──────────────────────────────────────────


def _init_repo(tmp_path: Path, version: str = "0.1.0") -> Path:
    """Spin up a tiny git repo with a VERSION file and conventional commits."""
    import os

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    (repo / "VERSION").write_text(f"{version}\n")
    (repo / "poc").mkdir()
    (repo / "poc" / "VERSION").write_text(f"{version}\n")
    git("add", ".")
    git("commit", "-q", "-m", "chore: initial")
    return repo


def _commit(repo: Path, message: str) -> None:
    import os

    f = repo / f"{abs(hash(message)) % 10_000}.txt"
    f.write_text("x")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
    )


def test_changelog_groups_commits_by_type(tmp_path: Path) -> None:
    """CAP-T149: feat / fix / docs land in the right sections."""
    repo = _init_repo(tmp_path)
    _commit(repo, "feat(hub): new endpoint")
    _commit(repo, "fix(scout): correct heartbeat")
    _commit(repo, "docs(readme): clarify install")
    _commit(repo, "refactor(hub): split module")

    body = cl.generate(repo_root=repo, today="2026-11-02")
    assert "## [v0.1.0] — 2026-11-02" in body
    assert "### Added" in body
    assert "**hub:** new endpoint" in body
    assert "### Fixed" in body
    assert "**scout:** correct heartbeat" in body
    assert "### Changed" in body
    assert "**hub:** split module" in body
    assert "### Documentation" in body


def test_changelog_surfaces_breaking_changes(tmp_path: Path) -> None:
    """CAP-T149: `!:` and BREAKING CHANGE footer land in the ⚠ block."""
    repo = _init_repo(tmp_path)
    _commit(repo, "feat(api)!: rename /scan to /ingest")
    _commit(repo, "feat(hub): add tag\n\nBREAKING CHANGE: changes the DB schema.")

    body = cl.generate(repo_root=repo, today="2026-11-02")
    assert "### ⚠ Breaking" in body
    assert "rename /scan to /ingest" in body
    assert "add tag" in body


def test_changelog_first_run_includes_all_history(tmp_path: Path) -> None:
    """CAP-T149: no existing CHANGELOG → section includes all commits."""
    repo = _init_repo(tmp_path)
    _commit(repo, "feat(hub): first feature")
    _commit(repo, "feat(hub): second feature")
    _commit(repo, "fix(hub): first fix")

    body = cl.generate(repo_root=repo, today="2026-11-02")
    assert "first feature" in body
    assert "second feature" in body
    assert "first fix" in body


def test_changelog_write_persists_file(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "feat(hub): x")

    path = cl.write(repo_root=repo, today="2026-11-02")
    assert path == repo / "CHANGELOG.md"
    assert path.exists()
    assert "v0.1.0" in path.read_text()


def test_changelog_second_bump_prepends_new_section(tmp_path: Path) -> None:
    """CAP-T149: incremental generation using git tags as the boundary.

    Workflow: operator bumps VERSION → generates CHANGELOG → tags
    `v<version>` → bumps again. The next changelog gen uses the tag
    to limit commits to the actual delta.
    """
    import os

    repo = _init_repo(tmp_path, version="0.1.0")
    _commit(repo, "feat(hub): first batch")
    cl.write(repo_root=repo, today="2026-11-02")

    # Tag the release — this is the boundary for the next delta.
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(
        ["git", "tag", "v0.1.0"],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
    )

    # Bump the VERSION file; add a new commit; regenerate.
    (repo / "VERSION").write_text("0.2.0\n")
    _commit(repo, "feat(scout): second batch")
    body = cl.generate(repo_root=repo, today="2026-11-03")

    # Both versions present; v0.2.0 appears first.
    v020 = body.find("## [v0.2.0]")
    v010 = body.find("## [v0.1.0]")
    assert 0 < v020 < v010
    # Only the new commit lives in the v0.2.0 section — tag bounded.
    v020_section = body[v020:v010]
    assert "second batch" in v020_section
    assert "first batch" not in v020_section


def test_changelog_without_tag_falls_back_to_full_history(tmp_path: Path) -> None:
    """CAP-T149 edge case: no `v<prev>` tag → generator includes full
    history in the new section. Documents the operator workflow —
    `git tag v<VERSION>` after each release keeps deltas clean."""
    repo = _init_repo(tmp_path, version="0.1.0")
    _commit(repo, "feat(hub): first batch")
    cl.write(repo_root=repo, today="2026-11-02")

    # NO tag — bump directly.
    (repo / "VERSION").write_text("0.2.0\n")
    _commit(repo, "feat(scout): second batch")
    body = cl.generate(repo_root=repo, today="2026-11-03")

    # Without the tag, the generator can't know the boundary; new section
    # includes everything up to HEAD. This is the fallback, not a bug —
    # operators get an idempotent CHANGELOG; worst case is duplication.
    v020 = body.find("## [v0.2.0]")
    v010 = body.find("## [v0.1.0]")
    assert 0 < v020 < v010
    v020_section = body[v020:v010]
    assert "second batch" in v020_section
    assert "first batch" in v020_section  # fallback dupes it, see docstring


# ── CAP-T149: verify-changelog gate ─────────────────────────────────────────


def test_verify_changelog_fails_on_missing_entry(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("0.9.9\n")
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [v0.1.0] — 2026-01-01\n\n### Added\n- initial\n"
    )
    errors = vc.check(tmp_path)
    assert errors
    assert "0.9.9" in errors[0]


def test_verify_changelog_passes_when_matched(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("0.3.3\n")
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [v0.3.3] — 2026-11-02\n\n### Added\n- x\n"
    )
    assert vc.check(tmp_path) == []


def test_verify_changelog_fails_on_missing_file(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("0.3.3\n")
    errors = vc.check(tmp_path)
    assert errors
    assert "missing" in errors[0].lower()


def test_verify_changelog_bypass_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T149: BEACONRY_SKIP_CHANGELOG=1 is the emergency bypass."""
    (tmp_path / "VERSION").write_text("0.9.9\n")
    monkeypatch.setenv("BEACONRY_SKIP_CHANGELOG", "1")
    assert vc.check(tmp_path) == []


# ── CAP-T147: CI yaml sanity ────────────────────────────────────────────────


def test_ci_yaml_parses() -> None:
    """CAP-T147: the workflow file is syntactically valid YAML with the
    jobs we expect."""
    import yaml

    repo_root = Path(__file__).resolve().parents[3]
    with open(repo_root / ".github" / "workflows" / "ci.yml") as f:
        doc = yaml.safe_load(f)
    assert "jobs" in doc
    expected_jobs = {"detect", "fast-check", "full-check", "release-check", "notify-failure"}
    assert expected_jobs <= set(doc["jobs"].keys())


def test_ci_yaml_uses_concurrency_cancellation() -> None:
    """CAP-T147: concurrency.cancel-in-progress saves CI minutes on
    rapid-fire pushes."""
    import yaml

    repo_root = Path(__file__).resolve().parents[3]
    with open(repo_root / ".github" / "workflows" / "ci.yml") as f:
        doc = yaml.safe_load(f)
    assert doc["concurrency"]["cancel-in-progress"] is True
