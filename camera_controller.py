"""
TerraFusion - Camera Controller (WiFi)
========================================
Controls Insta360 ONE RS 1-Inch 360 via WiFi HTTP API.
Uses the Open Spherical Camera (OSC) protocol over the camera's WiFi hotspot.

Setup:
  1. Turn on Insta360 and enable WiFi
  2. Connect a WiFi adapter to the camera's hotspot (e.g., "ONE RS XXXX.OSC")
  3. Camera IP is 192.168.42.1 (default)
  4. TerraFusion sends HTTP commands to control recording
"""

import time
import threading
import json
import urllib.request
import urllib.error
from datetime import datetime

# Insta360 OSC API defaults
DEFAULT_CAMERA_IP = "192.168.42.1"
DEFAULT_CAMERA_PORT = 20000
CONNECT_TIMEOUT = 5  # seconds


class CameraController:
    """Controls Insta360 camera via WiFi HTTP (OSC protocol)."""

    def __init__(self):
        self._connected = False
        self._recording = False
        self._camera_ip = DEFAULT_CAMERA_IP
        self._camera_port = DEFAULT_CAMERA_PORT
        self._device_name = ""
        self._model = ""
        self._firmware = ""
        self._serial = ""
        self._battery = -1
        self._storage_total = -1     # GB
        self._storage_remaining = -1  # GB
        self._storage_pct = -1        # %
        self._status = "Disconnected"
        self._error = ""
        self._lock = threading.Lock()
        self._record_start_time = None
        self._monitor_thread = None
        self._monitor_running = False

    @property
    def _base_url(self):
        return f"http://{self._camera_ip}:{self._camera_port}"

    def get_status(self):
        with self._lock:
            elapsed = 0
            if self._record_start_time and self._recording:
                elapsed = time.time() - self._record_start_time
            return {
                "available": True,
                "connected": self._connected,
                "recording": self._recording,
                "device_name": self._device_name,
                "device_address": f"{self._camera_ip}:{self._camera_port}",
                "model": self._model,
                "firmware": self._firmware,
                "serial": self._serial,
                "battery": self._battery,
                "storage_total_gb": self._storage_total,
                "storage_remaining_gb": self._storage_remaining,
                "storage_pct": self._storage_pct,
                "status": self._status,
                "error": self._error,
                "record_duration_s": round(elapsed, 1),
            }

    def _set_status(self, status, error=""):
        with self._lock:
            self._status = status
            self._error = error

    def _osc_request(self, endpoint, data=None, timeout=CONNECT_TIMEOUT):
        """Send HTTP request to camera OSC API."""
        url = f"{self._base_url}{endpoint}"
        try:
            if data is not None:
                body = json.dumps(data).encode("utf-8")
                req = urllib.request.Request(
                    url, data=body,
                    headers={"Content-Type": "application/json;charset=utf-8"}
                )
            else:
                req = urllib.request.Request(url)

            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        except urllib.error.URLError as e:
            raise ConnectionError(f"Cannot reach camera at {self._camera_ip}: {e.reason}")
        except urllib.error.HTTPError as e:
            raise ConnectionError(f"Camera HTTP error {e.code}: {e.read().decode()}")
        except Exception as e:
            raise ConnectionError(f"Camera request failed: {e}")

    def _osc_command(self, command_name, parameters=None):
        """Execute an OSC command on the camera."""
        payload = {"name": command_name}
        if parameters:
            payload["parameters"] = parameters
        return self._osc_request("/osc/commands/execute", payload)

    # ── Connection ──

    def connect(self, ip=None, port=None):
        """Connect to Insta360 camera via WiFi."""
        if ip:
            self._camera_ip = ip
        if port:
            self._camera_port = port

        self._set_status("Connecting...")
        try:
            # Step 1: Get camera info via /osc/info
            info = self._osc_request("/osc/info")
            self._device_name = info.get("manufacturer", "Insta360") + " " + info.get("model", "Camera")
            self._model = info.get("model", "Unknown")
            self._firmware = info.get("firmwareVersion", "Unknown")
            self._serial = info.get("serialNumber", "Unknown")

            # Step 2: Get camera state
            try:
                state = self._osc_request("/osc/state")
                state_data = state.get("state", {})
                self._battery = state_data.get("batteryLevel", -1)
                # Check if already recording
                cap_status = state_data.get("_captureStatus", "idle")
                if cap_status in ("shooting", "recording"):
                    self._recording = True
                    self._record_start_time = time.time()
            except Exception:
                pass  # State endpoint may not be available on all models

            self._connected = True
            self._set_status("Connected")

            # Start background state monitor
            self._start_monitor()
            return True

        except Exception as e:
            self._connected = False
            self._set_status("Error", str(e))
            return False

    def disconnect(self):
        """Disconnect from camera."""
        self._stop_monitor()
        if self._recording:
            try:
                self.stop_video()
            except Exception:
                pass
        self._connected = False
        self._device_name = ""
        self._model = ""
        self._firmware = ""
        self._serial = ""
        self._battery = -1
        self._set_status("Disconnected")

    # ── Recording Control ──

    def start_video(self):
        """Start video recording on Insta360."""
        if not self._connected:
            self._set_status("Error", "Not connected")
            return False

        try:
            # Set to video mode first
            try:
                self._osc_command("camera.setOptions", {
                    "options": {"captureMode": "video"}
                })
            except Exception:
                pass  # Some models don't need explicit mode set

            # Start capture
            result = self._osc_command("camera.startCapture")

            if result.get("state") == "error":
                error_msg = result.get("error", {}).get("message", "Unknown error")
                self._set_status("Error", f"Start failed: {error_msg}")
                return False

            self._recording = True
            self._record_start_time = time.time()
            self._set_status("Recording")
            return True

        except Exception as e:
            self._set_status("Error", f"Start failed: {e}")
            return False

    def stop_video(self):
        """Stop video recording on Insta360."""
        if not self._connected:
            return False

        try:
            result = self._osc_command("camera.stopCapture")

            self._recording = False
            self._record_start_time = None
            self._set_status("Connected")
            return True

        except Exception as e:
            self._set_status("Error", f"Stop failed: {e}")
            return False

    def take_photo(self):
        """Take a single photo."""
        if not self._connected:
            self._set_status("Error", "Not connected")
            return False

        try:
            try:
                self._osc_command("camera.setOptions", {
                    "options": {"captureMode": "image"}
                })
            except Exception:
                pass

            result = self._osc_command("camera.takePicture")
            self._set_status("Photo taken")
            return True

        except Exception as e:
            self._set_status("Error", f"Photo failed: {e}")
            return False

    def get_options(self):
        """Get current camera options."""
        if not self._connected:
            return {}

        try:
            result = self._osc_command("camera.getOptions", {
                "optionNames": [
                    "captureMode",
                    "videoStitching",
                    "fileFormat",
                    "previewFormat",
                    "_resolution",
                    "_frameRate",
                ]
            })
            return result.get("results", {}).get("options", {})
        except Exception:
            return {}

    # ── Probe/Scan ──

    def probe(self, ip=None, port=None):
        """Quick probe to check if an Insta360 is reachable at given IP."""
        target_ip = ip or self._camera_ip
        target_port = port or self._camera_port

        old_ip, old_port = self._camera_ip, self._camera_port
        self._camera_ip = target_ip
        self._camera_port = target_port

        try:
            info = self._osc_request("/osc/info", timeout=3)
            name = info.get("manufacturer", "") + " " + info.get("model", "")
            result = {
                "found": True,
                "name": name.strip(),
                "model": info.get("model", ""),
                "serial": info.get("serialNumber", ""),
                "firmware": info.get("firmwareVersion", ""),
                "ip": target_ip,
                "port": target_port,
            }
            self._camera_ip = old_ip
            self._camera_port = old_port
            return result

        except Exception as e:
            self._camera_ip = old_ip
            self._camera_port = old_port
            return {
                "found": False,
                "name": "",
                "ip": target_ip,
                "port": target_port,
                "error": str(e),
            }

    def scan_network(self, timeout=2):
        """
        Scan common Insta360 camera IPs.
        Insta360 typically uses 192.168.42.1 on port 20000.
        Also tries port 80 and alternative subnets.
        """
        self._set_status("Scanning...")
        candidates = [
            ("192.168.42.1", 20000),
            ("192.168.42.1", 80),
            ("192.168.43.1", 20000),
            ("192.168.43.1", 80),
        ]
        # Remove duplicates while preserving order
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                unique_candidates.append(c)

        found = []
        for ip, port in unique_candidates:
            old_ip, old_port = self._camera_ip, self._camera_port
            self._camera_ip = ip
            self._camera_port = port
            try:
                info = self._osc_request("/osc/info", timeout=timeout)
                name = info.get("manufacturer", "") + " " + info.get("model", "")
                found.append({
                    "name": name.strip(),
                    "model": info.get("model", ""),
                    "serial": info.get("serialNumber", ""),
                    "ip": ip,
                    "port": port,
                })
            except Exception:
                pass
            finally:
                self._camera_ip = old_ip
                self._camera_port = old_port

        if found:
            self._set_status(f"Found {len(found)} camera(s)")
        else:
            self._set_status("No camera found")

        return found

    # ── Background State Monitor ──

    def _start_monitor(self):
        """Periodically poll camera state (battery, capture status)."""
        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True
        )
        self._monitor_thread.start()

    def _stop_monitor(self):
        self._monitor_running = False

    def _monitor_loop(self):
        """Poll camera state every 5 seconds."""
        while self._monitor_running and self._connected:
            try:
                state = self._osc_request("/osc/state", timeout=3)
                state_data = state.get("state", {})
                with self._lock:
                    self._battery = state_data.get("batteryLevel", self._battery)
                    # Storage: Insta360 reports in bytes
                    total_sp = state_data.get("_totalSpace", state_data.get("storageTotal", -1))
                    remain_sp = state_data.get("_remainingSpace", state_data.get("storageRemaining", -1))
                    if total_sp > 0:
                        self._storage_total = round(total_sp / (1024**3), 1)
                        self._storage_remaining = round(remain_sp / (1024**3), 1) if remain_sp > 0 else 0
                        self._storage_pct = round((remain_sp / total_sp) * 100, 0) if total_sp > 0 else 0
                    cap_status = state_data.get("_captureStatus", "idle")
                    if cap_status in ("shooting", "recording") and not self._recording:
                        self._recording = True
                        if not self._record_start_time:
                            self._record_start_time = time.time()
                    elif cap_status == "idle" and self._recording:
                        self._recording = False
                        self._record_start_time = None
            except Exception:
                with self._lock:
                    if self._connected:
                        self._connected = False
                        self._status = "Connection lost"
                break

            time.sleep(5)

    # ── Sync wrappers (for API compatibility) ──

    def sync_scan(self, timeout=8):
        return self.scan_network(timeout=min(timeout // 3, 2))

    def sync_connect(self, address):
        # address can be "ip:port" or just "ip"
        if ":" in address:
            parts = address.rsplit(":", 1)
            ip = parts[0]
            port = int(parts[1])
        else:
            ip = address
            port = DEFAULT_CAMERA_PORT
        return self.connect(ip, port)

    def sync_disconnect(self):
        return self.disconnect()

    def sync_start_video(self):
        return self.start_video()

    def sync_stop_video(self):
        return self.stop_video()


class MultiCameraManager:
    """Manages multiple Insta360 cameras for multi-camera 360 rigs."""

    def __init__(self):
        self._cameras = {}  # cam_id -> CameraController
        self._lock = threading.Lock()

    def add_camera(self, cam_id, ip=DEFAULT_CAMERA_IP, port=DEFAULT_CAMERA_PORT,
                   name="", role="primary"):
        """Add a camera to the managed set."""
        with self._lock:
            ctrl = CameraController()
            ctrl._camera_ip = ip
            ctrl._camera_port = port
            self._cameras[cam_id] = {
                "controller": ctrl,
                "id": cam_id,
                "name": name or f"Camera {len(self._cameras) + 1}",
                "role": role,  # primary, secondary, rear, etc.
                "ip": ip,
                "port": port,
            }
        return True

    def remove_camera(self, cam_id):
        with self._lock:
            entry = self._cameras.pop(cam_id, None)
            if entry:
                try:
                    entry["controller"].disconnect()
                except Exception:
                    pass
        return True

    def get_camera(self, cam_id):
        with self._lock:
            entry = self._cameras.get(cam_id)
            return entry["controller"] if entry else None

    def connect_camera(self, cam_id):
        ctrl = self.get_camera(cam_id)
        if ctrl:
            return ctrl.connect()
        return False

    def disconnect_camera(self, cam_id):
        ctrl = self.get_camera(cam_id)
        if ctrl:
            ctrl.disconnect()
            return True
        return False

    def connect_all(self):
        """Connect to all configured cameras."""
        results = {}
        with self._lock:
            cameras = dict(self._cameras)
        for cam_id, entry in cameras.items():
            try:
                ok = entry["controller"].connect()
                results[cam_id] = ok
            except Exception as e:
                results[cam_id] = False
        return results

    def start_all_video(self):
        """Start video on all connected cameras."""
        results = {}
        with self._lock:
            cameras = dict(self._cameras)
        for cam_id, entry in cameras.items():
            ctrl = entry["controller"]
            if ctrl._connected:
                try:
                    ok = ctrl.start_video()
                    results[cam_id] = ok
                except Exception:
                    results[cam_id] = False
        return results

    def stop_all_video(self):
        """Stop video on all connected cameras."""
        results = {}
        with self._lock:
            cameras = dict(self._cameras)
        for cam_id, entry in cameras.items():
            ctrl = entry["controller"]
            if ctrl._connected:
                try:
                    ok = ctrl.stop_video()
                    results[cam_id] = ok
                except Exception:
                    results[cam_id] = False
        return results

    def disconnect_all(self):
        with self._lock:
            cameras = dict(self._cameras)
        for cam_id, entry in cameras.items():
            try:
                entry["controller"].disconnect()
            except Exception:
                pass

    def get_all_status(self):
        """Get status of all cameras."""
        result = []
        with self._lock:
            cameras = dict(self._cameras)
        for cam_id, entry in cameras.items():
            status = entry["controller"].get_status()
            status["cam_id"] = cam_id
            status["cam_name"] = entry["name"]
            status["role"] = entry["role"]
            result.append(status)
        return result

    def list_cameras(self):
        with self._lock:
            return [
                {
                    "id": cam_id,
                    "name": entry["name"],
                    "role": entry["role"],
                    "ip": entry["ip"],
                    "port": entry["port"],
                    "connected": entry["controller"]._connected,
                    "recording": entry["controller"]._recording,
                }
                for cam_id, entry in self._cameras.items()
            ]

