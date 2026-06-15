"""
gARB — Downstream Impact Parameters
Drinking water intake proximity, EJ index, protected areas,
ocean/estuary distance, recreational beach proximity, tourism value, superfund.
"""

import math
import requests
import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from core import UTM_CRS, WGS84, safe_call, inverse_distance_score


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
    Score based on downstream drinking water intakes.
    Exponential decay: score = sum(exp(-d/10)) for each intake within 50km.
    """
    if intakes_gdf.empty:
        return 0.0
    distances_km = intakes_gdf.geometry.distance(candidate_point_utm) / 1000
    relevant = distances_km[distances_km < max_dist_km]
    return float(sum(math.exp(-d / 10) for d in relevant))


# ── 6.2 Environmental Justice Index (EPA EJSCREEN) ──────────────────

def get_ejscreen_index(lat, lon, radius_km=5):
    """
    Pull EPA EJSCREEN percentile indices for a location.
    Returns composite EJ priority score (0-1).
    """
    url = "https://ejscreen.epa.gov/mapper/ejscreenRESTbroker.aspx"
    params = {
        "namestr": "",
        "geometry": f'{{"x":{lon},"y":{lat},"spatialReference":{{"wkid":4326}}}}',
        "distance": radius_km,
        "unit": "9035",  # kilometers
        "areatype": "",
        "areaid": "",
        "f": "json",
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        data = r.json()
        results = data.get("data", {}).get("rows", [{}])[0]

        ej_index = float(results.get("P_EJ_PWDIS", 50))   # Wastewater discharge EJ
        ej_minority = float(results.get("P_MINORPCT", 50))  # Minority population
        ej_income = float(results.get("P_LWincome", 50))    # Low-income

        return (ej_index * 0.4 + ej_minority * 0.3 + ej_income * 0.3) / 100
    except Exception:
        return 0.5  # neutral fallback


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


# ── 6.5 Ocean/Estuary Proximity ─────────────────────────────────────

def estimate_estuary_distance_km(lat, lon):
    """
    Rough estimate of distance to nearest ocean/estuary from coordinates.
    For NC: Pamlico Sound / Atlantic coast is roughly at -76.5 lon.
    """
    # Simple great-circle approximation to NC coast
    coast_lon = -76.5
    coast_lat = lat  # approximate same latitude
    dlat = (lat - coast_lat) * 111.32
    dlon = (lon - coast_lon) * 111.32 * math.cos(math.radians(lat))
    return math.sqrt(dlat ** 2 + dlon ** 2)


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

    features = {
        "water_intake_score": safe_call(
            water_intake_score, point_utm, intakes_gdf, default=0.0
        ),
        "protected_area_score": safe_call(
            get_protected_area_score, point_utm, default=0.2
        ),
        "ej_index": safe_call(
            get_ejscreen_index, lat, lon, default=0.5
        ),
        "estuary_dist_km": estimate_estuary_distance_km(lat, lon),
        "beach_dist_km": estimate_estuary_distance_km(lat, lon) * 1.1,  # proxy
        "tourism_amenity_density": safe_call(
            get_tourism_amenity_density, point_utm, default=1.0
        ),
        "superfund_score": safe_call(
            superfund_proximity_score, point_utm, bbox, default=0.0
        ) if bbox else 0.0,
    }
    return features
