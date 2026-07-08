#!/usr/bin/env python3
"""
Bake a real river name into every scored region site.

Region sites come from the DEM stream network and carry only a numeric
segment id. The explorer can match them to OSM rivers client-side, but the
browser Overpass fetch times out on big-metro bboxes (Charlotte, NYC), leaving
sites unnamed. So we do it once here, server-side, with a small NAMED-waterways-
only Overpass query (cheap even for big cities) + the shared disk cache, and
write `river_name` into each feature's properties.

Honest: a site with no named OSM waterway within 300 m keeps `river_name: null`
(the explorer shows "Segment N"). No fabricated names. Reads/writes only
mock_data/regions/<slug>.geojson (never the frozen candidates files).

Run:  python3 scripts/add_river_names.py            # all built regions
      python3 scripts/add_river_names.py --only durham
"""
import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import geopandas as gpd
from shapely.geometry import LineString, Point

from core.real_sources import cached_get_json

REGIONS = Path(_ROOT) / "mock_data/regions"
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
GENERIC = {"river", "creek", "stream", "canal", "drain", "ditch"}
MATCH_M = 300.0


def named_waterways(bbox, kind="rivers"):
    """Named waterway ways within bbox (west,south,east,north). Cached. Returns
    a list of (name, [(lon,lat),...])."""
    w, s, e, n = bbox
    q = f'[out:json][timeout:90];(way["waterway"]["name"]({s},{w},{n},{e}););out geom;'
    for url in OVERPASS:
        j = cached_get_json(url, {"data": q}, kind="rivername", timeout=120)
        if j and "elements" in j:
            out = []
            for el in j["elements"]:
                g = el.get("geometry")
                nm = (el.get("tags", {}) or {}).get("name", "").strip()
                if g and len(g) >= 2 and nm:
                    out.append((nm, [(p["lon"], p["lat"]) for p in g]))
            return out
        time.sleep(2)
    return None  # fetch failed → distinguish from "genuinely none"


def add_names(slug):
    path = REGIONS / f"{slug}.geojson"
    doc = json.loads(path.read_text())
    feats = doc.get("features", [])
    if not feats:
        return slug, 0, 0, "zero-site region"
    region = doc.get("region", {})
    bbox = region.get("bbox")
    utm = f"EPSG:{region.get('utm_epsg', 32617)}"
    if not bbox:
        return slug, 0, 0, "no bbox in region block"

    ways = named_waterways(bbox)
    if ways is None:
        return slug, len(feats), 0, "Overpass fetch failed (kept unnamed)"
    if not ways:
        for f in feats:
            f["properties"]["river_name"] = None
        path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        return slug, len(feats), 0, "no named waterways in bbox"

    # Build a UTM GeoDataFrame of named ways, then nearest-match each site.
    gdf = gpd.GeoDataFrame(
        {"name": [nm for nm, _ in ways]},
        geometry=[LineString(coords) for _, coords in ways], crs="EPSG:4326",
    ).to_crs(utm)
    sites = gpd.GeoSeries(
        [Point(f["geometry"]["coordinates"]) for f in feats], crs="EPSG:4326"
    ).to_crs(utm)

    named = 0
    sindex = gdf.sindex
    for i, pt in enumerate(sites):
        # nearest candidate via spatial index, then verify distance
        try:
            cand = list(sindex.nearest(pt, max_distance=MATCH_M, return_all=False))
            idxs = cand[1] if cand and len(cand) > 1 else []
        except Exception:
            idxs = []
        best, bestd = None, MATCH_M
        pool = gdf.iloc[idxs] if len(idxs) else gdf
        for _, row in pool.iterrows():
            d = row.geometry.distance(pt)
            if d <= bestd:
                bestd, best = d, row["name"]
        # keep a real, specific name (skip bare "River"/"Creek"/... generics)
        rn = best if (best and best.lower() not in GENERIC) else None
        feats[i]["properties"]["river_name"] = rn
        if rn:
            named += 1

    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return slug, len(feats), named, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--pace", type=float, default=2.0)
    args = ap.parse_args()
    slugs = ([args.only] if args.only else
             sorted(p.stem for p in REGIONS.glob("*.geojson")))
    for i, slug in enumerate(slugs):
        try:
            s, n, named, status = add_names(slug)
        except Exception as e:
            s, n, named, status = slug, 0, 0, f"ERROR {type(e).__name__}: {e}"
        print(f"  {s:18s} {named:>3}/{n:<3} named · {status}", flush=True)
        if i < len(slugs) - 1:
            time.sleep(args.pace)


if __name__ == "__main__":
    main()
