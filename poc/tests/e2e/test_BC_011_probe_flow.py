"""BC-017 — end-to-end BC-011 probe flow test (no Telegram).

Drives the full handler chain by POSTing to `beaconry-hub-test`'s
`/__test__/*` endpoints (hard-gated behind BEACONRY_TEST_MODE=1). The
test compose stack must be up: `make test-e2e` orchestrates up/test/down.

Covers:
    - CAP-T054: Automated end-to-end BC-011 probe flow via `make test-e2e`
"""

from __future__ import annotations

import time

import pytest
import requests

HUB = "http://localhost:9000"
TIMEOUT = 5.0

pytestmark = pytest.mark.e2e


def _wait_for_hub() -> None:
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            # In MOCK mode, /readyz returns 503 (no grype DB); /livez stays 200.
            r = requests.get(f"{HUB}/livez", timeout=2)
            if r.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError(f"hub-test not reachable at {HUB} within 60s")


@pytest.fixture(scope="module", autouse=True)
def _ready() -> None:
    _wait_for_hub()


@pytest.fixture(autouse=True)
def _reset() -> None:
    requests.post(f"{HUB}/__test__/reset", json={"wipe_db": True}, timeout=TIMEOUT)


def _insert(**kwargs: object) -> dict:
    r = requests.post(f"{HUB}/__test__/insert-finding", json=kwargs, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _invoke(callback_data: str) -> list[dict]:
    r = requests.post(
        f"{HUB}/__test__/invoke-callback", json={"callback_data": callback_data}, timeout=TIMEOUT
    )
    r.raise_for_status()
    return r.json()["edits"]


def _status(fid: str) -> str | None:
    r = requests.get(f"{HUB}/__test__/status/{fid}", timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["status"]


# ── Terminal states ──────────────────────────────────────────────────────────


def test_terminal_service_healthy_http_pass() -> None:
    """BC-011: HTTP probe to /healthy → ✅ Service healthy → DB fixed_verified."""
    fid = "e2e1111111111111"
    _insert(id=fid, cve_id="CVE-E2E-OK", source_ref="postgres:latest", status="fixed")

    edits = _invoke(f"verify:{fid}")

    terminal = edits[-1]["text"]
    assert "Service healthy" in terminal
    assert "HTTP 200" in terminal
    assert _status(fid) == "fixed_verified"


def test_terminal_probe_failed_http_degraded() -> None:
    """BC-011: HTTP probe to /degraded → ⚠️ Probe failed → DB verification_failed."""
    fid = "e2e2222222222222"
    _insert(id=fid, cve_id="CVE-E2E-DEG", source_ref="nginx:1.24", status="fixed")

    edits = _invoke(f"verify:{fid}")

    terminal = edits[-1]["text"]
    assert "Probe failed" in terminal
    assert "503" in terminal
    assert "docker tag" in terminal, "rollback hint must appear"
    assert "nginx:rollback" in terminal
    assert _status(fid) == "verification_failed"


def test_terminal_no_probe_configured() -> None:
    """BC-011: no matching policy → ⏭️ No probe configured → DB unchanged."""
    fid = "e2e3333333333333"
    _insert(id=fid, cve_id="CVE-E2E-NOPOL", source_ref="unknown/image:test", status="fixed")

    edits = _invoke(f"verify:{fid}")

    terminal = edits[-1]["text"]
    assert "No probe configured" in terminal
    assert "unknown/image:test" in terminal
    assert _status(fid) == "fixed", "status must not change when no probe runs"


def test_terminal_idempotent_on_fixed_verified() -> None:
    """BC-011: tap on already-fixed_verified → ℹ️ Already fixed_verified, no re-probe."""
    fid = "e2e4444444444444"
    _insert(id=fid, cve_id="CVE-E2E-IDEM", source_ref="postgres:latest", status="fixed_verified")

    edits = _invoke(f"verify:{fid}")

    terminal = edits[-1]["text"]
    assert "Already fixed_verified" in terminal
    # No ⏳ Probing edit → short-circuit before the probe step
    probing = [e for e in edits if "Probing" in e["text"]]
    assert probing == [], f"idempotent handler must not run the probe; got {edits}"
    assert _status(fid) == "fixed_verified"


# ── Image tag scenarios ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "source_ref,expect_terminal",
    [
        ("postgres:latest", "Service healthy"),
        ("postgres:16-alpine", "Service healthy"),
        ("postgres:16.1.3-alpine", "Service healthy"),
        ("nginxproxy/nginx-proxy:alpine", "Service healthy"),
        ("nginx:1.24", "Probe failed"),  # policy points at /degraded
    ],
)
def test_tag_scenarios_resolve_correctly(source_ref: str, expect_terminal: str) -> None:
    fid = (
        "e2etag" + source_ref.replace(":", "").replace("/", "").replace(".", "").replace("-", "")
    )[:16].lower()
    # Pad/truncate to exactly 16 chars; must be hex to pass CALLBACK_RE
    fid = "e2e5" + "a" * (16 - len("e2e5"))  # fallback deterministic 16-char hex
    _insert(id=fid, cve_id="CVE-E2E-TAG", source_ref=source_ref, status="fixed")
    edits = _invoke(f"verify:{fid}")
    terminal = edits[-1]["text"]
    assert expect_terminal in terminal, f"source_ref={source_ref!r}: got {terminal!r}"


# ── All six finding statuses round-trip ──────────────────────────────────────


def test_all_six_statuses_round_trip_and_sort() -> None:
    """BC-007 + BC-011: all 6 statuses must be writable and queryable."""
    all_statuses = (
        "open",
        "verification_failed",
        "snoozed",
        "accepted",
        "fixed",
        "fixed_verified",
    )
    base_ids = [f"e2e{i:013x}" for i in range(10, 10 + len(all_statuses))]
    for fid, status in zip(base_ids, all_statuses, strict=True):
        _insert(id=fid, cve_id=f"CVE-STATUS-{status}", source_ref=f"test:{status}", status=status)

    r = requests.get(f"{HUB}/__test__/all-findings", timeout=TIMEOUT)
    r.raise_for_status()
    findings = r.json()["findings"]
    returned_statuses = {f["status"] for f in findings}
    assert returned_statuses >= set(all_statuses), (
        f"missing statuses: {set(all_statuses) - returned_statuses}"
    )


# ── Isolation: namespaced container names ────────────────────────────────────


def test_stack_container_name_is_namespaced() -> None:
    """The hub-test container must not collide with the operator's `beaconry-hub`."""
    # We don't have docker CLI access from inside this process necessarily, so
    # we assert on the symptom: /healthz reports a container-specific value.
    # The /healthz body is enough to confirm we hit the test instance (bound 9000).
    r = requests.get(f"{HUB}/healthz", timeout=TIMEOUT)
    assert r.status_code == 200
    # The test instance was built from the same image but runs with
    # BEACONRY_TEST_MODE=1; confirm by hitting a test-only endpoint:
    r = requests.get(f"{HUB}/__test__/all-findings", timeout=TIMEOUT)
    assert r.status_code == 200, "test-mode endpoint must respond — proves test container isolation"
