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

CAP_ID_RE = re.compile(r"\bCAP-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b")
CAP_HEADING_RE = re.compile(r"^###\s+(CAP-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)\s*:\s*(.+?)\s*$")
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


def parse_capabilities(path: Path) -> dict[str, Capability]:  # noqa: C901 — stateful markdown block parser; refactoring to a proper AST walker is logged in IDEAS.md
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


def _story_is_shipped(story_path: Path) -> bool:
    """Return True if the story file's frontmatter has status: shipped."""
    if not story_path.exists():
        return False
    for line in story_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("status:"):
            return stripped.split(":", 1)[1].strip() == "shipped"
        if stripped == "---" and "status:" in story_path.read_text().split("---", 2)[1]:
            continue
    return False


def _parse_shipping_evidence_rows(plan_text: str) -> list[list[str]] | None:
    """Return the parsed rows of the Shipping evidence table, or None if the
    section is absent (grandfathered). Returns an empty list if the section
    exists but the table is empty."""
    # Anchor to line-start so inline mentions in prose don't false-match.
    heading_match = re.search(r"^## Shipping evidence\s*$", plan_text, re.MULTILINE)
    if not heading_match:
        return None
    after = plan_text[heading_match.end() :]
    # Table ends at the next heading or EOF.
    next_heading = re.search(r"^##[^#]", after, re.MULTILINE)
    section = after[: next_heading.start()] if next_heading else after
    rows: list[list[str]] = []
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # Skip header + separator rows.
        if cells and cells[0].lower() in {"scenario / goal", "scenario", ""}:
            continue
        if all(set(c) <= {"-", " "} for c in cells):
            continue
        rows.append(cells)
    return rows


def _count_gherkin_scenarios(story_text: str) -> int:
    """Count `Scenario:` lines in the story's Gherkin block. 0 if no Gherkin found."""
    return len(re.findall(r"^\s*Scenario:", story_text, re.MULTILINE))


def check_shipping_evidence(story_path: Path, plan_path: Path) -> list[str]:
    """For a `shipped` story, assert its plan's Shipping evidence table is complete.
    Returns a list of error strings; empty = pass.

    Also warns (via stderr, not an error) when the story's Gherkin has more
    Scenario: lines than the plan's evidence table has rows — a hint that
    scenarios may be missing from the table (though a single row can
    legitimately roll up multiple scenarios).
    """
    errors: list[str] = []
    if not _story_is_shipped(story_path):
        return errors  # not shipped yet — audit happens at ship time
    if not plan_path.exists():
        return errors  # plan absent is a separate concern (CAP-T022)

    plan_text = plan_path.read_text()
    rows = _parse_shipping_evidence_rows(plan_text)
    if rows is None:
        return errors  # section absent — grandfathered pre-BC-008 plan

    if not rows:
        errors.append(
            f"{plan_path}: Shipping evidence section exists but table is empty "
            f"(story is shipped; fill the table or revert status)"
        )
        return errors

    for i, row in enumerate(rows, start=1):
        if any("<copy a" in cell or "<test name" in cell for cell in row):
            errors.append(
                f"{plan_path}: Shipping evidence row {i} still has template "
                f"placeholder text — fill it before shipping"
            )
            continue
        status = row[-1] if row else ""
        if "✓" not in status:
            errors.append(
                f"{plan_path}: Shipping evidence row {i} has status {status!r} — "
                f"a shipped story requires every row at ✓"
            )

    # Advisory: Gherkin scenario count vs evidence row count.
    scenario_count = _count_gherkin_scenarios(story_path.read_text())
    if scenario_count > len(rows):
        print(
            f"verify-traceability: WARNING: {story_path.name} has {scenario_count} "
            f"Gherkin Scenarios but {plan_path.name} has {len(rows)} evidence rows. "
            f"A row may legitimately roll up multiple scenarios — if so, ignore. "
            f"If not, add the missing rows.",
            file=sys.stderr,
        )

    return errors


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _check_shipping_evidence_all(docs: Path) -> list[str]:
    """BC-008 pre-ship audit — every shipped story's plan has complete evidence."""
    errors: list[str] = []
    stories_dir = docs / "stories"
    plans_dir = docs / "plan"
    if not (stories_dir.is_dir() and plans_dir.is_dir()):
        return errors
    for story_file in stories_dir.glob("BC-*.md"):
        slug = story_file.stem
        plan_file = plans_dir / f"{slug}-plan.md"
        errors.extend(check_shipping_evidence(story_file, plan_file))
    return errors


def _collect_test_covers(
    tests: Path,
) -> tuple[dict[Path, list[str]], list[str]]:
    """Parse Covers: blocks for every test file. Returns (covers, orphan_errors)."""
    errors: list[str] = []
    test_covers: dict[Path, list[str]] = {}
    for test_path in iter_test_files(tests):
        covers = parse_covers_block(test_path)
        if covers is None:
            errors.append(ORPHAN_TEMPLATE.format(path=_rel(test_path, tests)))
            continue
        test_covers[test_path] = covers
    return test_covers, errors


def _check_dangling_cap_refs(
    test_covers: dict[Path, list[str]], caps: dict[str, Capability], tests: Path
) -> list[str]:
    """Covers block references a cap id that doesn't exist in CAPABILITIES.md."""
    return [
        f"{_rel(test_path, tests)}: references {cap_id} but it is not in docs/CAPABILITIES.md"
        for test_path, cap_ids in test_covers.items()
        for cap_id in cap_ids
        if cap_id not in caps
    ]


