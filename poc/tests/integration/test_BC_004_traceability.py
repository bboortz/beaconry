"""BC-004 traceability enforcement — integration-tier tests.

Covers:
    - CAP-T016: verify-traceability enforces bidirectional links
    - CAP-T017: Orphan tests fail the gate
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_docs_and_tests(tmp_path: Path) -> tuple[Path, Path]:
    """Return (docs_dir, tests_dir) in a tmp sandbox.

    Also seeds docs/stories/ with a sentinel BC-999-fake.md so tests that
    use `origin: BC-999` without caring about origin-validation pass.
    """
    docs = tmp_path / "docs"
    (docs / "stories").mkdir(parents=True)
    (docs / "stories" / "BC-999-fake.md").write_text("# BC-999\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    return docs, tests


def _write_capabilities(docs: Path, body: str) -> None:
    (docs / "CAPABILITIES.md").write_text(body)


def _write_test(tests: Path, name: str, body: str) -> Path:
    f = tests / f"test_{name}.py"
    f.write_text(body)
    return f


def test_happy_path_zero_mismatches(tmp_docs_and_tests: tuple[Path, Path]) -> None:
    docs, tests = tmp_docs_and_tests
    _write_capabilities(
        docs,
        """
# Beaconry capabilities

## Business capabilities

### CAP-001: Example business capability
- **type:** business
- **status:** active
- **origin:** BC-999
- **description:** fixture description
- **verified by:**
  - `tests/test_example.py`
""",
    )
    _write_test(
        tests,
        "example",
        '"""Covers:\n    - CAP-001: Example business capability\n"""\n',
    )

    from poc.scripts.verify_traceability import run

    rc = run(docs=docs, tests=tests)
    assert rc == 0


def test_orphan_test_without_covers_block_fails(tmp_docs_and_tests: tuple[Path, Path]) -> None:
    docs, tests = tmp_docs_and_tests
    _write_capabilities(docs, "## Business capabilities\n")
    _write_test(tests, "orphan", "def test_x():\n    assert True\n")

    from poc.scripts.verify_traceability import run

    rc = run(docs=docs, tests=tests)
    assert rc != 0


def test_dangling_cap_reference_fails(
    tmp_docs_and_tests: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    docs, tests = tmp_docs_and_tests
    _write_capabilities(docs, "## Business capabilities\n")
    _write_test(
        tests,
        "dangling",
        '"""Covers:\n    - CAP-X999: nonexistent\n"""\n',
    )

    from poc.scripts.verify_traceability import run

    rc = run(docs=docs, tests=tests)
    assert rc != 0
    err = capsys.readouterr().err
    assert "CAP-X999" in err
    assert "test_dangling.py" in err


def test_missing_test_file_referenced_by_capability_fails(
    tmp_docs_and_tests: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    docs, tests = tmp_docs_and_tests
    _write_capabilities(
        docs,
        """
## Business capabilities

### CAP-001: Example
- **type:** business
- **status:** active
- **origin:** BC-999
- **description:** fixture description
- **verified by:**
  - `tests/test_does_not_exist.py`
""",
    )
    # No matching test file.

    from poc.scripts.verify_traceability import run

    rc = run(docs=docs, tests=tests)
    assert rc != 0
    assert "test_does_not_exist.py" in capsys.readouterr().err


def test_unverified_active_capability_fails(
    tmp_docs_and_tests: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    docs, tests = tmp_docs_and_tests
    _write_capabilities(
        docs,
        """
## Business capabilities

### CAP-001: Example
- **type:** business
- **status:** active
- **origin:** BC-999
- **description:** fixture description
- **verified by:**
""",
    )
    # No test files, no Verified by entries.

    from poc.scripts.verify_traceability import run

    rc = run(docs=docs, tests=tests)
    assert rc != 0
    assert "CAP-001" in capsys.readouterr().err


def test_aspirational_and_deprecated_capabilities_do_not_require_tests(
    tmp_docs_and_tests: tuple[Path, Path],
) -> None:
    docs, tests = tmp_docs_and_tests
    _write_capabilities(
        docs,
        """
