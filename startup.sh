#!/bin/bash
set -e

cd /opt/leos-S26-flight-computer
source .venv/bin/activate
exec python -m fc.services.logger