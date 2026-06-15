"""
gARB — Flow Transport Parameters
USGS discharge, Manning's velocity, Strahler order, catchment area,
flood return period, seasonal variability, runoff coefficient.
"""

import requests
import numpy as np
from core import ELLERBE_GAUGE, safe_call


# ── Manning's Roughness Coefficients (Chow 1959) ────────────────────

MANNING_N = {
    "clean_straight": 0.030,
    "winding_some_pool": 0.040,
    "sluggish_weedy": 0.070,
    "urban_concrete": 0.015,
}


# ── 3.1 USGS Stream Gauge Discharge ─────────────────────────────────

def get_discharge_stats(site_no=ELLERBE_GAUGE, start="2000-01-01", end="2023-12-31"):
    """
    Pull USGS daily discharge data and compute summary statistics.
    Returns dict with mean Q, peak Q, and CV.
    """
    try:
        import dataretrieval.nwis as nwis

        df, meta = nwis.get_dv(
            sites=site_no, parameterCd="00060",
            start=start, end=end,
        )

        discharge = df["00060_Mean"].dropna()

        peaks, _ = nwis.get_peaks(sites=site_no)
        peak_values = peaks["peak_va"].dropna().astype(float)

        stats = {
            "mean_q_cfs": float(discharge.mean()),
            "median_q_cfs": float(discharge.median()),
            "peak_q_cfs": float(peak_values.mean()) if len(peak_values) > 0 else float(discharge.quantile(0.99)),
            "max_q_cfs": float(peak_values.max()) if len(peak_values) > 0 else float(discharge.max()),
            "cv": float(discharge.std() / discharge.mean()) if discharge.mean() > 0 else 1.0,
            "high_flow_days": int((discharge > discharge.quantile(0.9)).sum()),
            "n_years": len(peak_values),
        }
        return stats

    except Exception as e:
        print(f"  [warn] USGS data fetch failed: {e}")
        # Ellerbe Creek typical values as fallback
        return {
            "mean_q_cfs": 12.5,
            "median_q_cfs": 5.2,
            "peak_q_cfs": 450.0,
            "max_q_cfs": 2100.0,
            "cv": 2.1,
            "high_flow_days": 36,
            "n_years": 20,
        }


# ── 3.2 Flow Velocity — Manning's Equation ──────────────────────────

def compute_flow_velocity(candidate_row, candidate_col, dem_array, dem_transform,
                          channel_width_m, discharge_cfs,
                          channel_type="winding_some_pool"):
    """
    Compute mean channel velocity using Manning's equation.
    V = (1/n) * R^(2/3) * S^(1/2)
    """
    n = MANNING_N[channel_type]

    # Channel slope from DEM
    reach_len = 100  # meters
    pixel_size = abs(dem_transform[0])
    pixels_downstream = max(1, int(reach_len / pixel_size))

    r, c = int(candidate_row), int(candidate_col)
    elev_here = dem_array[r, c]
    r_down = min(r + pixels_downstream, dem_array.shape[0] - 1)
    elev_down = dem_array[r_down, c]
    slope = max((elev_here - elev_down) / reach_len, 0.0001)

    # Hydraulic radius — rectangular channel approximation
    depth_m = 0.3 * channel_width_m
    area_cross = channel_width_m * depth_m
    wetted_perim = channel_width_m + 2 * depth_m
    R = area_cross / wetted_perim

    # Manning's velocity
    V_manning = (1.0 / n) * (R ** (2 / 3)) * (slope ** 0.5)

    # Continuity check: V = Q / A
    Q_m3s = discharge_cfs * 0.0283168
    V_continuity = Q_m3s / max(area_cross, 0.01)

    # Geometric mean of both estimates
    V_final = (abs(V_manning) * abs(V_continuity)) ** 0.5
    return float(V_final)


def velocity_feasibility(V_ms):
    """Feasibility gate — penalize sites with velocity outside deployable range."""
    if V_ms < 0.05:
        return 0.3  # too slow — debris doesn't concentrate
    if V_ms < 0.30:
        return 0.7  # slow but workable
    if V_ms <= 1.50:
        return 1.0  # optimal interception range
    if V_ms <= 2.50:
        return 0.5  # fast — trap needs heavy anchoring
    return 0.1  # too fast — trap will be damaged


