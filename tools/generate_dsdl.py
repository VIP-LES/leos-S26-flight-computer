#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "dsdl_out"
ROOT_NAMESPACES = (
    REPO_ROOT / "external" / "leos_cyphal_types" / "leos",
    REPO_ROOT / "external" / "public_regulated_data_types" / "uavcan",
    REPO_ROOT / "external" / "public_regulated_data_types" / "reg",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile Cyphal DSDL namespaces from git submodules into dsdl_out."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help=f"Generated Python output directory. Defaults to {OUTPUT_DIR}.",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not delete the output directory before regenerating.",
    )
    return parser.parse_args()


def _load_pycyphal_dsdl():
    try:
        return importlib.import_module("pycyphal.dsdl")
    except ModuleNotFoundError as ex:
        raise SystemExit(
            "pycyphal is not installed. Run `python3 -m pip install -r requirements.txt` first."
        ) from ex


def _validate_roots(roots: tuple[Path, ...]) -> None:
    missing = [path for path in roots if not path.is_dir()]
    if missing:
        details = "\n".join(f" - {path}" for path in missing)
        raise SystemExit(
            "Missing DSDL root namespaces. Initialize submodules first:\n"
            "  git submodule update --init --recursive\n"
            f"{details}"
        )


def _clean_output_dir(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def main() -> int:
    args = _parse_args()
    output_dir = args.output_dir.resolve()
    roots = tuple(path.resolve() for path in ROOT_NAMESPACES)

    _validate_roots(roots)

    if not args.no_clean:
        _clean_output_dir(output_dir)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    pycyphal_dsdl = _load_pycyphal_dsdl()

    compiled = pycyphal_dsdl.compile_all(
        roots,
        output_directory=output_dir,
        allow_unregulated_fixed_port_id=True,
    )

    print(f"Generated {len(compiled)} DSDL namespace(s) into {output_dir}")
    for package in compiled:
        print(f" - {package.name}: {package.path}")

    support_module = output_dir / "nunavut_support.py"
    if support_module.exists():
        print(f" - support module: {support_module}")
    else:
        print("Warning: nunavut_support.py was not generated", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
