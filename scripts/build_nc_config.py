#!/usr/bin/env python3
"""
Build the statewide-NC town entries for scripts/regions.json.

Inputs (all free, cached):
  * Census Gazetteer 2024 places file for NC (cache/refs/gaz_place_37.txt) —
    authoritative incorporated-place list (LSAD 25 city / 43 town / 47 village,
    FUNCSTAT A), with INTPTLAT/INTPTLONG + ALAND for centroid and bbox sizing.
  * NC DEQ "Physiography_of_NC" polygons (ArcGIS, 3 features) — each town is
    classified Piedmont / Blue Ridge / Coastal Plain by point-in-polygon at its
    centroid, which drives the honesty-gated science inputs:
      Piedmont     -> Bieger AHI width curve + SIR 2014-5030 HR1 flood
      Blue Ridge   -> Bieger AHI width curve, flood fallback (outside HR1)
      Coastal Plain-> Bieger APL width curve, flood fallback (needs I24H50Y)
  * mock_data/places.json — population join (ordering + reporting only).

Dedupe/merge:
  1. A town whose bbox overlaps an existing metro region's bbox by >= 60% of
     the town bbox is ABSORBED (already covered on the site via containment).
  2. Remaining towns are greedily cluster-merged (largest population first):
     a town absorbs neighbors whose bbox overlaps >= 50% of the smaller bbox;
     the merged bbox is the union, capped at ~28 km per side.

Run:  python3 scripts/build_nc_config.py          # updates scripts/regions.json
      python3 scripts/build_nc_config.py --dry    # report only
"""
import argparse
import csv
import json
import math
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.geometry import shape, Point
from shapely.prepared import prep

from core.real_sources import cached_get_json

CONFIG = Path(_ROOT) / "scripts/regions.json"
GAZ = Path(_ROOT) / "cache/refs/gaz_place_37.txt"

PROV_URL = ("https://services2.arcgis.com/kCu40SDxsCGcuUWO/arcgis/rest/services/"
            "Physiography_of_NC/FeatureServer/0/query")
NC_EST = {"lat": 35.05, "lon": -76.05, "label": "Pamlico Sound (shipped NC reference)"}
NC_BCH = {"lat": 34.21, "lon": -77.79, "label": "Wrightsville Beach (shipped NC reference)"}

CURVES = {
    "AHI": {"a": 3.12, "b": 0.415, "source": "Bieger et al. 2015 JAWRA Table 3, Appalachian Highlands"},
    "APL": {"a": 2.22, "b": 0.363, "source": "Bieger et al. 2015 JAWRA Table 3, Atlantic Plain"},
}
PROV_RULES = {
    "Piedmont": ("AHI", "sir2014_hr1", "NC Piedmont (DEQ physiography polygon)"),
    "Blue Ridge": ("AHI", "none", "NC Blue Ridge (outside SIR 2014-5030 urban coverage)"),
    "Coastal Plain": ("APL", "none", "NC Coastal Plain (SIR HR4 needs 24h-50y precip — not wired; flood fallback)"),
}


def load_provinces():
    j = cached_get_json(PROV_URL, {
        "where": "1=1", "outFields": "Physiograp", "returnGeometry": "true",
        "outSR": "4326", "f": "geojson"}, kind="physio", timeout=120)
    provs = []
    for f in j["features"]:
        provs.append((f["properties"]["Physiograp"], prep(shape(f["geometry"])),
                      shape(f["geometry"])))
    return provs


def classify(provs, lon, lat):
    pt = Point(lon, lat)
    for name, prepped, _ in provs:
        if prepped.contains(pt):
            return name
    # centroid outside every polygon (barrier islands etc.) → nearest polygon
    return min(provs, key=lambda p: p[2].distance(pt))[0]


def load_places():
    rows = []
    with open(GAZ, encoding="latin-1") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            r = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
            if r["LSAD"] in ("25", "43", "47") and r["FUNCSTAT"] == "A":
                rows.append({
                    "geoid": r["GEOID"],
                    "name": r["NAME"].rsplit(" ", 1)[0],   # strip 'town'/'city'/'village'
                    "lsad": r["LSAD"],
                    "aland_km2": float(r["ALAND"]) / 1e6,
                    "lat": float(r["INTPTLAT"]), "lon": float(r["INTPTLONG"]),
                })
    return rows


def join_population(towns):
    pj = json.load(open(Path(_ROOT) / "mock_data/places.json"))
    us = [p for p in pj if p["c"] == "US"]
    byname = {}
    for p in us:
        byname.setdefault(p["n"].lower(), []).append(p)
    for t in towns:
        pop = 0
        for p in byname.get(t["name"].lower(), []):
            if abs(p["la"] - t["lat"]) < 0.35 and abs(p["lo"] - t["lon"]) < 0.35:
                pop = max(pop, p["p"])
        t["population"] = pop
    return towns


def bbox_for(t):
    """ALAND-scaled square bbox: side_km = clamp(2.2*sqrt(area), 4, 22)."""
    side_km = max(4.0, min(22.0, 2.2 * math.sqrt(max(t["aland_km2"], 0.3))))
    dlat = side_km / 111.0 / 2
    dlon = side_km / (111.0 * math.cos(math.radians(t["lat"]))) / 2
    return [round(t["lon"] - dlon, 4), round(t["lat"] - dlat, 4),
            round(t["lon"] + dlon, 4), round(t["lat"] + dlat, 4)]


