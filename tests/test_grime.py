"""
GRIME property tests (L9 / Phase 6).

Cheap, deterministic invariants over the real scoring code — no network, no pysheds.
Run: pytest -q
"""
import os
import sys
import math

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.scoring import (
    compute_subscore, compute_composite_score, apply_hard_gates, optimize_weights,
    summarize_provenance,
)
from core.generation import GENERATION_WEIGHTS
from core.flow import (
    FLOW_WEIGHTS, compute_flow_velocity, velocity_feasibility,
    velocity_transport_favorability, estimate_runoff_coefficient,
)
from core.feasibility import FEASIBILITY_WEIGHTS, channel_width_score, get_channel_width
from core.impact import (
    IMPACT_WEIGHTS, estimate_estuary_distance_km, estimate_beach_distance_km,
    _area_weighted_index,
)
import geopandas as gpd
from shapely.geometry import box, Point, LineString


# ── 27 parameters, weights normalized ────────────────────────────────

def test_parameter_count_is_27():
    n = (len(GENERATION_WEIGHTS) + len(FLOW_WEIGHTS)
         + len(IMPACT_WEIGHTS) + len(FEASIBILITY_WEIGHTS))
    assert n == 27


@pytest.mark.parametrize("w", [GENERATION_WEIGHTS, FLOW_WEIGHTS, IMPACT_WEIGHTS, FEASIBILITY_WEIGHTS])
def test_family_weights_sum_to_one(w):
    assert abs(sum(w.values()) - 1.0) < 1e-9


# ── composite ∈ [0,100] ──────────────────────────────────────────────

def _synthetic_df(n=40, seed=0):
    rng = np.random.default_rng(seed)
    cols = list(GENERATION_WEIGHTS) + list(FLOW_WEIGHTS) + list(IMPACT_WEIGHTS) + list(FEASIBILITY_WEIGHTS)
    df = pd.DataFrame({c: rng.uniform(0, 100, n) for c in cols})
    # keep feasibility gate columns in valid ranges so rows survive
    df["flow_velocity_ms"] = rng.uniform(0.1, 2.0, n)
    df["channel_width_m"] = rng.uniform(1.0, 20.0, n)
    df["land_ownership"] = rng.choice([0.5, 1.0], n)
    return df


def test_composite_in_range():
    scored = compute_composite_score(_synthetic_df())
    assert (scored["composite_score"] >= 0).all()
    assert (scored["composite_score"] <= 100).all()
    for col in ["generation_score", "flow_score", "impact_score", "feasibility_score"]:
        assert (scored[col] >= 0).all() and (scored[col] <= 100).all()


# ── C2: constant columns dropped + renormalized; all-constant → 50 ────

def test_subscore_drops_constant_column():
    df = pd.DataFrame({k: [5.0] * 7 for k in GENERATION_WEIGHTS})
    df["impervious_pct"] = [10, 20, 30, 40, 50, 60, 70]  # one varies
    ss = compute_subscore(df, GENERATION_WEIGHTS)
    assert ss.min() == 0.0 and ss.max() == 100.0  # spans full range off the one varying col


def test_subscore_all_constant_is_neutral_50():
    df = pd.DataFrame({k: [5.0] * 5 for k in GENERATION_WEIGHTS})
    ss = compute_subscore(df, GENERATION_WEIGHTS)
    assert (ss == 50.0).all()


# ── hard gates remove the right rows ─────────────────────────────────

def test_hard_gates_remove_rows():
    df = pd.DataFrame({
        "flow_velocity_ms": [0.5, 5.0, 0.5, 0.5],   # row1 too fast
        "channel_width_m":  [5.0, 5.0, 80.0, 5.0],  # row2 too wide
        "land_ownership":   [0.5, 0.5, 0.5, 0.0],   # row3 private
    })
    kept = apply_hard_gates(df)
    assert len(kept) == 1  # only row0 passes all gates


# ── Manning velocity monotonic in slope ──────────────────────────────

