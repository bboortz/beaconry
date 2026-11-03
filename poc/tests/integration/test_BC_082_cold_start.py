"""BC-082 — cold-start smoke test.

Compose project `beaconry-cold-start` is brought up by the make target
`test-cold-start` BEFORE pytest runs; this test asserts the stack is
healthy once pytest starts, and verifies all 8 health endpoints +
grype DB download succeeded. Teardown is done by the make target too
(trap EXIT), so this file is purely assertions.

Covers:
    - CAP-T168: Cold-start smoke — proves a fresh homelab install path works
"""

from __future__ import annotations

import time

import pytest
import requests

pytestmark = pytest.mark.cold_start

HUB_URL = "http://localhost:9100"
SCOUT_URL = "http://localhost:9101"
# Max time to wait for both services to be ready. Hub needs ~90s for the
# first grype DB download on cold cache; scout needs ~5s after hub is ready.
BOOT_DEADLINE_S = 180


def _wait_endpoint(url: str, endpoint: str, timeout_s: int) -> int:
    """Poll a specific endpoint until 200 or deadline. Returns final status code."""
    deadline = time.time() + timeout_s
    last_code = -1
    while time.time() < deadline:
        try:
            r = requests.get(f"{url}/{endpoint}", timeout=5)
            last_code = r.status_code
            if r.status_code == 200:
                return 200
        except requests.RequestException:
            last_code = -2
        time.sleep(2)
    return last_code


@pytest.fixture(scope="module", autouse=True)
def _wait_for_cold_start() -> None:
    """Block until both hub + scout answer /readyz AND /startupz. Fail the
    module fast if either doesn't come up — those failures are the exact
    homelab-install issues BC-082 targets.

    `/startupz` on scout flips to 200 only after the first successful POST
    to hub (BC-063 contract). Needs ~SCAN_INTERVAL seconds after hub is
    ready, so budget for that explicitly instead of relying on `/readyz`
    (which goes green much earlier)."""
    hub_code = _wait_endpoint(HUB_URL, "readyz", BOOT_DEADLINE_S)
    assert hub_code == 200, (
        f"hub /readyz never returned 200 within {BOOT_DEADLINE_S}s (last={hub_code}). "
        f"Likely: grype DB download failed, DB file unwritable (/data chown bug), "
        f"or compose build error. Inspect: "
        f"docker compose -p beaconry-cold-start logs hub"
    )
    scout_ready = _wait_endpoint(SCOUT_URL, "readyz", 30)
    assert scout_ready == 200, (
        f"scout /readyz never returned 200 within 30s after hub was ready "
        f"(last={scout_ready}). Likely: scout can't reach hub (compose network), "
        f"docker socket gid mismatch (set DOCKER_GID), or syft binary missing."
    )
    # Scout /startupz needs the first scan to complete + first POST to hub.
    # SCAN_INTERVAL=60 in cold-start compose; budget 120s for the full cycle.
    scout_started = _wait_endpoint(SCOUT_URL, "startupz", 120)
    assert scout_started == 200, (
        f"scout /startupz never returned 200 within 120s (last={scout_started}). "
        f"Scout came up ready but never completed first POST to hub — check "
        f"docker compose -p beaconry-cold-start logs scout for RequestException."
    )


# ── All 8 health endpoints ──────────────────────────────────────────────────


def test_cold_start_all_eight_endpoints() -> None:
    """CAP-T168: all 4 health endpoints on both services return 200. If any
    returns 503, the "will homelab deploy work" promise is broken.

    One test (not parametrized) because the plan's shipping-evidence lint
    (BC-041 reverse-check) matches pytest node ids exactly.
    """
    endpoints = ("livez", "readyz", "startupz", "healthz")
    failures: list[str] = []
    for service, url in (("hub", HUB_URL), ("scout", SCOUT_URL)):
        for endpoint in endpoints:
            r = requests.get(f"{url}/{endpoint}", timeout=10)
            if r.status_code != 200:
                failures.append(
                    f"{service} /{endpoint} returned {r.status_code} (body: {r.text[:100]})"
                )
    assert not failures, "cold-start health probe failed:\n  " + "\n  ".join(failures)


# ── Hub /healthz detail: grype DB really downloaded ─────────────────────────


def test_cold_start_hub_healthz_shows_grype_db_ok() -> None:
    """CAP-T168: hub /healthz must report `grype_db: ok` — proves the
    DB download path works (BC-078 refactor could have broken file-system
    writes to the cache dir; this proves it doesn't)."""
    r = requests.get(f"{HUB_URL}/healthz", timeout=10)
    assert r.status_code == 200
    body = r.json()
    checks = body.get("checks", {})
    assert checks.get("grype_db") == "ok", (
        f"hub /healthz grype_db != ok: {checks}. The cold-start compose "
        f"doesn't set BEACONRY_SKIP_GRYPE_DB, so grype should have "
        f"downloaded its DB on first boot."
    )
    assert checks.get("grype_binary") == "ok", (
        f"hub /healthz grype_binary != ok: {checks}. The distroless runtime "
        f"should have /usr/bin/grype COPY'd from the builder stage."
    )
    assert checks.get("db") == "ok", (
        f"hub /healthz db != ok: {checks}. Likely /data permission problem — "
        f"hub runs as uid 65532 and must own /data/beaconry.db."
    )


# ── Scout /healthz detail: docker socket + inventory ────────────────────────


def test_cold_start_scout_healthz_shows_docker_socket_ok() -> None:
    """CAP-T168: scout /healthz must report `docker_socket: ok` — proves
    the compose `group_add: ${DOCKER_GID}` plumbing (BC-078) actually gives
    the non-root container access to the host socket."""
    r = requests.get(f"{SCOUT_URL}/healthz", timeout=10)
    assert r.status_code == 200
    body = r.json()
    checks = body.get("checks", {})
    assert checks.get("docker_socket") == "ok", (
        f"scout /healthz docker_socket != ok: {checks}. Likely DOCKER_GID "
        f"env didn't match the host's /var/run/docker.sock gid. "
        f"Run `getent group docker | cut -d: -f3` and set DOCKER_GID in .env."
    )


# ── Version strings match current VERSION ───────────────────────────────────


def test_cold_start_version_matches_source() -> None:
    """CAP-T168: both services report the current source VERSION in
    /healthz. Guards against: image was built from a stale src cache,
    operator pulled the stack but didn't `--build`."""
    from pathlib import Path

    expected = (Path(__file__).resolve().parents[3] / "VERSION").read_text().strip()
    for name, url in (("hub", HUB_URL), ("scout", SCOUT_URL)):
        r = requests.get(f"{url}/healthz", timeout=10)
        assert r.status_code == 200
        body = r.json()
        version = body.get("version", {}).get("software")
        assert version == expected, (
            f"{name} /healthz software version = {version!r}, expected {expected!r}. "
            f"Likely a stale image cache — compose should have rebuilt."
        )
