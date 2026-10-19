#!/usr/bin/env python3
"""
Append expansion regions to scripts/regions.json without touching existing ones.

  --nc-cdp   every NC census-designated place (LSAD 57), i.e. the unincorporated
             communities that build_nc_config.py (incorporated places only)
             leaves out. Same physiography rules, bbox sizing and dedupe as the
             NC towns; tier "cdp".
  --us N     the N most populous places in the conterminous US outside NC,
             ordered by population (largest first); tier "city".

Inputs (free, cached under cache/refs/):
  * Census Gazetteer 2024 places (NC: gaz_place_37.txt; national:
    us/2024_Gaz_place_national.txt) for centroid + land area.
  * ACS 2023 5-year B01003 total population per place (us/acs2023_place_pop.json).
  * USGS physiographic divisions, Fenneman & Johnson 1946
    (us/physio/physio.shp), mapped to Bieger et al. 2015 Table 3 width curves.
    Laurentian Upland and Interior Highlands use the paper's national curve
    because their regional fits (n=6, n=7) are "tentative at best".

Out-of-NC regions use flood_method "none" (SIR 2014-5030 is NC-calibrated
here), no litter or parcel source, and the nearest estuary / beach from the
reference lists below (straight-line, not basin-routed; labeled as such). As
with the NC towns, those two distances are min-max normalized within a region,
so a far reference is near-constant and drops out of the ranking.

Dedupe: a place is absorbed when its bbox overlaps an existing region's bbox by
>= 60% (or shares its slug); the rest are cluster-merged largest-first within
a division/province (>= 50% overlap of the smaller box, union capped ~28 km).

Run:  python3 scripts/build_expansion_config.py --nc-cdp
      python3 scripts/build_expansion_config.py --us 300
      (add --dry to report only)
"""
import argparse
import csv
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import build_nc_config as nc  # noqa: E402  (bbox/overlap/merge helpers, NC rules)
from core.region_sources import BIEGER_WIDTH_CURVES  # noqa: E402

CONFIG = Path(_ROOT) / "scripts/regions.json"
REFS = Path(_ROOT) / "cache/refs"
NAT_GAZ = REFS / "us/2024_Gaz_place_national.txt"
ACS_POP = REFS / "us/acs2023_place_pop.json"
PHYSIO = REFS / "us/physio/physio.shp"

CONUS_EXCLUDE = {"AK", "HI", "PR", "NC"}

# Consolidated city-county governments -> the city's common name.
CONSOLIDATED = {
    "Nashville-Davidson": "Nashville", "Louisville/Jefferson County": "Louisville",
    "Lexington-Fayette": "Lexington", "Augusta-Richmond County": "Augusta",
    "Athens-Clarke County": "Athens", "Macon-Bibb County": "Macon",
    "Columbus-Muscogee County": "Columbus", "Indianapolis city": "Indianapolis",
}

DIVISION_CURVE = {
    "APPALACHIAN HIGHLANDS": ("AHI", "Appalachian Highlands"),
    "ATLANTIC PLAIN": ("APL", "Atlantic Plain"),
    "INTERIOR PLAINS": ("IPL", "Interior Plains"),
    "PACIFIC MOUNTAIN SYSTEM": ("PMS", "Pacific Mountain System"),
    "ROCKY MOUNTAIN SYSTEM": ("RMS", "Rocky Mountain System"),
    "INTERMONTANE PLATEAUS": ("IMP", "Intermontane Plateaus"),
    "LAURENTIAN UPLAND": ("USA", "national model (Laurentian Upland fit is tentative, n=6)"),
    "INTERIOR HIGHLANDS": ("USA", "national model (Interior Highlands fit is tentative, n=7)"),
}

