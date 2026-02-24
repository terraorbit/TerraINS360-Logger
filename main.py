"""
TerraINS360 - Professional Mobile Mapping Platform v3.0
=========================================================
A Startup India Initiative

GNSS-based geospatial data acquisition for road asset inventory,
infrastructure mapping, and survey. Camera started manually.

Features:
  - RBAC: SuperAdmin (1) + Users (N)
  - Cloud S3 sync with offline queue
  - GeoParquet track export
  - PWA for Android with offline cache
  - GNSS device management
  - Operation modes & lever arm config

Tabs:
  1. Dashboard    - Live GNSS, survey controls
  2. Projects     - Project & vehicle management
  3. Tracks       - Catalogue, export (GeoParquet/CSV/NMEA)
  4. Admin        - Users, devices, cloud, settings (SuperAdmin only)

Usage:
    python main.py
    Open http://localhost:9360
"""

import os
import sys
import json
import time
import csv
import io
import shutil
import uvicorn
import xml.etree.ElementTree as ET
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Response, Cookie
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List

# ── Module imports ──
from config import config
from gnss_manager import GNSSManager, GNSSState
from ntrip_client import NTRIPClient, get_source_table
from nmea_logger import NMEALogger
from lever_arm import apply_lever_arm, describe_offset, compute_camera_position
from track_manager import TrackManager
from auth import auth_manager
from cloud_sync import cloud_sync

# ── Initialize components ──
gnss_state = GNSSState()
gnss = GNSSManager(gnss_state)
ntrip = NTRIPClient(gnss)
logger = NMEALogger(gnss_state)
tracks = TrackManager()


# ── FastAPI with lifespan ──

@asynccontextmanager
async def lifespan(app):
    """Startup and shutdown logic."""
    # Remove EON CAR vehicle if it exists in saved config
    profiles = config.get("vehicle", "profiles") or {}
    eon_keys = [k for k in profiles if k.lower().startswith("survey_vehicle_eon") or
                (profiles[k].get("name", "").upper() == "EON CAR")]
    if eon_keys:
        for k in eon_keys:
            del profiles[k]
        config.set("vehicle", "profiles", profiles)
        if config.get("vehicle", "active") in eon_keys:
            config.set("vehicle", "active", "custom")

    # GNSS auto-connect (non-blocking – works even if device is off)
    gnss_cfg = config.get("gnss")
    if gnss_cfg.get("auto_connect"):
        try:
            gnss.configure(gnss_cfg.get("host", "192.168.1.1"),
                           gnss_cfg.get("port", 1212))
            gnss.start()
            print(f"[GNSS] Auto-connect started (target {gnss_cfg.get('host')}:{gnss_cfg.get('port')})")
        except Exception as e:
            print(f"[GNSS] Auto-connect skipped: {e}")

    # NTRIP auto-connect
    ntrip_cfg = config.get("ntrip")
    if ntrip_cfg.get("enabled"):
        try:
            ntrip.configure(
                ntrip_cfg["host"], ntrip_cfg["port"],
                ntrip_cfg["mountpoint"], ntrip_cfg["username"],
                ntrip_cfg["password"], ntrip_cfg.get("send_gga", True),
                ntrip_cfg.get("gga_interval", 5),
            )
            ntrip.start()
        except Exception as e:
            print(f"[NTRIP] Auto-connect skipped: {e}")

    # S3 cloud sync
    cloud_cfg = config.get("cloud") or {}
    if cloud_cfg.get("enabled") and cloud_cfg.get("bucket"):
        cloud_sync.configure(
            cloud_cfg.get("aws_access_key", ""),
            cloud_cfg.get("aws_secret_key", ""),
            cloud_cfg.get("bucket", ""),
            cloud_cfg.get("region", "ap-south-1"),
            cloud_cfg.get("prefix", "terrains360/"),
        )
        cloud_sync.start_sync_worker()

    yield

    # Shutdown
    gnss.stop()
    ntrip.stop()
    cloud_sync.stop_sync_worker()

app = FastAPI(title="TerraINS360 Logger", version="3.1.0", lifespan=lifespan)

# CORS middleware - allow Android app and any client to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Static files
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ══════════════════════════════════════════
#  PYDANTIC MODELS
# ══════════════════════════════════════════

class LoginRequest(BaseModel):
    username: str
    password: str

class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str = ""
    email: str = ""
    role: str = "user"

class UserUpdate(BaseModel):
    username: str
    display_name: str = ""
    email: str = ""
    active: bool = True
    permissions: List[str] = []

class PasswordChange(BaseModel):
    username: str
    new_password: str

class ProjectConfig(BaseModel):
    name: str = ""
    operator: str = ""
    description: str = ""
    client: str = ""
    location: str = ""

class GNSSConfig(BaseModel):
    host: str = "192.168.1.1"
    port: int = 1212
    instrument_height: float = 0.0

class NTRIPConfig(BaseModel):
    host: str = ""
    port: int = 2201
    mountpoint: str = ""
    username: str = ""
    password: str = ""
    send_gga: bool = True
    gga_interval: int = 5

