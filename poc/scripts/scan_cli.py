"""BC-055 — `make scan` with HTTP routing.

Non-localhost CLI config → POST /scan/trigger on the hub (which proxies
to the scout internally).
Localhost config + scout container up → POST localhost:8001/scan/trigger
directly (unchanged BC-037 behavior).
Else fail with a clear message.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

from poc.scripts.cli_config import load_cli_config
from poc.scripts.cli_http import friendly_http_error, http_post, is_localhost_url

SCOUT_CONTAINER = "beaconry-scout"


def _scout_running() -> bool:
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format={{.State.Running}}", SCOUT_CONTAINER],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def _trigger_local_scout() -> int:
    """Pre-BC-055 path: curl the scout directly on localhost:8001."""
    url = "http://127.0.0.1:8001/scan/trigger"
    print(f"==> triggering scan via POST {url}")
    req = urllib.request.Request(url, method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 — localhost
            body = resp.read().decode() or "{}"
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
        body = exc.read().decode()[:200]
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        print(f"✗ scout unreachable: {exc.__class__.__name__}", file=sys.stderr)
        return 3
    if code == 202:
        print(f"✓ scan scheduled — {body}")
        print("   tail scout logs to watch: docker logs -f beaconry-scout")
        return 0
    if code == 409:
        print(f"⚠️  scan already running — {body}")
        return 2
    if code == 503:
        print(f"✗ scout 503 — {body}")
        return 3
    print(f"✗ unexpected HTTP {code} — {body}")
    return 4


def _trigger_remote_hub(cfg) -> int:  # type: ignore[no-untyped-def]
    url = cfg.hub_url.rstrip("/") + "/scan/trigger"
    print(f"==> triggering scan via POST {url}")
    try:
        body = http_post(url, token=cfg.hub_token)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode()[:200]
        except Exception:  # noqa: BLE001
            detail = ""
        if exc.code == 409:
            print(f"⚠️  scan already running — {detail}")
            return 2
        if exc.code == 503:
            print(f"✗ hub/scout 503 — {detail}")
            return 3
        print(f"✗ hub returned {exc.code} — {detail}")
        return 4
    except Exception as exc:  # noqa: BLE001
        print("✗ " + friendly_http_error(url, exc), file=sys.stderr)
        return 3
    print(f"✓ scan scheduled — {json.dumps(body)}")
    return 0


def main() -> int:
    cfg = load_cli_config()
    if not is_localhost_url(cfg.hub_url):
        return _trigger_remote_hub(cfg)
    if not _scout_running():
        print(
            "✗ scout container not running — start the stack with 'make demo' first "
            "OR configure ~/.config/beaconry/cli.yml to point at a remote hub",
            file=sys.stderr,
        )
        return 1
    return _trigger_local_scout()


if __name__ == "__main__":
    sys.exit(main())
