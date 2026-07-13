"""Offline contract tests for the official PAD-US service fallback."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import padus_service as ps


def _feature(objectid, designation="SP"):
    return {
        "type": "Feature",
        "properties": {"OBJECTID": objectid, "Des_Tp": designation,
                       "GAP_Sts": "2"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [-74.01, 40.69], [-73.99, 40.69], [-73.99, 40.71],
            [-74.01, 40.71], [-74.01, 40.69],
        ]]},
    }


def test_remote_padus_pages_and_normalizes(monkeypatch):
    calls = []

    def fake(url, params, **kwargs):
        if url == ps.PADUS_FEATURE_LAYER:
            return {"fields": [{"name": "Des_Tp", "domain": {
                "codedValues": [{"code": "SP", "name": "State Park"}]}}]}
        calls.append(params)
        if params["resultOffset"] == 0:
            return {"type": "FeatureCollection", "features": [_feature(1)],
                    "properties": {"exceededTransferLimit": True}}
        return {"type": "FeatureCollection", "features": [_feature(2)],
                "properties": {"exceededTransferLimit": False}}

    monkeypatch.setattr(ps, "cached_get_json", fake)
    got = ps.padus_protected_gdf_remote(
        (-74.02, 40.68, -73.98, 40.72), "EPSG:32618", page_size=1)
    assert got is not None and len(got) == 2
    assert set(got["designation"]) == {"State Park"}
    assert got.crs.to_epsg() == 32618
    assert [call["resultOffset"] for call in calls] == [0, 1]
    assert all(call["orderByFields"] == "OBJECTID" for call in calls)


def test_remote_padus_failure_is_unknown(monkeypatch):
    monkeypatch.setattr(ps, "cached_get_json", lambda *a, **k: None)
    assert ps.padus_protected_gdf_remote(
        (-74.02, 40.68, -73.98, 40.72), "EPSG:32618") is None


def test_remote_padus_successful_empty_is_real(monkeypatch):
    def fake(url, *args, **kwargs):
        if url == ps.PADUS_FEATURE_LAYER:
            return {"fields": [{"name": "Des_Tp", "domain": {
                "codedValues": [{"code": "SP", "name": "State Park"}]}}]}
        return {"type": "FeatureCollection", "features": [],
                "properties": {"exceededTransferLimit": False}}

    monkeypatch.setattr(ps, "cached_get_json", fake)
    got = ps.padus_protected_gdf_remote(
        (-95.5, 29.6, -95.2, 29.9), "EPSG:32615")
    assert got is not None and got.empty
    assert list(got.columns) == ["designation", "GAP_Sts", "geometry"]


def test_remote_padus_repairs_invalid_official_ring(monkeypatch):
    bowtie = _feature(3)
    bowtie["geometry"]["coordinates"] = [[
        [-74.01, 40.69], [-73.99, 40.71], [-73.99, 40.69],
        [-74.01, 40.71], [-74.01, 40.69],
    ]]

    def fake(url, *args, **kwargs):
        if url == ps.PADUS_FEATURE_LAYER:
            return {"fields": [{"name": "Des_Tp", "domain": {
                "codedValues": [{"code": "SP", "name": "State Park"}]}}]}
        return {"type": "FeatureCollection", "features": [bowtie],
                "properties": {"exceededTransferLimit": False}}

    monkeypatch.setattr(ps, "cached_get_json", fake)
    got = ps.padus_protected_gdf_remote(
        (-74.02, 40.68, -73.98, 40.72), "EPSG:32618")
    assert got is not None and len(got) == 1
    assert got.geometry.iloc[0].is_valid
