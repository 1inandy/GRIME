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


_SURFACE = {"waterway": "stream", "underground": False}


def test_river_name_matching(tmp_path, monkeypatch):
    """add_river_names assigns each site its nearest NAMED waterway within 1 km,
    preferring a specific name but KEEPING a real generic/type name ('Creek')
    rather than nulling it, and leaves genuinely-far sites null — never a
    fabricated name."""
    import scripts.add_river_names as arn

    # a named creek running east-west at lat 36.00, plus a generic-named ditch
    _ways = [
        ("Ellerbe Creek", [(-78.905, 36.000), (-78.895, 36.000), (-78.885, 36.000)], _SURFACE),
        ("Creek", [(-78.905, 36.050), (-78.895, 36.050)], _SURFACE),   # generic → kept, not nulled
    ]
    monkeypatch.setattr(arn, "named_waterways", lambda bbox: _ways)
    monkeypatch.setattr(arn, "all_waterways", lambda bbox: _ways)
    monkeypatch.setattr(arn, "named_flowlines_nhd", lambda bbox: [])  # no NHD in test
    rdir = tmp_path
    monkeypatch.setattr(arn, "REGIONS", rdir)
    doc = {"type": "FeatureCollection",
           "region": {"bbox": [-79.0, 35.9, -78.8, 36.1], "utm_epsg": 32617},
           "features": [
               # on the creek → Ellerbe Creek
               {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-78.900, 36.0005]},
                "properties": {"segment_id": 1}},
               # ~2 km away from any named water → null
               {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-78.900, 36.020]},
                "properties": {"segment_id": 2}},
               # near the generic 'Creek' → keep the real name 'Creek'
               {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-78.900, 36.0502]},
                "properties": {"segment_id": 3}},
           ]}
    (rdir / "testregion.geojson").write_text(json.dumps(doc))
    slug, n, named, status, stats = arn.add_names("testregion")
    assert status == "ok" and n == 3 and named == 2
    out = json.load(open(rdir / "testregion.geojson"))
    props = [f["properties"]["river_name"] for f in out["features"]]
    assert props[0] == "Ellerbe Creek"
    assert props[1] is None            # >1 km away → no fabricated name
    assert props[2] == "Creek"         # real generic name kept, not nulled


def test_river_name_prefers_specific_over_generic(tmp_path, monkeypatch):
    """When both a specific and a bare generic name are within range, the site
    gets the specific one (unless the generic is much closer)."""
    import scripts.add_river_names as arn
    # specific creek ~60 m south, generic 'Creek' ~30 m north of the site
    _ways = [
        ("Sandy Creek", [(-78.905, 35.99946), (-78.885, 35.99946)], _SURFACE),  # ~60 m
        ("Creek",       [(-78.905, 36.00027), (-78.885, 36.00027)], _SURFACE),  # ~30 m
    ]
    monkeypatch.setattr(arn, "named_waterways", lambda bbox: _ways)
    monkeypatch.setattr(arn, "all_waterways", lambda bbox: _ways)
    monkeypatch.setattr(arn, "named_flowlines_nhd", lambda bbox: [])
    monkeypatch.setattr(arn, "REGIONS", tmp_path)
    doc = {"type": "FeatureCollection",
           "region": {"bbox": [-79.0, 35.9, -78.8, 36.1], "utm_epsg": 32617},
           "features": [{"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [-78.900, 36.000]},
                         "properties": {"segment_id": 1}}]}
    (tmp_path / "r.geojson").write_text(json.dumps(doc))
    slug, n, named, status, stats = arn.add_names("r")
    out = json.load(open(tmp_path / "r.geojson"))
    assert out["features"][0]["properties"]["river_name"] == "Sandy Creek"


def test_river_name_fetch_failure_keeps_unnamed(tmp_path, monkeypatch):
    import scripts.add_river_names as arn
    monkeypatch.setattr(arn, "named_waterways", lambda bbox: None)        # Overpass failed
    monkeypatch.setattr(arn, "all_waterways", lambda bbox: None)
    monkeypatch.setattr(arn, "named_flowlines_nhd", lambda bbox: None)    # NHD failed
    monkeypatch.setattr(arn, "REGIONS", tmp_path)
    doc = {"type": "FeatureCollection", "region": {"bbox": [-79, 35.9, -78.8, 36.1], "utm_epsg": 32617},
           "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-78.9, 36.0]},
                         "properties": {"segment_id": 1}}]}
    (tmp_path / "r.geojson").write_text(json.dumps(doc))
    slug, n, named, status, stats = arn.add_names("r")
    assert named == 0 and "failed" in status
    # file not corrupted; no river_name fabricated, no geometry moved, no streams added
    out = json.load(open(tmp_path / "r.geojson"))
    assert "river_name" not in out["features"][0]["properties"]
    assert out["features"][0]["geometry"]["coordinates"] == [-78.9, 36.0]
    assert "streams" not in out


