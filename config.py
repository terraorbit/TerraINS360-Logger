"""
TerraINS360 - Configuration Manager v3.0
==========================================
Persistent JSON config. No camera sections.
Includes S3 cloud sync and operation mode settings.
"""

import json
from pathlib import Path

CONFIG_DIR = Path(__file__).parent / "data"
CONFIG_FILE = CONFIG_DIR / "terrafusion_config.json"

DEFAULT_CONFIG = {
    "project": {
        "id": "",
        "name": "",
        "operator": "",
        "description": "",
        "client": "",
        "location": "",
        "created": "",
    },
    "projects": [],
    "gnss": {
        "host": "192.168.1.1",
        "port": 1212,
        "auto_connect": True,
        "receiver_model": "GeoMate SG7",
        "serial_number": "",
        "instrument_height": 0.0,
        "antenna_phase_center": 0.077,
    },
    "gnss_devices": [
        {
            "id": "sg7_default",
            "name": "GeoMate SG7",
            "model": "UM980",
            "host": "192.168.1.1",
            "port": 1212,
            "protocol": "TCP_NMEA",
            "active": True,
        },
    ],
    "ntrip": {
        "enabled": False,
        "host": "211.144.120.97",
        "port": 2201,
        "mountpoint": "4661139",
        "username": "a",
        "password": "a",
        "send_gga": True,
        "gga_interval": 5,
    },
    "logging": {
        "output_dir": "",
        "hz": 5,
        "format": "both",
        "prefix": "TI",
        "auto_split_minutes": 0,
        "include_raw": True,
    },
    "vehicle": {
        "active": "car_2",
        "profiles": {
            "car_2": {
                "name": "Survey Vehicle 2",
                "type": "car",
                "description": "2-seater survey car with roof rack",
                "lever_arm": {"x": 0.0, "y": 0.0, "z": -1.5},
                "instrument_height": 2.0,
            },
            "car_4": {
                "name": "Survey Vehicle 4",
                "type": "car",
                "description": "4-seater survey SUV with roof rack",
                "lever_arm": {"x": 0.0, "y": 0.0, "z": -1.8},
                "instrument_height": 2.2,
            },
            "walk": {
                "name": "Walking / Backpack",
                "type": "walk",
                "description": "Backpack-mounted pole survey",
                "lever_arm": {"x": 0.0, "y": 0.0, "z": -0.3},
                "instrument_height": 2.0,
            },
            "bike": {
                "name": "Survey Bike",
                "type": "bike",
                "description": "Bicycle/motorbike with handlebar mount",
                "lever_arm": {"x": 0.0, "y": 0.0, "z": -0.8},
                "instrument_height": 1.5,
            },
            "custom": {
                "name": "Custom Vehicle",
                "type": "custom",
                "description": "",
                "lever_arm": {"x": 0.0, "y": 0.0, "z": 0.0},
                "instrument_height": 2.0,
            },
        },
    },
    "cloud": {
        "enabled": False,
        "provider": "s3",
        "bucket": "",
        "region": "ap-south-1",
        "prefix": "terrains360/",
        "aws_access_key": "",
        "aws_secret_key": "",
        "auto_sync": True,
        "sync_interval_s": 30,
    },
    "dashboard": {
        "port": 9360,
        "refresh_ms": 250,
        "theme": "dark",
    },
}


class ConfigManager:
    def __init__(self):
        self._config = {}
        self._load()

    def _load(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    self._config = json.load(f)
                self._merge_defaults(self._config, DEFAULT_CONFIG)
            except (json.JSONDecodeError, IOError):
                self._config = json.loads(json.dumps(DEFAULT_CONFIG))
        else:
            self._config = json.loads(json.dumps(DEFAULT_CONFIG))
            self._save()

    def _merge_defaults(self, config, defaults):
        for key, value in defaults.items():
            if key not in config:
                config[key] = json.loads(json.dumps(value))
            elif isinstance(value, dict) and isinstance(config[key], dict):
                self._merge_defaults(config[key], value)

    def _save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(self._config, f, indent=2)

    def get(self, section, key=None):
        if key is None:
            return self._config.get(section, {})
        return self._config.get(section, {}).get(key)

    def set(self, section, key, value):
        if section not in self._config:
            self._config[section] = {}
        self._config[section][key] = value
        self._save()

    def update_section(self, section, data):
        if section not in self._config:
            self._config[section] = {}
        self._config[section].update(data)
        self._save()

    def get_all(self):
        return json.loads(json.dumps(self._config))

    def reset(self):
        self._config = json.loads(json.dumps(DEFAULT_CONFIG))
        self._save()


config = ConfigManager()
