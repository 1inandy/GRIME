"""GRIME utilities — shared helpers across all modules."""

import os
import numpy as np
import geopandas as gpd
from shapely.geometry import Point


# ── Constants ────────────────────────────────────────────────────────
# NOTE: these defaults are scoped to the Durham, NC / Ellerbe Creek case study.
# State/county FIPS and the UTM zone are passed explicitly for other regions.
ELLERBE_BBOX = (-79.05, 35.90, -78.75, 36.05)
ELLERBE_GAUGE = "02086849"
# Approx. drainage area above USGS gauge 02086849 (Ellerbe Creek at Club Blvd),
# used to area-scale gauge discharge to a candidate's catchment (M4). ~8.2 mi².
ELLERBE_DRAINAGE_KM2 = 21.2
DURHAM_STATE_FIPS = "37"
DURHAM_COUNTY_FIPS = "063"
UTM_CRS = "EPSG:32617"  # UTM zone 17N — covers Durham, NC
WGS84 = "EPSG:4326"


def census_api_key():
    """Free Census Bureau API key from the CENSUS_API_KEY env var ('' if unset).

    The Census ACS API now rejects keyless requests with a "Missing Key" page,
    so population-density and EJ features need this set (sign up free at
    https://api.census.gov/data/key_signup.html). Without it they degrade to
    fallbacks, which `summarize_provenance` surfaces as constant columns.
    """
    return os.getenv("CENSUS_API_KEY", "").strip()


def safe_call(fn, *args, default=0.0, **kwargs):
    """Call fn with args, return default on any exception. Logs errors."""
    try:
        result = fn(*args, **kwargs)
        return result if result is not None else default
    except Exception as e:
        print(f"  [warn] {fn.__name__}: {e}")
        return default


# ── Shared OSM drive-network cache ───────────────────────────────────
# Both road density (catchment-clipped road km / catchment area) and road access
# (distance to the nearest road) need the bbox's drive network. Fetching it once
# (it is a large Overpass query) and reusing it for every candidate is the
# difference between a multi-hour run and a few minutes.
_DRIVE_GRAPH_CACHE = {}


def osm_drive_graph(bbox):
    """Fetch + cache the OSM drive network for ``bbox`` once.

    Returns ``{"nodes_utm": GeoDataFrame, "length_km": float}`` (nodes reprojected
    to UTM for metre-based nearest-road distance), or ``None`` on any failure.
    """
    key = tuple(round(float(x), 6) for x in bbox)
    if key in _DRIVE_GRAPH_CACHE:
        return _DRIVE_GRAPH_CACHE[key]
    try:
        import osmnx as ox
        west, south, east, north = bbox
        G = ox.graph_from_bbox(north, south, east, west, network_type="drive")
        # Road density measures physical road length, not directed graph arcs.
        # Convert to an undirected graph so two-way streets are not double-counted.
        try:
            road_graph = ox.convert.to_undirected(G)
        except AttributeError:  # osmnx 1.x compatibility
            road_graph = ox.utils_graph.get_undirected(G)
        nodes, edges = ox.graph_to_gdfs(road_graph)
        edges_utm = edges.to_crs(UTM_CRS)
        out = {
            "nodes_utm": nodes.to_crs(UTM_CRS),
            "edges_utm": edges_utm,
            "length_km": float(edges_utm.geometry.length.sum()) / 1000.0,
        }
    except Exception as e:
        print(f"  [warn] osm_drive_graph: {e}")
        out = None
    _DRIVE_GRAPH_CACHE[key] = out
    return out


def bbox_to_polygon(bbox, crs=WGS84):
    """Convert (west, south, east, north) bbox to a GeoDataFrame polygon."""
    from shapely.geometry import box
    poly = box(*bbox)
    return gpd.GeoDataFrame({"geometry": [poly]}, crs=crs)


def ensure_utm(gdf):
    """Reproject to UTM 17N if not already."""
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    if str(gdf.crs) != UTM_CRS:
        gdf = gdf.to_crs(UTM_CRS)
    return gdf


def inverse_distance_score(target_point, source_gdf, half_decay_m=500):
    """Compute inverse-distance-weighted score from sources to a target point."""
    if source_gdf.empty:
        return 0.0
    distances = source_gdf.geometry.distance(target_point)
    return float(sum(1 / (1 + (d / half_decay_m) ** 2) for d in distances))


def normalize_series(series, invert=False):
    """Min-max normalize a pandas Series to [0, 1]. Optionally invert."""
    mn, mx = series.min(), series.max()
    if mx == mn:
        return series * 0 + 0.5
    normed = (series - mn) / (mx - mn)
    return 1 - normed if invert else normed
