"""
GRIME — Deployment Feasibility Parameters
Road access, channel width, flow velocity gates, land ownership,
bank slope stability, bridge proximity bonus.
"""

import math

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from core import UTM_CRS, WGS84, safe_call, osm_drive_graph


# ── 7.1 Road Access Distance (OSMnx) ────────────────────────────────

def compute_road_access_distance(candidate_lon, candidate_lat):
    """
    Compute distance from candidate site to nearest driveable road.
    Uses OSMnx to pull the road network.
    """
    try:
        import osmnx as ox
        G = ox.graph_from_point(
            (candidate_lat, candidate_lon),
            dist=2000, network_type="drive",
        )
        nearest_node = ox.nearest_nodes(G, candidate_lon, candidate_lat)
        nd = G.nodes[nearest_node]
        nearest_pt = Point(nd["x"], nd["y"])
        candidate_pt = Point(candidate_lon, candidate_lat)
        dist_deg = candidate_pt.distance(nearest_pt)
        dist_m = dist_deg * 111320  # rough degrees-to-meters
        return dist_m
    except Exception:
        return 500.0  # assume moderate access


def road_access_distance_cached(point_utm, bbox=None, lon=None, lat=None):
    """Distance (m) to the nearest road.

    Uses the cached bbox drive network (``core.osm_drive_graph`` — one Overpass
    fetch shared across all candidates, nearest node in metres) when ``bbox`` is
    available; otherwise falls back to the per-point graph query. This replaces a
    ~18 s/candidate ``graph_from_point`` call with a metre-accurate KD-tree lookup.
    """
    if bbox is not None:
        info = osm_drive_graph(bbox)
        if info is not None and info.get("nodes_utm") is not None and not info["nodes_utm"].empty:
            return float(info["nodes_utm"].geometry.distance(point_utm).min())
    if lon is not None and lat is not None:
        return compute_road_access_distance(lon, lat)
    return 500.0


def road_access_score(dist_m):
    """Score road accessibility — closer is better."""
    if dist_m < 200:
        return 1.0
    if dist_m < 500:
        return 0.8
    if dist_m < 1000:
        return 0.6
    if dist_m < 2000:
        return 0.3
    return 0.1


# ── 7.2 Channel Width ───────────────────────────────────────────────

def get_channel_width(candidate_point_utm, stream_gdf, nbi_bridges_gdf=None):
    """
    Estimate channel width at candidate point.
    Primary: NHD width attribute. Fallback: bridge span. Last resort: stream-order estimate.
    """
    if stream_gdf.empty:
        return 5.0

    nearest_idx = stream_gdf.geometry.distance(candidate_point_utm).argmin()
    nearest_stream = stream_gdf.iloc[nearest_idx]

    # Method 1: NHD width attribute
    nhd_width = nearest_stream.get("width_m") or nearest_stream.get("WBAREASM")
    if nhd_width and float(nhd_width) > 0:
        return float(nhd_width)

    # Method 2: Bridge span as proxy
    if nbi_bridges_gdf is not None and not nbi_bridges_gdf.empty:
        distances = nbi_bridges_gdf.geometry.distance(candidate_point_utm)
        if distances.min() < 500:
            nearest_bridge = nbi_bridges_gdf.iloc[distances.argmin()]
            bridge_len = pd.to_numeric(
                nearest_bridge.get("length_ft", 0), errors="coerce"
            )
            if bridge_len and bridge_len > 0:
                return float(bridge_len) * 0.3048

    # Method 3: order-based estimate, calibrated + bounded (M1).
    # NOTE: Leopold (1964) hydraulic geometry is W ∝ Q^~0.5, NOT an exponential in
    # stream order. The old 2.5·2.5^order exploded (order 5 → 244 m) and self-tripped
    # the width hard gate, silently deleting large streams. Bounded power law instead:
    # order 1→3.0, 2→6.4, 3→10.0, 4→13.7, 5→17.6 m — always below the width gate.
    order = int(nearest_stream.get("stream_order", 2))
    return float(min(40.0, 3.0 * order ** 1.1))


def channel_width_score(width_m):
    """Feasibility score — hard gates at extremes."""
    if width_m < 0.5:
        return 0.0
    if width_m < 2.0:
        return 0.5
    if width_m <= 15:
        return 1.0
    if width_m <= 30:
        return 0.5
    if width_m <= 50:
        return 0.2
    return 0.0  # hard gate


# ── 7.3 Bank Slope Stability ────────────────────────────────────────

