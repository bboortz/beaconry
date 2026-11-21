#!/bin/sh
# validate.sh — gate a candidate diff against the R0 blocklist + size cap.
#
# Usage:  validate.sh <path-to-diff>
#
# Exit codes:
#   0  diff is acceptable
#   2  diff touches a protected path or exceeds 50 changed lines
#   3  usage / IO error

set -eu

if [ $# -ne 1 ]; then
  echo "usage: $0 <diff-path>" >&2
  exit 3
fi
DIFF=$1
if [ ! -f "$DIFF" ]; then
  echo "no such file: $DIFF" >&2
  exit 3
fi

MAX_LINES="${MAX_LINES:-50}"

# Protected paths (POSIX-ish globs → grep patterns). Any file path in the diff
# that matches one of these blocks the PR.
#
# Rule of thumb: these are the files whose change would let shepherd rewrite
# its own rules. Keep the list narrow and document additions in CHANGELOG.
PROTECTED_RE='^(\.github/workflows/.*|\.shepherd\.yml|scripts/release\.sh|VERSION|.*\.env|.*\.pem|.*\.key)$'

# ---- Scan file headers --------------------------------------------------
# Unified-diff file lines look like:
#   --- a/<path>
#   +++ b/<path>
#   diff --git a/<path> b/<path>
# Extract every <path>, dedupe, test each against the blocklist.
touched=$(awk '
  /^--- a\// { sub(/^--- a\//, ""); if ($0 != "/dev/null") print }
  /^\+\+\+ b\// { sub(/^\+\+\+ b\//, ""); if ($0 != "/dev/null") print }
  /^diff --git a\// {
    sub(/^diff --git a\//, "")
    sub(/ b\/.*$/, "")
    print
  }
' "$DIFF" | sort -u)

if [ -z "$touched" ]; then
  echo "validate: no file paths found in diff" >&2
  exit 2
fi

blocked=$(printf '%s\n' "$touched" | grep -E "$PROTECTED_RE" || true)
if [ -n "$blocked" ]; then
  echo "validate: diff touches protected path(s):" >&2
  printf '  %s\n' "$blocked" >&2
  exit 2
fi

# ---- Size cap -----------------------------------------------------------
# Count +/- lines, ignoring the +++/--- headers.
added=$(grep -c '^[+][^+]' "$DIFF" 2>/dev/null || echo 0)
removed=$(grep -c '^[-][^-]' "$DIFF" 2>/dev/null || echo 0)
added=${added:-0}
removed=${removed:-0}
total=$((added + removed))

if [ "$total" -gt "$MAX_LINES" ]; then
  echo "validate: diff is $total changed lines, cap is $MAX_LINES" >&2
  exit 2
fi

echo "validate: OK ($total changed lines across $(printf '%s' "$touched" | wc -l | tr -d ' ') file(s))"