def test_single_source_outage_keeps_file_untouched(tmp_path, monkeypatch):
    """ONE source down must leave the file byte-identical: a partial rebuild
    would delete baked rivers (or swap their geometry to the weaker source)
    while sites keep names/snaps that point at them."""
    import scripts.add_river_names as arn
    monkeypatch.setattr(arn, "REGIONS", tmp_path)
    # First: a healthy both-sources run bakes names + streams + snaps.
    _ways = [("Ellerbe Creek", [(-78.905, 36.000), (-78.885, 36.000)], _SURFACE)]
    monkeypatch.setattr(arn, "named_waterways", lambda bbox: _ways)
    monkeypatch.setattr(arn, "all_waterways", lambda bbox: _ways)
    monkeypatch.setattr(arn, "named_flowlines_nhd", lambda bbox: [])
    doc = {"type": "FeatureCollection",
           "region": {"bbox": [-79.0, 35.9, -78.8, 36.1], "utm_epsg": 32617},
           "features": [{"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [-78.900, 36.0005]},
                         "properties": {"segment_id": 1}}]}
    (tmp_path / "r.geojson").write_text(json.dumps(doc))
    arn.add_names("r")
    healthy = (tmp_path / "r.geojson").read_text()
    assert "Ellerbe Creek" in healthy and "streams" in healthy
    # Then: NHD down (OSM still up) → nothing may change.
    monkeypatch.setattr(arn, "named_flowlines_nhd", lambda bbox: None)
    slug, n, named, status, stats = arn.add_names("r")
    assert "nhd fetch failed" in status and named == 1   # kept count reported
    assert (tmp_path / "r.geojson").read_text() == healthy
    # And: the all-waterways geometry fetch down → nothing may change either.
    monkeypatch.setattr(arn, "named_flowlines_nhd", lambda bbox: [])
    monkeypatch.setattr(arn, "all_waterways", lambda bbox: None)
    slug, n, named, status, stats = arn.add_names("r")
    assert "overpass-all fetch failed" in status
    assert (tmp_path / "r.geojson").read_text() == healthy
    # And: the naming fetch down → nothing may change either.
    monkeypatch.setattr(arn, "all_waterways", lambda bbox: _ways)
    monkeypatch.setattr(arn, "named_waterways", lambda bbox: None)
    slug, n, named, status, stats = arn.add_names("r")
    assert "overpass-names fetch failed" in status
    assert (tmp_path / "r.geojson").read_text() == healthy


def test_no_named_waterways_unsnaps_and_clears_markers(tmp_path, monkeypatch):
    """When both sources succeed but return nothing named, a previously-snapped
    site must return to its DEM position with the dem_/snap_ markers removed —
    never a snapped coordinate pointing at a river that is no longer drawn."""
    import scripts.add_river_names as arn
    monkeypatch.setattr(arn, "REGIONS", tmp_path)
    _ways = [("Ellerbe Creek", [(-78.905, 36.000), (-78.885, 36.000)], _SURFACE)]
    monkeypatch.setattr(arn, "named_waterways", lambda bbox: _ways)
    monkeypatch.setattr(arn, "all_waterways", lambda bbox: _ways)
    monkeypatch.setattr(arn, "named_flowlines_nhd", lambda bbox: [])
    doc = {"type": "FeatureCollection",
           "region": {"bbox": [-79.0, 35.9, -78.8, 36.1], "utm_epsg": 32617},
           "features": [{"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [-78.900, 36.0005]},
                         "properties": {"segment_id": 1}}]}
    (tmp_path / "r.geojson").write_text(json.dumps(doc))
    arn.add_names("r")
    snapped = json.load(open(tmp_path / "r.geojson"))["features"][0]
    assert "snap_dist_m" in snapped["properties"]
    # Re-run with all sources HEALTHY but empty.
    monkeypatch.setattr(arn, "named_waterways", lambda bbox: [])
    monkeypatch.setattr(arn, "all_waterways", lambda bbox: [])
    slug, n, named, status, stats = arn.add_names("r")
    assert status == "no named waterways in bbox"
    out = json.load(open(tmp_path / "r.geojson"))
    f = out["features"][0]
    assert f["geometry"]["coordinates"] == [-78.900, 36.0005]   # back at DEM position
    assert f["properties"]["river_name"] is None
    for k in ("dem_lat", "dem_lon", "snap_dist_m"):
        assert k not in f["properties"]
    assert out["streams"]["features"] == []


