"""DockerAdapter — pulls the latest image tag to remediate container CVEs."""

import asyncio
import logging
import os

from .base import ExecutionResult, ExecutorPlugin, Finding

log = logging.getLogger("executor.docker")

MOCK = os.environ.get("BEACONRY_MOCK") == "1"


class DockerAdapter(ExecutorPlugin):
    def can_handle(self, finding: Finding) -> bool:
        return finding.source_type == "docker"

    def describe(self, finding: Finding) -> str:
        base = finding.source_ref.split(":")[0]
        return f"docker pull {base}:latest"

    async def execute(self, finding: Finding) -> ExecutionResult:
        base = finding.source_ref.split(":")[0]
        new_image = f"{base}:latest"
        rollback_cmd = f"docker tag {finding.source_ref} {base}:rollback"
        pull_cmd = f"docker pull {new_image}"

        if MOCK:
            print(f"    [mock] {rollback_cmd}")
            print(f"    [mock] {pull_cmd}")
            return ExecutionResult(
                success=True,
                message=f"[mock] pulled {new_image}",
                command=pull_cmd,
            )

        # Tag current image as :rollback before pulling (safety net)
        proc = await asyncio.create_subprocess_shell(
            rollback_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            log.warning("Rollback tag failed (non-fatal): %s", err.decode()[:100])

        # Pull latest
        log.info("Executing: %s", pull_cmd)
        proc = await asyncio.create_subprocess_shell(
            pull_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            return ExecutionResult(
                success=False,
                message=f"docker pull failed: {stderr.decode()[:200]}",
                command=pull_cmd,
            )

        log.info("Pulled %s successfully", new_image)
        return ExecutionResult(
            success=True,
            message=f"Pulled {new_image}",
            command=pull_cmd,
        )
