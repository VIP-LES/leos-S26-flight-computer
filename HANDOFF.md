# Flight Computer Handoff

## Validated

- The flight computer boots successfully.
- Local console login works.
- SSH works when Wi-Fi comes up.
- `ssh`, `fc-time-master`, `fc-lowrate-aggregate`, and `fc-logger` are enabled on boot.
- After boot, the three flight-computer services are expected to launch automatically via `systemd`.
- The flight computer hostname/IP can be checked locally with:
  - `hostname`
  - `hostname -I`
- The Python environment and service deployment path are working from:
  - `/home/leos-flight-computer/leos-S26-flight-computer`
- The GPS device path is working:
  - `gpsd` sees a real u-blox receiver on `/dev/serial0`
  - `gpspipe -w` streams `DEVICE`, `SKY`, and `TPV`
  - the FC Python environment can query `gpsd` directly
- The onboard log-writer path works:
  - log files are created under `/home/leos-flight-computer/leos-S26-flight-computer/logs`
  - direct `LogWriter` testing successfully writes `.sqlite3` log files
- The CAN controller is present and Linux sees it as:
  - `can0`
  - MCP251xfd on SPI
- `can0` can be brought up manually at the expected CAN FD settings:
  - nominal bitrate `1000000`
  - data bitrate `4000000`

## Still To Validate

- End-to-end Cyphal reception from the sensor board.
- End-to-end Cyphal reception from the EFM board.
- End-to-end logging of:
  - EFM data
  - sensor + GPS aggregate data
- Confirmed FC publication of sensor + GPS aggregate data over Cyphal.
- Valid GPS positional fix.

## Why The Above Is Still Blocked

- The main blocker is CAN bus behavior, not FC boot or Python/service deployment.
- The FC services can start, but when they attempt live CAN traffic the interface becomes error-passive / bus-off and transmit attempts fail.
- `candump can0` showed traffic dominated by frames ending in source node-ID `0A`, which is likely the FC's own local echo rather than confirmed external-node traffic.
- Because of that, end-to-end proof of receiving/logging sensor-board and EFM-board data was not completed here and should be handed to the CAN owner.
- GPS hardware/software path is validated, but the receiver did not acquire a fix indoors:
  - `gpsd`/u-blox communication works
  - `mode` remained `0/1`
  - `lat/lon` stayed `0`
  - `sats_valid` stayed `0`

## Important Boot / Recovery Instructions

- The expected repo path on the FC is:
  - `/home/leos-flight-computer/leos-S26-flight-computer`
- The important services are:
  - `fc-time-master`
  - `fc-lowrate-aggregate`
  - `fc-logger`
- These services are enabled and should start automatically on boot.
- `ssh` is also enabled and should start automatically on boot.
- If Wi-Fi comes up, the easiest way to recover the current IP for SSH is:

```bash
hostname -I
ip addr show wlan0
```

- If the Python environment or unit installation needs to be rebuilt, run:

```bash
cd /home/leos-flight-computer/leos-S26-flight-computer
./tools/bootstrap_pi.sh
```

- That script recreates `.venv`, installs Python dependencies, installs the service units into `/etc/systemd/system/`, reloads `systemd`, enables the services, and restarts them.

## Important Service / Code Interaction Notes

- Check service state with:

```bash
sudo systemctl status fc-time-master fc-lowrate-aggregate fc-logger --no-pager -l
```

- Read recent logs with:

```bash
sudo journalctl -u fc-time-master -n 50 --no-pager
sudo journalctl -u fc-lowrate-aggregate -n 50 --no-pager
sudo journalctl -u fc-logger -n 50 --no-pager
```

- Follow logs live with:

```bash
sudo journalctl -u fc-time-master -f
sudo journalctl -u fc-lowrate-aggregate -f
sudo journalctl -u fc-logger -f
```

- Restart the FC services with:

```bash
sudo systemctl restart fc-time-master fc-lowrate-aggregate fc-logger
```

- Stop them with:

```bash
sudo systemctl stop fc-time-master fc-lowrate-aggregate fc-logger
```

- Manual GPS validation:

```bash
gpspipe -w
```

- Direct Python GPS validation from the FC environment:

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

- Manual CAN bring-up used during triage:

```bash
sudo ip link set can0 down
sudo ip link set can0 txqueuelen 1000
sudo ip link set can0 up type can bitrate 1000000 dbitrate 4000000 fd on restart-ms 100
ip -details link show can0
```

- Log files currently appear as `.sqlite3` files in:
  - `/home/leos-flight-computer/leos-S26-flight-computer/logs`
