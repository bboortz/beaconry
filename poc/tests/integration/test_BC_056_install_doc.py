"""BC-056 — docs/INSTALL.md structure + drift lint (short-name reshape).

Covers:
    - CAP-T112: verify-install-doc lint
    - CAP-B039: Homelab operator installs beaconry via Ansible with safe
      credentials handling using repo-authoritative docs
"""

from __future__ import annotations

from pathlib import Path

from poc.scripts import verify_install_doc as vid

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALL_MD = REPO_ROOT / "docs" / "INSTALL.md"


# ── INSTALL.md shape ────────────────────────────────────────────────────────


def test_install_md_exists() -> None:
    assert INSTALL_MD.exists(), "docs/INSTALL.md must exist"


def test_install_md_sections_present() -> None:
    """CAP-B039: eight canonical sections — operator can navigate the doc."""
    text = INSTALL_MD.read_text()
    for heading in (
        "## Prerequisites",
        "## Quick start",
        "## Multi-host install",
        "## Operator CLI setup",
        "## Ansible installation",
        "## Credentials",
        "## Verification",
        "## Troubleshooting",
    ):
        assert heading in text, f"missing section: {heading}"


def test_ansible_section_ships_copyable() -> None:
    """CAP-B039: role tree + playbook + vault example all present."""
    text = INSTALL_MD.read_text()
    ansible_idx = text.index("## Ansible installation")
    creds_idx = text.index("## Credentials", ansible_idx)
    section = text[ansible_idx:creds_idx]
    assert "roles/" in section
    assert "beaconry_hub" in section and "beaconry_scout" in section
    assert "ansible-vault" in section.lower() or "vault.yml" in section
    assert "community.docker" in section
    # Vault example must mention the three secret vars under BC-055
    # short names (telegram_bot_token + the TWO independent hub tokens).
    assert "telegram_bot_token" in section
    assert "hub_scout_token" in section
    assert "hub_cli_token" in section


def test_credentials_section_forbids_commit() -> None:
    """CAP-B039: Credentials section explicitly says 'never commit'."""
    text = INSTALL_MD.read_text()
    creds_idx = text.index("## Credentials")
    verif_idx = text.index("## Verification", creds_idx)
    section = text[creds_idx:verif_idx]
    low = section.lower()
    assert "never commit" in low or "must never be committed" in low
    assert ".env" in section
    assert "ansible vault" in low or "ansible-vault" in low
    assert "systemd" in low and "environmentfile" in low


def test_credentials_rule_also_in_header() -> None:
    text = INSTALL_MD.read_text()
    header = text[: text.index("## Prerequisites")]
    low = header.lower()
    assert "credential" in low
    assert "never" in low and ("commit" in low or "committed" in low)


# ── Lint — stale references caught ─────────────────────────────────────────


def test_lint_catches_stale_env_var() -> None:
    install = "Here is `HUB_NONEXISTENT` used in a sentence.\n"
    variables = (
        "<!-- BC-039 BEGIN VARIABLES TABLE -->\n"
        "| `HUB_URL` | http://hub:8000 | URL | poc/x | r | BC-1 |\n"
        "<!-- BC-039 END VARIABLES TABLE -->\n"
    )
    violations = vid.check_env_vars(install, variables)
    assert any("HUB_NONEXISTENT" in v for v in violations)


def test_lint_env_var_passes_when_documented() -> None:
    install = "Reference to `HUB_URL` here.\n"
    variables = "| `HUB_URL` | http://hub:8000 | URL | poc/x | r | BC-1 |\n"
    assert vid.check_env_vars(install, variables) == []


def test_lint_ignores_short_words() -> None:
    """Non-env identifiers (CVE, KEV, NET, OPEN) must not false-positive."""
    install = "Columns include `KEV`, `NET`, `CVE`, `OPEN`, `FLEET`.\n"
    variables = ""
    assert vid.check_env_vars(install, variables) == []


def test_lint_catches_stale_make_target() -> None:
    install = "Run `make nonexistent-target` to do the thing.\n"
    makefile = ".PHONY: check lint real-target\nreal-target:\n\t@echo ok\n"
    violations = vid.check_make_targets(install, makefile)
    assert any("nonexistent-target" in v for v in violations)


def test_lint_make_target_passes_when_in_phony() -> None:
    install = "Run `make status` to check.\n"
    makefile = ".PHONY: status\nstatus:\n\t@echo ok\n"
    assert vid.check_make_targets(install, makefile) == []


def test_lint_catches_stale_file_path(tmp_path: Path) -> None:
    install = "See `poc/does-not-exist.py` for details.\n"
    (tmp_path / "docs").mkdir()
    violations = vid.check_file_paths(install, tmp_path)
    assert any("poc/does-not-exist.py" in v for v in violations)


def test_lint_file_path_passes_when_exists(tmp_path: Path) -> None:
    (tmp_path / "poc").mkdir()
    (tmp_path / "poc" / "real.py").write_text("")
    install = "See `poc/real.py` for details.\n"
    assert vid.check_file_paths(install, tmp_path) == []


def test_lint_skips_target_host_paths(tmp_path: Path) -> None:
    """Absolute paths + role-tree paths are target-host destinations, not
    repo files; the lint ignores them."""
    install = (
        "Put the file at `/etc/beaconry/scout.yml`.\n"
        "Role: `roles/beaconry_hub/defaults/main.yml`.\n"
        "User config: `~/.config/beaconry/cli.yml`.\n"
    )
    assert vid.check_file_paths(install, tmp_path) == []


# ── Lint — live repo ─────────────────────────────────────────────────────


def test_lint_passes_live_repo() -> None:
    violations = vid.check(REPO_ROOT)
    assert violations == [], "verify-install-doc drift:\n  " + "\n  ".join(violations)


def test_lint_cli_passes_live_repo() -> None:
    import subprocess

    r = subprocess.run(
        ["python3", "-m", "poc.scripts.verify_install_doc"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"verify-install-doc exited {r.returncode}:\n{r.stdout}\n{r.stderr}"


# ── Make-target wiring ───────────────────────────────────────────────────


def test_verify_install_doc_is_in_phony() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text()
    phony = vid.parse_phony_targets(makefile)
    assert "verify-install-doc" in phony


def test_verify_install_doc_is_in_check() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text()
    for line in makefile.splitlines():
        if line.startswith("check:") and "verify-install-doc" in line:
            return
    raise AssertionError("`check:` must depend on `verify-install-doc`")
