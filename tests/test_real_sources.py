"""
Tests for core.real_sources — the real per-site data fetchers/parsers wired in by
scripts/wire_real_data.py. Network calls are monkeypatched with recorded fixtures
(shapes captured from the live endpoints on 2026-07-06), so these run offline and
in CI. The regression fixtures below pin the two things most likely to break
silently: the EROM cfs (no cms conversion) and the TRI longitude-sign fix.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import real_sources as rs


# ── flood Q10 — USGS SIR 2014-5030 Table 7 HR1 (verified coefficients) ──

def test_flood_q10_matches_sir2014_hr1_equations():
    # small basin (DA ≤ 3 mi²): 381·DA^0.7536·10^(0.0076·IMP)
    da_km2 = 5.0                    # 1.93 mi²
    da_mi2 = da_km2 * rs.KM2_TO_MI2
    exp_small = 381.0 * da_mi2 ** 0.7536 * 10 ** (0.0076 * 30.0)
    assert rs.flood_q10_cfs_sir2014(da_km2, 30.0) == pytest.approx(exp_small, rel=1e-6)

    # large basin (3 < DA ≤ 436 mi²): 484·DA^0.5539·10^(0.0060·IMP)
    da_km2 = 40.0                   # 15.4 mi²
    da_mi2 = da_km2 * rs.KM2_TO_MI2
    exp_large = 484.0 * da_mi2 ** 0.5539 * 10 ** (0.0060 * 25.0)
    assert rs.flood_q10_cfs_sir2014(da_km2, 25.0) == pytest.approx(exp_large, rel=1e-6)


def test_flood_q10_monotonic_and_guarded():
    assert rs.flood_q10_cfs_sir2014(None, 30) is None
    assert rs.flood_q10_cfs_sir2014(0, 30) is None
    # more impervious → larger flood; more area → larger flood
    assert rs.flood_q10_cfs_sir2014(40, 50) > rs.flood_q10_cfs_sir2014(40, 10)
    assert rs.flood_q10_cfs_sir2014(80, 20) > rs.flood_q10_cfs_sir2014(10, 20)


# ── channel width — Bieger 2015 Table 3 (AHI division, metric) ──

def test_bieger_width_curve():
    # W(m) = 3.12·DA(km²)^0.415
    assert rs.bankfull_width_m_bieger(1.0) == pytest.approx(3.12, rel=1e-9)
    assert rs.bankfull_width_m_bieger(100.0) == pytest.approx(3.12 * 100 ** 0.415, rel=1e-9)
    # physically sensible for the Ellerbe catchment range (2–116 km² → ~4–22 m)
    assert 3.5 < rs.bankfull_width_m_bieger(2.0) < 5.0
    assert 18 < rs.bankfull_width_m_bieger(116.0) < 25
    assert rs.bankfull_width_m_bieger(None) is None


# ── EROM parsers: qe_ma is ALREADY cfs (regression against a cms bug) ──

_EROM_PROPS = {  # recorded shape for COMID 8778141 (Ellerbe Creek)
    "comid": 8778141, "gnis_name": "Ellerbe Creek", "totdasqkm": 15.5502,
    "qe_ma": 7.881, "va_ma": 0.83,
    "qe_01": 10.9, "qe_02": 11.8, "qe_03": 12.1, "qe_04": 8.2, "qe_05": 5.9,
    "qe_06": 4.4, "qe_07": 4.0, "qe_08": 3.7, "qe_09": 4.6, "qe_10": 5.2,
    "qe_11": 8.4, "qe_12": 9.6,
}


def test_erom_mean_q_is_cfs_not_cms():
    # 7.881 cfs, NOT 7.881×35.31 — a 15.5 km² creek is a single-digit-to-tens cfs stream
    q = rs.erom_mean_q_cfs(_EROM_PROPS)
    assert q == pytest.approx(7.881, rel=1e-9)
    assert q < 60  # would be ~278 under the cms bug


def test_erom_seasonal_cv_from_monthly():
    cv = rs.erom_seasonal_cv(_EROM_PROPS)
    assert 0.2 < cv < 0.6           # monthly-mean CV, not daily
    assert rs.erom_seasonal_cv({"qe_01": 5.0}) is None  # incomplete → None


def test_erom_drainage_km2():
    assert rs.erom_drainage_km2(_EROM_PROPS) == pytest.approx(15.5502)
    assert rs.erom_drainage_km2({}) is None


# ── TRI: the longitude-sign + null fix (the actual shipped bug) ──

def test_tri_sign_fix_and_null_handling(monkeypatch):
    # Envirofacts returns bare-positive longitudes and some null rows
    fixture = [
        {"pref_latitude": 36.0, "pref_longitude": 78.891667},      # sign-flip to -78.89
        {"pref_latitude": 35.995556, "pref_longitude": 78.899444},
        {"pref_latitude": None, "pref_longitude": None},           # null → dropped
        {"pref_latitude": 36.0, "pref_longitude": -78.9},          # already negative
        {"pref_latitude": 41.0, "pref_longitude": 74.0},           # NY-ish → outside window, dropped
    ]
    monkeypatch.setattr(rs, "cached_get_json", lambda *a, **k: fixture)
    pts = rs.tri_points_durham()
    assert len(pts) == 3                       # 2 sign-flipped + 1 already-negative; nulls/out-of-window gone
    for lat, lon in pts:
        assert 35.0 <= lat <= 37.0 and -80.0 <= lon <= -78.0   # all land in Durham region


def test_tri_zero_when_endpoint_dead(monkeypatch):
    monkeypatch.setattr(rs, "cached_get_json", lambda *a, **k: None)
    assert rs.tri_points_durham() == []


# ── land ownership classifier ──

@pytest.mark.parametrize("owner,expected", [
    ("CITY OF DURHAM", 1.0),
    ("DURHAM COUNTY", 1.0),
    ("NC DEPT OF TRANSPORTATION", 1.0),
    ("Duke University", 1.0),
    ("SMITH, JOHN & JANE", 0.5),
    ("ACME PROPERTIES LLC", 0.5),
    (None, 0.5),
    ("", 0.5),
])
def test_land_ownership_classifier(owner, expected):
    assert rs.land_ownership_from_owner(owner) == expected


# ── NBI DMS→DD guard ──

def test_dms_to_dd():
    # NBI raw format DDMMSSSS (seconds ×100): 36°01'23.40" → 36012340
    assert rs._dms_to_dd(36012340) == pytest.approx(36 + 1 / 60 + 23.40 / 3600, rel=1e-6)


def test_nbi_parses_decimal_degrees(monkeypatch):
    fixture = {"features": [
        {"attributes": {"LAT_016": 36.02, "LONG_017": -78.90}},
        {"attributes": {"LAT_016": 35.99, "LONG_017": 78.91}},   # positive lon → flipped
        {"attributes": {"LAT_016": None, "LONG_017": None}},       # dropped
    ]}
    monkeypatch.setattr(rs, "cached_get_json", lambda *a, **k: fixture)
    pts = rs.nbi_bridge_points((-79.05, 35.90, -78.75, 36.05))
    assert len(pts) == 2
    assert all(lon < 0 for _, lon in pts)


# ── CSO: distinguishes truthful-zero from download failure ──

def test_cso_download_failure_is_none_not_zero(monkeypatch):
    monkeypatch.setattr(rs, "cached_download", lambda *a, **k: None)
    assert rs.cso_points_nc((-79.05, 35.90, -78.75, 36.05)) is None  # unknown, not confirmed 0


# ── SEMS (Superfund) — fix-pass-2 Phase 1 (shapes recorded 2026-07-10) ──

def test_sems_sign_fix_bbox_filter_and_null_handling(monkeypatch):
    fixture = [
        # in-bbox, longitude sign dropped by Envirofacts → must be flipped
        {"name": "IN-BBOX SIGNFIX", "fk_ref_state_code": "NC",
         "primary_latitude_decimal_val": "35.99",
         "primary_longitude_decimal_val": "78.90"},
        # in-bbox, correct sign
        {"name": "IN-BBOX OK", "fk_ref_state_code": "NC",
         "primary_latitude_decimal_val": "36.01",
         "primary_longitude_decimal_val": "-78.95"},
        # no coordinates (2/3 of SEMS rows) → skipped, not fabricated
        {"name": "NO COORDS", "fk_ref_state_code": "NC",
         "primary_latitude_decimal_val": None,
         "primary_longitude_decimal_val": None},
        # out of bbox
        {"name": "CHARLOTTE", "fk_ref_state_code": "NC",
         "primary_latitude_decimal_val": "35.23",
         "primary_longitude_decimal_val": "-80.84"},
    ]
    monkeypatch.setattr(rs, "cached_get_json", lambda *a, **k: fixture)
    pts = rs.sems_superfund_points("NC", (-79.05, 35.90, -78.75, 36.05))
    assert pts == [(35.99, -78.90), (36.01, -78.95)]


def test_sems_none_when_endpoint_dead(monkeypatch):
    # None (unreachable) must be distinguishable from [] (no sites in bbox):
    # the wiring keeps the documented fallback only for None.
    monkeypatch.setattr(rs, "cached_get_json", lambda *a, **k: None)
    assert rs.sems_superfund_points("NC", (-79.05, 35.90, -78.75, 36.05)) is None


# ── SWAP surface intakes — fix-pass-2 Phase 1 ──

def test_surface_intake_parse(monkeypatch):
    fixture = {"features": [
        {"attributes": {"source_typ": "Surface Water"},
         "geometry": {"x": -79.406, "y": 36.127}},
        {"attributes": {"source_typ": "Surface Water"},
         "geometry": {}},                                   # no geometry → skip
    ]}
    monkeypatch.setattr(rs, "cached_get_json", lambda *a, **k: fixture)
    assert rs.nc_surface_intake_points((-80, 35, -79, 37)) == [(36.127, -79.406)]


def test_surface_intake_none_when_endpoint_dead(monkeypatch):
    monkeypatch.setattr(rs, "cached_get_json", lambda *a, **k: None)
    assert rs.nc_surface_intake_points((-80, 35, -79, 37)) is None


# ── PAD-US local clip — fix-pass-2 Phase 1 ──

def test_padus_missing_gdb_returns_none(monkeypatch):
    monkeypatch.setattr(rs, "_PADUS_GDB", rs.Path("/nonexistent/padus.gdb"))
    monkeypatch.setattr(rs, "_padus_cache", {"gdf": None, "loaded": False})
    assert rs.padus_protected_gdf((-79.05, 35.90, -78.75, 36.05), "EPSG:32617") is None


def test_padus_protected_area_score_math():
    # The scoring math itself (shipped curve, science-frozen): substring
    # designation match with 0.2 default, 1/(1+d_km/5) decay, 20 km buffer.
    import geopandas as gpd
    from shapely.geometry import Point, Polygon
    from core.impact import protected_area_score_from_gdf

    sq = Polygon([(4500, -500), (5500, -500), (5500, 500), (4500, 500)])  # centroid 5 km
    far = Polygon([(60000, 0), (61000, 0), (61000, 1000), (60000, 1000)])  # >20 km → excluded
    gdf = gpd.GeoDataFrame(
        {"designation": ["State Park Land", "National Park"]},
        geometry=[sq, far], crs="EPSG:32617")
    got = protected_area_score_from_gdf(Point(0, 0), gdf)
    assert got == pytest.approx(0.7 * (1 / (1 + 5.0 / 5)), rel=1e-9)  # substring 'State Park'
    empty = gpd.GeoDataFrame({"designation": []}, geometry=[], crs="EPSG:32617")
    assert protected_area_score_from_gdf(Point(0, 0), empty) == 0.0


# ── SIR 2014-5030 HR4 (Coastal Plain) flood — fix-pass-2 Phase 2 ──

def test_flood_q10_hr4_matches_sir2014_equation():
    da_km2 = 10.0
    da_mi2 = da_km2 * rs.KM2_TO_MI2
    exp = 51.8 * da_mi2 ** 0.6004 * 10 ** (0.0101 * 20.0) * 10 ** (0.0666 * 8.5)
    assert rs.flood_q10_cfs_sir2014_hr4(da_km2, 20.0, 8.5) == pytest.approx(exp, rel=1e-9)


def test_flood_q10_hr4_guards_and_domain():
    assert rs.flood_q10_cfs_sir2014_hr4(None, 20, 8.5) is None
    assert rs.flood_q10_cfs_sir2014_hr4(0, 20, 8.5) is None
    # no I24H50Y constant → None (documented fallback, never a guess)
    assert rs.flood_q10_cfs_sir2014_hr4(10.0, 20, None) is None
    # domain clamps (HR1 convention): below 0.10 mi2 and above 53.5 mi2
    lo = rs.flood_q10_cfs_sir2014_hr4(0.01, 0, 8.0)
    assert lo == pytest.approx(51.8 * 0.10 ** 0.6004 * 10 ** (0.0666 * 8.0), rel=1e-9)
    hi = rs.flood_q10_cfs_sir2014_hr4(1000.0, 0, 8.0)
    assert hi == pytest.approx(51.8 * 53.5 ** 0.6004 * 10 ** (0.0666 * 8.0), rel=1e-9)
    # wetter design storm → larger flood
    assert (rs.flood_q10_cfs_sir2014_hr4(10, 10, 11.0)
            > rs.flood_q10_cfs_sir2014_hr4(10, 10, 7.0))


def test_noaa_atlas14_parser(monkeypatch):
    text = (
        "Point precipitation frequency estimates (inches)\n"
        "by duration for ARI (years):, 1,2,5,10,25,50,100,200,500,1000\n"
        "12-hr:, 3.28,3.99,5.14,6.16,7.69,9.06,10.6,12.4,15.1,17.5\n"
        "24-hr:, 3.84,4.66,6.04,7.24,9.10,10.8,12.6,14.8,18.1,21.1\n"
        "2-day:, 4.54,5.48,7.03,8.38,10.4,12.3,14.3,16.6,20.2,23.3\n")
    monkeypatch.setattr(rs, "cached_get_text", lambda *a, **k: text)
    assert rs.noaa_atlas14_24h50y_in(34.22, -77.94) == pytest.approx(10.8)
    monkeypatch.setattr(rs, "cached_get_text", lambda *a, **k: None)
    assert rs.noaa_atlas14_24h50y_in(34.22, -77.94) is None


# ── NC DPS/OneMap 3.125-ft lidar DEM bank profiles — max-out step 1 ──

def test_nc_lidar_samples_parse_resolution_source_and_order(monkeypatch):
    calls = []

    def fake_post(url, payload, **kwargs):
        calls.append((url, payload))
        # locationId is local to each chunk. Deliberately reverse response order.
        return {"samples": [
            {"locationId": 1, "value": "300.0", "resolution": 4.0,
             "attributes": {"name": "coarse-overview"}},
            {"locationId": 0, "value": "291.203674316", "resolution": 3.125,
             "attributes": {"name": "Durham_2024_QL1_03ft_CountywideRaster"}},
        ]}

    monkeypatch.setattr(rs, "cached_post_form_json", fake_post)
    got = rs.nc_lidar_elevations([(2033454.49, 824484.42),
                                  (2033457.62, 824484.42)])
    assert len(calls) == 1
    assert calls[0][1]["geometryType"] == "esriGeometryMultipoint"
    assert got[0]["elevation_m"] == pytest.approx(291.203674316 * rs.FT_TO_M)
    assert got[0]["source"].startswith("Durham_2024_QL1")
    assert got[1] is None                 # coarse response is never called ~1 m


def test_nc_lidar_samples_failure_is_documented_fallback(monkeypatch):
    monkeypatch.setattr(rs, "cached_post_form_json", lambda *a, **k: None)
    assert rs.nc_lidar_elevations([(1.0, 2.0), (3.0, 4.0)]) == [None, None]


def test_bank_profile_metric_discriminates_and_guards():
    import math
    import numpy as np
    from core.feasibility import bank_slope_from_profile, bank_slope_score

    d = np.linspace(0.0, 50.0, 101)
    center = 25.0
    symmetric = np.abs(d - center) * math.tan(math.radians(20.0))
    assert bank_slope_from_profile(d, symmetric) == pytest.approx(20.0, abs=0.05)

    asymmetric = np.where(
        d < center, (center - d) * math.tan(math.radians(10.0)),
        (d - center) * math.tan(math.radians(30.0)))
    assert bank_slope_from_profile(d, asymmetric) == pytest.approx(20.0, abs=0.05)
    assert bank_slope_from_profile(d, np.zeros_like(d)) == pytest.approx(0.0)
    assert bank_slope_from_profile([0, 1], [10, 11]) is None

    # Frozen shipped curve boundaries; the new work changes inputs, not math.
    assert bank_slope_score(14.999) == 1.0
    assert bank_slope_score(15.0) == 0.5
    assert bank_slope_score(30.0) == 0.2
    assert bank_slope_score(45.0) == 0.1


# ── statewide land-ownership extension — fix-pass-2 Phase 2 ──

def test_land_ownership_statewide_hints():
    assert rs.land_ownership_from_owner("BOONE TOWN OF") == 1.0
    assert rs.land_ownership_from_owner("TOWN OF CHAPEL HILL") == 1.0
    assert rs.land_ownership_from_owner("VILLAGE OF PINEHURST") == 1.0
    assert rs.land_ownership_from_owner("DOE JOHN & JANE") == 0.5
    assert rs.land_ownership_from_owner(None) == 0.5


def test_nc_parcel_owner_parse(monkeypatch):
    fixture = {"features": [{"attributes": {"ownname": "BOONE TOWN OF"}}]}
    monkeypatch.setattr(rs, "cached_get_json", lambda *a, **k: fixture)
    assert rs.nc_parcel_owner_at(36.2017, -81.6526) == "BOONE TOWN OF"
    monkeypatch.setattr(rs, "cached_get_json", lambda *a, **k: None)
    assert rs.nc_parcel_owner_at(36.2017, -81.6526) is None
