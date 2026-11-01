"""BC-055 — `make set-status FINDING=<id> STATUS=<status>`.

Mutates a finding's status over HTTP by PATCHing the hub's
`/findings/{finding_id}` endpoint. Always goes through HTTP — there's
no local-DB fallback for mutating actions (operator could just open
the SQLite file themselves if that's what they wanted).

Usage:
    make set-status FINDING=<finding_id> STATUS=snoozed|accepted|fixed|open
"""

from __future__ import annotations

import os
import sys

from poc.scripts.cli_config import load_cli_config
from poc.scripts.cli_http import friendly_http_error, http_patch

_ALLOWED_STATUSES: frozenset[str] = frozenset({"snoozed", "accepted", "fixed", "open"})


def main() -> int:
    finding = os.environ.get("FINDING", "").strip()
    status = os.environ.get("STATUS", "").strip()

    if not finding:
        print("✗ FINDING=<id> is required", file=sys.stderr)
        return 2
    if not status:
        print(
            f"✗ STATUS=<one of {sorted(_ALLOWED_STATUSES)}> is required",
            file=sys.stderr,
        )
        return 2
    if status not in _ALLOWED_STATUSES:
        print(
            f"✗ invalid STATUS={status!r}; must be one of {sorted(_ALLOWED_STATUSES)}",
            file=sys.stderr,
        )
        return 2

    cfg = load_cli_config()
    url = cfg.hub_url.rstrip("/") + f"/findings/{finding}"
    try:
        body = http_patch(url, body={"status": status}, token=cfg.hub_token)
    except Exception as exc:  # noqa: BLE001 — single friendly line.
        print("✗ " + friendly_http_error(url, exc), file=sys.stderr)
        return 2
    old = body.get("old_status", "?")
    new = body.get("status", status)
    print(f"✓ {finding}: {old} → {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
