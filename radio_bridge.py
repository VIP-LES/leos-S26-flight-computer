import asyncio
import json
import paho.mqtt.client as mqtt
from packet import build_from_latest

class Latest:
    def __init__(self):
        self.temp_c = None
        self.pressure_pa = None
        self.air_pm25_env = None
        self.air_aqi_pm25_us = None
        self.uv_uvi = None

latest = Latest()

# MQTT Callbacks
def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode()

    if topic == "telemetry/temp":
        latest.temp_c = float(payload)
    elif topic == "telemetry/pressure":
        latest.pressure_pa = float(payload)
    elif topic == "telemetry/uv":
        latest.uv_uvi = float(payload)
    elif topic == "telemetry/air":
        data = json.loads(payload)
        latest.air_pm25_env = data['pm25']
        latest.air_aqi_pm25_us = data['aqi']

# ---- LoRa driver stub ----
class LoRaRadio:
    def send(self, payload: bytes):
        print(f"Sending LoRa Packet: {payload.hex()}")

async def radio_publish_loop(radio, period_s=1.0):
    seq = 0
    while True:
        seq = (seq + 1) & 0xFFFF
        frame = build_from_latest(
            seq=seq,
            temp_c=latest.temp_c,
            pressure_pa=latest.pressure_pa,
            air_pm25_env=latest.air_pm25_env,
            air_aqi_pm25_us=latest.air_aqi_pm25_us,
            uv_uvi=latest.uv_uvi,
        )
        radio.send(frame)
        await asyncio.sleep(period_s)

async def main():
    client = mqtt.Client()
    client.on_message = on_message
    client.connect("localhost", 1883)
    client.subscribe("telemetry/#")
    client.loop_start() # Run MQTT in background thread

    radio = LoRaRadio()
    print("MQTT -> Radio Bridge Started")
    await radio_publish_loop(radio)

if __name__ == "__main__":
    asyncio.run(main())