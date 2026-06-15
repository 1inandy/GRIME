"""
gARB — Composite Scoring Model
28 parameters → 4 sub-scores → 1 composite score per candidate site.

Architecture:
  Parameters → [MinMax normalize] → [weighted sum] → Sub-score (0-100)
  Sub-scores → [weighted sum] → Composite (0-100)

Sub-score weights: Generation 0.30, Flow 0.25, Impact 0.30, Feasibility 0.15
"""

import numpy as np
import pandas as pd
import geopandas as gpd
from sklearn.preprocessing import MinMaxScaler
from concurrent.futures import ThreadPoolExecutor, as_completed

from core import safe_call, UTM_CRS
from core.generation import GENERATION_WEIGHTS, compute_generation_features
from core.flow import FLOW_WEIGHTS, compute_flow_features, get_discharge_stats
from core.impact import IMPACT_WEIGHTS, compute_impact_features, get_drinking_water_intakes
from core.feasibility import (
    FEASIBILITY_WEIGHTS, compute_feasibility_features, passes_hard_gates
)


# ── Sub-score weights ────────────────────────────────────────────────

SUB_SCORE_WEIGHTS = {
    "generation_score": 0.30,
    "flow_score": 0.25,
    "impact_score": 0.30,
    "feasibility_score": 0.15,
}


# ── Sub-score computation ────────────────────────────────────────────

def compute_subscore(df, weights):
    """
    Normalize each parameter to [0,1] independently, then weight-sum.
    Returns Series of sub-scores (0-100) for all candidates.
    """
    cols = [c for c in weights.keys() if c in df.columns]
    if not cols:
        return pd.Series(50.0, index=df.index)

    scaler = MinMaxScaler()
    normed = pd.DataFrame(
        scaler.fit_transform(df[cols].fillna(0)),
        columns=cols, index=df.index,
    )

    # Invert distance-based columns (closer = better for impact)
    for col in ["estuary_dist_km", "beach_dist_km"]:
        if col in normed.columns:
            normed[col] = 1 - normed[col]

    # Invert seasonal CV (lower variability = better for flow)
    if "seasonal_cv" in normed.columns:
        normed["seasonal_cv"] = 1 - normed["seasonal_cv"]

    w = np.array([weights[c] for c in cols])
    w = w / w.sum()  # renormalize after filtering
    return (normed[cols] @ w) * 100


# ── Hard gates ───────────────────────────────────────────────────────

def apply_hard_gates(df):
    """Remove candidates that are physically infeasible."""
    n_before = len(df)

    if "flow_velocity_ms" in df.columns:
        df = df[df["flow_velocity_ms"] < 3.0]
    if "channel_width_m" in df.columns:
        df = df[(df["channel_width_m"] >= 0.5) & (df["channel_width_m"] <= 50.0)]
    if "land_ownership" in df.columns:
        df = df[df["land_ownership"] > 0]

    removed = n_before - len(df)
    if removed > 0:
        print(f"Hard gates removed {removed} candidates ({n_before} → {len(df)})")
    return df


# ── Composite score ──────────────────────────────────────────────────

def compute_composite_score(candidates_df):
    """Full scoring pipeline — returns ranked GeoDataFrame."""
    df = apply_hard_gates(candidates_df.copy())

    df["generation_score"] = compute_subscore(df, GENERATION_WEIGHTS)
    df["flow_score"] = compute_subscore(df, FLOW_WEIGHTS)
    df["impact_score"] = compute_subscore(df, IMPACT_WEIGHTS)
    df["feasibility_score"] = compute_subscore(df, FEASIBILITY_WEIGHTS)

    df["composite_score"] = (
        SUB_SCORE_WEIGHTS["generation_score"] * df["generation_score"]
        + SUB_SCORE_WEIGHTS["flow_score"] * df["flow_score"]
        + SUB_SCORE_WEIGHTS["impact_score"] * df["impact_score"]
        + SUB_SCORE_WEIGHTS["feasibility_score"] * df["feasibility_score"]
    )

    return df.sort_values("composite_score", ascending=False).reset_index(drop=True)


# ── Sensitivity Analysis ─────────────────────────────────────────────