class LogConfig(BaseModel):
    output_dir: str = ""
    hz: int = 5
    format: str = "both"
    prefix: str = "TI"

class VehicleSelect(BaseModel):
    vehicle_id: str = "custom"

class VehicleProfile(BaseModel):
    id: str
    name: str
    type: str = "custom"
    description: str = ""
    lever_arm_x: float = 0.0
    lever_arm_y: float = 0.0
    lever_arm_z: float = 0.0
    instrument_height: float = 2.0

class ProjectCreate(BaseModel):
    name: str
    operator: str = ""
    client: str = ""
    location: str = ""
    description: str = ""

class ProjectSwitch(BaseModel):
    project_id: str

class TrackStart(BaseModel):
    name: str = ""
    notes: str = ""

class TrackNotes(BaseModel):
    track_id: str
    notes: str = ""

class InstrumentHeight(BaseModel):
    height: float = 0.0

class CloudConfig(BaseModel):
    enabled: bool = False
    bucket: str = ""
    region: str = "ap-south-1"
    prefix: str = "terrains360/"
    aws_access_key: str = ""
    aws_secret_key: str = ""
    auto_sync: bool = True

class GNSSDeviceConfig(BaseModel):
    id: str
    name: str
    model: str = "UM980"
    host: str = "192.168.1.1"
    port: int = 1212
    protocol: str = "TCP_NMEA"
    active: bool = False

class AntennaSettings(BaseModel):
    antenna_phase_center: float = 0.077
    instrument_height: float = 0.0

# ══════════════════════════════════════════
#  AUTH HELPERS
# ══════════════════════════════════════════

