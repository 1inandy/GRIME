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
    _configure_osmnx._done = True


from core import WGS84, osm_drive_graph, inverse_distance_score, safe_call
from core.flow import (
    estimate_runoff_coefficient, compute_flow_velocity, velocity_feasibility,
)
from core.feasibility import (
    road_access_score, channel_width_score, bank_slope_score, compute_bank_slope,
    bridge_proximity_bonus,
)
from core.generation import get_road_density
from core.impact import get_tourism_amenity_density
from core.scoring import compute_composite_score, sensitivity_analysis, ALL_PARAMS
from core.pipeline import run_pipeline
from core import real_sources as rs
from core import region_sources as rg

CONFIG = Path(_ROOT) / "scripts/regions.json"
OUT_DIR = Path(_ROOT) / "mock_data/regions"
WORK_DIR = Path(_ROOT) / "cache/regions_work"
INDEX = OUT_DIR / "index.json"

PROTECTED = {"candidates.geojson", "candidates_v2.geojson", "places.json"}


def log(slug, msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{slug}] {msg}", flush=True)


def catchment_disc(pt_utm, area_km2):
    """The model's own per-candidate catchment proxy (matches core.scoring)."""
    area_km2 = area_km2 or 1.0
    radius_m = max(500.0, (max(area_km2, 0.01) / np.pi) ** 0.5 * 1000.0)
    return pt_utm.buffer(radius_m)


