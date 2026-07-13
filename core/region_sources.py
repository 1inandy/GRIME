"""
GRIME — region-generalized data sources for the multi-region runner.

Everything here is INPUT SOURCING for arbitrary US regions: it fetches real
values and maps them onto the existing 27 parameters using region-appropriate,
documented relationships. No weights, curves, thresholds, gates, or scoring
math are defined or altered here.

Region-honesty rules implemented:
  * Channel width uses the Bieger et al. 2015 (JAWRA 51(3), Table 3) regional
    curve for the region's physiographic division — never an NC-only curve
    extrapolated elsewhere. The curve used is recorded per region.
  * The SIR 2014-5030 HR1 flood regression applies ONLY where flagged in the
    region config (NC Piedmont). Everywhere else flood_q10 is a documented
    fallback (NaN → the scoring's existing constant-column handling).
  * Estuary/beach references are per-region config DATA (e.g. Lake Michigan is
    Chicago's receiving water, documented as non-estuary), not NC constants.
  * Census ACS population/EJ are fetched per block group BY BBOX (multi-county,
    multi-state safe), reusing the shipped table codes and the shipped
    area-weighting; the EJ percentile rank is computed within the fetched
    region's block groups (documented — county-rank in the single-county case).
"""
from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point

from core import census_api_key
from core.real_sources import cached_get_json, KM2_TO_MI2

WGS84 = "EPSG:4326"


# ── Bieger et al. 2015 Table 3 regional width curves (verified from the paper) ──

BIEGER_WIDTH_CURVES = {
    # code: (a, b) for W(m) = a * DA(km²)^b
    "AHI": (3.12, 0.415),   # Appalachian Highlands (incl. NC Piedmont / Blue Ridge / New England)
    "APL": (2.22, 0.363),   # Atlantic Plain (Coastal Plain incl. Gulf coast)
    "IPL": (2.56, 0.351),   # Interior Plains (Chicago)
    "PMS": (2.76, 0.399),   # Pacific Mountain System (SF, LA)
}


def regional_width_m(drainage_km2, curve_code):
    """Bankfull width (m) from the region's Bieger 2015 divisional curve."""
    if drainage_km2 is None or drainage_km2 <= 0:
        return None
    a, b = BIEGER_WIDTH_CURVES[curve_code]
    return a * drainage_km2 ** b


# ── SIR 2014-5030 HR1 flood (NC Piedmont ONLY — gated by region config) ──

def flood_q10_hr1(drainage_km2, impervious_pct):
    """10% AEP flood (cfs), USGS SIR 2014-5030 Table 7, Hydrologic Region 1.
    Callers must only invoke this for regions whose config says flood_method ==
    'sir2014_hr1' (NC Piedmont); the runner enforces that."""
    if drainage_km2 is None or drainage_km2 <= 0:
        return None
    da_mi2 = max(drainage_km2 * KM2_TO_MI2, 0.10)
    imp = max(0.0, float(impervious_pct or 0.0))
    if da_mi2 <= 3.0:
        return 381.0 * da_mi2 ** 0.7536 * 10 ** (0.0076 * imp)
    return 484.0 * min(da_mi2, 436.0) ** 0.5539 * 10 ** (0.0060 * imp)


# ── SIR 2014-5030 HR4 flood (NC Coastal Plain ONLY — gated by config) ──

def flood_q10_hr4(drainage_km2, impervious_pct, i24h50y_in):
    """10% AEP flood (cfs), USGS SIR 2014-5030 Table 7, Hydrologic Region 4
    (Coastal Plain). Callers must only invoke this for regions whose config
    says flood_method == 'sir2014_hr4' AND carries an i24h50y_in constant
    (NOAA Atlas 14 24-h/50-y depth at the region center); the runner enforces
    that. Delegates to the guarded core implementation."""
    from core.real_sources import flood_q10_cfs_sir2014_hr4
    return flood_q10_cfs_sir2014_hr4(drainage_km2, impervious_pct, i24h50y_in)


# ── haversine (same formula the shipped model uses) ──────────────────

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


# ── Census ACS block groups BY BBOX (multi-county / multi-state) ─────

_TIGER_URL = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
              "tigerWMS_ACS2022/MapServer/8/query")
_ACS_URL = "https://api.census.gov/data/2022/acs/acs5"


