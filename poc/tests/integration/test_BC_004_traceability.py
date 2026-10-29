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
    """Return (docs_dir, tests_dir) in a tmp sandbox."""
    docs = tmp_path / "docs"
    docs.mkdir()
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
- **verified by:**

### CAP-002: Old feature
- **type:** business
- **status:** deprecated
- **origin:** BC-998
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
    """Capability missing type / origin / status is flagged."""
    docs, tests = tmp_docs_and_tests
    _write_capabilities(
        docs,
        """
## Business capabilities

### CAP-B001: Example without type and origin
- **verified by:**
  - `tests/test_x.py`
""",
    )
    _write_test(
        tests,
        "x",
        '"""Covers:\n    - CAP-B001: Example without type and origin\n"""\n',
    )

    from poc.scripts.verify_traceability import run

    rc = run(docs=docs, tests=tests)
    assert rc != 0
    err = capsys.readouterr().err
    assert "missing required field 'type'" in err
    assert "missing required field 'origin'" in err
    assert "missing required field 'status'" in err


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
