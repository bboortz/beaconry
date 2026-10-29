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
import sys
from dataclasses import dataclass, field
from pathlib import Path

CAP_ID_RE = re.compile(r"\bCAP-[A-Za-z0-9]+\b")
CAP_HEADING_RE = re.compile(r"^###\s+(CAP-[A-Za-z0-9]+)\s*:\s*(.+?)\s*$")
FIELD_RE = re.compile(r"^-\s+\*\*(?P<key>[a-zA-Z_ ]+):\*\*\s*(?P<value>.*?)\s*$")
VERIFIED_BY_ITEM_RE = re.compile(r"^\s*-\s+`([^`]+)`\s*$")

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
    verified_by: list[str] = field(default_factory=list)


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


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def run(docs: Path, tests: Path) -> int:
    """Verify bidirectional traceability. Return 0 on success, 1 on any error."""
    errors: list[str] = []

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
