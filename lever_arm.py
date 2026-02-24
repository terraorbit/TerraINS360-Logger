"""
TerraFusion - Lever Arm Calculator
====================================
Applies antenna-to-camera offset corrections.
With instrument height, the flow is:

  Ground Level (GL)
       |
       | +instrument_height (GL → Antenna Phase Center)
       |
  GNSS Antenna (measurement point)
       |
       | +lever arm X/Y/Z (Antenna → Camera Lens)
       |
  Camera Lens (target point for geotagging)

The instrument front face aligns with trajectory direction.

X = Right(+) / Left(-)    (perpendicular to trajectory)
Y = Forward(+) / Backward(-)  (along trajectory)
Z = Up(+) / Down(-)       (vertical from antenna to camera)
"""

import math


def apply_lever_arm(lat, lon, alt, heading_deg, x, y, z, instrument_height=0.0):
    """
    Apply lever arm offsets to GNSS position to get camera lens position.

    The GNSS gives us the antenna phase center position.
    We transform to camera lens position using:
      1. Instrument aligned with trajectory (heading)
      2. Lever arm offset in body frame (X=right, Y=forward, Z=up)

    Args:
        lat: Latitude in decimal degrees (antenna position)
        lon: Longitude in decimal degrees (antenna position)
        alt: Ellipsoidal altitude in meters (antenna position)
        heading_deg: Vehicle heading in degrees (0=North, 90=East)
        x: Right offset in meters (antenna to camera)
        y: Forward offset in meters (antenna to camera)
        z: Up offset in meters (positive = camera above antenna)
        instrument_height: Height from GL to antenna phase center (meters)

    Returns:
        (camera_lat, camera_lon, camera_alt, camera_height_gl)
        camera_height_gl = height of camera lens above ground level
    """
    if x == 0 and y == 0 and z == 0:
        camera_height_gl = instrument_height
        return lat, lon, alt, camera_height_gl

    heading_rad = math.radians(heading_deg)

    # Convert body frame (x=right, y=forward) to North/East offsets
    # Instrument front face aligned with trajectory (heading direction)
    delta_north = y * math.cos(heading_rad) - x * math.sin(heading_rad)
    delta_east = y * math.sin(heading_rad) + x * math.cos(heading_rad)

    # Convert meters to degrees (approximate)
    meters_per_deg_lat = 111132.92
    meters_per_deg_lon = 111132.92 * math.cos(math.radians(lat))

    if meters_per_deg_lon == 0:
        meters_per_deg_lon = 1

    camera_lat = lat + delta_north / meters_per_deg_lat
    camera_lon = lon + delta_east / meters_per_deg_lon

    # Altitude: antenna_alt + z_offset = camera_alt
    # Z positive means camera is ABOVE antenna
    camera_alt = alt + z

    # Camera height above ground level
    # instrument_height is GL→antenna, z is antenna→camera
    camera_height_gl = instrument_height + z

    return camera_lat, camera_lon, camera_alt, camera_height_gl


def compute_camera_position(gnss_data, lever_arm, instrument_height=0.0):
    """
    Given a GNSS state snapshot, compute the camera lens position.

    Args:
        gnss_data: dict with latitude, longitude, altitude, course
        lever_arm: dict with x, y, z offsets
        instrument_height: GL to antenna (meters)

    Returns:
        dict with camera_lat, camera_lon, camera_alt, camera_height_gl
    """
    lat = gnss_data.get("latitude", 0.0)
    lon = gnss_data.get("longitude", 0.0)
    alt = gnss_data.get("altitude", 0.0)
    heading = gnss_data.get("course", 0.0)

    x = lever_arm.get("x", 0.0)
    y = lever_arm.get("y", 0.0)
    z = lever_arm.get("z", 0.0)

    cam_lat, cam_lon, cam_alt, cam_gl = apply_lever_arm(
        lat, lon, alt, heading, x, y, z, instrument_height
    )

    return {
        "camera_lat": cam_lat,
        "camera_lon": cam_lon,
        "camera_alt": cam_alt,
        "camera_height_gl": cam_gl,
        "antenna_lat": lat,
        "antenna_lon": lon,
        "antenna_alt": alt,
        "heading": heading,
    }


def offset_distance(x, y, z):
    """Calculate total 3D offset distance."""
    return math.sqrt(x**2 + y**2 + z**2)


def describe_offset(x, y, z, instrument_height=0.0):
    """Human-readable offset description."""
    parts = []
    if instrument_height > 0:
        parts.append(f"Instrument Height: {instrument_height:.3f}m (GL→Antenna)")
    if x != 0:
        parts.append(f"{'Right' if x > 0 else 'Left'} {abs(x):.3f}m")
    if y != 0:
        parts.append(f"{'Forward' if y > 0 else 'Back'} {abs(y):.3f}m")
    if z != 0:
        parts.append(f"Camera {'Above' if z > 0 else 'Below'} Antenna {abs(z):.3f}m")

    if not parts:
        return "No offset (camera co-located with antenna)"

    total_3d = offset_distance(x, y, z)
    cam_gl = instrument_height + z
    summary = f"Camera Lens: {cam_gl:.3f}m above GL | 3D offset: {total_3d:.3f}m"
    return " | ".join(parts) + f"\n{summary}"
