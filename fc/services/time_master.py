"""
Time Master + GPS Ingestion Service
====================================
Responsibilities
  1. Connect to the local gpsd daemon (reading /dev/ttyACMx).
  2. Maintain the latest GPS fix in memory.
  3. Publish ``uavcan.time.Synchronization`` at TIME_SYNC_HZ.
  4. Publish ``leos.gps.Fix`` at TIME_SYNC_HZ so the aggregator can embed it.

Run as:  python -m fc.services.time_master
"""

import asyncio
import time

import gpsd

from pycyphal.application import make_node, NodeInfo
from pycyphal.transport.can import CANTransport
from pycyphal.transport.can.media.socketcan import SocketCANMedia

# fc.__init__ ensures dsdl_out is on sys.path
import fc  # noqa: F401
import uavcan.time

from fc.config import (
    CAN_INTERFACE,
    CAN_MTU,
    GPS_FIX_DSDL,
    NODE_ID_TIME_MASTER,
    NODE_NAME_TIME_MASTER,
    TIME_SYNC_HZ,
    PORT_GPS_FIX,
    resolve_dsdl_class,
)


Fix_0_1 = resolve_dsdl_class(GPS_FIX_DSDL)


# ---------------------------------------------------------------------------
# GPS helpers
# ---------------------------------------------------------------------------

def _poll_gpsd() -> dict:
    """Poll gpsd and return a minimal fix dict."""
    try:
        packet = gpsd.get_current()
        if packet.mode >= 2:
            return {
                "fix_ok": True,
                "lat": getattr(packet, "lat", 0.0),
                "lon": getattr(packet, "lon", 0.0),
                "alt_m": getattr(packet, "alt", 0.0),
                "speed_mps": getattr(packet, "hspeed", 0.0),
                "track_deg": getattr(packet, "track", 0.0),
                "sats_used": getattr(packet, "sats_valid", 0),
                "sats_visible": getattr(packet, "sats", 0),
                "gps_utc": getattr(packet, "time", ""),
            }
    except Exception as exc:
        print(f"[time_master] GPS poll error: {exc}")
    return {"fix_ok": False}


# ---------------------------------------------------------------------------
# Main service loop
# ---------------------------------------------------------------------------

async def run() -> None:
    # ── Cyphal node ──────────────────────────────────────────────────────
    media = SocketCANMedia(CAN_INTERFACE, mtu=CAN_MTU)
    transport = CANTransport(media=media, local_node_id=NODE_ID_TIME_MASTER)
    node = make_node(
        info=NodeInfo(name=NODE_NAME_TIME_MASTER),
        transport=transport,
    )
    node.start()

    # Synchronization has fixed port 7168 — no port_id needed
    time_pub = node.make_publisher(uavcan.time.Synchronization_1_0)
    gps_pub = node.make_publisher(Fix_0_1, PORT_GPS_FIX)

    # ── Connect to gpsd ─────────────────────────────────────────────────
    print("[time_master] Connecting to gpsd …")
    gpsd.connect()
    print("[time_master] gpsd connected.")

    prev_tx_us: int = 0
    period = 1.0 / TIME_SYNC_HZ

    try:
        while True:
            # 1. Poll GPS (fast — returns cached fix)
            fix = _poll_gpsd()

            # 2. Publish time synchronization
            now_us = int(time.time() * 1_000_000)
            sync_msg = uavcan.time.Synchronization_1_0()
            sync_msg.previous_transmission_timestamp_microsecond = prev_tx_us
            await time_pub.publish(sync_msg)
            prev_tx_us = now_us

            # 3. Publish GPS fix
            gps_msg = Fix_0_1()
            gps_msg.fix_ok = fix.get("fix_ok", False)
            gps_msg.lat = fix.get("lat", 0.0)
            gps_msg.lon = fix.get("lon", 0.0)
            gps_msg.alt_m = fix.get("alt_m", 0.0)
            gps_msg.speed_mps = fix.get("speed_mps", 0.0)
            gps_msg.track_deg = fix.get("track_deg", 0.0)
            gps_msg.sats_used = fix.get("sats_used", 0)
            gps_msg.sats_visible = fix.get("sats_visible", 0)

            ts = uavcan.time.SynchronizedTimestamp_1_0()
            ts.microsecond = now_us
            gps_msg.gps_utc = ts

            await gps_pub.publish(gps_msg)

            await asyncio.sleep(period)
    finally:
        node.close()


if __name__ == "__main__":
    asyncio.run(run())
