#!/usr/bin/env python3
"""
GRIME model drift guard (Phase 5).

`model.json` is the single source of truth for the scoring model. This script
asserts that the Python constants and curve functions match it, failing loudly on
any drift — so docs↔code contradictions (M2/M3) can't silently creep back in.
Run it in CI / the verify step.

Run: python3 scripts/check_model.py   (exit 0 = match, 1 = drift)
"""
import inspect
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, Point

from core.scoring import SUB_SCORE_WEIGHTS, apply_hard_gates
from core.generation import GENERATION_WEIGHTS
from core.flow import (
    FLOW_WEIGHTS, estimate_runoff_coefficient, velocity_feasibility,
    velocity_transport_favorability,
)
from core.impact import IMPACT_WEIGHTS as IMPACT_W
from core.feasibility import FEASIBILITY_WEIGHTS, channel_width_score, get_channel_width
from core.pipeline import generate_candidates

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(_ROOT, "model.json")
EXPLORE_HTML = os.path.join(_ROOT, "dashboard", "explore", "index.html")


def approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


def main():
    m = json.load(open(MODEL))
    errors = []

    # 1) parameter weights, per family
    code_fams = {
        "generation": GENERATION_WEIGHTS,
        "flow": FLOW_WEIGHTS,
        "impact": IMPACT_W,
        "feasibility": FEASIBILITY_WEIGHTS,
    }
    n = 0
    for fam, code_w in code_fams.items():
        n += len(code_w)
        model_w = m["parameter_weights"][fam]
        if set(code_w) != set(model_w):
            errors.append(f"{fam}: param names differ {set(code_w) ^ set(model_w)}")
        for k in code_w:
            if k in model_w and not approx(float(code_w[k]), float(model_w[k])):
                errors.append(f"{fam}.{k}: code {code_w[k]} != model {model_w[k]}")

    # 2) parameter count
    if n != m["n_parameters"]:
        errors.append(f"n_parameters: code {n} != model {m['n_parameters']}")
    if n != 27:
        errors.append(f"expected 27 parameters, code has {n}")

    # 3) sub-score weights
    sw = m["subscore_weights"]
    code_sw = {"generation": SUB_SCORE_WEIGHTS["generation_score"],
               "flow": SUB_SCORE_WEIGHTS["flow_score"],
               "impact": SUB_SCORE_WEIGHTS["impact_score"],
               "feasibility": SUB_SCORE_WEIGHTS["feasibility_score"]}
    for k, v in code_sw.items():
        if not approx(v, sw[k]):
            errors.append(f"subscore {k}: code {v} != model {sw[k]}")

    # 4) runoff formula
    rc = m["runoff_coefficient"]
    if not approx(estimate_runoff_coefficient(0.0), rc["intercept"], 1e-6):
        errors.append("runoff intercept mismatch")
    slope_code = (estimate_runoff_coefficient(100.0) - estimate_runoff_coefficient(0.0)) / 100.0
    # (clamped at 0.95, so derive slope on a non-clamped point)
    slope_code = (estimate_runoff_coefficient(10.0) - estimate_runoff_coefficient(0.0)) / 10.0
    if not approx(slope_code, rc["slope"], 1e-6):
        errors.append(f"runoff slope: code {slope_code} != model {rc['slope']}")

    # 5) velocity_feasibility + channel_width_score curves at boundary points
    vf_checks = [(0.0, 0.3), (0.2, 0.7), (1.0, 1.0), (2.0, 0.5), (3.0, 0.1)]
    for v, exp in vf_checks:
        if not approx(velocity_feasibility(v), exp):
            errors.append(f"velocity_feasibility({v})={velocity_feasibility(v)} != {exp}")
    cw_checks = [(0.3, 0.0), (1.0, 0.5), (10.0, 1.0), (25.0, 0.5), (40.0, 0.2), (60.0, 0.0)]
    for w, exp in cw_checks:
        if not approx(channel_width_score(w), exp):
            errors.append(f"channel_width_score({w})={channel_width_score(w)} != {exp}")

    # 6) velocity transport-favorability Gaussian (Flow-side, M5) — previously
    # undocumented in model.json and unguarded, so its constants could drift
    # silently while the checker stayed green.
    vt = m["curves"]["velocity_transport_favorability"]
    center, width = float(vt["center"]), float(vt["width"])
    for v in (0.0, 0.3, center, 1.5, 2.5):
        expected = math.exp(-(((v - center) / width) ** 2))
        got = velocity_transport_favorability(v)
        if not approx(got, expected, 1e-9):
            errors.append(f"velocity_transport_favorability({v})={got} != {expected} "
                          f"(model center={center}, width={width})")

    # 7) hard gates — probe the gate actually used (apply_hard_gates) against the
    # model.json thresholds: velocity strictly < max, width inclusive [min, max],
    # ownership strictly > min_exclusive.
    hg = m["hard_gates"]
    vmax = float(hg["flow_velocity_ms_max"])
    wmin, wmax = float(hg["channel_width_m_min"]), float(hg["channel_width_m_max"])
    own_min = float(hg["land_ownership_min_exclusive"])
    probe = pd.DataFrame({
        #                      keep   drop   keep  drop        keep  drop        keep       drop
        "flow_velocity_ms":  [vmax - 1e-6, vmax, 1.0, 1.0,      1.0, 1.0,        1.0,       1.0],
        "channel_width_m":   [5.0,   5.0,  wmin, wmin - 1e-6,  wmax, wmax + 1e-6, 5.0,      5.0],
        "land_ownership":    [0.5,   0.5,  0.5,  0.5,          0.5,  0.5,        own_min + 1e-6, own_min],
    })
    kept = set(apply_hard_gates(probe).index)
    if kept != {0, 2, 4, 6}:
        errors.append(f"hard gates: expected to keep rows {{0,2,4,6}} of the probe, kept {sorted(kept)} "
                      f"(velocity<{vmax} strict, width [{wmin},{wmax}] inclusive, ownership>{own_min})")

    # 8) spacing — pipeline default and the explorer's JS constants
    spacing = m["spacing_m"]
    code_spacing = inspect.signature(generate_candidates).parameters["spacing_m"].default
    if code_spacing != spacing["pipeline_along_stream"]:
        errors.append(f"spacing pipeline_along_stream: code default {code_spacing} != "
                      f"model {spacing['pipeline_along_stream']}")
    try:
        js = open(EXPLORE_HTML).read()
        js_checks = [
            (r"SAME_STREAM_SPACE\s*=\s*(\d+)", spacing["explorer_same_stream"], "explorer_same_stream"),
            (r"CROSS_STREAM_SPACE\s*=\s*(\d+)", spacing["explorer_cross_stream"], "explorer_cross_stream"),
            (r"CATCH_EFFICIENCY\s*=\s*([\d.]+)", m["occlusion"]["catch_efficiency_eta"], "occlusion eta"),
        ]
        for pattern, model_val, label in js_checks:
            match = re.search(pattern, js)
            if not match:
                errors.append(f"{label}: constant not found in dashboard/explore/index.html")
            elif not approx(float(match.group(1)), float(model_val), 1e-9):
                errors.append(f"{label}: explorer JS {match.group(1)} != model {model_val}")
    except OSError as e:
        errors.append(f"explorer HTML unreadable for spacing/occlusion check: {e}")

    # 9) width-order fallback — probe get_channel_width on a stream lacking any
    # width attribute and compare against the model.json formula.
    formula = m["width_order_fallback"]["formula"]
    for order in (1, 2, 3, 4, 5):
        sg = gpd.GeoDataFrame(
            {"stream_order": [order], "geometry": [LineString([(0, 0), (100, 0)])]},
            crs="EPSG:32617",
        )
        got = get_channel_width(Point(50, 5), sg)
        expected = eval(formula, {"__builtins__": {}}, {"min": min, "order": order})
        if not approx(got, expected, 1e-9):
            errors.append(f"width_order_fallback(order={order}): code {got} != formula {expected}")

    # 10) impact proximity curves (live since fix-pass-2 Phase 1) — functional
    # probes: evaluate each scoring function on synthetic geometry and compare
    # against the model.json formula/constants.
    pc = m["proximity_curves"]
    from core import inverse_distance_score
    from core.impact import (PROTECTION_WEIGHTS, protected_area_score_from_gdf,
                             water_intake_score)
    from shapely.geometry import Polygon

    # superfund / cso share the inverse-distance curve
    for curve_name in ("superfund_score", "cso_density"):
        half = float(pc[curve_name]["half_decay_m"])
        for d in (0.0, half, 3 * half):
            src = gpd.GeoDataFrame(geometry=[Point(d, 0.0)], crs="EPSG:32617")
            got = inverse_distance_score(Point(0.0, 0.0), src, half)
            expected = 1.0 / (1.0 + (d / half) ** 2)
            if not approx(got, expected, 1e-9):
                errors.append(f"{curve_name} inverse-distance({d} m)={got} != {expected}")

    # protected areas: designation table + decay
    pa = pc["protected_area_score"]
    if {k: float(v) for k, v in pa["designation_weights"].items()} != PROTECTION_WEIGHTS:
        errors.append("protected_area designation_weights: model != core.impact.PROTECTION_WEIGHTS")
    sig = inspect.signature(protected_area_score_from_gdf)
    if not approx(float(sig.parameters["distance_km"].default), float(pa["buffer_km"])):
        errors.append(f"protected_area buffer_km: code default "
                      f"{sig.parameters['distance_km'].default} != model {pa['buffer_km']}")
    sq = Polygon([(9000, -500), (10000, -500), (10000, 500), (9000, 500)])  # centroid 9.5 km east
    for desig, w in [("State Park", 0.7), ("Something Unlisted", float(pa["default_designation_weight"]))]:
        padus = gpd.GeoDataFrame({"designation": [desig], "geometry": [sq]}, crs="EPSG:32617")
        got = protected_area_score_from_gdf(Point(0.0, 0.0), padus)
        expected = w * (1.0 / (1.0 + 9.5 / float(pa["decay_km"])))
        if not approx(got, expected, 1e-9):
            errors.append(f"protected_area_score({desig})={got} != {expected}")

    # water intakes: exp decay + 50 km cutoff
    wi = pc["water_intake_score"]
    sig = inspect.signature(water_intake_score)
    if not approx(float(sig.parameters["max_dist_km"].default), float(wi["max_dist_km"])):
        errors.append(f"water_intake max_dist_km: code default "
                      f"{sig.parameters['max_dist_km'].default} != model {wi['max_dist_km']}")
    for d_km, in_range in ((5.0, True), (30.0, True), (55.0, False)):
        gdfi = gpd.GeoDataFrame(geometry=[Point(d_km * 1000.0, 0.0)], crs="EPSG:32617")
        got = water_intake_score(Point(0.0, 0.0), gdfi)
        expected = math.exp(-d_km / float(wi["decay_km"])) if in_range else 0.0
        if not approx(got, expected, 1e-9):
            errors.append(f"water_intake_score(d={d_km} km)={got} != {expected}")

    if errors:
        print("MODEL DRIFT DETECTED:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print(f"model.json matches code: {n} parameters; validated param+subscore weights, "
          "runoff formula, velocity feasibility/transport curves, channel-width curve, "
          "hard gates, spacing + occlusion constants (pipeline & explorer), the "
          "width-order fallback, and the impact proximity curves "
          "(superfund/cso inverse-distance, PAD-US designation weights, intake decay).")
    sys.exit(0)


if __name__ == "__main__":
    main()
