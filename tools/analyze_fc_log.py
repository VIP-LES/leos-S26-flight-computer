#!/usr/bin/env python3
"""
Post-flight analyzer for flight-computer SQLite logs.

How to run
==========
This script is meant to run on a normal computer after you copy a flight log
database off the flight computer. It does not need to run on the flight
computer itself.

From the flight-computer repo root:

    cd /path/to/leos-S26-flight-computer

If you already have the repo's Python environment:

    .venv/bin/python tools/analyze_fc_log.py /path/to/log.sqlite3

If you are using the system Python instead:

    python3 tools/analyze_fc_log.py /path/to/log.sqlite3

You can also point it at a directory and it will automatically pick the newest
`.sqlite3` file in that directory:

    .venv/bin/python tools/analyze_fc_log.py /path/to/logs

What it does by default
=======================
1. Opens the SQLite database read-only.
2. Decodes all supported logged record types into readable values.
3. Prints a post-flight summary to the terminal.
4. Writes decoded CSV files and a JSON summary into an analysis folder.
5. Generates PNG plots if `matplotlib` is installed.

Default output location
=======================
By default, output is written next to the database in a folder named:

    <database_stem>_analysis

Example:

    logs/leos_20260418_053249.sqlite3
    -> logs/leos_20260418_053249_analysis/

Useful options
==============
Disable plots:

    .venv/bin/python tools/analyze_fc_log.py /path/to/log.sqlite3 --no-plots

Disable CSV/JSON export and print only the summary:

    .venv/bin/python tools/analyze_fc_log.py /path/to/log.sqlite3 --no-export

Only analyze one record type:

    .venv/bin/python tools/analyze_fc_log.py /path/to/log.sqlite3 --kind low_rate
    .venv/bin/python tools/analyze_fc_log.py /path/to/log.sqlite3 --kind efm

Show the decoded CSV column names without running a full analysis:

    .venv/bin/python tools/analyze_fc_log.py --list-fields

Write results to a specific directory:

    .venv/bin/python tools/analyze_fc_log.py /path/to/log.sqlite3 --output-dir /tmp/flight_analysis

Notes
=====
- This script currently decodes the record types that the flight computer
  logger stores today: low-rate aggregate data and EFM ADC data.
- Plot generation is optional. If `matplotlib` is not installed, decoding,
  summaries, and CSV/JSON export still work.
- Time-based plots use relative time in seconds starting from the first row
  in the log.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import statistics
import sys
import zlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DSDL_OUT = REPO_ROOT / "dsdl_out"
if str(DSDL_OUT) not in sys.path:
    sys.path.insert(0, str(DSDL_OUT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LOG_DIR = str(REPO_ROOT / "logs")


LOW_RATE_FIELDS = [
    "id",
    "rx_mono_ns",
    "t_rel_s",
    "record_kind",
    "port_id",
    "payload_len",
    "payload_crc32",
    "payload_crc32_calc",
    "crc_ok",
    "kind",
    "bme688_location",
    "bme688_valid",
    "bme688_humidity_pct",
    "bme688_pressure_pa",
    "bme688_temperature_k",
    "bme688_temperature_c",
    "bme688_altitude_m",
    "bme688_gas_resistance",
    "tsl2591_location",
    "tsl2591_valid",
    "tsl2591_light_lux",
    "tsl2591_raw_visible",
    "tsl2591_raw_infrared",
    "tsl2591_raw_full_spectrum",
    "ltr390_location",
    "ltr390_valid",
    "ltr390_uvs",
    "pmsa003i_location",
    "pmsa003i_valid",
    "pmsa003i_pm10_env",
    "pmsa003i_pm25_env",
    "pmsa003i_pm100_env",
    "pmsa003i_aqi_pm25_us",
    "pmsa003i_aqi_pm100_us",
    "pmsa003i_particles_03um",
    "pmsa003i_particles_05um",
    "pmsa003i_particles_10um",
    "pmsa003i_particles_25um",
    "pmsa003i_particles_50um",
    "pmsa003i_particles_100um",
    "gps_fix_ok",
    "gps_lat",
    "gps_lon",
    "gps_alt_m",
    "gps_speed_mps",
    "gps_track_deg",
    "gps_sats_used",
    "gps_sats_visible",
    "gps_utc_us",
    "gps_utc_iso",
]

EFM_FIELDS = [
    "id",
    "rx_mono_ns",
    "t_rel_s",
    "record_kind",
    "port_id",
    "payload_len",
    "payload_crc32",
    "payload_crc32_calc",
    "crc_ok",
    "kind",
    "efm_location",
    "efm_valid",
    "efm_adc1_ch1_diff",
    "efm_adc1_ch4_breakbeam",
    "efm_adc2_ch1_diff",
    "efm_adc2_ch4_breakbeam",
]


_RUNTIME = None


def _runtime():
    global _RUNTIME
    if _RUNTIME is None:
        from nunavut_support import deserialize as dsdl_deserialize

        from fc.config import PORT_EFM, PORT_LOW_AGG, RECORD_KIND_EFM, RECORD_KIND_LOW_AGG
        from leos.aggregate.LowRate_0_1 import LowRate_0_1
        from leos.efm.ADC_0_2 import ADC_0_2

        _RUNTIME = {
            "deserialize": dsdl_deserialize,
            "PORT_EFM": PORT_EFM,
            "PORT_LOW_AGG": PORT_LOW_AGG,
            "RECORD_KIND_EFM": RECORD_KIND_EFM,
            "RECORD_KIND_LOW_AGG": RECORD_KIND_LOW_AGG,
            "LowRate_0_1": LowRate_0_1,
            "ADC_0_2": ADC_0_2,
        }
    return _RUNTIME


@dataclass
class DecodedRow:
    kind: str
    row: dict[str, object]


def _pick_log_path(path: str | None) -> Path:
    candidate = Path(path or LOG_DIR).expanduser().resolve()
    if candidate.is_dir():
        logs = sorted(p for p in candidate.iterdir() if p.suffix == ".sqlite3")
        if not logs:
            raise FileNotFoundError(f"No .sqlite3 logs found in {candidate}")
        return logs[-1]
    if not candidate.is_file():
        raise FileNotFoundError(str(candidate))
    return candidate


def _string_value(uavcan_string) -> str:
    value = getattr(uavcan_string, "value", None)
    if value is None:
        return ""
    count = int(getattr(value, "count", 0))
    elements = getattr(value, "elements", [])
    return bytes(elements[:count]).decode("utf-8", errors="replace")


def _scalar_value(obj, *names: str):
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except Exception:
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _safe_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _iso_from_us(timestamp_us: int | None) -> str | None:
    if not timestamp_us:
        return None
    return datetime.fromtimestamp(timestamp_us / 1_000_000, tz=timezone.utc).isoformat()


def _decode_payload(record_kind: int, port_id: int, payload: bytes):
    runtime = _runtime()
    if record_kind == runtime["RECORD_KIND_LOW_AGG"] or port_id == runtime["PORT_LOW_AGG"]:
        return "low_rate", runtime["deserialize"](runtime["LowRate_0_1"], [memoryview(payload)])
    if record_kind == runtime["RECORD_KIND_EFM"] or port_id == runtime["PORT_EFM"]:
        return "efm", runtime["deserialize"](runtime["ADC_0_2"], [memoryview(payload)])
    return None, None


def _flatten_low_rate(base: dict[str, object], msg: LowRate_0_1) -> dict[str, object]:
    bme = msg.bme688
    tsl = msg.tsl2591
    ltr = msg.ltr390
    pms = msg.pmsa003i
    gps = msg.gps_data

    temp_k = _safe_float(_scalar_value(bme.temperature, "kelvin"))
    gps_utc_us = _safe_int(getattr(gps.gps_utc, "microsecond", 0))

    return {
        **base,
        "bme688_location": _string_value(bme.location),
        "bme688_valid": bool(bme.valid),
        "bme688_humidity_pct": _safe_float(bme.humidity),
        "bme688_pressure_pa": _safe_float(_scalar_value(bme.pressure, "pascal")),
        "bme688_temperature_k": temp_k,
        "bme688_temperature_c": None if temp_k is None else temp_k - 273.15,
        "bme688_altitude_m": _safe_float(_scalar_value(bme.altitude, "meter")),
        "bme688_gas_resistance": _safe_float(bme.gas_resistance),
        "tsl2591_location": _string_value(tsl.location),
        "tsl2591_valid": bool(tsl.valid),
        "tsl2591_light_lux": _safe_float(tsl.light_lux),
        "tsl2591_raw_visible": _safe_int(tsl.raw_visible),
        "tsl2591_raw_infrared": _safe_int(tsl.raw_infrared),
        "tsl2591_raw_full_spectrum": _safe_int(tsl.raw_full_spectrum),
        "ltr390_location": _string_value(ltr.location),
        "ltr390_valid": bool(ltr.valid),
        "ltr390_uvs": _safe_int(ltr.uvs),
        "pmsa003i_location": _string_value(pms.location),
        "pmsa003i_valid": bool(pms.valid),
        "pmsa003i_pm10_env": _safe_int(pms.pm10_env),
        "pmsa003i_pm25_env": _safe_int(pms.pm25_env),
        "pmsa003i_pm100_env": _safe_int(pms.pm100_env),
        "pmsa003i_aqi_pm25_us": _safe_int(pms.aqi_pm25_us),
        "pmsa003i_aqi_pm100_us": _safe_int(pms.aqi_pm100_us),
        "pmsa003i_particles_03um": _safe_int(pms.particles_03um),
        "pmsa003i_particles_05um": _safe_int(pms.particles_05um),
        "pmsa003i_particles_10um": _safe_int(pms.particles_10um),
        "pmsa003i_particles_25um": _safe_int(pms.particles_25um),
        "pmsa003i_particles_50um": _safe_int(pms.particles_50um),
        "pmsa003i_particles_100um": _safe_int(pms.particles_100um),
        "gps_fix_ok": bool(gps.fix_ok),
        "gps_lat": _safe_float(gps.lat),
        "gps_lon": _safe_float(gps.lon),
        "gps_alt_m": _safe_float(gps.alt_m),
        "gps_speed_mps": _safe_float(gps.speed_mps),
        "gps_track_deg": _safe_float(gps.track_deg),
        "gps_sats_used": _safe_int(gps.sats_used),
        "gps_sats_visible": _safe_int(gps.sats_visible),
        "gps_utc_us": gps_utc_us,
        "gps_utc_iso": _iso_from_us(gps_utc_us),
    }


def _flatten_efm(base: dict[str, object], msg: ADC_0_2) -> dict[str, object]:
    return {
        **base,
        "efm_location": _string_value(msg.location),
        "efm_valid": bool(msg.valid),
        "efm_adc1_ch1_diff": _safe_float(msg.adc1_ch1_diff),
        "efm_adc1_ch4_breakbeam": _safe_float(msg.adc1_ch4_breakbeam),
        "efm_adc2_ch1_diff": _safe_float(msg.adc2_ch1_diff),
        "efm_adc2_ch4_breakbeam": _safe_float(msg.adc2_ch4_breakbeam),
    }


def _iter_rows(conn: sqlite3.Connection, kind_filter: str) -> Iterable[tuple]:
    runtime = _runtime()
    clauses = []
    params: list[object] = []
    if kind_filter == "efm":
        clauses.append("(record_kind = ? OR port_id = ?)")
        params.extend([runtime["RECORD_KIND_EFM"], runtime["PORT_EFM"]])
    elif kind_filter == "low_rate":
        clauses.append("(record_kind = ? OR port_id = ?)")
        params.extend([runtime["RECORD_KIND_LOW_AGG"], runtime["PORT_LOW_AGG"]])

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT id, rx_mono_ns, record_kind, port_id, payload_len, payload_crc32, payload
        FROM records
        {where_sql}
        ORDER BY id ASC
    """
    return conn.execute(sql, params)


