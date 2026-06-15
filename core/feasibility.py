"""
gARB — Deployment Feasibility Parameters
Road access, channel width, flow velocity gates, land ownership,
bank slope stability, bridge proximity bonus.
"""

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from core import UTM_CRS, WGS84, safe_call


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
    Primary: NHD width attribute. Fallback: bridge span. Last resort: Strahler estimate.
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

    # Method 3: Strahler-based estimate (Leopold 1964)
    strahler = nearest_stream.get("strahler_order", 2)
    return 2.5 * (2.5 ** int(strahler))


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

def passes_hard_gates(velocity_ms=None, width_m=None, land_ownership=None):
    """Check if a candidate passes all hard feasibility gates."""
    if velocity_ms is not None and velocity_ms > 3.0:
        return False  # too fast
    if width_m is not None and (width_m > 50 or width_m < 0.5):
        return False  # too wide or too narrow
    if land_ownership is not None and land_ownership == 0.0:
        return False  # confirmed private
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
                                  nbi_gdf=None, dem_array=None, pixel_size_m=10):
    """Compute all feasibility features for a single candidate."""
    point_utm = candidate_row.geometry
    lat = candidate_row.get("lat", 36.0)
    lon = candidate_row.get("lon", -78.9)
    velocity = candidate_row.get("flow_velocity_ms", 0.5)

    # Road access
    road_dist = safe_call(compute_road_access_distance, lon, lat, default=500.0)

    # Channel width
    width = 5.0
    if stream_gdf is not None and not stream_gdf.empty:
        width = safe_call(get_channel_width, point_utm, stream_gdf, nbi_gdf, default=5.0)

    # Bank slope
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
