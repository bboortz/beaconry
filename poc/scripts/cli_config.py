"""BC-055 — operator-CLI config loader.

YAML at `~/.config/beaconry/cli.yml` (or `$BEACONRY_CLI_CONFIG`) carries
`hub_url` + `hub_token`. Env (`HUB_URL`, `HUB_TOKEN`) overrides YAML.
Empty env values are skipped so a shell default of `export HUB_URL=""`
doesn't clobber a real YAML config.

Missing file → defaults → operator keeps today's localhost behavior.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover — PyYAML ships with scout image + dev venv.
    yaml = None  # type: ignore[assignment]


class CliConfigError(Exception):
    """Raised when CLI config loading hits a hard error (bad YAML)."""


@dataclass(frozen=True)
class CliConfig:
    hub_url: str = "http://localhost:8000"
    hub_token: str = ""
    source_path: str = field(default="")


_ENV_MAP: dict[str, str] = {
    "HUB_URL": "hub_url",
    "HUB_TOKEN": "hub_token",
}


def _default_cli_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "beaconry" / "cli.yml"


def _env_overlay(env: Mapping[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for env_name, field_name in _ENV_MAP.items():
        value = env.get(env_name, "")
        if value == "":
            continue
        out[field_name] = value
    return out


def load_cli_config(
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> CliConfig:
    """Load CLI config with precedence: env > yaml > defaults."""
    env = env if env is not None else os.environ
    if path is None:
        explicit = env.get("BEACONRY_CLI_CONFIG", os.environ.get("BEACONRY_CLI_CONFIG", ""))
        yaml_path = Path(explicit) if explicit else _default_cli_config_path()
    else:
        yaml_path = Path(path)

    yaml_data: dict[str, Any] = {}
    if yaml_path.exists():
        if yaml is None:
            raise CliConfigError(
                "YAML config file present but PyYAML is not installed in this environment"
            )
        try:
            yaml_data = yaml.safe_load(yaml_path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise CliConfigError(f"invalid YAML in {yaml_path}: {exc}") from exc
        if not isinstance(yaml_data, dict):
            raise CliConfigError(
                f"invalid YAML in {yaml_path}: top-level must be a mapping, got "
                f"{type(yaml_data).__name__}"
            )

    merged = {**yaml_data, **_env_overlay(env)}
    known = {f.name for f in CliConfig.__dataclass_fields__.values()}
    merged = {k: v for k, v in merged.items() if k in known and k != "source_path"}
    return CliConfig(
        source_path=str(yaml_path) if yaml_path.exists() else "",
        **merged,
    )
