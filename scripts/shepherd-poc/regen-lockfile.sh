#!/bin/sh
# regen-lockfile.sh — apply the agent's manifest-only diff, then deterministically
# regenerate the lockfile by running the ecosystem's package manager. Writes the
# combined diff (manifest + lockfile) back to $DIFF_PATH.
#
# Required env:
#   DIFF_PATH   path to the agent's manifest-only diff (default /tmp/diff.patch)
#
# Exit codes:
#   0  combined diff written; $DIFF_PATH now contains manifest + lockfile changes
#   2  manifest diff did not apply; or regen produced no changes; or ecosystem unsupported
#   3  usage / tool missing

set -eu

DIFF_PATH="${DIFF_PATH:-/tmp/diff.patch}"
if [ ! -f "$DIFF_PATH" ]; then
  echo "regen-lockfile: no diff at $DIFF_PATH" >&2
  exit 3
fi

command -v git >/dev/null || { echo "git missing" >&2; exit 3; }

# Apply the manifest diff in the working tree (no commit yet). Try strict
# first, then --recount (LLM hunk line counts are routinely wrong), then
# --recount --3way, then --recount -p0 (some LLMs omit the `a/` / `b/`
# prefixes that git apply expects by default).
apply_tried() {
  if   git apply                             "$1" 2>"$2"; then echo "strict";       return 0
  elif git apply --recount                   "$1" 2>"$2"; then echo "recount";      return 0
  elif git apply --recount --3way            "$1" 2>"$2"; then echo "recount+3way"; return 0
  elif git apply --recount --3way -p0        "$1" 2>"$2"; then echo "recount+3way+p0"; return 0
  else                                                                               return 1
  fi
}

mode=$(apply_tried "$DIFF_PATH" /tmp/regen-apply.log) || {
  echo "regen-lockfile: agent's manifest diff did not apply in any mode:" >&2
  cat /tmp/regen-apply.log >&2
  echo "---- DIFF CONTENT ----" >&2
  cat "$DIFF_PATH" >&2
  echo "---- END DIFF ----" >&2
  exit 2
}
echo "→ agent diff applied in mode: $mode"

# Detect the ecosystem from what the agent touched. `git diff --name-only`
# against the un-staged working tree captures the manifest the agent edited.
touched=$(git diff --name-only -- . | sort -u)
echo "→ Touched manifests: $(printf '%s' "$touched" | tr '\n' ' ')"

ecosystem=""
case "$touched" in
  *package.json*)    ecosystem=npm ;;
  *pyproject.toml*|*requirements.txt*) ecosystem=pip ;;
  *go.mod*)          ecosystem=go ;;
  "")
    echo "regen-lockfile: agent diff touched nothing — the patch applied but was a no-op (lines already match target state?)" >&2
    echo "---- DIFF CONTENT ----" >&2
    cat "$DIFF_PATH" >&2
    echo "---- END DIFF ----" >&2
    exit 2
    ;;
  *)
    echo "regen-lockfile: ecosystem not yet supported for: $touched" >&2
    # Keep the manifest-only diff as-is — tolerable R0 behaviour.
    echo "→ Keeping manifest-only diff (no lockfile regen)."
    git diff -- . > "$DIFF_PATH.tmp" && mv "$DIFF_PATH.tmp" "$DIFF_PATH"
    exit 0
    ;;
esac

echo "→ Ecosystem: $ecosystem — regenerating lockfile"

case "$ecosystem" in
  npm)
    command -v npm >/dev/null || { echo "npm missing — install Node.js in the workflow" >&2; exit 3; }
    # --package-lock-only: don't install node_modules; only touch package-lock.json.
    # --ignore-scripts: refuse to run any package install-scripts (big R0 safety win).
    npm install --package-lock-only --ignore-scripts 2>/tmp/regen-npm.log || {
      echo "regen-lockfile: npm install --package-lock-only failed:" >&2
      cat /tmp/regen-npm.log >&2
      exit 2
    }
    ;;
  pip|go)
    echo "regen-lockfile: ecosystem $ecosystem not yet implemented (R1 scope)" >&2
    exit 2
    ;;
esac

# Capture the combined diff (manifest + lockfile) and replace the agent's
# manifest-only diff with it. validate.sh re-runs after this step.
combined_lines=$(git diff -- . | wc -l)
if [ "$combined_lines" = 0 ]; then
  echo "regen-lockfile: no changes after regen — nothing to open a PR for" >&2
  exit 2
fi

git diff -- . > "$DIFF_PATH.tmp" && mv "$DIFF_PATH.tmp" "$DIFF_PATH"
echo "→ Combined diff written: $DIFF_PATH ($combined_lines lines)"

# Reset the working tree so the later open-pr.sh can apply $DIFF_PATH onto a
# clean branch. Safe here: this is a CI runner, no human work exists.
git checkout -- .
echo "→ Working tree reset; combined diff preserved at $DIFF_PATH"
