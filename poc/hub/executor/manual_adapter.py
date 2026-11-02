"""ManualAdapter — operator runs the command themselves; hub logs intent.

BC-066: this is now the default executor. `describe()` returns the exact
shell command the operator should run on the affected host; `execute()`
is a no-op that returns success so the handler path stays uniform (the
hub-side `_handle_fix` branches on `EXECUTOR_NAME`, not on the result).
"""

from .base import ExecutionResult, ExecutorPlugin, Finding
from .docker_adapter import _resolve_pull_target


class ManualAdapter(ExecutorPlugin):
    def can_handle(self, finding: Finding) -> bool:
        return True  # handles anything as the fallback

    def describe(self, finding: Finding) -> str:
        if finding.source_type == "alpm":
            return f"sudo pacman -Syu {finding.package}"
        if finding.source_type == "python":
            return f"pip install --upgrade {finding.package}"
        if finding.source_type == "docker":
            # BC-066: emit the same `docker pull <image:tag>` DockerAdapter would run.
            # Operator executes it on their host; hub re-verifies on next scan.
            _, pull_target = _resolve_pull_target(finding.source_ref)
            return f"docker pull {pull_target}"
        return f"# manual fix required for {finding.package} ({finding.cve_id})"

    async def execute(self, finding: Finding) -> ExecutionResult:
        return ExecutionResult(
            success=True,
            message="Command shown — manual execution required",
            command=self.describe(finding),
        )
