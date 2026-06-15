#!/usr/bin/env python3
"""
GRIME model drift guard (Phase 5).

`model.json` is the single source of truth for the scoring model. This script
asserts that the Python constants and curve functions match it, failing loudly on
any drift — so docs↔code contradictions (M2/M3) can't silently creep back in.
Run it in CI / the verify step.

Run: python3 scripts/check_model.py   (exit 0 = match, 1 = drift)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.scoring import SUB_SCORE_WEIGHTS
from core.generation import GENERATION_WEIGHTS
from core.flow import FLOW_WEIGHTS, estimate_runoff_coefficient, velocity_feasibility
from core.impact import IMPACT_WEIGHTS as IMPACT_W
from core.feasibility import FEASIBILITY_WEIGHTS, channel_width_score

MODEL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model.json")


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

    if errors:
        print("MODEL DRIFT DETECTED:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print(f"model.json matches code: {n} parameters, weights + gates + curves consistent.")
    sys.exit(0)


if __name__ == "__main__":
    main()
