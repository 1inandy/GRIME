"""
GRIME — Trash Generation Parameters
Population density, impervious surface, road density, TRI, NPDES, CSO, 311 complaints.

All APIs are free and require no keys unless noted.
"""

import requests
import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from core import (
    DURHAM_STATE_FIPS, DURHAM_COUNTY_FIPS, UTM_CRS, WGS84,
    safe_call, inverse_distance_score, census_api_key, osm_drive_graph
)


# ── 2.1 Population Density (Census ACS) ─────────────────────────────

# Block-group population density (geometry + density) depends only on the county,
# so build it once and cache it; each candidate only does a cheap area-weighted
# overlay against its own catchment. (Was re-pulling ACS + the whole TIGER county
# per candidate.)
_POP_BG_CACHE = {}


def _block_group_density(state_fips, county_fips):
    """Live ACS + TIGER → GeoDataFrame[GEOID, density, geometry] (UTM) for a county.
    Returns None on failure. Cached by (state, county)."""
    key = (state_fips, county_fips)
    if key in _POP_BG_CACHE:
        return _POP_BG_CACHE[key]

    # Census API — ACS 5-year, block group level. The API now rejects keyless
    # requests, so include CENSUS_API_KEY when present (else this falls back).
    url = "https://api.census.gov/data/2022/acs/acs5"
    params = {
        "get": "B01003_001E,NAME",
        "for": "block group:*",
        "in": f"state:{state_fips} county:{county_fips}",
    }
    api_key = census_api_key()
    if api_key:
        params["key"] = api_key

    try:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code != 200:
            _POP_BG_CACHE[key] = None
            return None
        data = r.json()
        header = data[0]
        rows = data[1:]
    except Exception:
        _POP_BG_CACHE[key] = None
        return None

    # Get block group geometries from TIGER. Layer 8 is "Census Block Groups"
    # (layer 10 is school districts → has no COUNTY field). STATE/COUNTY are string
    # fields, so the values must be quoted or ArcGIS rejects the query
    # ("Unable to complete operation").
    tiger_url = (
        f"https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
        f"tigerWMS_ACS2022/MapServer/8/query?"
        f"where=STATE='{state_fips}'+AND+COUNTY='{county_fips}'"
        f"&outFields=GEOID,AREALAND&f=geojson&returnGeometry=true"
    )

    try:
        bg_gdf = gpd.read_file(tiger_url).to_crs(UTM_CRS)
    except Exception:
        _POP_BG_CACHE[key] = None
        return None

    # Merge population data
    pop_df = pd.DataFrame(rows, columns=header)
    pop_df["GEOID"] = pop_df["state"] + pop_df["county"] + pop_df["tract"] + pop_df["block group"]
    pop_df["population"] = pd.to_numeric(pop_df["B01003_001E"], errors="coerce").fillna(0)

    bg_gdf = bg_gdf.merge(pop_df[["GEOID", "population"]], on="GEOID", how="left")
    bg_gdf["population"] = bg_gdf["population"].fillna(0).astype(float)
    bg_gdf["area_km2"] = bg_gdf.geometry.area / 1e6
    bg_gdf["density"] = bg_gdf["population"] / bg_gdf["area_km2"].clip(lower=0.001)
    _POP_BG_CACHE[key] = bg_gdf
    return bg_gdf


def get_population_density(catchment_polygon, state_fips=DURHAM_STATE_FIPS,
                           county_fips=DURHAM_COUNTY_FIPS):
    """
    Pull ACS 5-year population density for a catchment area.
    Returns persons/km² as a float (area-weighted over the catchment).
    Uses Census API directly (no cenpy dependency needed).
    """
    bg_gdf = _block_group_density(state_fips, county_fips)
    if bg_gdf is None or bg_gdf.empty:
        return 500.0  # Durham average fallback

    # Area-weighted intersection with catchment
    catch_gdf = gpd.GeoDataFrame({"geometry": [catchment_polygon]}, crs=UTM_CRS)
    try:
        intersected = gpd.overlay(bg_gdf, catch_gdf, how="intersection")
        if intersected.empty:
            return 500.0
        intersected["weight"] = intersected.geometry.area
        weighted_density = (
            (intersected["density"] * intersected["weight"]).sum()
            / intersected["weight"].sum()
        )
        return float(weighted_density)
    except Exception:
        return 500.0


# ── 2.2 EPA TRI Facilities ──────────────────────────────────────────

_TRI_CACHE = {}


def get_tri_facilities(catchment_bbox, year=2022):
    """Pull TRI facilities from EPA efservice API. No key required."""
    key = (tuple(round(float(x), 6) for x in catchment_bbox), year)
    if key in _TRI_CACHE:
        return _TRI_CACHE[key]
    west, south, east, north = catchment_bbox
    url = (
        f"https://data.epa.gov/efservice/tri_facility/"
        f"XCOORD/BETWEEN/{west}/{east}/"
        f"YCOORD/BETWEEN/{south}/{north}/JSON"
    )
    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200 or not r.json():
            result = gpd.GeoDataFrame()
        else:
            facilities = r.json()
            result = gpd.GeoDataFrame(
                facilities,
                geometry=[Point(f["XCOORD"], f["YCOORD"]) for f in facilities
                          if f.get("XCOORD") and f.get("YCOORD")],
                crs=WGS84,
            ).to_crs(UTM_CRS)
    except Exception:
        result = gpd.GeoDataFrame()
    _TRI_CACHE[key] = result
    return result