def wire_region_parameters(cands, region):
    """Attach all 27 parameters to the candidate GeoDataFrame (region UTM).
    Returns (gdf, provenance_dict). Real where fetchable; NaN/documented
    fallback where not — never fabricated."""
    utm = f"EPSG:{region['utm_epsg']}"
    bbox = tuple(region["bbox"])
    slug = region["slug"]
    n = len(cands)
    prov = {}

    def mark(param, kind, source, n_real=None):
        prov[param] = {"kind": kind, "source": source,
                       "n_real": n_real if n_real is not None else (n if kind == "real" else 0),
                       "n_sites": n}

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
    tri = rg.to_utm_points(rg.tri_points_state(region["state"], bbox), utm)
    npdes = rg.to_utm_points(rs.npdes_outfall_points(bbox), utm)
    cso_raw = rs.cso_points_nc(bbox)          # national inventory, bbox-filtered
    cso = rg.to_utm_points(cso_raw or [], utm)
    nbi_pts = rg.to_utm_points(rg.nbi_bridge_points_paged(bbox), utm)
    nbi_gdf = gpd.GeoDataFrame(geometry=nbi_pts, crs=utm) if len(nbi_pts) else None
    log(slug, f"  TRI={len(tri)} NPDES={len(npdes)} CSO={'dl-fail' if cso_raw is None else len(cso)} NBI={len(nbi_pts)}")

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
    flood_on = region["flood_method"] == "sir2014_hr1"
    est, bch = region["estuary_ref"], region["beach_ref"]

    cols = {p: [np.nan] * n for p in ALL_PARAMS}
    extras = {k: [np.nan] * n for k in
              ("channel_width_m", "road_access_m", "bank_slope_deg")}
    counts = {p: 0 for p in ALL_PARAMS}

    parcels_on = region.get("parcels") == "durham"

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
        if len(tri):
            cols["tri_facility_density"][i] = round(float(tri.within(disc).sum()) / max(da, 0.01), 4)
            counts["tri_facility_density"] += 1
        if len(npdes):
            cols["npdes_points"][i] = int(npdes.within(disc).sum()); counts["npdes_points"] += 1
        if cso_raw is not None:
            cols["cso_density"][i] = round(inverse_distance_score(pt, cso, 500), 4)
            counts["cso_density"] += 1
        # litter_complaint_density: no machine-readable source → NaN fallback

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
            q10 = rg.flood_q10_hr1(da, imp if imp is not None else 0.0)
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
        # water_intake_score / protected_area_score / superfund_score: documented fallbacks

        # feasibility
        if drive is not None and drive.get("nodes_utm") is not None and not drive["nodes_utm"].empty:
            dist = float(drive["nodes_utm"].geometry.distance(pt).min())
            extras["road_access_m"][i] = round(dist, 1)
            cols["road_access_score"][i] = road_access_score(dist); counts["road_access_score"] += 1
        slope = safe_call(compute_bank_slope, int(row["pixel_row"]), int(row["pixel_col"]),
                          dem_arr, px, default=None)
        if slope is not None:
            extras["bank_slope_deg"][i] = round(float(slope), 2)
            cols["bank_slope_score"][i] = bank_slope_score(float(slope)); counts["bank_slope_score"] += 1
        if nbi_gdf is not None:
            cols["bridge_proximity_bonus"][i] = bridge_proximity_bonus(pt, nbi_gdf)
            counts["bridge_proximity_bonus"] += 1
        if parcels_on:
            owner = rs.parcel_owner_at(lat, lon)
            if owner is not None:
                cols["land_ownership"][i] = rs.land_ownership_from_owner(owner)
                counts["land_ownership"] += 1

    # Missing-data values must not trip hard gates (NaN > 0 is False, so a site
    # with an unfetchable parcel owner or no EROM flow would be silently GATED
    # OUT for missing data). Apply the model's own documented fallback constants
    # instead, so gates only ever fire on real values — matching shipped behavior.
    n_vel_fb = 0
    for i in range(n):
        if np.isnan(cols["flow_velocity_ms"][i]):
            cols["flow_velocity_ms"][i] = 0.5            # shipped compute_flow_features default
            cols["velocity_feasibility"][i] = velocity_feasibility(0.5)
            n_vel_fb += 1
        if parcels_on and np.isnan(cols["land_ownership"][i]):
            cols["land_ownership"][i] = 0.5              # shipped "unknown" ownership
    if n_vel_fb:
        log(slug, f"  velocity fallback (0.5 m/s, shipped default) for {n_vel_fb} sites lacking EROM/snap")

    # fallback constants where a whole column stayed NaN-by-design
    fallback_notes = {
        "litter_complaint_density": (0.0, "no machine-readable 311/litter source for this region"),
        "water_intake_score": (0.0, "no public surface-intake point layer (NC OneMap is wells-only and NC-only)"),
        "protected_area_score": (0.0, "PAD-US services unreachable; OSM proxy saturates — documented fallback"),
        "superfund_score": (0.0, "not wired (low weight; roadmap)"),
    }
    for p, (val, why) in fallback_notes.items():
        cols[p] = [val] * n
        mark(p, "fallback", why)
    if not parcels_on:
        cols["land_ownership"] = [0.5] * n
        mark("land_ownership", "fallback", "no parcel integration for this region (Durham only) — unknown 0.5")
    if not flood_on:
        mark("flood_q10_cfs", "fallback",
             f"flood regression not valid here ({region['notes'][:60]}...) — NaN, dropped by constant-column handling")

    gdf = cands.copy()
    for p, vals in cols.items():
        gdf[p] = vals
    for k, vals in extras.items():
        gdf[k] = vals

    # provenance for the real params
    real_sources_doc = {
        "population_density": "Census ACS 2022 B01003 block groups (bbox, multi-county), area-weighted",
        "ej_index": "Census ACS C17002+B03002 two-component demographic index, percentile-ranked within region",
        "impervious_pct": "EPA StreamCat pctimp2019 (per NHDPlus watershed)",
        "runoff_coeff_C": "derived from real impervious_pct (C=0.05+0.009*I)",
        "road_density_km_km2": "OSM drive network (osmnx, cached) clipped per catchment",
        "tri_facility_density": f"EPA Envirofacts TRI state={region['state']} (sign-fixed), per catchment",
        "npdes_points": "EPA ECHO NPDES outfalls (national bulk), per catchment",
        "cso_density": "EPA ECHO CSO inventory (national bulk), Cauchy proximity",
        "usgs_mean_q_cfs": "NHDPlus EROM qe_ma per COMID (snap-guarded)",
        "seasonal_cv": "CV of NHDPlus EROM monthly flows per COMID",
        "flood_q10_cfs": "USGS SIR 2014-5030 Table 7 HR1 (NC Piedmont only)" if flood_on else None,
        "channel_width_score": f"Bieger 2015 {curve} width curve on DEM drainage area → shipped width curve",
        "flow_velocity_ms": "Manning (region DEM/D8) blended with EROM continuity (shipped M4 logic)",
        "velocity_feasibility": "shipped step curve on the computed velocity",
        "stream_order": "DEM stream-network confluence heuristic (pipeline)",
        "catchment_area_km2": "DEM flow accumulation (pipeline)",
        "estuary_dist_km": f"haversine to region ref: {est['label']}",
        "beach_dist_km": f"haversine to region ref: {bch['label']}",
        "tourism_amenity_density": "OSM leisure/tourism features per 2 km radius",
        "road_access_score": "OSM drive network nearest-node distance",
        "bank_slope_score": "region DEM gradient (3x3)",
        "bridge_proximity_bonus": "FHWA/BTS NBI (NTAD), 50 m proximity",
    }
    if parcels_on:
        real_sources_doc["land_ownership"] = "Durham County parcels PROPERTY_OWNER"
    for p, src in real_sources_doc.items():
        if src is None or p in prov:
            continue
        mark(p, "real", src, n_real=counts.get(p, 0))
    return gdf, prov


