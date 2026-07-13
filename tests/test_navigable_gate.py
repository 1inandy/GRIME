"""Fix-pass-2 Phase 3: the USACE-NWN navigability hard gate.

All offline: the NWN geometry comes from the committed Wilmington fixture
(tests/fixtures/nwn_wilmington.geojson — Cape Fear River / AIWW segments
extracted 2026-07-10 from the cached statewide clip)."""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import real_sources as rs
from core.feasibility import NAVIGABLE_GATE_M, passes_hard_gates
from core.scoring import apply_hard_gates

FIXTURE = Path(ROOT) / "tests" / "fixtures" / "nwn_wilmington.geojson"
WILMINGTON_BBOX = (-78.05, 34.10, -77.75, 34.35)
DURHAM_BBOX = (-79.05, 35.90, -78.75, 36.05)
UTM18, UTM17 = "EPSG:32618", "EPSG:32617"


@pytest.fixture
def nwn_fixture(monkeypatch):
    monkeypatch.setattr(rs, "_NWN_GEOJSON", FIXTURE)
    monkeypatch.setattr(rs, "_nwn_cache", {"gdf": None, "loaded": False})


def test_union_nonempty_near_wilmington_empty_inland(nwn_fixture):
    u = rs.nwn_navigable_union(WILMINGTON_BBOX, UTM18)
    assert u is not None and not u.is_empty
    u2 = rs.nwn_navigable_union(DURHAM_BBOX, UTM17)
    assert u2 is not None and u2.is_empty  # computed absence, not a failure


def test_missing_clip_returns_none(monkeypatch):
    monkeypatch.setattr(rs, "_NWN_GEOJSON", Path("/nonexistent/nwn.geojson"))
    monkeypatch.setattr(rs, "_nwn_cache", {"gdf": None, "loaded": False})
    assert rs.nwn_navigable_union(WILMINGTON_BBOX, UTM18) is None


def test_nc_only_clip_is_unknown_outside_state(nwn_fixture):
    assert rs.nwn_navigable_union(
        (-74.13, 40.64, -73.88, 40.79), UTM18, state_abbr="NY") is None


def test_gate_drops_on_channel_keeps_offset_and_nan(nwn_fixture):
    """A candidate ON the Cape Fear shipping channel is excluded; one well
    off it survives; a row with no NWN data (NaN) survives — gates only fire
    on real values."""
    import geopandas as gpd

    u = rs.nwn_navigable_union(WILMINGTON_BBOX, UTM18)
    # take a point on the union and offset another 500 m east
    on_channel = u.representative_point()
    df = pd.DataFrame({
        "flow_velocity_ms": [1.0, 1.0, 1.0],
        "channel_width_m": [5.0, 5.0, 5.0],
        "land_ownership": [0.5, 0.5, 0.5],
        "navigable_dist_m": [
            float(on_channel.distance(u)),          # ~0 m → gated
            float(on_channel.distance(u)) + 500.0,  # 500 m off → kept
            np.nan,                                  # no data → kept
        ],
    })
    kept = apply_hard_gates(df)
    assert list(kept.index) == [1, 2]


def test_inland_control_no_site_newly_gated(nwn_fixture):
    """Durham (inland control): no NWN segment near the bbox → NaN distances →
    the gate removes nothing. Guards the plan's 'no inland site newly gated'."""
    u = rs.nwn_navigable_union(DURHAM_BBOX, UTM17)
    assert u.is_empty
    df = pd.DataFrame({
        "flow_velocity_ms": [1.0] * 4,
        "channel_width_m": [5.0] * 4,
        "land_ownership": [0.5] * 4,
        "navigable_dist_m": [np.nan] * 4,   # what the runner writes for empty
    })
    assert len(apply_hard_gates(df)) == 4


def test_passes_hard_gates_parity():
    assert passes_hard_gates(navigable_dist_m=NAVIGABLE_GATE_M) is False
    assert passes_hard_gates(navigable_dist_m=NAVIGABLE_GATE_M + 0.01) is True
    assert passes_hard_gates(navigable_dist_m=None) is True
    assert passes_hard_gates() is True
