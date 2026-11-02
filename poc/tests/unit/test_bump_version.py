"""BC-062 — SemVer bump helpers + pre-push verifier.

Covers:
    - CAP-T128: SemVer bump helper + pre-push enforcement
    - CAP-T151: Hermetic bump-verify tests (BC-071 — resist env leak)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from poc.scripts import bump_version as bv
from poc.scripts import verify_version_bumped as vvb

# ── bump() ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("current", "kind", "expected"),
    [
        ("0.1.0", "patch", "0.1.1"),
        ("0.1.0", "minor", "0.2.0"),
        ("0.1.0", "major", "1.0.0"),
        ("1.2.3", "patch", "1.2.4"),
        ("1.2.3", "minor", "1.3.0"),
        ("1.2.3", "major", "2.0.0"),
        ("9.9.9", "patch", "9.9.10"),
    ],
)
def test_bump_cases(current: str, kind: str, expected: str) -> None:
    assert bv.bump(current, kind) == expected


def test_bump_rejects_non_semver() -> None:
    with pytest.raises(ValueError):
        bv.bump("v1.2.3", "patch")
    with pytest.raises(ValueError):
        bv.bump("1.2", "patch")


def test_bump_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        bv.bump("1.2.3", "epoch")


# ── parse() ─────────────────────────────────────────────────────────────────


def test_parse_strips_prerelease_suffix() -> None:
    assert bv.parse("1.2.3-rc1") == (1, 2, 3)
    assert bv.parse("1.2.3+build.42") == (1, 2, 3)


# ── auto-infer from commits ─────────────────────────────────────────────────


def _init_repo(tmp_path: Path) -> Path:
    """Spin up a tiny git repo with a `dev` branch and HEAD on a feature branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q", "-b", "dev")
    (repo / "VERSION").write_text("0.1.0\n")
    (repo / "poc").mkdir()
    (repo / "poc" / "VERSION").write_text("0.1.0\n")
    git("add", ".")
    git("commit", "-q", "-m", "chore: initial")
    git("checkout", "-q", "-b", "feat/BC-999-x")
    return repo


def _commit(repo: Path, message: str, file_body: str = "x") -> None:
    f = repo / f"{abs(hash(message)) % 10_000}.txt"
    f.write_text(file_body)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message],
        cwd=repo,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


def test_infer_feat_commit_bumps_minor(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "feat(hub): add fleet column")
    assert bv.infer_bump_from_commits(repo) == "minor"


def test_infer_fix_commit_bumps_patch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "fix(hub): escape markdown")
    assert bv.infer_bump_from_commits(repo) == "patch"


def test_infer_bang_commit_bumps_major(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "feat(hub)!: rename /scan to /ingest")
    assert bv.infer_bump_from_commits(repo) == "major"


def test_infer_breaking_change_footer_bumps_major(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "feat(hub): new flow\n\nBREAKING CHANGE: /scan removed")
    assert bv.infer_bump_from_commits(repo) == "major"


def test_infer_mixed_commits_prefers_strongest(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "fix(hub): small nit")
    _commit(repo, "feat(hub): larger add")
    _commit(repo, "chore: tidy")
    assert bv.infer_bump_from_commits(repo) == "minor"


def test_infer_no_commits_falls_through_to_patch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    assert bv.infer_bump_from_commits(repo) == "patch"


# ── write + atomic 2-file update ────────────────────────────────────────────


