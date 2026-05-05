# LEOS S26 Flight Computer — Project Documentation



---

## 1. Overview

The **LEOS S26 Flight Computer (FC)** is the Raspberry Pi-based onboard computer for the LEOS S26 high-altitude payload. Its job is to:

1. Talk to the **sensor board** and the **EFM (Electric Field Mill) board** over **CAN FD** using the **Cyphal** protocol.
2. Read **GPS** data from a u-blox receiver via `gpsd`.
3. Maintain a shared sense of time across the bus by publishing **`uavcan.time.Synchronization`**.
4. Aggregate sensor + GPS data once per second into a single **low-rate aggregate** Cyphal message and publish it on the bus.
5. Log every aggregate and EFM message it sees to a local **SQLite database** for post-flight analysis.

The flight computer is intentionally a *bus participant*, not a *bus master*: each board owns its own sensors and publishes its own subjects. The FC subscribes, aggregates, republishes, and logs.

### High-level data flow

```
Sensor board ──┐
EFM board ─────┼──► CAN FD (can0) ──► Cyphal subjects ──► FC services
GPS (gpsd) ────┘                                              │
                                                              ├─► leos.aggregate.LowRate (1 Hz)
                                                              ├─► uavcan.time.Synchronization (2 Hz)
                                                              └─► SQLite log (logs/leos_*.sqlite3)
```

---

## 2. Repository Layout

```
leos-S26-flight-computer/
├── fc/                          # Flight-computer Python package (the runtime code)
│   ├── __init__.py              # Adds dsdl_out/ to sys.path
│   ├── config.py                # Single source of truth for IDs, ports, rates, paths
│   ├── cyphal_node.py           # Helper: builds a started Cyphal node on can0
│   ├── log_writer.py            # Async SQLite log writer with batched flushing
│   └── services/
│       ├── time_master.py       # Service: GPS poll + time-sync + GPS Cyphal publish
│       ├── lowrate_aggregate.py # Service: subscribe, sample-and-hold, aggregate, publish
│       └── logger.py            # Service: subscribe to aggregate + EFM, log to SQLite
│
├── tools/                       # Operational + analysis utilities
│   ├── bootstrap_pi.sh          # One-shot Pi recovery: venv + deps + systemd units
│   ├── generate_dsdl.py         # Compile DSDL submodules into dsdl_out/
│   ├── inspect_fc_log.py        # Quick CLI inspector for a live or finished log
│   ├── analyze_fc_log.py        # Post-flight: CSVs, JSON summary, PNG plots
│   └── fake_gpsd_server.py      # Local fake gpsd for off-Pi development
│
├── systemd/                     # Service unit files installed on the Pi
│   ├── fc-time-master.service
│   ├── fc-lowrate-aggregate.service
│   └── fc-logger.service
│
├── external/                    # Git submodules (DSDL source definitions)
│   ├── leos_cyphal_types/         # LEOS-specific DSDL (sensors, EFM, aggregate, GPS fix)
│   └── public_regulated_data_types/  # Standard Cyphal/UAVCAN DSDL
│
├── dsdl_out/                    # Generated Python from DSDL (treat as build output)
├── logs/                        # SQLite log files written at runtime
│
├── README.md                    # Setup, regeneration, recovery
├── HANDOFF.md                   # Validated-vs-blocked status, recovery commands
├── LOG_INSPECTOR_README.md      # Usage of tools/inspect_fc_log.py
├── requirements.txt             # Python dependencies
└── startup.sh                   # Manual fallback launcher (only runs the logger)
```

---

## 3. Architecture

The FC runs **three independent Python services**, each as its own systemd unit and its own Cyphal node on the same `can0` bus.

### 3.1 The three services

| Service | Cyphal node ID | Cyphal node name | Rate | What it does |
|---|---|---|---|---|
| `fc-time-master` | 10 | `leos.time_master` | 2 Hz | Polls `gpsd`, publishes `uavcan.time.Synchronization` and `leos.gps.Fix` |
| `fc-lowrate-aggregate` | 11 | `leos.lowrate_agg` | 1 Hz | Subscribes to all sensors + GPS, builds and publishes `leos.aggregate.LowRate` |
| `fc-logger` | 12 | `leos.logger` | event-driven | Subscribes to the aggregate and to EFM ADC, serializes payloads, writes them to SQLite |

Each service is its own process so a crash in one (e.g., a GPS hiccup) does not take down logging or aggregation.

### 3.2 Cyphal subject layout (relevant to the FC)

Port IDs are **derived from the generated DSDL** (`fixed_port_id`) at runtime, so subject-ID changes in the DSDL definitions automatically propagate without code edits. See `fc/config.py`.

