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


def test_main_prints_fallback_hint_when_unconfigured(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BEACONRY_MOCK", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(demo.sys, "argv", ["poc.demo"])
    rc = demo.main()
    err = capsys.readouterr().err
    assert rc == 2
    assert "TELEGRAM_BOT_TOKEN" in err
    assert "BEACONRY_MOCK=1" in err


async def test_run_real_pipeline_once_builds_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock syft + grype, verify findings are built, saved, notified."""
    import hub

    # Fixture SBOM
    fake_sbom = {"artifacts": [{"name": "openssl", "version": "1.0.1"}]}

    async def _fake_syft(image: str) -> dict:
        return fake_sbom

    async def _fake_grype(sbom: dict) -> list[dict]:
        return [
            {
                "vulnerability": {
                    "id": "CVE-TEST-9999",
                    "severity": "Critical",
                    "cvss": [{"metrics": {"baseScore": 9.5}}],
                    "description": "fake",
                    "fix": {"versions": ["1.0.2"]},
                },
                "artifact": {"name": "openssl", "version": "1.0.1"},
            }
        ]

    notified: list[object] = []

    async def _fake_notify(f: object) -> None:
        notified.append(f)

    monkeypatch.setattr(demo, "_run_syft", _fake_syft)
    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    monkeypatch.setattr(hub, "notify", _fake_notify)

    hub.init_db()

    findings = await demo.run_real_pipeline_once(image="nginx:1.24")

    assert len(findings) == 1
    assert findings[0].cve_id == "CVE-TEST-9999"
    assert findings[0].cvss == 9.5
    assert findings[0].source_type == "docker"
    assert len(notified) == 1


async def test_run_real_pipeline_once_skips_below_min_cvss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hub

    async def _fake_syft(image: str) -> dict:
        return {"artifacts": []}

    async def _fake_grype(sbom: dict) -> list[dict]:
        return [
            {
                "vulnerability": {
                    "id": "CVE-LOW",
                    "cvss": [{"metrics": {"baseScore": 2.0}}],
                },
                "artifact": {"name": "x", "version": "1"},
            }
        ]

    async def _fake_notify(f: object) -> None:  # pragma: no cover
        raise AssertionError("notify should not be called below MIN_CVSS")

    monkeypatch.setattr(demo, "_run_syft", _fake_syft)
    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    monkeypatch.setattr(hub, "notify", _fake_notify)
    monkeypatch.setenv("MIN_CVSS", "7.0")

    hub.init_db()
    findings = await demo.run_real_pipeline_once(image="nginx:1.24")
    assert findings == []


async def test_run_syft_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeProc:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"", b"syft error output")

    async def _fake_create(*args: object, **kwargs: object) -> _FakeProc:
        return _FakeProc()

    monkeypatch.setattr(demo.asyncio, "create_subprocess_exec", _fake_create)

    with pytest.raises(RuntimeError, match="syft failed"):
        await demo._run_syft("nginx:1.24")
