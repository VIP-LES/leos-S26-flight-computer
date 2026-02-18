import gpsd
import time
from datetime import datetime

def connect_to_gps():
    """Attempt to connect to the local gpsd daemon."""
    print("Connecting to local gpsd...")
    try:
        # Connects to localhost:2947 by default
        gpsd.connect()
        print("Connected successfully.")
        return True
    except Exception as e:
        print(f"Failed to connect to gpsd: {e}")
        return False

def read_comprehensive_gps_data():
    """Extracts every available data point from the GPS daemon."""
    if not connect_to_gps():
        return

    print("Listening for GPS data... (Waiting for 3D fix for full data)\n")

    while True:
        try:
            # Grab the latest packet from gpsd
            packet = gpsd.get_current()
            
            # Pack all available data into a structured dictionary
            # getattr() is used safely with fallbacks in case the GPS module 
            # hasn't locked onto a specific metric yet.
            gps_data = {
                # --- System & Status ---
                "system_time": time.time(),
                "gps_time": getattr(packet, 'time', 'Unknown'),
                "fix_mode": packet.mode, # 0=No value, 1=No fix, 2=2D, 3=3D
                
                # --- Position (TPV) ---
                "latitude": getattr(packet, 'lat', 0.0),
                "longitude": getattr(packet, 'lon', 0.0),
                "altitude_msl": getattr(packet, 'alt', 0.0), # Altitude in meters
                
                # --- Velocity & Movement (TPV) ---
                "speed_mps": getattr(packet, 'hspeed', 0.0), # Horizontal speed
                "climb_mps": getattr(packet, 'climb', 0.0),  # Vertical speed
                "track_true": getattr(packet, 'track', 0.0), # Course over ground (degrees)
                
                # --- Error Estimates (TPV) ---
                # These are crucial for avionics to know if the data is trustworthy
                "err_lat": packet.error.get('y', 0.0),   # Latitude error (meters)
                "err_lon": packet.error.get('x', 0.0),   # Longitude error (meters)
                "err_alt": packet.error.get('v', 0.0),   # Altitude error (meters)
                "err_speed": packet.error.get('s', 0.0), # Speed error (m/s)
                "err_climb": packet.error.get('c', 0.0), # Climb error (m/s)
                "err_time": packet.error.get('t', 0.0),  # Time error (seconds)
                
                # --- Satellite Info (SKY) ---
                "satellites_visible": getattr(packet, 'sats', 0),
                "satellites_used": getattr(packet, 'sats_valid', 0),
                "hdop": getattr(packet, 'hdop', 0.0), # Horizontal Dilution of Precision
                "vdop": getattr(packet, 'vdop', 0.0), # Vertical Dilution of Precision
                "pdop": getattr(packet, 'pdop', 0.0), # Position Dilution of Precision
            }

            # For demonstration, print the payload nicely
            if gps_data["fix_mode"] >= 2:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] FIX: {gps_data['fix_mode']}D | "
                      f"Sats: {gps_data['satellites_used']}/{gps_data['satellites_visible']} | "
                      f"Lat: {gps_data['latitude']:.6f} | Lon: {gps_data['longitude']:.6f} | "
                      f"Alt: {gps_data['altitude_msl']}m | Speed: {gps_data['speed_mps']}m/s")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Waiting for fix... (Sats in view: {gps_data['satellites_visible']})")

        except UserWarning:
            print("gpsd is running, but no data is coming from the module.")
        except Exception as e:
            print(f"Error reading GPS: {e}")
            
        # Poll at 10Hz (0.1s) to ensure we don't miss updates from fast GPS modules
        time.sleep(0.1) 

if __name__ == "__main__":
    read_comprehensive_gps_data()