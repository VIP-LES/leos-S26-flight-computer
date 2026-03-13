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

## Updating DSDL source revisions

To move a submodule to a newer upstream commit:

```bash
cd external/leos_cyphal_types
git fetch
git checkout <commit-or-tag>
cd ../public_regulated_data_types
git fetch
git checkout <commit-or-tag>
cd ../..
python3 tools/generate_dsdl.py
git add .gitmodules external dsdl_out
git commit -m "Update DSDL sources"
```

Submodules pin exact source revisions, so the flight computer repository records exactly which DSDL definitions were used to generate `dsdl_out`.