def _check_verified_by_file_exists(caps: dict[str, Capability], tests: Path) -> list[str]:
    """Verified-by must list a file that resolves on disk."""
    errors: list[str] = []
    for cap in caps.values():
        for ref in cap.verified_by:
            candidates = (
                [Path(ref)]
                if Path(ref).is_absolute()
                else [tests.parent / ref, tests.parent.parent / ref, Path.cwd() / ref]
            )
            if not any(c.exists() for c in candidates):
                errors.append(
                    f"CAPABILITIES.md § {cap.cap_id}: verified_by lists '{ref}' "
                    f"but the file does not exist"
                )
    return errors


def _check_cap_shape(caps: dict[str, Capability]) -> list[str]:
    """Active CAPs have verified-by; all CAPs have required fields + a name."""
    errors: list[str] = []
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
    return errors


def _check_forward_bidirectional(
    test_covers: dict[Path, list[str]], caps: dict[str, Capability], tests: Path
) -> list[str]:
    """Test claims a CAP → the CAP's Verified by must list the test file."""
    errors: list[str] = []
    for test_path, cap_ids in test_covers.items():
        rel = _rel(test_path, tests.parent)
        for cap_id in cap_ids:
            cap = caps.get(cap_id)
            if cap is None:
                continue
            if not any(vb.endswith(rel) or rel.endswith(vb) for vb in cap.verified_by):
                errors.append(
                    f"CAPABILITIES.md § {cap_id}: test {rel} claims to cover this "
                    f"capability but is not listed in Verified by"
                )
    return errors


def _check_reverse_bidirectional(
    test_covers: dict[Path, list[str]], caps: dict[str, Capability], tests: Path
) -> list[str]:
    """BC-024: CAP lists a test file → file's Covers must claim the CAP.

    File-missing errors already fire in _check_verified_by_file_exists —
    this skips those cases to avoid double-report."""
    errors: list[str] = []
    rels_by_test = {test_path: _rel(test_path, tests.parent) for test_path in test_covers}
    for cap in caps.values():
        for vb in cap.verified_by:
            matched_covers: list[str] | None = None
            for test_path, rel in rels_by_test.items():
                if vb.endswith(rel) or rel.endswith(vb):
                    matched_covers = test_covers[test_path]
                    break
            if matched_covers is None or cap.cap_id in matched_covers:
                continue
            errors.append(
                f"CAPABILITIES.md § {cap.cap_id} lists {vb} as verified-by, but "
                f"that file's Covers: block doesn't claim {cap.cap_id}. "
                f"Add '- {cap.cap_id}: ...' to the Covers block, or remove the "
                f"file from verified-by."
            )
    return errors


def _check_origin_story_exists(caps: dict[str, Capability], docs: Path) -> list[str]:
    """Each BC-NNN in a CAP's origin must point to a real docs/stories/BC-NNN-*.md."""
    stories_dir = docs / "stories"
    if not stories_dir.is_dir():
        return []
    story_names = {p.name for p in stories_dir.glob("BC-*.md")}

    def _story_exists(bc_id: str) -> bool:
        prefix = f"{bc_id}-"
        return any(name.startswith(prefix) for name in story_names)

    errors: list[str] = []
    for cap in caps.values():
        for bc_id in STORY_ID_RE.findall(cap.origin):
            if not _story_exists(bc_id):
                errors.append(
                    f"CAPABILITIES.md § {cap.cap_id}: origin references {bc_id} "
                    f"but no docs/stories/{bc_id}-*.md was found"
                )
    return errors


def _check_design_links(caps: dict[str, Capability], docs: Path) -> list[str]:
    """Design field file-refs must exist; '§ Heading' refs must match a real heading."""
    errors: list[str] = []
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
    return errors


def run(docs: Path, tests: Path, *, branch: str | None = None) -> int:
    """Verify bidirectional traceability. Return 0 on success, 1 on any error.

    When `branch` is None, the current git branch is detected automatically.
    Pass a string to force a specific branch (for tests).

    Orchestrates a registry of independent gate checkers — each returns its
    own list of error strings so that a single failing gate doesn't mask
    others. Add a new gate by writing `_check_<name>` and appending it to
    the registry below.
    """
    resolved_branch = branch if branch is not None else get_current_branch()
    caps = parse_capabilities(docs / "CAPABILITIES.md")
    test_covers, orphan_errors = _collect_test_covers(tests)

    errors: list[str] = []
    errors.extend(check_branch_plan_doc(resolved_branch, docs))
    errors.extend(_check_shipping_evidence_all(docs))
    errors.extend(orphan_errors)
    errors.extend(_check_dangling_cap_refs(test_covers, caps, tests))
    errors.extend(_check_verified_by_file_exists(caps, tests))
    errors.extend(_check_cap_shape(caps))
    errors.extend(_check_forward_bidirectional(test_covers, caps, tests))
    errors.extend(_check_reverse_bidirectional(test_covers, caps, tests))
    errors.extend(_check_origin_story_exists(caps, docs))
    errors.extend(_check_design_links(caps, docs))

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
