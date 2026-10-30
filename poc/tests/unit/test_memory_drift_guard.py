"""BC-015 — memory drift regression guard.

Growing banned-pattern list detects when memory/*.md re-introduces stale
claims about shipped behavior. Seeded with BC-013 + BC-014 drifts; new
entries get added at ship time per the DD-TGD step 6a checklist.

Covers:
    - CAP-T047: `make verify-memory-drift` catches regressions of shipped documentation-reality changes
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from poc.scripts import verify_memory_drift as guard

# ── Loader + schema ───────────────────────────────────────────────────────────


def test_loader_parses_valid_toml(tmp_path: Path) -> None:
    bans_toml = tmp_path / "bans.toml"
    bans_toml.write_text(
        dedent(
            """\
            [[ban]]
            pattern = "docker pull.*:latest"
            banned_since = "BC-014"
            reason = "DockerAdapter uses same-track pull"
            """
        )
    )
    entries = guard.load_bans(bans_toml)
    assert len(entries) == 1
    assert entries[0].banned_since == "BC-014"
    assert entries[0].reason == "DockerAdapter uses same-track pull"


def test_loader_rejects_missing_pattern_field(tmp_path: Path) -> None:
    bans_toml = tmp_path / "bans.toml"
    bans_toml.write_text(
        dedent(
            """\
            [[ban]]
            banned_since = "BC-999"
            reason = "no pattern specified"
            """
        )
    )
    with pytest.raises(ValueError, match="pattern"):
        guard.load_bans(bans_toml)


def test_loader_rejects_missing_banned_since(tmp_path: Path) -> None:
    bans_toml = tmp_path / "bans.toml"
    bans_toml.write_text(
        dedent(
            """\
            [[ban]]
            pattern = "foo"
            reason = "no banned_since"
            """
        )
    )
    with pytest.raises(ValueError, match="banned_since"):
        guard.load_bans(bans_toml)


def test_loader_accepts_optional_exception_regex(tmp_path: Path) -> None:
    bans_toml = tmp_path / "bans.toml"
    bans_toml.write_text(
        dedent(
            """\
            [[ban]]
            pattern = ":latest"
            banned_since = "BC-014"
            reason = "same-track pull"
            exception_regex = ["pre-BC-014 behavior pulled.*:latest", "legacy single"]
            """
        )
    )
    entries = guard.load_bans(bans_toml)
    assert entries[0].exception_regex == [
        "pre-BC-014 behavior pulled.*:latest",
        "legacy single",
    ]


# ── Matching ──────────────────────────────────────────────────────────────────


def _write_bans(tmp_path: Path, *entries: str) -> Path:
    bans = tmp_path / "bans.toml"
    bans.write_text("\n".join(entries))
    return bans


def _write_memory(tmp_path: Path, filename: str, body: str) -> Path:
    memory = tmp_path / "memory"
    memory.mkdir(exist_ok=True)
    f = memory / filename
    f.write_text(body)
    return memory


def test_clean_memory_passes(tmp_path: Path) -> None:
    bans = _write_bans(
        tmp_path,
        '[[ban]]\npattern = "docker pull.*:latest"\nbanned_since = "BC-014"\nreason = "x"\n',
    )
    memory_dir = _write_memory(tmp_path, "a.md", "nothing stale here")
    violations = guard.scan(bans, memory_dir)
    assert violations == []


def test_bc014_drift_is_caught(tmp_path: Path) -> None:
    """Specific regression: an operator restores the pre-BC-014 wording."""
    bans = _write_bans(
        tmp_path,
        '[[ban]]\npattern = "DockerAdapter.*docker pull.*:latest"\n'
        'banned_since = "BC-014"\nreason = "same-track pull replaces :latest"\n',
    )
    memory_dir = _write_memory(
        tmp_path,
        "design-decisions.md",
        "DockerAdapter — docker pull :latest && docker compose up -d",
    )
    violations = guard.scan(bans, memory_dir)
    assert len(violations) == 1
    v = violations[0]
    assert v.file.name == "design-decisions.md"
    assert v.banned_since == "BC-014"
    assert "same-track" in v.reason


def test_bc013_drift_is_caught(tmp_path: Path) -> None:
    bans = _write_bans(
        tmp_path,
        '[[ban]]\npattern = "[Ss]cout.*file-sentinel"\n'
        'banned_since = "BC-013"\nreason = "scout migrated to HTTP"\n',
    )
    memory_dir = _write_memory(
        tmp_path,
        "health-checks.md",
        "Scout uses file-sentinels in /data/",
    )
    violations = guard.scan(bans, memory_dir)
    assert len(violations) == 1
    assert violations[0].banned_since == "BC-013"


def test_exception_regex_passes(tmp_path: Path) -> None:
    """Legitimate historical mention excepted via explicit exception_regex."""
    bans = _write_bans(
        tmp_path,
        '[[ban]]\npattern = ":latest"\nbanned_since = "BC-014"\n'
        'reason = "same-track"\nexception_regex = ["pre-BC-014.*:latest"]\n',
    )
    memory_dir = _write_memory(
        tmp_path, "notes.md", "pre-BC-014 behavior pulled :latest; superseded now."
    )
    violations = guard.scan(bans, memory_dir)
    assert violations == [], f"exception_regex should pass this: {violations}"


def test_multiple_matches_on_same_line_count_once(tmp_path: Path) -> None:
    bans = _write_bans(
        tmp_path,
        '[[ban]]\npattern = "latest"\nbanned_since = "BC-014"\nreason = "x"\n',
    )
    memory_dir = _write_memory(tmp_path, "x.md", "latest latest latest")
    violations = guard.scan(bans, memory_dir)
    assert len(violations) == 1, "a single line matches once per ban entry"


def test_multiple_bans_all_evaluated(tmp_path: Path) -> None:
    bans = _write_bans(
        tmp_path,
        '[[ban]]\npattern = "scout.*file-sentinel"\n'
        'banned_since = "BC-013"\nreason = "scout HTTP"\n',
        '[[ban]]\npattern = "docker pull.*:latest"\n'
        'banned_since = "BC-014"\nreason = "same-track"\n',
    )
    memory_dir = _write_memory(
        tmp_path,
        "both.md",
        "scout uses file-sentinels and docker pull :latest",
    )
    violations = guard.scan(bans, memory_dir)
    assert {v.banned_since for v in violations} == {"BC-013", "BC-014"}


# ── Error messaging ───────────────────────────────────────────────────────────


def test_violation_format_is_actionable(tmp_path: Path) -> None:
    """Error line must name file, line number, banned_since BC, and reason."""
    bans = _write_bans(
        tmp_path,
        '[[ban]]\npattern = "DockerAdapter.*:latest"\n'
        'banned_since = "BC-014"\nreason = "same-track pull"\n',
    )
    memory_dir = _write_memory(
        tmp_path,
        "design.md",
        "line 1\nDockerAdapter — docker pull :latest\nline 3",
    )
    violations = guard.scan(bans, memory_dir)
    formatted = guard.format_violation(violations[0])
    assert "design.md" in formatted
    assert ":2" in formatted or "line 2" in formatted
    assert "BC-014" in formatted
    assert "same-track pull" in formatted


# ── Real memory files pass today ──────────────────────────────────────────────


def test_shipped_memory_files_pass_the_current_bans() -> None:
    """Running against the live repo's bans + memory must be clean post-close-gaps."""
    repo_root = Path(__file__).resolve().parents[3]
    bans_file = repo_root / "poc" / "scripts" / "memory_drift_bans.toml"
    memory_dir = repo_root / "memory"
    if not bans_file.exists():
        pytest.skip("bans file not yet created")
    violations = guard.scan(bans_file, memory_dir)
    assert violations == [], (
        f"BC-015 shipped memory files should be clean; found drift: {violations}"
    )
