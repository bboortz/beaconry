"""ManualAdapter — fallback for everything that can't be auto-fixed.

Shows the operator the exact command. Marks the finding as requiring manual action.
"""

from .base import ExecutionResult, ExecutorPlugin, Finding


class ManualAdapter(ExecutorPlugin):
    def can_handle(self, finding: Finding) -> bool:
        return True  # handles anything as the fallback

    def describe(self, finding: Finding) -> str:
        if finding.source_type == "alpm":
            return f"sudo pacman -Syu {finding.package}"
        if finding.source_type == "python":
            return f"pip install --upgrade {finding.package}"
        return f"# manual fix required for {finding.package} ({finding.cve_id})"

    async def execute(self, finding: Finding) -> ExecutionResult:
        return ExecutionResult(
            success=True,
            message="Command shown — manual execution required",
            command=self.describe(finding),
        )
