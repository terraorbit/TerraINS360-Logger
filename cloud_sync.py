"""
TerraINS360 - Cloud Sync Module (S3)
======================================
Syncs config, tracks (GeoParquet), and session data to AWS S3.
Includes offline queue with auto-sync when connectivity returns.
"""

import json
import time
import threading
import os
from pathlib import Path
from datetime import datetime

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

try:
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point
    HAS_GEO = True
except ImportError:
    HAS_GEO = False

SYNC_DIR = Path(__file__).parent / "data"
QUEUE_FILE = SYNC_DIR / "sync_queue.json"
SYNC_STATE_FILE = SYNC_DIR / "sync_state.json"


class CloudSync:
    """Manages S3 cloud sync with offline queue."""

    def __init__(self):
        self._s3 = None
        self._bucket = ""
        self._prefix = "terrains360/"
        self._configured = False
        self._online = False
        self._lock = threading.Lock()
        self._queue = []
        self._sync_thread = None
        self._running = False
        self._last_sync = ""
        self._sync_stats = {"uploaded": 0, "failed": 0, "queued": 0}
        self._load_queue()
        self._load_state()

    def configure(self, aws_access_key: str, aws_secret_key: str,
                  bucket: str, region: str = "ap-south-1",
                  prefix: str = "terrains360/"):
        """Configure S3 connection."""
        if not HAS_BOTO3:
            return {"error": "boto3 not installed"}
        try:
            self._s3 = boto3.client(
                "s3",
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key,
                region_name=region,
            )
            self._bucket = bucket
            self._prefix = prefix
            self._configured = True
            # Test connectivity
            self._s3.head_bucket(Bucket=bucket)
            self._online = True
            return {"status": "connected", "bucket": bucket}
        except NoCredentialsError:
            return {"error": "Invalid AWS credentials"}
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "404":
                return {"error": f"Bucket '{bucket}' not found"}
            elif code == "403":
                return {"error": "Access denied to bucket"}
            return {"error": str(e)}
        except Exception as e:
            self._configured = True  # Save config even if offline
            self._online = False
            return {"status": "configured_offline", "error": str(e)}

    def check_connectivity(self) -> bool:
        """Check if S3 is reachable."""
        if not self._configured or not self._s3:
            return False
        try:
            self._s3.head_bucket(Bucket=self._bucket)
            self._online = True
            return True
        except Exception:
            self._online = False
            return False

    # ── Queue Management ──

    def _load_queue(self):
        SYNC_DIR.mkdir(parents=True, exist_ok=True)
        if QUEUE_FILE.exists():
            try:
                with open(QUEUE_FILE, "r") as f:
                    self._queue = json.load(f)
            except Exception:
                self._queue = []

    def _save_queue(self):
        SYNC_DIR.mkdir(parents=True, exist_ok=True)
        with open(QUEUE_FILE, "w") as f:
            json.dump(self._queue, f, indent=2)

    def _load_state(self):
        if SYNC_STATE_FILE.exists():
            try:
                with open(SYNC_STATE_FILE, "r") as f:
                    state = json.load(f)
                    self._last_sync = state.get("last_sync", "")
                    self._sync_stats = state.get("stats", self._sync_stats)
            except Exception:
                pass

    def _save_state(self):
        SYNC_DIR.mkdir(parents=True, exist_ok=True)
        with open(SYNC_STATE_FILE, "w") as f:
            json.dump({
                "last_sync": self._last_sync,
                "stats": self._sync_stats,
            }, f, indent=2)

    def enqueue(self, action: str, local_path: str, s3_key: str,
                metadata: dict = None):
        """Add an item to the sync queue."""
        with self._lock:
            item = {
                "id": f"sync_{int(time.time()*1000)}",
                "action": action,  # upload, delete
                "local_path": local_path,
                "s3_key": s3_key,
                "metadata": metadata or {},
                "created": datetime.now().isoformat(),
                "status": "pending",  # pending, uploading, done, failed
                "retries": 0,
                "error": "",
            }
            self._queue.append(item)
            self._sync_stats["queued"] = len([q for q in self._queue if q["status"] == "pending"])
            self._save_queue()
            return item

    # ── Upload Operations ──

    def upload_file(self, local_path: str, s3_key: str) -> dict:
        """Upload a file to S3 immediately or queue if offline."""
        full_key = f"{self._prefix}{s3_key}"

        if self._online and self._s3:
            try:
                self._s3.upload_file(local_path, self._bucket, full_key)
                self._sync_stats["uploaded"] += 1
                self._last_sync = datetime.now().isoformat()
                self._save_state()
                return {"status": "uploaded", "key": full_key}
            except Exception as e:
                # Queue for later
                self.enqueue("upload", local_path, full_key)
                return {"status": "queued", "error": str(e)}
        else:
            self.enqueue("upload", local_path, full_key)
            return {"status": "queued", "reason": "offline"}

    def upload_config(self, config_data: dict) -> dict:
        """Upload config JSON to S3."""
        config_path = SYNC_DIR / "config_upload.json"
        with open(config_path, "w") as f:
            json.dump(config_data, f, indent=2)
        return self.upload_file(str(config_path), "config/terrains360_config.json")

    def upload_track_geoparquet(self, track: dict, csv_path: str = None) -> dict:
        """Convert track to GeoParquet and upload."""
        if not HAS_GEO:
            return {"error": "geopandas/pyarrow not installed"}

        track_id = track.get("id", "unknown")

        # If CSV data exists, convert to GeoParquet
        if csv_path and Path(csv_path).exists():
            try:
                df = pd.read_csv(csv_path)
                if "Latitude" in df.columns and "Longitude" in df.columns:
                    geometry = [
                        Point(lon, lat)
                        for lat, lon in zip(df["Latitude"], df["Longitude"])
                    ]
                    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
                else:
                    gdf = gpd.GeoDataFrame(df)

                parquet_path = SYNC_DIR / f"tracks/{track_id}.parquet"
                parquet_path.parent.mkdir(parents=True, exist_ok=True)
                gdf.to_parquet(str(parquet_path))

                # Upload parquet
                result = self.upload_file(
                    str(parquet_path),
                    f"tracks/{track_id}.parquet"
                )

                # Also upload track metadata
                meta_path = SYNC_DIR / f"tracks/{track_id}_meta.json"
                with open(meta_path, "w") as f:
                    json.dump(track, f, indent=2)
                self.upload_file(str(meta_path), f"tracks/{track_id}_meta.json")

                return result
            except Exception as e:
                return {"error": f"GeoParquet conversion failed: {e}"}
        else:
            # Just upload track metadata
            meta_path = SYNC_DIR / f"tracks/{track_id}_meta.json"
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            with open(meta_path, "w") as f:
                json.dump(track, f, indent=2)
            return self.upload_file(str(meta_path), f"tracks/{track_id}_meta.json")

    # ── Sync Engine ──

    def start_sync_worker(self):
        """Start background sync worker."""
        if self._running:
            return
        self._running = True
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_thread.start()

    def stop_sync_worker(self):
        self._running = False

    def _sync_loop(self):
        """Background loop: check connectivity, process queue."""
        while self._running:
            try:
                if self.check_connectivity():
                    self._process_queue()
                time.sleep(30)  # Check every 30s
            except Exception:
                time.sleep(60)

    def _process_queue(self):
        """Process pending items in sync queue."""
        with self._lock:
            pending = [q for q in self._queue if q["status"] == "pending"]

        for item in pending:
            if not self._online:
                break
            try:
                if item["action"] == "upload":
                    if Path(item["local_path"]).exists():
                        self._s3.upload_file(
                            item["local_path"],
                            self._bucket,
                            item["s3_key"]
                        )
                        item["status"] = "done"
                        self._sync_stats["uploaded"] += 1
                    else:
                        item["status"] = "failed"
                        item["error"] = "File not found"
                        self._sync_stats["failed"] += 1

                elif item["action"] == "delete":
                    self._s3.delete_object(Bucket=self._bucket, Key=item["s3_key"])
                    item["status"] = "done"

            except Exception as e:
                item["retries"] += 1
                item["error"] = str(e)
                if item["retries"] >= 5:
                    item["status"] = "failed"
                    self._sync_stats["failed"] += 1

        # Clean done items older than 1 hour
        with self._lock:
            self._queue = [
                q for q in self._queue
                if q["status"] != "done"
            ]
            self._sync_stats["queued"] = len([q for q in self._queue if q["status"] == "pending"])

        self._last_sync = datetime.now().isoformat()
        self._save_queue()
        self._save_state()

    def force_sync(self) -> dict:
        """Force immediate sync attempt."""
        if not self._configured:
            return {"error": "S3 not configured"}
        if self.check_connectivity():
            self._process_queue()
            return {"status": "synced", "stats": self._sync_stats}
        return {"status": "offline", "queued": self._sync_stats["queued"]}

    def get_status(self) -> dict:
        """Get sync status."""
        return {
            "configured": self._configured,
            "online": self._online,
            "bucket": self._bucket,
            "last_sync": self._last_sync,
            "stats": dict(self._sync_stats),
            "queue_size": len(self._queue),
            "pending": len([q for q in self._queue if q["status"] == "pending"]),
        }

    def get_queue(self) -> list:
        """Get current sync queue."""
        return [
            {k: v for k, v in q.items() if k != "local_path"}
            for q in self._queue
        ]


cloud_sync = CloudSync()
