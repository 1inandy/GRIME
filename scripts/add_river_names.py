#!/usr/bin/env python3
"""
Bake real river names, drawn waterway geometry, and on-creek display positions
into every scored region file.

Region sites come from the DEM stream network and carry only a numeric segment
id. This script enriches mock_data/regions/<slug>.geojson server-side, from REAL
sources only (no fabricated names or geometry):

  1. NAME  — each site gets `river_name`: its nearest NAMED waterway within 1 km
     (distance-tiered, whole-geometry snap, specific names preferred over bare
     generics, but a real generic name is kept rather than nulled).
  2. DRAW  — a `streams` FeatureCollection is written into the doc: ONE clean
     merged line per named river that carries >=1 site (union + linemerge of its
     segments, culverted/underground reaches excluded, sub-200 m stray fragments
     dropped, 5 m simplify). Per river the geometry comes from a single source
     (OSM preferred, NHD when OSM is fragmentary) so the map never draws
     overlapping parallel duplicates. The explorer renders these directly — no
     live Overpass dependency for scored regions.
  3. SNAP  — each named site's DISPLAY coordinate is snapped to the nearest
     point on its river's drawn line when that line passes within 150 m. The
     original DEM coordinate is preserved in `dem_lat`/`dem_lon` (and the
     untouched `lat`/`lon` properties). NOTHING scientific changes: every score,
     sub-score, rank, and DEM-derived parameter is byte-identical — only the
     displayed geometry moves, and only within 150 m.

Name/geometry sources (both cached on disk, see core.real_sources):
  - OpenStreetMap named waterway ways (Overpass; tags kept so culverted reaches
    are excluded from DRAWN geometry while still allowed to lend their NAME).
  - USGS NHDPlus flowlines carrying a GNIS name (WaterData GeoServer WFS).

Honest failure states: if EITHER source fails to fetch, the file is left
exactly as it was (names, geometry, snaps, streams) — a partial run must never
rewrite the drawn rivers out from under sites that keep their names/snapped
positions from a healthier run. A site with no named waterway within 1 km keeps
river_name null and its DEM position. Idempotent: re-runs re-derive the snap
from dem_lat/dem_lon, never from an already-snapped coordinate.

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
import pyproj
from shapely.geometry import LineString, MultiLineString, Point, mapping
from shapely.geometry import shape as shape_of
from shapely.ops import linemerge, nearest_points, unary_union

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

# Display snap + drawn-geometry hygiene.
SNAP_M = 150.0        # snap a site onto its river's drawn line only within this
MIN_PART_M = 200.0    # drop stray merged fragments shorter than this ...
MIN_TOTAL_M = 1000.0  # ... but only when the river has >= this much total length
SIMPLIFY_M = 5.0      # simplify tolerance for drawn lines (UTM metres)


def _is_generic(name):
    return name.strip().lower() in GENERIC


def _underground(tags):
    t = tags or {}
    return (t.get("tunnel") in ("yes", "culvert", "building_passage")
            or t.get("covered") == "yes" or t.get("layer") in ("-1", "-2"))


def named_waterways(bbox):
    """Named OSM waterway ways within bbox (west,south,east,north). Cached.
    Returns [(name, [(lon,lat),...], meta), ...] where meta carries the waterway
    type and whether the reach is underground (culvert/tunnel/covered) — culverted
    reaches may lend their NAME but are excluded from DRAWN geometry. Returns
    None on fetch failure (distinct from an empty list = 'genuinely none')."""
    w, s, e, n = bbox
    q = (f'[out:json][timeout:90];'
         f'(way["waterway"]["name"]({s},{w},{n},{e}););out geom;')
    for url in OVERPASS:
        j = cached_get_json(url, {"data": q}, kind="rivername", timeout=120)
        if j and "elements" in j:
            out = []
            for el in j["elements"]:
                g = el.get("geometry")
                tags = el.get("tags", {}) or {}
                nm = (tags.get("name", "") or "").strip()
                if g and len(g) >= 2 and nm:
                    meta = {"waterway": tags.get("waterway", "stream"),
                            "underground": _underground(tags)}
                    out.append((nm, [(p["lon"], p["lat"]) for p in g], meta))
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


def _waterway_order(ww):
    return {"river": 4, "canal": 3}.get(ww, 2)


def _merge_river(geoms_utm):
    """Union + line-merge a river's segment geometries into clean parts, dropping
    short stray fragments (never the whole river). Returns [LineString] in UTM."""
    if not geoms_utm:
        return []
    u = unary_union(geoms_utm)
    m = linemerge(u) if u.geom_type != "LineString" else u
    parts = list(m.geoms) if m.geom_type == "MultiLineString" else [m]
    parts = [p for p in parts if p.geom_type == "LineString" and p.length > 0]
    total = sum(p.length for p in parts)
    if total >= MIN_TOTAL_M:
        kept = [p for p in parts if p.length >= MIN_PART_M]
        if kept:
            parts = kept
    return [p.simplify(SIMPLIFY_M) for p in parts]


def _round_coords(geom_4326, nd=6):
    gj = mapping(geom_4326)

    def rnd(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            return [round(c[0], nd), round(c[1], nd)]
        return [rnd(x) for x in c]

    gj["coordinates"] = rnd(gj["coordinates"])
    return gj


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

    fb = (bbox[0] - BBOX_BUFFER_DEG, bbox[1] - BBOX_BUFFER_DEG,
          bbox[2] + BBOX_BUFFER_DEG, bbox[3] + BBOX_BUFFER_DEG)
    osm = named_waterways(fb)
    nhd = named_flowlines_nhd(fb)
    if osm is None or nhd is None:
        # EITHER source down → touch nothing. Names, snapped geometry, and the
        # baked streams were derived together from a both-sources run; a partial
        # rebuild would delete drawn rivers (or swap their geometry to the
        # weaker source) while sites keep names/snaps that point at them.
        kept = sum(1 for f in feats if f["properties"].get("river_name"))
        which = ("both" if osm is None and nhd is None
                 else "overpass" if osm is None else "nhd")
        return slug, len(feats), kept, f"{which} fetch failed (kept existing)", {}

    # ── Assemble the match pool (both sources, culverts included: a culverted
    #    reach can lend its NAME) and per-river geometry pools for drawing. ──
    names, geoms, generic, srcs = [], [], [], []
    rivers = {}   # key → {display, osm:[(geom, underground)], nhd:[geom], ww:{type:len}}

    def _river(key, display):
        if key not in rivers:
            rivers[key] = {"display": display, "osm": [], "nhd": [], "ww": {}}
        return rivers[key]

    if osm:
        for nm, coords, meta in osm:
            try:
                g = LineString(coords)
            except Exception:
                continue
            names.append(nm); geoms.append(g); generic.append(_is_generic(nm)); srcs.append("osm")
            r = _river(nm.strip().lower(), nm)
            r["osm"].append((g, bool(meta.get("underground"))))
            ww = meta.get("waterway", "stream")
            r["ww"][ww] = r["ww"].get(ww, 0) + g.length
    if nhd:
        for nm, gj in nhd:
            try:
                g = shape_of(gj)
            except Exception:
                continue
            names.append(nm); geoms.append(g); generic.append(_is_generic(nm)); srcs.append("nhd")
            _river(nm.strip().lower(), nm)["nhd"].append(g)

    if not geoms:
        # Both sources succeeded but returned nothing named nearby. Null the
        # names, and ALSO undo any previous run's snapping (restore the DEM
        # position, drop the dem_/snap_ markers) so the file never carries a
        # snapped coordinate pointing at a river that is no longer drawn.
        for f in feats:
            p = f["properties"]
            p["river_name"] = None
            if p.get("dem_lat") is not None and p.get("dem_lon") is not None:
                f["geometry"]["coordinates"] = [p["dem_lon"], p["dem_lat"]]
            p.pop("dem_lat", None)
            p.pop("dem_lon", None)
            p.pop("snap_dist_m", None)
        doc["streams"] = {"type": "FeatureCollection", "features": []}
        path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        return slug, len(feats), 0, "no named waterways in bbox", {}

    match_gdf = gpd.GeoDataFrame(
        {"name": names, "is_generic": generic, "src": srcs},
        geometry=geoms, crs="EPSG:4326").to_crs(utm)
    sindex = match_gdf.sindex

    # ── Site source coordinates: ALWAYS the DEM position. dem_lat/dem_lon (from
    #    a previous snapped run) win over the (possibly snapped) geometry, which
    #    is what makes re-runs idempotent instead of drifting. ──
    src_lonlat = []
    for f in feats:
        p = f["properties"]
        if p.get("dem_lat") is not None and p.get("dem_lon") is not None:
            src_lonlat.append((float(p["dem_lon"]), float(p["dem_lat"])))
        else:
            c = f["geometry"]["coordinates"]
            src_lonlat.append((float(c[0]), float(c[1])))
    sites_utm = gpd.GeoSeries([Point(lon, lat) for lon, lat in src_lonlat],
                              crs="EPSG:4326").to_crs(utm)

    # ── 1. NAME each site (both sources are healthy past the outage gate) ──
    named = 0
    stats = {"osm": 0, "nhd": 0, "generic": 0, "t300": 0, "t600": 0, "t1000": 0,
             "snapped": 0, "rivers": 0}
    site_river = [None] * len(feats)
    for i, pt in enumerate(sites_utm):
        name, dist, srctag = _match(pt, match_gdf, sindex)
        if name:
            feats[i]["properties"]["river_name"] = name
            site_river[i] = name.strip().lower()
            named += 1
            stats[srctag] = stats.get(srctag, 0) + 1
            if _is_generic(name):
                stats["generic"] += 1
            stats["t300" if dist <= TIERS[0] else
                  "t600" if dist <= TIERS[1] else "t1000"] += 1
        else:
            feats[i]["properties"]["river_name"] = None   # honest null

    # ── 2. DRAW: one merged line per site-bearing river ──
    site_keys = {}
    for k in site_river:
        if k:
            site_keys[k] = site_keys.get(k, 0) + 1
    to_4326 = {}          # cache: key → transformer via GeoSeries below
    drawn_parts_utm = {}  # key → [LineString] (UTM, for snapping)
    stream_feats = []
    for key in sorted(site_keys):
        r = rivers.get(key)
        if not r:
            continue
        osm_surface = [g for g, under in r["osm"] if not under]
        osm_len = sum(g.length for g in osm_surface)
        nhd_len = sum(g.length for g in r["nhd"])
        # One source per river → no overlapping parallel OSM/NHD duplicates.
        # Prefer OSM (higher resolution); fall back to NHD when OSM is absent or
        # clearly fragmentary compared to NHD's coverage of the same river.
        if osm_surface and (not r["nhd"] or osm_len >= 0.5 * nhd_len):
            chosen, source = osm_surface, "osm"
        elif r["nhd"]:
            chosen, source = r["nhd"], "nhd"
        else:
            continue   # river named only by fully-culverted OSM ways → nothing to draw
        # Work in UTM (metres) for merge thresholds + snapping.
        chosen_utm = list(gpd.GeoSeries(chosen, crs="EPSG:4326").to_crs(utm))
        parts = _merge_river(chosen_utm)
        if not parts:
            continue
        drawn_parts_utm[key] = parts
        parts_4326 = list(gpd.GeoSeries(parts, crs=utm).to_crs("EPSG:4326"))
        geom = parts_4326[0] if len(parts_4326) == 1 else MultiLineString(parts_4326)
        ww = max(r["ww"], key=r["ww"].get) if r["ww"] else (
            "river" if key.endswith("river") else "stream")
        total_km = sum(p.length for p in parts) / 1000.0
        stream_feats.append({
            "type": "Feature",
            "geometry": _round_coords(geom),
            "properties": {"name": r["display"], "riverKey": key,
                           "waterway": ww, "order": _waterway_order(ww),
                           "source": source, "n_sites": site_keys[key],
                           "length_km": round(total_km, 2)},
        })
    stats["rivers"] = len(stream_feats)
    doc["streams"] = {"type": "FeatureCollection", "features": stream_feats}

    # ── 3. SNAP each named site onto its river's drawn line (≤ SNAP_M) ──
    tf = pyproj.Transformer.from_crs(utm, "EPSG:4326", always_xy=True)
    for i, f in enumerate(feats):
        p = f["properties"]
        lon0, lat0 = src_lonlat[i]
        key = site_river[i]
        snapped = False
        if key and key in drawn_parts_utm:
            pt = sites_utm.iloc[i]
            drawn = unary_union(drawn_parts_utm[key]) if len(drawn_parts_utm[key]) > 1 \
                else drawn_parts_utm[key][0]
            d = pt.distance(drawn)
            if d <= SNAP_M:
                on_line = nearest_points(drawn, pt)[0]
                slon, slat = tf.transform(on_line.x, on_line.y)
                f["geometry"]["coordinates"] = [round(slon, 6), round(slat, 6)]
                p["dem_lat"] = lat0
                p["dem_lon"] = lon0
                p["snap_dist_m"] = round(d, 1)
                stats["snapped"] += 1
                snapped = True
        if not snapped:
            # Restore/keep the DEM position; drop stale snap markers from any
            # previous run whose match no longer holds.
            f["geometry"]["coordinates"] = [lon0, lat0]
            p.pop("dem_lat", None)
            p.pop("dem_lon", None)
            p.pop("snap_dist_m", None)

    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return slug, len(feats), named, "ok", stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--pace", type=float, default=2.0)
    args = ap.parse_args()
    slugs = ([args.only] if args.only else
             sorted(p.stem for p in REGIONS.glob("*.geojson")))
    tot_named = tot_sites = 0
    agg = {"osm": 0, "nhd": 0, "generic": 0, "t300": 0, "t600": 0, "t1000": 0,
           "snapped": 0, "rivers": 0}
    for i, slug in enumerate(slugs):
        try:
            s, n, named, status, st = add_names(slug)
        except Exception as e:
            s, n, named, status, st = slug, 0, 0, f"ERROR {type(e).__name__}: {e}", {}
        nullpct = f"{100*(n-named)/n:.0f}%" if n else "—"
        extra = ""
        if st:
            extra = (f" [osm {st.get('osm',0)} · nhd {st.get('nhd',0)} · "
                     f"snap {st.get('snapped',0)} · rivers {st.get('rivers',0)}]")
            for k in agg:
                agg[k] += st.get(k, 0)
        print(f"  {s:20s} {named:>4}/{n:<4} named · null {nullpct:>4} · {status}{extra}", flush=True)
        tot_named += named; tot_sites += n
        if i < len(slugs) - 1:
            time.sleep(args.pace)
    if tot_sites:
        print(f"\n  TOTAL {tot_named}/{tot_sites} named "
              f"({100*(tot_sites-tot_named)/tot_sites:.1f}% null) · "
              f"snapped {agg['snapped']} · drawn rivers {agg['rivers']} · "
              f"by source: osm {agg['osm']} · nhd {agg['nhd']} · generic {agg['generic']} · "
              f"tiers ≤300m {agg['t300']} / ≤600m {agg['t600']} / ≤1km {agg['t1000']}", flush=True)


if __name__ == "__main__":
    main()
