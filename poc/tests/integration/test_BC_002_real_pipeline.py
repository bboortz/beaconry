"""BC-002 integration test — opt-in real-tools exercise.

Story: docs/stories/BC-002-real-pipeline-one-container.md
Plan:  docs/plan/BC-002-real-pipeline-one-container-plan.md

Marked `real_tools`. Skipped unless syft + grype are on $PATH AND
nginx:1.24 image is present locally. Does not send real Telegram messages
(Telegram.Bot.send_message is monkey-patched).

Run with:  pytest -m real_tools -v

Covers:
    - CAP-B002: Operator receives a real Telegram notification for a new CVE finding
"""

from __future__ import annotations

import shutil
import subprocess

import pytest


def _tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def _image_pulled(ref: str) -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "image", "inspect", ref],
                capture_output=True,
                check=False,
                timeout=10,
            ).returncode
            == 0
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


_REAL_TOOLS_AVAILABLE = (
    _tool_available("syft") and _tool_available("grype") and _image_pulled("nginx:1.24")
)


pytestmark = [
    pytest.mark.real_tools,
    pytest.mark.skipif(
        not _REAL_TOOLS_AVAILABLE,
        reason="requires syft + grype on PATH and nginx:1.24 pulled",
    ),
]


@pytest.mark.asyncio
async def test_real_syft_grype_produces_findings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Real syft + real grype on nginx:1.24 → at least one Finding; Telegram mocked."""
    # Mock Telegram so no real message is sent.
    sent: list[dict] = []

    class _FakeBot:
        async def send_message(self, **kwargs: object) -> object:
            sent.append(dict(kwargs))
            return object()

    class _FakeApp:
        bot = _FakeBot()

    import hub  # from poc/hub, added to sys.path via conftest

    monkeypatch.setattr(hub, "MOCK", False, raising=True)
    monkeypatch.setattr(hub, "tg_app", _FakeApp(), raising=False)

    from poc.demo import run_real_pipeline_once

    findings = await run_real_pipeline_once(image="nginx:1.24")

    assert len(findings) >= 1, "real grype must find at least one CVE in nginx:1.24"
    assert all(f.cve_id.startswith("CVE-") or f.cve_id.startswith("GHSA-") for f in findings)
    assert all(f.cvss >= 0 for f in findings)
    assert len(sent) >= 1, "notify should have called the (mocked) Telegram bot"
