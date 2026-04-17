#!/bin/bash
set -e

REPO="/home/leos-flight-computer/leos-S26-flight-computer"

cd "$REPO"
source "$REPO/.venv/bin/activate"

# Only needed if DSDL imports ever fail
export PYTHONPATH="$REPO/dsdl_out:$PYTHONPATH"

exec python -m fc.services.logger