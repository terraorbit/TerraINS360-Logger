"""
TerraFusion - GNSS Manager
===========================
Manages TCP connection to SG7 receiver, parses NMEA sentences,
maintains real-time GNSS state.
"""

import socket
import threading
import time
import re
import math
from datetime import datetime

FIX_NAMES = {
    0: "Invalid", 1: "Single", 2: "DGPS", 3: "PPS",
    4: "RTK Fixed", 5: "RTK Float", 6: "Estimated",
}

FIX_COLORS = {
    0: "#ef4444", 1: "#f97316", 2: "#eab308", 3: "#06b6d4",
    4: "#22c55e", 5: "#14b8a6", 6: "#6b7280",
}


def nmea_checksum(sentence):
    """Validate NMEA checksum."""
    if "*" not in sentence:
        return True
    data, chk = sentence.split("*", 1)
    data = data.lstrip("$")
    calc = 0
    for c in data:
        calc ^= ord(c)
    try:
        return calc == int(chk[:2], 16)
    except ValueError:
        return False


class GNSSState:
    """Thread-safe GNSS state container."""

    def __init__(self):
        self.lock = threading.Lock()
        self.data = {
            "connected": False,
            "timestamp_utc": "",
            "date_utc": "",
            "latitude": 0.0,
            "longitude": 0.0,
            "lat_display": "",
            "lon_display": "",
            "altitude": 0.0,
            "geoid_height": 0.0,
            "fix_quality": 0,
            "fix_name": "No Data",
            "fix_color": "#6b7280",
            "satellites_used": 0,
            "hdop": 0.0,
            "pdop": 0.0,
            "vdop": 0.0,
            "h_precision": 0.0,
            "v_precision": 0.0,
            "speed_kmh": 0.0,
            "course": 0.0,
            "update_count": 0,
            "last_update": "",
            "receiver_sn": "",
            "fix_history": [],
        }

    def update(self, **kwargs):
        with self.lock:
            self.data.update(kwargs)

    def get(self, key=None):
        with self.lock:
            if key:
                return self.data.get(key)
            return dict(self.data)

    def snapshot(self):
        with self.lock:
            d = dict(self.data)
            d["fix_history"] = list(self.data["fix_history"][-200:])
            # Add data age for frontend staleness detection
            lu = self.data.get("_last_data_epoch", 0)
            d["data_age_s"] = round(time.time() - lu, 1) if lu else -1
            return d


def _lat_to_dd(raw, ns):
    if not raw:
        return 0.0
    d = int(raw[:2])
    m = float(raw[2:])
    dd = d + m / 60.0
    return -dd if ns == "S" else dd


def _lon_to_dd(raw, ew):
    if not raw:
        return 0.0
    d = int(raw[:3])
    m = float(raw[3:])
    dd = d + m / 60.0
    return -dd if ew == "W" else dd


def _dd_to_dms(dd, is_lat=True):
    ns = ("N" if dd >= 0 else "S") if is_lat else ("E" if dd >= 0 else "W")
    dd = abs(dd)
    d = int(dd)
    m = int((dd - d) * 60)
    s = (dd - d - m / 60) * 3600
    return f"{d}\u00b0{m:02d}'{s:06.3f}\"{ns}"


# Seconds without data before forcing reconnect
STALE_TIMEOUT = 15