| Subject | DSDL type | Direction relative to FC |
|---|---|---|
| Time sync | `uavcan.time.Synchronization_1_0` (port 7168) | FC publishes |
| GPS fix | `leos.gps.Fix_0_1` | FC publishes |
| Aggregate | `leos.aggregate.LowRate_0_1` (port 1500) | FC publishes (from aggregator), FC subscribes (from logger) |
| EFM ADC | `leos.efm.ADC_0_2` (port 1400) | FC subscribes |
| Sensors | `leos.sensors.BME688_0_1`, `TSL2591_0_1`, `LTR390_0_1`, `PMSA003I_0_1` | FC subscribes |

### 3.3 Aggregation freshness gating

The aggregator caches the latest message per subject in memory (sample-and-hold). Every second, it builds a `LowRate` message and copies a sub-message **only if it is fresher than `STALE_MS = 2000 ms`**. If a sensor goes silent, its sub-message reverts to a default-constructed value, so downstream consumers always receive a complete message and can decide what to do with stale fields.

### 3.4 Logger backpressure policy

`fc/log_writer.py` is an async SQLite writer with a bounded in-memory queue. Flush triggers:

- queued bytes ≥ `LOG_BATCH_BYTES` (256 KiB), **or**
- ≥ `LOG_FLUSH_MS` (200 ms) since the last flush.

Under overload (queue ≥ 8192 records), the **oldest non-LowRateAggregate record is dropped first**. Aggregate records are treated as critical because they are the canonical 1 Hz "did the FC see anything" heartbeat. If every queued record is critical, it falls back to dropping the oldest record.

### 3.5 SQLite log schema

```sql
CREATE TABLE metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rx_mono_ns    INTEGER NOT NULL,   -- monotonic-clock receive time (ns)
    record_kind   INTEGER NOT NULL,   -- 1 = low_agg, 2 = efm, 3 = status
    port_id       INTEGER NOT NULL,
    payload       BLOB    NOT NULL,   -- raw serialized DSDL
    payload_len   INTEGER NOT NULL,
    payload_crc32 INTEGER NOT NULL    -- zlib.crc32 of payload
);
```

`metadata.start_mono_ns` records when the log was opened, so analysis tools can compute relative timestamps.

---

## 4. Configuration Reference (`fc/config.py`)

The whole FC is parameterized from `fc/config.py`. Important constants:

```python
CAN_INTERFACE = "can0"
CAN_MTU       = 64           # CAN FD

NODE_ID_TIME_MASTER = 10
NODE_ID_LOW_AGG     = 11
NODE_ID_LOGGER      = 12

LOWRATE_AGG_HZ = 1
TIME_SYNC_HZ   = 2
STALE_MS       = 2000

LOWRATE_DSDL = "leos.aggregate.LowRate_0_1"
GPS_FIX_DSDL = "leos.gps.Fix_0_1"
EFM_DSDL     = "leos.efm.ADC_0_2"

SENSORS = [
    ("leos.sensors.BME688_0_1",   "bme688"),
    ("leos.sensors.TSL2591_0_1",  "tsl2591"),
    ("leos.sensors.LTR390_0_1",   "ltr390"),
    ("leos.sensors.PMSA003I_0_1", "pmsa003i"),
]

LOG_DIR        = "<repo>/logs"
LOG_BATCH_BYTES = 256 * 1024
LOG_FLUSH_MS    = 200
```

> If the structure of an aggregate sub-message changes in DSDL, only the `SENSORS` list (field names) needs to track it; subject IDs come from DSDL automatically.

---

## 5. Setup

### 5.1 First-time clone

```bash
git clone --recurse-submodules https://github.com/VIP-LES/leos-S26-flight-computer.git
cd leos-S26-flight-computer
```

If you already cloned without submodules:

```bash
git submodule update --init --recursive
```

### 5.2 Python dependencies

```bash
python3 -m pip install -r requirements.txt
```

`requirements.txt`:

- `gpsd-py3` — Python client for the local `gpsd` daemon
- `pycyphal` — Cyphal/CAN runtime
- `nunavut`, `pydsdl` — DSDL → Python compilation
- `numpy` — required transitively

### 5.3 Generating DSDL Python

The runtime imports generated packages from `dsdl_out/`. Regenerate after pulling new DSDL revisions:

```bash
python3 tools/generate_dsdl.py
```

This compiles three root namespaces:

- `external/leos_cyphal_types/leos`
- `external/public_regulated_data_types/uavcan`
- `external/public_regulated_data_types/reg`

By default it deletes `dsdl_out/` first so stale generated files don't survive namespace changes. Use `--no-clean` for incremental generation.

