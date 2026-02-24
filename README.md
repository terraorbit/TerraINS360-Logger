# TerraINS360 Logger v3.1

**Professional GNSS Survey & Mobile Mapping Platform**
*A Startup India Initiative*

Real-time GNSS data acquisition platform for road asset inventory, infrastructure mapping, and geospatial survey. Runs as a web dashboard on desktop browsers and as a native Android APK.

---

## Features

- **Live GNSS Dashboard** — Real-time position, fix quality, satellite count, HDOP, speed, and fix history chart
- **NTRIP RTK Corrections** — Connect to any NTRIP caster for DGPS/RTK Float/RTK Fixed positioning
- **Survey Recording** — Log GNSS data at configurable rates (1–10 Hz) in CSV + NMEA format
- **Multi-Project Management** — Create, switch, and export projects with operator/client metadata
- **Track Catalogue** — Browse, export (CSV/NMEA/KML/GeoParquet), and delete survey tracks
- **Vehicle Profiles** — Pre-configured lever arm offsets for cars, bikes, walking, and custom setups
- **RBAC Authentication** — JWT-based login with SuperAdmin (1) + Users (N) roles
- **Cloud S3 Sync** — Auto-upload tracks as GeoParquet to AWS S3 with offline queue
- **PWA Support** — Installable progressive web app with offline caching via service worker
- **Android APK** — Native Android app built with Capacitor for field use on phones/tablets

## Hardware Support

| Device | Connection | Protocol |
|--------|-----------|----------|
| GeoMate SG7 (UM980) | TCP `192.168.1.1:1212` | NMEA 0183 |
| Any TCP NMEA receiver | Configurable host:port | GGA/GST/RMC/GSA |

## Architecture

```
TerraFusion/
├── main.py                 # FastAPI server (port 9360)
├── gnss_manager.py         # TCP NMEA connection & parsing
├── ntrip_client.py         # NTRIP v1 RTCM corrections client
├── nmea_logger.py          # Survey data recorder (CSV + NMEA)
├── track_manager.py        # Track catalogue & metadata
├── config.py               # Persistent JSON configuration
├── auth.py                 # JWT + bcrypt RBAC authentication
├── cloud_sync.py           # AWS S3 sync with offline queue
├── lever_arm.py            # Antenna offset & camera position math
├── camera_controller.py    # Camera integration (future)
├── requirements.txt        # Python dependencies
├── capacitor.config.json   # Android build configuration
├── package.json            # Node.js / Capacitor dependencies
├── static/                 # Desktop web UI
│   ├── index.html          # Single-page application
│   ├── manifest.json       # PWA manifest
│   ├── sw.js               # Service worker for offline cache
│   ├── icon-192.png
│   └── icon-512.png
├── www/                    # Android APK embedded UI
│   └── index.html          # Mobile-optimized SPA
├── android/                # Capacitor Android project
│   ├── app/
│   │   ├── build.gradle
│   │   ├── terrains360.keystore
│   │   └── src/
│   └── gradlew.bat
└── data/                   # Runtime data (auto-created)
    ├── terrafusion_config.json
    ├── users.json
    ├── sessions/           # Recorded survey CSVs & NMEAs
    └── tracks/             # GeoParquet exports
```

---

## Setup

### Prerequisites

- **Python 3.11+** (tested with 3.13)
- **Node.js 18+** (for Android APK builds only)
- **Java 21** (for Android APK builds only)
- **Android SDK 34+** (for Android APK builds only)

### 1. Desktop / Server Setup

```bash
# Clone or copy the TerraFusion folder

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# Install Python dependencies
pip install fastapi uvicorn pydantic bleak bcrypt pyjwt

# Run the server
cd TerraFusion
python main.py
```

Open **http://localhost:9360** in any browser.

**Default login:**
- Username: `admin`
- Password: `admin123`

### 2. Android APK Build

```bash
# Install Capacitor dependencies
npm install

# Sync web assets to Android project
npx cap sync android

# Set environment variables (Windows)
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-21.0.6.7-hotspot"
$env:ANDROID_HOME = "C:\Users\HP\Android\Sdk"

# Build signed release APK
cd android
.\gradlew.bat clean assembleRelease

# APK output location:
# android/app/build/outputs/apk/release/app-release.apk
```

