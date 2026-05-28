"""gARB utilities — shared helpers across all modules."""

import numpy as np
import geopandas as gpd
from shapely.geometry import Point


# ── Constants ────────────────────────────────────────────────────────
ELLERBE_BBOX = (-79.05, 35.90, -78.75, 36.05)
ELLERBE_GAUGE = "02086849"
DURHAM_STATE_FIPS = "37"
DURHAM_COUNTY_FIPS = "063"
UTM_CRS = "EPSG:32617"  # UTM zone 17N — covers Durham, NC
WGS84 = "EPSG:4326"


def safe_call(fn, *args, default=0.0, **kwargs):
    """Call fn with args, return default on any exception. Logs errors."""
    try:
        result = fn(*args, **kwargs)
        return result if result is not None else default
    except Exception as e:
        print(f"  [warn] {fn.__name__}: {e}")
        return default


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
