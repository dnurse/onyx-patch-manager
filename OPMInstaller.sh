#!/usr/bin/env bash
# Alias for the interactive maintenance entrypoint.
# Keep the installer logic in OPMInstall.sh and OPMMaintenance.sh.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/OPMInstall.sh" "$@"
