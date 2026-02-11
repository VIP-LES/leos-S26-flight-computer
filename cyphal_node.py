import asyncio
import os
import sys
import json
import paho.mqtt.client as mqtt
from pycyphal.application import make_node, NodeInfo
from pycyphal.transport.can import CANTransport
from pycyphal.transport.can.media.socketcan import SocketCANMedia

# ---- DSDL import ----
dsdl_out_path = os.path.join(os.path.expanduser("~"), "LEOS-S26-FC/dsdl_out")
sys.path.insert(0, dsdl_out_path)
from leos.sensors import UVLight_0_1, Temp_0_1, Pressure_0_1, AirQuality_0_1

# MQTT Setup
mqtt_client = mqtt.Client()
mqtt_client.connect("localhost", 1883)

async def handle_temp(sub):
    while True:
        msg, _ = await sub.receive()
        val = msg.temperature.kelvin - 273.15
        mqtt_client.publish("telemetry/temp", val)

async def handle_pressure(sub):
    while True:
        msg, _ = await sub.receive()
        mqtt_client.publish("telemetry/pressure", msg.pressure.pascal)

async def handle_air(sub):
    while True:
        msg, _ = await sub.receive()
        data = {"pm25": int(msg.pm25_env), "aqi": int(msg.aqi_pm25_us)}
        mqtt_client.publish("telemetry/air", json.dumps(data))

async def handle_uv(sub):
    while True:
        msg, _ = await sub.receive()
        mqtt_client.publish("telemetry/uv", float(msg.uvi))

async def main():
    media = SocketCANMedia("can0", mtu=64)
    transport = CANTransport(media=media, local_node_id=42)
    node = make_node(info=NodeInfo(name="cyphal_bridge"), transport=transport)

    node.start()
    print("Cyphal -> MQTT Bridge Started")

    await asyncio.gather(
        handle_temp(node.make_subscriber(Temp_0_1)),
        handle_pressure(node.make_subscriber(Pressure_0_1)),
        handle_air(node.make_subscriber(AirQuality_0_1)),
        handle_uv(node.make_subscriber(UVLight_0_1)),
    )

if __name__ == "__main__":
    asyncio.run(main())