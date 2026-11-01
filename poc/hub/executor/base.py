"""Executor plugin interface — every remediation adapter implements this."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Finding:
    id: str
    host: str
    cve_id: str
    package: str
    version: str
    fixed_version: str
    severity: str
    cvss: float
    source_type: str  # "docker" | "alpm" | "python"
    source_ref: str  # image name ("nginx:1.24") or package name ("openssl")
    description: str
    tier: int = 3  # urgency tier (0 highest, 3 lowest) — see poc/hub/urgency.py
    kev: bool = False  # True if the CVE is in the CISA KEV catalog (BC-020)
    internet_facing: bool = (
        False  # BC-030: True if source_ref's running containers publish non-loopback ports
    )
    # BC-043: when `cve_id` is non-CVE-shaped (GO-*, GHSA-*, AVG-*), the first
    # CVE-shaped id grype provided in `relatedVulnerabilities`. Lets operators
    # cross-reference NVD / GitHub advisories; carries the KEV paper trail.
    cve_alias: str | None = None


@dataclass
class ExecutionResult:
    success: bool
    message: str
    command: str = ""


class ExecutorPlugin(ABC):
    @abstractmethod
    def can_handle(self, finding: Finding) -> bool:
        """Return True if this executor can remediate this finding."""
        ...

    @abstractmethod
    async def execute(self, finding: Finding) -> ExecutionResult:
        """Execute the remediation. Returns result."""
        ...

    @abstractmethod
    def describe(self, finding: Finding) -> str:
        """Return the command string shown in the Telegram notification."""
        ...
