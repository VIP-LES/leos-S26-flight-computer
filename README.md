# LEOS S26 Flight Computer

This repository tracks its DSDL source definitions as git submodules and compiles them into the checked-in `dsdl_out/` Python package tree used at runtime.

## Repository layout

- `external/leos_cyphal_types`: vendor-specific LEOS DSDL source definitions.
- `external/public_regulated_data_types`: standard regulated Cyphal DSDL source definitions.
- `dsdl_out`: generated Python output, including `nunavut_support.py`.

The flight computer imports generated packages from `dsdl_out`, so `dsdl_out` should be treated as generated build output rather than hand-edited source.

## Initial setup

Clone the repository and initialize submodules:

```bash
git clone --recurse-submodules https://github.com/VIP-LES/leos-S26-flight-computer.git
cd leos-S26-flight-computer
```

If the repository is already cloned:

```bash
git submodule update --init --recursive
```

Install Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

## Raspberry Pi recovery / redeploy

If the flight computer power-cycles and comes back without the Python virtualenv
or current systemd units installed, the quickest recovery path on the Pi is:

```bash
cd /home/leos-flight-computer/leos-S26-flight-computer
chmod +x tools/bootstrap_pi.sh
./tools/bootstrap_pi.sh
```

This script:

- recreates `.venv`
- installs `requirements.txt`
- copies the service units from `systemd/` into `/etc/systemd/system/`
- reloads `systemd`
- enables and restarts the three flight-computer services

To find the current IP address locally on the Pi after boot, use:

```bash
hostname -I
ip addr show wlan0
```

To validate the local GPS path as soon as the network is back, use:

```bash
gpspipe -w
sudo journalctl -u fc-time-master -n 50 --no-pager
```

## Regenerating DSDL output

Regenerate all Python DSDL packages from the submodules:

```bash
python3 tools/generate_dsdl.py
```

This script:

- compiles `external/leos_cyphal_types/leos`
- compiles `external/public_regulated_data_types/uavcan`
- compiles `external/public_regulated_data_types/reg`
- writes the generated output into `dsdl_out`
- regenerates `dsdl_out/nunavut_support.py` alongside the generated packages

The script deletes `dsdl_out` before regeneration by default so stale generated files do not survive namespace changes. Use `python3 tools/generate_dsdl.py --no-clean` only if you explicitly want incremental generation.

The runtime service configuration in [`fc/config.py`] reads fixed port IDs from the generated DSDL classes, so subject-ID changes usually do not require service code edits. If the structure of aggregate messages changes, the sensor-to-field mapping in `fc/config.py` still needs to be updated to match the new generated type layout.

## Updating DSDL source revisions

For normal updates, refresh both submodules to the latest commit on their tracked branch and regenerate:

```bash
git submodule update --remote --merge
.my-venv/bin/python tools/generate_dsdl.py
git add .
git commit -m "Update DSDL sources"
```

Use `git submodule update --init --recursive` for first-time setup after clone. Use `git submodule update --remote --merge` when you want to pull newer upstream DSDL revisions into this repository.

If you need to pin a specific upstream commit or tag instead of following the tracked branch, check out that revision inside the submodule explicitly, then regenerate and commit the updated submodule pointer.

Submodules pin exact source revisions in this repository, so each commit records exactly which DSDL definitions were used to generate `dsdl_out`.
