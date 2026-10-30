"""Bidirectional traceability enforcement between CAPABILITIES.md and tests.

Parses two sources:
  1. docs/CAPABILITIES.md — one block per capability with id, type, status,
     origin, verified-by list.
  2. poc/tests/**/test_*.py — top-of-file docstring with a `Covers:` block
     listing CAP ids.

Fails (non-zero exit, errors on stderr) when:
  - A test file has no `Covers:` block
  - A test's `Covers:` references a CAP id that doesn't exist
  - A CAP's `Verified by:` lists a test file path that doesn't exist on disk
  - A CAP with status `active` has no `Verified by:` entries
  - A test claims to cover a CAP that doesn't list the test in its
    `Verified by:` section (bidirectional consistency)

Deprecated and aspirational CAPs are exempt from the unverified check.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

CAP_ID_RE = re.compile(r"\bCAP-[A-Za-z0-9]+\b")
CAP_HEADING_RE = re.compile(r"^###\s+(CAP-[A-Za-z0-9]+)\s*:\s*(.+?)\s*$")
FIELD_RE = re.compile(r"^-\s+\*\*(?P<key>[a-zA-Z_ ]+):\*\*\s*(?P<value>.*?)\s*$")
VERIFIED_BY_ITEM_RE = re.compile(r"^\s*-\s+`([^`]+)`\s*$")
STORY_ID_RE = re.compile(r"\bBC-\d+\b")
MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")

ORPHAN_TEMPLATE = (
    "{path}: missing Covers block.\n"
    "    Add at the top of the file:\n"
    '        """<module description>\n'
    "\n"
    "        Covers:\n"
    "            - CAP-NNN: <human name>\n"
    '        """\n'
)


@dataclass
class Capability:
    cap_id: str
    name: str
    cap_type: str = "unknown"
    status: str = "unknown"
    origin: str = ""
    description: str = ""
    design: str = ""
    verified_by: list[str] = field(default_factory=list)


def parse_design_refs(design_field: str) -> list[tuple[str, str | None]]:
    """Parse 'DESIGN.md § Pipeline, memory/foo.md' → [('DESIGN.md', 'Pipeline'),
    ('memory/foo.md', None)]."""
    refs: list[tuple[str, str | None]] = []
    for piece in design_field.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "§" in piece:
            file_part, section_part = piece.split("§", 1)
            refs.append((file_part.strip(), section_part.strip()))
        else:
            refs.append((piece, None))
    return refs


def extract_headings(path: Path) -> set[str]:
    """Return the set of heading texts (any level) from a markdown file."""
    headings: set[str] = set()
    for line in path.read_text().splitlines():
        m = MARKDOWN_HEADING_RE.match(line)
        if m:
            headings.add(m.group(1).strip())
    return headings


def parse_capabilities(path: Path) -> dict[str, Capability]:
    """Parse CAPABILITIES.md into {cap_id: Capability}."""
    if not path.exists():
        return {}
    caps: dict[str, Capability] = {}
    current: Capability | None = None
    in_verified_by = False

    for raw_line in path.read_text().splitlines():
        heading = CAP_HEADING_RE.match(raw_line)
        if heading:
            current = Capability(cap_id=heading.group(1), name=heading.group(2))
            caps[current.cap_id] = current
            in_verified_by = False
            continue

        if current is None:
            continue

        field_match = FIELD_RE.match(raw_line)
        if field_match:
            key = field_match.group("key").strip().lower()
            value = field_match.group("value").strip()
            in_verified_by = False
            if key == "type":
                current.cap_type = value
            elif key == "status":
                current.status = value
            elif key == "origin":
                current.origin = value
            elif key == "description":
                current.description = value
            elif key == "design":
                current.design = value
            elif key == "verified by":
                in_verified_by = True
            continue

        if in_verified_by:
            item = VERIFIED_BY_ITEM_RE.match(raw_line)
            if item:
                current.verified_by.append(item.group(1))
            elif raw_line.strip() == "":
                continue
            else:
                in_verified_by = False

    return caps


def parse_covers_block(path: Path) -> list[str] | None:
    """Return the list of CAP ids covered by a test file, or None if no Covers block."""
    source = path.read_text()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    doc = ast.get_docstring(tree)
    if not doc or "Covers:" not in doc:
        return None
    after = doc.split("Covers:", 1)[1]
    return CAP_ID_RE.findall(after)


def iter_test_files(tests_root: Path) -> list[Path]:
    return sorted(p for p in tests_root.rglob("test_*.py") if p.is_file())


