#!/usr/bin/env bash
# Compatibility entry point: the root installer deploys the complete current bridge.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/../install.sh" "$@"
