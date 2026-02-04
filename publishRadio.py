import asyncio
from pycyphal.application import make_node, NodeInfo
from pycyphal.transport.can import CANTransport
from pycyphal.transport.can.media.socketcan import SocketCANMedia
import sys
import os
import serial
import time

# --- DSDL Import ---
dsdl_out_path = os.path.join(os.path.expanduser('~'), 'LEOS-S26-FC/dsdl_out')
sys.path.insert(0, dsdl_out_path)

# Import from leos.sensors
from leos.sensors import UVLight_0_1, Temp_0_1, Pressure_0_1, AirQuality_0_1
# Import from leos.efm
from leos.efm import ADC4_0_1
# Import from leos.services
from leos.service import Cutdown_0_1

async def main():
    # --- Setup CAN transport ---
    media = SocketCANMedia("can0", mtu=64)
    transport = CANTransport(media=media, local_node_id=42)

    # --- Setup Node ---
    node = make_node(
        info=NodeInfo(name="my_pi_node"),
        transport=transport,
    )

    # --- Create subscribers ---
    clientCutdown = node.make_client(Cutdown_0_1, 10)
    subTemp = node.make_subscriber(Temp_0_1)
    subAir = node.make_subscriber(AirQuality_0_1)
    subPressure = node.make_subscriber(Pressure_0_1)
    subUV = node.make_subscriber(UVLight_0_1)
    subADC = node.make_subscriber(ADC4_0_1)