### 3. GNSS Connection

1. Connect your PC/phone to the SG7's Wi-Fi network
2. The receiver defaults to TCP `192.168.1.1:1212`
3. Open the Dashboard tab → click **Connect** under GNSS
4. Position data streams automatically once connected

### 4. NTRIP RTK Setup

For SG7 base station corrections:

| Field | Value |
|-------|-------|
| Host | `211.144.120.97` |
| Port | `2201` |
| Mountpoint | SG7 serial number (e.g. `4661139`) |
| Username | `a` |
| Password | `a` |

1. Start the base SG7 using the MateSurvey controller app
2. Enter the NTRIP settings in the Dashboard tab
3. Click **Connect** — fix should upgrade from Single → DGPS → RTK Float → RTK Fixed

### 5. Network / Remote Access

The server binds to `0.0.0.0:9360`, accessible from any device on the same network.

For internet access via Cloudflare:
- Create an A record pointing to your public IP (proxied)
- Add an Origin Rule to rewrite destination port to `9360`
- Set SSL mode to **Flexible**

---

## Usage Guide

### Desktop Field Deployment (Laptop, No Mobile)

If the user is in the field with only a laptop (no Android phone), the system works entirely through the browser:

1. **Connect laptop to SG7 WiFi** — the GeoMate SG7 receiver creates a hotspot at `192.168.1.1`
2. **Start the server** on the laptop:
   ```bash
   cd TerraFusion
   python main.py
   ```
3. **Open browser** (Chrome/Edge) → `http://localhost:9360`
4. The full UI loads from `static/index.html` — identical features to the mobile APK

**Internet for NTRIP RTK:** The laptop needs internet access to receive RTK corrections via NTRIP. Options:
- **USB tethering** from a phone for internet + WiFi connected to SG7
- **Dual WiFi adapter** — one adapter for SG7 hotspot, another for internet
- **Record without RTK** — collect data with standalone fix, post-process later

> **Note:** The desktop browser UI and mobile APK both connect to the same Python backend. The only difference is the APK bundles the UI for offline Android use via Capacitor. If both laptop and phone are on the SG7 network, either can access `http://<laptop-ip>:9360`.

### Survey Workflow

1. **Login** with your credentials
2. **Create a Project** (Projects tab) with name, operator, client info
3. **Connect GNSS** — wait for a valid fix (RTK Fixed is best)
4. **Set Instrument Height** — enter the antenna height above ground
5. **Start Survey** — begins recording at the configured Hz rate
6. **Drive/Walk** the survey route
7. **Stop Survey** — track is saved and auto-synced to cloud (if configured)

### Export Formats

| Format | Description |
|--------|-------------|
| **CSV** | Timestamped lat/lon/alt/fix/sats/hdop/speed columns |
| **NMEA** | Raw NMEA sentences (GGA, GST, RMC, GSA) |
| **KML** | Google Earth track visualization with start/end markers |
| **GeoParquet** | Columnar geospatial format for GIS analysis |

### Admin Panel (SuperAdmin only)

- **User Management** — Create/edit/disable user accounts
- **GNSS Devices** — Add/remove/configure multiple receivers
- **Cloud Sync** — Configure S3 bucket, view sync queue
- **Vehicle Profiles** — Define lever arm offsets per vehicle

---

## API Reference

All endpoints are served at `http://localhost:9360/api/`.

### Authentication
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/login` | Login with username/password |
| POST | `/api/auth/logout` | Clear session |
| GET | `/api/auth/me` | Current user info |

### GNSS
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Full system status (GNSS + NTRIP + logging) |
| POST | `/api/gnss/connect` | Connect to GNSS receiver |
| POST | `/api/gnss/disconnect` | Disconnect GNSS |
| POST | `/api/gnss/instrument_height` | Set antenna height |

### NTRIP
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/ntrip/connect` | Connect to NTRIP caster |
| POST | `/api/ntrip/disconnect` | Disconnect NTRIP |
| GET | `/api/ntrip/sourcetable` | List available mountpoints |

