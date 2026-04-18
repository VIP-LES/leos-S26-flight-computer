#!/bin/bash
set -euo pipefail

REPO="${REPO:-/home/leos-flight-computer/leos-S26-flight-computer}"
VENV_PYTHON="$REPO/.venv/bin/python"

cd "$REPO"

# Keep the generated packages importable even if the service environment is thin.
export PYTHONPATH="$REPO/dsdl_out:${PYTHONPATH:-}"

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "Missing virtualenv interpreter: $VENV_PYTHON" >&2
    exit 1
fi

exec "$VENV_PYTHON" -m fc.services.logger
