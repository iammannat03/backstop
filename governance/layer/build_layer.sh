#!/usr/bin/env bash
# Fetches the Linux opa binary into layer/bin/ for the Lambda layer. Not run
# automatically by anything, invoke it by hand before `sam build`.
#
# Usage: bash governance/layer/build_layer.sh [arm64|amd64]
#
# The architecture must match Globals.Function.Architectures in template.yaml
# (arm64 today), and the version must be recent enough to parse the policies,
# which use the current Rego syntax. Both defaults are set accordingly. Override
# the version with OPA_VERSION=vX.Y.Z if needed.
#
# A bin/opa left over from local development is usually a macOS binary, which
# cannot run in the Lambda Linux runtime, so it is always replaced. Lambda
# mounts layer contents at /opt, so the file ends up at /opt/bin/opa.
set -euo pipefail

OPA_VERSION="${OPA_VERSION:-v1.20.2}"
LAYER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCH="${1:-arm64}"

case "$ARCH" in
  amd64|x86_64)
    ASSET="opa_linux_amd64_static"
    ;;
  arm64|aarch64)
    ASSET="opa_linux_arm64_static"
    ;;
  *)
    echo "Unknown architecture: $ARCH (expected arm64 or amd64)" >&2
    exit 1
    ;;
esac

mkdir -p "$LAYER_DIR/bin"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# -f makes an HTTP error (for example a wrong version number) fail the script
# instead of saving the error page as the binary.
curl -fL --retry 3 -o "$TMP" \
  "https://github.com/open-policy-agent/opa/releases/download/${OPA_VERSION}/${ASSET}"

if [ "$(head -c 4 "$TMP" | tail -c 3)" != "ELF" ]; then
  echo "Downloaded file is not a Linux binary, aborting." >&2
  exit 1
fi

# The previous binary may be read-only, so remove it before replacing it.
rm -f "$LAYER_DIR/bin/opa"
mv "$TMP" "$LAYER_DIR/bin/opa"
chmod +x "$LAYER_DIR/bin/opa"

echo "Wrote Linux ${ARCH} opa ${OPA_VERSION} to $LAYER_DIR/bin/opa"
