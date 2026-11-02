"""BC-070 — unified `beaconry` CLI dispatcher.

Thin argparse router over the existing per-command scripts. Each
subcommand just calls the existing module's `main()` — no new
logic, no duplicated help text.

Install:
    pip install -e .

Then from anywhere:
    beaconry status
    beaconry findings --status open
    beaconry audit LAST=20
    beaconry scan
    beaconry set-status FINDING=<id> STATUS=snoozed
    beaconry soak
    beaconry version
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# BC-070: subcommand → (module_dotted_path, summary, usage block).
# Each entry supports `beaconry <sub> --help` without importing the
# delegate (delegates are env-var driven, no native argparse help).
_SUBCOMMANDS: dict[str, tuple[str, str, str]] = {
    "status": (
        "poc.scripts.status",
        "one-line health table for every beaconry service + FLEET",
        "Usage: beaconry status\n"
        "\n"
        "No flags. Reads ~/.config/beaconry/cli.yml for hub_url + hub_token.\n"
        "Prints a table of hub/scout state + FLEET rows (per-scout version,\n"
        "health, last-heartbeat, last-scan).",
    ),
    "findings": (
        "poc.scripts.findings",
        "read-only view of the findings DB (STATUS= / HOST= filters)",
        "Usage: beaconry findings [STATUS=<open|snoozed|accepted|fixed|regressed>] [HOST=<name>]\n"
        "\n"
        "Env-var filters (make-style). Examples:\n"
        "    beaconry findings STATUS=open\n"
        "    beaconry findings HOST=server2\n"
        "    beaconry findings STATUS=regressed HOST=server2",
    ),
    "audit": (
        "poc.scripts.audit",
        "operator view of the audit log (LAST= / SINCE= / EVENT= / CVE= / HOST= / FINDING=)",
        "Usage: beaconry audit [LAST=N] [SINCE=1h|30m|…] [UNTIL=…] [EVENT=a,b] [CVE=…] [HOST=…] [COUNT=1] [FOLLOW=1]\n"
        "\n"
        "Env-var filters (make-style). Examples:\n"
        "    beaconry audit LAST=20\n"
        "    beaconry audit SINCE=1h EVENT=notify_sent,notify_sent_failed\n"
        "    beaconry audit COUNT=1       # event-type summary\n"
        "    beaconry audit FOLLOW=1      # tail -f style",
    ),
    "scan": (
        "poc.scripts.scan_cli",
        "trigger an immediate scout scan (bypasses SCAN_INTERVAL)",
        "Usage: beaconry scan\n"
        "\n"
        "No flags. Routes to local compose if hub is on localhost, else HTTP\n"
        "to the configured remote hub. Response: 202 scheduled or 409 already-running.",
    ),
    "set-status": (
        "poc.scripts.set_status",
        "mutate finding status over HTTP (FINDING= / STATUS=)",
        "Usage: beaconry set-status FINDING=<id> STATUS=<snoozed|accepted|fixed|open>\n"
        "\n"
        "Always HTTP — no local-DB fallback for mutations. Examples:\n"
        "    beaconry set-status FINDING=f0b442fb STATUS=snoozed\n"
        "    beaconry set-status FINDING=f0b442fb STATUS=fixed",
    ),
    "soak": (
        "poc.scripts.soak",
        "audit-log health check over the last 24h (BC-069/BC-074/BC-075)",
        "Usage: beaconry soak\n"
        "\n"
        "Scans $DATA_DIR/audit.log for error events + threshold anomalies in the\n"
        "last SOAK_WINDOW_S seconds (default 86400 = 24h). Exit codes: 0 clean,\n"
        "1 errors or anomalies, 2 missing log.\n"
        "    SOAK_WINDOW_S=1800 beaconry soak   # 30-min window\n"
        "    DATA_DIR=/data beaconry soak        # alt audit log path",
    ),
    "metrics": (
        "poc.scripts.metrics",
        "beaconry usability + feature-usage metrics from audit.log (BC-075)",
        "Usage: beaconry metrics [usability|features|all] [--since 7d] [--json]\n"
        "\n"
        "Two lenses on the same data:\n"
        "  usability  — key user: 'is this tool worth it?' (action rate,\n"
        "               time-to-fix, regression rate, ignored rate)\n"
        "  features   — developer: 'which features earn their keep?' (digest\n"
        "               fires, heartbeat health, manual-fix uptake)\n"
        "  all        — both (default)\n"
        "\n"
        "    beaconry metrics usability --since 7d\n"
        "    beaconry metrics features --json\n"
        "    beaconry metrics all --since 24h",
    ),
}


def _print_version() -> int:
    # Resolve VERSION file (works from installed or editable layout).
    repo_root = Path(__file__).resolve().parents[2]
    try:
        version = (repo_root / "VERSION").read_text().strip()
    except OSError:
        version = "unknown"
    print(f"beaconry {version}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="beaconry",
        description="Beaconry — CVE scanning + remediation for self-hosted operators.",
        epilog=(
            "Each subcommand has its own --help:\n"
            "    beaconry findings --help\n"
            "Config lives in ~/.config/beaconry/cli.yml (hub_url + hub_token)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name, (_module, help_text, _usage) in _SUBCOMMANDS.items():
        sub.add_parser(name, help=help_text, add_help=False)
    sub.add_parser("version", help="print the installed beaconry version")
    return parser


def _print_subcommand_help(cmd: str) -> int:
    """BC-070: render the per-subcommand help from `_SUBCOMMANDS`. The
    delegates themselves don't use argparse (they're env-var driven),
    so the dispatcher owns their --help text."""
    _module, summary, usage = _SUBCOMMANDS[cmd]
    print(f"beaconry {cmd} — {summary}\n")
    print(usage)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    parser = _build_parser()

    # No arg → show help, exit 0 (friendlier than argparse's default).
    if not args:
        parser.print_help()
        return 0

    cmd = args[0]
    if cmd in ("-h", "--help"):
        parser.print_help()
        return 0
    if cmd == "version":
        return _print_version()
    if cmd not in _SUBCOMMANDS:
        parser.print_usage(sys.stderr)
        print(f"beaconry: unknown subcommand {cmd!r}", file=sys.stderr)
        print("Try `beaconry --help` for the list.", file=sys.stderr)
        return 2

    # Subcommand --help: dispatcher owns the text (delegates don't use argparse).
    rest = args[1:]
    if rest and rest[0] in ("-h", "--help"):
        return _print_subcommand_help(cmd)

    # Dispatch — delegate owns everything else. Rebuild sys.argv so the
    # delegate sees `script_name <its args>` the way it expects when run
    # via `python -m`.
    module_path = _SUBCOMMANDS[cmd][0]
    import importlib

    module = importlib.import_module(module_path)
    if not hasattr(module, "main"):
        print(f"beaconry: {module_path} has no main() entry point", file=sys.stderr)
        return 2

    # Preserve the delegate's own `sys.argv[0]` convention. Second element
    # onwards is whatever the operator typed after the subcommand.
    saved_argv = sys.argv
    try:
        sys.argv = [f"beaconry {cmd}", *rest]
        rc = module.main()
    finally:
        sys.argv = saved_argv
    return rc if isinstance(rc, int) else 0


if __name__ == "__main__":
    sys.exit(main())
