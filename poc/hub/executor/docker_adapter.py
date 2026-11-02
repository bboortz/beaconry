"""DockerAdapter — re-pulls the image's existing tag (same track) to remediate container CVEs.

BC-014: preserves `source_ref` tag verbatim. Fluid tags (`:alpine`, `:16-alpine`,
`:stable`) pick up upstream patch updates via Docker Hub tag-mutability; pinned
tags (`:16.1.3-alpine`) stay pinned so operators can't be silently moved across
major versions. If source_ref has no tag at all (`nginx`), falls back to `:latest`
which matches Docker's own default.
"""

import asyncio
import logging
import os

from .base import ExecutionResult, ExecutorPlugin, Finding

log = logging.getLogger("executor.docker")

MOCK = os.environ.get("BEACONRY_MOCK") == "1"


def _resolve_pull_target(source_ref: str) -> tuple[str, str]:
    """Return (base_name, pull_target). pull_target is the full `name:tag` to pull.

    `source_ref="postgres:16-alpine"` → ("postgres", "postgres:16-alpine")
    `source_ref="nginx"` → ("nginx", "nginx:latest")  — Docker's own fallback.
    """
    if ":" in source_ref:
        base_name = source_ref.split(":")[0]
        return base_name, source_ref
    return source_ref, f"{source_ref}:latest"


class DockerAdapter(ExecutorPlugin):
    def can_handle(self, finding: Finding) -> bool:
        return finding.source_type == "docker"

    def describe(self, finding: Finding) -> str:
        _, pull_target = _resolve_pull_target(finding.source_ref)
        return f"docker pull {pull_target}"

    async def execute(self, finding: Finding) -> ExecutionResult:
        base_name, pull_target = _resolve_pull_target(finding.source_ref)
        # BC-067: `source_ref` originates in the SBOM — a crafted image
        # tag like `nginx; rm -rf /; #` would have been shell-injected
        # under the previous `create_subprocess_shell` path. argv form
        # of `create_subprocess_exec` passes each token as a positional
        # kernel argument, so shell metacharacters are inert. The
        # printable `command` field below is for display only — never
        # fed to a shell.
        rollback_argv = ["docker", "tag", finding.source_ref, f"{base_name}:rollback"]
        pull_argv = ["docker", "pull", pull_target]
        rollback_cmd = " ".join(rollback_argv)  # display-only
        pull_cmd = " ".join(pull_argv)  # display-only

        if MOCK:
            print(f"    [mock] {rollback_cmd}")
            print(f"    [mock] {pull_cmd}")
            return ExecutionResult(
                success=True,
                message=f"[mock] pulled {pull_target}",
                command=pull_cmd,
            )

        # Tag current image as :rollback before pulling (safety net)
        proc = await asyncio.create_subprocess_exec(
            *rollback_argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            log.warning("Rollback tag failed (non-fatal): %s", err.decode()[:100])

        # Pull same-track tag
        log.info("Executing: %s", pull_cmd)
        proc = await asyncio.create_subprocess_exec(
            *pull_argv,
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

        log.info("Pulled %s successfully", pull_target)
        return ExecutionResult(
            success=True,
            message=f"Pulled {pull_target}",
            command=pull_cmd,
        )
