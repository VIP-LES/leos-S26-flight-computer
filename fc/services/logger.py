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

    # ── Subscribers ──────────────────────────────────────────────────────
    agg_sub = node.make_subscriber(LowRate_0_1, PORT_LOW_AGG)
    efm_sub = node.make_subscriber(EFM_0_1, PORT_EFM)
    geiger_sub = node.make_subscriber(GeigerCounter_0_1, PORT_GEIGER_COUNTER)

    async def _log_aggregate():
        async for msg, _transfer in agg_sub:
            payload = dsdl.to_builtin(msg)
            log.write(kind=RECORD_KIND_LOW_AGG, port_id=PORT_LOW_AGG, payload=payload)

    async def _log_geiger():
        queue = asyncio.Queue()

        async def receiver():
            async for msg, _transfer in geiger_sub:
                await queue.put(msg)

        receiver_task = asyncio.create_task(receiver())
        buffer = bytearray()

        try:
            with open("logs/geiger.bin", "ab") as f:
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
                await queue.put(msg)

        receiver_task = asyncio.create_task(receiver())
        buffer = bytearray()

        try:
            with open("logs/efm.bin", "ab") as f:
                while True:
                    timed_out = False

                    try:
                        msg = await asyncio.wait_for(
                            queue.get(),
                            timeout=1.0,
                        )

                        if msg.samples is not None:
                            batch = msg.samples

                            values = [
                                sum(batch.efm0_diff) / 4.0,
                                sum(batch.efm1_diff) / 4.0,
                            ]

                            for idx, value in enumerate(values):
                                buffer.extend(
                                    struct.pack(
                                        "<Q B f",
                                        batch.timestamp_us,
                                        idx,
                                        value,
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
            await asyncio.gather(receiver_task, return_exceptions=True)

            if buffer:
                with open("logs/efm.bin", "ab") as f:
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
