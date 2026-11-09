"""BC-011 — target-container health probe.

Operator-triggered post-restart health verification. BC-009's `verify_fix`
asserts "CVE is gone from the image file"; BC-011 adds "and the service
you restarted answers my probe". Three probe types for V1:

- `docker_healthcheck` — read Docker's own `State.Health.Status` for a
  named container. Zero-config if the image declares a HEALTHCHECK.
- `tcp` — plain socket connect to `<host>:<port>`. Fast; service-agnostic.
- `http` — HTTP GET to a URL. Pass on 2xx-3xx, fail on 4xx-5xx or network error.

Policies carried via `BEACONRY_HEALTH_PROBES` env var. Syntax:
  <image_ref>=<probe_type>:<probe_arg>;<image_ref>=<probe_type>:<probe_arg>

Parser fails fast at hub startup on malformed entries — silent-skip would
make operators believe they're covered when they aren't.
"""

from __future__ import annotations

import asyncio
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal

import docker
import docker.errors

ProbeType = Literal["docker_healthcheck", "tcp", "http"]
_VALID_PROBE_TYPES: tuple[ProbeType, ...] = ("docker_healthcheck", "tcp", "http")


class MalformedPolicyError(ValueError):
    """BEACONRY_HEALTH_PROBES env var could not be parsed."""


@dataclass(frozen=True)
class Probe:
    image_ref: str
    probe_type: ProbeType
    probe_arg: str


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    reason: str


def parse_policies(env: str | None) -> dict[str, Probe]:
    """Parse BEACONRY_HEALTH_PROBES. Returns dict[image_ref -> Probe]."""
    if not env:
        return {}
    out: dict[str, Probe] = {}
    for raw_entry in env.split(";"):
        entry = raw_entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise MalformedPolicyError(
                f"policy entry missing '=': {entry!r} "
                f"(expected `<image_ref>=<probe_type>:<probe_arg>`)"
            )
        image_ref, spec = entry.split("=", 1)
        image_ref = image_ref.strip()
        spec = spec.strip()
        if ":" not in spec:
            raise MalformedPolicyError(
                f"policy for {image_ref!r} missing probe-type delimiter ':': {spec!r}"
            )
        probe_type, probe_arg = spec.split(":", 1)
        probe_type = probe_type.strip()
        probe_arg = probe_arg.strip()
        if probe_type not in _VALID_PROBE_TYPES:
            raise MalformedPolicyError(
                f"policy for {image_ref!r}: unknown probe type {probe_type!r}. "
                f"Valid: {_VALID_PROBE_TYPES}"
            )
        out[image_ref] = Probe(image_ref=image_ref, probe_type=probe_type, probe_arg=probe_arg)
    return out


def resolve_probe(image_ref: str, policies: dict[str, Probe]) -> Probe | None:
    """Return the probe for this image, or None if no policy matches."""
    return policies.get(image_ref)


async def run_probe(probe: Probe) -> ProbeResult:
    """Dispatch to the probe implementation by type."""
    if probe.probe_type == "tcp":
        return await _probe_tcp(probe.probe_arg)
    if probe.probe_type == "http":
        return await _probe_http(probe.probe_arg)
    if probe.probe_type == "docker_healthcheck":
        return await _probe_docker_healthcheck(probe.probe_arg)
    return ProbeResult(ok=False, reason=f"unknown probe type: {probe.probe_type}")


async def run_probe_with_retry(probe: Probe, *, max_attempts: int, base_s: float) -> ProbeResult:
    """BC-110: run `probe` up to `max_attempts` times with exponential
    backoff between retries.

    Fixes the common race where a probe fires 100ms after `docker restart`
    and the service hasn't bound its port / finished its healthcheck
    `starting → healthy` transition yet. Pre-BC-110 any single failure
    (even a 1ms-off-timing miss) flagged `verification_failed` and the
    operator had to manually tap Verify again after waiting.

    Backoff between attempts: `base_s × 2^(attempt-1)` (e.g. base_s=2
    → 2s, 4s, 8s). First attempt fires immediately; sleep happens
    AFTER a failure, before the next attempt. Returns the FIRST
    successful result, or the LAST failed result.
    """
    max_attempts = max(1, int(max_attempts))
    last: ProbeResult = ProbeResult(ok=False, reason="no attempts made")
    for attempt in range(1, max_attempts + 1):
        last = await run_probe(probe)
        if last.ok:
            if attempt > 1:
                last = ProbeResult(
                    ok=True,
                    reason=f"{last.reason} (passed on attempt {attempt}/{max_attempts})",
                )
            return last
        if attempt < max_attempts:
            await asyncio.sleep(base_s * (2 ** (attempt - 1)))
    return last


async def _probe_tcp(arg: str) -> ProbeResult:
    """TCP connect to <host>:<port>."""
    if ":" not in arg:
        return ProbeResult(ok=False, reason=f"malformed tcp arg (expected host:port): {arg!r}")
    host, _, port_str = arg.rpartition(":")
    try:
        port = int(port_str)
    except ValueError:
        return ProbeResult(ok=False, reason=f"malformed tcp port in {arg!r}")
    try:
        reader_writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5.0)
        _, writer = reader_writer
        writer.close()
        import contextlib as _ctx

        with _ctx.suppress(Exception):
            await writer.wait_closed()
        return ProbeResult(ok=True, reason=f"tcp connect ok {host}:{port}")
    except (ConnectionRefusedError, TimeoutError, OSError, socket.gaierror) as e:
        return ProbeResult(ok=False, reason=f"tcp connect failed {host}:{port}: {type(e).__name__}")


async def _probe_http(url: str) -> ProbeResult:
    """HTTP GET. 2xx-3xx pass; 4xx-5xx and transport errors fail."""

    def _blocking_get() -> ProbeResult:
        try:
            with urllib.request.urlopen(url, timeout=5.0) as resp:  # noqa: S310 — operator-provided
                status = getattr(resp, "status", 0) or getattr(resp, "code", 0)
                if 200 <= status < 400:
                    return ProbeResult(ok=True, reason=f"HTTP {status}")
                return ProbeResult(ok=False, reason=f"HTTP {status}")
        except urllib.error.HTTPError as e:
            return ProbeResult(ok=False, reason=f"HTTP {e.code}")
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return ProbeResult(ok=False, reason=f"HTTP transport error: {type(e).__name__}")

    return await asyncio.to_thread(_blocking_get)


async def _probe_docker_healthcheck(container_name: str) -> ProbeResult:
    """Read docker inspect <container>.State.Health.Status."""

    def _blocking_inspect() -> ProbeResult:
        try:
            client = docker.from_env()
            container = client.containers.get(container_name)
            health = container.attrs.get("State", {}).get("Health", {})
            status = health.get("Status", "no-healthcheck")
            if status == "healthy":
                return ProbeResult(ok=True, reason=f"docker reports {container_name} healthy")
            return ProbeResult(ok=False, reason=f"docker reports {container_name} {status}")
        except docker.errors.NotFound as e:
            return ProbeResult(ok=False, reason=f"container {container_name} not found: {e}")
        except docker.errors.DockerException as e:
            return ProbeResult(ok=False, reason=f"docker inspect error: {type(e).__name__}")

    return await asyncio.to_thread(_blocking_inspect)