def test_write_updates_both_files(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("0.1.0\n")
    (tmp_path / "poc").mkdir()
    (tmp_path / "poc" / "VERSION").write_text("0.1.0\n")
    bv.write(tmp_path, "0.2.0")
    assert (tmp_path / "VERSION").read_text().strip() == "0.2.0"
    assert (tmp_path / "poc" / "VERSION").read_text().strip() == "0.2.0"


# ── verify_version_bumped ───────────────────────────────────────────────────


def test_verify_pass_when_version_bumped(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "VERSION").write_text("0.2.0\n")
    (repo / "poc" / "VERSION").write_text("0.2.0\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "feat: bump"],
        cwd=repo,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    errors = vvb.check(repo)
    assert errors == []


def test_verify_fails_when_version_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BC-071: hermetic — a stray BEACONRY_SKIP_VERSION_BUMP in the parent
    # process env would short-circuit vvb.check() to [] and false-pass this.
    monkeypatch.delenv("BEACONRY_SKIP_VERSION_BUMP", raising=False)
    repo = _init_repo(tmp_path)
    _commit(repo, "feat: no bump")
    errors = vvb.check(repo)
    assert any("VERSION unchanged" in e for e in errors), errors


def test_verify_fails_when_version_went_backwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BC-071: same hermeticity concern as above.
    monkeypatch.delenv("BEACONRY_SKIP_VERSION_BUMP", raising=False)
    repo = _init_repo(tmp_path)
    (repo / "VERSION").write_text("0.0.1\n")
    (repo / "poc" / "VERSION").write_text("0.0.1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "oops: downgrade"],
        cwd=repo,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    errors = vvb.check(repo)
    assert any("backwards" in e or "equal" in e for e in errors), errors


def test_verify_pass_on_dev_branch_itself(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "checkout", "-q", "dev"], cwd=repo, check=True)
    assert vvb.check(repo) == []


def test_verify_pass_when_no_commits_ahead(tmp_path: Path) -> None:
    """Fast-forward push (feature branch == dev tip) → nothing to require."""
    repo = _init_repo(tmp_path)
    # feat branch created at dev tip with no new commits.
    errors = vvb.check(repo)
    assert errors == []


def test_verify_pass_with_skip_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "feat: no bump")
    monkeypatch.setenv("BEACONRY_SKIP_VERSION_BUMP", "1")
    assert vvb.check(repo) == []


# ── bump_version.main() CLI exerciser ───────────────────────────────────────


def test_main_dry_run_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "VERSION").write_text("0.1.0\n")
    (tmp_path / "poc").mkdir()
    (tmp_path / "poc" / "VERSION").write_text("0.1.0\n")
    monkeypatch.setattr(bv, "_repo_root", lambda: tmp_path)
    rc = bv.main(["--minor", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "would bump minor: 0.1.0 -> 0.2.0" in out
    assert (tmp_path / "VERSION").read_text().strip() == "0.1.0"


def test_main_patch_writes_both_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "VERSION").write_text("1.2.3\n")
    (tmp_path / "poc").mkdir()
    (tmp_path / "poc" / "VERSION").write_text("1.2.3\n")
    monkeypatch.setattr(bv, "_repo_root", lambda: tmp_path)
    rc = bv.main(["--patch"])
    assert rc == 0
    assert (tmp_path / "VERSION").read_text().strip() == "1.2.4"
    assert (tmp_path / "poc" / "VERSION").read_text().strip() == "1.2.4"
    out = capsys.readouterr().out
    assert "bumped patch: 1.2.3 -> 1.2.4" in out


def test_main_auto_falls_through_to_patch_outside_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T128: --auto in a non-git dir degrades to patch (safe default)."""
    (tmp_path / "VERSION").write_text("1.0.0\n")
    (tmp_path / "poc").mkdir()
    (tmp_path / "poc" / "VERSION").write_text("1.0.0\n")
    monkeypatch.setattr(bv, "_repo_root", lambda: tmp_path)
    rc = bv.main(["--auto"])
    assert rc == 0
    assert (tmp_path / "VERSION").read_text().strip() == "1.0.1"


def test_main_missing_version_file_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(bv, "_repo_root", lambda: tmp_path)
    rc = bv.main(["--patch"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "cannot read VERSION" in err


def test_main_invalid_semver_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "VERSION").write_text("not-a-version\n")
    (tmp_path / "poc").mkdir()
    (tmp_path / "poc" / "VERSION").write_text("not-a-version\n")
    monkeypatch.setattr(bv, "_repo_root", lambda: tmp_path)
    rc = bv.main(["--patch"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "not a SemVer" in err