### 5.4 Updating DSDL source revisions

```bash
git submodule update --remote --merge
.venv/bin/python tools/generate_dsdl.py
git add .
git commit -m "Update DSDL sources"
```

Submodules pin exact source revisions, so each repo commit records exactly which DSDL definitions produced `dsdl_out/`.

---

## 6. Deploying on the Raspberry Pi

The expected repo location on the FC is:

```
/home/leos-flight-computer/leos-S26-flight-computer
```

### 6.1 One-shot bootstrap / recovery

If the Pi power-cycles and comes back without the venv or systemd units:

```bash
cd /home/leos-flight-computer/leos-S26-flight-computer
chmod +x tools/bootstrap_pi.sh
./tools/bootstrap_pi.sh
```

`tools/bootstrap_pi.sh` will:

1. Recreate `.venv`.
2. Install `requirements.txt`.
3. Copy each unit from `systemd/` into `/etc/systemd/system/`.
4. Run `systemctl daemon-reload`.
5. `enable` and `restart` `fc-time-master`, `fc-lowrate-aggregate`, `fc-logger`.
6. Print the resulting `systemctl status`.

The script must be run **as the `leos-flight-computer` user** so the venv and repo stay correctly owned.

### 6.2 systemd unit details

All three units share this skeleton:

```ini
[Service]
Type=simple
User=leos-flight-computer
WorkingDirectory=/home/leos-flight-computer/leos-S26-flight-computer
ExecStart=/home/.../.venv/bin/python -m fc.services.<service_module>
Restart=always
RestartSec=1
```

Ordering:

- `fc-time-master` starts after `network.target`.
- `fc-lowrate-aggregate` `Requires=` and `After=` `fc-time-master`.
- `fc-logger` is ordered `After=` both of the above.

### 6.3 Service operations

Status:

```bash
sudo systemctl status fc-time-master fc-lowrate-aggregate fc-logger --no-pager -l
```

Recent logs:

```bash
sudo journalctl -u fc-time-master -n 50 --no-pager
sudo journalctl -u fc-lowrate-aggregate -n 50 --no-pager
sudo journalctl -u fc-logger -n 50 --no-pager
```

Live logs:

```bash
sudo journalctl -u fc-time-master -f
sudo journalctl -u fc-lowrate-aggregate -f
sudo journalctl -u fc-logger -f
```

Restart / stop:

```bash
sudo systemctl restart fc-time-master fc-lowrate-aggregate fc-logger
sudo systemctl stop    fc-time-master fc-lowrate-aggregate fc-logger
```

### 6.4 Networking and discovery

After boot, find the Pi's current IP locally:

```bash
hostname -I
ip addr show wlan0
```

`ssh` is enabled at boot, so once the Wi-Fi associates the FC is reachable.

### 6.5 GPS validation

Quick raw-stream check from `gpsd`:

```bash
gpspipe -w
```

Programmatic check from inside the FC environment:

```bash
cd /home/leos-flight-computer/leos-S26-flight-computer
.venv/bin/python - <<'PY'
import gpsd
gpsd.connect()
packet = gpsd.get_current()
print("mode:", packet.mode)
print("lat:", getattr(packet, "lat", None))
print("lon:", getattr(packet, "lon", None))
print("sats_valid:", getattr(packet, "sats_valid", None))
print("sats:", getattr(packet, "sats", None))
print("time:", getattr(packet, "time", None))
PY
```

### 6.6 CAN bring-up (manual, for triage)

```bash
sudo ip link set can0 down
sudo ip link set can0 txqueuelen 1000
sudo ip link set can0 up type can bitrate 1000000 dbitrate 4000000 fd on restart-ms 100
ip -details link show can0
```

The hardware is an MCP251xfd CAN-FD controller on SPI, exposed by Linux as `can0`.
Required link settings: nominal **1 Mbit/s**, data **4 Mbit/s**, FD on.

---

## 7. Tooling

### 7.1 `tools/inspect_fc_log.py` — Quick log inspector

A lightweight CLI that opens a log read-only and prints decoded human-readable rows. Safe to run while the logger is still writing to the same file.

It decodes:

- **Low-rate aggregate** rows (`port_id=1500`)
- **EFM ADC** rows (`port_id=1400`)

#### Common usage

From the repo root on the Pi:

```bash
cd /home/leos-flight-computer/leos-S26-flight-computer
```

Newest log automatically:

```bash
.venv/bin/python tools/inspect_fc_log.py
```

Filter by kind:

```bash
.venv/bin/python tools/inspect_fc_log.py --kind efm
.venv/bin/python tools/inspect_fc_log.py --kind low_rate
```

