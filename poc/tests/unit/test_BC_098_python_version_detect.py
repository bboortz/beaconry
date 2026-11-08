"""BC-098 — Stage-at-runtime-path: Dockerfiles detect Python minor at build.

Covers:
    - CAP-T200: both poc/{hub,scout}/Dockerfile contain zero Python
      version literals in executable (RUN/COPY/ARG/CMD/ENTRYPOINT)
      lines. The builder stage detects the Python minor at build time
      via `python3 -c 'import sys; ...'` and moves site-packages into
      `/stage/usr/local/lib/python${PY_VER}/`. The runtime stage copies
      the staged root with `COPY --from=builder /stage /`. No
      `COPY --from=builder /install/lib/python3.X/...` remains.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HUB_DF = REPO / "poc" / "hub" / "Dockerfile"
SCOUT_DF = REPO / "poc" / "scout" / "Dockerfile"

_DOCKERFILES = (HUB_DF, SCOUT_DF)

# Executable lines — not comments. Match Dockerfile instruction lines
# that would act on a literal path at build/run time.
_INSTRUCTION_PREFIX_RE = re.compile(
    r"^\s*(RUN|COPY|ADD|ARG|ENV|CMD|ENTRYPOINT|WORKDIR|LABEL|EXPOSE|USER)\b",
    re.IGNORECASE,
)

# Python version literal we must NOT find in instruction lines.
_PY_LITERAL_RE = re.compile(r"python3\.\d+", re.IGNORECASE)


# ── G1: zero Python version literals in instruction lines ────────────────────


def test_no_python_minor_literals_in_instructions() -> None:
    """No COPY / RUN / ARG / … line may reference `python3.X`.

    Comments (# …) may still name historical versions; those are prose
    and don't influence the build. The forbidden shape is anything that
    would act on a specific minor at build time — exactly what BC-098
    retires.
    """
    offenders: list[str] = []
    for df in _DOCKERFILES:
        for lineno, raw in enumerate(df.read_text().splitlines(), start=1):
            stripped = raw.lstrip()
            if stripped.startswith("#") or not stripped:
                continue
            if not _INSTRUCTION_PREFIX_RE.match(raw):
                # Likely a continuation (`\`) of the previous instruction
                # — treat the whole line as part of the instruction.
                pass
            if _PY_LITERAL_RE.search(raw):
                offenders.append(f"  {df.name}:{lineno}: {stripped}")
    assert not offenders, (
        "BC-098 forbids `python3.X` literals in Dockerfile instructions. "
        "Use the builder-stage PY_VER shell detection instead.\nFound:\n" + "\n".join(offenders)
    )


# ── G2: builder stage carries the PY_VER detect + mv step ────────────────────


def test_py_ver_detect_step_present_in_both() -> None:
    """Each Dockerfile has exactly one RUN that queries `sys.version_info`
    and moves site-packages into `/stage/usr/local/lib/python${PY_VER}`."""
    # Match the key tokens of the full step — regex kept loose on
    # whitespace + line continuations so minor formatting changes don't
    # break the lint.
    detect_re = re.compile(
        r'PY_VER="\$\(python3\s+-c\s+[\'"]import\s+sys[^\n]*?\)"\s*\\?\s*\n'
        r".*?mv\s+[\"']/install/lib/python\$\{PY_VER\}/site-packages[\"']\s*\\?\s*\n"
        r".*?[\"']/stage/usr/local/lib/python\$\{PY_VER\}/site-packages[\"']",
        re.DOTALL,
    )
    for df in _DOCKERFILES:
        body = df.read_text()
        matches = detect_re.findall(body)
        assert len(matches) == 1, (
            f"{df.name}: expected exactly one PY_VER detect + mv step, got {len(matches)}"
        )


# ── G3: runtime stage uses the single staged COPY ───────────────────────────


def test_runtime_copies_stage_root() -> None:
    """Each Dockerfile has exactly one `COPY --from=builder /stage /`."""
    stage_re = re.compile(r"^COPY\s+--from=builder\s+/stage\s+/\s*$", re.MULTILINE)
    for df in _DOCKERFILES:
        body = df.read_text()
        matches = stage_re.findall(body)
        assert len(matches) == 1, (
            f"{df.name}: expected exactly one `COPY --from=builder /stage /`, got {len(matches)}"
        )


def test_no_legacy_install_lib_copy_remains() -> None:
    """No `COPY --from=builder /install/lib/python3.X/...` anywhere."""
    legacy_re = re.compile(r"^COPY\s+--from=builder\s+/install/lib/python3\.\d+/", re.MULTILINE)
    for df in _DOCKERFILES:
        body = df.read_text()
        assert not legacy_re.search(body), (
            f"{df.name}: legacy `COPY --from=builder /install/lib/python3.X/...` "
            f"still present — replace with `COPY --from=builder /stage /`"
        )


# ── Guard: syft step still runs BEFORE the move (SBOM must see wheels) ──────


def test_syft_runs_before_py_ver_move() -> None:
    """Order matters: if the move runs first, /install is empty and the
    SBOM captures nothing. syft must come first in both files."""
    for df in _DOCKERFILES:
        body = df.read_text()
        syft_idx = body.find("syft dir:/install -o syft-json=/sbom.json")
        move_idx = body.find('mv "/install/lib/python${PY_VER}/site-packages"')
        if move_idx == -1:
            # Also accept single-quoted form
            move_idx = body.find("mv '/install/lib/python${PY_VER}/site-packages'")
        assert syft_idx != -1, f"{df.name}: syft step missing"
        assert move_idx != -1, f"{df.name}: PY_VER mv step missing"
        assert syft_idx < move_idx, (
            f"{df.name}: syft step must run BEFORE the site-packages mv "
            f"(otherwise SBOM is empty). syft at pos {syft_idx}, mv at {move_idx}"
        )


# ── Guard: redundant /stage/data COPY dropped from hub ──────────────────────


def test_hub_no_redundant_stage_data_copy() -> None:
    """Hub used to have `COPY --from=builder --chown=65532:65532 /stage/data /data`.
    BC-098's `COPY --from=builder /stage /` subsumes it; the standalone
    copy is removed."""
    body = HUB_DF.read_text()
    assert "/stage/data /data" not in body, (
        "hub/Dockerfile still has the redundant `COPY .../stage/data /data` — "
        "BC-098's `COPY --from=builder /stage /` already covers it"
    )
