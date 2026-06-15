"""
GRIME — Flow Transport Parameters
USGS discharge, Manning's velocity, stream order, catchment area,
flood return period, seasonal variability, runoff coefficient.
"""

import math
import requests
import numpy as np
from core import ELLERBE_GAUGE, ELLERBE_DRAINAGE_KM2, safe_call


# ── pysheds D8 direction map: code → (drow, dcol) in a north-up raster ──
# pysheds default ESRI dirmap: 64=N 128=NE 1=E 2=SE 4=S 8=SW 16=W 32=NW
D8_DIRMAP = {
    64: (-1, 0), 128: (-1, 1), 1: (0, 1), 2: (1, 1),
    4: (1, 0), 8: (1, -1), 16: (0, -1), 32: (-1, -1),
}


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
            # L6: count of annual-peak *records*, not guaranteed-distinct years.
            "n_peak_records": len(peak_values),
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
            "n_peak_records": 20,
        }


# ── 3.2 Flow Velocity — Manning's Equation ──────────────────────────

def _follow_downstream(r, c, dem_array, pixel_m, fdir=None, reach=100.0):
    """
    Walk ~`reach` metres downstream from (r, c) and return the end (row, col).

    C1b/M7: the old code stepped `reach/pixel` cells *due south*, which (with the
    degree-grid bug) meant ~1.1M cells → it clamped to the bottom DEM row and the
    slope was meaningless. Here we follow the **D8 flow grid** when `fdir` is
    given, falling back to steepest local descent otherwise.
    """
    steps = max(1, int(round(reach / max(pixel_m, 1e-6))))
    rr, cc = int(r), int(c)
    nrows, ncols = dem_array.shape
    for _ in range(steps):
        moved = False
        if fdir is not None:
            try:
                code = int(fdir[rr, cc])
            except Exception:
                code = -1
            if code in D8_DIRMAP:
                dr, dc = D8_DIRMAP[code]
                nr, nc = rr + dr, cc + dc
                if 0 <= nr < nrows and 0 <= nc < ncols:
                    rr, cc = nr, nc
                    moved = True
        if not moved:
            # Steepest-descent fallback (no fdir, or fdir hit a sink/edge).
            best, best_drop = None, 0.0
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = rr + dr, cc + dc
                    if 0 <= nr < nrows and 0 <= nc < ncols:
                        drop = float(dem_array[rr, cc] - dem_array[nr, nc])
                        if drop > best_drop:
                            best_drop, best = drop, (nr, nc)
            if best is None:
                break
            rr, cc = best
    return rr, cc


def velocity_transport_favorability(v_ms):
    """
    M5: velocity is "good" for interception only in a mid-range. Too slow → debris
    settles / doesn't arrive; too fast → debris overtops or the net tears. So in the
    Flow sub-score velocity must be a **peaked** transport-favorability curve, not
    monotonic-good (which would fight the Feasibility `velocity_feasibility` gate).
    Gaussian centred on ~0.9 m/s (width 0.6) → 1.0 at the optimum, ~0 at extremes.
    """
    return float(math.exp(-((v_ms - 0.9) / 0.6) ** 2))


def compute_flow_velocity(candidate_row, candidate_col, dem_array, dem_transform,
                          channel_width_m, discharge_cfs,
                          channel_type="winding_some_pool", fdir=None,
                          catchment_area_km2=None,
                          ref_catchment_km2=ELLERBE_DRAINAGE_KM2):
    """
    Compute mean channel velocity using Manning's equation.
    V = (1/n) * R^(2/3) * S^(1/2), cross-checked against area-scaled continuity.
    """
    n = MANNING_N[channel_type]

    # Channel slope along the real downstream reach (C1b/M7). After the C1 UTM
    # reprojection, pixel_size is metres, so the walk covers ~reach metres.
    pixel_size = abs(dem_transform[0])
    r, c = int(candidate_row), int(candidate_col)
    elev_here = float(dem_array[r, c])
    r_down, c_down = _follow_downstream(r, c, dem_array, pixel_size,
                                        fdir=fdir, reach=100.0)
    walked_m = max(math.hypot(r_down - r, c_down - c) * pixel_size, 1.0)
    elev_down = float(dem_array[r_down, c_down])
    slope = max((elev_here - elev_down) / walked_m, 0.0001)

    # Hydraulic radius — rectangular channel approximation
    depth_m = 0.3 * channel_width_m
    area_cross = channel_width_m * depth_m
    wetted_perim = channel_width_m + 2 * depth_m
    R = area_cross / wetted_perim

    # Manning's velocity
    V_manning = (1.0 / n) * (R ** (2 / 3)) * (slope ** 0.5)

    # Continuity cross-check: V = Q / A. M4 — scale the gauge's basin-wide mean Q to
    # the candidate's own catchment area (now real km² after C1a) so this is
    # site-specific rather than injecting one gauge's mean flow into every site.
    Q_site = discharge_cfs
    if catchment_area_km2 is not None and ref_catchment_km2:
        Q_site = discharge_cfs * (max(catchment_area_km2, 0.01) / max(ref_catchment_km2, 0.1))
    Q_m3s = Q_site * 0.0283168
    V_continuity = Q_m3s / max(area_cross, 0.01)

    # Geometric mean of both estimates (a soft blend, not a fully independent check)
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
    Estimate the rational-method runoff coefficient C from impervious surface %.
    L4: this is a simple **linear impervious→C approximation**, C = 0.05 + 0.009·I
    (not a k-means/land-cover classification). Range 0.05 (forest) to 0.95 (concrete).
    """
    C = 0.05 + 0.009 * impervious_pct
    return min(max(C, 0.05), 0.95)


# ── Aggregate Flow Features ─────────────────────────────────────────

FLOW_WEIGHTS = {
    "usgs_mean_q_cfs": 0.22,
    "flow_velocity_ms": 0.16,
    "stream_order": 0.14,          # H3: node-degree heuristic, renamed from strahler_order
    "catchment_area_km2": 0.18,
    "flood_q10_cfs": 0.14,
    "seasonal_cv": 0.10,
    "runoff_coeff_C": 0.06,
}


def compute_flow_features(candidate_row, dem_array=None, dem_transform=None,
                          gauge_stats=None, fdir=None):
    """Compute all flow features for a single candidate."""
    if gauge_stats is None:
        gauge_stats = get_discharge_stats()

    width = candidate_row.get("channel_width_m", 5.0)
    imp = candidate_row.get("impervious_pct", 35.0)
    catch_km2 = candidate_row.get("catchment_area_km2", 1.0)

    velocity = 0.5  # default
    if dem_array is not None and dem_transform is not None:
        velocity = safe_call(
            compute_flow_velocity,
            candidate_row.get("pixel_row", 0),
            candidate_row.get("pixel_col", 0),
            dem_array, dem_transform,
            width, gauge_stats["mean_q_cfs"],
            fdir=fdir,                       # C1b/M7: follow the D8 flow grid
            catchment_area_km2=catch_km2,    # M4: site-specific continuity
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
        "stream_order": candidate_row.get("stream_order", 2),
        "catchment_area_km2": catch_km2,
        "flood_q10_cfs": q10,
        "seasonal_cv": gauge_stats["cv"],
        "runoff_coeff_C": estimate_runoff_coefficient(imp),
    }
    return features
