#!/usr/bin/env python3
"""
GRIME — config-driven multi-region runner.

For each region in scripts/regions.json: DEM → pysheds streams → candidates →
real per-site parameter wiring (region-appropriate sources) → the SHIPPED,
UNCHANGED scoring → mock_data/regions/<slug>.geojson (+ index.json).

Design for a multi-hour batch:
  * RESUMABLE — a region whose output file already exists is skipped (--force
    or --only slug to redo). The index is rewritten after every region.
  * Failure-isolated — one region's exception is logged into the index as
    status=failed and the batch continues.
  * Cached — HTTP JSON via core.real_sources' disk cache; DEM via the HyRiver
    sqlite cache; osmnx Overpass responses via osmnx's own cache folder with
    rate limiting on; per-region intermediates written to cache/regions_work/
    (NEVER to mock_data/, so the frozen candidates.geojson stays byte-safe).
  * Honest — per-parameter provenance per region; region-inappropriate
    relationships are never applied (flood only where flood_method says so);
    unfetchable values stay documented fallbacks (NaN/constants), no fabrication.

Run:  python3 scripts/run_regions.py                 # all pending regions
      python3 scripts/run_regions.py --only durham    # one region
      python3 scripts/run_regions.py --force --only x # redo one
"""
import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))
sys.path.insert(0, _ROOT)

def _configure_osmnx():
    """Import + configure osmnx (hard rate-limit + big-city-safe cache) lazily,
    once, right before the first graph call. Deferred so that merely IMPORTING
    this module — as the lightweight zero-region / supervisor tests do, and as
    the slim CI environment does (no heavy geo stack installed) — never requires
    osmnx. Only actually running a region's pipeline needs it."""
    import osmnx as ox
    if getattr(_configure_osmnx, "_done", False):
        return
    ox.settings.use_cache = True
    ox.settings.cache_folder = os.path.join(_ROOT, "cache", "osmnx")
    ox.settings.overpass_rate_limit = True
    ox.settings.requests_timeout = 300
    # Operational knob (fix-pass-2): point osmnx at an alternate Overpass
    # mirror base (e.g. https://overpass.kumi.systems/api — OSMnx appends
    # /interpreter itself) when the default endpoint is
    # rate-limiting a long batch. No default change; opt-in via env.
    _ep = os.environ.get("GRIME_OVERPASS_ENDPOINT")
    if _ep:
        _ep = _ep.rstrip("/")
        if _ep.endswith("/interpreter"):
            _ep = _ep.removesuffix("/interpreter")
        ox.settings.overpass_endpoint = _ep
        # Some public mirrors expose the interpreter but not the canonical
        # status-line format OSMnx 1.x's rate-limit parser requires. Operators
        # may bypass only that parser while the supervisor still enforces its
        # single-worker 4–10 s inter-region pacing. Default remains on.
        _rl = os.environ.get("GRIME_OVERPASS_RATE_LIMIT", "1").strip().lower()
        if _rl in {"0", "false", "no", "off"}:
            ox.settings.overpass_rate_limit = False
    _configure_osmnx._done = True


from core import WGS84, osm_drive_graph, inverse_distance_score, safe_call
from core.flow import (
    estimate_runoff_coefficient, compute_flow_velocity, velocity_feasibility,
)
from core.feasibility import (
    road_access_score, channel_width_score, bank_slope_score, compute_bank_slope,
    compute_bank_slopes_nc_lidar, bridge_proximity_bonus, NAVIGABLE_GATE_M,
)
from core.generation import get_road_density
from core.impact import (get_tourism_amenity_density,
                         protected_area_score_from_gdf, water_intake_score)
from core.scoring import compute_composite_score, sensitivity_analysis, ALL_PARAMS
from core.pipeline import run_pipeline
from core import real_sources as rs
from core import region_sources as rg
from core.padus_service import padus_protected_gdf_remote

CONFIG = Path(_ROOT) / "scripts/regions.json"
OUT_DIR = Path(_ROOT) / "mock_data/regions"
WORK_DIR = Path(_ROOT) / "cache/regions_work"
INDEX = OUT_DIR / "index.json"

PROTECTED = {"candidates.geojson", "candidates_v2.geojson", "places.json"}

MEASURED_FALLBACK_REASONS = {
    "population_density": (
        "Census block-group geometry or population was unavailable at this site; "
        "value remains null and is removed by constant-column handling"),
    "ej_index": (
        "Census block-group demographic inputs were unavailable at this site; "
        "value remains null and is removed by constant-column handling"),
    "impervious_pct": (
        "NLDI/StreamCat did not return a snap-compatible catchment value; "
        "value remains null and is removed by constant-column handling"),
    "runoff_coeff_C": (
        "source impervious_pct was unavailable; derived value remains null"),
    "usgs_mean_q_cfs": (
        "NHDPlus EROM was unavailable or failed the drainage-area snap guard; "
        "value remains null"),
    "seasonal_cv": (
        "NHDPlus monthly EROM was unavailable or failed the drainage-area "
        "snap guard; value remains null"),
    "flow_velocity_ms": (
        "EROM/DEM continuity input unavailable; shipped 0.5 m/s default used"),
    "velocity_feasibility": (
        "derived from the shipped 0.5 m/s velocity fallback"),
    "land_ownership": (
        "no containing parcel owner returned; shipped unknown ownership 0.5 used"),
    "road_density_km_km2": (
        "OSM drive-network measurement unavailable; value remains null"),
    "road_access_score": (
        "OSM drive-network measurement unavailable; value remains null"),
}



def _json_safe(obj):
    """NaN/Inf are not RFC-JSON and FastAPI's serializer refuses them (prod
    500 on /api/candidates, 2026-07-11). Missing-value floats become null;
    pandas reads null back as NaN so gate/scoring semantics are unchanged."""
    import math
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj

def log(slug, msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{slug}] {msg}", flush=True)


# ── Stream mask (owner-approved site-selection gate, 2026-07-09) ─────────
# DEM flow accumulation happily routes "streams" down street grids in flat,
# heavily-built metros (NYC/Chicago/LA/SF), producing candidate sites hundreds
# of metres from any real channel. The stream mask confines candidates to
# within STREAM_MASK_BUFFER_M of a REAL mapped waterway — the union of OSM
# waterway ways (named + unnamed, any channel type) and USGS NHDPlus flowlines,
# fetched through the same cached fetchers the river-name bake uses. The buffer
# matches the display snap radius (150 m), i.e. a kept site is exactly one that
# can honestly sit on its mapped channel. Applied BEFORE parameter wiring and
# the shipped hard gates; removals are recorded in provenance + the index. If
# EITHER geometry source is unavailable the region run ABORTS rather than
# running unmasked — never a silent methodology downgrade.
STREAM_MASK_BUFFER_M = 150.0


