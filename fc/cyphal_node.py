#!/usr/bin/env python3
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Optional

from pycyphal.application import make_node, NodeInfo
from pycyphal.transport.can import CANTransport
from pycyphal.transport.can.media.socketcan import SocketCANMedia


@dataclass(frozen=True)
class Config:
    can_iface: str = "can0"
    can_mtu: int = 64
    node_id: int = 42
    node_name: str = "leos.flight_computer"


def _repo_root_from_this_file() -> str:
    # Expected layout: repo_root/fc/cyphal_node.py
    here = os.path.dirname(os.path.abspath(__file__))  # repo_root/fc
    return os.path.dirname(here)  # repo_root


def _add_repo_dsdl_out_to_syspath() -> str:
    """
    Add repo_root/dsdl_out to sys.path so generated DSDL Python packages are importable.
    Returns the resolved dsdl_out path.
    """
    dsdl_out = os.path.join(_repo_root_from_this_file(), "dsdl_out")
    dsdl_out = os.path.abspath(dsdl_out)

    if not os.path.isdir(dsdl_out):
        raise FileNotFoundError(
            f"Missing generated DSDL output directory: {dsdl_out}\n"
            "Expected repo_root/dsdl_out. Generate it or commit it before deployment."
        )

    if dsdl_out not in sys.path:
        sys.path.insert(0, dsdl_out)

    return dsdl_out


def _make_can_transport(cfg: Config) -> CANTransport:
    media = SocketCANMedia(cfg.can_iface, mtu=cfg.can_mtu)
    return CANTransport(media=media, local_node_id=cfg.node_id)


def make_started_node(cfg: Optional[Config] = None):
    """
    Create and start a Cyphal node using SocketCAN.
    Returns the started node instance.
    """
    cfg = cfg or Config()
    _add_repo_dsdl_out_to_syspath()

    transport = _make_can_transport(cfg)
    node = make_node(info=NodeInfo(name=cfg.node_name), transport=transport)
    node.start()
    return node


async def _run_forever() -> None:
    make_started_node()
    await asyncio.Event().wait()


def main() -> None:
    asyncio.run(_run_forever())


if __name__ == "__main__":
    main()