"""
TerraFusion - Track Manager
=============================
Manages survey tracks (sessions of recorded data).
Each track belongs to a project and vehicle, and has
start/end times, NMEA/CSV files, and metadata.
"""

import json
import time
import threading
from pathlib import Path
from datetime import datetime

TRACKS_DIR = Path(__file__).parent / "data" / "tracks"


class Track:
    """A single survey track/session."""

    def __init__(self, track_id=None, **kwargs):
        self.id = track_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.name = kwargs.get("name", f"Track_{self.id}")
        self.project = kwargs.get("project", "")
        self.operator = kwargs.get("operator", "")
        self.vehicle = kwargs.get("vehicle", "custom")
        self.vehicle_name = kwargs.get("vehicle_name", "")
        self.instrument_height = kwargs.get("instrument_height", 0.0)
        self.lever_arm = kwargs.get("lever_arm", {"x": 0.0, "y": 0.0, "z": 0.0})
        self.start_time = kwargs.get("start_time", "")
        self.end_time = kwargs.get("end_time", "")
        self.duration_s = kwargs.get("duration_s", 0)
        self.records = kwargs.get("records", 0)
        self.hz = kwargs.get("hz", 5)
        self.fix_stats = kwargs.get("fix_stats", {})
        self.distance_m = kwargs.get("distance_m", 0.0)
        self.avg_speed_kmh = kwargs.get("avg_speed_kmh", 0.0)
        self.cameras = kwargs.get("cameras", [])
        self.files = kwargs.get("files", [])
        self.status = kwargs.get("status", "idle")  # idle, recording, completed, exported
        self.notes = kwargs.get("notes", "")
        self.start_lat = kwargs.get("start_lat", 0.0)
        self.start_lon = kwargs.get("start_lon", 0.0)
        self.end_lat = kwargs.get("end_lat", 0.0)
        self.end_lon = kwargs.get("end_lon", 0.0)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "project": self.project,
            "operator": self.operator,
            "vehicle": self.vehicle,
            "vehicle_name": self.vehicle_name,
            "instrument_height": self.instrument_height,
            "lever_arm": self.lever_arm,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_s": self.duration_s,
            "records": self.records,
            "hz": self.hz,
            "fix_stats": self.fix_stats,
            "distance_m": self.distance_m,
            "avg_speed_kmh": self.avg_speed_kmh,
            "cameras": self.cameras,
            "files": self.files,
            "status": self.status,
            "notes": self.notes,
            "start_lat": self.start_lat,
            "start_lon": self.start_lon,
            "end_lat": self.end_lat,
            "end_lon": self.end_lon,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(track_id=d.get("id"), **d)