# Receiving-water references for out-of-NC regions (lat, lon, label).
ESTUARIES = [
    (42.33, -70.95, "Boston Harbor"), (43.65, -70.20, "Casco Bay"),
    (41.60, -71.35, "Narragansett Bay"), (41.05, -73.00, "Long Island Sound"),
    (40.67, -74.05, "Upper New York Bay"), (39.85, -75.30, "Delaware River estuary"),
    (39.00, -75.20, "Delaware Bay"), (39.20, -76.40, "Upper Chesapeake Bay"),
    (37.00, -76.10, "Lower Chesapeake Bay"), (32.76, -79.90, "Charleston Harbor"),
    (32.03, -80.90, "Savannah River estuary"), (30.40, -81.40, "St. Johns River mouth"),
    (25.60, -80.20, "Biscayne Bay"), (27.70, -82.60, "Tampa Bay"),
    (30.50, -88.00, "Mobile Bay"), (29.15, -89.25, "Mississippi River delta"),
    (29.60, -94.90, "Upper Galveston Bay"), (27.75, -97.30, "Corpus Christi Bay"),
    (25.96, -97.15, "Rio Grande mouth"), (32.68, -117.18, "San Diego Bay"),
    (33.73, -118.20, "San Pedro Bay"), (37.70, -122.30, "San Francisco Bay"),
    (46.25, -123.90, "Columbia River estuary"), (47.60, -122.45, "Puget Sound"),
    (31.80, -114.80, "Colorado River delta (Gulf of California)"),
    (41.90, -87.60, "Lake Michigan shoreline (Great Lakes receiving water, not an ocean estuary)"),
    (43.03, -87.88, "Lake Michigan at Milwaukee (Great Lakes receiving water)"),
    (41.55, -81.70, "Lake Erie (Great Lakes receiving water)"),
    (43.30, -77.60, "Lake Ontario (Great Lakes receiving water)"),
    (43.90, -83.60, "Saginaw Bay, Lake Huron (Great Lakes receiving water)"),
    (46.75, -92.05, "Lake Superior at Duluth (Great Lakes receiving water)"),
]
BEACHES = [
    (42.42, -70.99, "Revere Beach"), (43.51, -70.38, "Old Orchard Beach"),
    (40.58, -73.82, "Rockaway Beach"), (40.22, -74.00, "Asbury Park beach"),
    (38.93, -74.92, "Cape May beach"), (38.34, -75.08, "Ocean City (MD) beach"),
    (36.85, -75.98, "Virginia Beach"), (33.69, -78.88, "Myrtle Beach"),
    (32.65, -79.94, "Folly Beach"), (32.00, -80.85, "Tybee Island beach"),
    (30.29, -81.39, "Jacksonville Beach"), (25.79, -80.13, "Miami Beach"),
    (27.98, -82.83, "Clearwater Beach"), (30.33, -87.14, "Pensacola Beach"),
    (30.25, -87.70, "Gulf Shores beach"), (30.39, -88.90, "Biloxi beach"),
    (29.24, -90.00, "Grand Isle beach"), (29.28, -94.80, "Galveston Island beach"),
    (27.60, -97.20, "North Padre Island beach"), (26.10, -97.16, "South Padre Island beach"),
    (32.77, -117.25, "Mission Beach"), (33.65, -118.00, "Huntington Beach"),
    (34.01, -118.50, "Santa Monica Beach"), (37.76, -122.51, "Ocean Beach"),
    (45.89, -123.96, "Cannon Beach"), (47.58, -122.41, "Alki Beach"),
    (41.965, -87.635, "Montrose Beach"), (43.06, -87.87, "Bradford Beach"),
    (41.49, -81.74, "Edgewater Beach (Lake Erie)"), (42.15, -80.10, "Presque Isle beach (Lake Erie)"),
    (43.26, -77.60, "Ontario Beach (Lake Ontario)"), (43.00, -82.42, "Lakeside Beach (Lake Huron)"),
    (46.75, -92.08, "Park Point beach (Lake Superior)"),
]


def safe_slug(name, taken):
    """URL/file-safe slug: ASCII-folded, [a-z0-9-] only, unique within `taken`."""
    import re
    import unicodedata
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    base, i = s, 2
    while s in taken:
        s = f"{base}-{i}"
        i += 1
    taken.add(s)
    return s


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def nearest_ref(refs, lat, lon):
    la, lo, label = min(refs, key=lambda r: haversine_km(lat, lon, r[0], r[1]))
    return {"lat": la, "lon": lo,
            "label": f"{label} (nearest listed reference, straight-line)"}