def sensitivity_analysis(candidates_df, n_perturbations=50):
    """
    Monte Carlo weight perturbation — assess ranking stability.
    Returns DataFrame showing how often each site appears in top 5.
    """
    baseline = compute_composite_score(candidates_df)

    if len(baseline) == 0:
        return baseline

    top5_counts = pd.Series(0, index=baseline.index, dtype=float)

    for _ in range(n_perturbations):
        alpha = np.array([0.30, 0.25, 0.30, 0.15]) * 10
        perturbed = np.random.dirichlet(alpha)

        df = baseline.copy()
        df["composite_score"] = (
            perturbed[0] * df["generation_score"]
            + perturbed[1] * df["flow_score"]
            + perturbed[2] * df["impact_score"]
            + perturbed[3] * df["feasibility_score"]
        )
        top5 = df.nlargest(5, "composite_score").index
        top5_counts[top5] += 1

    baseline["robustness_pct"] = (top5_counts / n_perturbations * 100)
    return baseline.sort_values("composite_score", ascending=False)


# ── Full Feature Pipeline ────────────────────────────────────────────

def build_all_features(candidates_gdf, bbox, stream_gdf=None,
                       dem_array=None, dem_transform=None,
                       max_workers=4):
    """
    Compute all 28 parameter features for all candidates.
    Uses threading for API calls. Returns enriched GeoDataFrame.
    """
    df = candidates_gdf.copy()
    print(f"Computing features for {len(df)} candidates...")

    # Pre-fetch shared data
    print("  Fetching USGS gauge data...")
    gauge_stats = safe_call(get_discharge_stats, default={
        "mean_q_cfs": 12.5, "median_q_cfs": 5.2, "peak_q_cfs": 450.0,
        "max_q_cfs": 2100.0, "cv": 2.1, "high_flow_days": 36, "n_years": 20,
    })

    print("  Fetching drinking water intakes...")
    intakes_gdf = safe_call(get_drinking_water_intakes, default=gpd.GeoDataFrame())

    # Process each candidate
    for idx, row in df.iterrows():
        if idx % 10 == 0:
            print(f"  Processing candidate {idx + 1}/{len(df)}...")

        # Flow features (fastest — local computation)
        flow_feats = compute_flow_features(
            row, dem_array=dem_array, dem_transform=dem_transform,
            gauge_stats=gauge_stats,
        )
        for k, v in flow_feats.items():
            df.at[idx, k] = v

        # Feasibility features
        feas_feats = compute_feasibility_features(
            row, stream_gdf=stream_gdf,
            dem_array=dem_array,
        )
        for k, v in feas_feats.items():
            df.at[idx, k] = v

    # Generation and Impact features — these hit APIs, do in bulk
    # For hackathon: use catchment-level aggregates rather than per-candidate
    print("  Computing generation features (catchment-level)...")
    gen_features = safe_call(
        _compute_catchment_generation, bbox,
        default={k: 0.5 for k in GENERATION_WEIGHTS},
    )
    for k, v in gen_features.items():
        df[k] = v

    print("  Computing impact features...")
    for idx, row in df.iterrows():
        impact_feats = compute_impact_features(
            row, intakes_gdf=intakes_gdf, bbox=bbox,
        )
        for k, v in impact_feats.items():
            df.at[idx, k] = v

    print("  Computing composite scores...")
    scored = compute_composite_score(df)
    print(f"  Done. Top score: {scored['composite_score'].max():.1f}")
    return scored


def _compute_catchment_generation(bbox):
    """Compute generation features at the catchment level (shared across candidates)."""
    from core.generation import (
        get_impervious_pct, tri_density_score, npdes_count
    )
    from core import bbox_to_polygon

    catch_poly = bbox_to_polygon(bbox).to_crs(UTM_CRS).geometry.iloc[0]
    west, south, east, north = bbox
    area_km2 = catch_poly.area / 1e6

    return {
        "population_density": safe_call(
            lambda: 800.0, default=500.0  # Durham ~800 persons/km²
        ),
        "impervious_pct": safe_call(
            get_impervious_pct, catch_poly, bbox, default=35.0
        ),
        "road_density_km_km2": 5.0,  # Durham average
        "tri_facility_density": safe_call(
            tri_density_score, catch_poly, area_km2, bbox, default=0.0
        ),
        "npdes_points": safe_call(
            npdes_count, catch_poly, bbox, default=0
        ),
        "cso_density": 0.5,  # catchment-level placeholder
        "litter_complaint_density": 2.0,  # Durham average
    }