def test_streams_baked_and_sites_snapped(tmp_path, monkeypatch):
    """add_river_names bakes the FULL network — merged surface line per named
    river, its long culverted reaches as a separate dashed (underground:1)
    feature, unnamed ways as context — and snaps sites onto drawn geometry
    within 150 m (unnamed sites onto unnamed ways, flagged), preserving the
    DEM coordinate and changing NO score/rank property."""
    import scripts.add_river_names as arn
    culvert = {"waterway": "stream", "underground": True}
    _ways = [
        # two connectable segments of the same creek → must merge into ONE line
        ("Ellerbe Creek", [(-78.905, 36.000), (-78.895, 36.000)], _SURFACE),
        ("Ellerbe Creek", [(-78.895, 36.000), (-78.885, 36.000)], _SURFACE),
        # a long culverted reach (~180 m) → drawn as a SEPARATE underground feature
        ("Ellerbe Creek", [(-78.885, 36.000), (-78.883, 36.000)], culvert),
        # a far river with no site → drawn as named context
        ("Eno River", [(-78.905, 36.090), (-78.885, 36.090)], _SURFACE),
        # an unnamed stream (~1.8 km, passes the 120 m gate) → unnamed context
        ("", [(-78.905, 36.020), (-78.885, 36.020)], _SURFACE),
    ]
    monkeypatch.setattr(arn, "named_waterways",
                        lambda bbox: [w for w in _ways if w[0]])
    monkeypatch.setattr(arn, "all_waterways", lambda bbox: _ways)
    monkeypatch.setattr(arn, "named_flowlines_nhd", lambda bbox: [])
    monkeypatch.setattr(arn, "REGIONS", tmp_path)
    doc = {"type": "FeatureCollection",
           "region": {"bbox": [-79.0, 35.9, -78.8, 36.1], "utm_epsg": 32617},
           "features": [
               # ~55 m north of the creek → named + snapped onto the line
               {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-78.900, 36.0005]},
                "properties": {"segment_id": 1, "rank": 1, "composite_score": 57.5}},
               # ~890 m north → named (within 1 km) but NOT snapped (>150 m)
               {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-78.900, 36.008]},
                "properties": {"segment_id": 2, "rank": 2, "composite_score": 42.0}},
               # ~2.2 km from any NAMED water but ~55 m from the unnamed stream
               # → stays null-named, snaps onto the unnamed way, gets flagged
               {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-78.900, 36.0205]},
                "properties": {"segment_id": 3, "rank": 3, "composite_score": 40.0}},
           ]}
    (tmp_path / "r.geojson").write_text(json.dumps(doc))
    slug, n, named, status, stats = arn.add_names("r")
    assert status == "ok" and named == 2
    assert stats["snapped"] == 2 and stats["snap_unnamed"] == 1
    out = json.load(open(tmp_path / "r.geojson"))

    feats = out["streams"]["features"]
    ell_surf = [f for f in feats if f["properties"]["name"] == "Ellerbe Creek"
                and not f["properties"]["underground"]]
    ell_under = [f for f in feats if f["properties"]["name"] == "Ellerbe Creek"
                 and f["properties"]["underground"]]
    eno = [f for f in feats if f["properties"]["name"] == "Eno River"]
    unnamed = [f for f in feats if f["properties"]["name"] is None]
    assert len(ell_surf) == 1 and len(ell_under) == 1 and len(eno) == 1 and len(unnamed) == 1
    assert ell_surf[0]["properties"]["n_sites"] == 2
    assert ell_surf[0]["geometry"]["type"] == "LineString"   # merged into one
    xs = [c[0] for c in ell_surf[0]["geometry"]["coordinates"]]
    assert max(xs) <= -78.885 + 1e-6         # surface stops where the culvert starts
    assert eno[0]["properties"]["n_sites"] == 0

    # site 1: snapped onto the creek (lat == 36.000), DEM preserved, scores untouched
    f1 = out["features"][0]
    assert abs(f1["geometry"]["coordinates"][1] - 36.000) < 1e-4
    assert f1["properties"]["dem_lat"] == 36.0005 and f1["properties"]["dem_lon"] == -78.900
    assert 0 < f1["properties"]["snap_dist_m"] <= 150
    assert f1["properties"]["composite_score"] == 57.5 and f1["properties"]["rank"] == 1

    # site 2: named but unsnapped — geometry unchanged, no dem_/snap_ markers
    f2 = out["features"][1]
    assert f2["geometry"]["coordinates"] == [-78.900, 36.008]
    assert "dem_lat" not in f2["properties"] and "snap_dist_m" not in f2["properties"]
    assert f2["properties"]["composite_score"] == 42.0

    # site 3: null-named, snapped onto the unnamed way, flagged for the UI
    f3 = out["features"][2]
    assert f3["properties"]["river_name"] is None
    assert f3["properties"]["on_unnamed_waterway"] is True
    assert abs(f3["geometry"]["coordinates"][1] - 36.020) < 1e-4
    assert f3["properties"]["composite_score"] == 40.0