def stream_mask_union(region):
    """UTM union of the mapped waterway geometry for the region (OSM + NHD,
    cached). The pool is aligned with what the explorer can DRAW and SNAP TO
    (see scripts/add_river_names): named ways of any kind (surface + culverted,
    the latter drawn dashed), unnamed SURFACE ways above the per-type length
    gates, and NHD flowlines — so a site kept by the mask is always one whose
    channel is actually visible on the map. Returns shapely geometry, or None
    when a source is unavailable."""
    from shapely.geometry import LineString
    from shapely.geometry import shape as shape_of
    from shapely.ops import unary_union
    from scripts.add_river_names import (BBOX_BUFFER_DEG, MIN_LEN_M,
                                         all_waterways, named_flowlines_nhd)
    bbox = region["bbox"]
    fb = (bbox[0] - BBOX_BUFFER_DEG, bbox[1] - BBOX_BUFFER_DEG,
          bbox[2] + BBOX_BUFFER_DEG, bbox[3] + BBOX_BUFFER_DEG)
    osm = all_waterways(fb)
    nhd = named_flowlines_nhd(fb)
    if osm is None or nhd is None:
        return None
    utm = f"EPSG:{region['utm_epsg']}"

    def to_utm(geoms):
        return list(gpd.GeoSeries(geoms, crs="EPSG:4326").to_crs(utm)) if geoms else []

    named_geoms, unnamed_geoms, unnamed_ww = [], [], []
    for nm, coords, meta in osm:
        try:
            g = LineString(coords)
        except Exception:
            continue
        if nm:
            named_geoms.append(g)
        elif not meta["underground"]:
            # unnamed culverts are never drawn — they must not keep a site
            unnamed_geoms.append(g)
            unnamed_ww.append(meta["waterway"])
    nhd_geoms = []
    for _nm, gj in nhd:
        try:
            nhd_geoms.append(shape_of(gj))
        except Exception:
            continue

    pool = to_utm(named_geoms) + to_utm(nhd_geoms)
    for g, ww in zip(to_utm(unnamed_geoms), unnamed_ww):
        if g.length >= MIN_LEN_M.get(ww, 120):   # same gates as the drawn network
            pool.append(g)
    if not pool:
        # Sources healthy but genuinely no mapped waterway in the bbox: an
        # empty mask (removes everything) is the honest result.
        from shapely.geometry import GeometryCollection
        return GeometryCollection()
    return unary_union(pool)


def apply_stream_mask(cands, mask):
    """Keep only candidates within STREAM_MASK_BUFFER_M of the mask geometry
    (same-CRS GeoDataFrame + shapely geometry). Empty mask → empty result."""
    if mask.is_empty:
        return cands.iloc[0:0].reset_index(drop=True)
    dists = cands.geometry.distance(mask)
    return cands[dists <= STREAM_MASK_BUFFER_M].reset_index(drop=True)


def catchment_disc(pt_utm, area_km2):
    """The model's own per-candidate catchment proxy (matches core.scoring)."""
    area_km2 = area_km2 or 1.0
    radius_m = max(500.0, (max(area_km2, 0.01) / np.pi) ** 0.5 * 1000.0)
    return pt_utm.buffer(radius_m)


def record_measured_provenance(prov, param, source, n_real, n_sites,
                               fallback_reason):
    """Record a measured source without ever calling zero measurements real.

    A source may populate only part of a candidate batch (for example, some
    NLDI snaps lack EROM attributes). Keep that source live when at least one
    site is measured, but disclose the per-site fallback count and reason.
    When no site is measured, the whole parameter is an explicit fallback.
    """
    n_real = int(n_real or 0)
    n_sites = int(n_sites)
    if n_real <= 0:
        prov[param] = {
            "kind": "fallback",
            "source": source,
            "reason": fallback_reason,
            "n_real": 0,
            "n_fallback": n_sites,
            "n_sites": n_sites,
        }
        return
    prov[param] = {
        "kind": "real",
        "source": source,
        "n_real": n_real,
        "n_sites": n_sites,
    }
    if n_real < n_sites:
        prov[param].update({
            "n_fallback": n_sites - n_real,
            "fallback_reason": fallback_reason,
        })


def normalize_output_provenance(doc):
    """Backfill fallback disclosure in an already-scored region document.

    This is metadata-only: it never changes a feature value, rank, score, or
    geometry. It makes outputs written before a resumed batch use the same
    provenance contract as workers launched after the resume.
    """
    provenance = doc.get("provenance") or {}
    params = provenance.get("parameters") or {}
    for param, reason in MEASURED_FALLBACK_REASONS.items():
        entry = params.get(param)
        if not isinstance(entry, dict):
            continue
        n_sites = int(entry.get("n_sites") or 0)
        n_real = int(entry.get("n_real") or 0)
        if entry.get("kind") == "real" and n_real <= 0:
            entry["kind"] = "fallback"
            entry["reason"] = reason
        if n_real < n_sites:
            entry["n_fallback"] = n_sites - n_real
            if entry.get("kind") == "real":
                entry["fallback_reason"] = reason
    flow = params.get("flow_velocity_ms") or {}
    transport = params.get("velocity_transport_favorability")
    if isinstance(transport, dict):
        n_sites = int(transport.get("n_sites") or flow.get("n_sites") or 0)
        n_real = int(transport.get("n_real") or flow.get("n_real") or 0)
        transport["n_real"] = n_real
        if n_real < n_sites:
            transport["n_fallback"] = n_sites - n_real
            transport["fallback_reason"] = MEASURED_FALLBACK_REASONS[
                "flow_velocity_ms"]
    flood = params.get("flood_q10_cfs")
    impervious = params.get("impervious_pct")
    if isinstance(flood, dict) and isinstance(impervious, dict):
        n_sites = int(flood.get("n_sites") or 0)
        n_imp_real = int(impervious.get("n_real") or 0)
        if n_imp_real < n_sites:
            flood["n_impervious_fallback"] = n_sites - n_imp_real
            flood["impervious_fallback_reason"] = (
                "StreamCat imperviousness unavailable; the existing regression-"
                "input fallback of 0.0% impervious was used and is disclosed here")
    return doc


def repair_region_provenance():
    """Normalize provenance metadata for every existing nonzero region file."""
    repaired = 0
    for path in sorted(OUT_DIR.glob("*.geojson")):
        doc = json.loads(path.read_text())
        if not doc.get("features"):
            continue
        before = json.dumps(doc.get("provenance"), sort_keys=True)
        normalize_output_provenance(doc)
        after = json.dumps(doc.get("provenance"), sort_keys=True)
        if after != before:
            path.write_text(
                json.dumps(_json_safe(doc), indent=1, sort_keys=True,
                           allow_nan=False) + "\n")
            repaired += 1
    print(f"[repair-provenance] normalized {repaired} region files", flush=True)


def complete_padus_inventory(bbox, utm_crs, source_states):
    """Load a complete PAD-US inventory for every state touching a region.

    Prefer the versioned state packages. If any package is unavailable, query
    the official nationwide service for the *whole* bbox. A failed nationwide
    query returns ``None`` even when one local state package succeeded: scoring
    a partial cross-state inventory would turn missing polygons into false
    computed absences.
    """
    frames = []
    missing = []
    for state in source_states:
        frame = rs.padus_protected_gdf(
            bbox, utm_crs, state_abbr=state)
        if frame is None:
            missing.append(state)
        else:
            frames.append(frame)
    if missing:
        remote = padus_protected_gdf_remote(bbox, utm_crs)
        if remote is None:
            return None, (
                "unavailable complete inventory "
                f"({','.join(missing)} state package missing; service failed)"
            )
        return remote, "USGS feature service"
    if not frames:
        return None, "unavailable complete inventory (no state package loaded)"
    return (gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=utm_crs),
            "state GDB")


