"""BC-018 — end-to-end BC-011 probe flow via telegram-mock.

Drives the full handler chain by:
  1. Seeding findings via `docker exec beaconry-hub-test python3 -c '...'`
  2. Injecting fake user taps into telegram-mock's /__driver__/* endpoints
  3. telegram-mock delivers them to hub via the real Telegram getUpdates
     protocol — hub runs its production python-telegram-bot dispatch chain
  4. Capturing hub's outbound sendMessage/editMessageText via /__driver__/sent
  5. Inspecting DB state via docker exec

Hub.py has ZERO test-only code (BC-018 superseded BC-017's /__test__/*
approach). The telegram-mock service is the external double; hub thinks
it's talking to real Telegram.

Covers:
    - CAP-T055: telegram-mock service — mini Telegram Bot API shim + driver
    - CAP-T056: Hub configurable Telegram API base URL via BEACONRY_TELEGRAM_BASE_URL
    - CAP-T163: Test-mode routes are unreachable in prod (runtime probe) — BC-079
"""

from __future__ import annotations

import json
import subprocess
import time

import pytest
import requests

MOCK = "http://localhost:9080"
TIMEOUT = 10.0
HUB_CONTAINER = "beaconry-hub-test"

pytestmark = pytest.mark.e2e


def _wait_for_stack() -> None:
    """Block until both hub-test and telegram-mock answer /livez."""
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            h = requests.get("http://localhost:9000/livez", timeout=2)
            m = requests.get(f"{MOCK}/livez", timeout=2)
            if h.status_code == 200 and m.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError("stack not reachable within 60s")


@pytest.fixture(scope="module", autouse=True)
def _ready() -> None:
    _wait_for_stack()


@pytest.fixture(autouse=True)
def _reset() -> None:
    # Wipe telegram-mock state
    requests.post(f"{MOCK}/__driver__/reset", timeout=TIMEOUT)
    # Wipe hub-test DB
    _exec_hub(
        "import sqlite3; con=sqlite3.connect('/data/beaconry.db'); "
        "con.execute('DELETE FROM findings'); con.commit(); con.close()"
    )