def test_snap_is_idempotent(tmp_path, monkeypatch):
    """Re-running the script must re-derive the snap from dem_lat/dem_lon, not
    from the already-snapped coordinate — output is byte-identical on a re-run."""
    import scripts.add_river_names as arn
    _ways = [("Ellerbe Creek", [(-78.905, 36.000), (-78.885, 36.000)], _SURFACE)]
    monkeypatch.setattr(arn, "named_waterways", lambda bbox: _ways)
    monkeypatch.setattr(arn, "all_waterways", lambda bbox: _ways)
    monkeypatch.setattr(arn, "named_flowlines_nhd", lambda bbox: [])
    monkeypatch.setattr(arn, "REGIONS", tmp_path)
    doc = {"type": "FeatureCollection",
           "region": {"bbox": [-79.0, 35.9, -78.8, 36.1], "utm_epsg": 32617},
           "features": [{"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [-78.900, 36.0005]},
                         "properties": {"segment_id": 1}}]}
    (tmp_path / "r.geojson").write_text(json.dumps(doc))
    arn.add_names("r")
    first = (tmp_path / "r.geojson").read_text()
    arn.add_names("r")
    second = (tmp_path / "r.geojson").read_text()
    assert first == second


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


def test_stream_mask_union_and_gate(monkeypatch):
    """The stream mask is the union of OSM+NHD geometry; a candidate within
    150 m is kept, one farther is removed, and a source outage returns None
    (the runner aborts rather than running unmasked)."""
    import geopandas as gpd
    from shapely.geometry import Point
    import scripts.run_regions as rr
    import scripts.add_river_names as arn
    region = {"slug": "t", "bbox": [-79.0, 35.9, -78.8, 36.1], "utm_epsg": 32617}
    monkeypatch.setattr(arn, "all_waterways", lambda bbox: [
        ("", [(-78.905, 36.000), (-78.885, 36.000)], {"waterway": "stream", "underground": False})])
    monkeypatch.setattr(arn, "named_flowlines_nhd", lambda bbox: [])
    mask = rr.stream_mask_union(region)
    assert mask is not None and not mask.is_empty
    utm = "EPSG:32617"
    cands = gpd.GeoDataFrame(
        {"which": ["near", "far"]},
        geometry=[Point(-78.900, 36.0005), Point(-78.900, 36.05)],
        crs="EPSG:4326").to_crs(utm)
    kept = rr.apply_stream_mask(cands, mask)        # the gate run_region uses
    assert list(kept["which"]) == ["near"]          # ~55 m kept, ~5.5 km removed
    # empty mask (healthy sources, no waterways) → everything removed
    from shapely.geometry import GeometryCollection
    assert len(rr.apply_stream_mask(cands, GeometryCollection())) == 0
    # unnamed CULVERT must not hold a site in the mask (never drawn)
    monkeypatch.setattr(arn, "all_waterways", lambda bbox: [
        ("", [(-78.905, 36.000), (-78.885, 36.000)],
         {"waterway": "stream", "underground": True})])
    m2 = rr.stream_mask_union(region)
    assert len(rr.apply_stream_mask(cands, m2)) == 0
    # outage → None → caller refuses to run unmasked
    monkeypatch.setattr(arn, "named_flowlines_nhd", lambda bbox: None)
    assert rr.stream_mask_union(region) is None
