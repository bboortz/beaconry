"""Fixture SBOMs and grype matches for BC-001 mocked demo.

Loaded when BEACONRY_MOCK=1. Encodes two findings:

- CVE-DEMO-0001: openssl 1.0.1 → 1.0.2
  CVSS 9.8, KEV=True, internet-facing → Tier 0, safe class → auto-fix

- CVE-DEMO-0002: zlib 1.2.3 → 1.2.4
  CVSS 9.3, KEV=False, internet-facing → Tier 1, operator confirm

The 'beaconry' key on each match is a fixture-only augmentation carrying
exposure + KEV flags that a real deployment would pull from feeds.
"""

from __future__ import annotations

FIXTURE_HOST = "homelab-demo"
FIXTURE_IMAGE = "nginx:1.24-fixture"


def fixture_sbom() -> dict:
    """Minimal syft-json shape with two artifacts (openssl, zlib)."""
    return {
        "schema": {"version": "16.0.10"},
        "artifacts": [
            {"id": "pkg-openssl", "name": "openssl", "version": "1.0.1", "type": "deb"},
            {"id": "pkg-zlib", "name": "zlib", "version": "1.2.3", "type": "deb"},
        ],
        "artifactRelationships": [],
        "source": {"type": "image", "target": FIXTURE_IMAGE},
        "distro": {"name": "Debian", "id": "debian", "versionID": "12"},
        "descriptor": {"name": "beaconry-fixture", "version": "0.1.0"},
    }


def fixture_scan_payload() -> dict:
    return {
        "host": FIXTURE_HOST,
        "timestamp": 0,
        "sboms": [
            {
                "source_meta": {"type": "docker", "image": FIXTURE_IMAGE},
                "sbom": fixture_sbom(),
            }
        ],
        "open_ports": [{"port": "443", "address": "0.0.0.0:443"}],
    }


def fixture_grype_matches() -> list[dict]:
    return [
        {
            "vulnerability": {
                "id": "CVE-DEMO-0001",
                "severity": "Critical",
                "cvss": [{"metrics": {"baseScore": 9.8}}],
                "description": (
                    "Heap buffer overflow in openssl heartbeat extension — "
                    "fixture CVE for demo purposes only."
                ),
                "fix": {"versions": ["1.0.2"]},
            },
            "artifact": {"name": "openssl", "version": "1.0.1"},
            "beaconry": {"kev": True, "exposure": "internet-facing"},
        },
        {
            "vulnerability": {
                "id": "CVE-DEMO-0002",
                "severity": "Critical",
                "cvss": [{"metrics": {"baseScore": 9.3}}],
                "description": (
                    "Integer overflow in zlib inflate — fixture CVE for demo purposes only."
                ),
                "fix": {"versions": ["1.2.4"]},
            },
            "artifact": {"name": "zlib", "version": "1.2.3"},
            "beaconry": {"kev": False, "exposure": "internet-facing"},
        },
    ]
