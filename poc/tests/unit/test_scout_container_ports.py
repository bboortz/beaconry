"""BC-030 — scout's container-port → internet_facing parsing.

Covers:
    - CAP-T068: Scout emits per-container port-publication map (the parse
      helper that turns Docker's NetworkSettings.Ports dict into a bool).
"""

from __future__ import annotations

from poc.scout.scout import is_container_internet_facing


def test_no_ports_dict_safe() -> None:
    """Containers with no port mappings (None or {}) → not exposed."""
    assert is_container_internet_facing(None) is False
    assert is_container_internet_facing({}) is False


def test_unpublished_port_is_falsy() -> None:
    """Docker returns `None` bindings for internally-exposed-but-not-published ports."""
    assert is_container_internet_facing({"80/tcp": None}) is False
    assert is_container_internet_facing({"80/tcp": []}) is False


def test_is_exposed_truthy_hosts() -> None:
    """0.0.0.0, ::, and empty HostIp (Docker's default) → exposed."""
    for host_ip in ("0.0.0.0", "::", ""):
        assert (
            is_container_internet_facing({"80/tcp": [{"HostIp": host_ip, "HostPort": "80"}]})
            is True
        ), f"HostIp={host_ip!r} must count as exposed"


def test_is_exposed_loopback_falsy() -> None:
    """127.0.0.1, ::1, localhost → NOT exposed."""
    for host_ip in ("127.0.0.1", "::1", "localhost"):
        assert (
            is_container_internet_facing({"80/tcp": [{"HostIp": host_ip, "HostPort": "80"}]})
            is False
        ), f"HostIp={host_ip!r} must NOT count as exposed"


def test_is_exposed_mixed_bindings_or_logic() -> None:
    """Any single non-loopback binding across any port → exposed."""
    ports = {
        "80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "80"}],
        "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "443"}],
    }
    assert is_container_internet_facing(ports) is True


def test_is_exposed_all_loopback_is_falsy() -> None:
    """All ports loopback-bound → not exposed (even with many ports)."""
    ports = {
        "80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "80"}],
        "443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "443"}],
        "8080/tcp": [{"HostIp": "::1", "HostPort": "8080"}],
    }
    assert is_container_internet_facing(ports) is False


def test_is_exposed_routable_ipv4() -> None:
    """A specific host IP (not loopback) counts as exposed — operator may be
    binding to one NIC only, but that NIC is reachable from somewhere."""
    assert (
        is_container_internet_facing({"80/tcp": [{"HostIp": "192.168.1.10", "HostPort": "80"}]})
        is True
    )


def test_is_exposed_handles_missing_hostip_key() -> None:
    """Robustness: a binding dict without a HostIp key defaults to empty → exposed."""
    assert is_container_internet_facing({"80/tcp": [{"HostPort": "80"}]}) is True


def test_multi_container_or_aggregate() -> None:
    """A helper-level smoke that the OR aggregation across multiple ports works:
    even if the first port is loopback, a later exposed port flips the result."""
    ports = {
        "22/tcp": [{"HostIp": "127.0.0.1", "HostPort": "22"}],
        "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "80"}],
    }
    assert is_container_internet_facing(ports) is True