def _tiger_blockgroups_bbox(bbox):
    """All ACS-2022 block-group geometries intersecting bbox, paged. Returns a
    WGS84 GeoDataFrame with GEOID, or None on failure."""
    w, s, e, n = bbox
    frames = []
    offset = 0
    while True:
        params = {
            "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
            "inSR": "4326", "outSR": "4326", "spatialRel": "esriSpatialRelIntersects",
            "outFields": "GEOID,AREALAND", "returnGeometry": "true", "f": "geojson",
            "resultOffset": offset, "resultRecordCount": 1000,
        }
        j = cached_get_json(_TIGER_URL, params, kind="tigerbg", timeout=90)
        if not j or not j.get("features"):
            break
        gdf = gpd.GeoDataFrame.from_features(j["features"], crs=WGS84)
        frames.append(gdf)
        if len(j["features"]) < 1000:
            break
        offset += 1000
        if offset > 20000:  # safety
            break
    if not frames:
        return None
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset="GEOID")
    return gpd.GeoDataFrame(out, crs=WGS84)


def _acs_county_rows(state, county):
    """ACS 5-yr block-group rows (population + EJ component tables) for one
    county. Returns a DataFrame or None. Cached on disk."""
    get_vars = ("B01003_001E,"
                "C17002_001E,C17002_002E,C17002_003E,C17002_004E,C17002_005E,"
                "C17002_006E,C17002_007E,B03002_001E,B03002_003E")
    params = {"get": get_vars, "for": "block group:*",
              "in": f"state:{state} county:{county}"}
    key = census_api_key()
    if key:
        params["key"] = key
    j = cached_get_json(_ACS_URL, params, kind="acs", timeout=60)
    if not j or len(j) < 2:
        return None
    df = pd.DataFrame(j[1:], columns=j[0])
    df["GEOID"] = df["state"] + df["county"] + df["tract"] + df["block group"]
    return df


def census_blockgroups_for_bbox(bbox, utm_crs):
    """Block-group GeoDataFrame (region UTM) with `density` (persons/km²) and
    `demo_index` (EJSCREEN-style two-component demographic index, percentile-
    ranked within the fetched region — the shipped construct generalized from
    one county to the region's block groups). None on failure."""
    bg = _tiger_blockgroups_bbox(bbox)
    if bg is None or bg.empty:
        return None
    # counties present in the bbox
    pairs = sorted({(g[:2], g[2:5]) for g in bg["GEOID"]})
    rows = []
    for st, cty in pairs:
        df = _acs_county_rows(st, cty)
        if df is not None:
            rows.append(df)
    if not rows:
        return None
    acs = pd.concat(rows, ignore_index=True)
    num = lambda c: pd.to_numeric(acs[c], errors="coerce").fillna(0)
    acs["population"] = num("B01003_001E")
    pov_total = num("C17002_001E").clip(lower=1)
    below_2x = sum(num(f"C17002_00{i}E") for i in range(2, 8))
    acs["pct_lowinc"] = (below_2x / pov_total).clip(0, 1)
    race_total = num("B03002_001E").clip(lower=1)
    acs["pct_poc"] = (1 - num("B03002_003E") / race_total).clip(0, 1)
    acs["demo_index"] = (acs["pct_lowinc"].rank(pct=True)
                         + acs["pct_poc"].rank(pct=True)) / 2

    merged = bg.merge(acs[["GEOID", "population", "demo_index"]], on="GEOID", how="left")
    merged = merged.to_crs(utm_crs)
    merged["population"] = merged["population"].fillna(0).astype(float)
    merged["demo_index"] = merged["demo_index"].fillna(merged["demo_index"].mean())
    merged["area_km2"] = merged.geometry.area / 1e6
    merged["density"] = merged["population"] / merged["area_km2"].clip(lower=0.001)
    return merged


def area_weighted(bg_gdf, polygon, column):
    """Area-weighted mean of `column` over `polygon` (same CRS). None if empty."""
    if bg_gdf is None or bg_gdf.empty:
        return None
    frame = gpd.GeoDataFrame({"geometry": [polygon]}, crs=bg_gdf.crs)
    try:
        inter = gpd.overlay(bg_gdf, frame, how="intersection")
    except Exception:
        inter = bg_gdf[bg_gdf.intersects(polygon)]
    if inter is None or inter.empty:
        return None
    w = inter.geometry.area
    if w.sum() <= 0:
        return float(inter[column].mean())
    return float((inter[column] * w).sum() / w.sum())


# ── EPA Envirofacts TRI by STATE (sign-fixed, bbox-filtered) ─────────