## Business capabilities

### CAP-001: Aspirational feature
- **type:** business
- **status:** aspirational
- **origin:** BC-999
- **description:** fixture description
- **verified by:**

### CAP-002: Old feature
- **type:** business
- **status:** deprecated
- **origin:** BC-999
- **description:** fixture description
- **verified by:**
""",
    )

    from poc.scripts.verify_traceability import run

    rc = run(docs=docs, tests=tests)
    assert rc == 0


def test_orphan_error_message_is_copy_pasteable(
    tmp_docs_and_tests: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The error for an orphan test must include a one-line template."""
    docs, tests = tmp_docs_and_tests
    _write_capabilities(docs, "## Business capabilities\n")
    _write_test(tests, "orphan", "def test_x():\n    pass\n")

    from poc.scripts.verify_traceability import run

    run(docs=docs, tests=tests)
    err = capsys.readouterr().err
    assert "Covers:" in err
    assert "CAP-NNN" in err  # the template placeholder


def test_required_fields_enforcement(
    tmp_docs_and_tests: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Capability missing type / origin / status / description is flagged."""
    docs, tests = tmp_docs_and_tests
    _write_capabilities(
        docs,
        """
## Business capabilities

### CAP-B001: Example without type origin status description
- **verified by:**
  - `tests/test_x.py`
""",
    )
    _write_test(
        tests,
        "x",
        '"""Covers:\n    - CAP-B001: Example without type origin status description\n"""\n',
    )

    from poc.scripts.verify_traceability import run

    rc = run(docs=docs, tests=tests)
    assert rc != 0
    err = capsys.readouterr().err
    assert "missing required field 'type'" in err
    assert "missing required field 'origin'" in err
    assert "missing required field 'status'" in err
    assert "missing required field 'description'" in err


def test_description_present_passes(
    tmp_docs_and_tests: tuple[Path, Path],
) -> None:
    """A cap with all required fields (incl. description) passes."""
    docs, tests = tmp_docs_and_tests
    _write_capabilities(
        docs,
        """
### CAP-B001: Example
- **type:** business
- **status:** active
- **origin:** BC-999
- **description:** A short prose description of what this capability does.
- **verified by:**
  - `tests/test_x.py`
""",
    )
    _write_test(tests, "x", '"""Covers:\n    - CAP-B001: Example\n"""\n')

    from poc.scripts.verify_traceability import run

    assert run(docs=docs, tests=tests) == 0


def test_origin_story_must_exist(
    tmp_docs_and_tests: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """origin: BC-NNN must point to a real docs/stories/BC-NNN-*.md file."""
    docs, tests = tmp_docs_and_tests
    _write_capabilities(
        docs,
        """
### CAP-B001: Example
- **type:** business
- **status:** active
- **origin:** BC-404
- **verified by:**
  - `tests/test_x.py`
""",
    )
    _write_test(tests, "x", '"""Covers:\n    - CAP-B001: Example\n"""\n')

    from poc.scripts.verify_traceability import run

    rc = run(docs=docs, tests=tests)
    assert rc != 0
    err = capsys.readouterr().err
    assert "BC-404" in err
    assert "CAP-B001" in err


def test_design_file_must_exist(
    tmp_docs_and_tests: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    docs, tests = tmp_docs_and_tests
    _write_capabilities(
        docs,
        """
### CAP-B001: Example
- **type:** business
- **status:** active
- **origin:** BC-999
- **description:** fixture description
- **design:** nonexistent.md
- **verified by:**
  - `tests/test_x.py`
""",
    )
    _write_test(tests, "x", '"""Covers:\n    - CAP-B001: Example\n"""\n')

    from poc.scripts.verify_traceability import run

    rc = run(docs=docs, tests=tests)
    assert rc != 0
    assert "nonexistent.md" in capsys.readouterr().err


def test_design_section_must_exist_in_file(
    tmp_docs_and_tests: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    docs, tests = tmp_docs_and_tests
    (tests.parent / "DESIGN.md").write_text("# Title\n\n## Real Section\nBody.\n")

    _write_capabilities(
        docs,
        """
### CAP-B001: Example
- **type:** business
- **status:** active
- **origin:** BC-999
- **description:** fixture description
- **design:** DESIGN.md § Imaginary Section
- **verified by:**
  - `tests/test_x.py`
""",
    )
    _write_test(tests, "x", '"""Covers:\n    - CAP-B001: Example\n"""\n')

    from poc.scripts.verify_traceability import run

    rc = run(docs=docs, tests=tests)
    assert rc != 0
    err = capsys.readouterr().err
    assert "Imaginary Section" in err
    assert "DESIGN.md" in err


def test_design_section_present_passes(tmp_docs_and_tests: tuple[Path, Path]) -> None:
    docs, tests = tmp_docs_and_tests
    (tests.parent / "DESIGN.md").write_text("# Title\n\n## Real Section\nBody.\n")

    _write_capabilities(
        docs,
        """
### CAP-B001: Example
- **type:** business
- **status:** active
- **origin:** BC-999
- **description:** fixture description
- **design:** DESIGN.md § Real Section
- **verified by:**
  - `tests/test_x.py`
""",
    )
    _write_test(tests, "x", '"""Covers:\n    - CAP-B001: Example\n"""\n')

    from poc.scripts.verify_traceability import run

    assert run(docs=docs, tests=tests) == 0


def test_design_without_section_only_requires_file_to_exist(
    tmp_docs_and_tests: tuple[Path, Path],
) -> None:
    docs, tests = tmp_docs_and_tests
    (tests.parent / "ANOTHER.md").write_text("# whatever\n")

    _write_capabilities(
        docs,
        """
### CAP-B001: Example
- **type:** business
- **status:** active
- **origin:** BC-999
- **description:** fixture description
- **design:** ANOTHER.md
- **verified by:**
  - `tests/test_x.py`
""",
    )
    _write_test(tests, "x", '"""Covers:\n    - CAP-B001: Example\n"""\n')

    from poc.scripts.verify_traceability import run

    assert run(docs=docs, tests=tests) == 0


def test_design_multiple_refs_comma_separated(tmp_docs_and_tests: tuple[Path, Path]) -> None:
    docs, tests = tmp_docs_and_tests
    (tests.parent / "DESIGN.md").write_text("## Alpha\n")
    (tests.parent / "memory").mkdir()
    (tests.parent / "memory" / "x.md").write_text("## Beta\n")

    _write_capabilities(
        docs,
        """
### CAP-B001: Example
- **type:** business
- **status:** active
- **origin:** BC-999
- **description:** fixture description
- **design:** DESIGN.md § Alpha, memory/x.md § Beta
- **verified by:**
  - `tests/test_x.py`
""",
    )
    _write_test(tests, "x", '"""Covers:\n    - CAP-B001: Example\n"""\n')

    from poc.scripts.verify_traceability import run

    assert run(docs=docs, tests=tests) == 0


def test_bidirectional_consistency_requires_test_file_in_verified_by(
    tmp_docs_and_tests: tuple[Path, Path],
) -> None:
    """A test that Covers CAP-001 must appear in CAP-001's Verified by list."""
    docs, tests = tmp_docs_and_tests
    _write_capabilities(
        docs,
        """
## Business capabilities

### CAP-001: Example
- **type:** business
- **status:** active
- **origin:** BC-999
- **description:** fixture description
- **verified by:**
""",
    )
    _write_test(
        tests,
        "covers_cap",
        '"""Covers:\n    - CAP-001: Example\n"""\n',
    )

    from poc.scripts.verify_traceability import run

    rc = run(docs=docs, tests=tests)
    # Test claims to cover CAP-001 but CAP-001 doesn't list the test — mismatch.
    assert rc != 0
