#!/usr/bin/env python3
"""
Bake a real river name into every scored region site.

Region sites come from the DEM stream network and carry only a numeric segment
id. We name them once here, server-side, from REAL sources only (no fabricated
names) and write `river_name` into each feature's properties (null → the UI shows
"Segment N").

Two real name sources, unioned:
  1. OpenStreetMap — named waterway ways in the region bbox (Overpass, cached).
  2. USGS NHDPlus (national) — flowlines carrying a GNIS name, from the USGS
     WaterData GeoServer WFS (wmadata:nhdflowline_network, `gnis_name`), cached.
NHD gives national coverage (Chicago/NYC/LA/Houston, not just NC) and often names
a stream OSM left unnamed; OSM often maps small urban creeks NHD's medium-res
network omits. Together they cover far more sites than OSM alone.

Matching (fixes the old "Segment N" over-nulling):
  - Snap along the WHOLE way/flowline geometry, not endpoints.
  - Distance-tiered: take the nearest NAMED waterway within 1 km (DEM-derived
    site coordinates rarely sit exactly on the OSM/NHD centreline), preferring a
    specific name over a bare generic one via a small distance penalty.
  - Keep a real generic/type name ("Creek", "Los Angeles River", …) rather than
    forcing null; only null when NO real name exists within range.

Honest failure states: if BOTH sources fail to fetch, existing names are kept
(never downgraded to null on a transient outage). A site with genuinely no named
waterway within 1 km stays null. Reads/writes only mock_data/regions/<slug>.geojson
(never the frozen candidates files); no scores/coords/geometry are touched.

Run:  python3 scripts/add_river_names.py            # all built regions
      python3 scripts/add_river_names.py --only durham
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import geopandas as gpd
from shapely.geometry import LineString, Point, shape as shape_of

from core.real_sources import cached_get_json

REGIONS = Path(_ROOT) / "mock_data/regions"
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
NHD_WFS = "https://api.water.usgs.gov/geoserver/wmadata/ows"

# Bare type-words: kept when they're the only real label available, but a
# *specific* name (anything not exactly one of these) is preferred nearby.
GENERIC = {"river", "creek", "stream", "canal", "drain", "ditch", "run",
           "branch", "brook", "bayou", "wash", "slough", "gut", "swamp",
           "lake", "pond", "reservoir", "channel"}

# Distance-tiered matching. Take the nearest named waterway within MAX_M; a bare
# generic name must be GENERIC_PENALTY_M closer than a specific one to win.
MAX_M = 1000.0
TIERS = (300.0, 600.0, 1000.0)     # reported confidence bands
GENERIC_PENALTY_M = 250.0
# Buffer the fetch bbox so a river whose named segment sits just past the region
# edge still names sites near that edge (~2 km).
BBOX_BUFFER_DEG = 0.02


def _is_generic(name):
    return name.strip().lower() in GENERIC


def named_waterways(bbox):
    """Named OSM waterway ways within bbox (west,south,east,north). Cached.
    Returns [(name, [(lon,lat),...]), ...], or None on fetch failure (distinct
    from an empty list = 'genuinely none')."""
    w, s, e, n = bbox
    q = (f'[out:json][timeout:90];'
         f'(way["waterway"]["name"]({s},{w},{n},{e}););out geom;')
    for url in OVERPASS:
        j = cached_get_json(url, {"data": q}, kind="rivername", timeout=120)
        if j and "elements" in j:
            out = []
            for el in j["elements"]:
                g = el.get("geometry")
                nm = ((el.get("tags", {}) or {}).get("name", "") or "").strip()
                if g and len(g) >= 2 and nm:
                    out.append((nm, [(p["lon"], p["lat"]) for p in g]))
            return out
        time.sleep(2)
    return None


def named_flowlines_nhd(bbox):
    """Named NHDPlus flowlines (USGS, national) within bbox (w,s,e,n). Cached +
    paginated. Returns [(gnis_name, geojson_geometry), ...], or None on total
    fetch failure. Empty gnis_name (incl. the ' ' placeholder) is skipped."""
    w, s, e, n = bbox
    out, start, PAGE, MAX_PAGES = [], 0, 1000, 8
    got_any = False
    for _ in range(MAX_PAGES):
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeName": "wmadata:nhdflowline_network",
            "outputFormat": "application/json", "srsName": "EPSG:4326",
            "count": PAGE, "startIndex": start,
            "bbox": f"{w},{s},{e},{n},EPSG:4326",
        }
        j = cached_get_json(NHD_WFS, params, kind="nhdname", timeout=120)
        if j is None:
            return out if got_any else None
        got_any = True
        feats = j.get("features", []) or []
        for f in feats:
            nm = ((f.get("properties", {}) or {}).get("gnis_name") or "").strip()
            g = f.get("geometry")
            if nm and g:
                out.append((nm, g))
        if len(feats) < PAGE:
            break
        start += PAGE
    return out


def _candidate_gdf(bbox, utm):
    """Union OSM ways + NHD flowlines into one UTM GeoDataFrame of named
    waterways. Returns (gdf, osm_ok, nhd_ok). gdf is None when there is nothing
    to match against."""
    fb = (bbox[0] - BBOX_BUFFER_DEG, bbox[1] - BBOX_BUFFER_DEG,
          bbox[2] + BBOX_BUFFER_DEG, bbox[3] + BBOX_BUFFER_DEG)
    osm = named_waterways(fb)
    nhd = named_flowlines_nhd(fb)
    names, geoms, gen, src = [], [], [], []
    if osm:
        for nm, coords in osm:
            try:
                geoms.append(LineString(coords))
            except Exception:
                continue
            names.append(nm); gen.append(_is_generic(nm)); src.append("osm")
    if nhd:
        for nm, gj in nhd:
            try:
                geoms.append(shape_of(gj))
            except Exception:
                continue
            names.append(nm); gen.append(_is_generic(nm)); src.append("nhd")
    if not geoms:
        return None, (osm is not None), (nhd is not None)
    gdf = gpd.GeoDataFrame(
        {"name": names, "is_generic": gen, "src": src},
        geometry=geoms, crs="EPSG:4326",
    ).to_crs(utm)
    return gdf, (osm is not None), (nhd is not None)


def _match(pt, gdf, sindex):
    """Nearest named waterway to a UTM point within MAX_M, preferring specific
    names. Returns (name, distance_m, source) or (None, None, None)."""
    try:
        cand = list(sindex.query(pt.buffer(MAX_M)))
    except Exception:
        cand = list(range(len(gdf)))
    best = (None, None, None)
    best_score = None
    for j in cand:
        row = gdf.iloc[j]
        d = row.geometry.distance(pt)
        if d > MAX_M:
            continue
        score = d + (GENERIC_PENALTY_M if row["is_generic"] else 0.0)
        if best_score is None or score < best_score:
            best_score = score
            best = (row["name"], d, row["src"])
    return best


def add_names(slug):
    path = REGIONS / f"{slug}.geojson"
    doc = json.loads(path.read_text())
    feats = doc.get("features", [])
    if not feats:
        return slug, 0, 0, "zero-site region", {}
    region = doc.get("region", {})
    bbox = region.get("bbox")
    utm = f"EPSG:{region.get('utm_epsg', 32617)}"
    if not bbox:
        return slug, 0, 0, "no bbox in region block", {}

    gdf, osm_ok, nhd_ok = _candidate_gdf(tuple(bbox), utm)
    if gdf is None:
        if not osm_ok and not nhd_ok:
            # Both sources failed → keep whatever names exist; never downgrade.
            return slug, len(feats), sum(
                1 for f in feats if f["properties"].get("river_name")
            ), "both fetches failed (kept existing)", {}
        # Both sources succeeded but returned nothing named nearby.
        for f in feats:
            f["properties"]["river_name"] = None
        path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        return slug, len(feats), 0, "no named waterways in bbox", {}

    sites = gpd.GeoSeries(
        [Point(f["geometry"]["coordinates"]) for f in feats], crs="EPSG:4326"
    ).to_crs(utm)
    sindex = gdf.sindex
    both_ok = osm_ok and nhd_ok
    named = 0
    stats = {"osm": 0, "nhd": 0, "generic": 0, "t300": 0, "t600": 0, "t1000": 0}
    for i, pt in enumerate(sites):
        name, dist, src = _match(pt, gdf, sindex)
        if name:
            feats[i]["properties"]["river_name"] = name
            named += 1
            stats[src] = stats.get(src, 0) + 1
            if _is_generic(name):
                stats["generic"] += 1
            stats["t300" if dist <= TIERS[0] else
                  "t600" if dist <= TIERS[1] else "t1000"] += 1
        elif both_ok:
            feats[i]["properties"]["river_name"] = None   # honest null
        # else: a source was down → leave the existing name in place

    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    status = "ok" if both_ok else (
        "ok (osm only — nhd fetch failed)" if osm_ok else
        "ok (nhd only — osm fetch failed)")
    return slug, len(feats), named, status, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--pace", type=float, default=2.0)
    args = ap.parse_args()
    slugs = ([args.only] if args.only else
             sorted(p.stem for p in REGIONS.glob("*.geojson")))
    tot_named = tot_sites = 0
    agg = {"osm": 0, "nhd": 0, "generic": 0, "t300": 0, "t600": 0, "t1000": 0}
    for i, slug in enumerate(slugs):
        try:
            s, n, named, status, st = add_names(slug)
        except Exception as e:
            s, n, named, status, st = slug, 0, 0, f"ERROR {type(e).__name__}: {e}", {}
        nullpct = f"{100*(n-named)/n:.0f}%" if n else "—"
        extra = ""
        if st:
            extra = f" [osm {st.get('osm',0)} · nhd {st.get('nhd',0)} · gen {st.get('generic',0)}]"
            for k in agg:
                agg[k] += st.get(k, 0)
        print(f"  {s:20s} {named:>4}/{n:<4} named · null {nullpct:>4} · {status}{extra}", flush=True)
        tot_named += named; tot_sites += n
        if i < len(slugs) - 1:
            time.sleep(args.pace)
    if tot_sites:
        print(f"\n  TOTAL {tot_named}/{tot_sites} named "
              f"({100*(tot_sites-tot_named)/tot_sites:.1f}% null) · "
              f"by source: osm {agg['osm']} · nhd {agg['nhd']} · generic {agg['generic']} · "
              f"tiers ≤300m {agg['t300']} / ≤600m {agg['t600']} / ≤1km {agg['t1000']}", flush=True)


if __name__ == "__main__":
    main()
