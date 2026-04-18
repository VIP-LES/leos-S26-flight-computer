# Flight Computer Log Inspector

This repo includes a small SQLite log inspection tool:

- [tools/inspect_fc_log.py](/Users/sebmar496/Desktop/leos/leos-S26-flight-computer/tools/inspect_fc_log.py)

It decodes flight-computer log rows into human-readable values for:

- low-rate aggregate data (`port_id=1500`)
- EFM ADC data (`port_id=1400`)

This is meant as a quick sanity-check tool while testing. It reads the SQLite log directly and can be used while the logger is still running.

## From The Flight Computer Repo Root

```bash
cd /home/leos-flight-computer/leos-S26-flight-computer
```

## Basic Usage

Use the newest log automatically:

```bash
.venv/bin/python tools/inspect_fc_log.py
```

Show only EFM rows from the newest log:

```bash
.venv/bin/python tools/inspect_fc_log.py --kind efm
```

Show only low-rate rows from the newest log:

```bash
.venv/bin/python tools/inspect_fc_log.py --kind low_rate
```

Show only the newest 10 rows:

```bash
.venv/bin/python tools/inspect_fc_log.py --limit 10
```

Show only the newest 5 EFM rows:

```bash
.venv/bin/python tools/inspect_fc_log.py --kind efm --limit 5
```

Show only the newest 5 low-rate rows:

```bash
.venv/bin/python tools/inspect_fc_log.py --kind low_rate --limit 5
```

## Point At The Logs Directory Explicitly

Use the newest `.sqlite3` inside `logs/`:

```bash
.venv/bin/python tools/inspect_fc_log.py logs
```

Only EFM:

```bash
.venv/bin/python tools/inspect_fc_log.py logs --kind efm
```

Only low-rate:

```bash
.venv/bin/python tools/inspect_fc_log.py logs --kind low_rate
```

## Point At One Specific SQLite File

```bash
.venv/bin/python tools/inspect_fc_log.py logs/leos_20260418_053249.sqlite3
```

Only EFM from one specific file:

```bash
.venv/bin/python tools/inspect_fc_log.py logs/leos_20260418_053249.sqlite3 --kind efm
```

Only low-rate from one specific file:

```bash
.venv/bin/python tools/inspect_fc_log.py logs/leos_20260418_053249.sqlite3 --kind low_rate
```

## Useful Helper Command

List available log files first:

```bash
ls -lh logs
```

Then inspect a specific file:

```bash
.venv/bin/python tools/inspect_fc_log.py logs/<filename>.sqlite3
```

## Notes

- The tool opens the database read-only.
- It is safe to use while the logger is still running.
- If you pass a directory, it automatically picks the newest `.sqlite3`.
- Supported filter values are:
  - `both`
  - `efm`
  - `low_rate`
