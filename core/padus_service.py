"""Official PAD-US 4.1 web-service fallback for unavailable state GDBs.

The preferred input remains the versioned state download. ScienceBase currently
requires an authenticated File Manager session for several state archives, so
this module uses USGS's public PAD-US combined-inventory FeatureServer rather
than silently replacing missing states with zero.
"""
from __future__ import annotations

import geopandas as gpd
from shapely.geometry import box

from core.real_sources import cached_get_json


PADUS_FEATURE_LAYER = (
    "https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/"
    "Protection_Mechanism_Category_PADUS/FeatureServer/0"
)
PADUS_FEATURE_SERVICE = f"{PADUS_FEATURE_LAYER}/query"


def _designation_domain():
    """Return the service's official Des_Tp code-to-description lookup."""
    metadata = cached_get_json(
        PADUS_FEATURE_LAYER, {"f": "json"}, kind="padus_agol_meta", timeout=120)
    if not isinstance(metadata, dict) or "fields" not in metadata:
        return None
    for field in metadata["fields"]:
        if field.get("name") != "Des_Tp":
            continue
        coded = (field.get("domain") or {}).get("codedValues")
        if not isinstance(coded, list):
            return None
        return {str(item["code"]): str(item["name"]).strip()
                for item in coded if "code" in item and "name" in item}
    return None


def padus_protected_gdf_remote(bbox, utm_crs, pad_km=20.0,
                               page_size=2000, max_pages=50):
    """Return bbox-local PAD-US polygons from the official USGS service.

    Returns ``None`` if any page fails and an empty GeoDataFrame after a
    successful zero-result query. The exact padded query geometry is applied in
    metric coordinates after download; the geographic envelope sent to ArcGIS
    is only a candidate filter.
    """
    try:
        w, s, e, n = (float(v) for v in bbox)
        pad_m = float(pad_km) * 1000.0
        if w >= e or s >= n or pad_m < 0:
            return None
    except (TypeError, ValueError):
        return None

    designation_domain = _designation_domain()
    if designation_domain is None:
        return None

    query_wgs = gpd.GeoSeries([box(w, s, e, n)], crs="EPSG:4326")
    exact_clip = query_wgs.to_crs(utm_crs).buffer(pad_m).iloc[0]
    expanded_wgs = gpd.GeoSeries([exact_clip], crs=utm_crs).to_crs(
        "EPSG:4326").total_bounds
    geometry = ",".join(f"{value:.8f}" for value in expanded_wgs)

    features = []
    for page in range(max_pages):
        data = cached_get_json(
            PADUS_FEATURE_SERVICE,
            {
                "where": "1=1",
                "geometry": geometry,
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "outSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "OBJECTID,Des_Tp,GAP_Sts",
                "returnGeometry": "true",
                "orderByFields": "OBJECTID",
                "resultOffset": page * page_size,
                "resultRecordCount": page_size,
                "f": "geojson",
            },
            kind="padus_agol",
            timeout=120,
        )
        if not isinstance(data, dict) or "features" not in data:
            return None
        batch = data["features"]
        features.extend(batch)
        exceeded = bool(data.get("properties", {}).get("exceededTransferLimit"))
        if not exceeded:
            break
    else:
        return None  # never treat a silently truncated page cap as complete

    if not features:
        return gpd.GeoDataFrame(
            {"designation": [], "GAP_Sts": []}, geometry=[], crs=utm_crs)
    try:
        frame = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
        if "Des_Tp" not in frame.columns:
            return None
        if "GAP_Sts" not in frame.columns:
            frame["GAP_Sts"] = None
        frame = frame[["Des_Tp", "GAP_Sts", "geometry"]].rename(
            columns={"Des_Tp": "designation"})
        frame["designation"] = frame["designation"].map(
            lambda value: designation_domain.get(str(value), str(value)))
        frame = frame[frame.geometry.notna()].to_crs(utm_crs)
        # The nationwide service contains some self-intersecting source rings.
        # Repair those official geometries before the exact spatial filter;
        # otherwise GEOS can abort a whole metro on one invalid polygon.
        invalid = ~frame.geometry.is_valid
        if invalid.any():
            frame.loc[invalid, "geometry"] = frame.loc[invalid].geometry.make_valid()
        return frame[frame.intersects(exact_clip)].copy()
    except Exception as exc:
        print(f"  [warn] PAD-US feature-service geometry parse failed: {exc}")
        return None
