"""
Logger Service
===============
Responsibilities
  1. Subscribe to the LowRateAggregate port.
  2. Subscribe to the EFM port.
  3. Serialize every incoming DSDL message to bytes (using the generated
     ``_serialize_`` interface via nunavut_support.serialize).
  4. Hand each serialized payload to the LogWriter.

The logger does **not** need to know the fields — it just serializes the
DSDL object and writes the bytes.

Run as:  python -m fc.services.logger
"""

import struct
import os

import pycyphal.dsdl as dsdl
import asyncio

from pycyphal.application import make_node, NodeInfo
from pycyphal.transport.can import CANTransport
from pycyphal.transport.can.media.socketcan import SocketCANMedia
from datetime import datetime

# fc.__init__ ensures dsdl_out is on sys.path
import fc  # noqa: F401
from nunavut_support import serialize as dsdl_serialize

from fc.config import (
    CAN_INTERFACE,
    CAN_MTU,
    EFM_DSDL,
    GEIGER_COUNTER_DSDL,
    LOWRATE_DSDL,
    NODE_ID_LOGGER,
    NODE_NAME_LOGGER,
    PORT_LOW_AGG,
    PORT_EFM,
    PORT_GEIGER_COUNTER,
    RECORD_KIND_LOW_AGG,
    RECORD_KIND_EFM,
    resolve_dsdl_class,
)
from fc.log_writer import LogWriter


LowRate_0_1 = resolve_dsdl_class(LOWRATE_DSDL)
EFM_0_1 = resolve_dsdl_class(EFM_DSDL)
GeigerCounter_0_1 = resolve_dsdl_class(GEIGER_COUNTER_DSDL)


def _serialize_msg(msg) -> bytes:
    """Serialize any DSDL message to bytes using the nunavut runtime."""
    fragments = dsdl_serialize(msg)
    return b"".join(bytes(f) for f in fragments)


async def run() -> None:
    # ── Cyphal node ──────────────────────────────────────────────────────
    media = SocketCANMedia(CAN_INTERFACE, mtu=CAN_MTU)
    transport = CANTransport(media=media, local_node_id=NODE_ID_LOGGER)
    node = make_node(
        info=NodeInfo(name=NODE_NAME_LOGGER),
        transport=transport,
    )
    node.start()

    # ── Log writer ───────────────────────────────────────────────────────
    log = LogWriter()
    await log.open()

    print(LowRate_0_1)
    # ── Subscribers ──────────────────────────────────────────────────────
    agg_sub = node.make_subscriber(LowRate_0_1, PORT_LOW_AGG)
    efm_sub = node.make_subscriber(EFM_0_1, PORT_EFM)
    geiger_sub = node.make_subscriber(GeigerCounter_0_1, PORT_GEIGER_COUNTER)

    async def _log_aggregate():
        async for msg, _transfer in agg_sub:
            payload = dsdl.to_builtin(msg)
            log.write(kind=RECORD_KIND_LOW_AGG, port_id=PORT_LOW_AGG, payload=payload)

    geiger_filename = datetime.now().strftime(
        "logs/geiger_%Y%m%d_%H%M%S.bin"
    )

    async def _log_geiger():
        queue = asyncio.Queue()

        async def receiver():
            async for msg, _transfer in geiger_sub:
                await queue.put(msg)

        receiver_task = asyncio.create_task(receiver())
        buffer = bytearray()

        try:
            with open(geiger_filename, "ab") as f:
                while True:
                    timed_out = False

                    try:
                        msg = await asyncio.wait_for(
                            queue.get(),
                            timeout=1.0,
                        )

                        for timestamp_us in msg.counts:
                            buffer.extend(struct.pack("<Q", timestamp_us))

                    except asyncio.TimeoutError:
                        timed_out = True

                    if buffer and (len(buffer) >= 8192 or timed_out):
                        f.write(buffer)
                        f.flush()
                        buffer.clear()

        finally:
            receiver_task.cancel()
            await asyncio.gather(receiver_task, return_exceptions=True)

            if buffer:
                with open("logs/geiger.bin", "ab") as f:
                    f.write(buffer)
                    f.flush()


    async def _log_efm():
        queue = asyncio.Queue()

        async def receiver():
            async for msg, _transfer in efm_sub:
                print(f"Efm message! {msg}")
                await queue.put(msg)

        receiver_task = asyncio.create_task(receiver())
        buffer = bytearray()

        efm_filename = datetime.now().strftime(
            "logs/efm_%Y%m%d_%H%M%S.bin"
        )

        try:
            with open(efm_filename, "ab") as f:
                while True:
                    timed_out = False

                    try:
                        msg = await asyncio.wait_for(
                            queue.get(),
                            timeout=1.0,
                        )

                        if msg.samples is not None:
                            for sample in msg.samples.readings:
                                buffer.extend(
                                    struct.pack(
                                        "<BQIiBB",
                                        0,
                                        sample.timestamp_us,
                                        sample.sequence,
                                        sample.raw,
                                        sample.channel,
                                        sample.hall,
                                    )
                                )

                        elif msg.hall_event is not None:
                            event = msg.hall_event

                            buffer.extend(
                                struct.pack(
                                    "<BQBB",
                                    1,
                                    event.timestamp_us,
                                    event.efm_index,
                                    int(event.rising),
                                )
                            )

                    except asyncio.TimeoutError:
                        timed_out = True

                    if buffer and (len(buffer) >= 8192 or timed_out):
                        f.write(buffer)
                        f.flush()
                        buffer.clear()

        finally:
            receiver_task.cancel()
            await asyncio.gather(
                receiver_task,
                return_exceptions=True,
            )

            if buffer:
                with open(efm_filename, "ab") as f:
                    f.write(buffer)
                    f.flush()

    print("[logger] Logging service started.")

    try:
        await asyncio.gather(
            asyncio.create_task(_log_aggregate()),
            asyncio.create_task(_log_efm()),
            asyncio.create_task(_log_geiger()),
        )
    finally:
        await log.close()
        node.close()


if __name__ == "__main__":
    asyncio.run(run())