def write_zero_region(region, pre_gate, reason):
    """Write a valid, empty region file + return an index entry for a town that
    yielded no deployable site. This is a CORRECT outcome (recorded, not retried),
    never a reason to loosen a gate or fabricate a site."""
    slug = region["slug"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = {
        "type": "FeatureCollection",
        "note": (f"GRIME region '{region['name']}' — real-data pipeline produced "
                 f"0 deployable candidate sites. {reason}. This is an honest zero "
                 f"(the model targets small urban waterways and does not force "
                 f"sites where none qualify), not a failure."),
        "region": {k: region.get(k) for k in
                   ("slug", "name", "state", "bbox", "center", "utm_epsg",
                    "width_curve", "flood_method", "notes")},
        "provenance": {"n_parameters": len(ALL_PARAMS), "varying": [], "constant": [],
                       "parameters": {}, "hard_gate_removed": pre_gate,
                       "candidates_pre_gate": pre_gate, "zero_reason": reason},
        "features": [],
    }
    (OUT_DIR / f"{slug}.geojson").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
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

    region = dict(region)
    region["_dem"], region["_transform"], region["_fdir"] = elevation, transform, fdir

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
            "(velocity/width/ownership) — no deployable site in this bbox")
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
                 f"region-appropriate real parameters→shipped scoring, math unchanged). "
                 f"Width curve: {region['width_curve']['source']}. Flood: "
                 f"{'SIR 2014-5030 HR1' if region['flood_method']=='sir2014_hr1' else 'not applicable here (documented fallback)'}. "
                 f"{region['notes']}"),
        "region": {k: region[k] for k in
                   ("slug", "name", "state", "bbox", "center", "utm_epsg",
                    "width_curve", "flood_method", "estuary_ref", "beach_ref", "notes")},
        "provenance": {"n_parameters": len(ALL_PARAMS), "varying": varying,
                       "constant": constant, "parameters": prov,
                       "hard_gate_removed": gated_out, "candidates_pre_gate": n_before},
        "features": feats,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{slug}.geojson"
    assert out_path.name not in PROTECTED
    out_path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    dt = time.time() - t0
    log(slug, f"WROTE {out_path} ({len(feats)} sites, {len(varying)}/27 varying) in {dt/60:.1f} min")

    top = feats[0]["properties"] if feats else {}
    return {
        "slug": slug, "name": region["name"], "state": region["state"],
        "bbox": region["bbox"], "center": region["center"],
        "tier": region.get("tier", "metro"),
        "site_count": len(feats), "candidates_pre_gate": n_before,
        "gate_removed": gated_out, "params_varying": len(varying),
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
    INDEX.write_text(json.dumps(idx, indent=1, sort_keys=True) + "\n")


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


def supervise(args):
    """Supervisor mode: iterate the config, run each region as a SUBPROCESS with a
    hard wall-clock timeout, gentle inter-region pacing + jitter (Overpass is the
    scaling bottleneck), skip already-done regions (resume), and collect failures
    for a clean retry pass instead of aborting the batch."""
    import random
    import subprocess

    cfg = json.loads(CONFIG.read_text())
    regions = cfg["regions"]
    if args.tier:
        regions = [r for r in regions if r.get("tier", "metro") == args.tier]
    if args.limit:
        regions = regions[:args.limit]
    timeout = args.timeout
    pace_lo, pace_hi = args.pace, args.pace + args.jitter

    def already_done(slug):
        return (OUT_DIR / f"{slug}.geojson").exists()

    def run_one(slug):
        cmd = [sys.executable, os.path.abspath(__file__), "--only", slug]
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
    args = ap.parse_args()

    if args.supervise:
        supervise(args)
    elif args.only:
        run_worker(args)
    else:
        # default: supervise the whole config
        args.supervise = True
        supervise(args)


if __name__ == "__main__":
    main()
