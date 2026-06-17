#!/usr/bin/env python3
"""
GRIME — reproducible end-to-end scoring of candidate sites (H2).

The audit found that no committed entry point ran `build_all_features` /
`compute_composite_score`, so the shipped `candidates.geojson` could not be
reproduced from the code (and would have shown the 0.01 km² catchment bug). This
script closes that gap.

Two modes:

  --live   Run the REAL pipeline: run_pipeline (DEM→UTM→pysheds→streams→
           candidates) → build_all_features → compute_composite_score. Requires
           py3dep + pysheds + network (+ a CENSUS_API_KEY for live ACS/EJ). This is
           the path a judge runs on a full environment; it produces field values.

  (default / --offline)
           Deterministic, network-free regeneration. The *scoring* is the real
           shipped code — hard gates, MinMax with the C2 constant-column drop +
           renormalize, the M5 velocity favorability curve, the two-level composite,
           and the Dirichlet sensitivity analysis. Only the raw per-candidate
           PARAMETERS are deterministic **synthetic estimates** (seeded; clearly
           labeled in the output's `note`), because the live DEM/federal APIs are
           not available offline. This makes the shipped file byte-reproducible
           while honestly not claiming to be live field data.

Run:  python3 scripts/score_candidates.py            # offline, reproducible
      python3 scripts/score_candidates.py --live      # full real pipeline
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
from shapely.geometry import Point
from dotenv import load_dotenv

# Allow `python3 scripts/score_candidates.py` from anywhere (repo root on path).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Read CENSUS_API_KEY (and friends) from the gitignored .env at the repo root,
# pinned to the repo path so it loads regardless of the current directory.
load_dotenv(os.path.join(_REPO_ROOT, ".env"))
sys.path.insert(0, _REPO_ROOT)

from core import UTM_CRS, WGS84
from core.flow import estimate_runoff_coefficient
from core.feasibility import (
    road_access_score, channel_width_score, velocity_feasibility, bank_slope_score,
)
from core.impact import estimate_estuary_distance_km, estimate_beach_distance_km
from core.scoring import (
    compute_composite_score, sensitivity_analysis, summarize_provenance,
)

SEED = 42
OUT = Path("mock_data/candidates.geojson")

# Synthetic Ellerbe-Creek-area tributaries (name, urbanization 0-1, polyline of
# (lat, lon) from upstream→downstream). Deterministic geometry near Durham, NC.
TRIBUTARIES = [
    ("South Ellerbe", 0.85, [(36.012, -78.905), (36.018, -78.895), (36.028, -78.882),
                             (36.040, -78.872), (36.052, -78.860)]),
    ("Goose Creek", 0.55, [(36.005, -78.945), (36.015, -78.935), (36.027, -78.922),
                           (36.038, -78.910)]),
    ("Warren Creek", 0.40, [(35.985, -78.960), (35.996, -78.948), (36.008, -78.936)]),
    ("Sandy Creek", 0.65, [(35.970, -78.920), (35.980, -78.908), (35.992, -78.896),
                           (36.004, -78.884)]),
    ("North Ellerbe", 0.75, [(36.045, -78.905), (36.052, -78.893), (36.060, -78.880)]),
]


def _haversine_km(a1, o1, a2, o2):
    R = 6371.0088
    dA, dO = math.radians(a2 - a1), math.radians(o2 - o1)
    h = math.sin(dA / 2) ** 2 + math.cos(math.radians(a1)) * math.cos(math.radians(a2)) * math.sin(dO / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(h)))


def build_synthetic_candidates(seed=SEED):
    """Deterministic candidate set with synthetic-but-coherent raw parameters.

    Raw values vary per candidate (so the real C2 normalization has signal); the
    scoring itself is done by the shipped pipeline, not here.
    """
    rng = np.random.default_rng(seed)
    rows = []
    cid = 0
    for name, urban, line in TRIBUTARIES:
        # candidate every ~300 m along the polyline, downstream fraction 0→1
        n = max(3, len(line) + rng.integers(1, 3))
        order = 2 + int(round(urban * 3))           # 2..5 stream order
        for i in range(n):
            uf = (i + 0.5) / n                       # 0=headwater, 1=mouth
            # interpolate position along the polyline
            t = uf * (len(line) - 1)
            k = min(int(t), len(line) - 2)
            f = t - k
            lat = line[k][0] + f * (line[k + 1][0] - line[k][0])
            lon = line[k][1] + f * (line[k + 1][1] - line[k][1])
            jit = lambda s: float(rng.normal(0, s))

            catchment = round(0.5 + uf * (8 + 55 * urban), 2)        # real km², grows downstream
            imperv = float(np.clip(15 + 55 * urban + uf * 12 + jit(4), 3, 95))
            pop = float(max(120, (300 + 1800 * urban) * (0.7 + uf) + jit(150)))
            velocity = float(np.clip(0.25 + uf * 1.4 + jit(0.15), 0.05, 2.8))
            width = float(np.clip(2.0 + order * 2.5 + uf * 4 + jit(1.5), 0.6, 45))
            road_m = float(np.clip(120 + jit(220) + (1 - urban) * 400, 20, 2200))
            slope_deg = float(np.clip(6 + (1 - uf) * 14 + jit(4), 1, 48))

            rows.append({
                "id": cid, "stream_name": name, "geometry": Point(lon, lat),
                "lat": lat, "lon": lon, "stream_order": order,
                # generation (raw)
                "population_density": round(pop, 1),
                "impervious_pct": round(imperv, 1),
                "road_density_km_km2": round(2.0 + urban * 8 + jit(0.8), 2),
                "tri_facility_density": round(max(0, urban * 1.2 + jit(0.3)), 3),
                "npdes_points": int(max(0, round(urban * 4 + jit(1)))),
                "cso_density": round(max(0, urban * 0.8 + jit(0.2)), 3),
                "litter_complaint_density": round(max(0, 1 + urban * 6 + uf * 3 + jit(1)), 2),
                # flow (raw)
                "usgs_mean_q_cfs": round(2 + catchment * 0.9 + jit(1.5), 2),
                "flow_velocity_ms": round(velocity, 3),
                "catchment_area_km2": catchment,
                "flood_q10_cfs": round(80 + catchment * 25 + jit(20), 1),
                "seasonal_cv": round(float(np.clip(1.4 + jit(0.4), 0.4, 3.5)), 3),
                "runoff_coeff_C": round(estimate_runoff_coefficient(imperv), 3),
                # impact (raw) — estuary/beach are the REAL decorrelated haversines
                "water_intake_score": round(max(0, (1 - uf) * 1.5 + jit(0.3)), 3),
                "protected_area_score": round(max(0, 0.2 + (1 - urban) * 0.8 + jit(0.15)), 3),
                "ej_index": round(float(np.clip(0.3 + urban * 0.5 + jit(0.08), 0, 1)), 3),
                "estuary_dist_km": round(estimate_estuary_distance_km(lat, lon), 2),
                "beach_dist_km": round(estimate_beach_distance_km(lat, lon), 2),
                "tourism_amenity_density": round(max(0, urban * 3 + jit(0.6)), 3),
                "superfund_score": round(max(0, urban * 0.6 + jit(0.2)), 3),
                # feasibility (raw → real scoring functions)
                "road_access_m": round(road_m, 1),
                "road_access_score": road_access_score(road_m),
                "channel_width_m": round(width, 1),
                "channel_width_score": channel_width_score(width),
                "velocity_feasibility": velocity_feasibility(velocity),
                "land_ownership": float(rng.choice([0.5, 1.0], p=[0.7, 0.3])),
                "bank_slope_deg": round(slope_deg, 1),
                "bank_slope_score": bank_slope_score(slope_deg),
                "bridge_proximity_bonus": float(rng.choice([0.0, 0.2], p=[0.75, 0.25])),
            })
            cid += 1
    gdf = gpd.GeoDataFrame(rows, crs=WGS84)
    return gdf


def run_offline():
    np.random.seed(SEED)  # make the Dirichlet sensitivity analysis reproducible
    gdf = build_synthetic_candidates(SEED)
    print(f"Built {len(gdf)} synthetic candidates (seed={SEED}).")
    summarize_provenance(gdf)
    scored = sensitivity_analysis(gdf, n_perturbations=500)  # adds robustness_pct
    scored = scored.reset_index(drop=True)
    scored["rank"] = scored.index + 1
    return scored


def run_live(bbox=None):
    from core import ELLERBE_BBOX
    from core.pipeline import run_pipeline, process_hydrology, fetch_dem
    from core.scoring import build_all_features
    bbox = bbox or ELLERBE_BBOX
    # Fetch + condition the DEM ONCE, then thread that hydrology into run_pipeline
    # so it does not fetch/condition the DEM a second time (the old code called
    # fetch_dem + process_hydrology here AND again inside run_pipeline).
    dem = fetch_dem(bbox)
    hydrology = process_hydrology(dem, bbox)
    grid, fdir, acc, elevation, transform = hydrology
    # Stream/candidate density: a 10 m DEM at the default 500-cell threshold (0.05
    # km²) yields a ~2 M-segment network and tens of thousands of candidates — far
    # too many for per-candidate federal/OSM lookups. Use a hydrologically
    # meaningful channel threshold (≈2 km² drainage) and a 1.5 km along-stream
    # spacing → a tractable ~150-site real network across the Ellerbe/Eno streams.
    LIVE_THRESHOLD = 20000   # accumulation cells (×100 m² = 2.0 km² min drainage)
    LIVE_SPACING_M = 1500
    stream_gdf, candidates, _ = run_pipeline(
        bbox=bbox, hydrology=hydrology, return_hydrology=True,
        threshold=LIVE_THRESHOLD, spacing_m=LIVE_SPACING_M)
    # Candidates stay in UTM: build_all_features' per-candidate catchment discs and
    # all distance maths are metre-based, and lat/lon for API lookups are already
    # columns on the candidates (so we must NOT reproject the geometry to WGS84,
    # which would turn the metre buffers into ~degree-sized polygons and collapse
    # the per-candidate generation/EJ features to constant fallbacks).
    scored = build_all_features(
        candidates, bbox, stream_gdf=stream_gdf,
        dem_array=elevation, dem_transform=transform, fdir=fdir,
    )
    np.random.seed(SEED)  # reproducible Dirichlet draws for robustness_pct
    scored = sensitivity_analysis(scored, n_perturbations=200).reset_index(drop=True)
    scored["rank"] = scored.index + 1
    return scored


def write_geojson(scored, mode):
    scored = scored.to_crs(WGS84)
    feats = []
    for _, r in scored.iterrows():
        props = {k: (round(v, 4) if isinstance(v, float) else v)
                 for k, v in r.drop(labels="geometry").items()}
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point",
                                   "coordinates": [round(r.geometry.x, 6), round(r.geometry.y, 6)]},
                      "properties": props})
    if mode == "live":
        note = ("Scored by scripts/score_candidates.py (live pipeline). Real USGS 3DEP "
                "10 m DEM → UTM reprojection → pysheds D8 fill/flowdir/accumulation → "
                "stream network (≥2 km² drainage) → candidate sites; the 27 parameters "
                "come from live endpoints where available — Census ACS 5-yr (population "
                "density + EJSCREEN demographic-index reconstruction), USGS NWIS gauge, "
                "OSM drive network / leisure+tourism features, EPA TRI/FRS — with "
                "summarize_provenance reporting which vary per-candidate vs. fall back "
                "(dead endpoints: EPA ECHO, USGS StreamStats, EPA EJSCREEN, Durham 311; "
                "NLCD imperviousness not served by py3dep). Scoring is the real shipped "
                "code: hard gates, MinMax + C2 constant-column drop, M5 velocity curve, "
                "two-level composite, Dirichlet sensitivity.")
    else:
        note = ("Scored by scripts/score_candidates.py (deterministic offline mode, "
                "seed=42). In offline mode the scoring (hard gates, MinMax+constant-drop, "
                "M5 velocity curve, composite, Dirichlet sensitivity) is the real "
                "shipped code; raw parameters are deterministic synthetic estimates, "
                "not live gauge/Census measurements.")
    fc = {
        "type": "FeatureCollection",
        "note": note,
        "features": feats,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(fc, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"Wrote {len(feats)} scored candidates → {OUT}")
    top = scored.iloc[0]
    # Live candidates have no synthetic `stream_name`; fall back to a segment label.
    label = top.get("stream_name") or f"segment {int(top.get('segment_id', 0))}"
    print(f"  composite range: {scored['composite_score'].min():.2f}"
          f"–{scored['composite_score'].max():.2f}; "
          f"top: {label} = {top['composite_score']:.2f}")


def main():
    ap = argparse.ArgumentParser(description="Reproducibly score GRIME candidates")
    ap.add_argument("--live", action="store_true",
                    help="run the real DEM+API pipeline (needs pysheds/py3dep/network)")
    args = ap.parse_args()
    scored = run_live() if args.live else run_offline()
    write_geojson(scored, "live" if args.live else "offline")


if __name__ == "__main__":
    main()