def get_current_branch() -> str:
    """Return the current git branch, or empty string on detached HEAD / no repo."""
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def check_branch_plan_doc(branch: str, docs: Path) -> list[str]:
    """Return a list of errors if branch is feat/<slug> or fix/<slug> and the
    expected plan doc doesn't exist. Empty list means pass (or N/A)."""
    if not branch:
        return []
    if "/" not in branch:
        return []
    prefix, slug = branch.split("/", 1)
    if prefix not in ("feat", "fix"):
        return []
    expected = docs / "plan" / f"{slug}-plan.md"
    if expected.exists():
        return []
    return [
        f"branch '{branch}' is a {prefix}/* branch but '{expected}' doesn't exist.\n"
        f"    Create it from docs/plan/_template.md before the next commit, OR\n"
        f"    rename the branch to a chore/* / docs/* / refactor/* type that is\n"
        f"    exempt from the plan-doc rule."
    ]


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def run(docs: Path, tests: Path, *, branch: str | None = None) -> int:
    """Verify bidirectional traceability. Return 0 on success, 1 on any error.

    When `branch` is None, the current git branch is detected automatically.
    Pass a string to force a specific branch (for tests).
    """
    errors: list[str] = []

    # Branch-plan-doc check (fast, runs first so a missing plan surfaces early).
    resolved_branch = branch if branch is not None else get_current_branch()
    errors.extend(check_branch_plan_doc(resolved_branch, docs))

    cap_file = docs / "CAPABILITIES.md"
    caps = parse_capabilities(cap_file)
    test_files = iter_test_files(tests)

    test_covers: dict[Path, list[str]] = {}
    for test_path in test_files:
        covers = parse_covers_block(test_path)
        if covers is None:
            errors.append(ORPHAN_TEMPLATE.format(path=_rel(test_path, tests)))
            continue
        test_covers[test_path] = covers

    for test_path, cap_ids in test_covers.items():
        for cap_id in cap_ids:
            if cap_id not in caps:
                errors.append(
                    f"{_rel(test_path, tests)}: references {cap_id} but it is not in "
                    f"docs/CAPABILITIES.md"
                )

    for cap in caps.values():
        for ref in cap.verified_by:
            candidates = (
                [Path(ref)]
                if Path(ref).is_absolute()
                else [
                    tests.parent / ref,
                    tests.parent.parent / ref,
                    Path.cwd() / ref,
                ]
            )
            if not any(c.exists() for c in candidates):
                errors.append(
                    f"CAPABILITIES.md § {cap.cap_id}: verified_by lists '{ref}' "
                    f"but the file does not exist"
                )

    for cap in caps.values():
        if cap.status == "active" and not cap.verified_by:
            errors.append(
                f"CAPABILITIES.md § {cap.cap_id}: status=active but no Verified by entries "
                f"(use status=aspirational for in-flight capabilities)"
            )
        for field_name, value in (
            ("type", cap.cap_type),
            ("status", cap.status),
            ("origin", cap.origin),
            ("description", cap.description),
        ):
            if not value or value == "unknown":
                errors.append(
                    f"CAPABILITIES.md § {cap.cap_id}: missing required field '{field_name}'"
                )
        if not cap.name.strip():
            errors.append(
                f"CAPABILITIES.md § {cap.cap_id}: missing human-readable name after the id"
            )

    for test_path, cap_ids in test_covers.items():
        rel = _rel(test_path, tests.parent)
        for cap_id in cap_ids:
            cap = caps.get(cap_id)
            if cap is None:
                continue
            matches = [vb for vb in cap.verified_by if vb.endswith(rel) or rel.endswith(vb)]
            if not matches:
                errors.append(
                    f"CAPABILITIES.md § {cap_id}: test {rel} claims to cover this "
                    f"capability but is not listed in Verified by"
                )

    # Origin story resolution: each BC-NNN in origin must point to a real story file.
    stories_dir = docs / "stories"
    if stories_dir.is_dir():
        story_files = {p.name: p for p in stories_dir.glob("BC-*.md")}

        def _story_exists(bc_id: str) -> bool:
            prefix = f"{bc_id}-"
            return any(name.startswith(prefix) for name in story_files)

        for cap in caps.values():
            for bc_id in STORY_ID_RE.findall(cap.origin):
                if not _story_exists(bc_id):
                    errors.append(
                        f"CAPABILITIES.md § {cap.cap_id}: origin references {bc_id} "
                        f"but no docs/stories/{bc_id}-*.md was found"
                    )

    # Design link resolution: file must exist; section (if given) must be a heading.
    repo_root = docs.parent
    for cap in caps.values():
        if not cap.design:
            continue
        for file_ref, section in parse_design_refs(cap.design):
            target = repo_root / file_ref
            if not target.exists():
                errors.append(
                    f"CAPABILITIES.md § {cap.cap_id}: design references '{file_ref}' "
                    f"but the file does not exist (resolved to {target})"
                )
                continue
            if section is not None and section not in extract_headings(target):
                errors.append(
                    f"CAPABILITIES.md § {cap.cap_id}: design references "
                    f"'{file_ref} § {section}' but no such heading exists in that file"
                )

    if errors:
        print("verify-traceability: found mismatches:\n", file=sys.stderr)
        for err in errors:
            print(err, file=sys.stderr)
        print(f"\n{len(errors)} mismatch(es).", file=sys.stderr)
        return 1

    print(f"✓ {len(caps)} capabilities, {len(test_covers)} tests, 0 mismatches")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    root = Path(args[0]) if args else Path.cwd()
    return run(docs=root / "docs", tests=root / "poc" / "tests")


if __name__ == "__main__":
    sys.exit(main())
