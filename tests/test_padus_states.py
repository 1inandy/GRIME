"""Focused tests for the state-resolved PAD-US 4.1 local loader."""

import os
import sys

import geopandas as gpd
from shapely.geometry import Polygon

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import real_sources as rs


def _set_root(monkeypatch, tmp_path):
    root = tmp_path / "padus"
    monkeypatch.setattr(rs, "_PADUS_ROOT", root)
    monkeypatch.setattr(rs, "_PADUS_GDB", root / "nc" / "PADUS4_1_StateNC.gdb")
    monkeypatch.setattr(rs, "_padus_cache", {})
    for state in ("NC", "NY", "TX"):
        monkeypatch.delenv(f"GRIME_PADUS_GDB_{state}", raising=False)
    return root


def _empty_native():
    return gpd.GeoDataFrame(
        {"d_Des_Tp": [], "GAP_Sts": []}, geometry=[], crs="EPSG:3857")


def test_state_paths_and_layers_follow_cache_layout(tmp_path, monkeypatch):
    root = _set_root(monkeypatch, tmp_path)

    assert rs._padus_gdb_for_state("ny") == root / "ny" / "PADUS4_1_StateNY.gdb"
    assert rs._padus_gdb_for_state("TX") == root / "tx" / "PADUS4_1_StateTX.gdb"
    assert rs._padus_gdb_for_state("NC") == root / "nc" / "PADUS4_1_StateNC.gdb"
    assert rs._padus_layer_for_state("ny").endswith("_State_NY")
    assert rs._padus_layer_for_state("tx").endswith("_State_TX")
    assert rs._padus_gdb_for_state("../NY") is None


def test_per_state_environment_override(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    override = tmp_path / "custom" / "new_york.gdb"
    monkeypatch.setenv("GRIME_PADUS_GDB_NY", str(override))
    assert rs._padus_gdb_for_state("NY") == override


def test_missing_state_gdb_is_unknown_not_empty(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    got = rs.padus_protected_gdf(
        (-74.1, 40.6, -73.9, 40.8), "EPSG:32618", state_abbr="NY")
    assert got is None


def test_bbox_filtered_state_read_and_exact_clip(tmp_path, monkeypatch):
    root = _set_root(monkeypatch, tmp_path)
    gdb = root / "ny" / "PADUS4_1_StateNY.gdb"
    gdb.mkdir(parents=True)
    calls = []

    inside = Polygon([(-100, -100), (100, -100), (100, 100), (-100, 100)])
    outside = Polygon([(5000, 5000), (5100, 5000), (5100, 5100), (5000, 5100)])
    native = gpd.GeoDataFrame(
        {"d_Des_Tp": ["State Park", "Local Park"], "GAP_Sts": [2, 3]},
        geometry=[inside, outside], crs="EPSG:3857")

    def fake_read_file(path, *, layer, rows=None, bbox=None, **kwargs):
        calls.append({"path": path, "layer": layer, "rows": rows, "bbox": bbox})
        return _empty_native() if rows == 0 else native.copy()

    monkeypatch.setattr(gpd, "read_file", fake_read_file)
    got = rs.padus_protected_gdf(
        (-0.01, -0.01, 0.01, 0.01), "EPSG:3857",
        state_abbr="NY", pad_km=1.0)

    assert got is not None and len(got) == 1
    assert got.iloc[0]["designation"] == "State Park"
    assert got.crs.to_epsg() == 3857
    assert calls[0]["rows"] == 0 and calls[0]["bbox"] is None
    assert calls[1]["bbox"] is not None
    assert calls[1]["layer"].endswith("_State_NY")
    # A repeated identical query is served from the per-state/bbox cache.
    got.iloc[0, got.columns.get_loc("designation")] = "mutated"
    again = rs.padus_protected_gdf(
        (-0.01, -0.01, 0.01, 0.01), "EPSG:3857",
        state_abbr="NY", pad_km=1.0)
    assert len(calls) == 2
    assert again.iloc[0]["designation"] == "State Park"


def test_successful_empty_query_is_real_empty_gdf(tmp_path, monkeypatch):
    root = _set_root(monkeypatch, tmp_path)
    (root / "tx" / "PADUS4_1_StateTX.gdb").mkdir(parents=True)

    monkeypatch.setattr(gpd, "read_file", lambda *a, **k: _empty_native())
    got = rs.padus_protected_gdf(
        (-95.5, 29.6, -95.2, 29.9), "EPSG:32615", state_abbr="TX")

    assert got is not None
    assert got.empty
    assert "designation" in got.columns
    assert got.crs.to_epsg() == 32615


def test_unreadable_state_gdb_is_unknown(tmp_path, monkeypatch):
    root = _set_root(monkeypatch, tmp_path)
    (root / "ny" / "PADUS4_1_StateNY.gdb").mkdir(parents=True)

    def fail_read(*args, **kwargs):
        raise OSError("corrupt test geodatabase")

    monkeypatch.setattr(gpd, "read_file", fail_read)
    got = rs.padus_protected_gdf(
        (-74.1, 40.6, -73.9, 40.8), "EPSG:32618", state_abbr="NY")
    assert got is None