def overlap_frac(a, b, of="a"):
    """Overlap area as a fraction of bbox `a` (or the smaller if of='min')."""
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = w * h
    area = lambda bb: (bb[2] - bb[0]) * (bb[3] - bb[1])
    if of == "min":
        return inter / max(min(area(a), area(b)), 1e-12)
    return inter / max(area(a), 1e-12)


def slugify(name, taken):
    s = name.lower().replace(" ", "-").replace(".", "").replace("'", "")
    base, i = s, 2
    while s in taken:
        s = f"{base}-{i}"
        i += 1
    taken.add(s)
    return s


def cap_union(a, b, max_side_km=28.0):
    """Union bbox unless it would exceed the side cap (returns None then)."""
    u = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
    lat_mid = (u[1] + u[3]) / 2
    h_km = (u[3] - u[1]) * 111.0
    w_km = (u[2] - u[0]) * 111.0 * math.cos(math.radians(lat_mid))
    return u if (h_km <= max_side_km and w_km <= max_side_km) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    cfg = json.loads(CONFIG.read_text())
    # Absorb baseline = the hand-curated metro regions only; tier=town entries
    # are regenerated by this script and must not absorb their own next run.
    existing = [r for r in cfg["regions"] if r.get("tier") != "town"]
    existing_boxes = [r["bbox"] for r in existing]
    taken_slugs = {r["slug"] for r in existing}

    towns = join_population(load_places())
    provs = load_provinces()
    for t in towns:
        t["province"] = classify(provs, t["lon"], t["lat"])
        t["bbox"] = bbox_for(t)

    # 1) absorbed by existing metro regions — by bbox overlap OR by name
    # (NC municipality names are unique statewide, so a gazetteer place whose
    # name slug matches an existing region IS that region: the metro bbox may
    # be centered differently than the huge city-limits ALAND box, e.g. Raleigh).
    existing_name_slugs = {r["slug"] for r in existing}
    absorbed, remaining = [], []
    for t in towns:
        name_slug = t["name"].lower().replace(" ", "-").replace(".", "").replace("'", "")
        if name_slug in existing_name_slugs or \
           any(overlap_frac(t["bbox"], eb, of="a") >= 0.60 for eb in existing_boxes):
            absorbed.append(t)
        else:
            remaining.append(t)

    # 2) cluster-merge remaining towns (largest pop, then land area, first)
    remaining.sort(key=lambda t: (-t["population"], -t["aland_km2"]))
    clusters = []
    for t in remaining:
        placed = False
        for c in clusters:
            if t["province"] != c["province"]:
                continue
            if overlap_frac(t["bbox"], c["bbox"], of="min") >= 0.50:
                u = cap_union(c["bbox"], t["bbox"])
                if u is not None:
                    c["bbox"] = [round(x, 4) for x in u]
                    c["members"].append(t)
                    placed = True
                    break
        if not placed:
            clusters.append({"primary": t, "province": t["province"],
                             "bbox": list(t["bbox"]), "members": []})

    # emit config entries
    new_regions = []
    for c in clusters:
        t = c["primary"]
        curve, flood, note = PROV_RULES[t["province"]]
        slug = slugify(t["name"], taken_slugs)
        merged = [m["name"] for m in c["members"]]
        entry = {
            "slug": slug, "name": f"{t['name']}, NC", "state": "NC", "tier": "town",
            "population": t["population"], "aland_km2": round(t["aland_km2"], 2),
            "center": [t["lon"], t["lat"]], "bbox": c["bbox"],
            "utm_epsg": 32600 + int((t["lon"] + 180) // 6) + 1,
            "width_curve": dict(code=curve, **CURVES[curve]),
            "flood_method": flood,
            "estuary_ref": NC_EST, "beach_ref": NC_BCH,
            "parcels": None,
            "notes": note + (f"; merged nearby towns: {', '.join(merged)}" if merged else ""),
        }
        if merged:
            entry["merged_places"] = merged
        new_regions.append(entry)

    n_merged_members = sum(len(c["members"]) for c in clusters)
    print(f"NC incorporated places enumerated: {len(towns)}")
    print(f"  absorbed by existing {len(existing)} regions: {len(absorbed)}")
    print(f"  merged into a neighboring town's region:      {n_merged_members}")
    print(f"  new regions to run:                           {len(new_regions)}")
    from collections import Counter
    print("  new regions by province:", dict(Counter(c['province'] for c in clusters)))
    print("  absorbed examples:", sorted(a['name'] for a in absorbed)[:12], "...")

    if args.dry:
        return
    # replace any previous tier=town entries (regeneration idempotent)
    cfg["regions"] = [r for r in cfg["regions"] if r.get("tier") != "town"] + new_regions
    CONFIG.write_text(json.dumps(cfg, indent=1) + "\n")
    print(f"wrote {CONFIG}: {len(cfg['regions'])} total regions")


if __name__ == "__main__":
    main()
