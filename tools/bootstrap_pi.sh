#!/bin/bash
set -euo pipefail

REPO="${REPO:-/home/leos-flight-computer/leos-S26-flight-computer}"
VENV="$REPO/.venv"
PYTHON_BIN="$VENV/bin/python"
PIP_BIN="$VENV/bin/pip"
SERVICE_DIR="/etc/systemd/system"
SERVICES=(
    fc-time-master.service
    fc-lowrate-aggregate.service
    fc-logger.service
)

if [[ ! -d "$REPO" ]]; then
    echo "Repository not found at $REPO" >&2
    exit 1
fi

if [[ "$(id -un)" != "leos-flight-computer" ]]; then
    echo "Run this script as leos-flight-computer so the venv and repo stay owned correctly." >&2
    exit 1
fi

cd "$REPO"

python3 -m venv "$VENV"
"$PIP_BIN" install --upgrade pip
"$PIP_BIN" install -r "$REPO/requirements.txt"

for service in "${SERVICES[@]}"; do
    sudo cp "$REPO/systemd/$service" "$SERVICE_DIR/$service"
done

sudo systemctl daemon-reload
sudo systemctl enable fc-time-master fc-lowrate-aggregate fc-logger
sudo systemctl restart fc-time-master fc-lowrate-aggregate fc-logger
sudo systemctl status fc-time-master fc-lowrate-aggregate fc-logger --no-pager -l
