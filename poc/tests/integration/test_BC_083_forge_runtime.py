"""BC-083 — forge base image: runtime-side assertions against the live
cold-start stack.

Covers:
    - CAP-T169: Runtime image from forge — contract verified at runtime
"""

from __future__ import annotations

import subprocess

import pytest

pytestmark = pytest.mark.cold_start


HUB_CONTAINER = "beaconry-cold-start-hub"
SCOUT_CONTAINER = "beaconry-cold-start-scout"


def _exec(container: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", container, *args],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


# ── No shell ────────────────────────────────────────────────────────────────


def test_hub_has_no_shell() -> None:
    """CAP-T169: forge base ships no shell; BC-078 contract preserved."""
    for shell in ("/bin/sh", "/bin/bash"):
        r = _exec(HUB_CONTAINER, shell, "-c", "echo")
        assert r.returncode != 0, (
            f"{shell} unexpectedly executed in hub container — the 'no shell' "
            f"contract from BC-078 is broken on the forge image"
        )


def test_scout_has_no_shell() -> None:
    """CAP-T169: same for scout."""
    for shell in ("/bin/sh", "/bin/bash"):
        r = _exec(SCOUT_CONTAINER, shell, "-c", "echo")
        assert r.returncode != 0


# ── Uid 65532 preserved (BC-077/BC-078/BC-082 chown target) ─────────────────


def test_hub_runs_as_65532() -> None:
    """CAP-T169: forge image bakes USER 65532; we rely on that uid so the
    BC-082 data volume's 65532:65532 ownership keeps the hub writable."""
    r = _exec(
        HUB_CONTAINER,
        "/usr/local/bin/python3",
        "-c",
        "import os;print(os.getuid(), os.getgid())",
    )
    assert r.returncode == 0, f"docker exec failed: {r.stderr}"
    assert r.stdout.strip() == "65532 65532", (
        f"hub runs as `{r.stdout.strip()}`, expected `65532 65532`. "
        f"Forge image's baked USER directive has drifted — operators' "
        f"BC-082 chown step would break."
    )


def test_scout_runs_as_65532() -> None:
    """CAP-T169: same for scout."""
    r = _exec(
        SCOUT_CONTAINER,
        "/usr/local/bin/python3",
        "-c",
        "import os;print(os.getuid(), os.getgid())",
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "65532 65532"


# ── HTTPS works (SSL_CERT_FILE baked in) ────────────────────────────────────


def test_hub_can_make_https_request() -> None:
    """CAP-T169: SSL_CERT_FILE is baked into the forge image; verify that
    python's urllib finds the CA bundle and HTTPS succeeds. Without this,
    grype DB download / Telegram / KEV feed all fail with SSL errors."""
    r = _exec(
        HUB_CONTAINER,
        "/usr/local/bin/python3",
        "-c",
        "import urllib.request,ssl;"
        "ctx=ssl.create_default_context();"
        "r=urllib.request.urlopen('https://api.github.com',timeout=10,context=ctx);"
        "print(r.status)",
    )
    assert r.returncode == 0, (
        f"HTTPS request from hub failed — SSL_CERT_FILE not resolving? stderr: {r.stderr[:400]}"
    )
    assert "200" in r.stdout


# ── Bundled binaries all work ───────────────────────────────────────────────


def test_hub_grype_binary_runs() -> None:
    """CAP-T169: grype COPY'd to /usr/local/bin runs (PATH resolves it)."""
    r = _exec(HUB_CONTAINER, "/usr/local/bin/grype", "version")
    assert r.returncode == 0, f"grype failed: {r.stderr[:200]}"
    assert "grype" in r.stdout.lower() or "version" in r.stdout.lower()


def test_hub_syft_binary_runs() -> None:
    """CAP-T169: syft present + runs in hub."""
    r = _exec(HUB_CONTAINER, "/usr/local/bin/syft", "version")
    assert r.returncode == 0, f"syft failed: {r.stderr[:200]}"


def test_hub_docker_cli_runs() -> None:
    """CAP-T169: docker CLI (BC-009 DockerAdapter dep) runs in hub."""
    r = _exec(HUB_CONTAINER, "/usr/local/bin/docker", "--version")
    assert r.returncode == 0, f"docker CLI failed: {r.stderr[:200]}"
    assert "Docker version" in r.stdout


def test_scout_syft_binary_runs() -> None:
    """CAP-T169: syft COPY'd into scout runs — SBOM generation path works."""
    r = _exec(SCOUT_CONTAINER, "/usr/local/bin/syft", "version")
    assert r.returncode == 0, f"syft failed in scout: {r.stderr[:200]}"


# ── Runtime import probe — catches "missing shared lib" class of bug ────────


def _imports_from_requirements(req_path: str) -> list[str]:
    """Return the set of top-level Python module names we expect to be
    importable, derived from a requirements.txt. Maps PyPI package names
    to their import names where they differ (e.g. python-telegram-bot →
    telegram, pyyaml → yaml).
    """
    from pathlib import Path

    # PyPI package → importable module name mapping for cases where they
    # differ. Covers all deps in poc/{hub,scout}/requirements.txt.
    pypi_to_import = {
        "python-telegram-bot": "telegram",
        "pyyaml": "yaml",
        "uvicorn[standard]": "uvicorn",
    }
    imports: list[str] = []
    for line in Path(__file__).resolve().parents[3].joinpath(req_path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip `==x.y.z`, `[extras]`, etc. — keep the base name.
        pkg = line.split("==")[0].split(">=")[0].split("<=")[0].split("[")[0].strip()
        if not pkg:
            continue
        imports.append(pypi_to_import.get(pkg.lower(), pkg.lower().replace("-", "_")))
    return imports


def test_hub_can_import_all_runtime_deps() -> None:
    """CAP-T169 ext: every top-level dep in hub/requirements.txt must `import`
    cleanly inside the running container.

    This catches the generic class of base-image regressions BC-083
    discovered the hard way: forge v2 shipped without /usr/lib/x86_64-linux-gnu/librt.so.1,
    pydantic_core's Rust wheel wouldn't load, hub crashed at startup with
    `ImportError: librt.so.1: cannot open shared object file`. A per-dep
    import probe fires on any missing-.so / wrong-ABI / corrupted-wheel
    bug for every future base-image migration.
    """
    imports = _imports_from_requirements("poc/hub/requirements.txt")
    script = "; ".join(f"import {m}" for m in imports) + "; print('ok')"
    r = _exec(HUB_CONTAINER, "/usr/local/bin/python3", "-c", script)
    assert r.returncode == 0 and "ok" in r.stdout, (
        f"hub container failed to import one of its declared runtime deps "
        f"({imports}): {r.stderr[:400]}. This is the BC-083 librt-class of bug "
        f"— a shared library, wheel ABI, or module is missing from the base image."
    )


def test_scout_can_import_all_runtime_deps() -> None:
    """CAP-T169 ext: same for scout."""
    imports = _imports_from_requirements("poc/scout/requirements.txt")
    script = "; ".join(f"import {m}" for m in imports) + "; print('ok')"
    r = _exec(SCOUT_CONTAINER, "/usr/local/bin/python3", "-c", script)
    assert r.returncode == 0 and "ok" in r.stdout, (
        f"scout container failed to import a declared runtime dep ({imports}): {r.stderr[:400]}"
    )


# ── /etc/passwd entry for 65532 (forge image contract) ──────────────────────


def test_nonroot_user_is_resolvable() -> None:
    """CAP-T169: pwd.getpwuid(65532) must succeed inside the container.
    Forge image bakes the entry; libs like psutil / k8s-client break if not."""
    r = _exec(
        HUB_CONTAINER,
        "/usr/local/bin/python3",
        "-c",
        "import pwd;u=pwd.getpwuid(65532);print(u.pw_name)",
    )
    assert r.returncode == 0, f"pwd lookup for 65532 failed: {r.stderr[:200]}"
    assert r.stdout.strip() == "nonroot"
