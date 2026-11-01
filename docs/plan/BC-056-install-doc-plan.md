# BC-056 — INSTALL.md + verify-install-doc (short-name reshape)

*Branch: `feat/BC-056-install-doc`*
*Start: 2026-11-01*

## Scope

- `docs/INSTALL.md` — authoritative install guide (8 sections).
- `poc/scripts/verify_install_doc.py` — lint over INSTALL.md:
  1. Every env var referenced must be in VARIABLES.md (short-name shape).
  2. Every `make <target>` must be in Makefile `.PHONY:`.
  3. Every `poc/…` or `docs/…` path mentioned must exist on disk.
- Makefile target `verify-install-doc` wired into `make check`.
- Tests covering doc structure + Ansible/credentials content + lint
  positives/negatives + Makefile wiring + live-repo-clean regression.

## Design decisions

### Short-name reshape

BC-054 (the pre-reset version) assumed `BEACONRY_HUB_*` long names.
BC-056 uses the BC-055 short names everywhere (`HUB_URL`, `HUB_TOKEN`,
`HUB_SCOUT_TOKEN`, `HUB_CLI_TOKEN`, `HUB_PUBLISH_ADDR`). Operator
mental model: one `.env` on the hub, one `.env` per scout host, one
`~/.config/beaconry/cli.yml` per operator CLI host.

### Tailscale as the recommended transport

Operator runs Tailscale across their fleet; hub exposes port 8000 on
the Tailscale IP (`100.x.x.x`). No TLS termination in beaconry — the
Tailscale link is already encrypted. Reverse-proxy fallback mentioned
for operators who prefer traditional TLS at nginx/caddy.

### Three safe places for tokens

Credentials section enumerates: (1) `.env` on each host, 0600, in
`.gitignore`. (2) Ansible Vault. (3) systemd `EnvironmentFile=`.
Up-front banner repeats "never commit tokens to git".

### Lint ignores target-host destinations

Paths like `/etc/beaconry/scout.yml`, `~/.config/beaconry/cli.yml`,
`roles/…` are install destinations or Ansible scaffolding operators
create themselves — definitionally not in this repo. The regex only
matches backticked `poc/…` or `docs/…` paths.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| INSTALL.md exists on disk | integration | `test_BC_056_install_doc.py::test_install_md_exists` | ✓ |
| INSTALL.md has 8 mandated sections | integration | `test_BC_056_install_doc.py::test_install_md_sections_present` | ✓ |
| Ansible section ships copyable artifacts | integration | `test_BC_056_install_doc.py::test_ansible_section_ships_copyable` | ✓ |
| Credentials section forbids commit + 3 safe places | integration | `test_BC_056_install_doc.py::test_credentials_section_forbids_commit` | ✓ |
| Top-of-doc banner repeats credentials rule | integration | `test_BC_056_install_doc.py::test_credentials_rule_also_in_header` | ✓ |
| Lint catches stale env var | integration | `test_BC_056_install_doc.py::test_lint_catches_stale_env_var` | ✓ |
| Lint env-var passes when documented | integration | `test_BC_056_install_doc.py::test_lint_env_var_passes_when_documented` | ✓ |
| Lint ignores short non-env identifiers (KEV/CVE/NET) | integration | `test_BC_056_install_doc.py::test_lint_ignores_short_words` | ✓ |
| Lint catches stale make target | integration | `test_BC_056_install_doc.py::test_lint_catches_stale_make_target` | ✓ |
| Lint make-target passes when in .PHONY | integration | `test_BC_056_install_doc.py::test_lint_make_target_passes_when_in_phony` | ✓ |
| Lint catches stale file path | integration | `test_BC_056_install_doc.py::test_lint_catches_stale_file_path` | ✓ |
| Lint file-path passes when exists | integration | `test_BC_056_install_doc.py::test_lint_file_path_passes_when_exists` | ✓ |
| Lint skips target-host paths (`/etc/…`, `~/…`, `roles/…`) | integration | `test_BC_056_install_doc.py::test_lint_skips_target_host_paths` | ✓ |
| Lint passes clean on shipped INSTALL.md | integration | `test_BC_056_install_doc.py::test_lint_passes_live_repo` | ✓ |
| CLI invocation exits 0 on shipped INSTALL.md | integration | `test_BC_056_install_doc.py::test_lint_cli_passes_live_repo` | ✓ |
| `verify-install-doc` in Makefile `.PHONY:` | integration | `test_BC_056_install_doc.py::test_verify_install_doc_is_in_phony` | ✓ |
| `make check` depends on `verify-install-doc` | integration | `test_BC_056_install_doc.py::test_verify_install_doc_is_in_check` | ✓ |
| CAP-T112 + CAP-B039 registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

Legend: ✓ covered · ❌ open.
