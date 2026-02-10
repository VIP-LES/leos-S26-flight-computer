# packet.py
from __future__ import annotations

import struct
import time
from typing import Optional

from telemetry_pb2 import Telemetry


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= (b << 8) & 0xFFFF
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


class PacketBuilder:
    def build_from_latest(
        self,
        *,
        seq: int,
        temp_c: Optional[float],
        pressure_pa: Optional[float],
        air_pm25_env: Optional[int],
        air_aqi_pm25_us: Optional[int],
        uv_uvi: Optional[float],
    ) -> bytes:
        msg = Telemetry()
        msg.seq = int(seq)
        msg.unix_s = int(time.time())

        if temp_c is not None:
            msg.temp_c = float(temp_c)
        if pressure_pa is not None:
            msg.pressure_pa = float(pressure_pa)
        if air_pm25_env is not None:
            msg.air_pm25_env = int(air_pm25_env)
        if air_aqi_pm25_us is not None:
            msg.air_aqi_pm25_us = int(air_aqi_pm25_us)
        if uv_uvi is not None:
            msg.uv_uvi = float(uv_uvi)

        payload = msg.SerializeToString()
        crc = crc16_ccitt_false(payload)
        return payload + struct.pack(">H", crc)


_builder = PacketBuilder()

def build_from_latest(**kwargs) -> bytes:
    return _builder.build_from_latest(**kwargs)
