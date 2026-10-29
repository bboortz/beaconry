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