Limit rows (newest first, then printed oldest-first within the limit):

```bash
.venv/bin/python tools/inspect_fc_log.py --limit 10
.venv/bin/python tools/inspect_fc_log.py --kind efm --limit 5
```

Point at a directory (uses newest `.sqlite3` inside):

```bash
.venv/bin/python tools/inspect_fc_log.py logs
```

Point at a specific file:

```bash
.venv/bin/python tools/inspect_fc_log.py logs/leos_20260418_053249.sqlite3 --kind low_rate
```

List available log files:

```bash
ls -lh logs
```

#### Notes

- Database is opened **read-only**.
- Safe to use while the logger is still running.
- Supported `--kind` values: `both` (default), `efm`, `low_rate`.

### 7.2 `tools/analyze_fc_log.py` — Post-flight analyzer

Designed to run on a normal computer **after** copying a log off the FC. It:

1. Opens the SQLite database read-only.
2. Decodes every supported record into flat rows.
3. Prints a post-flight summary to the terminal.
4. Writes `low_rate_decoded.csv`, `efm_decoded.csv`, and `summary.json`.
5. Generates PNG plots if `matplotlib` is installed (skipped silently otherwise).

#### Default usage

```bash
.venv/bin/python tools/analyze_fc_log.py /path/to/log.sqlite3
```

You can also point at a directory and it will pick the newest `.sqlite3` file:

```bash
.venv/bin/python tools/analyze_fc_log.py /path/to/logs
```

#### Output location

By default, output goes next to the database in `<db_stem>_analysis/`. Example:

```
logs/leos_20260418_053249.sqlite3
  → logs/leos_20260418_053249_analysis/
      ├── summary.json
      ├── low_rate_decoded.csv
      ├── efm_decoded.csv
      ├── gps_altitude.png
      ├── gps_speed.png
      ├── gps_ground_track.png
      ├── bme_pressure.png
      ├── bme_temperature.png
      ├── bme_humidity.png
      └── efm_channels.png
```

#### Useful flags

| Flag | Effect |
|---|---|
| `--no-plots` | Skip plot generation |
| `--no-export` | Skip CSV/JSON export, print summary only |
| `--kind low_rate` / `--kind efm` | Only analyze one record type |
| `--list-fields` | Print decoded CSV column names and exit |
| `--output-dir /tmp/foo` | Write outputs somewhere else |

#### What the summary covers

- Record counts (decoded total, low-rate count, EFM count)
- Duration, observed publish rates (Hz), and max gap (s) for each kind
- Issue counters: `crc_mismatch`, `decode_failures`, `unknown_kind`
- First and last valid GPS fix (lat, lon, alt, UTC ISO)
- Stats blocks (count / min / max / mean / stdev) for:
  - GPS altitude, speed
  - BME688 temperature, pressure, humidity, altitude
  - EFM ADC1 ch1/ch4, ADC2 ch1/ch4

### 7.3 `tools/fake_gpsd_server.py` — Local GPS simulator

A drop-in fake of the `gpsd` daemon on `127.0.0.1:2947` for **off-Pi development**. It speaks just enough of the gpsd JSON protocol (`VERSION`, `?WATCH`, `?POLL`) for `gpsd-py3` to think it is talking to a real daemon.

```bash
python tools/fake_gpsd_server.py                # default: circle around LA
python tools/fake_gpsd_server.py --mode static  # one fixed point
python tools/fake_gpsd_server.py --mode linear  # straight-line flight
python tools/fake_gpsd_server.py --lat 33.7756 --lon -84.3963 --alt 320
```

Modes:

- **static** — emits the same lat/lon/alt forever
- **linear** — moves in a straight line at 15 m/s, heading 045°
- **circle** — orbits the origin point with ~500 m radius, 60 s period

This lets you run `fc.services.time_master` against synthetic GPS data without owning a real receiver.

### 7.4 `tools/generate_dsdl.py` — DSDL compiler