def tri_points_state(state_abbr, bbox):
    """TRI facility points in bbox for a state — same longitude-sign + null fix
    as the Durham fetcher, generalized. Returns [(lat, lon)]."""
    url = (f"https://data.epa.gov/efservice/tri_facility/state_abbr/"
           f"{state_abbr}/rows/0:9999/JSON")
    j = cached_get_json(url, kind="tri_state", timeout=120)
    if not j:
        return []
    w, s, e, n = bbox
    pts = []
    for f in j:
        lat = f.get("pref_latitude") or f.get("fac_latitude")
        lon = f.get("pref_longitude") or f.get("fac_longitude")
        if lat in (None, "", 0) or lon in (None, "", 0):
            continue
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        if lon > 0:
            lon = -lon           # Envirofacts drops the minus sign in CONUS
        if lat < 0:
            lat = -lat
        if s <= lat <= n and w <= lon <= e:
            pts.append((lat, lon))
    return pts


# ── NBI bridges with pagination (big-city bboxes exceed one page) ────

def nbi_bridge_points_paged(bbox, page=2000, max_pages=10):
    """NBI bridge points in bbox from the NTAD feature service, paged (big-city
    bboxes exceed one 2000-record page). The service's LATDD/LONGDD fields are
    decimal degrees; LAT_016/LONG_017 are the raw NBI DMS strings (DDMMSSss) —
    prefer the decimal fields and fall back to a DMS parse."""
    from core.real_sources import _dms_to_dd
    url = ("https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
           "NTAD_National_Bridge_Inventory/FeatureServer/0/query")
    w, s, e, n = bbox
    out = []
    for i in range(max_pages):
        params = {
            "where": "1=1", "geometry": f"{w},{s},{e},{n}",
            "geometryType": "esriGeometryEnvelope", "inSR": "4326", "outSR": "4326",
            "outFields": "LATDD,LONGDD,LAT_016,LONG_017",
            "returnGeometry": "false", "f": "json",
            "resultRecordCount": page, "resultOffset": i * page,
        }
        j = cached_get_json(url, params, kind="nbi", timeout=90)
        feats = (j or {}).get("features", [])
        for feat in feats:
            a = feat.get("attributes", {})
            lat = lon = None
            try:
                lat, lon = float(a.get("LATDD")), float(a.get("LONGDD"))
            except (TypeError, ValueError):
                try:  # DMS fallback (strings like '35511941' / '078562274')
                    lat = _dms_to_dd(float(a.get("LAT_016")))
                    lon = -_dms_to_dd(float(a.get("LONG_017")))
                except (TypeError, ValueError):
                    continue
            if lat is None or lon is None:
                continue
            if lon > 0:
                lon = -lon
            if s - 0.2 <= lat <= n + 0.2 and w - 0.2 <= lon <= e + 0.2:
                out.append((lat, lon))
        if len(feats) < page:
            break
    return out


def to_utm_points(pts, utm_crs):
    """[(lat, lon)] → GeoSeries in the region's UTM."""
    if not pts:
        return gpd.GeoSeries([], crs=utm_crs)
    g = gpd.GeoSeries([Point(lon, lat) for lat, lon in pts], crs=WGS84)
    return g.to_crs(utm_crs)


# ── Municipal litter / illegal-dumping complaints ───────────────────

LITTER_HALF_DECAY_M = 500.0

_LITTER_SOURCES = {
    "charlotte_311": {
        "label": "Charlotte ServiceRequests311 (litter/debris or dumping in street/ROW)",
        "url": ("https://gis.charlottenc.gov/arcgis/rest/services/ODP/"
                "ServiceRequests311/MapServer/0/query"),
    },
    "raleigh_ask": {
        "label": "Ask Raleigh requests (REQUEST_TYPE='Litter')",
        "url": ("https://services.arcgis.com/v400IkDOw1ad7Yad/arcgis/rest/services/"
                "Ask_Raleigh_Requests/FeatureServer/0/query"),
    },
    "greensboro_trash_archive": {
        "label": "Greensboro Trash & Waste code-complaint archive (through 2024-06-18)",
        "url": ("https://gis.greensboro-nc.gov/arcgis/rest/services/"
                "OpenGateCity/OpenData_CC_DS/MapServer"),
    },
}


def _arcgis_features_paged(url, params, kind, page_size=1000, max_pages=100):
    """Return every ArcGIS feature for a query, or ``None`` on any failed page."""
    out = []
    for page in range(max_pages):
        query = dict(params)
        query.update({"resultOffset": page * page_size,
                      "resultRecordCount": page_size, "f": "json"})
        data = cached_get_json(url, query, kind=kind, timeout=120)
        if data is None or "features" not in data:
            return None
        features = data["features"]
        out.extend(features)
        if not data.get("exceededTransferLimit") and len(features) < page_size:
            return out
    return None                         # a silent page cap is not a valid dataset


