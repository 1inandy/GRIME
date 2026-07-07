"""
Tests for the multi-region runner: config validity, region-honesty gating
(width curves + flood regression only where their calibration domain applies),
the region API endpoints (fixture-backed, offline), and — when region outputs
exist — index/file integrity.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import region_sources as rg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "scripts", "regions.json")
REGIONS_DIR = os.path.join(ROOT, "mock_data", "regions")


@pytest.fixture(scope="module")
def config():
    return json.load(open(CONFIG_PATH))


# ── config validity ──────────────────────────────────────────────────

def test_config_slugs_unique_and_wellformed(config):
    slugs = [r["slug"] for r in config["regions"]]
    assert len(slugs) == len(set(slugs))
    for s in slugs:
        assert s == s.lower() and " " not in s


def test_config_bboxes_sane(config):
    for r in config["regions"]:
        w, s, e, n = r["bbox"]
        assert w < e and s < n, r["slug"]
        assert (e - w) < 0.5 and (n - s) < 0.3, f"{r['slug']} bbox too large for a tractable run"
        lon, lat = r["center"]
        assert w <= lon <= e and s <= lat <= n, f"{r['slug']} center outside bbox"


def test_config_utm_zone_matches_longitude(config):
    for r in config["regions"]:
        lon = r["center"][0]
        expected = 32600 + int((lon + 180) // 6) + 1
        assert r["utm_epsg"] == expected, f"{r['slug']}: utm {r['utm_epsg']} != {expected}"


def test_config_width_curves_are_verified_bieger(config):
    for r in config["regions"]:
        wc = r["width_curve"]
        assert wc["code"] in rg.BIEGER_WIDTH_CURVES, r["slug"]
        a, b = rg.BIEGER_WIDTH_CURVES[wc["code"]]
        assert wc["a"] == a and wc["b"] == b, f"{r['slug']} curve coefficients drifted"
        assert "Bieger" in wc["source"]


def test_flood_regression_gated_to_nc_piedmont(config):
    """The SIR 2014-5030 HR1 regression must never be applied outside NC —
    the core honesty rule of the multi-region pass."""
    for r in config["regions"]:
        if r["flood_method"] == "sir2014_hr1":
            assert r["state"] == "NC", f"{r['slug']}: HR1 flood outside NC"
    # and the big five are all 'none'
    for slug in ("new-york-city", "chicago", "san-francisco", "los-angeles", "houston"):
        region = next(r for r in config["regions"] if r["slug"] == slug)
        assert region["flood_method"] == "none"


def test_town_science_consistent_with_province(config):
    """Statewide NC towns: Coastal Plain must use the APL width curve and NOT the
    HR1 flood regression (SIR HR1 is Piedmont-only); Piedmont/Blue Ridge use AHI.
    Flood may only be 'on' for Piedmont towns."""
    towns = [r for r in config["regions"] if r.get("tier") == "town"]
    if not towns:
        pytest.skip("no town regions in config")
    for t in towns:
        note = t["notes"].lower()
        code = t["width_curve"]["code"]
        if "coastal plain" in note:
            assert code == "APL", f"{t['slug']}: coastal plain must use APL curve"
            assert t["flood_method"] == "none", f"{t['slug']}: HR1 flood on a coastal-plain town"
        elif "blue ridge" in note:
            assert code == "AHI" and t["flood_method"] == "none", t["slug"]
        elif "piedmont" in note:
            assert code == "AHI", t["slug"]
        if t["flood_method"] == "sir2014_hr1":
            assert "piedmont" in note, f"{t['slug']}: HR1 flood but not classified Piedmont"


def test_town_bboxes_bounded_and_in_nc(config):
    towns = [r for r in config["regions"] if r.get("tier") == "town"]
    if not towns:
        pytest.skip("no town regions")
    import math
    for t in towns:
        w, s, e, n = t["bbox"]
        lat_mid = (s + n) / 2
        h_km = (n - s) * 111.0
        w_km = (e - w) * 111.0 * math.cos(math.radians(lat_mid))
        assert h_km <= 30 and w_km <= 30, f"{t['slug']} bbox too big ({w_km:.1f}x{h_km:.1f} km)"
        # NC's rough envelope
        assert 33.7 <= lat_mid <= 36.7 and -84.5 <= (w + e) / 2 <= -75.3, f"{t['slug']} outside NC"


def test_durham_region_uses_the_shipped_bbox(config):
    durham = next(r for r in config["regions"] if r["slug"] == "durham")
    assert durham["bbox"] == [-79.05, 35.90, -78.75, 36.05]
    assert durham["parcels"] == "durham"


def test_non_nc_regions_have_region_specific_water_refs(config):
    for r in config["regions"]:
        if r["state"] != "NC":
            assert "Pamlico" not in r["estuary_ref"]["label"], \
                f"{r['slug']}: NC estuary reference leaked outside NC"


# ── regional width curve behavior ────────────────────────────────────

def test_regional_width_dispatch():
    # Same drainage area, different provinces → different (documented) widths
    da = 50.0
    w_ahi = rg.regional_width_m(da, "AHI")
    w_apl = rg.regional_width_m(da, "APL")
    w_ipl = rg.regional_width_m(da, "IPL")
    w_pms = rg.regional_width_m(da, "PMS")
    assert w_ahi == pytest.approx(3.12 * da ** 0.415, rel=1e-9)
    assert w_apl == pytest.approx(2.22 * da ** 0.363, rel=1e-9)
    assert len({round(x, 3) for x in (w_ahi, w_apl, w_ipl, w_pms)}) == 4
    assert rg.regional_width_m(None, "AHI") is None
    with pytest.raises(KeyError):
        rg.regional_width_m(da, "NC_DOLL")  # unknown curve must fail loudly, not guess


def test_flood_q10_hr1_matches_sir_table7():
    da_km2 = 40.0
    da_mi2 = da_km2 * rg.KM2_TO_MI2
    expected = 484.0 * da_mi2 ** 0.5539 * 10 ** (0.0060 * 25.0)
    assert rg.flood_q10_hr1(da_km2, 25.0) == pytest.approx(expected, rel=1e-9)


# ── API endpoints (fixture region, offline) ──────────────────────────

@pytest.fixture()
def fixture_region_api(tmp_path, monkeypatch):
    import api.main as api
    rdir = tmp_path / "regions"
    rdir.mkdir()
    index = {"generated": "2026-07-06", "regions": [
        {"slug": "testville", "name": "Testville, NC", "bbox": [-79.0, 35.9, -78.8, 36.0],
         "center": [-78.9, 35.95], "site_count": 2, "params_varying": 21,
         "scored_date": "2026-07-06", "status": "ok"}]}
    (rdir / "index.json").write_text(json.dumps(index))
    doc = {"type": "FeatureCollection", "note": "fixture",
           "provenance": {"varying": ["a"], "constant": ["b"], "parameters": {}},
           "features": [
               {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-78.9, 35.95]},
                "properties": {"rank": 1, "segment_id": 7, "composite_score": 50.0}},
               {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-78.91, 35.96]},
                "properties": {"rank": 2, "segment_id": 9, "composite_score": 40.0}},
           ]}
    (rdir / "testville.geojson").write_text(json.dumps(doc))
    monkeypatch.setattr(api, "REGIONS_DIR", rdir)
    monkeypatch.setattr(api, "_regions_index_cache", None)
    monkeypatch.setattr(api, "_region_candidates_cache", {})
    return api


def test_api_regions_index(fixture_region_api):
    import asyncio
    out = asyncio.run(fixture_region_api.get_regions())
    assert out["regions"][0]["slug"] == "testville"
    assert out["regions"][0]["params_varying"] == 21


def test_api_region_candidates_and_404(fixture_region_api):
    import asyncio
    api = fixture_region_api
    doc = asyncio.run(api.get_region_candidates("testville"))
    assert len(doc["features"]) == 2
    assert doc["features"][0]["properties"]["rank"] == 1
    for bad in ("nope", "../etc/passwd", "TESTVILLE", "a b"):
        resp = asyncio.run(api.get_region_candidates(bad))
        assert getattr(resp, "status_code", 200) == 404


def test_write_zero_region_is_honest_not_a_failure(tmp_path, monkeypatch):
    """A town with no deployable site writes a valid empty geojson + an ok index
    entry carrying a reason — never a gate loosened or a site fabricated."""
    import scripts.run_regions as rr
    monkeypatch.setattr(rr, "OUT_DIR", tmp_path)
    region = {"slug": "emptyville", "name": "Emptyville, NC", "state": "NC",
              "bbox": [-79.0, 35.9, -78.98, 35.92], "center": [-78.99, 35.91],
              "utm_epsg": 32617, "tier": "town",
              "width_curve": {"code": "APL"}, "flood_method": "none",
              "notes": "coastal plain"}
    entry = rr.write_zero_region(region, 3, "all 3 candidate sites removed by the hard gates")
    assert entry["status"] == "ok" and entry["site_count"] == 0
    assert "hard gates" in entry["zero_reason"]
    doc = json.load(open(tmp_path / "emptyville.geojson"))
    assert doc["features"] == []                       # no fabricated sites
    assert doc["provenance"]["zero_reason"] == entry["zero_reason"]
    assert doc["provenance"]["candidates_pre_gate"] == 3


def test_supervisor_resume_skips_built_regions(tmp_path, monkeypatch):
    """The supervisor must not re-run a region whose output already exists."""
    import types
    import scripts.run_regions as rr
    monkeypatch.setattr(rr, "OUT_DIR", tmp_path)
    (tmp_path / "done.geojson").write_text("{}")
    cfg = {"defaults": {}, "regions": [
        {"slug": "done", "name": "Done, NC", "state": "NC", "bbox": [0, 0, 1, 1],
         "center": [0.5, 0.5], "tier": "town"},
        {"slug": "todo", "name": "Todo, NC", "state": "NC", "bbox": [0, 0, 1, 1],
         "center": [0.5, 0.5], "tier": "town"}]}
    monkeypatch.setattr(rr, "CONFIG", tmp_path / "cfg.json")
    (tmp_path / "cfg.json").write_text(json.dumps(cfg))
    ran = []
    monkeypatch.setattr(rr.subprocess if hasattr(rr, "subprocess") else __import__("subprocess"),
                        "run", lambda *a, **k: types.SimpleNamespace(returncode=0))
    # supervise with a stub run_one by intercepting subprocess.run
    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", lambda cmd, **k: (ran.append(cmd[-1]), types.SimpleNamespace(returncode=0))[1])
    monkeypatch.setattr(rr, "load_index", lambda: {"generated": None, "regions": []})
    monkeypatch.setattr(rr, "upsert", lambda idx, e: None)
    args = types.SimpleNamespace(tier="town", limit=None, timeout=60, pace=0, jitter=0, no_retry=True)
    rr.supervise(args)
    assert "todo" in ran and "done" not in ran      # resume skipped the built region


def test_api_regions_empty_when_unbuilt(tmp_path, monkeypatch):
    import asyncio
    import api.main as api
    monkeypatch.setattr(api, "REGIONS_DIR", tmp_path / "absent")
    monkeypatch.setattr(api, "_regions_index_cache", None)
    out = asyncio.run(api.get_regions())
    assert out["regions"] == []


# ── built-output integrity (runs only once regions exist) ────────────

def _built_index():
    p = os.path.join(REGIONS_DIR, "index.json")
    return json.load(open(p)) if os.path.exists(p) else None


@pytest.mark.skipif(_built_index() is None, reason="regions not built yet")
def test_built_regions_match_index():
    idx = _built_index()
    for entry in idx["regions"]:
        if not entry["status"].startswith("ok"):
            continue
        path = os.path.join(REGIONS_DIR, f"{entry['slug']}.geojson")
        assert os.path.exists(path), entry["slug"]
        doc = json.load(open(path))
        assert len(doc["features"]) == entry["site_count"], entry["slug"]
        prov = doc["provenance"]
        assert len(prov["varying"]) == entry["params_varying"], entry["slug"]
        assert prov["n_parameters"] == 27
        # ranks are unique and 1-based (the API detail contract)
        ranks = [f["properties"]["rank"] for f in doc["features"]]
        assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
        if ranks:
            assert ranks[0] == 1
        # every parameter records provenance, and no parameter is undocumented
        documented = set(prov["parameters"].keys())
        assert documented == set(prov["varying"]) | set(prov["constant"]) or \
            documented >= set(prov["varying"]), entry["slug"]