# ── 3.3 Flood Return Period — USGS StreamStats ──────────────────────

def get_flood_frequency(lat, lon, state_code="NC"):
    """
    Pull flood frequency statistics from USGS StreamStats API.
    Returns dict of Q2, Q10, Q25, Q50, Q100 in cfs.
    """
    delineate_url = "https://streamstats.usgs.gov/streamstatsservices/watershed.json"
    params = {
        "rcode": state_code,
        "xlocation": lon,
        "ylocation": lat,
        "crs": 4326,
        "includeparameters": "false",
        "includeflowtypes": "true",
        "includefeatures": "false",
    }

    try:
        r = requests.get(delineate_url, params=params, timeout=60)
        if r.status_code != 200:
            return None
        ws_data = r.json()
        workspace_id = ws_data.get("workspaceID", "")

        stats_url = "https://streamstats.usgs.gov/streamstatsservices/flowstatistics.json"
        stats_params = {
            "rcode": state_code,
            "workspaceID": workspace_id,
            "includeflowtypes": "true",
        }
        rs = requests.get(stats_url, params=stats_params, timeout=60)
        stats = rs.json()

        result = {}
        for group in stats.get("FlowStatisticsList", []):
            for stat in group.get("RegressionRegions", [{}])[0].get("Results", []):
                code = stat.get("code", "")
                value = stat.get("Value", None)
                if "Q" in code and value:
                    result[code] = float(value)
        return result  # {'Q2': 245.0, 'Q10': 890.0, ...}
    except Exception:
        return None


# ── 3.4 Seasonal Flow Variability ───────────────────────────────────

def compute_seasonal_cv(site_no=ELLERBE_GAUGE):
    """Coefficient of variation of monthly mean flows — higher = flashier."""
    stats = get_discharge_stats(site_no)
    return stats.get("cv", 2.0)


# ── 3.5 Runoff Coefficient C ────────────────────────────────────────

def estimate_runoff_coefficient(impervious_pct):
    """
    Estimate rational method runoff coefficient from impervious surface %.
    C = 0.05 + 0.009 * impervious_pct (linear approximation from NLCD k-means).
    Range: 0.05 (forest) to 0.95 (concrete).
    """
    C = 0.05 + 0.009 * impervious_pct
    return min(max(C, 0.05), 0.95)


# ── Aggregate Flow Features ─────────────────────────────────────────

FLOW_WEIGHTS = {
    "usgs_mean_q_cfs": 0.22,
    "flow_velocity_ms": 0.16,
    "strahler_order": 0.14,
    "catchment_area_km2": 0.18,
    "flood_q10_cfs": 0.14,
    "seasonal_cv": 0.10,
    "runoff_coeff_C": 0.06,
}


def compute_flow_features(candidate_row, dem_array=None, dem_transform=None,
                          gauge_stats=None):
    """Compute all flow features for a single candidate."""
    if gauge_stats is None:
        gauge_stats = get_discharge_stats()

    width = candidate_row.get("channel_width_m", 5.0)
    imp = candidate_row.get("impervious_pct", 35.0)

    velocity = 0.5  # default
    if dem_array is not None and dem_transform is not None:
        velocity = safe_call(
            compute_flow_velocity,
            candidate_row.get("pixel_row", 0),
            candidate_row.get("pixel_col", 0),
            dem_array, dem_transform,
            width, gauge_stats["mean_q_cfs"],
            default=0.5,
        )

    flood_stats = safe_call(
        get_flood_frequency,
        candidate_row.get("lat", 36.0),
        candidate_row.get("lon", -78.9),
        default=None,
    )
    q10 = flood_stats.get("Q10", gauge_stats["peak_q_cfs"]) if flood_stats else gauge_stats["peak_q_cfs"]

    features = {
        "usgs_mean_q_cfs": gauge_stats["mean_q_cfs"],
        "flow_velocity_ms": velocity,
        "strahler_order": candidate_row.get("strahler_order", 2),
        "catchment_area_km2": candidate_row.get("catchment_area_km2", 1.0),
        "flood_q10_cfs": q10,
        "seasonal_cv": gauge_stats["cv"],
        "runoff_coeff_C": estimate_runoff_coefficient(imp),
    }
    return features
