"""
Central configuration for all flight-computer Cyphal services.

Generated DSDL classes are treated as the source of truth. Fixed port IDs are
read from the generated models so this module stays aligned with dsdl_out.
"""

import importlib
import os
import sys


_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DSDL_OUT = os.path.join(_REPO_ROOT, "dsdl_out")
if os.path.isdir(_DSDL_OUT) and _DSDL_OUT not in sys.path:
    sys.path.insert(0, _DSDL_OUT)


def resolve_dsdl_class(fully_qualified: str):
    module_path, class_name = fully_qualified.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def fixed_port_id(fully_qualified: str) -> int:
    model = resolve_dsdl_class(fully_qualified)._MODEL_
    port_id = getattr(model, "fixed_port_id", None)
    if port_id is None:
        raise ValueError(f"{fully_qualified} does not define a fixed port ID")
    return int(port_id)

# ── CAN bus ──────────────────────────────────────────────────────────────────
CAN_INTERFACE = "can0"
CAN_MTU = 64  # CAN FD

# ── Cyphal node identities ──────────────────────────────────────────────────
NODE_ID_TIME_MASTER = 10
NODE_ID_LOW_AGG     = 11
NODE_ID_LOGGER      = 12

NODE_NAME_TIME_MASTER = "leos.time_master"
NODE_NAME_LOW_AGG     = "leos.lowrate_agg"
NODE_NAME_LOGGER      = "leos.logger"

# ── Fixed rates (Hz) ────────────────────────────────────────────────────────
LOWRATE_AGG_HZ = 1
TIME_SYNC_HZ   = 2

# ── Staleness threshold (ms) ────────────────────────────────────────────────
STALE_MS = 2000

# ── DSDL type names ─────────────────────────────────────────────────────────
LOWRATE_DSDL = "leos.aggregate.LowRate_0_1"
GPS_FIX_DSDL = "leos.gps.Fix_0_1"
EFM_DSDL = "leos.efm.ADC_0_2"

# Aggregate inputs. Keep this aligned with leos.aggregate.LowRate.
SENSORS = [
    ("leos.sensors.BME688_0_1", "bme688"),
    ("leos.sensors.TSL2591_0_1", "tsl2591"),
    ("leos.sensors.LTR390_0_1", "ltr390"),
    ("leos.sensors.PMSA003I_0_1", "pmsa003i"),
]

# ── Fixed port IDs derived from generated DSDL ──────────────────────────────
PORT_LOW_AGG = fixed_port_id(LOWRATE_DSDL)
PORT_GPS_FIX = fixed_port_id(GPS_FIX_DSDL)
PORT_EFM = fixed_port_id(EFM_DSDL)
SENSOR_PORTS = [
    (fixed_port_id(dsdl_fqn), dsdl_fqn, field_name) for dsdl_fqn, field_name in SENSORS
]

# ── Logging ─────────────────────────────────────────────────────────────────
LOG_DIR = os.path.join(_REPO_ROOT, "logs")

# Record-kind tags written into the SQLite log
RECORD_KIND_LOW_AGG = 1
RECORD_KIND_EFM     = 2
RECORD_KIND_STATUS  = 3

# Log-writer tuning
LOG_BATCH_BYTES = 256 * 1024   # flush when queue reaches 256 KiB …
LOG_FLUSH_MS    = 200          # … or every 200 ms, whichever comes first
