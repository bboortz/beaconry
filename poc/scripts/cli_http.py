"""BC-055 — shared HTTP helpers for the operator CLIs.

- `choose_source(cfg, compose_hub_running, force_local)` → `"http" |
  "local-compose" | "local-file"`.
- `http_get(url, token)` + `http_patch(url, body, token)` +
  `http_post(url, body, token)` wrap `urllib.request` with
  `Authorization: Bearer <token>` + JSON parse.

Localhost detection: `localhost`, `127.0.0.1`, `::1`, and empty URL are
all loopback → prefer the local docker-exec path. Anything else → HTTP.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Literal

from poc.scripts.cli_config import CliConfig

Source = Literal["http", "local-compose", "local-file"]

_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_localhost_url(url: str) -> bool:
    """Return True iff `url`'s host is a loopback alias."""
    if not url:
        return True
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return True
    host = (parsed.hostname or "").lower()
    return host in _LOCALHOST_HOSTS or host == ""


def choose_source(
    cfg: CliConfig,
    *,
    compose_hub_running: bool,
    force_local: bool = False,
) -> Source:
    """BC-055 routing: remote hub → HTTP; local compose → docker-exec;
    else local DB file."""
    if force_local:
        return "local-file"
    if not is_localhost_url(cfg.hub_url):
        return "http"
    if compose_hub_running:
        return "local-compose"
    return "local-file"


def _build_request(
    url: str, token: str, method: str, body: bytes | None = None
) -> urllib.request.Request:
    req = urllib.request.Request(url, method=method, data=body)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    return req


def http_get(url: str, token: str = "", timeout: float = 10.0) -> list[dict] | dict:
    """GET a JSON payload. Returns parsed JSON (list or dict)."""
    req = _build_request(url, token, "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — operator-configured URL
        return json.loads(resp.read().decode())


def http_post(url: str, body: dict | None = None, token: str = "", timeout: float = 10.0) -> dict:
    """POST JSON (empty body OK). Returns parsed JSON dict."""
    payload = json.dumps(body or {}).encode()
    req = _build_request(url, token, "POST", body=payload)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — operator-configured URL
        parsed = json.loads(resp.read().decode())
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def http_patch(url: str, body: dict, token: str = "", timeout: float = 10.0) -> dict:
    """PATCH JSON. Returns parsed JSON dict."""
    payload = json.dumps(body).encode()
    req = _build_request(url, token, "PATCH", body=payload)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — operator-configured URL
        parsed = json.loads(resp.read().decode())
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def friendly_http_error(url: str, exc: Exception) -> str:
    """Human-readable error line for CLI output. Masks internal exception shapes."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode()[:200]
        except Exception:  # noqa: BLE001
            body = ""
        return f"hub returned {exc.code} for {url}: {body}"
    if isinstance(exc, (urllib.error.URLError, ConnectionError, TimeoutError, OSError)):
        return f"failed to reach hub at {url}: {exc.__class__.__name__}"
    return f"unexpected error against {url}: {exc.__class__.__name__}: {exc}"


__all__ = [
    "CliConfig",
    "Source",
    "choose_source",
    "friendly_http_error",
    "http_get",
    "http_patch",
    "http_post",
    "is_localhost_url",
]


# Avoid unused-import complaint from ruff — Any is used in type hints
# available via module-level `from typing`.
_ = Any
