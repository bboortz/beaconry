# BC-057 — Multi-host polish (HOST filter + notification lead)

*Branch: `feat/BC-057-multi-host-polish`*
*Start: 2026-11-01*

## Scope

- `poc/scripts/findings.py` — argparse `--host` + plumb into
  `load_findings(host=None)` + the compose-container inline script via
  a new `##HOST_FILTER##` placeholder.
- `Makefile` — propagate `HOST=` to findings.
- Help: HOST=<label> under `make findings` + add `HOST` to the
  CLI_ONLY_ALLOWLIST if not already there (it is: already used by
  `make audit HOST=`).
- `poc/hub/hub.py` — `is_multi_host_inventory()` + wire into
  `format_notification`.

## Design

### Dynamic detection, not a flag

`is_multi_host_inventory()` runs `SELECT COUNT(DISTINCT host) FROM
findings` once per notification. When ≥2 → prepend `🖥️ <code>host</code>`.
Single-host operators get zero visual change; hub promotes the prefix
automatically on the second scout's first post.

### Lead-line placement

`🖥️ <code>host</code>` goes BEFORE the Tier-0 KEV header and the
Tier-1 INTERNET-FACING marker. "Where is this?" is the strongest
question in a multi-host context — operator may be looking at a mix
of laptop + server findings.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| load_findings(host=X) filters correctly | integration | `test_BC_057_multi_host.py::test_load_findings_filters_by_host` | ✓ |
| load_findings(host=X, status=Y) composes | integration | `test_BC_057_multi_host.py::test_load_findings_filters_compose` | ✓ |
| argparse `--host` passes through | integration | `test_BC_057_multi_host.py::test_argparse_accepts_host` | ✓ |
| is_multi_host_inventory False at 0 or 1 distinct hosts | integration | `test_BC_057_multi_host.py::test_is_multi_host_single_host` | ✓ |
| is_multi_host_inventory True at ≥2 distinct hosts | integration | `test_BC_057_multi_host.py::test_is_multi_host_multiple_hosts` | ✓ |
| Telegram prepends host line when multi-host | integration | `test_BC_057_multi_host.py::test_notification_prepends_host_multi_host` | ✓ |
| Telegram unchanged when single-host | integration | `test_BC_057_multi_host.py::test_notification_unchanged_single_host` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

Legend: ✓ covered · ❌ open.
