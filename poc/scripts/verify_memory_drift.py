"""BC-015 — memory drift regression guard.

Reads `poc/scripts/memory_drift_bans.toml`, scans `memory/*.md` for banned
regex patterns, and reports violations with their originating `banned_since`
BC id + reason.

Design: patterns are append-only. Each entry documents a historical fact
("this drift happened once, don't let it recur"). If a pattern becomes
over-broad, add `exception_regex` entries — don't delete the pattern.

Hooked into `make check`. Exits 0 clean, 1 on any violation.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Ban:
    pattern: str
    banned_since: str
    reason: str
    exception_regex: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Violation:
    file: Path
    line_number: int
    line_text: str
    banned_since: str
    reason: str
    pattern: str


def load_bans(path: Path) -> list[Ban]:
    """Parse a bans TOML file. Raises ValueError on missing required fields."""
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    bans_raw = data.get("ban", [])
    out: list[Ban] = []
    for i, entry in enumerate(bans_raw):
        if "pattern" not in entry:
            raise ValueError(f"ban #{i} missing required field 'pattern'")
        if "banned_since" not in entry:
            raise ValueError(f"ban #{i} missing required field 'banned_since'")
        if "reason" not in entry:
            raise ValueError(f"ban #{i} missing required field 'reason'")
        out.append(
            Ban(
                pattern=entry["pattern"],
                banned_since=entry["banned_since"],
                reason=entry["reason"],
                exception_regex=list(entry.get("exception_regex", [])),
            )
        )
    return out


def scan(bans_path: Path, memory_dir: Path) -> list[Violation]:
    """Scan every `memory/*.md` for banned patterns. Return all violations.

    Exceptions: if a matching line is also matched by any `exception_regex`,
    it's a legitimate historical mention and does not count.
    """
    bans = load_bans(bans_path)
    violations: list[Violation] = []
    for md_file in sorted(memory_dir.glob("*.md")):
        try:
            lines = md_file.read_text().splitlines()
        except OSError:
            continue
        for ban in bans:
            compiled = re.compile(ban.pattern)
            exceptions = [re.compile(e) for e in ban.exception_regex]
            for lineno, line in enumerate(lines, start=1):
                if not compiled.search(line):
                    continue
                if any(exc.search(line) for exc in exceptions):
                    continue
                violations.append(
                    Violation(
                        file=md_file,
                        line_number=lineno,
                        line_text=line.strip(),
                        banned_since=ban.banned_since,
                        reason=ban.reason,
                        pattern=ban.pattern,
                    )
                )
                break  # one hit per (file, ban) is enough — operator will see the line
    return violations


def format_violation(v: Violation) -> str:
    """Human-readable error line: file:line · BC-id · reason · matched text."""
    return (
        f"{v.file}:{v.line_number}: "
        f"[{v.banned_since}] matched banned pattern /{v.pattern}/ — "
        f"{v.reason}\n"
        f"    → {v.line_text}"
    )


def main() -> int:  # pragma: no cover - entry-point wrapper exercised via make check, not pytest
    repo_root = Path(__file__).resolve().parents[2]
    bans_file = repo_root / "poc" / "scripts" / "memory_drift_bans.toml"
    memory_dir = repo_root / "memory"
    if not bans_file.exists():
        print(f"verify-memory-drift: no bans file at {bans_file}; nothing to check")
        return 0
    try:
        violations = scan(bans_file, memory_dir)
    except ValueError as e:
        print(f"verify-memory-drift: malformed bans file: {e}", file=sys.stderr)
        return 1

    bans = load_bans(bans_file)
    if not violations:
        print(
            f"✓ {len(bans)} drift pattern(s) checked across {len(list(memory_dir.glob('*.md')))} memory file(s), 0 drifts found"
        )
        return 0

    print(f"verify-memory-drift: {len(violations)} drift(s) found:", file=sys.stderr)
    for v in violations:
        print(f"  {format_violation(v)}", file=sys.stderr)
    print(
        "\n  hint: either update the memory file to match current reality, "
        "or add an `exception_regex` entry in `poc/scripts/memory_drift_bans.toml` "
        "if this is a legitimate historical mention.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
