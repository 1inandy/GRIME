"""
GRIME — Composite Scoring Model
27 parameters → 4 sub-scores → 1 composite score per candidate site.

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
from core.flow import (
    FLOW_WEIGHTS, compute_flow_features, get_discharge_stats,
    velocity_transport_favorability,
)
from core.impact import IMPACT_WEIGHTS, compute_impact_features, get_drinking_water_intakes
from core.feasibility import (
    FEASIBILITY_WEIGHTS, compute_feasibility_features, passes_hard_gates
)

# All 27 parameter names, in family order — used by summarize_provenance.
ALL_PARAMS = (list(GENERATION_WEIGHTS) + list(FLOW_WEIGHTS)
              + list(IMPACT_WEIGHTS) + list(FEASIBILITY_WEIGHTS))


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

    C2/M2: a constant column maps to **0** under sklearn's MinMaxScaler, which
    silently deletes that parameter's weight from the sub-score (this is exactly
    what made the catchment-level Generation family contribute nothing). So we
    **exclude constant columns** and **renormalize the surviving weights**, logging
    what was dropped. If *nothing* varies, the family can't rank anything → return a
    neutral 50 (not 0).
    M5: velocity enters Flow through a peaked transport-favorability curve, not raw
    (raw monotonic-good would fight the Feasibility velocity gate).
    """
    cols = [c for c in weights.keys() if c in df.columns]
    if not cols:
        return pd.Series(50.0, index=df.index)

    sub = df[cols].fillna(0).copy()

    # M5: transform velocity to peaked favorability before it enters Flow scoring
    # (the raw value stays in df for the Feasibility gate + display).
    if "flow_velocity_ms" in sub.columns:
        sub["flow_velocity_ms"] = sub["flow_velocity_ms"].map(velocity_transport_favorability)

    # C2: keep only columns that actually vary; drop + renormalize the rest.
    varying = [c for c in cols if sub[c].nunique(dropna=False) > 1]
    dropped = [c for c in cols if c not in varying]
    if dropped:
        print(f"  [subscore] dropped {len(dropped)} constant column(s) "
              f"{dropped} → renormalizing {len(varying)} weight(s)")
    if not varying:
        return pd.Series(50.0, index=df.index)

    scaler = MinMaxScaler()
    normed = pd.DataFrame(
        scaler.fit_transform(sub[varying]),
        columns=varying, index=df.index,
    )

    # Invert distance-based columns (closer = better for impact)
    for col in ["estuary_dist_km", "beach_dist_km"]:
        if col in normed.columns:
            normed[col] = 1 - normed[col]

    # Invert seasonal CV (lower variability = better for flow)
    if "seasonal_cv" in normed.columns:
        normed["seasonal_cv"] = 1 - normed["seasonal_cv"]

    w = np.array([weights[c] for c in varying])
    w = w / w.sum()  # renormalize over surviving (varying) columns
    return (normed[varying] @ w) * 100


def summarize_provenance(df):
    """
    C2/C4 support: print a loud per-run summary of how many of the 27 parameters
    actually vary per-candidate (live) versus are constant (fallback/dead endpoint).
    Makes constant columns and dead endpoints visible every run.
    """
    present = [c for c in ALL_PARAMS if c in df.columns]
    varying = [c for c in present if df[c].nunique(dropna=False) > 1]
    constant = [c for c in present if c not in varying]
    print("=" * 66)
    print(f"  PROVENANCE: {len(varying)}/{len(ALL_PARAMS)} parameters vary "
          f"per-candidate (live) · {len(constant)} constant (fallback)")
    if constant:
        print(f"  constant/fallback: {', '.join(constant)}")
    missing = [c for c in ALL_PARAMS if c not in present]
    if missing:
        print(f"  missing entirely: {', '.join(missing)}")
    print("=" * 66)
    return {"varying": varying, "constant": constant, "n_params": len(ALL_PARAMS)}


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


# ── Bayesian Weight Optimization (C6) ────────────────────────────────

