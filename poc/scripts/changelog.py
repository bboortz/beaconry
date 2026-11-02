"""BC-069 — automated CHANGELOG.md generator.

Reads commits since the last `## [v*]` header in CHANGELOG.md,
categorises by conventional-commit prefix, and prepends a new
section for the version in VERSION.

Keep A Changelog format (https://keepachangelog.com/).

Groups:
  - feat     → Added
  - fix      → Fixed
  - refactor / chore / perf / style → Changed
  - docs     → Documentation (only emitted if non-empty)
  - !: / BREAKING CHANGE → extra "⚠ Breaking" block at top

Commits that don't match conventional-commit form land in "Other"
so nothing silently vanishes.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"
_VERSION = _REPO_ROOT / "VERSION"

_CONVENTIONAL_RE = re.compile(
    r"^(?P<type>feat|fix|docs|chore|refactor|perf|style|test|ci|build)"
    r"(?:\((?P<scope>[^)]+)\))?"
    r"(?P<bang>!?)"
    r":\s+"
    r"(?P<subject>.+)$"
)

_HEADER_RE = re.compile(r"^## \[v(?P<version>\d+\.\d+\.\d+[^\]]*)\]")

_GROUP_LABEL = {
    "feat": "Added",
    "fix": "Fixed",
    "refactor": "Changed",
    "chore": "Changed",
    "perf": "Changed",
    "style": "Changed",
    "test": "Changed",
    "ci": "Changed",
    "build": "Changed",
    "docs": "Documentation",
    "_other": "Other",
}

# Order of sections in the rendered output.
_GROUP_ORDER = ["Added", "Fixed", "Changed", "Documentation", "Other"]


def _git(args: list[str], cwd: Path = _REPO_ROOT) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return out.stdout if out.returncode == 0 else ""


def read_version() -> str:
    return _VERSION.read_text().strip()


def find_last_logged_version(path: Path = _CHANGELOG) -> str | None:
    """Return the top-most version header value from CHANGELOG.md, or
    None if the file doesn't exist or has no entry yet."""
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        m = _HEADER_RE.match(line)
        if m:
            return m.group("version")
    return None


def _collect_commits(repo_root: Path, since_tag: str | None) -> list[tuple[str, str]]:
    """Return [(subject, body), ...] for commits since `since_tag` on HEAD.
    First-run (no tag + no changelog entry) → entire history."""
    if since_tag:
        rev_range = f"v{since_tag}..HEAD"
        # If the tag doesn't actually exist as a git tag, treat as first-run.
        if not _git(["rev-parse", "--verify", f"v{since_tag}"], repo_root).strip():
            rev_range = "HEAD"
    else:
        rev_range = "HEAD"
    # %B is the full message (subject + body); use NUL separator so bodies
    # with newlines don't confuse parsing.
    out = _git(
        ["log", rev_range, "--pretty=format:%B%x00", "--no-merges", "--reverse"],
        repo_root,
    )
    if not out:
        return []
    commits: list[tuple[str, str]] = []
    for chunk in out.split("\x00"):
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = chunk.splitlines()
        subject = lines[0] if lines else ""
        body = "\n".join(lines[1:]).strip()
        commits.append((subject, body))
    return commits


def _categorize(subject: str, body: str) -> tuple[str, str, bool]:
    """Return (group_key, rendered_bullet, is_breaking)."""
    m = _CONVENTIONAL_RE.match(subject)
    is_breaking = bool(m and m.group("bang")) or "BREAKING CHANGE" in body
    if m:
        key = m.group("type")
        scope = m.group("scope")
        rendered = f"**{scope}:** {m.group('subject')}" if scope else m.group("subject")
    else:
        key = "_other"
        rendered = subject
    return _GROUP_LABEL[key], rendered, is_breaking


def _render_section(version: str, commits: list[tuple[str, str]], date: str) -> str:
    groups: dict[str, list[str]] = {label: [] for label in _GROUP_ORDER}
    breaking: list[str] = []
    for subject, body in commits:
        label, bullet, is_breaking = _categorize(subject, body)
        groups[label].append(bullet)
        if is_breaking:
            breaking.append(bullet)

    lines: list[str] = [f"## [v{version}] — {date}", ""]
    if breaking:
        lines.append("### ⚠ Breaking")
        for item in breaking:
            lines.append(f"- {item}")
        lines.append("")
    for label in _GROUP_ORDER:
        items = groups[label]
        if not items:
            continue
        if label == "Documentation" and all(not it for it in items):
            continue
        lines.append(f"### {label}")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines)


_PREAMBLE = """# Changelog

All notable changes to beaconry are recorded here. The format is based
on [Keep a Changelog](https://keepachangelog.com/), and this project
follows [Semantic Versioning](https://semver.org/).

"""


def generate(
    *,
    repo_root: Path = _REPO_ROOT,
    changelog_path: Path | None = None,
    today: str | None = None,
) -> str:
    """Build the full CHANGELOG.md body (preamble + sections) and return it.
    Does not write — caller decides when to persist."""
    import datetime

    if changelog_path is None:
        changelog_path = repo_root / "CHANGELOG.md"
    last_logged = find_last_logged_version(changelog_path)
    current = (repo_root / "VERSION").read_text().strip()
    date = today or datetime.date.today().isoformat()
    commits = _collect_commits(repo_root, last_logged)
    new_section = _render_section(current, commits, date)

    if last_logged == current:
        # Current VERSION already has a section — don't double-write.
        # Caller probably ran bump_version twice without re-releasing.
        return changelog_path.read_text() if changelog_path.exists() else _PREAMBLE + new_section

    if changelog_path.exists():
        existing = changelog_path.read_text()
        # Preserve preamble; prepend new section before the first `## [v`.
        split_at = existing.find("## [v")
        if split_at < 0:
            return _PREAMBLE + new_section
        return existing[:split_at] + new_section + "\n" + existing[split_at:]
    return _PREAMBLE + new_section


def write(
    *,
    repo_root: Path = _REPO_ROOT,
    changelog_path: Path | None = None,
    today: str | None = None,
) -> Path:
    if changelog_path is None:
        changelog_path = repo_root / "CHANGELOG.md"
    body = generate(repo_root=repo_root, changelog_path=changelog_path, today=today)
    changelog_path.write_text(body)
    return changelog_path


def main() -> int:  # pragma: no cover
    path = write()
    version = read_version()
    print(f"✓ CHANGELOG.md updated for v{version} → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