def acs_population():
    rows = json.load(open(ACS_POP))
    return {r[2] + r[3]: int(r[1]) for r in rows[1:] if r[1] not in (None, "", "-")}


def read_gaz(path, keep):
    out = []
    with open(path, encoding="latin-1") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            r = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
            if not keep(r):
                continue
            name = r["NAME"]
            for suffix in (" (balance)", " metropolitan government", " metro government",
                           " unified government", " consolidated government", " CDP",
                           " city and borough", " city", " town", " village", " borough",
                           " municipality"):
                if name.endswith(suffix):
                    name = name[: -len(suffix)]
            name = CONSOLIDATED.get(name, name)
            out.append({
                "geoid": r["GEOID"], "usps": r.get("USPS", "NC"), "name": name,
                "lsad": r["LSAD"], "aland_km2": float(r["ALAND"]) / 1e6,
                "lat": float(r["INTPTLAT"]), "lon": float(r["INTPTLONG"]),
            })
    return out


def dedupe_and_merge(places, existing, group_key):
    boxes = [r["bbox"] for r in existing]
    slugs = {r["slug"] for r in existing}
    names = {(r.get("state"), r["name"].split(",")[0].lower()) for r in existing}
    absorbed, remaining = [], []
    for t in places:
        t["bbox"] = nc.bbox_for(t)
        name_slug = f"{t['name']}-{t['usps']}".lower().replace(" ", "-").replace(".", "").replace("'", "")
        same_name = any(st == t["usps"] and (n == t["name"].lower() or n.startswith(t["name"].lower() + " "))
                        for st, n in names)
        if (same_name or name_slug in slugs
                or any(nc.overlap_frac(t["bbox"], b, of="a") >= 0.60 for b in boxes)):
            absorbed.append(t)
        else:
            remaining.append(t)
    remaining.sort(key=lambda t: (-t["population"], -t["aland_km2"]))
    clusters = []
    for t in remaining:
        for c in clusters:
            if group_key(t) != group_key(c["primary"]):
                continue
            if nc.overlap_frac(t["bbox"], c["bbox"], of="min") >= 0.50:
                u = nc.cap_union(c["bbox"], t["bbox"])
                if u is not None:
                    c["bbox"] = [round(x, 4) for x in u]
                    c["members"].append(t)
                    break
        else:
            clusters.append({"primary": t, "bbox": list(t["bbox"]), "members": []})
    return absorbed, clusters


def curve_entry(code, label):
    a, b = BIEGER_WIDTH_CURVES[code]
    return {"code": code, "a": a, "b": b,
            "source": f"Bieger et al. 2015 JAWRA Table 3, {label}"}