def _decode_all_rows(conn: sqlite3.Connection, kind_filter: str) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, int]]:
    low_rate_rows: list[dict[str, object]] = []
    efm_rows: list[dict[str, object]] = []
    issues = {"decode_failures": 0, "crc_mismatch": 0, "unknown_kind": 0}

    rows = list(_iter_rows(conn, kind_filter))
    if not rows:
        return low_rate_rows, efm_rows, issues

    first_rx_ns = int(rows[0][1])
    for row_id, rx_mono_ns, record_kind, port_id, payload_len, payload_crc32, payload in rows:
        payload_crc32_calc = zlib.crc32(payload) & 0xFFFFFFFF
        crc_ok = payload_crc32_calc == int(payload_crc32)
        if not crc_ok:
            issues["crc_mismatch"] += 1

        decoded_kind, decoded = _decode_payload(record_kind, port_id, payload)
        if decoded is None:
            issues["unknown_kind"] += 1
            continue

        base = {
            "id": int(row_id),
            "rx_mono_ns": int(rx_mono_ns),
            "t_rel_s": (int(rx_mono_ns) - first_rx_ns) / 1_000_000_000.0,
            "record_kind": int(record_kind),
            "port_id": int(port_id),
            "payload_len": int(payload_len),
            "payload_crc32": int(payload_crc32),
            "payload_crc32_calc": int(payload_crc32_calc),
            "crc_ok": crc_ok,
            "kind": decoded_kind,
        }

        try:
            if decoded_kind == "low_rate":
                low_rate_rows.append(_flatten_low_rate(base, decoded))
            elif decoded_kind == "efm":
                efm_rows.append(_flatten_efm(base, decoded))
        except Exception:
            issues["decode_failures"] += 1

    return low_rate_rows, efm_rows, issues


