#!/usr/bin/env python3
"""
Minimal flight-computer log inspector.

Decodes SQLite log rows for:
  - Low-rate aggregate (`port_id=1500`)
  - EFM ADC (`port_id=1400`)

Usage examples:
  python tools/inspect_fc_log.py
  python tools/inspect_fc_log.py logs
  python tools/inspect_fc_log.py logs/leos_20260418_053249.sqlite3 --kind efm
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Iterable


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DSDL_OUT = os.path.join(REPO_ROOT, "dsdl_out")
if DSDL_OUT not in sys.path:
    sys.path.insert(0, DSDL_OUT)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from nunavut_support import deserialize as dsdl_deserialize

from fc.config import LOG_DIR, PORT_EFM, PORT_LOW_AGG, RECORD_KIND_EFM, RECORD_KIND_LOW_AGG
from leos.aggregate.LowRate_0_1 import LowRate_0_1
from leos.efm.ADC_0_2 import ADC_0_2


def _pick_log_path(path: str | None) -> str:
    candidate = path or LOG_DIR
    candidate = os.path.abspath(candidate)
    if os.path.isdir(candidate):
        logs = sorted(
            os.path.join(candidate, name)
            for name in os.listdir(candidate)
            if name.endswith(".sqlite3")
        )
        if not logs:
            raise FileNotFoundError(f"No .sqlite3 logs found in {candidate}")
        return logs[-1]
    if not os.path.isfile(candidate):
        raise FileNotFoundError(candidate)
    return candidate


def _scalar_value(obj, *names: str):
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _fmt_float(value) -> str:
    try:
        return f"{float(value):.3f}"
    except Exception:
        return str(value)


def _decode_payload(record_kind: int, port_id: int, payload: bytes):
    if record_kind == RECORD_KIND_LOW_AGG or port_id == PORT_LOW_AGG:
        return "low_rate", dsdl_deserialize(LowRate_0_1, [memoryview(payload)])
    if record_kind == RECORD_KIND_EFM or port_id == PORT_EFM:
        return "efm", dsdl_deserialize(ADC_0_2, [memoryview(payload)])
    return None, None


def _summarize_low_rate(msg: LowRate_0_1) -> str:
    bme = msg.bme688
    tsl = msg.tsl2591
    ltr = msg.ltr390
    pms = msg.pmsa003i
    gps = msg.gps_data
    parts = [
        f"gps.fix_ok={gps.fix_ok}",
        f"gps.lat={_fmt_float(gps.lat)}",
        f"gps.lon={_fmt_float(gps.lon)}",
        f"gps.sats={gps.sats_used}/{gps.sats_visible}",
        f"bme.valid={bme.valid}",
        f"bme.temp={_fmt_float(_scalar_value(bme.temperature, 'kelvin'))}",
        f"bme.pressure={_fmt_float(_scalar_value(bme.pressure, 'pascal'))}",
        f"bme.humidity={_fmt_float(bme.humidity)}",
        f"tsl.valid={tsl.valid}",
        f"tsl.lux={_fmt_float(tsl.light_lux)}",
        f"ltr.valid={ltr.valid}",
        f"ltr.uvs={ltr.uvs}",
        f"pms.valid={pms.valid}",
        f"pms.pm25={pms.pm25_env}",
    ]
    return " ".join(parts)


def _string_value(uavcan_string) -> str:
    value = getattr(uavcan_string, "value", None)
    if value is None:
        return ""
    count = int(getattr(value, "count", 0))
    elements = getattr(value, "elements", [])
    return bytes(elements[:count]).decode("utf-8", errors="replace")


def _summarize_efm(msg: ADC_0_2) -> str:
    parts = [
        f"valid={msg.valid}",
        f"location={_string_value(msg.location)!r}",
        f"adc1_ch1_diff={_fmt_float(msg.adc1_ch1_diff)}",
        f"adc1_ch4_breakbeam={_fmt_float(msg.adc1_ch4_breakbeam)}",
        f"adc2_ch1_diff={_fmt_float(msg.adc2_ch1_diff)}",
        f"adc2_ch4_breakbeam={_fmt_float(msg.adc2_ch4_breakbeam)}",
    ]
    return " ".join(parts)


def _iter_rows(conn: sqlite3.Connection, kind_filter: str, limit: int) -> Iterable[tuple]:
    clauses = []
    params: list[object] = []
    if kind_filter == "efm":
        clauses.append("(record_kind = ? OR port_id = ?)")
        params.extend([RECORD_KIND_EFM, PORT_EFM])
    elif kind_filter == "low_rate":
        clauses.append("(record_kind = ? OR port_id = ?)")
        params.extend([RECORD_KIND_LOW_AGG, PORT_LOW_AGG])

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT id, rx_mono_ns, record_kind, port_id, payload_len, payload
        FROM records
        {where_sql}
        ORDER BY id DESC
        LIMIT ?
    """
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return reversed(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect decoded flight-computer log rows.")
    parser.add_argument(
        "path",
        nargs="?",
        default=LOG_DIR,
        help="SQLite log file or logs directory (defaults to fc/logs newest file).",
    )
    parser.add_argument(
        "--kind",
        choices=("both", "efm", "low_rate"),
        default="both",
        help="Which decoded rows to show.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many newest rows to show.",
    )
    args = parser.parse_args()

    log_path = _pick_log_path(args.path)
    conn = sqlite3.connect(f"file:{log_path}?mode=ro", uri=True)
    try:
        print(f"# log: {log_path}")
        print(f"# view: {args.kind}  limit: {args.limit}")
        for row_id, rx_mono_ns, record_kind, port_id, payload_len, payload in _iter_rows(
            conn, args.kind, args.limit
        ):
            decoded_kind, decoded = _decode_payload(record_kind, port_id, payload)
            if decoded is None:
                continue
            if decoded_kind == "low_rate":
                summary = _summarize_low_rate(decoded)
            else:
                summary = _summarize_efm(decoded)
            print(
                f"id={row_id} rx_mono_ns={rx_mono_ns} "
                f"kind={decoded_kind} port={port_id} bytes={payload_len} {summary}"
            )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
