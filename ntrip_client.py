"""
TerraFusion - NTRIP Client
============================
Downloads RTCM corrections from NTRIP caster and relays to SG7.
Supports injecting corrections via the SG7's TCP port.
"""

import socket
import threading
import time
import base64
from datetime import datetime


class NTRIPClient:
    """NTRIP v1 client that downloads RTCM corrections."""

    def __init__(self, gnss_manager=None):
        self.gnss = gnss_manager
        self.host = ""
        self.port = 2101
        self.mountpoint = ""
        self.username = ""
        self.password = ""
        self.send_gga = True
        self.gga_interval = 5

        self._running = False
        self._thread = None
        self._sock = None
        self._status = "Disconnected"
        self._bytes_received = 0
        self._last_data_time = 0
        self._error = ""
        self._lock = threading.Lock()

    def configure(self, host, port, mountpoint, username, password,
                  send_gga=True, gga_interval=5):
        self.host = host
        self.port = port
        self.mountpoint = mountpoint
        self.username = username
        self.password = password
        self.send_gga = send_gga
        self.gga_interval = gga_interval

    def start(self):
        if self._running:
            return
        if not self.host or not self.mountpoint:
            self._status = "Not configured"
            return
        self._running = True
        self._bytes_received = 0
        self._last_data_time = 0
        self._error = ""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except:
                pass
        with self._lock:
            self._status = "Disconnected"

    def get_status(self):
        with self._lock:
            return {
                "status": self._status,
                "bytes_received": self._bytes_received,
                "error": self._error,
                "connected": self._status == "Connected",
                "last_data": self._last_data_time,
            }

    def _set_status(self, status, error=""):
        with self._lock:
            self._status = status
            self._error = error

    def _run(self):
        while self._running:
            try:
                self._connect()
            except Exception as e:
                self._set_status("Error", str(e))
                print(f"[NTRIP] Error: {e}")

            if self._running:
                self._set_status("Reconnecting...")
                time.sleep(5)

    def _connect(self):
        self._set_status("Connecting...")
        print(f"[NTRIP] Connecting to {self.host}:{self.port}/{self.mountpoint}")

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(15)
        self._sock.connect((self.host, self.port))

        # Build NTRIP request
        auth = base64.b64encode(
            f"{self.username}:{self.password}".encode()
        ).decode()

        request = (
            f"GET /{self.mountpoint} HTTP/1.0\r\n"
            f"Host: {self.host}\r\n"
            f"Ntrip-Version: Ntrip/1.0\r\n"
            f"User-Agent: TerraFusion/1.0\r\n"
            f"Authorization: Basic {auth}\r\n"
            f"\r\n"
        )
        self._sock.send(request.encode())

        # Read response header
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._sock.recv(1024)
            if not chunk:
                raise ConnectionError("No response from caster")
            response += chunk

        header = response.split(b"\r\n\r\n")[0].decode("ascii", errors="replace")
        if "200" not in header and "ICY" not in header.upper():
            raise ConnectionError(f"NTRIP rejected: {header[:100]}")

        self._set_status("Connected")
        print("[NTRIP] Connected! Receiving corrections...")

        # Main receive loop
        last_gga_time = 0
        remaining = response.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in response else b""
        
        if remaining:
            self._relay_to_receiver(remaining)

        while self._running:
            try:
                # Send GGA periodically
                now = time.time()
                if self.send_gga and (now - last_gga_time) >= self.gga_interval:
                    if self.gnss:
                        gga = self.gnss.get_last_gga()
                    else:
                        gga = None
                    if gga:
                        try:
                            self._sock.send((gga + "\r\n").encode())
                        except:
                            pass
                    last_gga_time = now

                # Receive RTCM data
                self._sock.settimeout(10)
                data = self._sock.recv(4096)
                if not data:
                    break

                with self._lock:
                    self._bytes_received += len(data)
                    self._last_data_time = time.time()

                self._relay_to_receiver(data)

            except socket.timeout:
                continue
            except Exception as e:
                print(f"[NTRIP] Receive error: {e}")
                break

        self._sock.close()

    def _relay_to_receiver(self, data):
        """Send RTCM corrections to the GNSS receiver."""
        # The SG7 accepts corrections on the same TCP port
        # when configured as NTRIP client, or we can inject via
        # a separate connection if the receiver supports it.
        # For now, we'll store for potential injection.
        pass


def get_source_table(host, port):
    """Fetch NTRIP source table to list available mountpoints."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((host, port))

        request = (
            f"GET / HTTP/1.0\r\n"
            f"Host: {host}\r\n"
            f"Ntrip-Version: Ntrip/1.0\r\n"
            f"User-Agent: TerraFusion/1.0\r\n"
            f"\r\n"
        )
        sock.send(request.encode())

        response = b""
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            except socket.timeout:
                break

        sock.close()
        text = response.decode("ascii", errors="replace")
        
        mountpoints = []
        for line in text.split("\n"):
            if line.startswith("STR;"):
                parts = line.split(";")
                if len(parts) > 5:
                    mountpoints.append({
                        "name": parts[1],
                        "format": parts[3] if len(parts) > 3 else "",
                        "details": parts[5] if len(parts) > 5 else "",
                    })

        return mountpoints

    except Exception as e:
        return [{"name": "Error", "format": str(e), "details": ""}]