def _metadata_dict(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute("SELECT key, value FROM metadata ORDER BY key ASC").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(key): str(value) for key, value in rows}


def _series_values(rows: list[dict[str, object]], field: str, *, require_flag: str | None = None) -> list[float]:
    values: list[float] = []
    for row in rows:
        if require_flag is not None and not bool(row.get(require_flag)):
            continue
        value = _safe_float(row.get(field))
        if value is not None:
            values.append(value)
    return values


def _series_times_and_values(rows: list[dict[str, object]], field: str, *, require_flag: str | None = None) -> tuple[list[float], list[float]]:
    times: list[float] = []
    values: list[float] = []
    for row in rows:
        if require_flag is not None and not bool(row.get(require_flag)):
            continue
        value = _safe_float(row.get(field))
        t_rel_s = _safe_float(row.get("t_rel_s"))
        if value is None or t_rel_s is None:
            continue
        times.append(t_rel_s)
        values.append(value)
    return times, values


def _stats_dict(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    result: dict[str, float | int] = {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
    }
    if len(values) > 1:
        result["stdev"] = statistics.pstdev(values)
    return result


def _first_last_fix(rows: list[dict[str, object]]) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    fixes = [row for row in rows if bool(row.get("gps_fix_ok"))]
    if not fixes:
        return None, None
    return fixes[0], fixes[-1]


def _max_gap_seconds(rows: list[dict[str, object]]) -> float | None:
    if len(rows) < 2:
        return None
    deltas = []
    last = _safe_float(rows[0].get("t_rel_s"))
    for row in rows[1:]:
        current = _safe_float(row.get("t_rel_s"))
        if last is not None and current is not None:
            deltas.append(current - last)
        last = current
    return max(deltas) if deltas else None


def _observed_rate_hz(rows: list[dict[str, object]]) -> float | None:
    if len(rows) < 2:
        return None
    t0 = _safe_float(rows[0].get("t_rel_s"))
    t1 = _safe_float(rows[-1].get("t_rel_s"))
    if t0 is None or t1 is None or t1 <= t0:
        return None
    return (len(rows) - 1) / (t1 - t0)


def _build_summary(
    log_path: Path,
    metadata: dict[str, str],
    low_rate_rows: list[dict[str, object]],
    efm_rows: list[dict[str, object]],
    issues: dict[str, int],
) -> dict[str, object]:
    all_rows = low_rate_rows + efm_rows
    all_rows.sort(key=lambda row: int(row["id"]))

    first_t = _safe_float(all_rows[0].get("t_rel_s")) if all_rows else None
    last_t = _safe_float(all_rows[-1].get("t_rel_s")) if all_rows else None
    duration_s = None
    if first_t is not None and last_t is not None:
        duration_s = last_t - first_t

    first_fix, last_fix = _first_last_fix(low_rate_rows)
    summary = {
        "log_path": str(log_path),
        "metadata": metadata,
        "record_counts": {
            "decoded_total": len(all_rows),
            "low_rate": len(low_rate_rows),
            "efm": len(efm_rows),
        },
        "timing": {
            "duration_s": duration_s,
            "low_rate_rate_hz": _observed_rate_hz(low_rate_rows),
            "efm_rate_hz": _observed_rate_hz(efm_rows),
            "low_rate_max_gap_s": _max_gap_seconds(low_rate_rows),
            "efm_max_gap_s": _max_gap_seconds(efm_rows),
        },
        "issues": issues,
        "gps": {
            "first_valid_fix": first_fix,
            "last_valid_fix": last_fix,
            "altitude_m": _stats_dict(_series_values(low_rate_rows, "gps_alt_m", require_flag="gps_fix_ok")),
            "speed_mps": _stats_dict(_series_values(low_rate_rows, "gps_speed_mps", require_flag="gps_fix_ok")),
        },
        "bme688": {
            "temperature_c": _stats_dict(_series_values(low_rate_rows, "bme688_temperature_c", require_flag="bme688_valid")),
            "pressure_pa": _stats_dict(_series_values(low_rate_rows, "bme688_pressure_pa", require_flag="bme688_valid")),
            "humidity_pct": _stats_dict(_series_values(low_rate_rows, "bme688_humidity_pct", require_flag="bme688_valid")),
            "altitude_m": _stats_dict(_series_values(low_rate_rows, "bme688_altitude_m", require_flag="bme688_valid")),
        },
        "efm": {
            "adc1_ch1_diff": _stats_dict(_series_values(efm_rows, "efm_adc1_ch1_diff", require_flag="efm_valid")),
            "adc1_ch4_breakbeam": _stats_dict(_series_values(efm_rows, "efm_adc1_ch4_breakbeam", require_flag="efm_valid")),
            "adc2_ch1_diff": _stats_dict(_series_values(efm_rows, "efm_adc2_ch1_diff", require_flag="efm_valid")),
            "adc2_ch4_breakbeam": _stats_dict(_series_values(efm_rows, "efm_adc2_ch4_breakbeam", require_flag="efm_valid")),
        },
    }
    return summary


def _fmt_number(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _print_summary(summary: dict[str, object]) -> None:
    record_counts = summary["record_counts"]
    timing = summary["timing"]
    gps = summary["gps"]
    bme = summary["bme688"]
    efm = summary["efm"]
    issues = summary["issues"]

    print(f"# log: {summary['log_path']}")
    print(f"# decoded rows: {record_counts['decoded_total']}")
    print(
        "duration_s="
        f"{_fmt_number(timing['duration_s'])} "
        f"low_rate_count={record_counts['low_rate']} "
        f"low_rate_rate_hz={_fmt_number(timing['low_rate_rate_hz'])} "
        f"efm_count={record_counts['efm']} "
        f"efm_rate_hz={_fmt_number(timing['efm_rate_hz'])}"
    )
    print(
        "issues "
        f"crc_mismatch={issues['crc_mismatch']} "
        f"decode_failures={issues['decode_failures']} "
        f"unknown_kind={issues['unknown_kind']} "
        f"low_rate_max_gap_s={_fmt_number(timing['low_rate_max_gap_s'])} "
        f"efm_max_gap_s={_fmt_number(timing['efm_max_gap_s'])}"
    )

    first_fix = gps["first_valid_fix"]
    last_fix = gps["last_valid_fix"]
    if first_fix:
        print(
            "gps_first_fix "
            f"t_rel_s={_fmt_number(first_fix.get('t_rel_s'))} "
            f"lat={_fmt_number(first_fix.get('gps_lat'))} "
            f"lon={_fmt_number(first_fix.get('gps_lon'))} "
            f"alt_m={_fmt_number(first_fix.get('gps_alt_m'))} "
            f"utc={first_fix.get('gps_utc_iso') or 'n/a'}"
        )
    else:
        print("gps_first_fix none")

    if last_fix:
        print(
            "gps_last_fix "
            f"t_rel_s={_fmt_number(last_fix.get('t_rel_s'))} "
            f"lat={_fmt_number(last_fix.get('gps_lat'))} "
            f"lon={_fmt_number(last_fix.get('gps_lon'))} "
            f"alt_m={_fmt_number(last_fix.get('gps_alt_m'))} "
            f"utc={last_fix.get('gps_utc_iso') or 'n/a'}"
        )
    else:
        print("gps_last_fix none")

    for label, stats in [
        ("gps_altitude_m", gps["altitude_m"]),
        ("gps_speed_mps", gps["speed_mps"]),
        ("bme_temperature_c", bme["temperature_c"]),
        ("bme_pressure_pa", bme["pressure_pa"]),
        ("bme_humidity_pct", bme["humidity_pct"]),
        ("bme_altitude_m", bme["altitude_m"]),
        ("efm_adc1_ch1_diff", efm["adc1_ch1_diff"]),
        ("efm_adc1_ch4_breakbeam", efm["adc1_ch4_breakbeam"]),
        ("efm_adc2_ch1_diff", efm["adc2_ch1_diff"]),
        ("efm_adc2_ch4_breakbeam", efm["adc2_ch4_breakbeam"]),
    ]:
        if not stats:
            print(f"{label} none")
            continue
        print(
            f"{label} count={stats['count']} min={_fmt_number(stats['min'])} "
            f"max={_fmt_number(stats['max'])} mean={_fmt_number(stats['mean'])}"
        )


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})


