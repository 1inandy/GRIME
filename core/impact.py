"""
GRIME — Downstream Impact Parameters
Drinking water intake proximity, EJ index, protected areas,
ocean/estuary distance, recreational beach proximity, tourism value, superfund.
"""

import math
import requests
import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from core import (
    UTM_CRS, WGS84, DURHAM_STATE_FIPS, DURHAM_COUNTY_FIPS,
    safe_call, inverse_distance_score, census_api_key,
)


# ── 6.1 Drinking Water Intake Proximity (EPA SDWIS/ECHO) ────────────

def get_drinking_water_intakes(state="NC"):
    """Pull surface water intake locations from EPA ECHO API."""
    url = "https://echo.epa.gov/api/facility-search/water-systems"
    params = {
        "output": "JSON",
        "p_st": state,
        "p_source": "SW",  # Surface water
        "qcolumns": "1,2,3,4,5,6,12,13",
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        systems = r.json().get("Results", {}).get("Facilities", [])
        intakes = [s for s in systems if s.get("FacLat") and s.get("FacLon")]
        if not intakes:
            return gpd.GeoDataFrame()
        return gpd.GeoDataFrame(
            intakes,
            geometry=[Point(float(s["FacLon"]), float(s["FacLat"])) for s in intakes],
            crs=WGS84,
        ).to_crs(UTM_CRS)
    except Exception:
        return gpd.GeoDataFrame()


def water_intake_score(candidate_point_utm, intakes_gdf, max_dist_km=50):
    """
    Proximity score to drinking-water intakes.
    L5: this is **omnidirectional proximity, not flow-gated** — it sums exp(-d/10)
    over every intake within 50 km regardless of upstream/downstream direction.
    Flow-gated (network-distance) weighting is future work.
    """
    if intakes_gdf.empty:
        return 0.0
    distances_km = intakes_gdf.geometry.distance(candidate_point_utm) / 1000
    relevant = distances_km[distances_km < max_dist_km]
    return float(sum(math.exp(-d / 10) for d in relevant))


# ── 6.2 Environmental Justice Index (reconstructed from Census ACS) ──
#
# C4: EPA removed EJSCREEN (tool, downloads, and the ejscreenRESTbroker.aspx
# ArcGIS server) on 2025-02-05, and CEJST was pulled 2025-01-22 — there is no
# official live federal endpoint. EJSCREEN's *demographic index* is, by
# definition, percentile-ranked ACS demographics, so we reconstruct it directly
# from the live Census ACS 5-year API (the same plumbing population density uses).
# We implement EJSCREEN's two-component "core" demographic index:
#       demographic index = mean( %low-income , %people-of-color )
# percentile-ranked within the county and area-weighted over the catchment. The
# six-component "supplemental" index (adds limited-English, < HS education, under-5,
# over-64) is a documented extension; the two-component core is EPA's headline.

# Cache of per-county block-group demographics (geometry + demo_index) so we don't
# re-pull the whole county for every candidate.
_EJ_COUNTY_CACHE = {}


def _fetch_county_demographics(state_fips, county_fips):
    """Live ACS pull → GeoDataFrame[GEOID, demo_index, geometry] for one county.

    demo_index per block group = mean of the county percentile ranks of
    (% low-income, table C17002) and (% people of color, table B03002).
    Returns None on any failure (caller falls back to the neutral 0.5).
    """
    key = (state_fips, county_fips)
    if key in _EJ_COUNTY_CACHE:
        return _EJ_COUNTY_CACHE[key]

    base = "https://api.census.gov/data/2022/acs/acs5"
    get_vars = (
        "C17002_001E,C17002_002E,C17002_003E,C17002_004E,C17002_005E,"
        "C17002_006E,C17002_007E,B03002_001E,B03002_003E"
    )
    params = {
        "get": get_vars,
        "for": "block group:*",
        "in": f"state:{state_fips} county:{county_fips}",
    }
    api_key = census_api_key()
    if api_key:
        params["key"] = api_key
    try:
        r = requests.get(base, params=params, timeout=30)
        if r.status_code != 200:
            return None
        rows = r.json()
        header, data = rows[0], rows[1:]
    except Exception:
        return None

    df = pd.DataFrame(data, columns=header)
    num = lambda c: pd.to_numeric(df[c], errors="coerce").fillna(0)

    pov_total = num("C17002_001E").clip(lower=1)
    below_2x = sum(num(f"C17002_00{i}E") for i in range(2, 8))  # ratio < 2.0
    df["pct_lowinc"] = (below_2x / pov_total).clip(0, 1)

    race_total = num("B03002_001E").clip(lower=1)
    nh_white = num("B03002_003E")
    df["pct_poc"] = (1 - nh_white / race_total).clip(0, 1)

    # County percentile ranks → two-component demographic index in [0, 1]
    df["demo_index"] = (df["pct_lowinc"].rank(pct=True)
                        + df["pct_poc"].rank(pct=True)) / 2
    df["GEOID"] = (df["state"] + df["county"] + df["tract"] + df["block group"])

    tiger_url = (
        f"https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
        f"tigerWMS_ACS2022/MapServer/10/query?"
        f"where=STATE={state_fips}+AND+COUNTY={county_fips}"
        f"&outFields=GEOID&f=geojson&returnGeometry=true"
    )
    try:
        bg = gpd.read_file(tiger_url).to_crs(UTM_CRS)
    except Exception:
        return None

    bg = bg.merge(df[["GEOID", "demo_index"]], on="GEOID", how="left")
    bg["demo_index"] = bg["demo_index"].fillna(bg["demo_index"].mean())
    _EJ_COUNTY_CACHE[key] = bg
    return bg


def _area_weighted_index(bg_gdf, catchment_polygon):
    """Area-weighted mean of demo_index over the catchment (pure; unit-testable)."""
    if bg_gdf is None or bg_gdf.empty:
        return None
    catch = gpd.GeoDataFrame({"geometry": [catchment_polygon]}, crs=bg_gdf.crs)
    try:
        inter = gpd.overlay(bg_gdf, catch, how="intersection")
    except Exception:
        inter = bg_gdf[bg_gdf.intersects(catchment_polygon)]
    if inter is None or inter.empty:
        return float(bg_gdf["demo_index"].mean())
    w = inter.geometry.area
    if w.sum() <= 0:
        return float(inter["demo_index"].mean())
    return float((inter["demo_index"] * w).sum() / w.sum())


def get_ej_index(catchment_polygon, state_fips=DURHAM_STATE_FIPS,
                 county_fips=DURHAM_COUNTY_FIPS):
    """
    EJSCREEN demographic index reconstructed from live Census ACS, area-weighted
    over `catchment_polygon` (UTM). Returns 0-1, or None on failure (→ neutral 0.5
    via safe_call). Varies across catchments because it area-weights different
    block groups (also supports C2's per-candidate variation).
    """
    bg = _fetch_county_demographics(state_fips, county_fips)
    return _area_weighted_index(bg, catchment_polygon)


def get_ejscreen_index(lat, lon, radius_km=5):
    """DEPRECATED (C4): the EJSCREEN REST broker was decommissioned by EPA on
    2025-02-05 and always fails now. Kept only so old callers degrade gracefully;
    use `get_ej_index` (Census ACS reconstruction) instead."""
    return 0.5  # endpoint dead — neutral fallback


# ── 6.3 Protected Area Proximity (USGS PAD-US) ──────────────────────

PROTECTION_WEIGHTS = {
    "National Park": 1.0,
    "National Wildlife Refuge": 0.9,
    "Wilderness Area": 0.9,
    "State Park": 0.7,
    "State Wildlife": 0.6,
    "Local Park": 0.3,
}


def get_protected_area_score(candidate_point_utm, distance_km=20):
    """
    Score based on proximity to protected areas.
    Uses PAD-US if cached locally, otherwise estimates from OSM.
    """
    try:
        # Try PAD-US local cache first
        padus = gpd.read_file("data/padus.gpkg", layer="PADUS3_0Combined_Proclamation")
        padus = padus.to_crs(UTM_CRS)
        buffer = candidate_point_utm.buffer(distance_km * 1000)
        intersecting = padus[padus.intersects(buffer)]
        if intersecting.empty:
            return 0.0

        score = 0.0
        for _, area in intersecting.iterrows():
            designation = str(area.get("Des_Tp", ""))
            weight = next(
                (w for k, w in PROTECTION_WEIGHTS.items() if k in designation),
                0.2,
            )
            dist = candidate_point_utm.distance(area.geometry.centroid) / 1000
            score += weight * (1 / (1 + dist / 5))
        return score

    except Exception:
        # Fallback: use OSM parks
        try:
            import osmnx as ox
            pt_wgs = gpd.GeoDataFrame(
                {"geometry": [candidate_point_utm]}, crs=UTM_CRS
            ).to_crs(WGS84).geometry.iloc[0]

            parks = ox.features_from_point(
                (pt_wgs.y, pt_wgs.x),
                dist=distance_km * 1000,
                tags={"leisure": ["park", "nature_reserve"], "boundary": "protected_area"},
            )
            return min(len(parks) * 0.1, 1.0)
        except Exception:
            return 0.2  # assume some nearby green space


# ── 6.4 Superfund Site Proximity ─────────────────────────────────────

def get_superfund_sites(catchment_bbox):
    """Pull Superfund (CERCLIS/NPL) sites from EPA FRS API."""
    west, south, east, north = catchment_bbox
    url = "https://ofmpub.epa.gov/frs_public2/frs_rest_services.get_facilities"
    params = {
        "bbox": f"{west},{south},{east},{north}",
        "program_acronym": "CERCLIS",
        "output": "JSON",
        "pagesize": 100,
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        data = r.json().get("Results", {}).get("FRSFacility", [])
        if not data:
            return gpd.GeoDataFrame()
        return gpd.GeoDataFrame(
            data,
            geometry=[
                Point(float(f["Longitude83"]), float(f["Latitude83"]))
                for f in data if f.get("Longitude83") and f.get("Latitude83")
            ],
            crs=WGS84,
        ).to_crs(UTM_CRS)
    except Exception:
        return gpd.GeoDataFrame()


def superfund_proximity_score(candidate_point_utm, catchment_bbox):
    """Score based on proximity to Superfund sites."""
    sf = get_superfund_sites(catchment_bbox)
    return inverse_distance_score(candidate_point_utm, sf, half_decay_m=500)


# ── 6.5 Ocean/Estuary and Beach Proximity (H5 — must be distinct) ───

# Reference points (lat, lon). The estuary and beach references are genuinely
# different locations, so the two distances are decorrelated rather than the old
# `beach = estuary * 1.1` copy that double-counted one signal at weight 0.26.
NC_ESTUARY_REF = (35.05, -76.05)   # Pamlico Sound (estuary system)
NC_BEACH_REF = (34.21, -77.79)     # Wrightsville Beach (recreational beach)


def _haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km."""
    R = 6371.0088
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def estimate_estuary_distance_km(lat, lon):
    """
    Great-circle distance to the nearest NC estuary reference (Pamlico Sound).
    H5: a real haversine — the old code set `coast_lat = lat`, killing the dlat
    term so the result was longitude-only and didn't change with latitude.
    """
    return _haversine_km(lat, lon, *NC_ESTUARY_REF)


def estimate_beach_distance_km(lat, lon):
    """
    Great-circle distance to a recreational-beach reference (Wrightsville Beach).
    H5: genuinely distinct from the estuary reference, so estuary_dist and
    beach_dist are no longer perfectly collinear.
    """
    return _haversine_km(lat, lon, *NC_BEACH_REF)


# ── 6.6 Tourism / Recreation Value ──────────────────────────────────

def get_tourism_amenity_density(candidate_point_utm, radius_km=2):
    """Count parks, trails, and recreation amenities from OSM."""
    try:
        import osmnx as ox
        pt_wgs = gpd.GeoDataFrame(
            {"geometry": [candidate_point_utm]}, crs=UTM_CRS
        ).to_crs(WGS84).geometry.iloc[0]

        amenities = ox.features_from_point(
            (pt_wgs.y, pt_wgs.x),
            dist=radius_km * 1000,
            tags={"leisure": True, "tourism": True},
        )
        return len(amenities) / max(radius_km ** 2 * math.pi, 1)
    except Exception:
        return 1.0  # assume some amenities in urban area


# ── Aggregate Impact Features ────────────────────────────────────────

IMPACT_WEIGHTS = {
    "water_intake_score": 0.22,
    "protected_area_score": 0.16,
    "ej_index": 0.18,
    "estuary_dist_km": 0.14,  # inverted: closer = higher
    "beach_dist_km": 0.12,    # inverted
    "tourism_amenity_density": 0.10,
    "superfund_score": 0.08,
}


def compute_impact_features(candidate_row, intakes_gdf=None, bbox=None):
    """Compute all impact features for a single candidate."""
    point_utm = candidate_row.geometry
    lat = candidate_row.get("lat", 36.0)
    lon = candidate_row.get("lon", -78.9)

    if intakes_gdf is None:
        intakes_gdf = gpd.GeoDataFrame()

    # EJ index (C4): reconstructed from live Census ACS, area-weighted over the
    # candidate's catchment. Use an explicit catchment polygon if one was threaded
    # through; otherwise a per-candidate proxy disc sized by the upstream catchment
    # area, so the index varies across candidates rather than being one constant.
    catchment_poly = candidate_row.get("catchment_polygon")
    if catchment_poly is None:
        area_km2 = candidate_row.get("catchment_area_km2", 1.0) or 1.0
        radius_m = max(500.0, (max(area_km2, 0.01) / math.pi) ** 0.5 * 1000.0)
        catchment_poly = point_utm.buffer(radius_m)

    features = {
        "water_intake_score": safe_call(
            water_intake_score, point_utm, intakes_gdf, default=0.0
        ),
        "protected_area_score": safe_call(
            get_protected_area_score, point_utm, default=0.2
        ),
        "ej_index": safe_call(
            get_ej_index, catchment_poly, default=0.5
        ),
        "estuary_dist_km": estimate_estuary_distance_km(lat, lon),
        "beach_dist_km": estimate_beach_distance_km(lat, lon),  # H5: distinct reference
        "tourism_amenity_density": safe_call(
            get_tourism_amenity_density, point_utm, default=1.0
        ),
        "superfund_score": safe_call(
            superfund_proximity_score, point_utm, bbox, default=0.0
        ) if bbox else 0.0,
    }
    return features
