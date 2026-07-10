"""Tests for scripts/validate_paper.py — the FIX_PLAN_2 rule-4 validation
harness. Everything here is OFFLINE: the trap ground truth comes from the
committed fixture (tests/fixtures/waterkeepers_trash_trout_locations.geojson,
frozen 2026-07-09 from the public Waterkeepers Carolina ArcGIS layer) and the
Dirichlet metric runs on the committed mock_data/candidates.geojson."""
import os
import sys

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Point

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts import validate_paper as vp


# ── frozen ground-truth set ──────────────────────────────────────────────

def test_frozen_trap_set_is_exactly_the_27_paper_sites():
    traps = vp.load_traps()
    assert len(traps) == 27
    ids = {t["objectid"] for t in traps}
    # the two documented exclusions are absent
    assert 23 not in ids  # Fort Mill, SC — out of state
    assert 29 not in ids  # Raleigh - Walnut Creek — post-submission addition
    # spot-check the anchor sites the paper names (Blue Ridge / Piedmont / coast)
    names = {t["id"] for t in traps}
    assert "Durham - Third Fork Creek" in names
    assert "Boone - Winkler Creek" in names
    assert "Wilmington - Burnt Mill Creek" in names


def test_trap_coordinates_are_in_carolina_bounds():
    for t in vp.load_traps():
        assert -83.5 < t["lon"] < -75.0, t["id"]
        assert 33.5 < t["lat"] < 36.9, t["id"]


# ── Dirichlet stability (paper section 3.3) ──────────────────────────────

def test_dirichlet_reproduces_the_paper_figure_from_the_paper_tag(tmp_path):
    """The submitted 94.7% must stay reproducible from the submission-era
    Durham file, which lives byte-identical at git tag paper-2026 since the
    fix-pass-2 regen replaced the working-tree flagship. Skips on checkouts
    without the tag. Fewer draws than the paper run for speed; the figure is
    stable well past the first decimal at n=2000."""
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run(["git", "show", "paper-2026:mock_data/candidates.geojson"],
                       cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("paper-2026 tag not available in this checkout")
    paper_file = tmp_path / "paper_candidates.geojson"
    paper_file.write_text(r.stdout)
    res = vp.dirichlet_stability(geojson_path=str(paper_file), n=2000,
                                 seed=vp.DIRICHLET_SEED)
    assert res["n_candidates"] == 147
    assert abs(res["mean_pct"] - 94.7) < 1.0
    assert res["n_above_90"] >= 8


def test_dirichlet_on_the_shipped_flagship_stays_stable():
    """The re-scored flagship (24/27 live) must remain at least as rank-stable
    as the paper baseline demanded — the fix-pass-2 receipt measured 96.5%."""
    r = vp.dirichlet_stability(n=2000, seed=vp.DIRICHLET_SEED)
    assert r["n_candidates"] == 147
    assert r["mean_pct"] >= 90.0


def test_dirichlet_is_deterministic_for_a_seed():
    a = vp.dirichlet_stability(n=500, seed=7)
    b = vp.dirichlet_stability(n=500, seed=7)
    assert a == b


# ── region config selection ──────────────────────────────────────────────

def test_region_for_trap_prefers_smallest_containing_bbox():
    regions = [
        {"slug": "big", "bbox": [-80.0, 35.0, -78.0, 37.0], "center": [-79.0, 36.0]},
        {"slug": "small", "bbox": [-79.2, 35.8, -78.8, 36.2], "center": [-79.0, 36.0]},
        {"slug": "far", "bbox": [-77.0, 34.0, -76.5, 34.5], "center": [-76.75, 34.25]},
    ]
    trap = {"lon": -79.0, "lat": 36.0}
    assert vp.region_for_trap(trap, regions)["slug"] == "small"


def test_region_for_trap_falls_back_to_nearest_center():
    regions = [
        {"slug": "a", "bbox": [-80.0, 35.0, -79.5, 35.5], "center": [-79.75, 35.25]},
        {"slug": "b", "bbox": [-77.0, 34.0, -76.5, 34.5], "center": [-76.75, 34.25]},
    ]
    trap = {"lon": -79.4, "lat": 35.4}   # outside both bboxes, nearer to a
    assert vp.region_for_trap(trap, regions)["slug"] == "a"


# ── the frozen recovery criterion ────────────────────────────────────────

def _scored(points_scores):
    pts = [Point(x, y) for x, y, _ in points_scores]
    return gpd.GeoDataFrame(
        {"composite_score": [s for _, _, s in points_scores]},
        geometry=pts, crs="EPSG:32617")


def test_recovered_needs_high_score_inside_the_buffer():
    trap = Point(0, 0)
    hit = vp.evaluate_match(_scored([(50, 0, 85.0), (500, 0, 99.0)]), trap)
    assert hit["recovered"] and hit["nearest_m"] == 50.0
    assert hit["best_score_80m"] == 85.0


def test_low_score_inside_buffer_is_not_recovered():
    trap = Point(0, 0)
    miss = vp.evaluate_match(_scored([(50, 0, 40.0), (500, 0, 99.0)]), trap)
    assert not miss["recovered"]
    assert miss["any_within_80m"] is True
    assert miss["nearest_high_m"] == 500.0


def test_high_score_outside_buffer_is_not_recovered():
    trap = Point(0, 0)
    miss = vp.evaluate_match(_scored([(81, 0, 95.0)]), trap)
    assert not miss["recovered"]
    assert miss["any_within_80m"] is False


def test_buffer_boundary_is_inclusive():
    trap = Point(0, 0)
    edge = vp.evaluate_match(_scored([(80, 0, 70.0)]), trap)
    assert edge["recovered"]  # exactly 80 m, exactly score 70 → recovered
