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


def _parse_positive_int(raw: str | None, default: int, name: str) -> int:
    """BC-111: lenient env parse — invalid / non-positive → default + warn."""
    try:
        v = int(raw) if raw is not None else default
    except ValueError:
        log.warning("%s=%r invalid; using %d", name, raw, default)
        return default
    if v < 1:
        log.warning("%s=%d must be >= 1; using %d", name, v, default)
        return default
    return v


# BC-111: retry `docker pull` on transient network / registry 5xx with
# exponential backoff. Pre-BC-111 a one-off registry hiccup made the Fix
# button fail immediately — operator had to tap Fix-now again manually.
# Now the adapter retries N times before returning
# ExecutionResult(success=False).
DOCKER_PULL_MAX_ATTEMPTS = _parse_positive_int(
    os.environ.get("BEACONRY_DOCKER_PULL_MAX_ATTEMPTS"),
    3,
    "BEACONRY_DOCKER_PULL_MAX_ATTEMPTS",
)
DOCKER_PULL_BASE_S = _parse_positive_int(
    os.environ.get("BEACONRY_DOCKER_PULL_BASE_S"),
    5,
    "BEACONRY_DOCKER_PULL_BASE_S",
)


async def _run_docker_pull_once(pull_argv: list[str]) -> tuple[bool, str]:
    """BC-111: single-attempt `docker pull`. Returns (ok, stderr_tail).

    Extracted for retry symmetry — both first-try and retry go through
    this exact subprocess invocation.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *pull_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
    except (OSError, FileNotFoundError) as e:
        return False, f"spawn failed: {e}"
    if proc.returncode != 0:
        return False, stderr.decode(errors="replace")[:400]
    return True, ""


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

        # BC-111: pull same-track tag with retry on transient failures.
        # Registry 5xx / connection resets / DNS flakes clear on retry;
        # auth failures and tag-not-found do not — but we retry blindly
        # because distinguishing them from stderr strings is brittle
        # (varies per registry). The caller surfaces the final message
        # unchanged, so a permanently-failing tag still fails exactly
        # as before, just after more attempts.
        last_err = "no attempts made"
        for attempt in range(1, DOCKER_PULL_MAX_ATTEMPTS + 1):
            log.info(
                "Executing: %s (attempt %d/%d)",
                pull_cmd,
                attempt,
                DOCKER_PULL_MAX_ATTEMPTS,
            )
            ok, err = await _run_docker_pull_once(pull_argv)
            if ok:
                msg = f"Pulled {pull_target}"
                if attempt > 1:
                    msg += f" (succeeded on attempt {attempt}/{DOCKER_PULL_MAX_ATTEMPTS})"
                log.info("Pulled %s successfully on attempt %d", pull_target, attempt)
                return ExecutionResult(success=True, message=msg, command=pull_cmd)
            last_err = err
            log.warning(
                "docker pull attempt %d/%d failed: %s",
                attempt,
                DOCKER_PULL_MAX_ATTEMPTS,
                err[:200],
            )
            if attempt < DOCKER_PULL_MAX_ATTEMPTS:
                backoff = DOCKER_PULL_BASE_S * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)

        return ExecutionResult(
            success=False,
            message=f"docker pull failed: {last_err[:200]}",
            command=pull_cmd,
        )