class GNSSManager:
    """Manages SG7 TCP NMEA connection."""

    def __init__(self, state: GNSSState):
        self.state = state
        self.host = "192.168.1.1"
        self.port = 1212
        self._running = False
        self._thread = None
        self._sock = None
        self._last_gga = ""
        self._callbacks = []
        self._last_data_time = 0.0

    def configure(self, host, port):
        self.host = host
        self.port = port

    def on_position(self, callback):
        """Register callback for each position update."""
        self._callbacks.append(callback)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except:
                pass

    def get_last_gga(self):
        return self._last_gga

    def _reader_loop(self):
        while self._running:
            try:
                print(f"[GNSS] Connecting to {self.host}:{self.port}...")
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._sock.settimeout(10)
                self._sock.connect((self.host, self.port))
                self.state.update(connected=True)
                print("[GNSS] Connected!")

                buffer = ""
                self._last_data_time = time.time()
                while self._running:
                    try:
                        chunk = self._sock.recv(4096)
                        if not chunk:
                            print("[GNSS] Connection closed by receiver")
                            break
                        self._last_data_time = time.time()
                        self.state.update(_last_data_epoch=self._last_data_time)
                        text = chunk.decode("ascii", errors="replace")
                        buffer += text

                        sentences = re.findall(
                            r"(\$[A-Z]{2}[A-Z]{2,4},[^\r\n]*)", buffer
                        )
                        last_nl = buffer.rfind("\n")
                        if last_nl >= 0:
                            buffer = buffer[last_nl + 1:]

                        for s in sentences:
                            self._parse(s)

                    except socket.timeout:
                        # Check if data is stale (no data for STALE_TIMEOUT seconds)
                        age = time.time() - self._last_data_time
                        if age > STALE_TIMEOUT:
                            print(f"[GNSS] No data for {age:.0f}s - reconnecting...")
                            break
                        continue

            except Exception as e:
                print(f"[GNSS] Connection error: {e}")

            finally:
                self.state.update(connected=False)
                try:
                    self._sock.close()
                except:
                    pass

            if self._running:
                time.sleep(3)

    def _parse(self, sentence):
        fields = sentence.split(",")
        msg = fields[0][3:]  # GGA, GST, RMC, GSA...

        try:
            if msg == "GGA":
                self._parse_gga(fields, sentence)
            elif msg == "GST":
                self._parse_gst(fields)
            elif msg == "RMC":
                self._parse_rmc(fields)
            elif msg == "GSA":
                self._parse_gsa(fields)
        except (IndexError, ValueError):
            pass

    def _parse_gga(self, f, raw):
        t = f[1]
        ts = f"{t[:2]}:{t[2:4]}:{t[4:]}" if len(t) >= 6 else ""

        lat = _lat_to_dd(f[2], f[3])
        lon = _lon_to_dd(f[4], f[5])
        fix = int(f[6]) if f[6] else 0
        sats = int(f[7]) if f[7] else 0
        hdop = float(f[8]) if f[8] else 0.0
        alt = float(f[9]) if f[9] else 0.0
        geoid = float(f[11]) if f[11] else 0.0

        self._last_gga = raw

        with self.state.lock:
            hist = self.state.data["fix_history"]
            hist.append(fix)
            if len(hist) > 300:
                self.state.data["fix_history"] = hist[-300:]

        self.state.update(
            timestamp_utc=ts,
            latitude=lat,
            longitude=lon,
            lat_display=_dd_to_dms(lat, True),
            lon_display=_dd_to_dms(lon, False),
            fix_quality=fix,
            fix_name=FIX_NAMES.get(fix, f"Unknown({fix})"),
            fix_color=FIX_COLORS.get(fix, "#6b7280"),
            satellites_used=sats,
            hdop=hdop,
            altitude=alt,
            geoid_height=geoid,
            update_count=self.state.get("update_count") + 1,
            last_update=datetime.now().strftime("%H:%M:%S.%f")[:-3],
        )

        for cb in self._callbacks:
            try:
                cb(self.state.snapshot())
            except:
                pass

    def _parse_gst(self, f):
        lat_err = float(f[6]) if f[6] else 0.0
        lon_err = float(f[7]) if f[7] else 0.0
        alt_err = float(f[8].split("*")[0]) if f[8] else 0.0
        h_prec = math.sqrt(lat_err**2 + lon_err**2)
        self.state.update(h_precision=h_prec, v_precision=alt_err)

    def _parse_rmc(self, f):
        speed = float(f[7]) * 1.852 if f[7] else 0.0
        course = float(f[8]) if len(f) > 8 and f[8] else 0.0
        date = f[9] if len(f) > 9 and f[9] else ""
        if date and len(date) == 6:
            date = f"{date[0:2]}/{date[2:4]}/20{date[4:6]}"
        self.state.update(speed_kmh=speed, course=course, date_utc=date)

    def _parse_gsa(self, f):
        try:
            pdop = float(f[-3]) if f[-3] else 0.0
            hdop = float(f[-2]) if f[-2] else 0.0
            vdop = float(f[-1].split("*")[0]) if f[-1] else 0.0
            self.state.update(pdop=pdop, vdop=vdop)
        except (ValueError, IndexError):
            pass
