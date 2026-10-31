"""Reusable candidate payloads for scenarios.

Every candidate dict here matches the shape `/test/scan` accepts — no
grype involvement.
"""

from __future__ import annotations

from typing import Any


def cand(  # noqa: PLR0913 — fixture factory, keyword-only API; signature matches hub.Finding
    *,
    cve_id: str = "CVE-A",
    cvss: float = 9.5,
    severity: str = "Critical",
    package: str = "openssl",
    version: str = "1.0",
    fixed_version: str = "1.1",
    source_ref: str = "nginx:1.24",
    source_type: str = "docker",
    description: str = "simulated finding",
    kev: bool = False,
) -> dict[str, Any]:
    """Build a candidate dict suitable for POST /test/scan."""
    return {
        "cve_id": cve_id,
        "cvss": cvss,
        "severity": severity,
        "package": package,
        "version": version,
        "fixed_version": fixed_version,
        "source_type": source_type,
        "source_ref": source_ref,
        "description": description,
        "kev": kev,
    }