def build_nc_cdp(cfg):
    pops = acs_population()
    places = read_gaz(nc.GAZ, lambda r: r["LSAD"] == "57")
    for t in places:
        t["population"] = pops.get(t["geoid"], 0)
    provs = nc.load_provinces()
    for t in places:
        t["province"] = nc.classify(provs, t["lon"], t["lat"])
    absorbed, clusters = dedupe_and_merge(places, cfg["regions"], lambda t: t["province"])
    taken = {r["slug"] for r in cfg["regions"]}
    new = []
    for c in clusters:
        t = c["primary"]
        curve, flood, note = nc.PROV_RULES[t["province"]]
        merged = [m["name"] for m in c["members"]]
        e = {
            "slug": safe_slug(t["name"], taken), "name": f"{t['name']}, NC", "state": "NC",
            "tier": "cdp", "population": t["population"], "aland_km2": round(t["aland_km2"], 2),
            "center": [t["lon"], t["lat"]], "bbox": c["bbox"],
            "utm_epsg": 32600 + int((t["lon"] + 180) // 6) + 1,
            "width_curve": dict(code=curve, **nc.CURVES[curve]),
            "flood_method": flood, "estuary_ref": nc.NC_EST, "beach_ref": nc.NC_BCH,
            "parcels": None,
            "notes": note + " (unincorporated census-designated place)"
                     + (f"; merged nearby places: {', '.join(merged)}" if merged else ""),
        }
        if merged:
            e["merged_places"] = merged
        new.append(e)
    new.sort(key=lambda e: -e["population"])
    print(f"NC CDPs: {len(places)} enumerated · {len(absorbed)} absorbed by existing regions · "
          f"{sum(len(c['members']) for c in clusters)} merged · {len(new)} new regions")
    return new


def build_us(cfg, n_top):
    import geopandas as gpd
    from shapely.geometry import Point
    pops = acs_population()
    places = read_gaz(NAT_GAZ, lambda r: r["USPS"] not in CONUS_EXCLUDE)
    for t in places:
        t["population"] = pops.get(t["geoid"], 0)
    places.sort(key=lambda t: -t["population"])
    places = places[: n_top * 3]          # headroom for absorbed / merged places
    physio = gpd.read_file(PHYSIO).to_crs(4326)
    physio_m = physio.to_crs(5070)      # CONUS Albers, for nearest-polygon fallback
    sidx = physio.sindex
    for t in places:
        pt = Point(t["lon"], t["lat"])
        hits = [i for i in sidx.query(pt) if physio.geometry.iloc[i].contains(pt)]
        i = hits[0] if hits else int(physio_m.distance(
            gpd.GeoSeries([pt], crs=4326).to_crs(5070).iloc[0]).idxmin())
        t["division"] = physio["DIVISION"].iloc[i]
    absorbed, clusters = dedupe_and_merge(places, cfg["regions"], lambda t: t["division"])
    clusters.sort(key=lambda c: -c["primary"]["population"])
    taken = {r["slug"] for r in cfg["regions"]}
    new = []
    for c in clusters[:n_top]:
        t = c["primary"]
        code, label = DIVISION_CURVE[t["division"]]
        merged = [m["name"] for m in c["members"]]
        e = {
            "slug": safe_slug(f"{t['name']} {t['usps']}", taken),
            "name": f"{t['name']}, {t['usps']}", "state": t["usps"], "tier": "city",
            "population": t["population"], "aland_km2": round(t["aland_km2"], 2),
            "center": [t["lon"], t["lat"]], "bbox": c["bbox"],
            "utm_epsg": 32600 + int((t["lon"] + 180) // 6) + 1,
            "width_curve": curve_entry(code, label),
            "flood_method": "none",
            "estuary_ref": nearest_ref(ESTUARIES, t["lat"], t["lon"]),
            "beach_ref": nearest_ref(BEACHES, t["lat"], t["lon"]),
            "parcels": None,
            "notes": (f"{t['division'].title()} (USGS physiographic division); flood fallback "
                      "(SIR 2014-5030 is NC-calibrated here); no state parcel, litter or SWAP layer"
                      + (f"; merged nearby places: {', '.join(merged)}" if merged else "")),
        }
        if merged:
            e["merged_places"] = merged
        new.append(e)
    print(f"US: {len(places)} candidate places · {len(absorbed)} absorbed by existing regions · "
          f"{len(new)} new regions (top by population)")
    print("  divisions:", dict(Counter(e["width_curve"]["code"] for e in new)))
    return new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nc-cdp", action="store_true")
    ap.add_argument("--us", type=int, default=0)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    cfg = json.loads(CONFIG.read_text())
    added = []
    if args.nc_cdp:
        added += build_nc_cdp(cfg)
        cfg["regions"] += added
    if args.us:
        us = build_us(cfg, args.us)
        added += us
        cfg["regions"] += us
    for e in added[:8] + [e for e in added if e["tier"] == "city"][:25]:
        print(f"  {e['slug']:<28} {e['population']:>9,}  {e['width_curve']['code']}  "
              f"{e['estuary_ref']['label'][:40]}")
    if args.dry:
        print("(dry run — regions.json unchanged)")
        return
    CONFIG.write_text(json.dumps(cfg, indent=1) + "\n")
    print(f"regions.json: {len(cfg['regions'])} regions (+{len(added)})")


if __name__ == "__main__":
    main()
