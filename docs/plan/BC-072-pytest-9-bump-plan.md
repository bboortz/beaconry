# BC-072 — pytest 8 → 9 (fixes CVE-2025-71176) + cascading pytest-asyncio

*Branch: `fix/BC-072-pytest-9-bump`*
*Start: 2026-11-02*

## Context

Dependabot opened PR #1 against `main` proposing `pytest 8.3.3 → 9.0.3`
in isolation. Not safe to merge as-is:

1. Install breaks: `pytest-asyncio 0.24.0` pins `pytest<9`.
   Resolver rejects the combo.
2. PR targets `main` (Dependabot default). Our workflow has `main`
   as release-only, dev is where features/fixes land. Merging
   would create drift (`main` with pytest 9, `dev` still on 8).

But the underlying bump is good: pytest 9.0.3 fixes
**CVE-2025-71176** (insecure temp dir). Dev-only CVE but still
worth shipping.

## Fix

Dependency bump on dev, bundled correctly:

- `pytest 8.3.3 → 9.0.3`
- `pytest-asyncio 0.24.0 → 1.4.1.post1` (unblocks the `pytest<9` pin)

Everything else in `requirements-dev.txt` stays. Verified
`make check-full` passes end-to-end (884 tests + 10/10 scenarios
+ 11/11 CLI smokes).

Close PR #1 after merge — Dependabot will re-check and open a
corrected PR targeting dev if needed. (A `dependabot.yml` config
with `target-branch: dev` would prevent future drift; deferring
that to a separate decision.)

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| `pip install -r requirements-dev.txt` resolves | manual | local pip install, exit 0 | ✓ |
| `make check` green on pytest 9 | multi-gate | logs | ✓ |
| `make check-full` green on pytest 9 | multi-gate | logs | ✓ |

## Version

0.3.5 → 0.3.6 (patch — dev dep bump only, no runtime change).