See [§5.3](#53-generating-dsdl-python). Wraps `pycyphal.dsdl.compile_all` with the three project root namespaces and writes the result to `dsdl_out/`. Also produces `dsdl_out/nunavut_support.py`, which the runtime uses to serialize / deserialize messages.

### 7.5 `tools/bootstrap_pi.sh`

See [§6.1](#61-one-shot-bootstrap--recovery).

---

## 8. Current Handoff Status

### 8.1 Validated

- The flight computer **boots successfully**.
- Local console login works.
- `ssh` works once Wi-Fi comes up.
- `ssh`, `fc-time-master`, `fc-lowrate-aggregate`, and `fc-logger` are **enabled on boot** and start automatically via `systemd`.
- The hostname/IP can be checked with `hostname` / `hostname -I`.
- Python environment and service deployment from `/home/leos-flight-computer/leos-S26-flight-computer` works.
- The GPS device path works:
  - `gpsd` sees a real u-blox receiver on `/dev/serial0`.
  - `gpspipe -w` streams `DEVICE`, `SKY`, and `TPV`.
  - The FC Python environment can query `gpsd` directly.
- The onboard log-writer path works:
  - Log files appear in `logs/` as `leos_*.sqlite3`.
  - Direct `LogWriter` testing successfully writes records.
- The CAN controller is present and Linux sees it as `can0` (MCP251xfd on SPI), and `can0` can be brought up at the expected CAN FD settings (1 Mbit/s nominal, 4 Mbit/s data).

### 8.2 Still to validate

- End-to-end Cyphal **reception** from the sensor board.
- End-to-end Cyphal **reception** from the EFM board.
- End-to-end **logging** of EFM data and of sensor + GPS aggregate data.
- Confirmed FC **publication** of sensor + GPS aggregate data over Cyphal to other nodes.
- A valid GPS positional fix.

### 8.3 Why those are blocked

- The main blocker is **CAN bus behavior**, not boot or service deployment.
- The FC services start, but when they attempt live CAN traffic the interface goes **error-passive / bus-off** and transmit attempts fail.
- `candump can0` showed traffic dominated by frames ending in source node-ID `0A`, which is most likely the FC's own local echo rather than confirmed external-node traffic.
- Because of that, end-to-end proof of receiving/logging sensor-board and EFM-board data was not completed and should be handed to the CAN owner.
- GPS hardware/software path is validated, but the receiver did not acquire a fix indoors:
  - `gpsd` / u-blox communication works.
  - `mode` remained `0/1`.
  - `lat` / `lon` stayed `0`.
  - `sats_valid` stayed `0`.

---

## 9. Common Workflows (Cheat Sheet)

### Bring the FC back from a cold reboot

```bash
ssh leos-flight-computer@<ip>
cd /home/leos-flight-computer/leos-S26-flight-computer
sudo systemctl status fc-time-master fc-lowrate-aggregate fc-logger --no-pager -l
```

If services aren't installed or the venv is missing:

```bash
./tools/bootstrap_pi.sh
```

### Watch live data flow

```bash
sudo journalctl -u fc-lowrate-aggregate -f
.venv/bin/python tools/inspect_fc_log.py --kind low_rate --limit 5
```

### Pull a flight log off the Pi for analysis

```bash
# on your laptop
scp leos-flight-computer@<ip>:/home/leos-flight-computer/leos-S26-flight-computer/logs/leos_*.sqlite3 ./
.venv/bin/python tools/analyze_fc_log.py ./leos_20260418_053249.sqlite3
open leos_20260418_053249_analysis/  # macOS; use xdg-open on Linux
```

### Develop with no real hardware

```bash
# terminal 1: fake GPS
python tools/fake_gpsd_server.py --mode circle

# terminal 2: time-master service
.venv/bin/python -m fc.services.time_master
```

(For CAN, you can use `vcan0` as a virtual SocketCAN bus on Linux, then point `CAN_INTERFACE` at it temporarily.)

### Update DSDL definitions

```bash
git submodule update --remote --merge
.venv/bin/python tools/generate_dsdl.py
git add .
git commit -m "Update DSDL sources"
```

---

## 10. Glossary

| Term | Meaning |
|---|---|
| **Cyphal** | Open lightweight protocol stack for hard real-time intravehicular networks. Used here over CAN FD. |
| **DSDL** | Data Structure Description Language. Cyphal's IDL; defines every message and its fixed port ID. |
| **Nunavut** | The DSDL → Python code generator. Produces `dsdl_out/`. |
| **Subject / port ID** | A numeric ID identifying a Cyphal "topic" on the bus. The FC reads its IDs from generated DSDL. |
| **Node ID** | A numeric ID identifying a Cyphal participant on the bus. Each FC service has its own (10/11/12). |
| **`gpsd`** | Linux daemon that owns the GPS serial device and exposes a JSON socket. The FC reads from it via `gpsd-py3`. |
| **EFM** | Electric Field Mill. A separate sensor board that publishes its own ADC channels on the bus. |
| **Low-rate aggregate** | The 1 Hz `leos.aggregate.LowRate` message bundling all slow sensors + GPS into one frame. |
| **Sample-and-hold** | The aggregator caches the latest message per subject and reuses it until a fresher one arrives or it goes stale (`STALE_MS`). |
