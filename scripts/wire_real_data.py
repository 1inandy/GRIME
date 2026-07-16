#!/usr/bin/env python3
"""
GRIME — wire real per-site data into the 16 constant-fallback parameters, then
re-score the SAME 147 Durham candidate sites and write candidates_v2.geojson.

HARD CONSTRAINTS honored here:
  * No weights, curves, thresholds, gates, or scoring math change. This script
    only replaces constant fallback VALUES with real per-site values, then calls
    the shipped `compute_composite_score` unchanged.
  * The frozen mock_data/candidates.geojson is READ ONLY. Output goes to
    mock_data/candidates_v2.geojson (the overwrite guard is also respected).
  * Per-site values only (never a whole-bbox statistic). Downloads are cached in
    cache/wire and retried with backoff. Every parameter records its provenance.
  * Anything that can't be made real stays a documented fallback — no fabrication.

Run:  python3 scripts/wire_real_data.py            # fetch (cached) + re-score
      python3 scripts/wire_real_data.py --no-fetch # re-score from cache only
"""
import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))
sys.path.insert(0, _ROOT)

from core import UTM_CRS, WGS84, inverse_distance_score
from core.flow import estimate_runoff_coefficient
from core.feasibility import (bank_slope_score, bridge_proximity_bonus,
                              channel_width_score, compute_bank_slopes_nc_lidar)
from core.scoring import compute_composite_score, ALL_PARAMS
from core import real_sources as rs

IN = Path("mock_data/candidates.geojson")
OUT = Path("mock_data/candidates_v2.geojson")
BBOX = (-79.05, 35.90, -78.75, 36.05)           # Ellerbe/Durham working bbox
BBOX_WIDE = (-79.6, 35.4, -78.2, 36.6)          # for distant point sources

# Which parameters this pass makes real, and the source label recorded per site.
REAL_SOURCES = {
    "impervious_pct": "EPA StreamCat pctimp2019 (per NHDPlus catchment)",
    "runoff_coeff_C": "derived from real impervious_pct (C=0.05+0.009·I)",
    "usgs_mean_q_cfs": "NHDPlus EROM qe_ma (per-COMID mean annual flow; sites whose "
                       "NHD flowline drains ≪ GRIME's DEM catchment keep the fallback)",
    "seasonal_cv": "CV of NHDPlus EROM monthly flows qe_01..12 (per COMID)",
    "flood_q10_cfs": "USGS SIR 2014-5030 Table 7 HR1 10% AEP (GRIME DEM drainage area + real impervious)",
    "channel_width_score": "Bieger 2015 Table 3 AHI width W=3.12·DA^0.415 (GRIME DEM drainage area) → shipped width curve",
    "tri_facility_density": "EPA DataMap TRI open NC facilities (preferred decimal or packed-DMS recovery) per catchment",
    "npdes_points": "EPA ECHO active NPDES NPD/GPC/NGP outfalls (bulk) per catchment",
    "cso_density": "EPA ECHO open CSO/TCS PF outfalls (truthful 0 — Durham separated sewers)",
    "land_ownership": "Durham County parcels PROPERTY_OWNER (public 1.0 / unknown 0.5)",
    "bridge_proximity_bonus": "FHWA/BTS National Bridge Inventory (NTAD) proximity",
    # fix-pass-2 Phase 1/2 additions (same shipped curves as the region runner):
    "superfund_score": "EPA DataMap SEMS active state=NC sites (non-archived, georeferenced), "
                       "shipped 500 m inverse-distance curve",
    "protected_area_score": "USGS PAD-US 4.1 NC state clip (local GDB, retrieved 2026-07-10), "
                            "shipped designation-weighted proximity curve",
    "water_intake_score": "NC OneMap / NC DEQ SWAP public water supply sources "
                          "(source_typ='Surface Water'), shipped exp(-d/10km) curve",
    "bank_slope_score": "NC OneMap / NC DPS DEM03 statewide lidar-derived "
                        "3.125-ft elevations, perpendicular 50 m cross-section "
                        "with two-bank mean of the steepest 5 m rise; documented "
                        "substitute because USGS 1 m 3DEP is not statewide",
}
# Documented, honest fallbacks (not made real this pass) with the reason.
FALLBACK_REASONS = {
    "litter_complaint_density": "Durham One Call 311 endpoint dead / not machine-readable",
}

