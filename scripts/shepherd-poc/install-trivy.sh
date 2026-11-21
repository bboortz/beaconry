#!/bin/sh
# install-trivy.sh — download Trivy and verify its sigstore bundle.
#
# No aquasecurity/trivy-action wrapper. See GHSA-69fq-xp46-6x23 / CVE-2026-33634
# (TeamPCP, March 2026) for why: the wrapper's mutable tags were force-pushed to
# malware. v0.69.3 is the first binary release protected by GitHub immutable
# releases, published before the compromise window — this makes it the
# advisory's recommended known-safe version.
#
# Flags:
#   --dry-run    Print the cosign command that would be run, then exit 0.
#
# Exit codes: 0 ok, 1 install failure, 2 verification failure, 3 usage.

set -eu

TRIVY_VERSION="${TRIVY_VERSION:-0.69.3}"
PLATFORM="Linux-64bit"
ARCHIVE="trivy_${TRIVY_VERSION}_${PLATFORM}.tar.gz"
BUNDLE="${ARCHIVE}.sigstore.json"
BASE="https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}"
INSTALL_PREFIX="${INSTALL_PREFIX:-/usr/local/bin}"
WORK="${WORK:-$(mktemp -d)}"
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      echo "usage: $0 [--dry-run]"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 3 ;;
  esac
  shift
done

COSIGN_CMD='cosign verify-blob \
  --new-bundle-format \
  --certificate-identity-regexp "https://github\\.com/aquasecurity/" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  --bundle "'"$WORK"'/'"$BUNDLE"'" \
  "'"$WORK"'/'"$ARCHIVE"'"'

if [ "$DRY_RUN" = 1 ]; then
  echo "DRY RUN"
  echo "would download: $BASE/$ARCHIVE"
  echo "would download: $BASE/$BUNDLE"
  echo "would run:"
  echo "  $COSIGN_CMD"
  exit 0
fi

echo "→ Installing Trivy v$TRIVY_VERSION into $INSTALL_PREFIX"
command -v curl >/dev/null || { echo "curl missing" >&2; exit 1; }
command -v cosign >/dev/null || {
  echo "→ Installing cosign v2.5.3 (fallback — ubuntu-latest preinstalls it since late 2024)"
  curl -sSLo /tmp/cosign https://github.com/sigstore/cosign/releases/download/v2.5.3/cosign-linux-amd64
  chmod +x /tmp/cosign
  export PATH="/tmp:$PATH"
}

cd "$WORK"
curl -sSLO "$BASE/$ARCHIVE"
curl -sSLO "$BASE/$BUNDLE"

echo "→ Verifying sigstore bundle (new-bundle-format: .sigstore.json → sigstore bundle v0.3)"
cosign verify-blob \
  --new-bundle-format \
  --certificate-identity-regexp 'https://github\.com/aquasecurity/' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  --bundle "$BUNDLE" \
  "$ARCHIVE" || { echo "cosign verification failed" >&2; exit 2; }

tar -xzf "$ARCHIVE"
install -m 0755 trivy "$INSTALL_PREFIX/trivy"

echo "→ Installed: $(trivy --version | head -1)"
