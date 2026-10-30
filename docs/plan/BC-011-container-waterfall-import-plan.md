# BC-011 close-gap — container waterfall import

*Branch: `fix/BC-011-container-waterfall-import`*
*Start date: 2026-10-30*

## Situation

Live tap session on BC-011 caught a `ModuleNotFoundError: No module named 'poc'` the first time an operator tapped the 🔍 Verify service health button. Root cause: `on_button_click`'s verify branch had an inline `from poc.hub.waterfall import run_probe`. That works on host tests (where `poc` is importable as a package) but fails inside the hub container, which has `/app/waterfall.py` COPY'd flat with no `poc` package parent.

Hub.py already uses the dual-mode import pattern for `_waterfall` at the module top:

```python
try:
    import waterfall as _waterfall  # container path
except ImportError:
    from poc.hub import waterfall as _waterfall  # host test path
```

The verify branch should have used that binding instead of a fresh namespaced import. Fix is one line + a reminder comment.

## Problem to solve

Verify button taps crashed with `ModuleNotFoundError` on the first live run; unit tests passed because they ran in the host environment where `poc` is importable.

## Goal

Use the already-imported `_waterfall` module reference in the verify branch so the handler works in both container and host contexts.

## Scope

- `poc/hub/hub.py` — one-line change in the verify action branch, from `from poc.hub.waterfall import run_probe; result = await run_probe(probe)` → `result = await _waterfall.run_probe(probe)`. Comment added inline referencing the dual-mode import at the top.
- No test changes required — the handler already monkeypatches `poc.hub.waterfall.run_probe` at the module level (which is where `_waterfall` points on the host), so the existing BC-011 handler tests still cover both branches.

## Non-goals

- Changing the dual-mode import pattern itself
- Refactoring other inline imports in `on_button_click`
- Any new tests — fix is behavior-preserving on hosts, correctness-restoring in containers

## Design decisions

**Use the module-level `_waterfall` binding, not re-import locally.** Rejected: duplicate the try/except at point-of-use. Reason: duplication is noise; module-level binding is idiomatic and already works.

## Breaking changes

None operator-visible. Pre-fix, the verify branch would always crash in the container. Post-fix, it works. Nothing was working to break.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Verify branch runs inside the container | live manual | 2026-10-30 — all 4 BC-011 terminal states verified end-to-end on real Telegram against the Compose stack (✅ Service healthy, ℹ️ Already fixed_verified, ⚠️ Probe failed, ⏭️ No probe configured) | ✓ |
| Existing BC-011 handler tests still pass | unit test | `test_hub_helpers.py` 37 tests green | ✓ |
| Memory drift pattern added if applicable | N/A | N/A — close-gap only, no new convention | ✓ |

## Testing commands

```bash
source .venv/bin/activate
make check
```
