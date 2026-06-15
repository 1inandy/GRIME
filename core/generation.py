"""
gARB — Trash Generation Parameters
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
    safe_call, inverse_distance_score
)


# ── 2.1 Population Density (Census ACS) ─────────────────────────────

def get_population_density(catchment_polygon, state_fips=DURHAM_STATE_FIPS,
                           county_fips=DURHAM_COUNTY_FIPS):
    """
    Pull ACS 5-year population density for a catchment area.
    Returns persons/km² as a float.
    Uses Census API directly (no cenpy dependency needed).
    """
    # Census API — ACS 5-year, block group level
    url = "https://api.census.gov/data/2022/acs/acs5"
    params = {
        "get": "B01003_001E,NAME",
        "for": "block group:*",
        "in": f"state:{state_fips} county:{county_fips}",
    }

    try:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code != 200:
            return 500.0  # Durham average fallback
        data = r.json()
        header = data[0]
        rows = data[1:]
    except Exception:
        return 500.0

    # Get block group geometries from TIGER
    tiger_url = (
        f"https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
        f"tigerWMS_ACS2022/MapServer/10/query?"
        f"where=STATE={state_fips}+AND+COUNTY={county_fips}"
        f"&outFields=GEOID,AREALAND&f=geojson&returnGeometry=true"
    )

    try:
        bg_gdf = gpd.read_file(tiger_url).to_crs(UTM_CRS)
    except Exception:
        return 500.0

    # Merge population data
    pop_df = pd.DataFrame(rows, columns=header)
    pop_df["GEOID"] = pop_df["state"] + pop_df["county"] + pop_df["tract"] + pop_df["block group"]
    pop_df["population"] = pd.to_numeric(pop_df["B01003_001E"], errors="coerce").fillna(0)

    bg_gdf = bg_gdf.merge(pop_df[["GEOID", "population"]], on="GEOID", how="left")
    bg_gdf["population"] = bg_gdf["population"].fillna(0).astype(float)
    bg_gdf["area_km2"] = bg_gdf.geometry.area / 1e6
    bg_gdf["density"] = bg_gdf["population"] / bg_gdf["area_km2"].clip(lower=0.001)

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

def get_tri_facilities(catchment_bbox, year=2022):
    """Pull TRI facilities from EPA efservice API. No key required."""
    west, south, east, north = catchment_bbox
    url = (
        f"https://data.epa.gov/efservice/tri_facility/"
        f"XCOORD/BETWEEN/{west}/{east}/"
        f"YCOORD/BETWEEN/{south}/{north}/JSON"
    )
    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200 or not r.json():
            return gpd.GeoDataFrame()
        facilities = r.json()
        gdf = gpd.GeoDataFrame(
            facilities,
            geometry=[Point(f["XCOORD"], f["YCOORD"]) for f in facilities
                      if f.get("XCOORD") and f.get("YCOORD")],
            crs=WGS84,
        ).to_crs(UTM_CRS)
        return gdf
    except Exception:
        return gpd.GeoDataFrame()


def tri_density_score(catchment_polygon, catchment_area_km2, bbox):
    """Count TRI facilities per km² in catchment."""
    facilities = get_tri_facilities(bbox)
    if facilities.empty:
        return 0.0
    in_catchment = facilities[facilities.within(catchment_polygon)]
    return len(in_catchment) / max(catchment_area_km2, 0.01)


# ── 2.3 EPA ECHO — NPDES & CSO ──────────────────────────────────────

def get_npdes_facilities(catchment_bbox):
    """Pull NPDES permitted facilities from EPA ECHO API. No key required."""
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
            return gpd.GeoDataFrame(), gpd.GeoDataFrame()

        gdf = gpd.GeoDataFrame(
            data,
            geometry=[
                Point(float(f.get("FacLon", 0)), float(f.get("FacLat", 0)))
                for f in data if f.get("FacLat") and f.get("FacLon")
            ],
            crs=WGS84,
        ).to_crs(UTM_CRS)

        cso = gdf[gdf.get("CSOFlag", "N") == "Y"] if "CSOFlag" in gdf.columns else gdf.iloc[0:0]
        return gdf, cso
    except Exception:
        return gpd.GeoDataFrame(), gpd.GeoDataFrame()


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


def litter_complaint_density(catchment_polygon, catchment_area_km2):
    """Reports per km² in catchment."""
    complaints = get_litter_complaints(catchment_polygon)
    return len(complaints) / max(catchment_area_km2, 0.01)


# ── 2.5 Impervious Surface (NLCD) ───────────────────────────────────

def get_impervious_pct(catchment_polygon, bbox):
    """
    Estimate impervious surface % from NLCD.
    Uses py3dep to fetch NLCD-derived imperviousness if available,
    otherwise estimates from land cover classes.
    """
    try:
        import py3dep
        # Try fetching NLCD imperviousness layer
        imp = py3dep.get_map("Imperviousness", bbox, resolution=30, crs=WGS84)
        mean_imp = float(np.nanmean(imp.values))
        return min(max(mean_imp, 0), 100)
    except Exception:
        # Fallback: Durham urban average ~35%
        return 35.0


# ── 2.6 Road Density (Census TIGER) ─────────────────────────────────

def get_road_density(catchment_polygon, catchment_area_km2, bbox):
    """
    Compute road density (km/km²) from Census TIGER/Line shapefiles.
    Uses OSMnx as a more reliable alternative.
    """
    try:
        import osmnx as ox
        west, south, east, north = bbox
        G = ox.graph_from_bbox(north, south, east, west, network_type="drive")
        edges = ox.graph_to_gdfs(G, nodes=False)
        total_length_km = edges["length"].sum() / 1000
        return total_length_km / max(catchment_area_km2, 0.01)
    except Exception:
        return 5.0  # Durham average fallback


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
            litter_complaint_density, catchment_polygon, area_km2, default=0.0
        ),
    }
    return features
