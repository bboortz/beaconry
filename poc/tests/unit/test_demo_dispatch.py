"""Unit tests for BC-002 demo dispatch, prereq checks, self-test flag."""

from __future__ import annotations

import pytest

from poc import demo


def test_choose_mode_mock_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    assert demo.choose_mode(env=dict(BEACONRY_MOCK="1")) == "mock"


def test_choose_mode_real_when_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "123"}
    assert demo.choose_mode(env=env) == "real"


def test_choose_mode_error_when_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(demo.MissingConfigError) as excinfo:
        demo.choose_mode(env={})
    msg = str(excinfo.value)
    assert "TELEGRAM_BOT_TOKEN" in msg
    assert "BEACONRY_MOCK=1" in msg
    assert ".env.example" in msg


def test_choose_mode_mock_wins_over_real_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"BEACONRY_MOCK": "1", "TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "123"}
    assert demo.choose_mode(env=env) == "mock"


def test_check_prereqs_reports_missing_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo.shutil, "which", lambda name: None)
    missing = demo.check_prereqs(image="nginx:1.24", image_present=lambda ref: True)
    assert any("syft" in m for m in missing)
    assert any("grype" in m for m in missing)


def test_check_prereqs_reports_missing_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo.shutil, "which", lambda name: "/usr/bin/" + name)
    missing = demo.check_prereqs(image="nginx:1.24", image_present=lambda ref: False)
    assert any("nginx:1.24" in m for m in missing)


def test_check_prereqs_empty_when_all_good(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo.shutil, "which", lambda name: "/usr/bin/" + name)
    missing = demo.check_prereqs(image="nginx:1.24", image_present=lambda ref: True)
    assert missing == []


def test_parse_args_self_test() -> None:
    assert demo.parse_args(["--self-test"]).self_test is True
    assert demo.parse_args([]).self_test is False


def test_parse_args_non_interactive() -> None:
    assert demo.parse_args(["--non-interactive"]).non_interactive is True
    assert demo.parse_args([]).non_interactive is False