class TrackManager:
    """Manages all survey tracks for the project."""

    def __init__(self):
        self._tracks = {}  # id -> Track
        self._active_track = None
        self._lock = threading.Lock()
        self._load_all()

    def _tracks_dir(self):
        TRACKS_DIR.mkdir(parents=True, exist_ok=True)
        return TRACKS_DIR

    def _load_all(self):
        """Load all track metadata from disk."""
        tracks_dir = self._tracks_dir()
        for f in tracks_dir.glob("*.json"):
            try:
                with open(f, "r") as fh:
                    data = json.load(fh)
                    track = Track.from_dict(data)
                    self._tracks[track.id] = track
            except Exception:
                pass

    def _save_track(self, track):
        """Save track metadata to disk."""
        path = self._tracks_dir() / f"{track.id}.json"
        with open(path, "w") as f:
            json.dump(track.to_dict(), f, indent=2)

    def create_track(self, project="", operator="", vehicle="custom",
                     vehicle_name="", instrument_height=0.0,
                     lever_arm=None, hz=5, cameras=None, name=None):
        """Create a new track."""
        track = Track(
            name=name,
            project=project,
            operator=operator,
            vehicle=vehicle,
            vehicle_name=vehicle_name,
            instrument_height=instrument_height,
            lever_arm=lever_arm or {"x": 0.0, "y": 0.0, "z": 0.0},
            hz=hz,
            cameras=cameras or [],
            status="idle",
        )
        if not track.name or track.name == f"Track_{track.id}":
            track.name = f"{vehicle_name or vehicle}_{track.id}"

        with self._lock:
            self._tracks[track.id] = track
        self._save_track(track)
        return track

    def start_track(self, track_id, start_lat=0.0, start_lon=0.0):
        """Mark a track as recording."""
        with self._lock:
            track = self._tracks.get(track_id)
            if not track:
                return None
            track.status = "recording"
            track.start_time = datetime.now().isoformat()
            track.start_lat = start_lat
            track.start_lon = start_lon
            self._active_track = track
        self._save_track(track)
        return track

    def stop_track(self, track_id, records=0, duration_s=0,
                   files=None, end_lat=0.0, end_lon=0.0,
                   distance_m=0.0, avg_speed_kmh=0.0, fix_stats=None):
        """Mark a track as completed."""
        with self._lock:
            track = self._tracks.get(track_id)
            if not track:
                return None
            track.status = "completed"
            track.end_time = datetime.now().isoformat()
            track.records = records
            track.duration_s = duration_s
            track.files = files or []
            track.end_lat = end_lat
            track.end_lon = end_lon
            track.distance_m = distance_m
            track.avg_speed_kmh = avg_speed_kmh
            track.fix_stats = fix_stats or {}
            if self._active_track and self._active_track.id == track_id:
                self._active_track = None
        self._save_track(track)
        return track

    def get_track(self, track_id):
        with self._lock:
            t = self._tracks.get(track_id)
            return t.to_dict() if t else None

    def get_active_track(self):
        with self._lock:
            return self._active_track.to_dict() if self._active_track else None

    def list_tracks(self, vehicle=None, project=None):
        """List tracks, optionally filtered by vehicle/project."""
        with self._lock:
            tracks = list(self._tracks.values())

        if vehicle:
            tracks = [t for t in tracks if t.vehicle == vehicle]
        if project:
            tracks = [t for t in tracks if t.project == project]

        # Sort newest first
        tracks.sort(key=lambda t: t.start_time or t.id, reverse=True)
        return [t.to_dict() for t in tracks]

    def group_by_vehicle(self, project=None):
        """Group tracks by vehicle type."""
        all_tracks = self.list_tracks(project=project)
        groups = {}
        for t in all_tracks:
            v = t.get("vehicle", "unknown")
            if v not in groups:
                groups[v] = {"vehicle": v, "vehicle_name": t.get("vehicle_name", v), "tracks": []}
            groups[v]["tracks"].append(t)
        return groups

    def group_by_project(self):
        """Group tracks by project name."""
        all_tracks = self.list_tracks()
        groups = {}
        for t in all_tracks:
            p = t.get("project", "Unassigned") or "Unassigned"
            if p not in groups:
                groups[p] = {"project": p, "tracks": []}
            groups[p]["tracks"].append(t)
        return groups

    def delete_track(self, track_id):
        with self._lock:
            if track_id in self._tracks:
                del self._tracks[track_id]
        path = self._tracks_dir() / f"{track_id}.json"
        if path.exists():
            path.unlink()

    def update_notes(self, track_id, notes):
        with self._lock:
            track = self._tracks.get(track_id)
            if track:
                track.notes = notes
                self._save_track(track)
                return True
        return False

    def get_stats(self):
        """Get summary statistics."""
        with self._lock:
            tracks = list(self._tracks.values())
        total = len(tracks)
        completed = sum(1 for t in tracks if t.status == "completed")
        recording = sum(1 for t in tracks if t.status == "recording")
        total_records = sum(t.records for t in tracks)
        total_distance = sum(t.distance_m for t in tracks)
        total_duration = sum(t.duration_s for t in tracks)
        vehicles = list(set(t.vehicle for t in tracks))
        return {
            "total_tracks": total,
            "completed": completed,
            "recording": recording,
            "total_records": total_records,
            "total_distance_m": round(total_distance, 1),
            "total_duration_s": round(total_duration, 1),
            "vehicles_used": vehicles,
        }