# Input/measurement constants only. The shipped bank_slope_score curve below
# stays frozen; these constants make its input a real cross-bank measurement.
BANK_XS_HALF_WIDTH_M = 25.0
BANK_XS_NATIVE_SPACING_M = 3.125 * 0.3048
BANK_XS_TANGENT_REACH_M = 15.0
BANK_XS_CENTER_SEARCH_M = 3.0
BANK_XS_WINDOW_M = 5.0
BANK_XS_BANK_LIMIT_M = 20.0
BANK_XS_START_OFFSET_M = 1.0


def bank_cross_section_endpoints(candidate_point_utm, stream_gdf,
                                 half_width_m=BANK_XS_HALF_WIDTH_M,
                                 tangent_reach_m=BANK_XS_TANGENT_REACH_M,
                                 segment_id=None):
    """UTM endpoints of a transect perpendicular to the local stream tangent.

    The generated candidate carries its source ``segment_id``; nearest-stream
    lookup is retained as a defensive fallback for older candidate fixtures.
    """
    if stream_gdf is None or stream_gdf.empty:
        return None
    line = None
    if segment_id is not None and segment_id in stream_gdf.index:
        row = stream_gdf.loc[segment_id]
        line = row.geometry if hasattr(row, "geometry") else None
    if line is None:
        nearest_pos = stream_gdf.geometry.distance(candidate_point_utm).argmin()
        line = stream_gdf.iloc[nearest_pos].geometry
    if line is None or line.is_empty or line.length <= 0:
        return None

    along = float(line.project(candidate_point_utm))
    p0 = line.interpolate(max(0.0, along - float(tangent_reach_m)))
    p1 = line.interpolate(min(float(line.length), along + float(tangent_reach_m)))
    tx, ty = float(p1.x - p0.x), float(p1.y - p0.y)
    norm = math.hypot(tx, ty)
    if norm <= 1e-9:
        coords = list(line.coords)
        if len(coords) < 2:
            return None
        tx, ty = coords[-1][0] - coords[0][0], coords[-1][1] - coords[0][1]
        norm = math.hypot(tx, ty)
    if norm <= 1e-9:
        return None
    nx, ny = -ty / norm, tx / norm
    h = float(half_width_m)
    return (Point(candidate_point_utm.x - nx * h, candidate_point_utm.y - ny * h),
            Point(candidate_point_utm.x + nx * h, candidate_point_utm.y + ny * h))


def bank_slope_from_profile(distances_m, elevations_m,
                            center_search_m=BANK_XS_CENTER_SEARCH_M,
                            window_m=BANK_XS_WINDOW_M,
                            bank_limit_m=BANK_XS_BANK_LIMIT_M,
                            start_offset_m=BANK_XS_START_OFFSET_M):
    """Robust cross-section bank angle in degrees from a 1 m profile.

    The channel center is the lowest sample within ``center_search_m`` of the
    transect midpoint (tolerating a small DEM/vector offset). On each bank, take
    the steepest positive elevation rise over a fixed 5 m window within 20 m of
    the channel, then average the two bank angles. A 5 m rise window suppresses
    pixel noise while preserving the steep Piedmont banks that a 10 m 3x3 mean
    blurred below the frozen 15-degree score threshold.
    """
    try:
        d = np.asarray(distances_m, dtype=float)
        z = np.asarray(elevations_m, dtype=float)
    except (TypeError, ValueError):
        return None
    valid = np.isfinite(d) & np.isfinite(z)
    if valid.sum() < 5:
        return None
    d, z = d[valid], z[valid]
    order = np.argsort(d)
    d, z = d[order], z[order]
    if d[-1] - d[0] < 2 * (float(start_offset_m) + float(window_m)):
        return None

    midpoint = (float(d[0]) + float(d[-1])) / 2.0
    center_mask = np.abs(d - midpoint) <= float(center_search_m)
    if not center_mask.any():
        return None
    center_indices = np.flatnonzero(center_mask)
    center = float(d[center_indices[np.argmin(z[center_mask])]])

    step = max(1.0, float(np.median(np.diff(np.unique(d)))))
    offsets = np.arange(float(start_offset_m),
                        float(bank_limit_m) - float(window_m) + step / 2,
                        step)
    side_angles = []
    for sign in (-1.0, 1.0):
        grades = []
        for off in offsets:
            inner_d = center + sign * off
            outer_d = center + sign * (off + float(window_m))
            if min(inner_d, outer_d) < d[0] or max(inner_d, outer_d) > d[-1]:
                continue
            inner_z = float(np.interp(inner_d, d, z))
            outer_z = float(np.interp(outer_d, d, z))
            grades.append(max(0.0, (outer_z - inner_z) / float(window_m)))
        if grades:
            side_angles.append(math.degrees(math.atan(max(grades))))
    return float(np.mean(side_angles)) if side_angles else None


