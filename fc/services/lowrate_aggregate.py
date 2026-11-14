"""
Low-Rate Sensor Aggregation Service
=====================================
Responsibilities
  1. Subscribe to the four low-rate sensor subjects + GPS fix.
  2. Cache the latest message for each (sample-and-hold).
  3. Every 1 s, build and publish a ``leos.aggregate.LowRate`` message.
  4. Apply freshness gating locally by only copying recently received
     sensor/GPS messages into the aggregate.

Run as:  python -m fc.services.lowrate_aggregate
"""

import asyncio
import time

from pycyphal.application import make_node, NodeInfo
from pycyphal.transport.can import CANTransport
from pycyphal.transport.can.media.socketcan import SocketCANMedia
from leos.efm import HallEvent_0_1, SampleBatch_0_1
from collections import deque

# fc.__init__ ensures dsdl_out is on sys.path
import fc  # noqa: F401

from fc.config import (
    CAN_INTERFACE,
    CAN_MTU,
    EFM_DSDL,
    NODE_ID_LOW_AGG,
    NODE_NAME_LOW_AGG,
    LOWRATE_AGG_HZ,
    STALE_MS,
    PORT_EFM,
    PORT_LOW_AGG,
    PORT_GEIGER_COUNTER,
    PORT_GPS_FIX,
    GEIGER_COUNTER_DSDL,
    GPS_FIX_DSDL,
    LOWRATE_DSDL,
    SENSOR_PORTS,
    resolve_dsdl_class,
)


Fix_0_1 = resolve_dsdl_class(GPS_FIX_DSDL)
LowRate_0_1 = resolve_dsdl_class(LOWRATE_DSDL)
GeigerCounter_0_1 = resolve_dsdl_class(GEIGER_COUNTER_DSDL)
EFM_0_1 = resolve_dsdl_class(EFM_DSDL)

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

    RISING = 0
    FALLING = 1

    WINDOW_US = 1_000_000

    last_edge_us = [
        [None, None]
        for _ in range(2)
    ]

    last_hall_rx_time = [None, None]

    periods_us = [
        deque(maxlen=10)
        for _ in range(2)
    ]

    motor_frequency_hz = [0.0, 0.0]


    def record_motor_edge(
        motor_index: int,
        timestamp_us: int,
        rising: bool,
    ) -> float:
        """Record an edge and return rolling motor frequency in Hz."""

        edges = last_edge_us[motor_index]
        periods = periods_us[motor_index]

        while periods and timestamp_us - periods[0][0] > WINDOW_US:
            periods.popleft()

        if rising:
            previous = edges[RISING]

            if previous is not None:
                delta_us = timestamp_us - previous

                if 0 < delta_us <= WINDOW_US:
                    periods.append((timestamp_us, delta_us))

            edges[RISING] = timestamp_us

        else:
            edges[FALLING] = timestamp_us

        if not periods:
            return 0.0

        average_period_us = (
            sum(delta for _, delta in periods) / len(periods)
        )

        return 1_000_000.0 / average_period_us

    # ── Per-sensor state: {field_name: {"msg": <latest>, "rx_ms": int}} ──
    latest: dict[str, dict] = {}
    geiger_counts: int = 0
    efm_hall_counts = [0, 0]
    print("EFM class:", EFM_0_1)
    print("Hall class:", HallEvent_0_1)
    print("Sample class:", SampleBatch_0_1)
    # ── Create one subscriber per sensor ─────────────────────────────────
    async def _sensor_callback(field_name: str, sub):
        """Receive loop for a single sensor subject."""
        print(f"Sensor callback: {field_name}")
        async for msg, _transfer in sub:
            print(f"Received: {field_name}")
            latest[field_name] = {"msg": msg, "rx_ms": _now_ms()}

    tasks: list[asyncio.Task] = []
    for port_id, dsdl_fqn, field_name in SENSOR_PORTS:
        cls = resolve_dsdl_class(dsdl_fqn)
        sub = node.make_subscriber(cls, port_id)
        print(f"Subscribed to {field_name}, {port_id}, {cls}")
        tasks.append(asyncio.create_task(_sensor_callback(field_name, sub)))

    # ── GPS fix subscriber ───────────────────────────────────────────────
    gps_sub = node.make_subscriber(Fix_0_1, PORT_GPS_FIX)

    async def _gps_callback():
        async for msg, _transfer in gps_sub:
            latest["gps_data"] = {"msg": msg, "rx_ms": _now_ms()}

    tasks.append(asyncio.create_task(_gps_callback()))

    geiger_counter_sub = node.make_subscriber(GeigerCounter_0_1, PORT_GEIGER_COUNTER)

    async def _geiger_counter_callback():
        nonlocal geiger_counts
        async for msg, _transfer in geiger_counter_sub:
            geiger_counts += len(msg.counts)

    tasks.append(asyncio.create_task(_geiger_counter_callback()))

    efm_sub = node.make_subscriber(EFM_0_1, PORT_EFM)

    async def _efm_callback():
        nonlocal efm_hall_counts

        async for msg, _transfer in efm_sub:

            if msg.hall_event is not None:
                event = msg.hall_event
                idx = event.efm_index

                if 0 <= idx < 2:
                    efm_hall_counts[idx] += 1

                    last_hall_rx_time[idx] = time.monotonic()

                    motor_frequency_hz[idx] = record_motor_edge(
                        motor_index=idx,
                        timestamp_us=event.timestamp_us,
                        rising=event.rising,
                    )

    tasks.append(asyncio.create_task(_efm_callback()))

    # ── Aggregation loop at LOWRATE_AGG_HZ ───────────────────────────────
    period = 1.0 / LOWRATE_AGG_HZ

    async def _aggregation_loop():
        nonlocal geiger_counts
        while True:
            now_ms = _now_ms()

            agg = LowRate_0_1()

            # Copy fresh sensor samples into the aggregate. If a sample is stale,
            # the default-constructed nested message remains in place.
            for _port_id, _dsdl_fqn, field_name in SENSOR_PORTS:
                entry = latest.get(field_name)
                if entry is not None and (now_ms - entry["rx_ms"]) < STALE_MS:
                    setattr(agg, field_name, entry["msg"])

            # GPS fix uses the same freshness gating pattern. fix_ok remains
            # owned by the GPS message itself.
            gps_entry = latest.get("gps_data")
            if gps_entry is not None and (now_ms - gps_entry["rx_ms"]) < STALE_MS:
                agg.gps_data = gps_entry["msg"]

            agg.geiger_aggregate.counts_in_interval = geiger_counts
            geiger_counts = 0

            # EFM aggregation
            now = time.monotonic()

            for idx in range(2):
                efm = agg.efm_aggregate[idx]

                last_rx = last_hall_rx_time[idx]

                valid = (
                    last_rx is not None
                    and (now - last_rx) <= 1.0
                    and motor_frequency_hz[idx] > 0.0
                )

                efm.valid = valid
                efm.motor_frequency = (
                    motor_frequency_hz[idx] if valid else 0.0
                )

                efm_hall_counts[idx] = 0

            print(f"Published lowrate agg!");

            await agg_pub.publish(agg)
            await asyncio.sleep(period)

    tasks.append(asyncio.create_task(_aggregation_loop()))

    try:
        await asyncio.gather(*tasks)
    finally:
        node.close()


if __name__ == "__main__":
    asyncio.run(run())
