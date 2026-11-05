"""BC-059 — Telegram callback_data carries CVE id.

Covers:
    - CAP-T118: callback_data reshape with CVE suffix + 64-byte guard
    - CAP-T119: Backward-compatible CALLBACK_RE accepts both 3-part and 4-part
    - CAP-B042: Operator sees the CVE in the Telegram "Finding not found" error
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")

    import hub

    importlib.reload(hub)
    hub.init_db()
    yield {"hub": hub, "db": hub.DB_PATH, "audit": data_dir / "audit.log"}
    importlib.reload(hub)


def _finding(hub, *, fid: str = "f0b442fb8acffb61", cve: str = "CVE-2026-111497") -> object:
    return hub.Finding(
        id=fid,
        host="laptop",
        cve_id=cve,
        package="brotli-libs",
        version="1.0",
        fixed_version="1.1",
        severity="Critical",
        cvss=9.8,
        source_type="docker",
        source_ref="nginx:alpine",
        description="d",
        tier=2,
    )


# ── _cb_data builder ───────────────────────────────────────────────────────


def test_cb_data_includes_cve_suffix(hub_env) -> None:
    """CAP-T118: _cb_data appends :cve_id after the action/id/extras."""
    hub = hub_env["hub"]
    f = _finding(hub)
    assert hub._cb_data("fix", f) == "fix:f0b442fb8acffb61:CVE-2026-111497"
    assert hub._cb_data("snooze", f, "4") == "snooze:f0b442fb8acffb61:4:CVE-2026-111497"


def test_cb_data_drops_cve_over_cap(hub_env) -> None:
    """CAP-T118: 64-byte guard — defensively drop CVE if it would blow the cap."""
    hub = hub_env["hub"]
    # Fabricate a pathological 48-char cve id. (Real ids never approach this.)
    f = _finding(hub, cve="X" + "0" * 47)
    cb = hub._cb_data("snooze", f, "9999")
    assert len(cb.encode()) <= 64
    # And it fell back to the no-CVE shape.
    assert cb == "snooze:f0b442fb8acffb61:9999"


def test_cb_data_no_cve_when_cve_id_empty(hub_env) -> None:
    """When finding.cve_id is empty, _cb_data emits the no-CVE shape."""
    hub = hub_env["hub"]
    f = _finding(hub, cve="")
    assert hub._cb_data("fix", f) == "fix:f0b442fb8acffb61"


def test_callback_data_size_guard(hub_env) -> None:
    """CAP-T118: every _cb_data output must fit in Telegram's 64-byte limit."""
    hub = hub_env["hub"]
    for cve in (
        "CVE-2026-111497",
        "GO-2026-1234567890",
        "AVG-2909",
        "CVE-2026-0",  # minimal
    ):
        f = _finding(hub, cve=cve)
        for action in ("fix", "ack", "verify"):
            cb = hub._cb_data(action, f)
            assert len(cb.encode()) <= 64, (action, cve, cb)
        cb = hub._cb_data("snooze", f, "4")
        assert len(cb.encode()) <= 64, ("snooze", cve, cb)


# ── Keyboard builders use the new shape ────────────────────────────────────


def _unwrap_cb(markup) -> list[str]:  # type: ignore[no-untyped-def]
    """Flatten InlineKeyboardMarkup → list of callback_data strings."""
    out: list[str] = []
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data:
                out.append(btn.callback_data)
    return out


def test_fix_button_has_cve(hub_env) -> None:
    """CAP-T118: _kb_finding Fix button carries the CVE."""
    hub = hub_env["hub"]
    cbs = _unwrap_cb(hub._kb_finding(_finding(hub)))
    assert any(c.startswith("fix:") and c.endswith(":CVE-2026-111497") for c in cbs)


def test_snooze_button_has_cve_after_hours(hub_env) -> None:
    """CAP-T118: Snooze button is `snooze:<id>:<hours>:CVE-…` (CVE after hours).

    BC-089: keyboard now has multiple snooze buttons (one per hour in
    `BEACONRY_SNOOZE_DEFAULT_HOURS`). Picking the 4h variant for the
    callback-shape assertion since `4` is always in the default list.
    """
    hub = hub_env["hub"]
    cbs = _unwrap_cb(hub._kb_finding(_finding(hub)))
    snooze = next(c for c in cbs if c.startswith("snooze:") and c.split(":")[2] == "4")
    parts = snooze.split(":")
    assert parts[0] == "snooze"
    assert parts[1] == "f0b442fb8acffb61"
    assert parts[2] == "4"
    assert parts[3] == "CVE-2026-111497"


def test_ack_button_has_cve(hub_env) -> None:
    hub = hub_env["hub"]
    cbs = _unwrap_cb(hub._kb_finding(_finding(hub)))
    assert any(c.startswith("ack:") and c.endswith(":CVE-2026-111497") for c in cbs)


