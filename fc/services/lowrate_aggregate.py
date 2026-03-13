"""
Low-Rate Sensor Aggregation Service
=====================================
Responsibilities
  1. Subscribe to the four low-rate sensor subjects + GPS fix.
  2. Cache the latest message for each (sample-and-hold).
  3. Every 1 s, build and publish a ``leos.aggregate.LowRate`` message
     with validity flags reflecting staleness.

Run as:  python -m fc.services.lowrate_aggregate
"""

import asyncio
import time

from pycyphal.application import make_node, NodeInfo
from pycyphal.transport.can import CANTransport
from pycyphal.transport.can.media.socketcan import SocketCANMedia

# fc.__init__ ensures dsdl_out is on sys.path
import fc  # noqa: F401
import uavcan.time

from fc.config import (
    CAN_INTERFACE,
    CAN_MTU,
    NODE_ID_LOW_AGG,
    NODE_NAME_LOW_AGG,
    LOWRATE_AGG_HZ,
    STALE_MS,
    PORT_LOW_AGG,
    PORT_GPS_FIX,
    GPS_FIX_DSDL,
    LOWRATE_DSDL,
    SENSOR_PORTS,
    resolve_dsdl_class,
)


Fix_0_1 = resolve_dsdl_class(GPS_FIX_DSDL)
LowRate_0_1 = resolve_dsdl_class(LOWRATE_DSDL)


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


# ---------------------------------------------------------------------------
# Main service loop
# ---------------------------------------------------------------------------

async def run() -> None:
    # ── Cyphal node ──────────────────────────────────────────────────────
    media = SocketCANMedia(CAN_INTERFACE, mtu=CAN_MTU)
    transport = CANTransport(media=media, local_node_id=NODE_ID_LOW_AGG)
    node = make_node(
        info=NodeInfo(name=NODE_NAME_LOW_AGG),
        transport=transport,
    )
    node.start()

    agg_pub = node.make_publisher(LowRate_0_1, PORT_LOW_AGG)

    # ── Per-sensor state: {field_name: {"msg": <latest>, "rx_ms": int}} ──
    latest: dict[str, dict] = {}

    # ── Create one subscriber per sensor ─────────────────────────────────
    async def _sensor_callback(field_name: str, sub):
        """Receive loop for a single sensor subject."""
        async for msg, _transfer in sub:
            latest[field_name] = {"msg": msg, "rx_ms": _now_ms()}

    tasks: list[asyncio.Task] = []
    for port_id, dsdl_fqn, field_name in SENSOR_PORTS:
        cls = resolve_dsdl_class(dsdl_fqn)
        sub = node.make_subscriber(cls, port_id)
        tasks.append(asyncio.create_task(_sensor_callback(field_name, sub)))

    # ── GPS fix subscriber ───────────────────────────────────────────────
    gps_sub = node.make_subscriber(Fix_0_1, PORT_GPS_FIX)

    async def _gps_callback():
        async for msg, _transfer in gps_sub:
            latest["gps_data"] = {"msg": msg, "rx_ms": _now_ms()}

    tasks.append(asyncio.create_task(_gps_callback()))

    # ── Aggregation loop at LOWRATE_AGG_HZ ───────────────────────────────
    period = 1.0 / LOWRATE_AGG_HZ

    async def _aggregation_loop():
        while True:
            now_ms = _now_ms()

            agg = LowRate_0_1()

            # Packet timestamp (UTC microseconds)
            ts = uavcan.time.SynchronizedTimestamp_1_0()
            ts.microsecond = int(time.time() * 1_000_000)
            agg.t_pkt = ts

            # Fill each sensor field + validity flag
            for _port_id, _dsdl_fqn, field_name in SENSOR_PORTS:
                entry = latest.get(field_name)
                if entry is not None and (now_ms - entry["rx_ms"]) < STALE_MS:
                    setattr(agg, field_name, entry["msg"])
                    setattr(agg, f"{field_name}_valid", True)
                else:
                    setattr(agg, f"{field_name}_valid", False)

            # GPS fix (no separate *_valid flag — fix_ok is inside the message)
            gps_entry = latest.get("gps_data")
            if gps_entry is not None and (now_ms - gps_entry["rx_ms"]) < STALE_MS:
                agg.gps_data = gps_entry["msg"]
            # else: gps_data stays at default (fix_ok=False)

            await agg_pub.publish(agg)
            await asyncio.sleep(period)

    tasks.append(asyncio.create_task(_aggregation_loop()))

    try:
        await asyncio.gather(*tasks)
    finally:
        node.close()


if __name__ == "__main__":
    asyncio.run(run())
