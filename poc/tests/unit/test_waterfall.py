"""BC-011 — waterfall probe primitives.

Covers:
    - CAP-T048: Policy-driven probe resolver (BEACONRY_HEALTH_PROBES env var)
    - CAP-T049: Three probe types — docker_healthcheck, tcp, http
"""

from __future__ import annotations

import pytest

from poc.hub import waterfall

# ── Policy parser ─────────────────────────────────────────────────────────────


def test_parse_policies_empty_returns_empty_dict() -> None:
    assert waterfall.parse_policies("") == {}
    assert waterfall.parse_policies(None) == {}  # type: ignore[arg-type]


def test_parse_policies_single_tcp() -> None:
    policies = waterfall.parse_policies("postgres:16-alpine=tcp:postgres:5432")
    assert "postgres:16-alpine" in policies
    probe = policies["postgres:16-alpine"]
    assert probe.probe_type == "tcp"
    assert probe.probe_arg == "postgres:5432"


def test_parse_policies_round_trip_multiple_entries() -> None:
    env = (
        "postgres:16-alpine=tcp:postgres:5432;"
        "nginxproxy/nginx-proxy:alpine=http:http://nginx-proxy/healthz;"
        "redis:7-alpine=docker_healthcheck:redis"
    )
    policies = waterfall.parse_policies(env)
    assert len(policies) == 3
    assert policies["postgres:16-alpine"].probe_type == "tcp"
    assert policies["nginxproxy/nginx-proxy:alpine"].probe_type == "http"
    assert policies["redis:7-alpine"].probe_type == "docker_healthcheck"


def test_parse_policies_malformed_raises() -> None:
    with pytest.raises(waterfall.MalformedPolicyError, match="postgres"):
        waterfall.parse_policies("postgres:16-alpine=garbage")


def test_parse_policies_unknown_probe_type_raises() -> None:
    with pytest.raises(waterfall.MalformedPolicyError, match="unknown probe type"):
        waterfall.parse_policies("foo:latest=ping:host:1")


def test_parse_policies_tolerates_whitespace() -> None:
    policies = waterfall.parse_policies("  postgres:16-alpine = tcp:postgres:5432  ")
    assert "postgres:16-alpine" in policies


# ── Resolver ──────────────────────────────────────────────────────────────────


def test_resolve_probe_matches_exact_image() -> None:
    policies = waterfall.parse_policies("postgres:16-alpine=tcp:postgres:5432")
    probe = waterfall.resolve_probe("postgres:16-alpine", policies)
    assert probe is not None
    assert probe.probe_type == "tcp"


def test_resolve_probe_no_match_returns_none() -> None:
    policies = waterfall.parse_policies("postgres:16-alpine=tcp:postgres:5432")
    assert waterfall.resolve_probe("nginx:alpine", policies) is None


def test_resolve_probe_empty_policies_returns_none() -> None:
    assert waterfall.resolve_probe("postgres:16-alpine", {}) is None


# ── TCP probe ────────────────────────────────────────────────────────────────


async def test_tcp_probe_passes_on_open_port(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_open(host: str, port: int) -> tuple:
        class _Writer:
            def close(self) -> None:
                pass

            async def wait_closed(self) -> None:
                pass

        return (None, _Writer())

    monkeypatch.setattr(waterfall.asyncio, "open_connection", _fake_open)
    probe = waterfall.Probe(image_ref="x", probe_type="tcp", probe_arg="host:5432")
    result = await waterfall.run_probe(probe)
    assert result.ok is True


async def test_tcp_probe_fails_on_connection_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_open(host: str, port: int) -> tuple:
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(waterfall.asyncio, "open_connection", _fake_open)
    probe = waterfall.Probe(image_ref="x", probe_type="tcp", probe_arg="host:5432")
    result = await waterfall.run_probe(probe)
    assert result.ok is False
    assert "refused" in result.reason.lower() or "connect" in result.reason.lower()


async def test_tcp_probe_rejects_malformed_arg() -> None:
    probe = waterfall.Probe(image_ref="x", probe_type="tcp", probe_arg="not-a-host-port")
    result = await waterfall.run_probe(probe)
    assert result.ok is False
    assert "malformed" in result.reason.lower() or "parse" in result.reason.lower()


# ── HTTP probe ────────────────────────────────────────────────────────────────


async def test_http_probe_2xx_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResp:
        status = 200

        def __init__(self) -> None:
            pass

        def read(self) -> bytes:
            return b""

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *a: object) -> None:
            pass

    def _fake_urlopen(url: str, timeout: float = 5.0) -> _FakeResp:
        return _FakeResp()

    monkeypatch.setattr(waterfall.urllib.request, "urlopen", _fake_urlopen)
    probe = waterfall.Probe(image_ref="x", probe_type="http", probe_arg="http://host/healthz")
    result = await waterfall.run_probe(probe)
    assert result.ok is True


async def test_http_probe_5xx_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    def _fake_urlopen(url: str, timeout: float = 5.0) -> object:
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(waterfall.urllib.request, "urlopen", _fake_urlopen)
    probe = waterfall.Probe(image_ref="x", probe_type="http", probe_arg="http://host/healthz")
    result = await waterfall.run_probe(probe)
    assert result.ok is False
    assert "503" in result.reason


# ── Docker HEALTHCHECK probe ──────────────────────────────────────────────────


async def test_docker_healthcheck_probe_reads_container_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeContainer:
        attrs = {"State": {"Health": {"Status": "healthy"}}}

    class _FakeContainers:
        def get(self, name: str) -> _FakeContainer:
            return _FakeContainer()

    class _FakeClient:
        containers = _FakeContainers()

    monkeypatch.setattr(waterfall.docker, "from_env", lambda: _FakeClient())
    probe = waterfall.Probe(image_ref="x", probe_type="docker_healthcheck", probe_arg="redis")
    result = await waterfall.run_probe(probe)
    assert result.ok is True


async def test_docker_healthcheck_probe_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeContainer:
        attrs = {"State": {"Health": {"Status": "unhealthy"}}}

    class _FakeContainers:
        def get(self, name: str) -> _FakeContainer:
            return _FakeContainer()

    class _FakeClient:
        containers = _FakeContainers()

    monkeypatch.setattr(waterfall.docker, "from_env", lambda: _FakeClient())
    probe = waterfall.Probe(image_ref="x", probe_type="docker_healthcheck", probe_arg="redis")
    result = await waterfall.run_probe(probe)
    assert result.ok is False
    assert "unhealthy" in result.reason.lower()


async def test_docker_healthcheck_probe_container_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docker.errors

    class _FakeContainers:
        def get(self, name: str) -> object:
            raise docker.errors.NotFound(f"container {name} not found")

    class _FakeClient:
        containers = _FakeContainers()

    monkeypatch.setattr(waterfall.docker, "from_env", lambda: _FakeClient())
    probe = waterfall.Probe(image_ref="x", probe_type="docker_healthcheck", probe_arg="redis")
    result = await waterfall.run_probe(probe)
    assert result.ok is False
    assert "not found" in result.reason.lower() or "notfound" in result.reason.lower()