def test_regression_kb_has_cve(hub_env) -> None:
    """CAP-T118: BC-031 regression keyboard carries CVE on both buttons."""
    hub = hub_env["hub"]
    cbs = _unwrap_cb(hub._kb_regression(_finding(hub)))
    assert all(c.endswith(":CVE-2026-111497") for c in cbs)
    # Fix + Ack, no Snooze.
    assert any(c.startswith("fix:") for c in cbs)
    assert any(c.startswith("ack:") for c in cbs)
    assert not any(c.startswith("snooze:") for c in cbs)


def test_verify_button_has_cve(hub_env) -> None:
    """CAP-T118: static grep — verify button is built via _cb_data in BC-059."""
    hub = hub_env["hub"]
    src = Path(hub.__file__).read_text()
    assert 'callback_data=_cb_data("verify"' in src


# ── CALLBACK_RE accepts both shapes ────────────────────────────────────────


def test_old_callback_data_still_matches(hub_env) -> None:
    """CAP-T119: pre-BC-059 3-part callback_data still parses (back-compat)."""
    hub = hub_env["hub"]
    for data in (
        "fix:f0b442fb8acffb61",
        "snooze:f0b442fb8acffb61:4",
        "ack:f0b442fb8acffb61",
        "verify:f0b442fb8acffb61",
    ):
        assert hub.CALLBACK_RE.match(data), data


def test_new_callback_data_matches_with_cve(hub_env) -> None:
    """CAP-T119: 4-part callback_data with CVE suffix parses."""
    hub = hub_env["hub"]
    for data in (
        "fix:f0b442fb8acffb61:CVE-2026-111497",
        "snooze:f0b442fb8acffb61:4:CVE-2026-111497",
        "ack:f0b442fb8acffb61:GO-2026-1234567",
        "verify:f0b442fb8acffb61:AVG-2909",
    ):
        assert hub.CALLBACK_RE.match(data), data


def test_callback_re_rejects_garbage(hub_env) -> None:
    """Defensive: anything outside the shape still fails."""
    hub = hub_env["hub"]
    for data in (
        "snooze:f0b442fb8acffb61:4:not-a-cve-id-lowercase",
        "evil:f0b442fb8acffb61",
        "fix:tooShort",
        "fix:f0b442fb8acffb61:4:CVE-2026-111497:extra",
    ):
        assert not hub.CALLBACK_RE.match(data), data


# ── Not-found error names CVE ──────────────────────────────────────────────


def test_not_found_names_cve_when_in_callback(hub_env) -> None:
    """CAP-B042: error subject references the CVE from callback_data."""
    src = Path(hub_env["hub"].__file__).read_text()
    # New error template splits on parts and surfaces cve_in_cb.
    assert "cve_in_cb" in src
    assert "{_esc(cve_in_cb)}" in src


def test_not_found_falls_back_to_id_without_cve(hub_env) -> None:
    """CAP-B042: old 3-part callback_data (no CVE) → id-prefix fallback."""
    src = Path(hub_env["hub"].__file__).read_text()
    # Fallback branch still emits the id prefix in a 'finding …' subject.
    assert '"finding <code>{short_id}…</code>"' in src


# ── Smoke: end-to-end through on_button_click ─────────────────────────────


@pytest.mark.asyncio
async def test_on_button_click_old_shape_still_works(hub_env) -> None:
    """E2E: pre-BC-059 callback_data (no CVE) routes to the handler and the
    error branch triggers with the id-prefix wording when the finding is
    missing. Validates back-compat end-to-end."""
    hub = hub_env["hub"]

    class _FakeQuery:
        data = "snooze:f0b442fb8acffb61:4"
        edited: str | None = None

        async def answer(self):
            return None

        async def edit_message_text(self, text, **kwargs):
            _FakeQuery.edited = text

    class _FakeUpdate:
        callback_query = _FakeQuery()

    await hub.on_button_click(_FakeUpdate(), None)
    assert "finding <code>f0b442fb" in _FakeQuery.edited  # type: ignore[operator]
    assert "stale notification" in _FakeQuery.edited.lower()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_on_button_click_new_shape_names_cve(hub_env) -> None:
    """E2E: post-BC-059 callback_data (with CVE) + missing finding →
    error names the CVE."""
    hub = hub_env["hub"]

    class _FakeQuery:
        data = "snooze:f0b442fb8acffb61:4:CVE-2026-111497"
        edited: str | None = None

        async def answer(self):
            return None

        async def edit_message_text(self, text, **kwargs):
            _FakeQuery.edited = text

    class _FakeUpdate:
        callback_query = _FakeQuery()

    await hub.on_button_click(_FakeUpdate(), None)
    edited = _FakeQuery.edited
    assert edited is not None
    assert "CVE-2026-111497" in edited
    assert "f0b442fb" in edited  # id prefix still shown as a secondary id