def _attrs_lower(feature):
    return {str(k).lower(): v for k, v in feature.get("attributes", {}).items()}


def _in_bbox(lat, lon, bbox):
    w, s, e, n = bbox
    return s <= lat <= n and w <= lon <= e


def municipal_litter_points(source_key, bbox):
    """Official litter/illegal-dumping complaint points for a configured city.

    Returns ``[(lat, lon), ...]`` (including a truthful empty list after a
    successful query) or ``None`` for an unsupported/unavailable source. Filters
    are deliberately exact: unrelated pollution, blocked-drain, or generic code
    cases are not treated as litter.
    """
    if source_key not in _LITTER_SOURCES:
        return None
    source = _LITTER_SOURCES[source_key]
    if source_key == "charlotte_311":
        features = _arcgis_features_paged(
            source["url"], {
                "where": ("REQUEST_TYPE IN ('LITTER/DEBRIS IN STREET',"
                          "'DUMPING IN STREET/ROW')"),
                "outFields": ("OBJECTID,REQUEST_TYPE,TITLE,RECEIVED_DATE,"
                              "LATITUDE,LONGITUDE"),
                "returnGeometry": "false",
            }, "litter_charlotte", page_size=7500)
        if features is None:
            return None
        points = []
        for feature in features:
            a = _attrs_lower(feature)
            try:
                lat, lon = float(a["latitude"]), float(a["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if _in_bbox(lat, lon, bbox):
                points.append((lat, lon))
        return points

    if source_key == "raleigh_ask":
        w, s, e, n = bbox
        features = _arcgis_features_paged(
            source["url"], {
                "where": "REQUEST_TYPE = 'Litter'",
                "geometry": f"{w},{s},{e},{n}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326", "outSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "OBJECTID,CATEGORY,SERVICE,REQUEST_TYPE,APPLIED_DATE",
                "returnGeometry": "true",
            }, "litter_raleigh", page_size=1000)
        if features is None:
            return None
        points = []
        for feature in features:
            geom = feature.get("geometry", {})
            try:
                lon, lat = float(geom["x"]), float(geom["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if _in_bbox(lat, lon, bbox):
                points.append((lat, lon))
        return points

    # Greensboro's official archive separates complaint cases (table 1) from
    # geocoded violations (layer 3). Join on CaseNumber and deduplicate cases;
    # multiple violation rows must never multiply a single complaint.
    base = source["url"]
    complaints = _arcgis_features_paged(
        f"{base}/1/query", {
            "where": "CaseTypeCode = '17-1 TW' AND Origin = 'C - Complaint'",
            "outFields": "CaseNumber,CaseTypeCode,Origin,EntryDate",
            "returnGeometry": "false",
        }, "litter_greensboro_cases", page_size=1000)
    violations = _arcgis_features_paged(
        f"{base}/3/query", {
            "where": "CaseTypeCode = '17-1 TW'",
            "outFields": "CaseNumber,CaseTypeCode,Latitude,Longitude",
            "returnGeometry": "false",
        }, "litter_greensboro_violations", page_size=1000)
    if complaints is None or violations is None:
        return None
    case_ids = {str(_attrs_lower(f).get("casenumber", "")).strip()
                for f in complaints}
    case_ids.discard("")
    by_case = {}
    for feature in violations:
        a = _attrs_lower(feature)
        case = str(a.get("casenumber", "")).strip()
        if not case or case not in case_ids or case in by_case:
            continue
        try:
            lat, lon = float(a["latitude"]), float(a["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if _in_bbox(lat, lon, bbox):
            by_case[case] = (lat, lon)
    return list(by_case.values())


def litter_density_from_points(catchment_polygon, catchment_area_km2,
                               complaint_points_utm,
                               half_decay_m=LITTER_HALF_DECAY_M):
    """Distance-decayed complaints per catchment km².

    A complaint inside the catchment has weight 1. Outside complaints decay as
    ``1/(1+(distance/half_decay)^2)``. The source set is fetched once per region.
    """
    if complaint_points_utm is None or len(complaint_points_utm) == 0:
        return 0.0
    distances = complaint_points_utm.distance(catchment_polygon)
    weights = 1.0 / (1.0 + (distances / float(half_decay_m)) ** 2)
    return float(weights.sum() / max(float(catchment_area_km2), 0.01))


def litter_source_label(source_key):
    source = _LITTER_SOURCES.get(source_key)
    return source["label"] if source else None