def optimize_weights(candidates_df, known_good_indices, n_calls=40, random_state=42):
    """
    Gaussian-process (Bayesian) optimization of the four sub-score weights so that
    known-good deployment sites rank as near the top as possible.

    Parameters
    ----------
    candidates_df : (Geo)DataFrame. If the four ``*_score`` columns are already
        present they are used directly; otherwise they are computed from the
        per-family parameter weights via :func:`compute_subscore`.
    known_good_indices : iterable of ``candidates_df`` index labels that are
        known-good sites (e.g. field-validated accumulation points).
    n_calls : gp_minimize evaluations. random_state : reproducibility.

    Returns
    -------
    dict mapping ``generation_score``/``flow_score``/``impact_score``/
    ``feasibility_score`` to optimized weights that sum to 1.0.

    scikit-optimize is imported lazily; an informative ImportError is raised if
    it is not installed.
    """
    try:
        from skopt import gp_minimize
        from skopt.space import Real
    except ImportError as e:  # pragma: no cover - exercised only without skopt
        raise ImportError(
            "optimize_weights requires scikit-optimize "
            "(pip install scikit-optimize)."
        ) from e

    df = candidates_df.copy()
    families = (
        ("generation_score", GENERATION_WEIGHTS),
        ("flow_score", FLOW_WEIGHTS),
        ("impact_score", IMPACT_WEIGHTS),
        ("feasibility_score", FEASIBILITY_WEIGHTS),
    )
    for col, fam_w in families:
        if col not in df.columns:
            df[col] = compute_subscore(df, fam_w)

    sub = df[[c for c, _ in families]].to_numpy(dtype=float)
    good = [df.index.get_loc(i) for i in known_good_indices if i in df.index]
    if not good:
        raise ValueError("none of known_good_indices are present in candidates_df")

    n = len(df)

    def objective(w):
        w = np.asarray(w, dtype=float)
        w = w / w.sum()
        composite = sub @ w
        order = np.argsort(-composite)              # best → worst
        rank = np.empty(n, dtype=int)
        rank[order] = np.arange(n)                  # rank 0 = top of the list
        return float(np.mean(rank[good]))           # minimize mean rank of good sites

    space = [Real(0.05, 0.60, name=s)
             for s in ("generation", "flow", "impact", "feasibility")]
    result = gp_minimize(objective, space, n_calls=n_calls, random_state=random_state)

    w = np.asarray(result.x, dtype=float)
    w = w / w.sum()
    return {c: float(w[i]) for i, (c, _) in enumerate(families)}


# ── Full Feature Pipeline ────────────────────────────────────────────

def _candidate_catchment_polygon(row):
    """Per-candidate catchment proxy: a disc sized by the candidate's upstream
    catchment area (real km² after C1a). Different candidates → different polygons
    → different block groups / land cover, so generation + EJ vary per candidate
    (C2). When a true delineated catchment is available it can be threaded in via a
    'catchment_polygon' column instead."""
    poly = row.get("catchment_polygon")
    if poly is not None:
        return poly
    area_km2 = row.get("catchment_area_km2", 1.0) or 1.0
    radius_m = max(500.0, (max(area_km2, 0.01) / np.pi) ** 0.5 * 1000.0)
    return row.geometry.buffer(radius_m)


def build_all_features(candidates_gdf, bbox, stream_gdf=None,
                       dem_array=None, dem_transform=None, fdir=None,
                       max_workers=4):
    """
    Compute all 27 parameter features for all candidates.
    Uses threading for API calls. Returns enriched GeoDataFrame.

    C2: generation features are computed **per candidate** on each candidate's own
    catchment (proxy disc), not collapsed to one catchment-wide column. `fdir` (the
    D8 flow grid) is threaded into the flow features so channel slope follows the
    real downstream direction (C1b/M7).
    """
    df = candidates_gdf.copy()
    print(f"Computing features for {len(df)} candidates...")

    # Pre-fetch shared data
    print("  Fetching USGS gauge data...")
    gauge_stats = safe_call(get_discharge_stats, default={
        "mean_q_cfs": 12.5, "median_q_cfs": 5.2, "peak_q_cfs": 450.0,
        "max_q_cfs": 2100.0, "cv": 2.1, "high_flow_days": 36, "n_peak_records": 20,
    })

    print("  Fetching drinking water intakes...")
    intakes_gdf = safe_call(get_drinking_water_intakes, default=gpd.GeoDataFrame())

    # Process each candidate
    for idx, row in df.iterrows():
        if idx % 10 == 0:
            print(f"  Processing candidate {idx + 1}/{len(df)}...")

        # Flow features (fastest — local computation; fdir → real downstream slope)
        flow_feats = compute_flow_features(
            row, dem_array=dem_array, dem_transform=dem_transform,
            gauge_stats=gauge_stats, fdir=fdir,
        )
        for k, v in flow_feats.items():
            df.at[idx, k] = v

        # Feasibility features
        feas_feats = compute_feasibility_features(
            row, stream_gdf=stream_gdf,
            dem_array=dem_array, bbox=bbox,
        )
        for k, v in feas_feats.items():
            df.at[idx, k] = v

    # Generation features — PER CANDIDATE (C2), on each candidate's own catchment.
    print("  Computing generation features (per candidate)...")
    for idx, row in df.iterrows():
        catch_poly = _candidate_catchment_polygon(row)
        gen_feats = safe_call(
            compute_generation_features, row, catch_poly, bbox,
            default={k: 0.5 for k in GENERATION_WEIGHTS},
        )
        for k, v in gen_feats.items():
            df.at[idx, k] = v

    print("  Computing impact features...")
    for idx, row in df.iterrows():
        impact_feats = compute_impact_features(
            row, intakes_gdf=intakes_gdf, bbox=bbox,
        )
        for k, v in impact_feats.items():
            df.at[idx, k] = v

    # Loud per-run provenance: how many of the 27 params actually vary (C2/C4).
    summarize_provenance(df)

    print("  Computing composite scores...")
    scored = compute_composite_score(df)
    print(f"  Done. Top score: {scored['composite_score'].max():.1f}")
    return scored