def bank_profile_points(candidate_row, stream_gdf):
    """Return ``(distances_m, points_utm)`` across a candidate's two banks."""
    endpoints = bank_cross_section_endpoints(
        candidate_row.geometry, stream_gdf,
        segment_id=candidate_row.get("segment_id"))
    if endpoints is None:
        return None
    p0, p1 = endpoints
    length = float(p0.distance(p1))
    if length <= 0:
        return None
    n_points = max(3, int(round(length / BANK_XS_NATIVE_SPACING_M)) + 1)
    distances = np.linspace(0.0, length, n_points)
    ux, uy = (p1.x - p0.x) / length, (p1.y - p0.y) / length
    points = [Point(p0.x + ux * d, p0.y + uy * d) for d in distances]
    return distances, points


def _bank_info_from_samples(distances, samples):
    elevations = [np.nan if sample is None else sample["elevation_m"]
                  for sample in samples]
    slope = bank_slope_from_profile(distances, elevations)
    if slope is None:
        return None
    sources = sorted({sample["source"] for sample in samples if sample is not None})
    return {"slope_deg": float(slope), "sources": sources,
            "n_samples": sum(sample is not None for sample in samples),
            "n_expected": len(samples)}


def compute_bank_slope_nc_lidar(candidate_row, stream_gdf):
    """Measure one bank profile from the statewide 3.125-ft NC lidar DEM."""
    profile = bank_profile_points(candidate_row, stream_gdf)
    if profile is None:
        return None
    distances, points = profile
    from core.real_sources import (NC_LIDAR_SOURCE_CRS_EPSG,
                                   nc_lidar_elevations)
    try:
        stateplane = gpd.GeoSeries(points, crs=stream_gdf.crs).to_crs(
            f"EPSG:{NC_LIDAR_SOURCE_CRS_EPSG}")
        coords = [(float(p.x), float(p.y)) for p in stateplane]
    except Exception:
        return None
    return _bank_info_from_samples(distances, nc_lidar_elevations(coords))


def compute_bank_slopes_nc_lidar(candidates, stream_gdf):
    """Batch every candidate cross-section into light ArcGIS multipoint calls."""
    profiles = [bank_profile_points(row, stream_gdf)
                for _, row in candidates.iterrows()]
    flat_points = []
    slices = []
    for profile in profiles:
        start = len(flat_points)
        if profile is not None:
            flat_points.extend(profile[1])
        slices.append((start, len(flat_points)))
    if not flat_points:
        return [None] * len(profiles)
    from core.real_sources import (NC_LIDAR_SOURCE_CRS_EPSG,
                                   nc_lidar_elevations)
    try:
        stateplane = gpd.GeoSeries(flat_points, crs=stream_gdf.crs).to_crs(
            f"EPSG:{NC_LIDAR_SOURCE_CRS_EPSG}")
        coords = [(float(p.x), float(p.y)) for p in stateplane]
    except Exception:
        return [None] * len(profiles)
    samples = nc_lidar_elevations(coords)
    out = []
    for profile, (start, end) in zip(profiles, slices):
        out.append(None if profile is None else
                   _bank_info_from_samples(profile[0], samples[start:end]))
    return out

def compute_bank_slope(candidate_row, candidate_col, dem_array, pixel_size_m):
    """
    Compute bank slope (degrees) at candidate site from DEM gradient.
    Looks at cross-channel slope (perpendicular to flow direction).
    """
    r, c = int(candidate_row), int(candidate_col)
    rows, cols = dem_array.shape

    # Sample a 3x3 neighborhood
    r_min, r_max = max(0, r - 1), min(rows - 1, r + 1)
    c_min, c_max = max(0, c - 1), min(cols - 1, c + 1)

    neighborhood = dem_array[r_min:r_max + 1, c_min:c_max + 1]
    if neighborhood.size < 4:
        return 10.0

    # Gradient magnitude
    gy, gx = np.gradient(neighborhood, pixel_size_m)
    slope_rad = np.arctan(np.sqrt(gx ** 2 + gy ** 2))
    slope_deg = float(np.degrees(np.mean(slope_rad)))
    return slope_deg


def bank_slope_score(slope_deg):
    """Score bank slope — gentle slopes are easier to access."""
    if slope_deg < 15:
        return 1.0
    if slope_deg < 30:
        return 0.5
    if slope_deg < 45:
        return 0.2
    return 0.1


# ── 7.4 Land Ownership ──────────────────────────────────────────────

