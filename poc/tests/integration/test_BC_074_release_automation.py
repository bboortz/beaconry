"""BC-074 — release automation, 24h soak, feature-complete lint, CI-FAILURES log.

Covers:
    - CAP-T147: CI gated-promote path (BC-074 added workflow_dispatch promote)
    - CAP-T148: 24h soak default + MM-DD timestamp
    - CAP-T152: Release automation workflow (release.yml)
    - CAP-T153: Feature-completeness lint
    - CAP-T154: CI-failure learning log
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import yaml

from poc.scripts import soak as sk
from poc.scripts import verify_feature_complete as vfc

REPO_ROOT = Path(__file__).resolve().parents[3]


# ── CAP-T148: 24h soak ──────────────────────────────────────────────────────


def test_soak_default_window_is_24h(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T148: default SOAK_WINDOW_S is 86400 (24h). An event 2h old
    counts in the window — pre-BC-074 (1h default) it would have been
    outside and treated as clean."""
    monkeypatch.delenv("SOAK_WINDOW_S", raising=False)
    now = int(time.time())
    (tmp_path / "audit.log").write_text(
        json.dumps(
            {
                "ts": now - 7200,  # 2h ago
                "event": "notify_sent_failed",
                "finding_id": "ab",
                "cve_id": "CVE-X",
            }
        )
        + "\n"
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    rc = sk.soak(now=now)
    assert rc == 1
    out = capsys.readouterr().out
    assert "24h" in out


def test_soak_timestamp_shows_month_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T148: 24h windows cross midnight — timestamps include MM-DD
    so operator can tell a 23h-old event from a 1h-old one on different days."""
    monkeypatch.delenv("SOAK_WINDOW_S", raising=False)
    now = int(time.time())
    (tmp_path / "audit.log").write_text(
        json.dumps({"ts": now - 60, "event": "regressed", "cve_id": "CVE-X"}) + "\n"
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    sk.soak(now=now)
    out = capsys.readouterr().out
    # Format: MM-DD HH:MM:SS. Look for the "DD HH:" shape.
    import re

    assert re.search(r"\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", out), out


def test_soak_window_override_still_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T148 regression guard: operator can still shrink to 1h via env.
    An event 2h old is OUTSIDE a 1h window → clean. In-window `scan_received`
    added so BC-075's pipeline-silence check doesn't trip."""
    now = int(time.time())
    (tmp_path / "audit.log").write_text(
        "\n".join(
            [
                json.dumps({"ts": now - 7200, "event": "regressed", "cve_id": "CVE-X"}),
                json.dumps({"ts": now - 60, "event": "scan_received", "host": "h1"}),
            ]
        )
        + "\n"
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOAK_WINDOW_S", "3600")
    rc = sk.soak(now=now)
    assert rc == 0  # event is 2h old, 1h window → outside → clean


# ── CAP-T152: release.yml ───────────────────────────────────────────────────


def _load_release_yaml() -> dict:
    with open(REPO_ROOT / ".github" / "workflows" / "release.yml") as f:
        return yaml.safe_load(f)


def test_release_yaml_parses() -> None:
    """CAP-T152: workflow file is syntactically valid YAML."""
    doc = _load_release_yaml()
    assert "jobs" in doc
    assert "promote" in doc["jobs"]


def test_release_yaml_dispatch_only() -> None:
    """CAP-T152: release workflow MUST only trigger via workflow_dispatch —
    not on push. Operator reserves the trigger as the review step."""
    doc = _load_release_yaml()
    triggers = doc.get("on") or doc.get(True)  # yaml quirk: 'on' is parsed as bool True
    assert triggers is not None
    assert "workflow_dispatch" in triggers
    assert "push" not in triggers
    assert "pull_request" not in triggers


def test_release_yaml_has_ff_main_and_tag_steps() -> None:
    """CAP-T152: core promotion steps are present (ff-main, tag, push)."""
    doc = _load_release_yaml()
    steps = doc["jobs"]["promote"]["steps"]
    step_names = {s.get("name", "") for s in steps if isinstance(s, dict)}
    assert any("Fast-forward" in n for n in step_names)
    assert any("Tag" in n for n in step_names)
    assert any("CI" in n for n in step_names)  # CI-green assertion step


def test_release_yaml_requires_contents_write() -> None:
    """CAP-T152: pushing to main requires contents:write."""
    doc = _load_release_yaml()
    perms = doc.get("permissions", {})
    assert perms.get("contents") == "write"


# ── CAP-T153: verify-feature-complete ───────────────────────────────────────


def _mkstory(path: Path, bc_id: str, status: str) -> None:
    path.write_text(f"---\nid: {bc_id}\ntitle: test\nstatus: {status}\n---\n\n# {bc_id}\n")


def _mkplan(path: Path, bc_id: str, rows: list[str]) -> None:
    """rows is a list of status glyphs (✓ / ❌ / ⚠️)."""
    body = f"# {bc_id} — plan\n\n## Shipping evidence\n\n| Scenario | Pointer | Status |\n|---|---|---|\n"
    for i, row in enumerate(rows):
        body += f"| scenario {i} | pointer-{i} | {row} |\n"
    path.write_text(body)


def test_verify_feature_complete_blocks_shipped_with_open_rows(tmp_path: Path) -> None:
    """CAP-T153: a shipped story with a ❌ row in the plan blocks the push."""
    (tmp_path / "docs" / "stories").mkdir(parents=True)
    (tmp_path / "docs" / "plan").mkdir(parents=True)
    _mkstory(tmp_path / "docs" / "stories" / "BC-999-x.md", "BC-999", "shipped")
    _mkplan(
        tmp_path / "docs" / "plan" / "BC-999-x-plan.md",
        "BC-999",
        rows=["✓", "❌"],
    )
    errors = vfc.check(tmp_path)
    assert errors
    assert any("BC-999" in e and "open row" in e for e in errors)


def test_verify_feature_complete_allows_in_progress_with_open_rows(tmp_path: Path) -> None:
    """CAP-T153: an in-progress story is allowed to have open rows (WIP)."""
    (tmp_path / "docs" / "stories").mkdir(parents=True)
    (tmp_path / "docs" / "plan").mkdir(parents=True)
    _mkstory(tmp_path / "docs" / "stories" / "BC-998-x.md", "BC-998", "in-progress")
    _mkplan(
        tmp_path / "docs" / "plan" / "BC-998-x-plan.md",
        "BC-998",
        rows=["❌", "❌"],
    )
    assert vfc.check(tmp_path) == []


def test_verify_feature_complete_flags_orphan_plans(tmp_path: Path) -> None:
    """CAP-T153: a plan without a paired story is flagged."""
    (tmp_path / "docs" / "stories").mkdir(parents=True)
    (tmp_path / "docs" / "plan").mkdir(parents=True)
    _mkplan(tmp_path / "docs" / "plan" / "BC-997-orphan-plan.md", "BC-997", rows=["✓"])
    errors = vfc.check(tmp_path)
    assert any("BC-997" in e and "no paired story" in e for e in errors)


def test_verify_feature_complete_passes_live() -> None:
    """CAP-T153 regression guard: live repo passes the lint. If we ever
    ship a BC with ❌ rows, this fires."""
    errors = vfc.check(REPO_ROOT)
    assert errors == [], errors


def test_verify_feature_complete_bypass_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T153: BEACONRY_SKIP_FEATURE_COMPLETE=1 short-circuits."""
    (tmp_path / "docs" / "stories").mkdir(parents=True)
    (tmp_path / "docs" / "plan").mkdir(parents=True)
    _mkstory(tmp_path / "docs" / "stories" / "BC-996-x.md", "BC-996", "shipped")
    _mkplan(
        tmp_path / "docs" / "plan" / "BC-996-x-plan.md",
        "BC-996",
        rows=["❌", "❌"],
    )
    monkeypatch.setenv("BEACONRY_SKIP_FEATURE_COMPLETE", "1")
    assert vfc.check(tmp_path) == []


def test_verify_feature_complete_ignores_template_files(tmp_path: Path) -> None:
    """CAP-T153: `_template.md` and `_8-questions.md` under stories/
    aren't real stories and shouldn't be linted."""
    (tmp_path / "docs" / "stories").mkdir(parents=True)
    (tmp_path / "docs" / "plan").mkdir(parents=True)
    (tmp_path / "docs" / "stories" / "_template.md").write_text("---\nstatus: shipped\n---\n")
    assert vfc.check(tmp_path) == []


# ── CAP-T154: CI-FAILURES.md ───────────────────────────────────────────────


def test_ci_failures_log_present() -> None:
    """CAP-T154: the learning log exists and has the backfilled entries."""
    log = REPO_ROOT / "docs" / "plan" / "CI-FAILURES.md"
    assert log.exists()
    body = log.read_text()
    # Backfilled entries we promised in BC-074's plan.
    assert "BC-071" in body or "BEACONRY_SKIP_VERSION_BUMP" in body
    assert "PR #1" in body or "pytest-asyncio" in body
    # Header format discipline.
    assert "Append-only" in body
    assert "**Root cause**" in body
