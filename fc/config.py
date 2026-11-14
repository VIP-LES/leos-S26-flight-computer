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
NODE_ID_CUTDOWN     = 13
NODE_ID_SENSOR      = 21
NODE_ID_POWER       = 22

NODE_NAME_TIME_MASTER = "leos.time_master"
NODE_NAME_LOW_AGG     = "leos.lowrate_agg"
NODE_NAME_LOGGER      = "leos.logger"
NODE_NAME_CUTDOWN     = "leos.cutdown"

# ── Fixed rates (Hz) ────────────────────────────────────────────────────────
LOWRATE_AGG_HZ = 1
TIME_SYNC_HZ   = 2

# ── Staleness threshold (ms) ────────────────────────────────────────────────
STALE_MS = 2000

# ── DSDL type names ─────────────────────────────────────────────────────────
LOWRATE_DSDL = "leos.aggregate.LowRate_0_1"
GPS_FIX_DSDL = "leos.gps.Fix_0_1"
EFM_DSDL = "leos.efm.EFM_0_2"
GEIGER_COUNTER_DSDL = "leos.sensors.GeigerCounter_0_1"
RADIO_CUTDOWN_DSDL = "leos.cutdown.RadioCutdown_0_1"
RELAY_DSDL = "leos.relays.Relays_1_0"
VARAIBLE_VOLTAGE_DSDL = "leos.power.VariableVoltage_0_1"

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
PORT_GEIGER_COUNTER = fixed_port_id(GEIGER_COUNTER_DSDL)
PORT_RADIO_CUTDOWN = fixed_port_id(RADIO_CUTDOWN_DSDL)
PORT_RELAY = fixed_port_id(RELAY_DSDL)
PORT_VARIABLE_VOLTAGE = fixed_port_id(VARAIBLE_VOLTAGE_DSDL)
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
MAX_QUEUE_RECORDS   = 1000
LOG_BATCH_SIZE      = 100
LOG_FLUSH_MS        = LOG_BATCH_SIZE * LOWRATE_AGG_HZ * 1000