### Survey
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/survey/start` | Start recording |
| POST | `/api/survey/stop` | Stop recording |

### Tracks
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/tracks` | List all tracks |
| GET | `/api/tracks/{id}` | Get track details |
| GET | `/api/tracks/{id}/points` | Get GPS points for map |
| GET | `/api/tracks/{id}/export?format=csv` | Export track |
| DELETE | `/api/tracks/{id}` | Delete track |

### Projects
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/projects` | List projects |
| POST | `/api/projects/create` | Create project |
| POST | `/api/projects/switch` | Switch active project |
| GET | `/api/projects/export?project=NAME` | Export all project tracks |

### Config
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/config` | Get full configuration |
| POST | `/api/config/reset` | Reset to defaults (admin) |

---

## Common Issues

### Server won't start — "Address already in use"

Another process is using port 9360. Kill it:

```powershell
Get-NetTCPConnection -LocalPort 9360 -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

Then restart: `python main.py`

### GNSS shows "Invalid" or "No Data"

- Verify the SG7 is powered on and broadcasting Wi-Fi
- Confirm your PC/phone is connected to the SG7's Wi-Fi network
- Check the host/port (default: `192.168.1.1:1212`)
- The receiver needs clear sky view for satellite lock

### NTRIP won't connect

- **"Connection refused"** — Check host/port; use port `2201` (not `9901`)
- **"Authorization failed"** — Verify mountpoint matches the base SG7 serial number
- **"No data"** — Ensure the base SG7 is started via MateSurvey before connecting
- **Firewall** — Port 2201 must be reachable from your network

### Fix stuck at DGPS / Single

- NTRIP must be connected and receiving RTCM data (check byte counter)
- Base station needs clear sky view with 10+ satellites
- RTK Float typically within 30 seconds, RTK Fixed within 1–3 minutes
- Baseline distance affects convergence time (closer base = faster fix)

### Android APK won't connect to server

- The APK connects to the server IP configured in `www/index.html`
- Phone and server must be on the same network
- Update the API base URL in `www/index.html` if the server IP changes
- For remote access, use the Cloudflare subdomain URL

### Build fails — "JAVA_HOME not set"

```powershell
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-21.0.6.7-hotspot"
$env:ANDROID_HOME = "C:\Users\HP\Android\Sdk"
```

### Login fails after fresh install

Default credentials: `admin` / `admin123`. If the users.json is corrupted, delete it and restart — a fresh admin account is auto-created.

### Cloud sync not working

- Verify S3 credentials in Admin → Cloud settings
- Check the sync queue: `GET /api/admin/cloud/queue`
- Ensure the bucket exists and IAM user has `s3:PutObject` permission
- Offline uploads are queued and retried automatically

---

## Configuration

All settings are persisted in `data/terrafusion_config.json` and survive restarts. Key sections:

| Section | Description |
|---------|-------------|
| `gnss` | Receiver host, port, auto-connect, instrument height |
| `ntrip` | Caster host, port, mountpoint, credentials |
| `logging` | Hz rate, format (csv/nmea/both), file prefix |
| `vehicle` | Active vehicle, lever arm profiles |
| `cloud` | S3 bucket, credentials, auto-sync toggle |
| `project` | Active project metadata |
| `dashboard` | Port (9360), refresh rate, theme |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13, FastAPI, Uvicorn |
| Frontend | Vanilla HTML/CSS/JS (single-file SPA) |
| Auth | JWT (PyJWT) + bcrypt password hashing |
| Mobile | Capacitor 8.x → Android WebView APK |
| GNSS | TCP NMEA 0183 (GGA/GST/RMC/GSA) |
| RTK | NTRIP v1 client (RTCM3 corrections) |
| Cloud | AWS S3 (boto3) with GeoParquet export |
| Build | Gradle 8.x, Android SDK 34, Java 21 |

---

## License

Proprietary — TerraOrbit Technologies. All rights reserved.
