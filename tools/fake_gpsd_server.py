#!/usr/bin/env python3
"""
Fake gpsd server for testing.

Simulates a gpsd daemon on localhost:2947 that responds to POLL requests
with realistic GPS data. Works with the gpsd-py3 Python library.

Usage:
    python test_gpsd_server.py                  # Default: circle around LA
    python test_gpsd_server.py --mode static    # Stay at one fixed point
    python test_gpsd_server.py --mode linear    # Fly a straight line
    python test_gpsd_server.py --mode circle    # Circle pattern (default)
"""

import socket
import threading
import json
import time
import math
import argparse
from datetime import datetime, timezone

HOST = "127.0.0.1"
PORT = 2947


class GPSSimulator:
    """Generates fake GPS fixes in various flight patterns."""

    def __init__(self, mode="circle"):
        self.mode = mode
        self.start_time = time.time()

        # Origin point (Los Angeles area)
        self.origin_lat = 34.0522
        self.origin_lon = -118.2437
        self.base_alt = 300.0  # meters MSL

        # Circle params
        self.circle_radius_deg = 0.005  # ~500m radius
        self.circle_period = 60.0       # seconds per orbit

        # Linear params (heading roughly north-east)
        self.linear_speed_mps = 15.0    # m/s (~33 mph)
        self.linear_heading = 45.0      # degrees true north

        # Satellite simulation
        self.sats_visible = 12
        self.sats_used = 9

    def get_fix(self):
        """Return a (lat, lon, alt, speed, track, climb) tuple for the current moment."""
        elapsed = time.time() - self.start_time

        if self.mode == "static":
            return (
                self.origin_lat,
                self.origin_lon,
                self.base_alt,
                0.0,   # speed
                0.0,   # track
                0.0,   # climb
            )

        elif self.mode == "linear":
            dist_m = self.linear_speed_mps * elapsed
            heading_rad = math.radians(self.linear_heading)
            dlat = (dist_m * math.cos(heading_rad)) / 111320.0
            dlon = (dist_m * math.sin(heading_rad)) / (
                111320.0 * math.cos(math.radians(self.origin_lat))
            )
            alt = self.base_alt + 0.5 * math.sin(elapsed / 10.0)
            return (
                self.origin_lat + dlat,
                self.origin_lon + dlon,
                alt,
                self.linear_speed_mps,
                self.linear_heading,
                0.5 * math.cos(elapsed / 10.0),
            )

        else:  # circle
            angle = (2 * math.pi * elapsed) / self.circle_period
            lat = self.origin_lat + self.circle_radius_deg * math.cos(angle)
            lon = self.origin_lon + self.circle_radius_deg * math.sin(angle)
            alt = self.base_alt + 5.0 * math.sin(elapsed / 8.0)
            track = math.degrees(math.atan2(math.cos(angle), -math.sin(angle))) % 360
            circumference_m = 2 * math.pi * self.circle_radius_deg * 111320.0
            speed = circumference_m / self.circle_period
            climb = 5.0 * math.cos(elapsed / 8.0) / 8.0
            return (lat, lon, alt, speed, track, climb)

    def tpv_object(self):
        """Build a TPV object (used inside a POLL response)."""
        lat, lon, alt, speed, track, climb = self.get_fix()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        return {
            "class": "TPV",
            "device": "/dev/pts/0",
            "mode": 3,
            "time": now,
            "ept": 0.005,
            "lat": round(lat, 8),
            "lon": round(lon, 8),
            "alt": round(alt, 3),
            "epx": 5.0,
            "epy": 4.5,
            "epv": 9.8,
            "track": round(track, 4),
            "speed": round(speed, 4),
            "climb": round(climb, 4),
            "eps": 0.5,
            "epc": 1.0,
        }

    def sky_object(self):
        """Build a SKY object (used inside a POLL response)."""
        satellites = []
        for i in range(self.sats_visible):
            sat = {
                "PRN": i + 1,
                "el": 10 + (i * 7) % 80,
                "az": (i * 30) % 360,
                "ss": 20 + (i * 3) % 30,
                "used": i < self.sats_used,
            }
            satellites.append(sat)

        return {
            "class": "SKY",
            "device": "/dev/pts/0",
            "hdop": 0.9,
            "vdop": 1.5,
            "pdop": 1.7,
            "satellites": satellites,
        }

    def poll_response(self):
        """Build the full POLL response that gpsd-py3 expects."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        return {
            "class": "POLL",
            "time": now,
            "active": 1,
            "tpv": [self.tpv_object()],
            "sky": [self.sky_object()],
        }


def handle_client(conn, addr, simulator):
    """Handle a single gpsd client connection."""
    print(f"[+] Client connected: {addr}")

    try:
        stream = conn.makefile(mode="rw", buffering=1)

        # 1) Send VERSION greeting
        version = json.dumps({
            "class": "VERSION",
            "release": "3.25",
            "rev": "fake-test-server",
            "proto_major": 3,
            "proto_minor": 15,
        })
        stream.write(version + "\n")
        stream.flush()
        print("    Sent VERSION")

        # 2) Read and respond to commands in a loop
        while True:
            line = stream.readline()
            if not line:
                break  # client disconnected

            line = line.strip()
            if not line:
                continue

            print(f"    Recv: {line}")

            if line.startswith("?WATCH"):
                # Client sends ?WATCH={"enable":true}
                # Respond with DEVICES then WATCH (gpsd-py3 reads exactly 2 lines)
                devices_resp = json.dumps({
                    "class": "DEVICES",
                    "devices": [{
                        "class": "DEVICE",
                        "path": "/dev/pts/0",
                        "activated": "2025-01-01T00:00:00.000Z",
                        "driver": "SiRF",
                        "bps": 9600,
                    }],
                })
                watch_resp = json.dumps({
                    "class": "WATCH",
                    "enable": True,
                    "json": True,
                    "nmea": False,
                    "raw": 0,
                    "scaled": False,
                    "timing": False,
                    "split24": False,
                    "pps": False,
                })
                stream.write(devices_resp + "\n")
                stream.write(watch_resp + "\n")
                stream.flush()
                print("    Sent DEVICES + WATCH")

            elif line.startswith("?POLL"):
                # Client polls for current fix — send back a POLL response
                poll = json.dumps(simulator.poll_response())
                stream.write(poll + "\n")
                stream.flush()

    except (BrokenPipeError, ConnectionResetError, OSError) as e:
        print(f"[-] Client disconnected: {addr} ({e})")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Fake gpsd server for testing")
    parser.add_argument(
        "--mode",
        choices=["static", "linear", "circle"],
        default="circle",
        help="Movement pattern (default: circle)",
    )
    parser.add_argument(
        "--lat", type=float, default=34.0522, help="Origin latitude (default: 34.0522)"
    )
    parser.add_argument(
        "--lon", type=float, default=-118.2437, help="Origin longitude (default: -118.2437)"
    )
    parser.add_argument(
        "--alt", type=float, default=300.0, help="Base altitude in meters (default: 300)"
    )
    args = parser.parse_args()

    simulator = GPSSimulator(mode=args.mode)
    simulator.origin_lat = args.lat
    simulator.origin_lon = args.lon
    simulator.base_alt = args.alt

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(5)

    print(f"=== Fake gpsd server running on {HOST}:{PORT} ===")
    print(f"    Mode: {args.mode}")
    print(f"    Origin: ({args.lat}, {args.lon}) @ {args.alt}m")
    print(f"    Ctrl+C to stop\n")

    try:
        while True:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr, simulator), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\nShutting down fake gpsd server.")
    finally:
        server.close()


if __name__ == "__main__":
    main()