def wire_region_parameters(cands, region):
    """Attach all 27 parameters to the candidate GeoDataFrame (region UTM).
    Returns (gdf, provenance_dict). Real where fetchable; NaN/documented
    fallback where not — never fabricated."""
    utm = f"EPSG:{region['utm_epsg']}"
    bbox = tuple(region["bbox"])
    slug = region["slug"]
    source_states = tuple(region.get("source_states", [region["state"]]))
    source_states_label = ",".join(source_states)
    n = len(cands)
    prov = {}

    def mark(param, kind, source, n_real=None):
        prov[param] = {"kind": kind, "source": source,
                       "n_real": n_real if n_real is not None else (n if kind == "real" else 0),
                       "n_sites": n}

    def fetch_state_points(fetcher, query_bbox):
        """Fetch a complete multi-state inventory, never an unlabeled partial."""
        merged = []
        for state in source_states:
            points = fetcher(state, query_bbox)
            if points is None:
                return None
            merged.extend(points)
        return list(dict.fromkeys(merged))

    # ── shared fetches ──
    log(slug, "NLDI snap + EROM + StreamCat...")
    comids = [rs.comid_for_point(lat, lon) for lat, lon in zip(cands["lat"], cands["lon"])]
    erom = rs.erom_for_comids([c for c in comids if c])
    imperv = rs.streamcat_impervious([c for c in comids if c])
    log(slug, f"  comids {sum(c is not None for c in comids)}/{n} · erom {len(erom)} · imperv {len(imperv)}")

    log(slug, "Census block groups (bbox, multi-county)...")
    bg = rg.census_blockgroups_for_bbox(bbox, utm)
    log(slug, f"  block groups: {0 if bg is None else len(bg)}")

    log(slug, "Point layers: TRI(state) / NPDES / CSO / NBI...")
    tri_raw = fetch_state_points(rs.tri_facility_points, bbox)
    tri = rg.to_utm_points(tri_raw or [], utm)
    npdes_raw = rs.npdes_outfall_points(bbox, state_abbrs=source_states)
    npdes = rg.to_utm_points(npdes_raw or [], utm)
    cso_raw = rs.cso_outfall_points(bbox, state_abbrs=source_states)
    cso = rg.to_utm_points(cso_raw or [], utm)
    nbi_raw = rg.nbi_bridge_points_paged(bbox)
    nbi_pts = rg.to_utm_points(nbi_raw or [], utm)
    # A successful empty inventory is real and scores a real 0.0 at every
    # candidate. Source failure stays distinguishable as None.
    nbi_gdf = (gpd.GeoDataFrame(geometry=nbi_pts, crs=utm)
               if nbi_raw is not None else None)
    log(slug, f"  states={source_states_label} "
              f"TRI={'fetch-fail' if tri_raw is None else len(tri)} "
              f"NPDES={'dl-fail' if npdes_raw is None else len(npdes)} "
              f"CSO={'dl-fail' if cso_raw is None else len(cso)} "
              f"NBI={'fetch-fail' if nbi_raw is None else len(nbi_pts)}")

    litter_key = region.get("litter_source")
    litter_raw = rg.municipal_litter_points(litter_key, bbox) if litter_key else None
    litter = rg.to_utm_points(litter_raw or [], utm)
    log(slug, f"Municipal litter complaints: "
              f"{'unsupported' if not litter_key else ('fetch-fail' if litter_raw is None else len(litter))}")

    log(slug, "Impact layers: SEMS(superfund) / PAD-US / SWAP intakes...")
    # Superfund: SEMS state inventory, bbox +0.05 deg so a just-outside site
    # still contributes through the fast (500 m half-decay) proximity curve.
    pad = 0.05
    sems_bbox = (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
    sems_raw = fetch_state_points(rs.sems_superfund_points, sems_bbox)
    sems = rg.to_utm_points(sems_raw or [], utm)
    # Prefer versioned state GDBs. When any required package is unavailable,
    # query USGS's official PAD-US combined-inventory service for the whole bbox.
    padus, padus_source_kind = complete_padus_inventory(
        bbox, utm, source_states)
    # Drinking-water intakes: NC OneMap SWAP surface intakes; the shipped curve
    # integrates to 50 km, so fetch a 0.5-deg-padded bbox.
    intakes_gdf = None
    intake_raw = None
    if region["state"] == "NC":
        intake_raw = rs.nc_surface_intake_points(
            (bbox[0] - 0.5, bbox[1] - 0.5, bbox[2] + 0.5, bbox[3] + 0.5))
        if intake_raw is not None:
            intake_pts = rg.to_utm_points(intake_raw, utm)
            intakes_gdf = gpd.GeoDataFrame(geometry=intake_pts, crs=utm)
    log(slug, f"  SEMS={'dl-fail' if sems_raw is None else len(sems)} "
              f"PAD-US={'none' if padus is None else len(padus)} "
              f"intakes={'n/a' if region['state'] != 'NC' else ('dl-fail' if intake_raw is None else len(intake_raw))}")

    log(slug, "OSM drive network + tourism features (rate-limited, cached)...")
    drive = safe_call(osm_drive_graph, bbox, utm_crs=utm, default=None)
    if drive is None:
        log(slug, "  [warn] drive network unavailable — road params fall back")

    # DEM bits for velocity/slope
    dem_arr = region["_dem"]
    transform = region["_transform"]
    fdir = region["_fdir"]
    px = abs(transform[0])
    curve = region["width_curve"]["code"]
    flood_method = region["flood_method"]
    # HR4 additionally needs the region's NOAA Atlas 14 24-h/50-y depth; a
    # coastal config without it stays a documented fallback, never a guess.
    flood_on = (flood_method == "sir2014_hr1"
                or (flood_method == "sir2014_hr4"
                    and region.get("i24h50y_in") is not None))
    est, bch = region["estuary_ref"], region["beach_ref"]

    cols = {p: [np.nan] * n for p in ALL_PARAMS}
    extras = {k: [np.nan] * n for k in
              ("channel_width_m", "road_access_m", "bank_slope_deg",
               "bank_slope_source")}
    counts = {p: 0 for p in ALL_PARAMS}

    # Candidate-only perpendicular cross sections avoid downloading a region-
    # wide ~1 m raster. The official NC service provides statewide lidar DEM
    # coverage; outside NC, and for any unavailable profile, keep the documented
    # 10 m 3DEP gradient rather than pretending a high-resolution measurement.
    bank_lidar = [None] * n
    if region["state"] == "NC":
        log(slug, "NC OneMap/DPS 3.125-ft lidar bank cross-sections (batched, cached)...")
        bank_lidar = compute_bank_slopes_nc_lidar(cands, region["_streams"])
        log(slug, f"  high-resolution bank profiles: "
                  f"{sum(v is not None for v in bank_lidar)}/{n}")

    # Parcels: Durham keeps its dedicated county endpoint (original wiring);
    # every other NC region uses the NC OneMap statewide layer (fix-pass-2
    # Phase 2, P1.4). Out-of-state regions stay on the documented fallback.
    parcels_durham = region.get("parcels") == "durham"
    parcels_on = parcels_durham or region["state"] == "NC"

    for i in range(n):
        row = cands.iloc[i]
        pt = row.geometry
        lat, lon = float(row["lat"]), float(row["lon"])
        da = float(row["catchment_area_km2"])
        disc = catchment_disc(pt, da)
        props = erom.get(comids[i], {})
        # StreamCat's watershed metric belongs to the snapped COMID — apply the
        # same snap guard below before trusting it (a Hudson-mainstem snap would
        # otherwise assign whole-basin imperviousness to a Bronx street stream).
        _erom_da_pre = rs.erom_drainage_km2(props)
        _snap_pre = (_erom_da_pre is None) or (da <= 0) or (0.4 <= _erom_da_pre / da <= 10.0)
        imp = imperv.get(comids[i]) if _snap_pre else None

        # generation
        if bg is not None:
            d = rg.area_weighted(bg, disc, "density")
            if d is not None:
                cols["population_density"][i] = round(d, 1); counts["population_density"] += 1
            ej = rg.area_weighted(bg, disc, "demo_index")
            if ej is not None:
                cols["ej_index"][i] = round(ej, 4); counts["ej_index"] += 1
        if imp is not None:
            cols["impervious_pct"][i] = round(imp, 2); counts["impervious_pct"] += 1
            cols["runoff_coeff_C"][i] = round(estimate_runoff_coefficient(imp), 4)
            counts["runoff_coeff_C"] += 1
        if drive is not None:
            rd = safe_call(get_road_density, disc, da, bbox, utm_crs=utm, default=None)
            if rd is not None:
                cols["road_density_km_km2"][i] = round(rd, 3); counts["road_density_km_km2"] += 1
        if tri_raw is not None:
            cols["tri_facility_density"][i] = round(float(tri.within(disc).sum()) / max(da, 0.01), 4)
            counts["tri_facility_density"] += 1
        if npdes_raw is not None:
            cols["npdes_points"][i] = int(npdes.within(disc).sum()); counts["npdes_points"] += 1
        if cso_raw is not None:
            cols["cso_density"][i] = round(inverse_distance_score(pt, cso, 500), 4)
            counts["cso_density"] += 1
        if litter_raw is not None:
            cols["litter_complaint_density"][i] = round(
                rg.litter_density_from_points(disc, da, litter), 4)
            counts["litter_complaint_density"] += 1

        # flow — SYMMETRIC snap guard: reject the COMID when its drainage is far
        # smaller (minor tributary) OR far larger (site snapped to a tidal/major
        # mainstem — NYC sites next to the Hudson inherited basin-scale flows and
        # basin-wide imperviousness before this upper bound). 0.4x–10x keeps
        # legitimate confluence mismatches, drops orders-of-magnitude wrong snaps.
        q = rs.erom_mean_q_cfs(props)
        erom_da = rs.erom_drainage_km2(props)
        snap_ok = (erom_da is None) or (da <= 0) or (0.4 <= erom_da / da <= 10.0)
        if q is not None and snap_ok:
            cols["usgs_mean_q_cfs"][i] = round(q, 4); counts["usgs_mean_q_cfs"] += 1
        cv = rs.erom_seasonal_cv(props)
        if cv is not None and snap_ok:
            cols["seasonal_cv"][i] = round(cv, 4); counts["seasonal_cv"] += 1
        cols["stream_order"][i] = int(row.get("stream_order", 2)); counts["stream_order"] += 1
        cols["catchment_area_km2"][i] = round(da, 4); counts["catchment_area_km2"] += 1
        if flood_on:
            if flood_method == "sir2014_hr1":
                q10 = rg.flood_q10_hr1(da, imp if imp is not None else 0.0)
            else:
                q10 = rg.flood_q10_hr4(da, imp if imp is not None else 0.0,
                                       region["i24h50y_in"])
            if q10 is not None:
                cols["flood_q10_cfs"][i] = round(q10, 2); counts["flood_q10_cfs"] += 1

        width = rg.regional_width_m(da, curve)
        if width is not None:
            extras["channel_width_m"][i] = round(width, 2)
            cols["channel_width_score"][i] = channel_width_score(width)
            counts["channel_width_score"] += 1

        # velocity: Manning from the region DEM, continuity from the site's own
        # EROM flow scaled by the DEM/NHD drainage ratio (the shipped M4 logic,
        # fed region-correct inputs).
        vel = None
        if q is not None and snap_ok and width is not None:
            vel = safe_call(
                compute_flow_velocity,
                int(row["pixel_row"]), int(row["pixel_col"]), dem_arr, transform,
                width, q, fdir=fdir, catchment_area_km2=da,
                ref_catchment_km2=(erom_da or da), default=None)
        if vel is not None:
            cols["flow_velocity_ms"][i] = round(float(vel), 4); counts["flow_velocity_ms"] += 1
            cols["velocity_feasibility"][i] = velocity_feasibility(float(vel))
            counts["velocity_feasibility"] += 1

        # impact
        cols["estuary_dist_km"][i] = round(rg.haversine_km(lat, lon, est["lat"], est["lon"]), 2)
        counts["estuary_dist_km"] += 1
        cols["beach_dist_km"][i] = round(rg.haversine_km(lat, lon, bch["lat"], bch["lon"]), 2)
        counts["beach_dist_km"] += 1
        tour = safe_call(get_tourism_amenity_density, pt, 2, bbox, utm_crs=utm, default=None)
        if tour is not None:
            cols["tourism_amenity_density"][i] = round(float(tour), 4)
            counts["tourism_amenity_density"] += 1
        # superfund: shipped inverse-distance curve (500 m half-decay) over the
        # SEMS state inventory. A 0.0 here is a COMPUTED zero (no site in
        # range), not a fallback.
        if sems_raw is not None:
            cols["superfund_score"][i] = round(inverse_distance_score(pt, sems, 500), 4)
            counts["superfund_score"] += 1
        # protected areas: shipped PAD-US proximity math on the local 4.1 clip.
        if padus is not None:
            sc = safe_call(protected_area_score_from_gdf, pt, padus, default=None)
            if sc is not None:
                cols["protected_area_score"][i] = round(float(sc), 4)
                counts["protected_area_score"] += 1
        # drinking-water intakes: shipped exp(-d/10km) sum within 50 km over
        # SWAP surface intakes (NC-only layer).
        if intakes_gdf is not None:
            cols["water_intake_score"][i] = round(
                water_intake_score(pt, intakes_gdf), 4)
            counts["water_intake_score"] += 1

        # feasibility
        if drive is not None and drive.get("nodes_utm") is not None and not drive["nodes_utm"].empty:
            dist = float(drive["nodes_utm"].geometry.distance(pt).min())
            extras["road_access_m"][i] = round(dist, 1)
            cols["road_access_score"][i] = road_access_score(dist); counts["road_access_score"] += 1
        slope_info = bank_lidar[i]
        slope = slope_info["slope_deg"] if slope_info is not None else None
        slope_source = "nc-dps-lidar-dem03-cross-section"
        if slope is None:
            slope = safe_call(compute_bank_slope, int(row["pixel_row"]),
                              int(row["pixel_col"]), dem_arr, px, default=None)
            slope_source = "usgs-3dep-10m-gradient-fallback"
        if slope is not None:
            extras["bank_slope_deg"][i] = round(float(slope), 2)
            extras["bank_slope_source"][i] = slope_source
            cols["bank_slope_score"][i] = bank_slope_score(float(slope))
            counts["bank_slope_score"] += 1
        if nbi_gdf is not None:
            cols["bridge_proximity_bonus"][i] = bridge_proximity_bonus(pt, nbi_gdf)
            counts["bridge_proximity_bonus"] += 1
        if parcels_on:
            owner = (rs.parcel_owner_at(lat, lon) if parcels_durham
                     else rs.nc_parcel_owner_at(lat, lon))
            if owner is not None:
                cols["land_ownership"][i] = rs.land_ownership_from_owner(owner)
                counts["land_ownership"] += 1

    # Missing-data values must not trip hard gates (NaN > 0 is False, so a site
    # with an unfetchable parcel owner or no EROM flow would be silently GATED
    # OUT for missing data). Apply the model's own documented fallback constants
    # instead, so gates only ever fire on real values — matching shipped behavior.
    n_vel_fb = 0
    n_owner_fb = 0
    for i in range(n):
        if np.isnan(cols["flow_velocity_ms"][i]):
            cols["flow_velocity_ms"][i] = 0.5            # shipped compute_flow_features default
            cols["velocity_feasibility"][i] = velocity_feasibility(0.5)
            n_vel_fb += 1
        if parcels_on and np.isnan(cols["land_ownership"][i]):
            cols["land_ownership"][i] = 0.5              # shipped "unknown" ownership
            n_owner_fb += 1
    if n_vel_fb:
        log(slug, f"  velocity fallback (0.5 m/s, shipped default) for {n_vel_fb} sites lacking EROM/snap")

    # fallback constants where a whole column stayed NaN-by-design
    fallback_notes = {}
    if tri_raw is None:
        fallback_notes["tri_facility_density"] = (
            0.0, f"EPA DataMap TRI unavailable for states={source_states_label}")
    if npdes_raw is None:
        fallback_notes["npdes_points"] = (
            0.0, "EPA ECHO national NPDES bulk archive unavailable — documented fallback")
    if cso_raw is None:
        fallback_notes["cso_density"] = (
            0.0, "EPA ECHO national CSO bulk archive unavailable — documented fallback")
    if litter_raw is None:
        fallback_notes["litter_complaint_density"] = (
            0.0, ("configured official municipal litter feed unavailable for this run"
                  if litter_key else
                  "no public machine-readable litter/illegal-dumping feed for this region"))
    if sems_raw is None:
        fallback_notes["superfund_score"] = (
            0.0, "EPA Envirofacts SEMS unreachable for this run — documented fallback")
    if padus is None:
        fallback_notes["protected_area_score"] = (
            0.0, (f"PAD-US 4.1 state package(s) and official feature service "
                  f"unavailable for states={source_states_label}"))
    if intakes_gdf is None:
        fallback_notes["water_intake_score"] = (
            0.0, ("no public surface-intake layer wired outside NC (SWAP layer is NC-only)"
                  if region["state"] != "NC" else
                  "NC OneMap SWAP intake layer unreachable for this run — documented fallback"))
    if counts["tourism_amenity_density"] == 0:
        fallback_notes["tourism_amenity_density"] = (
            0.0, "OSM leisure/tourism query unavailable — documented fallback")
    if counts["road_density_km_km2"] == 0:
        fallback_notes["road_density_km_km2"] = (
            np.nan, "OSM drive-network query unavailable — documented null fallback")
    if counts["road_access_score"] == 0:
        fallback_notes["road_access_score"] = (
            np.nan, "OSM drive-network query unavailable — documented null fallback")
    if nbi_raw is None:
        fallback_notes["bridge_proximity_bonus"] = (
            0.0, "FHWA/BTS NBI query unavailable — documented 0.0 fallback")
    for p, (val, why) in fallback_notes.items():
        cols[p] = [val] * n
        mark(p, "fallback", why)
    if not parcels_on:
        cols["land_ownership"] = [0.5] * n
        mark("land_ownership", "fallback",
             "no parcel integration outside NC (statewide layer is NC OneMap) — unknown 0.5")
    if not flood_on:
        mark("flood_q10_cfs", "fallback",
             f"flood regression not valid here ({region['notes'][:60]}...) — NaN, dropped by constant-column handling")

    gdf = cands.copy()
    for p, vals in cols.items():
        gdf[p] = vals
    for k, vals in extras.items():
        gdf[k] = vals

    # Navigability gate input (fix-pass-2 Phase 3): distance to the nearest
    # USACE NWN navigable segment. NaN when no NWN clip is on disk OR when no
    # navigable water exists near this bbox — in both cases the hard gate
    # stays inert for the row (gates only fire on real values). The gate
    # itself lives in core.scoring.apply_hard_gates (NAVIGABLE_GATE_M).
    nwn = rs.nwn_navigable_union(bbox, utm, state_abbr=region["state"])
    if nwn is None:
        gdf["navigable_dist_m"] = np.nan
        mark("navigable_dist_m", "fallback",
             f"no USACE NWN clip covering state={region['state']} — navigability gate inert")
    elif nwn.is_empty:
        gdf["navigable_dist_m"] = np.nan
        mark("navigable_dist_m", "real",
             "USACE/BTS NWN (NTAD): no navigable segment within this bbox "
             "(+2 km) — computed absence, gate passes all sites", n_real=n)
    else:
        gdf["navigable_dist_m"] = [round(float(d), 1)
                                   for d in gdf.geometry.distance(nwn)]
        mark("navigable_dist_m", "real",
             "USACE/BTS National Waterway Network lines (NTAD, retrieved "
             "2026-07-10), distance to nearest navigable segment", n_real=n)

    # provenance for the real params
    real_sources_doc = {
        "population_density": "Census ACS 2022 B01003 block groups (bbox, multi-county), area-weighted",
        "ej_index": "Census ACS C17002+B03002 two-component demographic index, percentile-ranked within region",
        "impervious_pct": "EPA StreamCat pctimp2019 (per NHDPlus watershed)",
        "runoff_coeff_C": "derived from real impervious_pct (C=0.05+0.009*I)",
        "road_density_km_km2": "OSM drive network (osmnx, cached) clipped per catchment",
        "tri_facility_density": (f"EPA DataMap TRI open facilities states={source_states_label} "
                                 "(preferred decimal or packed-DMS recovery), per catchment"),
        "npdes_points": (f"EPA ECHO active NPDES outfalls states={source_states_label} "
                         "(types NPD/GPC/NGP; statuses ADC/EFF/EXP), per catchment"),
        "cso_density": (f"EPA ECHO open CSO/TCS outfall PF coordinates "
                        f"states={source_states_label}, Cauchy proximity"),
        "litter_complaint_density": (
            f"{rg.litter_source_label(litter_key)}; complaint distance to catchment "
            f"decays as 1/(1+(d/{rg.LITTER_HALF_DECAY_M:g}m)^2), divided by "
            "catchment km²" if litter_raw is not None else None),
        "usgs_mean_q_cfs": "NHDPlus EROM qe_ma per COMID (snap-guarded)",
        "seasonal_cv": "CV of NHDPlus EROM monthly flows per COMID",
        "flood_q10_cfs": (
            None if not flood_on else
            "USGS SIR 2014-5030 Table 7 HR1 (NC Piedmont)"
            if flood_method == "sir2014_hr1" else
            "USGS SIR 2014-5030 Table 7 HR4 (NC Coastal Plain; I24H50Y from "
            "NOAA Atlas 14 at region center)"),
        "channel_width_score": f"Bieger 2015 {curve} width curve on DEM drainage area → shipped width curve",
        "flow_velocity_ms": "Manning (region DEM/D8) blended with EROM continuity (shipped M4 logic)",
        "velocity_feasibility": "shipped step curve on the computed velocity",
        "stream_order": "DEM stream-network confluence heuristic (pipeline)",
        "catchment_area_km2": "DEM flow accumulation (pipeline)",
        "estuary_dist_km": f"haversine to region ref: {est['label']}",
        "beach_dist_km": f"haversine to region ref: {bch['label']}",
        "tourism_amenity_density": "OSM leisure/tourism features per 2 km radius",
        "road_access_score": "OSM drive network nearest-node distance",
        "bank_slope_score": (
            "NC OneMap / NC DPS DEM03 statewide lidar-derived 3.125-ft (0.953 m) "
            "elevations (perpendicular 50 m transect; robust 5 m bank-rise "
            "metric); unavailable profiles use the region 10 m 3DEP "
            "DEM-gradient fallback" if region["state"] == "NC" else
            "USGS 3DEP 10 m DEM local gradient; NC lidar source is outside this region"),
        "bridge_proximity_bonus": "FHWA/BTS NBI (NTAD), 50 m proximity",
        "superfund_score": (f"EPA DataMap SEMS active sites states={source_states_label} "
                            "(non-archived, georeferenced), shipped 500 m "
                            "inverse-distance curve") if sems_raw is not None else None,
        "protected_area_score": (f"USGS PAD-US 4.1 {padus_source_kind}; "
                                 f"states={source_states_label}; shipped "
                                 "designation-weighted proximity curve")
                                 if padus is not None else None,
        "water_intake_score": ("NC OneMap / NC DEQ SWAP public water supply sources "
                               "(source_typ='Surface Water'), shipped exp(-d/10km) "
                               "curve") if intakes_gdf is not None else None,
    }
    if parcels_on:
        real_sources_doc["land_ownership"] = (
            "Durham County parcels PROPERTY_OWNER" if parcels_durham
            else "NC OneMap statewide parcels (NC Parcels Transformer) ownname")
    for p, src in real_sources_doc.items():
        if src is None or p in prov:
            continue
        record_measured_provenance(
            prov, p, src, counts.get(p, 0), n,
            MEASURED_FALLBACK_REASONS.get(
                p, f"{src} returned no usable per-site measurement"),
        )
    if "flow_velocity_ms" in prov:
        prov["flow_velocity_ms"]["n_fallback"] = n_vel_fb
    if "velocity_feasibility" in prov:
        prov["velocity_feasibility"]["n_fallback"] = n_vel_fb
    if parcels_on and "land_ownership" in prov:
        prov["land_ownership"]["n_fallback"] = n_owner_fb
    if "bank_slope_score" in prov:
        prov["bank_slope_score"].update({
            "n_nc_lidar_3_125ft": sum(v is not None for v in bank_lidar),
            "n_3dep_10m_fallback": sum(
                src == "usgs-3dep-10m-gradient-fallback"
                for src in extras["bank_slope_source"]),
            "source_mosaics": sorted({
                source for info in bank_lidar if info is not None
                for source in info["sources"]
            }),
        })

    # P0 Option A (owner-confirmed 2026-07-09): name the SCORED form of velocity
    # in provenance so the two velocity constructs are distinguishable. The Flow
    # family consumes flow_velocity_ms through the peaked transport-favorability
    # Gaussian; the raw value feeds velocity_feasibility and the 3.0 m/s gate.
    # Documented in model.json curves.* and dashboard/docs/documentation.md
    # ("Why velocity appears twice"); both curves are drift-guarded.
    prov["velocity_transport_favorability"] = {
        "kind": "derived",
        "source": ("scored form of flow_velocity_ms inside the Flow family — "
                   "peaked Gaussian exp(-((v-0.9)/0.6)^2), "
                   "model.json curves.velocity_transport_favorability; "
                   "construct: transport/delivery favorability, deliberately "
                   "distinct from velocity_feasibility (device operability)"),
        "n_real": counts.get("flow_velocity_ms", 0),
        "n_sites": n,
    }
    return gdf, prov


def write_zero_region(region, pre_gate, reason, mask_stats=None):
    """Write a valid, empty region file + return an index entry for a town that
    yielded no deployable site. This is a CORRECT outcome (recorded, not retried),
    never a reason to loosen a gate or fabricate a site."""
    slug = region["slug"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    provenance = {"n_parameters": len(ALL_PARAMS), "varying": [], "constant": [],
                  "parameters": {}, "hard_gate_removed": pre_gate,
                  "candidates_pre_gate": pre_gate, "zero_reason": reason}
    if mask_stats is not None:
        provenance["stream_mask"] = {
            "buffer_m": STREAM_MASK_BUFFER_M,
            "sources": ["osm-overpass (all waterway ways)",
                        "usgs-nhdplus flowlines"],
            "pre_mask": int(mask_stats["pre_mask"]),
            "removed": int(mask_stats["removed"]),
        }
    doc = {
        "type": "FeatureCollection",
        "note": (f"GRIME region '{region['name']}' — real-data pipeline produced "
                 f"0 deployable candidate sites. {reason}. This is an honest zero "
                 f"(the model targets small urban waterways and does not force "
                 f"sites where none qualify), not a failure."),
        "region": {k: region.get(k) for k in
                   ("slug", "name", "state", "bbox", "center", "utm_epsg",
                    "width_curve", "flood_method", "notes")},
        "provenance": provenance,
        "features": [],
    }
    (OUT_DIR / f"{slug}.geojson").write_text(json.dumps(_json_safe(doc), indent=1, sort_keys=True, allow_nan=False) + "\n")
    log(slug, f"ZERO candidates — {reason}")
    return {
        "slug": slug, "name": region["name"], "state": region.get("state"),
        "bbox": region["bbox"], "center": region["center"],
        "tier": region.get("tier", "metro"),
        "site_count": 0, "candidates_pre_gate": pre_gate, "gate_removed": pre_gate,
        "params_varying": 0,
        "scored_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "ok", "zero_reason": reason, "top_site": None,
    }


def run_region(region, defaults):
    _configure_osmnx()          # real pipeline run → configure osmnx now (lazy)
    slug = region["slug"]
    utm = f"EPSG:{region['utm_epsg']}"
    work = WORK_DIR / slug
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    log(slug, f"pipeline: DEM->streams->candidates (bbox={region['bbox']}, {utm})")
    # Fetch + condition the DEM once; we keep the hydrology tuple because the
    # wiring stage needs dem/fdir/transform for velocity and bank slope.
    from core.pipeline import fetch_dem, process_hydrology
    dem = fetch_dem(tuple(region["bbox"]), resolution=defaults["resolution_m"], utm_crs=utm)
    hydrology = process_hydrology(dem, tuple(region["bbox"]))
    grid, fdir, acc, elevation, transform = hydrology
    stream_gdf, cands, _ = run_pipeline(
        bbox=tuple(region["bbox"]), hydrology=hydrology, return_hydrology=True,
        threshold=defaults["threshold_cells"], spacing_m=defaults["spacing_m"],
        output_dir=str(work),                      # NEVER mock_data/ — frozen files
        utm_crs=utm,
    )
    if cands is None or len(cands) == 0:
        # A small/rural town with no waterway above the ~2 km² channel threshold
        # is a CORRECT zero result — record it, don't retry, don't loosen the gate.
        return write_zero_region(region, 0,
            "no waterway above the 2 km² channel threshold in this bbox")
    log(slug, f"candidates: {len(cands)}")

    # ── STREAM MASK: confine candidates to real mapped waterways ──
    mask = stream_mask_union(region)
    if mask is None:
        raise RuntimeError("stream-mask geometry sources unavailable "
                           "(Overpass/NHD) — refusing to run unmasked")
    n_pre_mask = len(cands)
    cands = apply_stream_mask(cands, mask)
    mask_removed = n_pre_mask - len(cands)
    log(slug, f"stream mask (±{STREAM_MASK_BUFFER_M:.0f} m of mapped OSM/NHD "
              f"waterways): kept {len(cands)}/{n_pre_mask}")
    if len(cands) == 0:
        # Honest zero: every DEM flow path in this bbox is an artifact with no
        # mapped channel within the buffer (gridded metro) — no deployable site.
        return write_zero_region(region, 0,
            f"all {n_pre_mask} DEM candidate sites lie >{STREAM_MASK_BUFFER_M:.0f} m "
            "from any mapped OSM/NHD waterway (stream mask)",
            mask_stats={"pre_mask": n_pre_mask, "removed": mask_removed})

    region = dict(region)
    region["_dem"], region["_transform"], region["_fdir"] = elevation, transform, fdir
    region["_streams"] = stream_gdf

    wired, prov = wire_region_parameters(cands, region)

    log(slug, "scoring (shipped compute_composite_score + Dirichlet n=%d)..." % defaults["sensitivity_n"])
    n_before = len(wired)
    np.random.seed(defaults["seed"])
    scored = sensitivity_analysis(wired, n_perturbations=defaults["sensitivity_n"])
    scored = scored.reset_index(drop=True)
    scored["rank"] = scored.index + 1
    gated_out = n_before - len(scored)
    if len(scored) == 0:
        # Every candidate failed the shipped hard gates (too fast / too wide /
        # confirmed private). Also a correct zero — never loosen a gate.
        return write_zero_region(region, n_before,
            f"all {n_before} candidate sites removed by the hard gates "
            "(velocity/width/ownership) — no deployable site in this bbox",
            mask_stats={"pre_mask": n_pre_mask, "removed": mask_removed})
    log(slug, f"scored {len(scored)}/{n_before} sites (hard gates removed {gated_out}); "
              f"composite {scored['composite_score'].min():.1f}-{scored['composite_score'].max():.1f}")

    present = [c for c in ALL_PARAMS if c in scored.columns]
    varying = sorted(c for c in present if scored[c].nunique(dropna=False) > 1)
    constant = sorted(c for c in present if c not in varying)

    out_gdf = scored.to_crs(WGS84)
    feats = []
    for _, r in out_gdf.iterrows():
        props = {}
        for k, v in r.drop(labels="geometry").items():
            if isinstance(v, float):
                props[k] = None if np.isnan(v) else round(v, 4)
            elif isinstance(v, (np.integer,)):
                props[k] = int(v)
            else:
                props[k] = v
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point",
                                   "coordinates": [round(r.geometry.x, 6), round(r.geometry.y, 6)]},
                      "properties": props})
    doc = {
        "type": "FeatureCollection",
        "note": (f"GRIME region '{region['name']}' — real-data pipeline (DEM→streams→candidates→"
                 f"stream mask (sites confined to ±{STREAM_MASK_BUFFER_M:.0f} m of mapped OSM/NHD "
                 f"waterways)→region-appropriate real parameters→shipped scoring, math unchanged). "
                 f"Width curve: {region['width_curve']['source']}. Flood: "
                 f"{'SIR 2014-5030 HR1' if region['flood_method'] == 'sir2014_hr1' else 'SIR 2014-5030 HR4 (I24H50Y: NOAA Atlas 14)' if region['flood_method'] == 'sir2014_hr4' and region.get('i24h50y_in') is not None else 'not applicable here (documented fallback)'}. "
                 f"{region['notes']}"),
        "region": {k: region[k] for k in
                   ("slug", "name", "state", "bbox", "center", "utm_epsg",
                    "width_curve", "flood_method", "estuary_ref", "beach_ref", "notes")},
        "provenance": {"n_parameters": len(ALL_PARAMS), "varying": varying,
                       "constant": constant, "parameters": prov,
                       "hard_gate_removed": gated_out, "candidates_pre_gate": n_before,
                       # auditable navigability exclusions (Phase 3 gate)
                       "gated_navigable": int(
                           (wired["navigable_dist_m"] <= NAVIGABLE_GATE_M).sum()
                           if "navigable_dist_m" in wired.columns else 0),
                       "stream_mask": {"buffer_m": STREAM_MASK_BUFFER_M,
                                       "sources": ["osm-overpass (all waterway ways)",
                                                   "usgs-nhdplus flowlines"],
                                       "pre_mask": n_pre_mask,
                                       "removed": mask_removed}},
        "features": feats,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{slug}.geojson"
    assert out_path.name not in PROTECTED
    out_path.write_text(json.dumps(_json_safe(doc), indent=1, sort_keys=True, allow_nan=False) + "\n")
    dt = time.time() - t0
    log(slug, f"WROTE {out_path} ({len(feats)} sites, {len(varying)}/27 varying) in {dt/60:.1f} min")

    top = feats[0]["properties"] if feats else {}
    return {
        "slug": slug, "name": region["name"], "state": region["state"],
        "bbox": region["bbox"], "center": region["center"],
        "tier": region.get("tier", "metro"),
        "site_count": len(feats), "candidates_pre_gate": n_before,
        "gate_removed": gated_out, "mask_removed": mask_removed,
        "params_varying": len(varying),
        "scored_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "ok", "runtime_min": round(dt / 60, 1),
        "top_site": {"rank": 1, "segment_id": top.get("segment_id"),
                     "composite": top.get("composite_score"),
                     "lat": top.get("lat"), "lon": top.get("lon")} if feats else None,
    }


def load_index():
    if INDEX.exists():
        try:
            return json.loads(INDEX.read_text())
        except Exception:
            pass
    return {"generated": None, "regions": []}


def save_index(idx):
    idx["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(_json_safe(idx), indent=1, sort_keys=True, allow_nan=False) + "\n")


def upsert(idx, entry):
    idx["regions"] = [r for r in idx["regions"] if r["slug"] != entry["slug"]]
    idx["regions"].append(entry)
    # stable order: config order
    order = {r["slug"]: i for i, r in enumerate(json.loads(CONFIG.read_text())["regions"])}
    idx["regions"].sort(key=lambda r: order.get(r["slug"], 999))
    save_index(idx)


def run_worker(args):
    """Worker mode: run one region in-process and upsert its index entry.
    A per-region timeout is enforced by the SUPERVISOR (separate process), so a
    hang here can be killed without taking down the batch."""
    cfg = json.loads(CONFIG.read_text())
    region = next((r for r in cfg["regions"] if r["slug"] == args.only), None)
    if region is None:
        sys.exit(f"unknown slug {args.only}")
    out_path = OUT_DIR / f"{region['slug']}.geojson"
    if out_path.exists() and not args.force:
        log(region["slug"], "exists — skipping (resumable)")
        return
    try:
        entry = run_region(region, cfg["defaults"])
    except Exception as e:
        traceback.print_exc()
        entry = {"slug": region["slug"], "name": region["name"], "state": region.get("state"),
                 "bbox": region["bbox"], "center": region["center"],
                 "tier": region.get("tier", "metro"), "site_count": 0, "params_varying": 0,
                 "scored_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                 "status": f"failed: {type(e).__name__}: {str(e)[:140]}"}
    upsert(load_index(), entry)


def scope_supervisor_regions(regions, tier=None, start_after=None, limit=None):
    """Apply deterministic supervisor scope/resume filters."""
    scoped = list(regions)
    if tier:
        scoped = [r for r in scoped if r.get("tier", "metro") == tier]
    if start_after:
        slugs = [r["slug"] for r in scoped]
        if start_after not in slugs:
            raise ValueError(f"unknown --start-after slug {start_after}")
        scoped = scoped[slugs.index(start_after) + 1:]
    if limit:
        scoped = scoped[:limit]
    return scoped


def supervise(args):
    """Supervisor mode: iterate the config, run each region as a SUBPROCESS with a
    hard wall-clock timeout, gentle inter-region pacing + jitter (Overpass is the
    scaling bottleneck), skip already-done regions (resume), and collect failures
    for a clean retry pass instead of aborting the batch."""
    import random
    import subprocess

    cfg = json.loads(CONFIG.read_text())
    try:
        regions = scope_supervisor_regions(
            cfg["regions"], getattr(args, "tier", None),
            getattr(args, "start_after", None), getattr(args, "limit", None))
    except ValueError as exc:
        sys.exit(str(exc))
    timeout = args.timeout
    pace_lo, pace_hi = args.pace, args.pace + args.jitter

    # A failed forced rebuild leaves the PREVIOUS geojson on disk (serving
    # continuity) with status=failed in the index. Resume must treat such a
    # region as NOT done, or it stays stuck on the stale file forever.
    _failed = {r["slug"] for r in load_index().get("regions", [])
               if not str(r.get("status", "")).startswith("ok")}

    def already_done(slug):
        if getattr(args, "force", False):
            return False
        return (OUT_DIR / f"{slug}.geojson").exists() and slug not in _failed

    def run_one(slug):
        cmd = [sys.executable, os.path.abspath(__file__), "--only", slug]
        if getattr(args, "force", False):
            cmd.append("--force")
        try:
            r = subprocess.run(cmd, timeout=timeout)
            return r.returncode == 0
        except subprocess.TimeoutExpired:
            log(slug, f"TIMEOUT after {timeout}s — killed, will retry")
            upsert(load_index(), {
                "slug": slug, "name": next(x["name"] for x in cfg["regions"] if x["slug"] == slug),
                "state": "NC", "bbox": next(x["bbox"] for x in cfg["regions"] if x["slug"] == slug),
                "center": next(x["center"] for x in cfg["regions"] if x["slug"] == slug),
                "tier": "town", "site_count": 0, "params_varying": 0,
                "scored_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "status": f"failed: timeout>{timeout}s"})
            return False

    pending = [r for r in regions if not already_done(r["slug"])]
    print(f"[supervisor] {len(regions)} in scope · {len(regions)-len(pending)} already done · "
          f"{len(pending)} to run · timeout={timeout}s pace={pace_lo}-{pace_hi}s", flush=True)

    for i, region in enumerate(pending):
        slug = region["slug"]
        print(f"[supervisor] {i+1}/{len(pending)} → {slug}", flush=True)
        run_one(slug)
        if i < len(pending) - 1:
            time.sleep(random.uniform(pace_lo, pace_hi))   # gentle on Overpass

    # one retry pass over anything still failed/absent
    idx = load_index()
    failed = [r["slug"] for r in idx["regions"]
              if not str(r["status"]).startswith("ok") and r["slug"] in {x["slug"] for x in regions}]
    failed += [r["slug"] for r in regions if not already_done(r["slug"])
               and r["slug"] not in {e["slug"] for e in idx["regions"]}]
    failed = sorted(set(failed))
    if failed and not args.no_retry:
        print(f"[supervisor] retry pass over {len(failed)} failed/absent: {failed[:10]}...", flush=True)
        for slug in failed:
            if already_done(slug):
                continue
            run_one(slug)
            time.sleep(random.uniform(pace_lo, pace_hi))

    idx = load_index()
    ok = sum(1 for r in idx["regions"] if str(r["status"]).startswith("ok"))
    zero = sum(1 for r in idx["regions"] if r.get("site_count") == 0 and str(r["status"]).startswith("ok"))
    fail = [r["slug"] for r in idx["regions"] if not str(r["status"]).startswith("ok")]
    print(f"\n[supervisor] done: {ok} ok ({zero} zero-candidate) · {len(fail)} failed: {fail}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="worker: run a single slug")
    ap.add_argument("--force", action="store_true", help="re-run even if output exists")
    ap.add_argument("--supervise", action="store_true",
                    help="supervisor: subprocess-per-region with timeout + retry + pacing")
    ap.add_argument("--tier", default=None, help="supervise only this tier (e.g. town)")
    ap.add_argument("--limit", type=int, default=None, help="supervise only the first N regions")
    ap.add_argument("--timeout", type=int, default=1200, help="per-region wall-clock seconds")
    ap.add_argument("--pace", type=float, default=4.0, help="min inter-region sleep seconds")
    ap.add_argument("--jitter", type=float, default=6.0, help="added random pacing seconds")
    ap.add_argument("--no-retry", action="store_true")
    ap.add_argument("--start-after", default=None,
                    help="supervisor: resume scoped config strictly after this slug")
    ap.add_argument("--repair-provenance", action="store_true",
                    help="metadata-only: normalize provenance in existing outputs")
    args = ap.parse_args()

    if args.repair_provenance:
        repair_region_provenance()
    elif args.supervise:
        supervise(args)
    elif args.only:
        run_worker(args)
    else:
        # default: supervise the whole config
        args.supervise = True
        supervise(args)


if __name__ == "__main__":
    main()