def _exec_hub(python_code: str) -> str:
    """Run a Python snippet inside hub-test, return stdout."""
    r = subprocess.run(
        ["docker", "exec", HUB_CONTAINER, "python3", "-c", python_code],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(f"docker exec failed: {r.stderr}")
    return r.stdout


def _seed_finding(fid: str, cve_id: str, source_ref: str, status: str) -> None:
    """Seed via hub's own API so schema changes (new columns, new fields)
    propagate automatically. BC-021 close-gap: replaces the previous raw
    CREATE TABLE + positional INSERT that silently broke when BC-020 added
    the `kev` column."""
    _exec_hub(
        f"""
import sys; sys.path.insert(0, '/app')
import hub
from executor.base import Finding
hub.init_db()
f = Finding(
    id={fid!r},
    host='test-host',
    cve_id={cve_id!r},
    package='pkg',
    version='1.0',
    fixed_version='1.1',
    severity='High',
    cvss=8.0,
    source_type='docker',
    source_ref={source_ref!r},
    description='e2e fixture',
    tier=2,
    kev=False,
)
hub.save_finding(f)
# save_finding writes status='open' by default; coerce to the requested state.
if {status!r} != 'open':
    hub.set_status({fid!r}, {status!r})
"""
    )


def _get_status(fid: str) -> str | None:
    out = _exec_hub(
        f"""
import sqlite3
con = sqlite3.connect('/data/beaconry.db')
row = con.execute("SELECT status FROM findings WHERE id='{fid}'").fetchone()
print(row[0] if row else '')
"""
    )
    s = out.strip()
    return s if s else None


def _inject_callback(data: str) -> int:
    r = requests.post(f"{MOCK}/__driver__/inject-callback", json={"data": data}, timeout=TIMEOUT)
    r.raise_for_status()
    return int(r.json()["injected_update_id"])


def _wait_for_edit(contains: str, timeout: float = 10.0) -> dict:
    """Poll /__driver__/sent until an edit containing `contains` appears."""
    deadline = time.time() + timeout
    last_sent: list[dict] = []
    while time.time() < deadline:
        r = requests.get(f"{MOCK}/__driver__/sent", timeout=TIMEOUT)
        r.raise_for_status()
        last_sent = r.json()["sent"]
        hits = [e for e in last_sent if contains in e.get("text", "")]
        if hits:
            return hits[-1]
        time.sleep(0.3)
    raise RuntimeError(
        f"edit containing {contains!r} did not appear within {timeout}s. Last sent: {json.dumps(last_sent, indent=2)}"
    )


# ── Terminal states ──────────────────────────────────────────────────────────


def test_terminal_service_healthy_http_pass() -> None:
    """BC-011: HTTP probe to /healthy → ✅ Service healthy → DB fixed_verified."""
    fid = "e2eb018ca1e00001"
    _seed_finding(fid, "CVE-E2E-OK", "postgres:latest", "fixed")
    _inject_callback(f"verify:{fid}")
    terminal = _wait_for_edit("Service healthy")
    assert "Service healthy" in terminal["text"]
    assert "HTTP 200" in terminal["text"]
    assert _get_status(fid) == "fixed_verified"


def test_terminal_probe_failed_http_degraded() -> None:
    """BC-011: HTTP probe to /degraded → ⚠️ Probe failed → DB verification_failed."""
    fid = "e2eb018ca1e00002"
    _seed_finding(fid, "CVE-E2E-DEG", "nginx:1.24", "fixed")
    _inject_callback(f"verify:{fid}")
    terminal = _wait_for_edit("Probe failed")
    assert "503" in terminal["text"]
    assert "docker tag" in terminal["text"]
    assert "nginx:rollback" in terminal["text"]
    assert _get_status(fid) == "verification_failed"


def test_terminal_no_probe_configured() -> None:
    """BC-011: no matching policy → ⏭️ No probe configured → DB unchanged."""
    fid = "e2eb018ca1e00003"
    _seed_finding(fid, "CVE-E2E-NOPOL", "unknown/image:test", "fixed")
    _inject_callback(f"verify:{fid}")
    terminal = _wait_for_edit("No probe configured")
    assert "unknown/image:test" in terminal["text"]
    assert _get_status(fid) == "fixed"


def test_terminal_idempotent_on_fixed_verified() -> None:
    """BC-011: tap on already-fixed_verified → ℹ️ Already, no re-probe."""
    fid = "e2eb018ca1e00004"
    _seed_finding(fid, "CVE-E2E-IDEM", "postgres:latest", "fixed_verified")
    _inject_callback(f"verify:{fid}")
    _wait_for_edit("Already fixed_verified")
    # No "Probing" phase for idempotent short-circuit
    r = requests.get(f"{MOCK}/__driver__/sent", timeout=TIMEOUT)
    sent = r.json()["sent"]
    probing = [e for e in sent if "Probing" in e.get("text", "")]
    assert probing == [], f"idempotent handler must not probe; got {sent}"
    assert _get_status(fid) == "fixed_verified"


# ── Image tag scenarios ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "source_ref,expect_terminal",
    [
        ("postgres:latest", "Service healthy"),
        ("postgres:16-alpine", "Service healthy"),
        ("postgres:16.1.3-alpine", "Service healthy"),
        ("nginxproxy/nginx-proxy:alpine", "Service healthy"),
        ("nginx:1.24", "Probe failed"),
    ],
)
def test_tag_scenarios_resolve_correctly(source_ref: str, expect_terminal: str) -> None:
    # Unique 16-hex id per parametrized case (deterministic from the source_ref)
    import hashlib

    fid = hashlib.sha256(source_ref.encode()).hexdigest()[:16]
    _seed_finding(fid, "CVE-E2E-TAG", source_ref, "fixed")
    _inject_callback(f"verify:{fid}")
    terminal = _wait_for_edit(expect_terminal)
    assert expect_terminal in terminal["text"], (
        f"source_ref={source_ref!r}: got {terminal['text']!r}"
    )


# ── All six finding statuses ─────────────────────────────────────────────────


def test_all_six_statuses_round_trip() -> None:
    all_statuses = (
        "open",
        "verification_failed",
        "snoozed",
        "accepted",
        "fixed",
        "fixed_verified",
    )
    base_ids = [f"e2estatus{i:07x}" for i in range(len(all_statuses))]
    for fid, status in zip(base_ids, all_statuses, strict=True):
        _seed_finding(fid, f"CVE-STATUS-{status}", f"test:{status}", status)
    for fid, status in zip(base_ids, all_statuses, strict=True):
        assert _get_status(fid) == status


# ── Hub-side cleanliness assertion ───────────────────────────────────────────


def test_hub_container_has_no_test_mode_code() -> None:
    """BC-018 contract, rewritten in BC-079: `/test/*` routes must be
    unreachable when BEACONRY_TEST_MODE is unset.

    Pre-BC-079 this scanned hub.py source for the string `BEACONRY_TEST_MODE`.
    BC-038 legitimately re-added the string as a feature flag for the
    scenario harness — the source scan went stale and silently red. The
    real contract is "test routes are unreachable in prod", which is a
    runtime property; probe it directly.
    """
    import urllib.error
    import urllib.request

    # docker-compose.test.yml does not set BEACONRY_TEST_MODE, so the
    # /test/* branch in hub.py must not have been registered.
    req = urllib.request.Request("http://localhost:9000/test/__driver__/snoozes", method="GET")
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 404, (
        f"/test/__driver__/snoozes returned {code}, expected 404. Test-mode "
        f"routes appear to be live in the shipped image — BC-018 contract broken."
    )


def test_isolation_from_normal_stack() -> None:
    """Confirm we're hitting the test container, not an operator's normal stack."""
    r = requests.get(f"{MOCK}/__driver__/state", timeout=TIMEOUT)
    assert r.status_code == 200, (
        "telegram-mock /__driver__/state must respond — proves we're on the test stack"
    )
