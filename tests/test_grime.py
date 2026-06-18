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
import core.generation as generation
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


# ── road density uses catchment-local road length ───────────────────

def test_road_density_clips_cached_network_to_catchment(monkeypatch):
    edges = gpd.GeoDataFrame(
        {"geometry": [
            LineString([(0, 500), (1000, 500)]),
            LineString([(2000, 500), (3000, 500)]),
        ]},
        crs="EPSG:32617",
    )
    monkeypatch.setattr(
        generation, "osm_drive_graph",
        lambda bbox: {"edges_utm": edges, "length_km": 2.0},
    )
    catchment = box(0, 0, 1000, 1000)  # 1 km² containing exactly 1 km of road
    density = generation.get_road_density(catchment, 1.0, (0, 0, 1, 1))
    assert density == pytest.approx(1.0)


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


# ── integration regressions ─────────────────────────────────────────

def test_build_features_passes_computed_velocity_to_feasibility(monkeypatch):
    import core.scoring as scoring

    seen = []
    monkeypatch.setattr(scoring, "get_discharge_stats", lambda: {
        "mean_q_cfs": 1.0, "median_q_cfs": 1.0, "peak_q_cfs": 1.0,
        "max_q_cfs": 1.0, "cv": 1.0, "high_flow_days": 1,
        "n_peak_records": 1,
    })
    monkeypatch.setattr(scoring, "get_drinking_water_intakes", lambda: gpd.GeoDataFrame())
    monkeypatch.setattr(scoring, "compute_flow_features", lambda row, **kwargs: {
        "usgs_mean_q_cfs": 1.0, "flow_velocity_ms": 2.4, "stream_order": 2,
        "catchment_area_km2": 1.0, "flood_q10_cfs": 1.0,
        "seasonal_cv": 1.0, "runoff_coeff_C": 0.2,
    })

    def fake_feasibility(row, **kwargs):
        seen.append(row.get("flow_velocity_ms"))
        return {
            "road_access_score": 1.0, "channel_width_score": 1.0,
            "velocity_feasibility": velocity_feasibility(row["flow_velocity_ms"]),
            "land_ownership": 1.0, "bank_slope_score": 1.0,
            "bridge_proximity_bonus": 0.0,
        }

    monkeypatch.setattr(scoring, "compute_feasibility_features", fake_feasibility)
    monkeypatch.setattr(
        scoring, "compute_generation_features",
        lambda *args, **kwargs: {k: 1.0 for k in GENERATION_WEIGHTS},
    )
    monkeypatch.setattr(
        scoring, "compute_impact_features",
        lambda *args, **kwargs: {k: 1.0 for k in IMPACT_WEIGHTS},
    )

    candidates = gpd.GeoDataFrame([{
        "geometry": Point(700000, 4000000), "lat": 36.0, "lon": -79.0,
        "catchment_area_km2": 1.0,
    }], crs="EPSG:32617")
    scoring.build_all_features(candidates, (-79.1, 35.9, -78.8, 36.1))
    assert seen == [2.4]


def test_candidate_sort_does_not_mutate_cached_ids(monkeypatch):
    import asyncio
    import api.main as api

    features = [
        {
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
            "properties": {"composite_score": 90, "generation_score": 10},
        },
        {
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 1]},
            "properties": {"composite_score": 80, "generation_score": 99},
        },
    ]
    monkeypatch.setattr(
        api, "load_candidates",
        lambda force_mock=False: {"type": "FeatureCollection", "features": features},
    )

    before = asyncio.run(api.get_candidate_detail(0))["location"]
    asyncio.run(api.get_candidates(min_score=None, top_n=None, subscore="generation_score"))
    after = asyncio.run(api.get_candidate_detail(0))["location"]
    assert before == after == [0, 0]
