"""BC-091 — KEV fetch uses identifiable User-Agent.

Covers:
    - CAP-T187: KEV fetch User-Agent is identifiable + operator-overridable
"""

from __future__ import annotations

import importlib

import pytest


def _reload_kev():
    """Re-import the kev module so module-level env reads re-evaluate."""
    import poc.hub.kev as kev_mod

    importlib.reload(kev_mod)
    return kev_mod


# ── Scenario 1 + 4: default UA names version + repo ─────────────────────────


def test_default_user_agent_names_version_and_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_KEV_USER_AGENT", raising=False)
    kev = _reload_kev()
    from poc.beaconry_health import SOFTWARE_VERSION

    assert (
        f"beaconry/{SOFTWARE_VERSION} (+https://github.com/bboortz/beaconry)"
    ) == kev.KEV_USER_AGENT


def test_ua_version_tracks_software_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """UA always embeds the current SOFTWARE_VERSION — not a frozen string."""
    monkeypatch.delenv("BEACONRY_KEV_USER_AGENT", raising=False)
    kev = _reload_kev()
    from poc.beaconry_health import SOFTWARE_VERSION

    assert f"beaconry/{SOFTWARE_VERSION}" in kev.KEV_USER_AGENT
    assert "(+https://github.com/bboortz/beaconry)" in kev.KEV_USER_AGENT


# ── Scenario 2: operator override ───────────────────────────────────────────


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    custom = "custom-ua/1.0 (+contact@example.com)"
    monkeypatch.setenv("BEACONRY_KEV_USER_AGENT", custom)
    kev = _reload_kev()
    assert custom == kev.KEV_USER_AGENT
    # Cleanup: restore default for other tests.
    monkeypatch.delenv("BEACONRY_KEV_USER_AGENT")
    _reload_kev()


# ── Scenario 3: empty env falls back ────────────────────────────────────────


def test_empty_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_KEV_USER_AGENT", "")
    kev = _reload_kev()
    assert "beaconry/" in kev.KEV_USER_AGENT
    assert "(+https://github.com/bboortz/beaconry)" in kev.KEV_USER_AGENT
    monkeypatch.delenv("BEACONRY_KEV_USER_AGENT")
    _reload_kev()


# ── Scenario 5: _http_fetch sends the configured UA ────────────────────────


def test_http_fetch_sends_configured_ua(monkeypatch: pytest.MonkeyPatch) -> None:
    """_http_fetch builds a Request with User-Agent header; ensure the
    header matches the module-level KEV_USER_AGENT, not the old stale
    hardcoded string."""
    monkeypatch.delenv("BEACONRY_KEV_USER_AGENT", raising=False)
    kev = _reload_kev()

    captured_headers: dict[str, str] = {}

    class _FakeResp:
        def read(self) -> bytes:
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def _fake_urlopen(req, timeout=None):
        for k, v in req.headers.items():
            captured_headers[k.lower()] = v
        return _FakeResp()

    monkeypatch.setattr(kev.urllib.request, "urlopen", _fake_urlopen)
    kev._http_fetch("https://example.com/kev.json")
    assert "user-agent" in captured_headers
    assert captured_headers["user-agent"] == kev.KEV_USER_AGENT
    # Negative: old hardcoded string MUST NOT appear.
    assert captured_headers["user-agent"] != "beaconry/0.1 (+kev)"


# ── Regression guard: the default UA must contain a repo URL ───────────────


def test_default_ua_contains_contact_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """CISA policy: identifiable UA with contact URL. A future refactor
    that drops the (+repo) suffix would re-open the 403 risk."""
    monkeypatch.delenv("BEACONRY_KEV_USER_AGENT", raising=False)
    kev = _reload_kev()
    assert "(+" in kev.KEV_USER_AGENT
    assert "github.com" in kev.KEV_USER_AGENT
