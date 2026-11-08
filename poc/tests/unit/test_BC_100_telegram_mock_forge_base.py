"""BC-100 — telegram_mock onto the forge-signed scratch base (BC-099 recipe).

Covers:
    - CAP-T202: poc/telegram_mock/Dockerfile carries the same
      structural invariants as poc/{hub,scout}/Dockerfile post-BC-099:
      BuildKit syntax directive, named runtime-src stage, forge
      builder base, BC-099 ABI guard with marker text,
      Python-minor-agnostic staging mv, `COPY --from=builder /stage /`
      in runtime, baked HEALTHCHECK, CMD adapted to the scratch
      ENTRYPOINT (no `python3` prefix), zero python3.X literals in
      instruction lines.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MOCK_DF = REPO / "poc" / "telegram_mock" / "Dockerfile"


# ── G1: syntax directive ─────────────────────────────────────────────────────


def test_syntax_directive_present() -> None:
    first_line = MOCK_DF.read_text().splitlines()[0]
    assert re.match(r"^#\s*syntax\s*=\s*docker/dockerfile:\S+\s*$", first_line), (
        f"telegram_mock/Dockerfile first line must be a syntax directive; got: {first_line!r}"
    )


# ── G2: runtime-src named stage + FROM runtime-src AS runtime ────────────────


def test_runtime_src_stage_present() -> None:
    body = MOCK_DF.read_text()
    assert re.search(
        r"^FROM\s+ghcr\.io/bboortz/python-scratch-trixie:latest\s+AS\s+runtime-src\b",
        body,
        re.MULTILINE,
    ), "telegram_mock/Dockerfile missing `FROM python-scratch-trixie:latest AS runtime-src`"


def test_runtime_stage_from_runtime_src() -> None:
    body = MOCK_DF.read_text()
    assert re.search(r"^FROM\s+runtime-src\s+AS\s+runtime\b", body, re.MULTILINE), (
        "telegram_mock/Dockerfile runtime stage must `FROM runtime-src AS runtime`"
    )


def test_builder_stage_is_forge_builder() -> None:
    body = MOCK_DF.read_text()
    assert re.search(
        r"^FROM\s+ghcr\.io/bboortz/forge-builder-trixie:latest\s+AS\s+builder\b",
        body,
        re.MULTILINE,
    ), "telegram_mock/Dockerfile builder stage must `FROM forge-builder-trixie:latest`"


# ── G3: BC-099 ABI guard RUN step ───────────────────────────────────────────


def test_abi_guard_run_step_present() -> None:
    body = MOCK_DF.read_text()
    assert re.search(
        r"^RUN\s+--mount=from=runtime-src,source=/usr/local/lib,target=/runtime-lib\b",
        body,
        re.MULTILINE,
    ), "telegram_mock/Dockerfile missing BC-099 ABI guard RUN step"


def test_abi_guard_emits_marker() -> None:
    body = MOCK_DF.read_text()
    assert "BC-099 ABI guard tripped" in body, (
        "telegram_mock/Dockerfile ABI guard must include the `BC-099 ABI guard tripped` marker"
    )


def test_abi_guard_compares_minors() -> None:
    body = MOCK_DF.read_text()
    assert 'BUILDER_PY_VER="$(python3 -c' in body
    assert "RUNTIME_PY_VER=" in body and "/runtime-lib" in body
    assert '[ "$BUILDER_PY_VER" != "$RUNTIME_PY_VER" ]' in body


# ── G4: single staged COPY in runtime ───────────────────────────────────────


def test_runtime_copies_stage_root() -> None:
    body = MOCK_DF.read_text()
    assert re.search(r"^COPY\s+--from=builder\s+/stage\s+/\s*$", body, re.MULTILINE), (
        "telegram_mock/Dockerfile runtime must `COPY --from=builder /stage /`"
    )


# ── G5: CMD adapted to scratch ENTRYPOINT ───────────────────────────────────


def test_cmd_adapted_to_scratch_entrypoint() -> None:
    """Scratch base has ENTRYPOINT ["/usr/local/bin/python3"]; the top-level
    CMD must not re-prefix with `python3`, else the container tries
    `python3 python3 ...`.

    Match top-level CMD only (column 0) — HEALTHCHECK's continuation also
    begins with `CMD` but is indented.
    """
    body = MOCK_DF.read_text()
    cmd_line = next(
        (ln for ln in body.splitlines() if ln.startswith("CMD ")),
        None,
    )
    assert cmd_line is not None, "telegram_mock/Dockerfile missing top-level CMD line"
    # Scratch ENTRYPOINT is python3 — CMD args must not re-prefix.
    assert '"python3"' not in cmd_line, (
        f"telegram_mock CMD must not re-prefix `python3` (scratch ENTRYPOINT is python3): "
        f"{cmd_line}"
    )
    # mock_bot module must still be referenced
    assert "mock_bot:app" in cmd_line, (
        f"telegram_mock top-level CMD must still point at mock_bot:app: {cmd_line}"
    )


# ── G6: HEALTHCHECK baked in ────────────────────────────────────────────────


def test_healthcheck_baked() -> None:
    body = MOCK_DF.read_text()
    assert re.search(
        r"HEALTHCHECK\s+--interval=\S+\s+--timeout=\S+\s+--start-period=\S+\s+--retries=\d+\s*\\?\s*\n"
        r'\s*CMD\s+\[\s*"/hc"\s*,\s*"-t"\s*,\s*"3"\s*,\s*"http://localhost:8080/readyz"\s*\]',
        body,
    ), (
        "telegram_mock/Dockerfile missing baked HEALTHCHECK targeting "
        "`/hc -t 3 http://localhost:8080/readyz`"
    )


# ── G7: zero python3.X literals in instruction lines ────────────────────────


def test_no_python_minor_literals() -> None:
    """COPY / RUN / ARG / … lines must not reference `python3.X`.

    Comments may still name historical versions; that's prose.
    """
    _instruction_re = re.compile(
        r"^\s*(RUN|COPY|ADD|ARG|ENV|CMD|ENTRYPOINT|WORKDIR|LABEL|EXPOSE|USER|HEALTHCHECK)\b",
        re.IGNORECASE,
    )
    _py_literal_re = re.compile(r"python3\.\d+", re.IGNORECASE)
    offenders: list[str] = []
    for lineno, raw in enumerate(MOCK_DF.read_text().splitlines(), start=1):
        if raw.lstrip().startswith("#") or not raw.strip():
            continue
        if _py_literal_re.search(raw):
            offenders.append(f"  {MOCK_DF.name}:{lineno}: {raw.strip()}")
    assert not offenders, (
        "telegram_mock/Dockerfile must not carry python3.X literals in instructions.\n"
        "Found:\n" + "\n".join(offenders)
    )