# These parameters are intentionally carried unchanged from the June 2026
# live flagship. This pass re-scores the same frozen 147 sites; it does not
# silently relabel their existing live measurements as newly fetched.
INHERITED_SOURCES = {
    "population_density": (
        "inherited unchanged from the June 2026 live flagship: Census ACS "
        "2022 B01003 block-group population, area-weighted to the candidate "
        "catchment"),
    "road_density_km_km2": (
        "inherited unchanged from the June 2026 live flagship: OSM drive-network "
        "edge length clipped to the candidate catchment"),
    "flow_velocity_ms": (
        "inherited unchanged from the June 2026 live flagship: Manning/continuity "
        "velocity from the 10 m 3DEP DEM, D8 flow grid, channel width, and "
        "discharge input"),
    "stream_order": (
        "inherited unchanged from the June 2026 live flagship: DEM stream-network "
        "confluence-degree heuristic"),
    "catchment_area_km2": (
        "inherited unchanged from the June 2026 live flagship: 10 m USGS 3DEP "
        "D8 flow accumulation"),
    "ej_index": (
        "inherited unchanged from the June 2026 live flagship: Census ACS "
        "C17002+B03002 low-income/people-of-color demographic index"),
    "estuary_dist_km": (
        "inherited unchanged from the June 2026 live flagship: haversine distance "
        "to the Pamlico Sound estuary reference"),
    "beach_dist_km": (
        "inherited unchanged from the June 2026 live flagship: haversine distance "
        "to the Wrightsville Beach recreational reference"),
    "tourism_amenity_density": (
        "inherited unchanged from the June 2026 live flagship: OSM leisure/tourism "
        "features within 2 km"),
    "road_access_score": (
        "inherited unchanged from the June 2026 live flagship: nearest OSM "
        "drive-network node distance through the shipped access curve"),
    "velocity_feasibility": (
        "derived from inherited flow_velocity_ms through the shipped device-"
        "operability step curve"),
}


def _catchment_disc_utm(point_utm, area_km2):
    """The model's own per-candidate catchment proxy (core.scoring
    ._candidate_catchment_polygon): a disc sized by upstream catchment area."""
    area_km2 = area_km2 or 1.0
    radius_m = max(500.0, (max(area_km2, 0.01) / np.pi) ** 0.5 * 1000.0)
    return point_utm.buffer(radius_m)


def load_v1():
    data = json.loads(IN.read_text())
    rows = []
    for f in data["features"]:
        p = dict(f["properties"])
        lon, lat = f["geometry"]["coordinates"]
        p["lon"], p["lat"] = lon, lat
        p["geometry"] = Point(lon, lat)
        rows.append(p)
    gdf = gpd.GeoDataFrame(rows, crs=WGS84)
    return gdf, data.get("note", "")


def _durham_streams_for_bank_slope():
    """Load the exact cached stream vectors that generated the frozen sites.

    If that reproducibility cache is absent, rebuild the same 10 m/2 km² stream
    universe and use nearest-line tangents; the 147 flagship site geometries
    themselves remain frozen either way.
    """
    cached = Path("cache/regions_work/durham/streams.geojson")
    if cached.exists():
        streams = gpd.read_file(cached)
        # This legacy cache is GeoJSON with UTM coordinates but no reliable CRS.
        return streams.set_crs(UTM_CRS, allow_override=True)
    from core.pipeline import fetch_dem, process_hydrology, run_pipeline
    print("  cached Durham stream vectors absent; rebuilding 10 m stream tangents...")
    hydrology = process_hydrology(fetch_dem(BBOX), BBOX)
    streams, _candidates, _ = run_pipeline(
        bbox=BBOX, hydrology=hydrology, return_hydrology=True,
        threshold=20000, spacing_m=1500,
        output_dir="cache/wire/bank_slope_durham", utm_crs=UTM_CRS)
    return streams