def get_current_user(request: Request) -> dict | None:
    """Extract user from JWT cookie or Authorization header."""
    token = request.cookies.get("ti_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if token:
        return auth_manager.get_user_from_token(token)
    return None

def require_auth(request: Request) -> dict:
    """Require authenticated user."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Authentication required")
    return user

def require_admin(request: Request) -> dict:
    """Require superadmin role."""
    user = require_auth(request)
    if user.get("role") != "superadmin":
        raise HTTPException(403, "SuperAdmin access required")
    return user


# ══════════════════════════════════════════
#  API ENDPOINTS
# ══════════════════════════════════════════

# ── Pages ──

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    return html_path.read_text(encoding="utf-8")

@app.get("/sw.js")
async def service_worker():
    sw_path = STATIC_DIR / "sw.js"
    if sw_path.exists():
        return FileResponse(str(sw_path), media_type="application/javascript")
    raise HTTPException(404)

@app.get("/manifest.json")
async def manifest():
    m_path = STATIC_DIR / "manifest.json"
    if m_path.exists():
        return FileResponse(str(m_path), media_type="application/json")
    raise HTTPException(404)


# ── Auth ──

@app.post("/api/auth/login")
async def login(req: LoginRequest, response: Response):
    user = auth_manager.authenticate(req.username, req.password)
    if not user:
        raise HTTPException(401, "Invalid credentials")
    token = auth_manager.create_token(user["username"], user["role"])
    response.set_cookie("ti_token", token, httponly=True, max_age=86400*7, samesite="lax")
    return {"status": "ok", "user": user, "token": token}

@app.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie("ti_token")
    return {"status": "logged_out"}

@app.get("/api/auth/me")
async def auth_me(request: Request):
    user = get_current_user(request)
    if not user:
        return {"authenticated": False}
    return {"authenticated": True, "user": user}


# ── Full Status (polled by UI) ──

@app.get("/api/status")
async def get_status(request: Request):
    gnss_data = gnss_state.snapshot()
    ntrip_data = ntrip.get_status()
    log_data = logger.get_status()
    active_track = tracks.get_active_track()

    # Vehicle config
    vehicle_cfg = config.get("vehicle") or {}
    active_vehicle = vehicle_cfg.get("active", "custom")
    profiles = vehicle_cfg.get("profiles", {})
    vp = profiles.get(active_vehicle, {})
    lever = vp.get("lever_arm", {"x": 0.0, "y": 0.0, "z": 0.0})
    inst_h = config.get("gnss", "instrument_height") or vp.get("instrument_height", 0.0)

    cam_pos = compute_camera_position(gnss_data, lever, inst_h)
    sync_st = cloud_sync.get_status()

    return {
        "gnss": gnss_data,
        "ntrip": ntrip_data,
        "logging": log_data,
        "active_track": active_track,
        "vehicle": {
            "id": active_vehicle,
            "name": vp.get("name", active_vehicle),
            "type": vp.get("type", "custom"),
            "lever_arm": lever,
            "instrument_height": inst_h,
        },
        "camera_position": cam_pos,
        "lever_arm_desc": describe_offset(
            lever.get("x", 0), lever.get("y", 0), lever.get("z", 0), inst_h
        ),
        "project": config.get("project") or {},
        "track_stats": tracks.get_stats(),
        "cloud": sync_st,
    }


# ── Project ──

@app.post("/api/project/set")
async def set_project(cfg: ProjectConfig, request: Request):
    config.update_section("project", {
        "name": cfg.name, "operator": cfg.operator,
        "description": cfg.description, "client": cfg.client,
        "location": cfg.location,
    })
    projects = config.get("projects") or []
    for p in projects:
        if p.get("id") == config.get("project", "id"):
            p.update({"name": cfg.name, "operator": cfg.operator,
                       "description": cfg.description, "client": cfg.client,
                       "location": cfg.location})
            break
    config._config["projects"] = projects
    config._save()
    return {"status": "ok"}

@app.get("/api/project")
async def get_project():
    return config.get("project") or {}


# ── Multi-Project Management ──

@app.get("/api/projects")
async def list_projects():
    projects = config.get("projects") or []
    active_id = config.get("project", "id") or ""
    return {"projects": projects, "active_id": active_id}

@app.post("/api/projects/create")
async def create_project(cfg: ProjectCreate):
    projects = config.get("projects") or []
    project_id = f"proj_{int(time.time())}_{len(projects)}"
    new_project = {
        "id": project_id,
        "name": cfg.name,
        "operator": cfg.operator,
        "client": cfg.client,
        "location": cfg.location,
        "description": cfg.description,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "surveys_count": 0,
    }
    projects.append(new_project)
    config._config["projects"] = projects
    config.update_section("project", {
        "id": project_id, "name": cfg.name, "operator": cfg.operator,
        "description": cfg.description, "client": cfg.client,
        "location": cfg.location,
    })
    config._save()
    return {"status": "created", "project": new_project}

@app.post("/api/projects/switch")
async def switch_project(cfg: ProjectSwitch):
    projects = config.get("projects") or []
    target = None
    for p in projects:
        if p.get("id") == cfg.project_id:
            target = p
            break
    if not target:
        raise HTTPException(404, "Project not found")
    config.update_section("project", {
        "id": target["id"], "name": target.get("name", ""),
        "operator": target.get("operator", ""),
        "description": target.get("description", ""),
        "client": target.get("client", ""),
        "location": target.get("location", ""),
    })
    return {"status": "switched", "project": target}

@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    projects = config.get("projects") or []
    projects = [p for p in projects if p.get("id") != project_id]
    config._config["projects"] = projects
    if config.get("project", "id") == project_id:
        config.update_section("project", {
            "id": "", "name": "", "operator": "", "description": "",
            "client": "", "location": "",
        })
    config._save()
    return {"status": "deleted"}


# ── GNSS Control ──

@app.post("/api/gnss/connect")
async def gnss_connect(cfg: GNSSConfig):
    gnss.configure(cfg.host, cfg.port)
    config.update_section("gnss", {
        "host": cfg.host, "port": cfg.port,
        "instrument_height": cfg.instrument_height,
    })
    gnss.start()
    return {"status": "connecting", "host": cfg.host, "port": cfg.port}

@app.post("/api/gnss/disconnect")
async def gnss_disconnect():
    gnss.stop()
    return {"status": "disconnected"}


@app.post("/api/gnss/antenna_settings")
async def set_antenna_settings(cfg: AntennaSettings):
    config.set("gnss", "antenna_phase_center", cfg.antenna_phase_center)
    config.set("gnss", "instrument_height", cfg.instrument_height)
    return {"antenna_phase_center": cfg.antenna_phase_center, "instrument_height": cfg.instrument_height}
@app.post("/api/gnss/instrument_height")
async def set_instrument_height(cfg: InstrumentHeight):
    config.set("gnss", "instrument_height", cfg.height)
    return {"instrument_height": cfg.height}


# ── GNSS Device Management (Admin) ──

@app.get("/api/gnss/devices")
async def list_gnss_devices():
    return {"devices": config.get("gnss_devices") or []}

@app.post("/api/gnss/devices/save")
async def save_gnss_device(cfg: GNSSDeviceConfig, request: Request):
    require_admin(request)
    devices = config.get("gnss_devices") or []
    found = False
    for d in devices:
        if d["id"] == cfg.id:
            d.update(cfg.model_dump())
            found = True
            break
    if not found:
        devices.append(cfg.model_dump())
    config._config["gnss_devices"] = devices
    config._save()
    if cfg.active:
        for d in devices:
            if d["id"] != cfg.id:
                d["active"] = False
        config._config["gnss_devices"] = devices
        config.update_section("gnss", {"host": cfg.host, "port": cfg.port})
        config._save()
    return {"status": "saved"}

@app.delete("/api/gnss/devices/{device_id}")
async def delete_gnss_device(device_id: str, request: Request):
    require_admin(request)
    devices = config.get("gnss_devices") or []
    devices = [d for d in devices if d["id"] != device_id]
    config._config["gnss_devices"] = devices
    config._save()
    return {"status": "deleted"}


# ── NTRIP Control ──

@app.post("/api/ntrip/connect")
async def ntrip_connect(cfg: NTRIPConfig):
    ntrip.configure(
        cfg.host, cfg.port, cfg.mountpoint,
        cfg.username, cfg.password,
        cfg.send_gga, cfg.gga_interval,
    )
    config.update_section("ntrip", {
        "host": cfg.host, "port": cfg.port,
        "mountpoint": cfg.mountpoint,
        "username": cfg.username, "password": cfg.password,
        "send_gga": cfg.send_gga, "gga_interval": cfg.gga_interval,
        "enabled": True,
    })
    ntrip.start()
    return {"status": "connecting"}

@app.post("/api/ntrip/disconnect")
async def ntrip_disconnect():
    ntrip.stop()
    config.set("ntrip", "enabled", False)
    return {"status": "disconnected"}

@app.get("/api/ntrip/sourcetable")
async def ntrip_sourcetable(host: str, port: int = 2101):
    mounts = get_source_table(host, port)
    return {"mountpoints": mounts}


# ── Vehicle Management ──

@app.get("/api/vehicles")
async def list_vehicles():
    vehicle_cfg = config.get("vehicle") or {}
    return {
        "active": vehicle_cfg.get("active", "custom"),
        "profiles": vehicle_cfg.get("profiles", {}),
    }

@app.post("/api/vehicle/select")
async def select_vehicle(cfg: VehicleSelect):
    config.set("vehicle", "active", cfg.vehicle_id)
    profiles = config.get("vehicle", "profiles") or {}
    vp = profiles.get(cfg.vehicle_id, {})
    return {"active": cfg.vehicle_id, "profile": vp}

@app.post("/api/vehicle/save")
async def save_vehicle(cfg: VehicleProfile, request: Request):
    require_admin(request)
    profiles = config.get("vehicle", "profiles") or {}
    profiles[cfg.id] = {
        "name": cfg.name,
        "type": cfg.type,
        "description": cfg.description,
        "lever_arm": {"x": cfg.lever_arm_x, "y": cfg.lever_arm_y, "z": cfg.lever_arm_z},
        "instrument_height": cfg.instrument_height,
    }
    config.set("vehicle", "profiles", profiles)
    return {"status": "saved", "profile": profiles[cfg.id]}

@app.post("/api/vehicle/delete")
async def delete_vehicle(cfg: VehicleSelect, request: Request):
    require_admin(request)
    profiles = config.get("vehicle", "profiles") or {}
    if cfg.vehicle_id in profiles and cfg.vehicle_id != "custom":
        del profiles[cfg.vehicle_id]
        config.set("vehicle", "profiles", profiles)
        if config.get("vehicle", "active") == cfg.vehicle_id:
            config.set("vehicle", "active", "custom")
    return {"status": "deleted"}


# ── Survey / Recording ──

@app.post("/api/survey/start")
async def survey_start(cfg: TrackStart):
    vehicle_cfg = config.get("vehicle") or {}
    active_v = vehicle_cfg.get("active", "custom")
    profiles = vehicle_cfg.get("profiles", {})
    vp = profiles.get(active_v, {})
    lever = vp.get("lever_arm", {"x": 0.0, "y": 0.0, "z": 0.0})
    inst_h = config.get("gnss", "instrument_height") or vp.get("instrument_height", 0.0)

    log_cfg = config.get("logging") or {}
    hz = log_cfg.get("hz", 5)
    fmt = log_cfg.get("format", "both")
    prefix = log_cfg.get("prefix", "TI")
    output_dir = log_cfg.get("output_dir", "") or str(Path(__file__).parent / "data" / "sessions")

    snap = gnss_state.snapshot()
    project = config.get("project") or {}

    track = tracks.create_track(
        project=project.get("name", ""),
        operator=project.get("operator", ""),
        vehicle=active_v,
        vehicle_name=vp.get("name", active_v),
        instrument_height=inst_h,
        lever_arm=lever,
        hz=hz,
        cameras=[],
        name=cfg.name or None,
    )

    tracks.start_track(track.id, snap.get("latitude", 0), snap.get("longitude", 0))
    logger.configure(output_dir, hz, fmt, prefix, lever)
    log_result = logger.start()

    return {
        "status": "recording",
        "track_id": track.id,
        "track_name": track.name,
        "hz": hz,
        "vehicle": active_v,
        "log": log_result,
    }

@app.post("/api/survey/stop")
async def survey_stop():
    log_result = logger.stop()
    active = tracks.get_active_track()

    if active:
        snap = gnss_state.snapshot()
        tracks.stop_track(
            active["id"],
            records=log_result.get("records", 0),
            duration_s=log_result.get("duration_s", 0),
            files=[log_result.get("session", "")],
            end_lat=snap.get("latitude", 0),
            end_lon=snap.get("longitude", 0),
        )
        # Auto-sync track to cloud
        track_data = tracks.get_track(active["id"])
        if track_data:
            sessions_dir = Path(__file__).parent / "data" / "sessions"
            csv_path = None
            for fname in track_data.get("files", []):
                p = sessions_dir / f"{fname}.csv"
                if p.exists():
                    csv_path = str(p)
                    break
            cloud_sync.upload_track_geoparquet(track_data, csv_path)

    return {
        "status": "stopped",
        "log": log_result,
        "track": tracks.get_track(active["id"]) if active else None,
    }

@app.post("/api/logging/configure")
async def configure_logging(cfg: LogConfig):
    output_dir = cfg.output_dir or str(Path(__file__).parent / "data" / "sessions")
    config.update_section("logging", {
        "output_dir": output_dir, "hz": cfg.hz,
        "format": cfg.format, "prefix": cfg.prefix,
    })
    return {"status": "configured"}


# ── Track Management ──

@app.get("/api/tracks")
async def list_tracks(vehicle: str = None, project: str = None):
    return {"tracks": tracks.list_tracks(vehicle=vehicle, project=project)}

@app.get("/api/tracks/grouped")
async def grouped_tracks(project: str = None):
    return {"groups": tracks.group_by_vehicle(project=project)}

@app.get("/api/tracks/{track_id}")
async def get_track(track_id: str):
    t = tracks.get_track(track_id)
    if not t:
        raise HTTPException(404, "Track not found")
    return t

@app.get("/api/tracks/{track_id}/points")
async def get_track_points(track_id: str):
    """Return track GPS points for map visualization."""
    t = tracks.get_track(track_id)
    if not t:
        raise HTTPException(404, "Track not found")
    sessions_dir = Path(__file__).parent / "data" / "sessions"
    points = []
    for fname in t.get("files", []):
        csv_path = sessions_dir / f"{fname}.csv"
        if csv_path.exists():
            try:
                with open(csv_path, "r") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        try:
                            lat = float(row.get("latitude", 0))
                            lon = float(row.get("longitude", 0))
                            if lat == 0 and lon == 0:
                                continue
                            points.append({
                                "lat": lat,
                                "lon": lon,
                                "alt": float(row.get("altitude", 0)),
                                "fix": int(row.get("fix_quality", 0)),
                                "time": row.get("utc_time", ""),
                                "speed": float(row.get("speed_kmh", 0)),
                                "sats": int(row.get("satellites", 0)),
                                "hdop": float(row.get("hdop", 0)),
                            })
                        except (ValueError, TypeError):
                            continue
            except Exception:
                pass
            break
    return {"track_name": t.get("name", ""), "track_id": track_id, "points": points}

@app.post("/api/tracks/notes")
async def update_track_notes(cfg: TrackNotes):
    tracks.update_notes(cfg.track_id, cfg.notes)
    return {"status": "ok"}

@app.delete("/api/tracks/{track_id}")
async def delete_track(track_id: str):
    tracks.delete_track(track_id)
    return {"status": "deleted"}

@app.get("/api/tracks/stats")
async def track_stats():
    return tracks.get_stats()

@app.get("/api/tracks/{track_id}/export")
async def export_track(track_id: str, format: str = "csv"):
    t = tracks.get_track(track_id)
    if not t:
        raise HTTPException(404, "Track not found")

    sessions_dir = Path(__file__).parent / "data" / "sessions"

    if format == "kml":
        kml_content = generate_kml_from_track(t, sessions_dir)
        if not kml_content:
            raise HTTPException(404, "No CSV data found for KML conversion")
        from starlette.responses import Response as StarletteResponse
        return StarletteResponse(
            content=kml_content,
            media_type="application/vnd.google-earth.kml+xml",
            headers={"Content-Disposition": f'attachment; filename="{t["name"]}.kml"'}
        )

    if format == "geoparquet":
        parquet_dir = Path(__file__).parent / "data" / "tracks"
        pp = parquet_dir / f"{track_id}.parquet"
        if pp.exists():
            return FileResponse(str(pp), filename=f"{t['name']}.parquet",
                                media_type="application/octet-stream")
        for fname in t.get("files", []):
            csv_p = sessions_dir / f"{fname}.csv"
            if csv_p.exists():
                cloud_sync.upload_track_geoparquet(t, str(csv_p))
                if pp.exists():
                    return FileResponse(str(pp), filename=f"{t['name']}.parquet",
                                        media_type="application/octet-stream")
        raise HTTPException(404, "No data for GeoParquet conversion")

    for fname in t.get("files", []):
        ext = ".csv" if format == "csv" else ".nmea"
        fpath = sessions_dir / f"{fname}{ext}"
        if fpath.exists():
            return FileResponse(
                fpath, filename=f"{t['name']}{ext}",
                media_type="application/octet-stream",
            )
    raise HTTPException(404, f"No {format} file found for this track")

@app.post("/api/tracks/{track_id}/sync")
async def sync_track(track_id: str):
    """Manually sync a track to cloud."""
    t = tracks.get_track(track_id)
    if not t:
        raise HTTPException(404, "Track not found")
    sessions_dir = Path(__file__).parent / "data" / "sessions"
    csv_path = None
    for fname in t.get("files", []):
        p = sessions_dir / f"{fname}.csv"
        if p.exists():
            csv_path = str(p)
            break
    result = cloud_sync.upload_track_geoparquet(t, csv_path)
    return result


# ── Admin: User Management ──

@app.get("/api/admin/users")
async def admin_list_users(request: Request):
    require_admin(request)
    return {"users": auth_manager.list_users()}

@app.post("/api/admin/users/create")
async def admin_create_user(cfg: UserCreate, request: Request):
    require_admin(request)
    result = auth_manager.create_user(
        cfg.username, cfg.password, cfg.display_name, cfg.email, cfg.role
    )
    return result

@app.post("/api/admin/users/update")
async def admin_update_user(cfg: UserUpdate, request: Request):
    require_admin(request)
    result = auth_manager.update_user(
        cfg.username, display_name=cfg.display_name,
        email=cfg.email, active=cfg.active,
        permissions=cfg.permissions,
    )
    return result

@app.post("/api/admin/users/password")
async def admin_change_password(cfg: PasswordChange, request: Request):
    require_admin(request)
    result = auth_manager.change_password(cfg.username, cfg.new_password)
    return result

@app.delete("/api/admin/users/{username}")
async def admin_delete_user(username: str, request: Request):
    require_admin(request)
    result = auth_manager.delete_user(username)
    return result


# ── Admin: Cloud Sync ──

@app.post("/api/admin/cloud/configure")
async def admin_cloud_configure(cfg: CloudConfig, request: Request):
    require_admin(request)
    config.update_section("cloud", {
        "enabled": cfg.enabled, "bucket": cfg.bucket,
        "region": cfg.region, "prefix": cfg.prefix,
        "aws_access_key": cfg.aws_access_key,
        "aws_secret_key": cfg.aws_secret_key,
        "auto_sync": cfg.auto_sync,
    })
    if cfg.enabled and cfg.bucket:
        result = cloud_sync.configure(
            cfg.aws_access_key, cfg.aws_secret_key,
            cfg.bucket, cfg.region, cfg.prefix,
        )
        if cfg.auto_sync:
            cloud_sync.start_sync_worker()
        return result
    return {"status": "saved"}

@app.get("/api/admin/cloud/status")
async def admin_cloud_status():
    return cloud_sync.get_status()

@app.post("/api/admin/cloud/sync")
async def admin_force_sync(request: Request):
    require_admin(request)
    cloud_sync.upload_config(config.get_all())
    return cloud_sync.force_sync()

@app.get("/api/admin/cloud/queue")
async def admin_sync_queue(request: Request):
    require_admin(request)
    return {"queue": cloud_sync.get_queue()}


# ── KML Generation Helper ──

def generate_kml_from_track(track_data, sessions_dir):
    """Generate KML string from a track's CSV data."""
    points = []
    for fname in track_data.get("files", []):
        csv_path = sessions_dir / f"{fname}.csv"
        if csv_path.exists():
            try:
                with open(csv_path, "r") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        try:
                            lat = float(row.get("latitude", 0))
                            lon = float(row.get("longitude", 0))
                            alt = float(row.get("altitude", 0))
                            if lat == 0 and lon == 0:
                                continue
                            points.append((lon, lat, alt))
                        except (ValueError, TypeError):
                            continue
            except Exception:
                pass
            break

    if not points:
        return None

    track_name = track_data.get("name", "Track")
    start_time = track_data.get("start_time", "")
    project = track_data.get("project", "")

    kml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    kml += '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
    kml += '<Document>\n'
    kml += f'  <name>{track_name}</name>\n'
    kml += f'  <description>Project: {project}\nStart: {start_time}\nRecords: {track_data.get("records", 0)}</description>\n'
    kml += '  <Style id="trackStyle"><LineStyle><color>ff0ea5e9</color><width>3</width></LineStyle></Style>\n'
    kml += '  <Style id="startStyle"><IconStyle><color>ff00ff00</color><scale>1.2</scale></IconStyle></Style>\n'
    kml += '  <Style id="endStyle"><IconStyle><color>ff0000ff</color><scale>1.2</scale></IconStyle></Style>\n'

    # Start point
    kml += '  <Placemark>\n'
    kml += f'    <name>Start: {track_name}</name>\n'
    kml += '    <styleUrl>#startStyle</styleUrl>\n'
    kml += f'    <Point><coordinates>{points[0][0]},{points[0][1]},{points[0][2]}</coordinates></Point>\n'
    kml += '  </Placemark>\n'

    # End point
    kml += '  <Placemark>\n'
    kml += f'    <name>End: {track_name}</name>\n'
    kml += '    <styleUrl>#endStyle</styleUrl>\n'
    kml += f'    <Point><coordinates>{points[-1][0]},{points[-1][1]},{points[-1][2]}</coordinates></Point>\n'
    kml += '  </Placemark>\n'

    # Track line
    kml += '  <Placemark>\n'
    kml += f'    <name>{track_name}</name>\n'
    kml += '    <styleUrl>#trackStyle</styleUrl>\n'
    kml += '    <LineString>\n'
    kml += '      <altitudeMode>absolute</altitudeMode>\n'
    kml += '      <coordinates>\n'
    for p in points:
        kml += f'        {p[0]},{p[1]},{p[2]}\n'
    kml += '      </coordinates>\n'
    kml += '    </LineString>\n'
    kml += '  </Placemark>\n'

    kml += '</Document>\n'
    kml += '</kml>'
    return kml


# ── Admin: All Projects & Tracks ──

@app.get("/api/admin/all_projects")
async def admin_all_projects(request: Request):
    require_admin(request)
    projects = config.get("projects") or []
    all_tracks = tracks.list_tracks()
    # Group tracks by project name
    tracks_by_proj = {}
    for t in all_tracks:
        pn = t.get("project", "Unassigned")
        if pn not in tracks_by_proj:
            tracks_by_proj[pn] = []
        tracks_by_proj[pn].append(t)

    result = []
    for p in projects:
        proj_tracks = tracks_by_proj.get(p.get("name", ""), [])
        result.append({
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "operator": p.get("operator", ""),
            "client": p.get("client", ""),
            "location": p.get("location", ""),
            "created": p.get("created", ""),
            "tracks": proj_tracks,
        })
    # Include tracks from unassigned projects
    if "Unassigned" in tracks_by_proj:
        result.append({
            "id": "unassigned",
            "name": "Unassigned",
            "operator": "",
            "client": "",
            "location": "",
            "created": "",
            "tracks": tracks_by_proj["Unassigned"],
        })
    return {"projects": result}


# ── Project-Level Export ──

@app.get("/api/projects/export")
async def export_project_tracks(project: str, format: str = "csv"):
    """Export all tracks for a project as a combined file."""
    all_tracks = tracks.list_tracks(project=project)
    if not all_tracks:
        raise HTTPException(404, f"No tracks found for project '{project}'")

    sessions_dir = Path(__file__).parent / "data" / "sessions"

    if format == "kml":
        # Combined KML for all project tracks
        kml = '<?xml version="1.0" encoding="UTF-8"?>\n'
        kml += '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
        kml += '<Document>\n'
        kml += f'  <name>{project}</name>\n'
        kml += f'  <description>All tracks for project: {project}</description>\n'
        kml += '  <Style id="trackStyle"><LineStyle><color>ff0ea5e9</color><width>3</width></LineStyle></Style>\n'
        for t in all_tracks:
            track_kml_points = []
            for fname in t.get("files", []):
                csv_path = sessions_dir / f"{fname}.csv"
                if csv_path.exists():
                    try:
                        with open(csv_path, "r") as f:
                            reader = csv.DictReader(f)
                            for row in reader:
                                try:
                                    lat = float(row.get("latitude", 0))
                                    lon = float(row.get("longitude", 0))
                                    alt = float(row.get("altitude", 0))
                                    if lat == 0 and lon == 0:
                                        continue
                                    track_kml_points.append((lon, lat, alt))
                                except (ValueError, TypeError):
                                    continue
                    except Exception:
                        pass
                    break
            if track_kml_points:
                kml += '  <Placemark>\n'
                kml += f'    <name>{t.get("name", "Track")}</name>\n'
                kml += '    <styleUrl>#trackStyle</styleUrl>\n'
                kml += '    <LineString><altitudeMode>absolute</altitudeMode><coordinates>\n'
                for p in track_kml_points:
                    kml += f'      {p[0]},{p[1]},{p[2]}\n'
                kml += '    </coordinates></LineString>\n'
                kml += '  </Placemark>\n'
        kml += '</Document>\n</kml>'
        from starlette.responses import Response as StarletteResponse
        return StarletteResponse(
            content=kml,
            media_type="application/vnd.google-earth.kml+xml",
            headers={"Content-Disposition": f'attachment; filename="{project}_tracks.kml"'}
        )

    # CSV: combine all tracks
    output = io.StringIO()
    writer = None
    for t in all_tracks:
        for fname in t.get("files", []):
            csv_path = sessions_dir / f"{fname}.csv"
            if csv_path.exists():
                try:
                    with open(csv_path, "r") as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            if writer is None:
                                fieldnames = ["track_name", "track_id"] + list(row.keys())
                                writer = csv.DictWriter(output, fieldnames=fieldnames)
                                writer.writeheader()
                            row["track_name"] = t.get("name", "")
                            row["track_id"] = t.get("id", "")
                            writer.writerow(row)
                except Exception:
                    pass
                break
    if not writer:
        raise HTTPException(404, "No CSV data found for this project")
    from starlette.responses import Response as StarletteResponse
    return StarletteResponse(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{project}_tracks.csv"'}
    )


@app.get("/api/projects/export_all")
async def export_all_projects(format: str = "csv"):
    """Export all tracks from all projects."""
    all_tracks = tracks.list_tracks()
    if not all_tracks:
        raise HTTPException(404, "No tracks found")

    sessions_dir = Path(__file__).parent / "data" / "sessions"

    if format == "kml":
        kml = '<?xml version="1.0" encoding="UTF-8"?>\n'
        kml += '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
        kml += '<Document>\n'
        kml += '  <name>All Projects - TerraINS360</name>\n'
        kml += '  <Style id="trackStyle"><LineStyle><color>ff0ea5e9</color><width>3</width></LineStyle></Style>\n'
        # Group by project
        by_proj = {}
        for t in all_tracks:
            pn = t.get("project", "Unassigned")
            if pn not in by_proj:
                by_proj[pn] = []
            by_proj[pn].append(t)
        for proj_name, proj_tracks in by_proj.items():
            kml += f'  <Folder>\n    <name>{proj_name}</name>\n'
            for t in proj_tracks:
                points = []
                for fname in t.get("files", []):
                    csv_path = sessions_dir / f"{fname}.csv"
                    if csv_path.exists():
                        try:
                            with open(csv_path, "r") as f:
                                reader = csv.DictReader(f)
                                for row in reader:
                                    try:
                                        lat = float(row.get("latitude", 0))
                                        lon = float(row.get("longitude", 0))
                                        alt = float(row.get("altitude", 0))
                                        if lat == 0 and lon == 0:
                                            continue
                                        points.append((lon, lat, alt))
                                    except (ValueError, TypeError):
                                        continue
                        except Exception:
                            pass
                        break
                if points:
                    kml += f'    <Placemark>\n      <name>{t.get("name", "Track")}</name>\n'
                    kml += '      <styleUrl>#trackStyle</styleUrl>\n'
                    kml += '      <LineString><altitudeMode>absolute</altitudeMode><coordinates>\n'
                    for p in points:
                        kml += f'        {p[0]},{p[1]},{p[2]}\n'
                    kml += '      </coordinates></LineString>\n    </Placemark>\n'
            kml += '  </Folder>\n'
        kml += '</Document>\n</kml>'
        from starlette.responses import Response as StarletteResponse
        return StarletteResponse(
            content=kml,
            media_type="application/vnd.google-earth.kml+xml",
            headers={"Content-Disposition": 'attachment; filename="all_projects.kml"'}
        )

    # CSV combined
    output = io.StringIO()
    writer = None
    for t in all_tracks:
        for fname in t.get("files", []):
            csv_path = sessions_dir / f"{fname}.csv"
            if csv_path.exists():
                try:
                    with open(csv_path, "r") as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            if writer is None:
                                fieldnames = ["project", "track_name", "track_id"] + list(row.keys())
                                writer = csv.DictWriter(output, fieldnames=fieldnames)
                                writer.writeheader()
                            row["project"] = t.get("project", "")
                            row["track_name"] = t.get("name", "")
                            row["track_id"] = t.get("id", "")
                            writer.writerow(row)
                except Exception:
                    pass
                break
    if not writer:
        raise HTTPException(404, "No CSV data found")
    from starlette.responses import Response as StarletteResponse
    return StarletteResponse(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="all_projects.csv"'}
    )


# ── Config ──

@app.get("/api/config")
async def get_config():
    cfg = config.get_all()
    if "cloud" in cfg:
        cfg["cloud"] = {k: v for k, v in cfg["cloud"].items()
                        if k not in ("aws_access_key", "aws_secret_key")}
    return cfg

@app.post("/api/config/reset")
async def reset_config(request: Request):
    require_admin(request)
    config.reset()
    return {"status": "reset"}


# ══════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════

def main():
    port = config.get("dashboard", "port") or 9360
    print()
    print("  \033[36m+======================================================+\033[0m")
    print("  \033[36m|\033[0m  \033[1;97mTerraINS360 Logger\033[0m v3.1 - GNSS Survey Logger     \033[36m|\033[0m")
    print("  \033[36m|\033[0m  \033[33mA Startup India Initiative\033[0m                          \033[36m|\033[0m")
    print("  \033[36m+------------------------------------------------------+\033[0m")
    proj = config.get("project") or {}
    if proj.get("name"):
        pname = proj['name'][:40]
        print(f"  \033[36m|\033[0m  Project   : {pname:<40} \033[36m|\033[0m")
    print(f"  \033[36m|\033[0m  Dashboard : \033[4mhttp://localhost:{port}\033[0m{'':>{34-len(str(port))}} \033[36m|\033[0m")
    print(f"  \033[36m|\033[0m  GNSS      : {config.get('gnss', 'host')}:{config.get('gnss', 'port'):<21} \033[36m|\033[0m")
    v = config.get("vehicle") or {}
    active_v = v.get("active", "custom")
    profiles = v.get("profiles", {})
    vp = profiles.get(active_v, {})
    vname = vp.get('name', active_v)[:36]
    print(f"  \033[36m|\033[0m  Vehicle   : {vname:<40} \033[36m|\033[0m")
    cloud_cfg = config.get("cloud") or {}
    cloud_stat = "ON" if cloud_cfg.get("enabled") else "OFF"
    bucket = cloud_cfg.get("bucket", "-")[:30]
    print(f"  \033[36m|\033[0m  Cloud S3  : {cloud_stat} ({bucket}){'':>{28-len(bucket)}} \033[36m|\033[0m")
    print(f"  \033[36m|\033[0m  Auth      : RBAC (SuperAdmin + Users)                \033[36m|\033[0m")
    print(f"  \033[36m|\033[0m  IMU       : \033[90mNext version\033[0m                              \033[36m|\033[0m")
    print("  \033[36m+======================================================+\033[0m")
    print()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
