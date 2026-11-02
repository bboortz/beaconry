---
id: BC-070
title: Operator CLI install — `pip install -e . → beaconry …`
status: shipped
created: 2026-11-02
branch: feat/BC-070-cli-install
plan: docs/plan/BC-070-cli-install-plan.md
---

# BC-070: Operator CLI install

Operator asked how to install the CLI locally. Pre-BC-070 the only
path was `cd beaconry && make status` from the repo root. BC-070
adds a `pip install -e .` entry point so `beaconry` is callable
from anywhere.

## What shipped

### `beaconry` command on $PATH

`pyproject.toml` declares `[project.scripts] beaconry =
"poc.scripts.cli:main"` + a setuptools build backend. After
`pip install -e .`:

```
beaconry status
beaconry findings STATUS=open
beaconry audit LAST=20
beaconry scan
beaconry set-status FINDING=<id> STATUS=snoozed
beaconry soak
beaconry version
```

### Dispatcher (`poc/scripts/cli.py`)

Thin argparse router over the existing per-command scripts.
Delegates are env-var driven (`FINDING=...`), so the dispatcher
owns per-subcommand `--help` text rather than delegating to each
script's non-existent argparse. Each entry in `_SUBCOMMANDS` has
module path + one-line summary + usage block with examples.

### Install docs

`docs/INSTALL.md § Operator CLI setup` now has two paths — "Option
A: pip install -e . + beaconry on $PATH (recommended)" and "Option
B: make from the repo (works today, no install step)". Both routes
share the same `~/.config/beaconry/cli.yml` config.

## Version

0.3.3 → 0.3.4 (patch — packaging only, no runtime behaviour change).