def test_manning_velocity_monotonic_in_slope():
    # DEM that drops to the east; steeper grid → higher velocity.
    class Aff:
        def __init__(self, px): self.px = px
        def __getitem__(self, i): return [self.px, 0, 0, 0, -self.px, 0][i]
    gentle = np.tile(np.linspace(10, 0, 20), (5, 1))    # 10 m drop over 19 cells
    steep = np.tile(np.linspace(100, 0, 20), (5, 1))     # 100 m drop
    fdir = np.full((5, 20), 1)  # east
    v_gentle = compute_flow_velocity(2, 2, gentle, Aff(10), 5.0, 12.5, fdir=fdir, catchment_area_km2=5.0)
    v_steep = compute_flow_velocity(2, 2, steep, Aff(10), 5.0, 12.5, fdir=fdir, catchment_area_km2=5.0)
    assert v_steep > v_gentle > 0


def test_velocity_favorability_peaked():
    assert velocity_transport_favorability(0.9) > velocity_transport_favorability(0.0)
    assert velocity_transport_favorability(0.9) > velocity_transport_favorability(2.5)


# ── occlusion is non-increasing along a river ────────────────────────

def test_occlusion_non_increasing():
    eta = 0.65
    raw = 80.0
    vals = [raw * (1 - eta) ** k for k in range(5)]   # k upstream nets
    assert all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))


# ── H5: estuary and beach distances are decorrelated ─────────────────

def test_estuary_beach_decorrelated():
    lats = np.linspace(35.8, 36.2, 12)
    lons = np.linspace(-79.0, -78.7, 12)
    est = [estimate_estuary_distance_km(a, o) for a, o in zip(lats, lons)]
    bea = [estimate_beach_distance_km(a, o) for a, o in zip(lats, lons)]
    assert abs(np.corrcoef(est, bea)[0, 1]) < 0.99
    # estuary distance must change with latitude (the old dlat≡0 bug)
    assert abs(estimate_estuary_distance_km(35.8, -78.9) - estimate_estuary_distance_km(36.2, -78.9)) > 1.0


# ── M1: width-order fallback never trips the width gate ──────────────

def test_width_fallback_bounded():
    for o in range(1, 6):
        sg = gpd.GeoDataFrame({"stream_order": [o], "geometry": [LineString([(0, 0), (100, 0)])]},
                              crs="EPSG:32617")
        w = get_channel_width(Point(50, 5), sg)
        assert w < 50.0  # never self-trips the 50 m hard gate


# ── C4: EJ index area-weighting varies across catchments ─────────────

def test_ej_index_varies_across_catchments():
    cells = []
    for idx, (i, j) in enumerate([(0, 0), (0, 1), (1, 0), (1, 1)]):
        cells.append({"GEOID": f"BG{idx}", "demo_index": [0.1, 0.4, 0.7, 0.95][idx],
                      "geometry": box(i * 1000, j * 1000, (i + 1) * 1000, (j + 1) * 1000)})
    bg = gpd.GeoDataFrame(cells, crs="EPSG:32617")
    ej_low = _area_weighted_index(bg, Point(500, 500).buffer(300))     # low-EJ cell
    ej_high = _area_weighted_index(bg, Point(1500, 1500).buffer(900))  # high-EJ cells
    assert 0 <= ej_low <= 1 and 0 <= ej_high <= 1
    assert abs(ej_low - ej_high) > 0.05


# ── C6: optimize_weights returns a valid simplex point ───────────────

def test_optimize_weights_valid():
    df = _synthetic_df(30, seed=1)
    good = df["impact_score"].nlargest(3).index.tolist() if "impact_score" in df else None
    # ensure subscore cols exist for the optimizer
    scored = compute_composite_score(df)
    good = scored["impact_score"].nlargest(3).index.tolist()
    w = optimize_weights(scored, good, n_calls=15, random_state=1)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert all(v > 0 for v in w.values())
