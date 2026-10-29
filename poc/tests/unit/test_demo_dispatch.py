"""Unit tests for BC-002 demo dispatch, prereq checks, self-test flag.

Covers:
    - CAP-B002: Operator receives a real Telegram notification for a new CVE finding
    - CAP-B004: Operator is not flooded on noisy scans
    - CAP-B005: Operator can self-test the Telegram wiring without a scan
    - CAP-T009: Demo dispatcher chooses mock vs real based on env
    - CAP-T010: Prereq check surfaces missing syft/grype/image
    - CAP-T011: Real syft errors are surfaced
    - CAP-T012: Build-findings dedupes CVEs across packages
    - CAP-T013: Build-findings filters below MIN_CVSS
    - CAP-T014: Pipeline survives one failing notification
    - CAP-T015: Per-run cap caps individual sends and triggers a digest
"""

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


def test_build_findings_dedupes_cve_across_packages() -> None:
    """Same CVE on openssl + libssl1.1 → one Finding (highest-CVSS variant kept)."""
    matches = [
        {
            "vulnerability": {"id": "CVE-X", "cvss": [{"metrics": {"baseScore": 7.5}}]},
            "artifact": {"name": "libssl1.1", "version": "1"},
        },
        {
            "vulnerability": {"id": "CVE-X", "cvss": [{"metrics": {"baseScore": 9.1}}]},
            "artifact": {"name": "openssl", "version": "1"},
        },
        {
            "vulnerability": {"id": "CVE-Y", "cvss": [{"metrics": {"baseScore": 8.0}}]},
            "artifact": {"name": "curl", "version": "7"},
        },
    ]
    findings = demo._build_findings_from_matches(
        matches, image="nginx:1.24", host="h", min_cvss=7.0
    )
    assert len(findings) == 2
    cves = {f.cve_id: f for f in findings}
    assert cves["CVE-X"].cvss == 9.1  # highest-CVSS variant kept
    assert cves["CVE-X"].package == "openssl"


def test_build_findings_filters_below_min_cvss() -> None:
    matches = [
        {
            "vulnerability": {"id": "CVE-LOW", "cvss": [{"metrics": {"baseScore": 3.0}}]},
            "artifact": {"name": "x", "version": "1"},
        },
    ]
    findings = demo._build_findings_from_matches(matches, image="img:1", host="h", min_cvss=7.0)
    assert findings == []


async def test_pipeline_cap_sends_top_n_and_a_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BEACONRY_MAX_FINDINGS_PER_RUN caps individual notifications; digest fires."""
    import hub

    async def _fake_syft(image: str) -> dict:
        return {"artifacts": []}

    async def _fake_grype(sbom: dict) -> list[dict]:
        return [
            {
                "vulnerability": {"id": f"CVE-{i:03d}", "cvss": [{"metrics": {"baseScore": 9.9}}]},
                "artifact": {"name": f"pkg-{i}", "version": "1"},
            }
            for i in range(20)
        ]

    notify_calls: list[str] = []

    async def _fake_notify(f: object) -> None:
        notify_calls.append(f.cve_id)  # type: ignore[attr-defined]

    digest_calls: list[list[object]] = []

    async def _fake_digest(remaining: list[object]) -> None:
        digest_calls.append(list(remaining))

    monkeypatch.setattr(demo, "_run_syft", _fake_syft)
    monkeypatch.setattr(demo, "_send_digest", _fake_digest)
    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    monkeypatch.setattr(hub, "notify", _fake_notify)
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "5")
    monkeypatch.setenv("MIN_CVSS", "0")

    hub.init_db()
    findings = await demo.run_real_pipeline_once(image="nginx:1.24")

    assert len(findings) == 5
    assert len(notify_calls) == 5
    assert len(digest_calls) == 1 and len(digest_calls[0]) == 15


async def test_one_bad_notification_does_not_crash_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for BC-002: one notify failure must not kill the whole loop."""
    import hub

    async def _fake_syft(image: str) -> dict:
        return {"artifacts": []}

    async def _fake_grype(sbom: dict) -> list[dict]:
        return [
            {
                "vulnerability": {"id": f"CVE-X-{i}", "cvss": [{"metrics": {"baseScore": 9.0}}]},
                "artifact": {"name": f"pkg{i}", "version": "1"},
            }
            for i in range(3)
        ]

    call_log: list[str] = []

    async def _flaky_notify(f: object) -> None:
        call_log.append(f.cve_id)  # type: ignore[attr-defined]
        if f.cve_id == "CVE-X-1":  # type: ignore[attr-defined]
            from telegram.error import BadRequest

            raise BadRequest("Can't parse entities (fixture)")

    monkeypatch.setattr(demo, "_run_syft", _fake_syft)
    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    monkeypatch.setattr(hub, "notify", _flaky_notify)
    monkeypatch.setenv("MIN_CVSS", "0")

    hub.init_db()
    findings = await demo.run_real_pipeline_once(image="nginx:1.24")

    # All 3 were tried, 1 failed. Returned findings exclude the failed one.
    assert len(call_log) == 3
    assert len(findings) == 2
    assert "CVE-X-1" not in [f.cve_id for f in findings]