def fetch_and_wire(gdf, do_fetch=True):
    """Return (enriched_gdf_utm, provenance_dict). Reuses the disk cache so
    --no-fetch works fully offline after one online run."""
    utm = gdf.to_crs(UTM_CRS)
    n = len(gdf)
    prov = {}  # param -> {'source': str, 'n_real': int, 'kind': 'real'|'fallback'}

    # ── snap every site to a COMID, then batch EROM + StreamCat ──
    print(f"Snapping {n} sites to NHDPlus COMIDs (NLDI, cached)...")
    comids = []
    for lat, lon in zip(gdf["lat"], gdf["lon"]):
        comids.append(rs.comid_for_point(lat, lon) if do_fetch else rs.comid_for_point(lat, lon))
    n_comid = sum(c is not None for c in comids)
    print(f"  {n_comid}/{n} snapped")
    erom = rs.erom_for_comids([c for c in comids if c])
    imperv = rs.streamcat_impervious([c for c in comids if c])
    print(f"  EROM for {len(erom)} COMIDs · StreamCat imperv for {len(imperv)} COMIDs")

    # ── one-shot point layers ──
    print("Fetching TRI / NPDES / CSO / NBI point layers (cached)...")
    tri_pts = rs.tri_facility_points("NC", BBOX)
    npdes_pts = rs.npdes_outfall_points(BBOX, state_abbrs=("NC",))
    cso_pts = rs.cso_outfall_points(BBOX, state_abbrs=("NC",))
    nbi_pts = rs.nbi_bridge_points(BBOX)
    print(f"  TRI={'fetch-fail' if tri_pts is None else len(tri_pts)} · "
          f"NPDES={'dl-fail' if npdes_pts is None else len(npdes_pts)} · "
          f"CSO={'dl-fail' if cso_pts is None else len(cso_pts)} · NBI={len(nbi_pts)}")

    def to_utm_points(pts):
        if not pts:
            return gpd.GeoSeries([], crs=UTM_CRS)
        g = gpd.GeoSeries([Point(lon, lat) for lat, lon in pts], crs=WGS84)
        return g.to_crs(UTM_CRS)

    tri_utm = to_utm_points(tri_pts)
    npdes_utm = to_utm_points(npdes_pts)
    cso_utm = to_utm_points(cso_pts or [])
    nbi_utm = to_utm_points(nbi_pts)

    # fix-pass-2 Phase 1/2 impact layers (same fetchers as the region runner)
    print("Fetching SEMS / PAD-US / SWAP intake layers (cached)...")
    pad = 0.05
    sems_pts = rs.sems_superfund_points(
        "NC", (BBOX[0] - pad, BBOX[1] - pad, BBOX[2] + pad, BBOX[3] + pad))
    sems_utm = to_utm_points(sems_pts or [])
    padus = rs.padus_protected_gdf(BBOX, UTM_CRS)
    intake_pts = rs.nc_surface_intake_points(BBOX_WIDE)
    intakes_gdf = (gpd.GeoDataFrame(geometry=to_utm_points(intake_pts), crs=UTM_CRS)
                   if intake_pts is not None else None)
    print(f"  SEMS={'dl-fail' if sems_pts is None else len(sems_utm)} · "
          f"PAD-US={'none' if padus is None else len(padus)} · "
          f"intakes={'dl-fail' if intake_pts is None else len(intake_pts)}")

    print("Fetching NC 3.125-ft lidar bank cross-sections (batched, cached)...")
    bank_lidar = compute_bank_slopes_nc_lidar(
        utm, _durham_streams_for_bank_slope())
    print(f"  high-resolution bank profiles: "
          f"{sum(info is not None for info in bank_lidar)}/{n}")

    # New columns (start as copies so fallbacks keep the v1 value)
    new = {c: list(gdf[c]) for c in ALL_PARAMS if c in gdf.columns}
    bank_degrees = list(gdf.get("bank_slope_deg", pd.Series([10.0] * n)))
    real_counts = {k: 0 for k in REAL_SOURCES}

    print("Computing per-site real values...")
    for i in range(n):
        row_utm = utm.iloc[i]
        pt = row_utm.geometry
        area = float(gdf["catchment_area_km2"].iloc[i])
        disc = _catchment_disc_utm(pt, area)
        props = erom.get(comids[i], {})

        # GRIME's own DEM-derived drainage area (already a trusted real column) is
        # used for flood + width so those stay internally consistent with the rest
        # of the model and avoid the NHD-vs-DEM catchment mismatch.
        dem_da = area
        erom_da = rs.erom_drainage_km2(props)

        # flow: EROM mean annual flow — but only trust it when the snapped NHD
        # flowline's own drainage is not wildly smaller than GRIME's DEM catchment
        # (a much-smaller EROM drainage means the point snapped to a minor tributary
        # and the flow badly understates the site → keep the documented fallback).
        q = rs.erom_mean_q_cfs(props)
        snap_ok = (erom_da is None) or (dem_da <= 0) or (erom_da / dem_da >= 0.4)
        if q is not None and snap_ok:
            new["usgs_mean_q_cfs"][i] = round(q, 4); real_counts["usgs_mean_q_cfs"] += 1
        cv = rs.erom_seasonal_cv(props)
        if cv is not None:
            new["seasonal_cv"][i] = round(cv, 4); real_counts["seasonal_cv"] += 1

        # imperviousness (per catchment) + derived runoff C
        imp = imperv.get(comids[i])
        if imp is not None:
            new["impervious_pct"][i] = round(imp, 2); real_counts["impervious_pct"] += 1
            new["runoff_coeff_C"][i] = round(estimate_runoff_coefficient(imp), 4)
            real_counts["runoff_coeff_C"] += 1

        # flood Q10 — GRIME DEM drainage area + real impervious
        q10 = rs.flood_q10_cfs_sir2014(dem_da, imp if imp is not None else new["impervious_pct"][i])
        if q10 is not None:
            new["flood_q10_cfs"][i] = round(q10, 2); real_counts["flood_q10_cfs"] += 1

        # channel width → shipped width curve (curve unchanged; only the width is real)
        w = rs.bankfull_width_m_bieger(dem_da)
        if w is not None:
            new["channel_width_score"][i] = channel_width_score(w)
            real_counts["channel_width_score"] += 1

        # generation point densities (constructs unchanged: count-in-catchment)
        if tri_pts is not None:
            in_c = tri_utm[tri_utm.within(disc)]
            new["tri_facility_density"][i] = round(len(in_c) / max(area, 0.01), 4)
            real_counts["tri_facility_density"] += 1
        if npdes_pts is not None:
            new["npdes_points"][i] = int(npdes_utm.within(disc).sum())
            real_counts["npdes_points"] += 1
        if cso_pts is not None:  # download succeeded → a real count (0 for Durham)
            new["cso_density"][i] = round(inverse_distance_score(pt, cso_utm, 500), 4)
            real_counts["cso_density"] += 1

        # feasibility: bridge proximity (existing 0.2/0.0 function, real NBI)
        if len(nbi_utm):
            gdf_nbi = gpd.GeoDataFrame(geometry=nbi_utm, crs=UTM_CRS)
            new["bridge_proximity_bonus"][i] = bridge_proximity_bonus(pt, gdf_nbi)
            real_counts["bridge_proximity_bonus"] += 1

        # Candidate-only high-resolution bank cross-section; if any profile is
        # unavailable, preserve that site's existing real 10 m DEM value.
        slope_info = bank_lidar[i]
        if slope_info is not None:
            slope = float(slope_info["slope_deg"])
            bank_degrees[i] = round(slope, 2)
            new["bank_slope_score"][i] = bank_slope_score(slope)
            real_counts["bank_slope_score"] += 1

        # land ownership from the containing parcel
        owner = rs.parcel_owner_at(gdf["lat"].iloc[i], gdf["lon"].iloc[i])
        if owner is not None:
            new["land_ownership"][i] = rs.land_ownership_from_owner(owner)
            real_counts["land_ownership"] += 1

        # impact: superfund / protected areas / drinking-water intakes
        # (fix-pass-2 — shipped curves, real layers; 0.0 = computed zero)
        if sems_pts is not None:
            new["superfund_score"][i] = round(inverse_distance_score(pt, sems_utm, 500), 4)
            real_counts["superfund_score"] += 1
        if padus is not None:
            from core.impact import protected_area_score_from_gdf
            new["protected_area_score"][i] = round(
                float(protected_area_score_from_gdf(pt, padus)), 4)
            real_counts["protected_area_score"] += 1
        if intakes_gdf is not None:
            from core.impact import water_intake_score
            new["water_intake_score"][i] = round(
                float(water_intake_score(pt, intakes_gdf)), 4)
            real_counts["water_intake_score"] += 1

    # write columns back
    enriched = utm.copy()
    for c, vals in new.items():
        enriched[c] = vals
    enriched["bank_slope_deg"] = bank_degrees

    # Navigability gate input (fix-pass-2 Phase 3) — same semantics as the
    # region runner: NaN (gate inert) when the NWN clip is missing or when no
    # navigable segment exists near the bbox.
    nwn = rs.nwn_navigable_union(BBOX, UTM_CRS)
    if nwn is None or nwn.is_empty:
        enriched["navigable_dist_m"] = np.nan
        prov["navigable_dist_m"] = {
            "kind": "real" if nwn is not None else "fallback",
            "source": ("USACE/BTS NWN (NTAD): no navigable segment near the "
                       "Durham bbox — computed absence, gate passes all sites"
                       if nwn is not None else
                       "USACE NWN clip not on disk — navigability gate inert"),
            "n_sites": n,
        }
    else:
        enriched["navigable_dist_m"] = [round(float(d), 1)
                                        for d in enriched.geometry.distance(nwn)]
        prov["navigable_dist_m"] = {
            "kind": "real",
            "source": ("USACE/BTS National Waterway Network lines (NTAD, "
                       "retrieved 2026-07-10), distance to nearest navigable "
                       "segment"),
            "n_real": n, "n_sites": n,
        }

    for k, src in REAL_SOURCES.items():
        if real_counts[k] > 0:
            prov[k] = {"kind": "real", "source": src,
                       "n_real": real_counts[k], "n_sites": n}
        else:
            prov[k] = {"kind": "fallback", "source": src,
                       "reason": "source unavailable or unusable; preserved prior fallback",
                       "n_real": 0, "n_sites": n}
    prov["bank_slope_score"].update({
        "n_nc_lidar_3_125ft": sum(info is not None for info in bank_lidar),
        "n_3dep_10m_fallback": sum(info is None for info in bank_lidar),
        "source_mosaics": sorted({source for info in bank_lidar if info is not None
                                  for source in info["sources"]}),
    })
    for k, reason in FALLBACK_REASONS.items():
        prov[k] = {"kind": "fallback", "reason": reason, "n_sites": n}
    for k, source in INHERITED_SOURCES.items():
        prov[k] = {
            "kind": "derived" if k == "velocity_feasibility" else "real",
            "source": source,
            "n_real": n,
            "n_sites": n,
        }
    assert set(ALL_PARAMS) <= set(prov), (
        "every scored parameter must carry source or fallback provenance")
    # P0 Option A: name the SCORED form of velocity so the two velocity
    # constructs are distinguishable (see model.json curves.* and
    # documentation.md "Why velocity appears twice").
    prov["velocity_transport_favorability"] = {
        "kind": "derived",
        "source": ("scored form of flow_velocity_ms inside the Flow family — "
                   "peaked Gaussian exp(-((v-0.9)/0.6)^2), model.json "
                   "curves.velocity_transport_favorability; the raw velocity "
                   "feeds velocity_feasibility and the 3.0 m/s hard gate"),
        "n_sites": n,
    }
    return enriched, prov


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true",
                    help="re-score from the disk cache only (offline)")
    args = ap.parse_args()

    if OUT.resolve() == IN.resolve():
        sys.exit("REFUSING: output path is the frozen live dataset")

    gdf, v1_note = load_v1()
    print(f"Loaded {len(gdf)} v1 candidates from {IN}")

    enriched, prov = fetch_and_wire(gdf, do_fetch=not args.no_fetch)

    # Re-score with the SHIPPED, UNCHANGED scoring (hard gates + MinMax + composite).
    print("Re-scoring with core.scoring.compute_composite_score (unchanged)...")
    scored = compute_composite_score(enriched)
    scored = scored.reset_index(drop=True)
    scored["rank"] = scored.index + 1
    print(f"  scored {len(scored)} sites; composite "
          f"{scored['composite_score'].min():.2f}–{scored['composite_score'].max():.2f}")

    # provenance summary + varying/constant counts on the NEW dataset
    present = [c for c in ALL_PARAMS if c in scored.columns]
    varying = sorted(c for c in present if scored[c].nunique(dropna=False) > 1)
    constant = sorted(c for c in present if c not in varying)
    print(f"  varying now: {len(varying)}/27 · constant: {len(constant)}")

    # write WGS84 geojson with per-parameter provenance
    out_gdf = scored.to_crs(WGS84)
    feats = []
    for _, r in out_gdf.iterrows():
        props = {k: (round(v, 4) if isinstance(v, float) else v)
                 for k, v in r.drop(labels="geometry").items()}
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point",
                                   "coordinates": [round(r.geometry.x, 6), round(r.geometry.y, 6)]},
                      "properties": props})
    note = ("candidates_v2: the 147 frozen Durham sites RE-SCORED after wiring real "
            "per-site data into the previously constant parameters (see provenance). "
            "Scoring math is unchanged from candidates.geojson — same weights, curves, "
            "gates, MinMax. Sources: NHDPlus EROM, EPA StreamCat, USGS SIR 2014-5030 "
            "flood regression, Bieger 2015 width curve, EPA TRI/ECHO, FHWA NBI, Durham "
            "parcels, EPA SEMS (Superfund), USGS PAD-US 4.1 (local NC clip), NC DEQ "
            "SWAP surface intakes, and NC DPS/OneMap 3.125-ft lidar bank profiles. "
            "Parameters that could not be made real remain "
            "documented fallbacks.")
    fc = {
        "type": "FeatureCollection",
        "note": note,
        "provenance": {
            "n_parameters": len(ALL_PARAMS),
            "varying": varying, "constant": constant,
            "parameters": prov,
        },
        "features": feats,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    from scripts.run_regions import _json_safe
    OUT.write_text(json.dumps(_json_safe(fc), indent=1, sort_keys=True, allow_nan=False) + "\n")
    print(f"Wrote {len(feats)} re-scored candidates → {OUT}")
    real_now = [k for k, v in prov.items() if v["kind"] == "real"]
    print(f"  made real: {len(real_now)} parameters ({sum(1 for k in real_now if k in varying)} of them vary)")


if __name__ == "__main__":
    main()
