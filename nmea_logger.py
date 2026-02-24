"""
TerraFusion - NMEA Logger
==========================
Records NMEA data to files with configurable Hz rate.
Supports NMEA raw, CSV, and both formats.
"""

import threading
import time
import math
import csv
from pathlib import Path
from datetime import datetime


class NMEALogger:
    """Records GNSS data to files."""

    def __init__(self, gnss_state):
        self.gnss_state = gnss_state
        self.output_dir = Path(".")
        self.hz = 5
        self.fmt = "both"  # nmea, csv, both
        self.prefix = "TF"
        self.lever_arm = {"x": 0.0, "y": 0.0, "z": 0.0}

        self._running = False
        self._thread = None
        self._nmea_file = None
        self._csv_file = None
        self._csv_writer = None
        self._session_name = ""
        self._record_count = 0
        self._start_time = None
        self._lock = threading.Lock()

    def configure(self, output_dir, hz=5, fmt="both", prefix="TF", lever_arm=None):
        self.output_dir = Path(output_dir) if output_dir else Path(".")
        self.hz = max(1, min(hz, 20))
        self.fmt = fmt
        self.prefix = prefix
        if lever_arm:
            self.lever_arm = lever_arm

    def start(self):
        if self._running:
            return {"error": "Already recording"}

        self.output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._session_name = f"{self.prefix}_{ts}"
        self._record_count = 0
        self._start_time = time.time()

        # Open files
        if self.fmt in ("nmea", "both"):
            nmea_path = self.output_dir / f"{self._session_name}.nmea"
            self._nmea_file = open(nmea_path, "w", newline="\n")

        if self.fmt in ("csv", "both"):
            csv_path = self.output_dir / f"{self._session_name}.csv"
            self._csv_file = open(csv_path, "w", newline="")
            self._csv_writer = csv.writer(self._csv_file)
            self._csv_writer.writerow([
                "Timestamp_UTC", "Date_UTC",
                "Latitude", "Longitude",
                "Ellipsoid_Height_m", "Geoid_Height_m",
                "Fix_Quality", "Fix_Name",
                "Satellites_Used", "HDOP", "PDOP", "VDOP",
                "H_Precision_m", "V_Precision_m",
                "Speed_kmh", "Course_deg",
                "Lever_X_m", "Lever_Y_m", "Lever_Z_m",
                "Corrected_Alt_m",
            ])

        self._running = True
        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()

        return {
            "status": "recording",
            "session": self._session_name,
            "hz": self.hz,
            "format": self.fmt,
        }

    def stop(self):
        if not self._running:
            return {"error": "Not recording"}

        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

        # Close files
        if self._nmea_file:
            self._nmea_file.close()
            self._nmea_file = None
        if self._csv_file:
            self._csv_file.close()
            self._csv_file = None
            self._csv_writer = None

        elapsed = time.time() - self._start_time if self._start_time else 0

        return {
            "status": "stopped",
            "session": self._session_name,
            "records": self._record_count,
            "duration_s": round(elapsed, 1),
        }

    def get_status(self):
        with self._lock:
            elapsed = time.time() - self._start_time if self._start_time and self._running else 0
            return {
                "recording": self._running,
                "session": self._session_name,
                "records": self._record_count,
                "hz": self.hz,
                "format": self.fmt,
                "duration_s": round(elapsed, 1),
                "output_dir": str(self.output_dir),
            }

    def _record_loop(self):
        interval = 1.0 / self.hz
        last_record = 0

        while self._running:
            now = time.time()
            if now - last_record < interval:
                time.sleep(0.01)
                continue

            last_record = now
            snap = self.gnss_state.get()

            if snap["fix_quality"] == 0:
                continue  # Skip invalid fixes

            with self._lock:
                self._record_count += 1

            # Write NMEA raw
            if self._nmea_file and snap.get("_raw_gga"):
                self._nmea_file.write(snap["_raw_gga"] + "\n")
                self._nmea_file.flush()

            # Write CSV
            if self._csv_writer:
                corrected_alt = snap["altitude"] - self.lever_arm.get("z", 0.0)
                self._csv_writer.writerow([
                    snap["timestamp_utc"],
                    snap.get("date_utc", ""),
                    f"{snap['latitude']:.10f}",
                    f"{snap['longitude']:.10f}",
                    f"{snap['altitude']:.4f}",
                    f"{snap['geoid_height']:.4f}",
                    snap["fix_quality"],
                    snap["fix_name"],
                    snap["satellites_used"],
                    f"{snap['hdop']:.2f}",
                    f"{snap.get('pdop', 0.0):.2f}",
                    f"{snap.get('vdop', 0.0):.2f}",
                    f"{snap['h_precision']:.4f}",
                    f"{snap['v_precision']:.4f}",
                    f"{snap['speed_kmh']:.2f}",
                    f"{snap.get('course', 0.0):.2f}",
                    f"{self.lever_arm['x']:.3f}",
                    f"{self.lever_arm['y']:.3f}",
                    f"{self.lever_arm['z']:.3f}",
                    f"{corrected_alt:.4f}",
                ])
                self._csv_file.flush()