def get_land_ownership(candidate_point_utm):
    """
    Check if land at candidate is public (1.0), unknown (0.5), or private (0.0).
    Uses PAD-US if available, otherwise defaults to unknown.
    """
    try:
        padus = gpd.read_file("data/padus.gpkg", layer="PADUS3_0Combined_Proclamation")
        padus = padus.to_crs(UTM_CRS)
        containing = padus[padus.contains(candidate_point_utm)]
        if not containing.empty:
            return 1.0  # public land
        return 0.5  # unknown — not in PAD-US
    except Exception:
        return 0.5  # assume unknown


# ── 7.5 Bridge Proximity Bonus ───────────────────────────────────────

def bridge_proximity_bonus(candidate_point_utm, nbi_bridges_gdf=None, threshold_m=50):
    """
    Bonus if a bridge is within threshold distance — structural anchor point.
    Returns 0.2 bonus if bridge is nearby, 0 otherwise.
    """
    if nbi_bridges_gdf is None or nbi_bridges_gdf.empty:
        return 0.0
    min_dist = nbi_bridges_gdf.geometry.distance(candidate_point_utm).min()
    return 0.2 if min_dist < threshold_m else 0.0


# ── Hard Gates ───────────────────────────────────────────────────────

NAVIGABLE_GATE_M = 100.0  # min distance (m) to a USACE NWN navigable segment


def passes_hard_gates(velocity_ms=None, width_m=None, land_ownership=None,
                      navigable_dist_m=None):
    """Check if a candidate passes all hard feasibility gates."""
    if velocity_ms is not None and velocity_ms > 3.0:
        return False  # too fast
    if width_m is not None and (width_m > 50 or width_m < 0.5):
        return False  # too wide or too narrow
    if land_ownership is not None and land_ownership == 0.0:
        return False  # confirmed private
    if navigable_dist_m is not None and navigable_dist_m <= NAVIGABLE_GATE_M:
        # On legally navigable water (USACE Section 10): a stationary net is
        # unsafe/illegal in a shipping channel (fix-pass-2 Phase 3 gate).
        return False
    return True


# ── Aggregate Feasibility Features ───────────────────────────────────

FEASIBILITY_WEIGHTS = {
    "road_access_score": 0.25,
    "channel_width_score": 0.20,
    "velocity_feasibility": 0.20,
    "land_ownership": 0.15,
    "bank_slope_score": 0.10,
    "bridge_proximity_bonus": 0.10,
}


def compute_feasibility_features(candidate_row, stream_gdf=None,
                                  nbi_gdf=None, dem_array=None, pixel_size_m=10,
                                  bbox=None):
    """Compute all feasibility features for a single candidate."""
    point_utm = candidate_row.geometry
    lat = candidate_row.get("lat", 36.0)
    lon = candidate_row.get("lon", -78.9)
    velocity = candidate_row.get("flow_velocity_ms", 0.5)

    # Road access — nearest road via the cached bbox drive network (metres).
    road_dist = safe_call(road_access_distance_cached, point_utm, bbox, lon, lat,
                          default=500.0)

    # Channel width
    width = 5.0
    if stream_gdf is not None and not stream_gdf.empty:
        width = safe_call(get_channel_width, point_utm, stream_gdf, nbi_gdf, default=5.0)

    # Bank slope: official statewide NC 3.125-ft lidar profile. Preserve the
    # documented 10 m 3DEP DEM-gradient fallback when samples are unavailable.
    slope_info = safe_call(compute_bank_slope_nc_lidar, candidate_row, stream_gdf,
                           default=None)
    slope = slope_info["slope_deg"] if slope_info is not None else None
    if slope is None:
        slope = 10.0
        if dem_array is not None:
            slope = safe_call(
                compute_bank_slope,
                candidate_row.get("pixel_row", 0),
                candidate_row.get("pixel_col", 0),
                dem_array, pixel_size_m,
                default=10.0,
            )

    features = {
        "road_access_score": road_access_score(road_dist),
        "road_access_m": road_dist,
        "channel_width_score": channel_width_score(width),
        "channel_width_m": width,
        "velocity_feasibility": velocity_feasibility(velocity),
        "land_ownership": safe_call(get_land_ownership, point_utm, default=0.5),
        "bank_slope_score": bank_slope_score(slope),
        "bank_slope_deg": slope,
        "bridge_proximity_bonus": safe_call(
            bridge_proximity_bonus, point_utm, nbi_gdf, default=0.0
        ),
    }
    return features


# Import velocity_feasibility from flow module for consistency
from core.flow import velocity_feasibility
