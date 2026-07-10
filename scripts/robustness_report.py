#!/usr/bin/env python3
"""
GRIME robustness report (Phase 6 #3).

Runs the REAL Dirichlet Monte-Carlo sensitivity analysis at n=500 on the scored
candidates — the SHIPPED flagship dataset (mock_data/candidates.geojson) when it
is present, falling back to the deterministic 26-candidate synthetic set for
offline/fresh checkouts — and emits:
  - a rank-stability table (how often each top site stays in the top 5 under
    perturbed sub-score weights),
  - an actual histogram PNG (dashboard/docs/exports/robustness_hist.png),
  - the top sites as field-validation candidates (Phase 6 #4).

This replaces any synthetic/Beta-mock robustness figure with reproducible output
from the shipped code. Deterministic (seeded).

Run: python3 scripts/robustness_report.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use("Agg")


def _notna(v):
    try:
        return v == v and v is not None
    except Exception:
        return False
import matplotlib.pyplot as plt

from core.scoring import sensitivity_analysis
from scripts.score_candidates import build_synthetic_candidates, SEED

FLAGSHIP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "mock_data", "candidates.geojson")


def load_flagship():
    """The shipped 147-site dataset as a DataFrame (scoring is column-only), or
    None when the file is absent. Fix-pass-2: the docs' robustness figure now
    describes the SHIPPED ranking, not the offline synthetic demo (audit
    finding: 'the figure does not describe the shipped ranking')."""
    import json as _json
    import pandas as _pd
    if not os.path.exists(FLAGSHIP):
        return None
    with open(FLAGSHIP) as f:
        gj = _json.load(f)
    rows = [ft["properties"] for ft in gj.get("features", [])]
    if not rows:
        return None
    return _pd.DataFrame(rows)

OUT_PNG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "dashboard", "docs", "exports", "robustness_hist.png")
N = 500


def main():
    np.random.seed(SEED)  # reproducible Dirichlet draws
    gdf = load_flagship()
    source = "shipped flagship dataset (mock_data/candidates.geojson)"
    if gdf is None:
        gdf = build_synthetic_candidates(SEED)
        source = "offline synthetic demo set (flagship file absent)"
    print(f"Input: {source} — {len(gdf)} candidates")
    scored = sensitivity_analysis(gdf, n_perturbations=N).reset_index(drop=True)
    scored["rank"] = scored.index + 1

    rb = scored["robustness_pct"]
    print(f"Dirichlet sensitivity: n={N} perturbations over the 4 sub-score weights "
          f"(α ∝ [0.30,0.25,0.30,0.15]·10), {len(scored)} candidates.\n")
    print("Rank-stability of the top sites (kept in top 5 across perturbations):")
    print(f"  {'rank':>4}  {'stream':<18} {'composite':>9} {'robustness%':>11}")
    for _, r in scored.head(8).iterrows():
        label = r.get("stream_name") or (
            f"segment {r['segment_id']}" if "segment_id" in r and _notna(r.get("segment_id"))
            else f"site {int(r['rank'])}")
        print(f"  {int(r['rank']):>4}  {str(label):<18} "
              f"{r['composite_score']:>9.2f} {r['robustness_pct']:>10.1f}%")

    stable = int((rb >= 80).sum())
    print(f"\n  {stable} site(s) are highly robust (≥80% top-5 retention); "
          f"median robustness {rb.median():.1f}%.")

    # Real histogram
    os.makedirs(os.path.dirname(OUT_PNG), exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 4), dpi=130)
    ax.hist(rb, bins=np.arange(0, 105, 10), color="#58B09C", edgecolor="#1E1B12")
    ax.set_xlabel("Top-5 retention under weight perturbation (%)")
    ax.set_ylabel("Number of candidate sites")
    ax.set_title(f"GRIME rank stability — Dirichlet sensitivity (n={N})")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT_PNG)
    print(f"\nWrote histogram → {os.path.relpath(OUT_PNG)}")


if __name__ == "__main__":
    main()
