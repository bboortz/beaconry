# BC-058 — Mutation + audit cleanup

*Branch: `feat/BC-058-mutation-and-audit-cleanup`*
*Start: 2026-11-01*

## Scope

### Fix 1 — `set_status` rowcount check (`poc/hub/hub.py`)

```python
class FindingNotFound(Exception):
    """Raised by set_status when the finding_id doesn't exist in the DB."""

def set_status(finding_id: str, status: str, *, source: str = "telegram") -> None:
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT host, cve_id, status FROM findings WHERE id = ?", (finding_id,)
    ).fetchone()
    if row is None:
        con.close()
        raise FindingNotFound(finding_id)
    host, cve_id, old_status = row
    # … UPDATE as today …
    _audit.append(
        "status_change",
        finding_id=finding_id,
        host=host,
        cve_id=cve_id,
        old_status=old_status,
        new_status=status,
        source=source,
    )
```

Callers in Telegram handlers (`_handle_fix`, `_handle_snooze`,
`_handle_ack`, `_handle_verify`) now pass `source="telegram"` and are
protected from the FindingNotFound raise by their preceding
`load_finding` check.

### Fix 2 — Audit shape consistency

Already covered by Fix 1 (same `_audit.append` call shape as BC-055's
PATCH handler). The two paths now emit the same payload — only `source`
differs (`"telegram"` vs. `"http"`).

### Fix 3 — `notify_sent` + `notify_digest_sent` audit events

Add `_audit.append` calls after each `_dispatch_*` succeeds in `notify`,
`notify_regression`, `notify_heartbeat`, and the digest send site.

```python
async def notify(finding: Finding) -> None:
    if MOCK and not _notifiers_pkg.enabled_adapters():
        _mock_print("Notification", format_notification(finding))
        return
    await _dispatch_finding(finding)
    _audit.append(
        "notify_sent",
        finding_id=finding.id,
        cve_id=finding.cve_id,
        host=finding.host,
        kind="finding",
    )
```

Digest site emits a single `notify_digest_sent` carrying the
`finding_ids` list of suppressed items.

### Fix 4 — Friendlier Telegram "Finding not found" error

```python
if not finding:
    short_id = finding_id[:8]
    action = parts[0]
    await query.edit_message_text(
        f"⚠️ {action.capitalize()} failed — finding "
        f"<code>{short_id}…</code> is not in the current DB. "
        f"Likely a stale notification from before a scan reset. "
        f"Safe to ignore.",
        parse_mode="HTML",
    )
    return
```

### Fix 5 — Memoize `is_multi_host_inventory` per batch

`format_notification` gains an optional `multi_host: bool | None`
kwarg. When `None`, falls back to the DB lookup (back-compat).
Dispatchers that render multiple notifications in a loop (digest,
sweep) compute it once up-front.

```python
def format_notification(finding: Finding, multi_host: bool | None = None) -> str:
    is_multi = multi_host if multi_host is not None else is_multi_host_inventory()
    if is_multi:
        lines.append(f"🖥️ <code>{esc(finding.host)}</code>")
```

## Design decisions

### Why raise instead of return bool

`FindingNotFound` surfaces as a clear exception; callers who already
checked `load_finding` can `# noqa` or wrap. Returning bool invites the
"forgot to check" footgun this story is specifically closing.

### Why `source` on the audit event

Operator can grep `make audit CVE=X` and see "transitioned via
telegram at T1, then via http at T2" — the audit log becomes a
reproducible history including the mutation path.

### Why friendly error + hint vs. CVE id in callback_data

Callback_data is capped at 64 bytes; shoehorning the CVE id in means
truncating something else. Follow-up BC can reshape callback_data to
`{action}:{id}:{cve}:{snooze_hours}` with length guards. For now, the
8-char id prefix + hint is sufficient diagnosis for the operator.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| set_status raises FindingNotFound on unknown id | integration | `test_BC_058_cleanup.py::test_set_status_raises_on_unknown_id` | ✓ |
| set_status emits rich audit shape | integration | `test_BC_058_cleanup.py::test_set_status_emits_rich_audit` | ✓ |
| set_status audit `source="telegram"` by default | integration | `test_BC_058_cleanup.py::test_set_status_source_telegram_default` | ✓ |
| set_status unknown-id writes NO audit event | integration | `test_BC_058_cleanup.py::test_set_status_unknown_id_no_audit` | ✓ |
| set_status 'fixed' still sets last_fixed_ts (BC-031 preserved) | integration | `test_BC_058_cleanup.py::test_set_status_fixed_sets_last_fixed_ts` | ✓ |
| notify emits notify_sent audit event | integration | `test_BC_058_cleanup.py::test_notify_emits_notify_sent` | ✓ |
| notify_regression emits notify_sent with kind=regression | integration | `test_BC_058_cleanup.py::test_notify_regression_emits_notify_sent` | ✓ |
| notify_heartbeat emits notify_sent with kind=heartbeat | integration | `test_BC_058_cleanup.py::test_notify_heartbeat_emits_notify_sent` | ✓ |
| digest emits notify_digest_sent with finding_ids list | integration | `test_BC_058_cleanup.py::test_digest_emits_notify_digest_sent` | ✓ |
| Telegram "Finding not found" error includes id prefix | integration | `test_BC_058_cleanup.py::test_telegram_not_found_includes_id_prefix` | ✓ |
| Telegram "Finding not found" error includes stale-notification hint | integration | `test_BC_058_cleanup.py::test_telegram_not_found_includes_stale_hint` | ✓ |
| format_notification multi_host hint bypasses DB lookup | integration | `test_BC_058_cleanup.py::test_format_notification_multi_host_hint` | ✓ |
| format_notification multi_host=None falls back to DB | integration | `test_BC_058_cleanup.py::test_format_notification_multi_host_fallback` | ✓ |
| format_notification single-host inventory → no prefix (fallback branch) | integration | `test_BC_058_cleanup.py::test_format_notification_single_host_fallback` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

Legend: ✓ covered · ❌ open.
