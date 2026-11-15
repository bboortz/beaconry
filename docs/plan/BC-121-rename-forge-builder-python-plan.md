# BC-121 — Track forge-builder-trixie → forge-builder-python-trixie rename

*Branch: `fix/BC-121-rename-forge-builder-python`*
*Start date: 2026-11-15*

---

## Situation

Forge's FG-056 renamed `ghcr.io/bboortz/forge-builder-trixie` →
`ghcr.io/bboortz/forge-builder-python-trixie` to make the builder
family naming self-descriptive (siblings: `forge-builder-go-trixie`,
`forge-builder-node-trixie`).

Beaconry's builder stages FROM this image. The old GHCR tag stays
on GHCR after the rename but stops receiving updates. Without this
PR, beaconry's daily builds freeze on the last pre-rename digest.

## Goals

1. Flip every beaconry reference from `forge-builder-trixie` →
   `forge-builder-python-trixie`.
2. Keep verify-pinned-versions lint passing against the new name.
3. Leave historical docs (CHANGELOG, shipped stories/plans) verbatim
   so the archaeology of the old name survives in context.

## Scope

- `poc/scout/Dockerfile` — 2 refs (FROM + diagnostic echo)
- `poc/hub/Dockerfile` — 5 refs (FROM + comments + diagnostic echo)
- `poc/telegram_mock/Dockerfile` — FROM + comments
- `poc/scripts/verify_pinned_versions.py` — docstring + regex
- `poc/tests/unit/test_BC_095_builder_from.py` — test expectation
- `poc/tests/unit/test_BC_100_telegram_mock_forge_base.py` — test expectation

## Non-goals

- Rewriting historical CHANGELOG / story / plan docs (they document
  decisions at the time — rewriting erases history).
- Changing the runtime image names (`python-scratch-trixie` stays).

## Design decisions

- Rename all live code refs, keep all historical docs as-is.
- No new env var or config knob needed — the GHCR path is literal.

## Breaking changes

### 1. API / return shape
None.

### 2. Data model
None.

### 3. Behavioral semantics
None visible to operators. Builds that previously pulled the old
tag will pull the new one; images are byte-identical today (Forge
published both from the same build). Operators notice nothing.

## Merge plan

1. All work on `fix/BC-121-rename-forge-builder-python`
2. `make check` passes on branch tip
3. PR to `dev`; ff the dev rename once Forge FG-056 published
4. Squash merge

## Rollout plan

Additive on the Forge side (new tag already published). On deploy
of a BC-121 beaconry image, the next `docker pull` resolves the new
tag — no operator action.

## Sibling

Forge PR #104 — publishes the renamed tag on the Forge side.

## Testing strategy

- Unit tests `test_BC_095_builder_from` + `test_BC_100_telegram_mock_forge_base`
  assert the exact FROM line.
- `verify_pinned_versions` lint matches the new GHCR regex.

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_095_builder_from.py poc/tests/unit/test_BC_100_telegram_mock_forge_base.py -v
```