def _write_outputs(
    output_dir: Path,
    low_rate_rows: list[dict[str, object]],
    efm_rows: list[dict[str, object]],
    summary: dict[str, object],
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    summary_path = output_dir / "summary.json"
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
    written.append(summary_path)

    if low_rate_rows:
        low_rate_path = output_dir / "low_rate_decoded.csv"
        _write_csv(low_rate_path, LOW_RATE_FIELDS, low_rate_rows)
        written.append(low_rate_path)

    if efm_rows:
        efm_path = output_dir / "efm_decoded.csv"
        _write_csv(efm_path, EFM_FIELDS, efm_rows)
        written.append(efm_path)

    return written


def _save_plot(fig, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    fig.clf()


def _generate_plots(output_dir: Path, low_rate_rows: list[dict[str, object]], efm_rows: list[dict[str, object]]) -> list[Path]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("# matplotlib not installed; skipping plots")
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def line_plot(path: Path, title: str, ylabel: str, series: list[tuple[str, list[float], list[float]]]) -> None:
        fig, ax = plt.subplots(figsize=(10, 5))
        plotted = False
        for label, xs, ys in series:
            if xs and ys:
                ax.plot(xs, ys, label=label, linewidth=1.5)
                plotted = True
        if not plotted:
            plt.close(fig)
            return
        ax.set_title(title)
        ax.set_xlabel("Time since first log row (s)")
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        if len(series) > 1:
            ax.legend()
        _save_plot(fig, path)
        plt.close(fig)
        written.append(path)

    line_plot(
        output_dir / "gps_altitude.png",
        "GPS Altitude",
        "Altitude (m)",
        [("gps_alt_m", *_series_times_and_values(low_rate_rows, "gps_alt_m", require_flag="gps_fix_ok"))],
    )
    line_plot(
        output_dir / "gps_speed.png",
        "GPS Speed",
        "Speed (m/s)",
        [("gps_speed_mps", *_series_times_and_values(low_rate_rows, "gps_speed_mps", require_flag="gps_fix_ok"))],
    )
    line_plot(
        output_dir / "bme_pressure.png",
        "BME688 Pressure",
        "Pressure (Pa)",
        [("bme688_pressure_pa", *_series_times_and_values(low_rate_rows, "bme688_pressure_pa", require_flag="bme688_valid"))],
    )
    line_plot(
        output_dir / "bme_temperature.png",
        "BME688 Temperature",
        "Temperature (C)",
        [("bme688_temperature_c", *_series_times_and_values(low_rate_rows, "bme688_temperature_c", require_flag="bme688_valid"))],
    )
    line_plot(
        output_dir / "bme_humidity.png",
        "BME688 Humidity",
        "Humidity (%)",
        [("bme688_humidity_pct", *_series_times_and_values(low_rate_rows, "bme688_humidity_pct", require_flag="bme688_valid"))],
    )
    line_plot(
        output_dir / "efm_channels.png",
        "EFM Channels",
        "Value",
        [
            ("adc1_ch1_diff", *_series_times_and_values(efm_rows, "efm_adc1_ch1_diff", require_flag="efm_valid")),
            ("adc1_ch4_breakbeam", *_series_times_and_values(efm_rows, "efm_adc1_ch4_breakbeam", require_flag="efm_valid")),
            ("adc2_ch1_diff", *_series_times_and_values(efm_rows, "efm_adc2_ch1_diff", require_flag="efm_valid")),
            ("adc2_ch4_breakbeam", *_series_times_and_values(efm_rows, "efm_adc2_ch4_breakbeam", require_flag="efm_valid")),
        ],
    )

    gps_points = [
        row for row in low_rate_rows
        if bool(row.get("gps_fix_ok")) and _safe_float(row.get("gps_lat")) is not None and _safe_float(row.get("gps_lon")) is not None
    ]
    if gps_points:
        fig, ax = plt.subplots(figsize=(7, 7))
        lons = [_safe_float(row.get("gps_lon")) for row in gps_points]
        lats = [_safe_float(row.get("gps_lat")) for row in gps_points]
        lons = [x for x in lons if x is not None]
        lats = [y for y in lats if y is not None]
        if lons and lats and len(lons) == len(lats):
            ax.plot(lons, lats, linewidth=1.5)
            ax.scatter([lons[0]], [lats[0]], label="start", s=35)
            ax.scatter([lons[-1]], [lats[-1]], label="end", s=35)
            ax.set_title("GPS Ground Track")
            ax.set_xlabel("Longitude")
            ax.set_ylabel("Latitude")
            ax.grid(True, alpha=0.3)
            ax.legend()
            track_path = output_dir / "gps_ground_track.png"
            _save_plot(fig, track_path)
            written.append(track_path)
        plt.close(fig)

    return written


def _default_output_dir(log_path: Path) -> Path:
    return log_path.with_name(f"{log_path.stem}_analysis")


def _list_fields() -> None:
    print("# low_rate fields")
    for name in LOW_RATE_FIELDS:
        print(name)
    print("# efm fields")
    for name in EFM_FIELDS:
        print(name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze and decode flight-computer SQLite logs.")
    parser.add_argument(
        "path",
        nargs="?",
        default=LOG_DIR,
        help="SQLite log file or directory containing log files. Defaults to the repo log directory.",
    )
    parser.add_argument(
        "--kind",
        choices=("both", "low_rate", "efm"),
        default="both",
        help="Restrict analysis to one record type.",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for CSV, JSON, and plot output. Defaults to <db_stem>_analysis next to the database.",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="Skip plot generation.",
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="Skip CSV and JSON export.",
    )
    parser.add_argument(
        "--list-fields",
        action="store_true",
        help="Print decoded field names and exit.",
    )
    args = parser.parse_args()

    if args.list_fields:
        _list_fields()
        return 0

    log_path = _pick_log_path(args.path)
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else _default_output_dir(log_path)

    conn = sqlite3.connect(f"file:{log_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        metadata = _metadata_dict(conn)
        low_rate_rows, efm_rows, issues = _decode_all_rows(conn, args.kind)
    finally:
        conn.close()

    summary = _build_summary(log_path, metadata, low_rate_rows, efm_rows, issues)
    _print_summary(summary)

    if not low_rate_rows and not efm_rows:
        print("# no supported decoded rows found")
        return 0

    if not args.no_export:
        written = _write_outputs(output_dir, low_rate_rows, efm_rows, summary)
        print(f"# wrote {len(written)} data files to {output_dir}")

    if not args.no_plots:
        plot_paths = _generate_plots(output_dir, low_rate_rows, efm_rows)
        if plot_paths:
            print(f"# wrote {len(plot_paths)} plot files to {output_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
