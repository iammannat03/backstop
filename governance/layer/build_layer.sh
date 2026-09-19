#!/usr/bin/env bash
# Fetches the Linux/amd64 opa binary into layer/bin/ for a real Lambda
# layer build. Not run automatically by anything, invoke by hand when
# preparing a deployment.
#
# The bin/opa checked into this directory during local development is
# whatever the developer's machine produced (commonly a macOS Mach-O
# binary), which will not execute inside the Lambda runtime's Linux
# container. Lambda layer contents are mounted at /opt, so a working
# layer needs a Linux ELF binary at bin/opa before it is packaged.
set -euo pipefail

OPA_VERSION="${OPA_VERSION:-v0.68.0}"
LAYER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCH="${1:-amd64}"

case "$ARCH" in
  amd64|x86_64)
    ASSET="opa_linux_amd64_static"
    ;;
  arm64|aarch64)
    ASSET="opa_linux_arm64_static"
    ;;
  *)
    echo "Unknown architecture: $ARCH (expected amd64 or arm64)" >&2
    exit 1
    ;;
esac

mkdir -p "$LAYER_DIR/bin"
curl -L -o "$LAYER_DIR/bin/opa" \
  "https://github.com/open-policy-agent/opa/releases/download/${OPA_VERSION}/${ASSET}"
chmod +x "$LAYER_DIR/bin/opa"

echo "Wrote Linux ${ARCH} opa ${OPA_VERSION} to $LAYER_DIR/bin/opa"