def tri_density_score(catchment_polygon, catchment_area_km2, bbox):
    """Count TRI facilities per km² in catchment."""
    facilities = get_tri_facilities(bbox)
    if facilities.empty:
        return 0.0
    in_catchment = facilities[facilities.within(catchment_polygon)]
    return len(in_catchment) / max(catchment_area_km2, 0.01)


# ── 2.3 EPA ECHO — NPDES & CSO ──────────────────────────────────────

_NPDES_CACHE = {}


def get_npdes_facilities(catchment_bbox):
    """Pull NPDES permitted facilities from EPA ECHO API. No key required.

    ECHO's facility-search endpoint is frequently 404 (see healthcheck); on any
    failure this returns empty frames. Cached by bbox so the dead endpoint is hit
    once, not once per candidate."""
    key = tuple(round(float(x), 6) for x in catchment_bbox)
    if key in _NPDES_CACHE:
        return _NPDES_CACHE[key]
    west, south, east, north = catchment_bbox
    url = "https://echo.epa.gov/api/facility-search/facilities"
    params = {
        "output": "JSON",
        "p_c1lon": west, "p_c1lat": south,
        "p_c2lon": east, "p_c2lat": north,
        "p_permit_type": "NPD",
        "p_major": "Y",
        "qcolumns": "1,3,4,5,13,16,23,24",
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        data = r.json().get("Results", {}).get("Facilities", [])
        if not data:
            result = (gpd.GeoDataFrame(), gpd.GeoDataFrame())
        else:
            gdf = gpd.GeoDataFrame(
                data,
                geometry=[
                    Point(float(f.get("FacLon", 0)), float(f.get("FacLat", 0)))
                    for f in data if f.get("FacLat") and f.get("FacLon")
                ],
                crs=WGS84,
            ).to_crs(UTM_CRS)

            cso = gdf[gdf.get("CSOFlag", "N") == "Y"] if "CSOFlag" in gdf.columns else gdf.iloc[0:0]
            result = (gdf, cso)
    except Exception:
        result = (gpd.GeoDataFrame(), gpd.GeoDataFrame())
    _NPDES_CACHE[key] = result
    return result


def npdes_count(catchment_polygon, bbox):
    """Count NPDES discharge points in catchment."""
    gdf, _ = get_npdes_facilities(bbox)
    if gdf.empty:
        return 0
    return int(gdf[gdf.within(catchment_polygon)].shape[0])


def cso_proximity_score(candidate_point_utm, bbox):
    """Score based on CSO density upstream. CSOs = direct debris injection."""
    _, cso_gdf = get_npdes_facilities(bbox)
    return inverse_distance_score(candidate_point_utm, cso_gdf, half_decay_m=500)


# ── 2.4 Local 311 Litter Complaints ─────────────────────────────────

def get_litter_complaints(catchment_polygon, city="durham"):
    """
    Pull litter/illegal dumping 311 complaints from open data portal.
    Durham Open Data: ArcGIS REST API, no key required.
    """
    url = (
        "https://services.arcgis.com/OUDgzKjgJqDGaYSz/arcgis/rest/services/"
        "Durham_311_Service_Requests/FeatureServer/0/query"
    )
    bounds = catchment_polygon.bounds  # (minx, miny, maxx, maxy) in UTM

    # Convert bounds back to WGS84 for API
    bounds_gdf = gpd.GeoDataFrame(
        {"geometry": [Point(bounds[0], bounds[1]), Point(bounds[2], bounds[3])]},
        crs=UTM_CRS,
    ).to_crs(WGS84)
    b = bounds_gdf.total_bounds  # [minx, miny, maxx, maxy] in WGS84

    params = {
        "where": "category LIKE '%LITTER%' OR category LIKE '%DUMP%'",
        "geometry": f"{b[0]},{b[1]},{b[2]},{b[3]}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326", "outSR": "4326",
        "outFields": "objectid,category,lat,lon",
        "f": "geojson",
    }

    try:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code != 200:
            return gpd.GeoDataFrame()
        complaints = gpd.read_file(r.text).to_crs(UTM_CRS)
        return complaints[complaints.within(catchment_polygon)]
    except Exception:
        return gpd.GeoDataFrame()


# Whole-bbox litter/dumping complaint set, fetched once and filtered per candidate
# (rather than a separate Durham-311 query per candidate catchment).
_LITTER_CACHE = {}


def _bbox_litter(bbox):
    """All litter/dumping 311 complaints in ``bbox`` (UTM), cached. None on failure."""
    key = tuple(round(float(x), 6) for x in bbox)
    if key in _LITTER_CACHE:
        return _LITTER_CACHE[key]
    west, south, east, north = bbox
    url = (
        "https://services.arcgis.com/OUDgzKjgJqDGaYSz/arcgis/rest/services/"
        "Durham_311_Service_Requests/FeatureServer/0/query"
    )
    params = {
        "where": "category LIKE '%LITTER%' OR category LIKE '%DUMP%'",
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326", "outSR": "4326",
        "outFields": "objectid,category,lat,lon",
        "f": "geojson",
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code != 200 or not r.text.strip():
            result = None
        else:
            result = gpd.read_file(r.text).to_crs(UTM_CRS)
    except Exception:
        result = None
    _LITTER_CACHE[key] = result
    return result


def litter_complaint_density(catchment_polygon, catchment_area_km2, bbox=None):
    """Reports per km² in catchment. Uses the cached whole-bbox complaint set when
    ``bbox`` is given (filtering locally), else the per-catchment query."""
    if bbox is not None:
        complaints = _bbox_litter(bbox)
        if complaints is None:
            return 0.0
        in_catch = complaints[complaints.within(catchment_polygon)] if not complaints.empty else complaints
        return len(in_catch) / max(catchment_area_km2, 0.01)
    complaints = get_litter_complaints(catchment_polygon)
    return len(complaints) / max(catchment_area_km2, 0.01)


# ── 2.5 Impervious Surface (NLCD) ───────────────────────────────────

# NLCD imperviousness is a whole-bbox raster mean — identical for every candidate,
# so cache it by bbox instead of re-fetching the raster once per candidate.
_IMPERV_CACHE = {}


def get_impervious_pct(catchment_polygon, bbox):
    """
    Estimate impervious surface % from NLCD.
    Uses py3dep to fetch NLCD-derived imperviousness if available,
    otherwise estimates from land cover classes.
    """
    key = tuple(round(float(x), 6) for x in bbox)
    if key in _IMPERV_CACHE:
        return _IMPERV_CACHE[key]
    try:
        import py3dep
        # Try fetching NLCD imperviousness layer
        imp = py3dep.get_map("Imperviousness", bbox, resolution=30, crs=WGS84)
        mean_imp = float(np.nanmean(imp.values))
        val = min(max(mean_imp, 0), 100)
    except Exception:
        # Fallback: Durham urban average ~35%
        val = 35.0
    _IMPERV_CACHE[key] = val
    return val


# ── 2.6 Road Density (Census TIGER) ─────────────────────────────────

def get_road_density(catchment_polygon, catchment_area_km2, bbox):
    """
    Compute road density (km/km²) from OSM drive-network edges clipped to the
    candidate's catchment polygon.
    The (large) bbox drive network is fetched once and shared via
    ``core.osm_drive_graph`` — re-pulling it per candidate was the single slowest
    call in build_all_features.
    """
    info = osm_drive_graph(bbox)
    if info is None:
        return 5.0  # Durham average fallback
    edges = info.get("edges_utm")
    if edges is None or edges.empty:
        return 0.0

    # Bounding-box prefilter keeps the per-candidate intersection cheap while the
    # shared graph remains cached for the whole run.
    minx, miny, maxx, maxy = catchment_polygon.bounds
    nearby = edges.cx[minx:maxx, miny:maxy]
    if nearby.empty:
        return 0.0
    clipped = nearby.geometry.intersection(catchment_polygon)
    road_km = float(clipped.length.sum()) / 1000.0
    return road_km / max(catchment_area_km2, 0.01)


# ── Aggregate Generation Score ───────────────────────────────────────

GENERATION_WEIGHTS = {
    "population_density": 0.18,
    "impervious_pct": 0.20,
    "road_density_km_km2": 0.10,
    "tri_facility_density": 0.18,
    "npdes_points": 0.12,
    "cso_density": 0.12,
    "litter_complaint_density": 0.10,
}


def compute_generation_features(candidate_row, catchment_polygon, bbox):
    """Compute all generation features for a single candidate."""
    area_km2 = candidate_row.get("catchment_area_km2", 1.0)
    point_utm = candidate_row.geometry

    features = {
        "population_density": safe_call(
            get_population_density, catchment_polygon, default=500.0
        ),
        "impervious_pct": safe_call(
            get_impervious_pct, catchment_polygon, bbox, default=35.0
        ),
        "road_density_km_km2": safe_call(
            get_road_density, catchment_polygon, area_km2, bbox, default=5.0
        ),
        "tri_facility_density": safe_call(
            tri_density_score, catchment_polygon, area_km2, bbox, default=0.0
        ),
        "npdes_points": safe_call(
            npdes_count, catchment_polygon, bbox, default=0
        ),
        "cso_density": safe_call(
            cso_proximity_score, point_utm, bbox, default=0.0
        ),
        "litter_complaint_density": safe_call(
            litter_complaint_density, catchment_polygon, area_km2, bbox, default=0.0
        ),
    }
    return features
