# lora_telemetry_packet.py
#
# Telemetry packet for SX1262 LoRa downlink (built from PyCyphal sensor data)
# EFM REMOVED.
#
# Packet layout (little-endian):
#   MAGIC    2  b"\xAA\x55"
#   VER      1  uint8
#   MSGTYPE  1  uint8  (1 = telemetry)
#   SEQ      2  uint16
#   UNIX_S   4  uint32
#   FLAGS    2  uint16
#   PAYLOAD  N  fields in fixed order, present only if their flag is set
#   CRC16    2  uint16   (CRC-16/CCITT-FALSE over VER..PAYLOAD)

from __future__ import annotations

from dataclasses import dataclass
import struct
import time
from typing import Optional, Tuple

MAGIC = b"\xAA\x55"
VERSION = 1
MSGTYPE_TELEMETRY = 1

# Presence flags
F_TEMP = 1 << 0
F_PRESSURE = 1 << 1
F_AIR_PM25 = 1 << 2
F_AIR_AQI25 = 1 << 3
F_UV = 1 << 4


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


def _clamp_u16(x: int) -> int:
    return 0 if x < 0 else 65535 if x > 65535 else x


def _clamp_u32(x: int) -> int:
    return 0 if x < 0 else 0xFFFFFFFF if x > 0xFFFFFFFF else x


def _clamp_i16(x: int) -> int:
    return -32768 if x < -32768 else 32767 if x > 32767 else x


@dataclass
class TelemetryPacket:
    seq: int
    unix_s: int

    temp_c: Optional[float] = None
    pressure_pa: Optional[int] = None
    pm25_env: Optional[int] = None
    aqi_pm25_us: Optional[int] = None
    uv_uvi: Optional[float] = None

    def encode(self) -> bytes:
        payload = bytearray()
        flags = 0

        if self.temp_c is not None:
            flags |= F_TEMP
            temp_x100 = _clamp_i16(int(round(self.temp_c * 100.0)))
            payload += struct.pack("<h", temp_x100)

        if self.pressure_pa is not None:
            flags |= F_PRESSURE
            payload += struct.pack("<I", _clamp_u32(int(self.pressure_pa)))

        if self.pm25_env is not None:
            flags |= F_AIR_PM25
            payload += struct.pack("<I", _clamp_u32(int(self.pm25_env)))

        if self.aqi_pm25_us is not None:
            flags |= F_AIR_AQI25
            payload += struct.pack("<I", _clamp_u32(int(self.aqi_pm25_us)))

        if self.uv_uvi is not None:
            flags |= F_UV
            uv_x100 = _clamp_u16(int(round(self.uv_uvi * 100.0)))
            payload += struct.pack("<H", uv_x100)

        header = struct.pack(
            "<BBH I H",
            VERSION,
            MSGTYPE_TELEMETRY,
            self.seq & 0xFFFF,
            _clamp_u32(self.unix_s),
            flags & 0xFFFF,
        )

        crc = crc16_ccitt_false(header + payload)
        return MAGIC + header + payload + struct.pack("<H", crc)


def decode_packet(frame: bytes) -> Tuple[TelemetryPacket, dict]:
    if len(frame) < 2 + 1 + 1 + 2 + 4 + 2 + 2:
        raise ValueError("frame too short")

    if frame[:2] != MAGIC:
        raise ValueError("bad magic")

    ver, mtype, seq, unix_s, flags = struct.unpack_from("<BBH I H", frame, 2)

    if ver != VERSION:
        raise ValueError("unsupported version")

    if mtype != MSGTYPE_TELEMETRY:
        raise ValueError("unsupported msg type")

    crc_given = struct.unpack_from("<H", frame, len(frame) - 2)[0]
    body = frame[2:-2]

    crc_calc = crc16_ccitt_false(body)
    if crc_calc != crc_given:
        raise ValueError("crc mismatch")

    offset = 2 + struct.calcsize("<BBH I H")
    fields = {}

    def take(fmt: str):
        nonlocal offset
        size = struct.calcsize(fmt)
        out = struct.unpack_from(fmt, frame, offset)
        offset += size
        return out[0] if len(out) == 1 else out

    if flags & F_TEMP:
        fields["temp_c"] = take("<h") / 100.0

    if flags & F_PRESSURE:
        fields["pressure_pa"] = take("<I")

    if flags & F_AIR_PM25:
        fields["pm25_env"] = take("<I")

    if flags & F_AIR_AQI25:
        fields["aqi_pm25_us"] = take("<I")

    if flags & F_UV:
        fields["uv_uvi"] = take("<H") / 100.0

    pkt = TelemetryPacket(
        seq=seq,
        unix_s=unix_s,
    )

    return pkt, fields


def build_from_latest(
    *,
    seq: int,
    temp_c: Optional[float],
    pressure_pa: Optional[int],
    air_pm25_env: Optional[int],
    air_aqi_pm25_us: Optional[int],
    uv_uvi: Optional[float],
) -> bytes:
    pkt = TelemetryPacket(
        seq=seq,
        unix_s=int(time.time()),
        temp_c=temp_c,
        pressure_pa=pressure_pa,
        pm25_env=air_pm25_env,
        aqi_pm25_us=air_aqi_pm25_us,
        uv_uvi=uv_uvi,
    )
    return pkt.encode()
