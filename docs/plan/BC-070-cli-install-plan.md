# BC-070 — Operator CLI install (`pip install -e . → beaconry …`)

*Branch: `feat/BC-070-cli-install`*
*Start: 2026-11-02*

Make the operator CLI callable from anywhere, not just the repo root.
`pip install -e .` registers a `beaconry` command that dispatches to
every existing operator script.

## Scope

### 1. Dispatcher `poc/scripts/cli.py`

Thin `argparse` subparser router. Each subcommand just calls the
existing script's `main()`. No new logic.

Subcommands:

| `beaconry …` | Delegates to |
|---|---|
| `status` | `poc.scripts.status:main` |
| `findings [--status …] [--host …]` | `poc.scripts.findings:main` (passes argv) |
| `audit [--last …] [--since …] …` | `poc.scripts.audit:main` (passes argv) |
| `scan` | `poc.scripts.scan_cli:main` |
| `set-status --finding … --status …` | `poc.scripts.set_status:main` |
| `soak` | `poc.scripts.soak:main` |
| `version` | prints VERSION |

`beaconry --help` prints the subcommand list. Each subcommand gets
its own `--help` through the delegate's argparse.

### 2. `pyproject.toml` additions

```toml
[project]
# existing fields...
dependencies = [
    "pyyaml>=6.0",       # for CliConfig
    "requests>=2.31",    # for CLI HTTP (optional for pure-stdlib path)
]

[project.scripts]
beaconry = "poc.scripts.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
include = ["poc*"]
```

Pure-stdlib CLI already works — the `requests` + `pyyaml` deps are
only for the richer path. Keep both.

### 3. Install docs

Short section in `docs/INSTALL.md`:

```bash
pip install -e .                        # from the repo
beaconry status                         # confirm
```

Config stays in `~/.config/beaconry/cli.yml` — created by BC-055 docs
if the operator hasn't run `make status` yet.

### 4. Smoke test

A `test_BC_070_cli_install.py` verifies:

- `beaconry --help` lists expected subcommands
- `beaconry version` returns VERSION
- Each subcommand's `--help` succeeds
- Setup.py-style metadata is present

### Version

`0.3.3 → 0.3.4` (patch — packaging only, no runtime behaviour change).

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| `beaconry --help` lists subcommands | integration | `test_BC_070_cli_install.py::test_root_help_lists_subcommands` | ✓ |
| `beaconry` with no args prints help (exit 0) | integration | `test_BC_070_cli_install.py::test_no_args_prints_help` | ✓ |
| `beaconry version` prints VERSION | integration | `test_BC_070_cli_install.py::test_version_subcommand` | ✓ |
| Unknown subcommand → exit 2 + usage line | integration | `test_BC_070_cli_install.py::test_unknown_subcommand_fails` | ✓ |
| Dispatcher sets sys.argv[0] to `beaconry <sub>` for delegate | integration | `test_BC_070_cli_install.py::test_subcommand_dispatch_sets_argv_prefix` | ✓ |
| Dispatcher restores sys.argv after delegate returns | integration | `test_BC_070_cli_install.py::test_subcommand_dispatch_restores_argv` | ✓ |
| Delegate's exit code bubbles up | integration | `test_BC_070_cli_install.py::test_subcommand_dispatch_propagates_exit_code` | ✓ |
| Entry point `beaconry = poc.scripts.cli:main` in pyproject | integration | `test_BC_070_cli_install.py::test_pyproject_declares_entry_point` | ✓ |
| pyproject version matches VERSION file | integration | `test_BC_070_cli_install.py::test_pyproject_version_matches_version_file` | ✓ |
| pyproject declares pyyaml + requests runtime deps | integration | `test_BC_070_cli_install.py::test_pyproject_declares_runtime_dependencies` | ✓ |
| pyproject declares setuptools build backend | integration | `test_BC_070_cli_install.py::test_pyproject_build_system_present` | ✓ |
| Each delegate's `--help` renders dispatcher-owned text (parametrize: findings/audit/set-status) | integration | `test_BC_070_cli_install.py::test_subcommand_helps_work[findings]` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Breaking changes

None. Pure additive — `make <target>` keeps working.

## Rollout

- `make bump-patch` → 0.3.4
- Merge to dev
- Operator: `pip install -e /home/benni/git/beaconry` then `beaconry status`
