# publishRadio.py
import asyncio
import time
import os
import sys

from pycyphal.application import make_node, NodeInfo
from pycyphal.transport.can import CANTransport
from pycyphal.transport.can.media.socketcan import SocketCANMedia

# ---- DSDL import ----
dsdl_out_path = os.path.join(os.path.expanduser("~"), "LEOS-S26-FC/dsdl_out")
sys.path.insert(0, dsdl_out_path)

from leos.sensors import UVLight_0_1, Temp_0_1, Pressure_0_1, AirQuality_0_1
from leos.service import Cutdown_0_1

# ---- Your packet builder (EFM removed) ----
from packet import build_from_latest


# ---- LoRa driver stub (replace with your real sx1262 import/init) ----
class LoRaRadio:
    def __init__(self) -> None:
        # TODO: init your sx1262 here (SPI pins, freq, bw, sf, etc.)
        pass

    def send(self, payload: bytes) -> None:
        # TODO: replace with your driver call, e.g. sx.send(payload)
        print(f"[LoRa] sending {len(payload)} bytes: {payload.hex()}")


class Latest:
    """Holds latest sensor values."""

    def __init__(self) -> None:
        self.temp_c = None
        self.pressure_pa = None
        self.air_pm25_env = None
        self.air_aqi_pm25_us = None
        self.uv_uvi = None


async def subscribe_loop(subTemp, subPressure, subAir, subUV, latest: Latest) -> None:
    """
    Continuously receives messages and updates the cache.
    Use receive() (blocking) per-subscription in separate tasks.
    """

    async def run_temp():
        while True:
            msg, _ = await subTemp.receive()
            # Your Temp_0_1 uses kelvin field in your earlier code:
            latest.temp_c = msg.temperature.kelvin - 273.15

    async def run_pressure():
        while True:
            msg, _ = await subPressure.receive()
            latest.pressure_pa = msg.pressure.pascal

    async def run_air():
        while True:
            msg, _ = await subAir.receive()
            latest.air_pm25_env = int(msg.pm25_env)
            latest.air_aqi_pm25_us = int(msg.aqi_pm25_us)

    async def run_uv():
        while True:
            msg, _ = await subUV.receive()
            # If msg.uvi is already numeric:
            latest.uv_uvi = float(msg.uvi)

    await asyncio.gather(run_temp(), run_pressure(), run_air(), run_uv())


async def radio_publish_loop(
    radio: LoRaRadio, latest: Latest, period_s: float = 1.0
) -> None:
    """
    Periodically builds ONE packet from latest values and transmits over LoRa.
    """
    seq = 0
    while True:
        seq = (seq + 1) & 0xFFFF

        frame = build_from_latest(
            seq=seq,
            temp_c=latest.temp_c,
            pressure_pa=latest.pressure_pa,
            air_pm25_env=latest.air_pm25_env,
            air_aqi_pm25_us=latest.air_aqi_pm25_us,
            uv_uvi=latest.uv_uvi,
        )

        radio.send(frame)
        await asyncio.sleep(period_s)


async def main():
    # --- CAN transport ---
    media = SocketCANMedia("can0", mtu=64)
    transport = CANTransport(media=media, local_node_id=42)

    # --- Node ---
    node = make_node(info=NodeInfo(name="my_pi_node"), transport=transport)

    # --- Subs ---
    subTemp = node.make_subscriber(Temp_0_1)
    subAir = node.make_subscriber(AirQuality_0_1)
    subPressure = node.make_subscriber(Pressure_0_1)
    subUV = node.make_subscriber(UVLight_0_1)

    node.start()
    print("Cyphal node started. Caching latest sensor values...")

    latest = Latest()
    radio = LoRaRadio()

    # Run subscriber tasks + periodic radio publisher concurrently
    await asyncio.gather(
        subscribe_loop(subTemp, subPressure, subAir, subUV, latest),
        radio_publish_loop(radio, latest, period_s=1.0),  # change rate here
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except PermissionError:
        print("PermissionError: could not access can0 (try sudo).")
    except KeyboardInterrupt:
        print("\nStopped.")
