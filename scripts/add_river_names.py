#!/usr/bin/env python3
"""
Bake real river names, drawn waterway geometry, and on-creek display positions
into every scored region file.

Region sites come from the DEM stream network and carry only a numeric segment
id. This script enriches mock_data/regions/<slug>.geojson server-side, from REAL
sources only (no fabricated names or geometry):

  1. NAME  — each site gets `river_name`: its nearest NAMED waterway within 1 km
     (distance-tiered, whole-geometry snap, specific names preferred over bare
     generics, but a real generic name is kept rather than nulled). Sites with
     no named waterway within 1 km stay null; if such a site sits on a real but
     UNNAMED mapped waterway it is flagged `on_unnamed_waterway` so the UI can
     say "Unnamed creek" instead of a bare segment id.
  2. DRAW  — a `streams` FeatureCollection with the FULL mapped waterway
     network:
       - one merged feature per NAMED river's surface reaches. Where OSM only
         partially maps a river, NHD flowlines that are NOT near any OSM
         geometry of that river are merged in as gap-fill — rivers no longer
         cut off where one source stops. (Near-duplicate NHD is dropped, so no
         parallel double lines.)
       - that river's CULVERTED/UNDERGROUND reaches (>=100 m) as a separate
         feature with underground:1 — the explorer draws them dashed, so a
         river that dives under a city stays visually continuous instead of
         leaving floating fragments.
       - every UNNAMED surface waterway (OSM, same per-type length gates as the
         live explorer, plus NHD flowlines with no GNIS name that OSM doesn't
         cover) as unnamed context lines — DEM sites usually sit on exactly
         these, and without them they looked like dots floating on nothing.
     Rivers carrying >=1 site render bright; everything else renders as
     distance-faded context.
  3. SNAP  — each named site's DISPLAY coordinate snaps to the nearest point on
     its river's drawn geometry (surface or dashed-underground) within 150 m.
     Unnamed sites snap to the nearest unnamed waterway within 150 m. The
     original DEM coordinate is preserved in `dem_lat`/`dem_lon` (and the
     untouched `lat`/`lon` properties). NOTHING scientific changes: every
     score, sub-score, rank, and DEM-derived parameter is byte-identical —
     only the displayed geometry moves, and only within 150 m.

Sources (all cached on disk, see core.real_sources):
  - OSM named waterway ways (Overpass, kind=rivername) — the NAMING pool. Kept
    as its own cached query so names stay stable across re-runs.
  - OSM ALL waterway ways incl. unnamed (Overpass, kind=riverall) — geometry.
  - USGS NHDPlus flowlines, named + unnamed (WaterData WFS, kind=nhdname).

Honest failure states: if ANY source fails to fetch, the file is left exactly
as it was (names, geometry, snaps, streams). Idempotent: re-runs re-derive the
snap from dem_lat/dem_lon, never from an already-snapped coordinate.

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
SNAP_M = 150.0        # snap a site onto drawn geometry only within this
MIN_PART_M = 200.0    # drop stray merged fragments shorter than this ...
MIN_TOTAL_M = 1000.0  # ... but only when the river has >= this much total length
SIMPLIFY_M = 5.0      # simplify tolerance for drawn lines (UTM metres)
MIN_UNDER_M = 100.0   # underground reaches shorter than this are road-crossing
                      # stubs — skipped (the "dashed culvert spaghetti" guard)
MIN_CONTEXT_M = 500.0 # site-less NAMED rivers shorter than this are noise
NHD_DUP_M = 40.0      # NHD line ~this close to OSM geometry = same channel; drop
# Unnamed OSM ways: same per-type minimum lengths the live explorer uses, so a
# backyard ditch doesn't become map clutter.
MIN_LEN_M = {"river": 50, "canal": 50, "stream": 120, "drain": 250, "ditch": 250}


def _is_generic(name):
    return name.strip().lower() in GENERIC


def _underground(tags):
    t = tags or {}
    return (t.get("tunnel") in ("yes", "culvert", "building_passage")
            or t.get("covered") == "yes" or t.get("layer") in ("-1", "-2"))


def named_waterways(bbox):
    """Named OSM waterway ways within bbox (west,south,east,north). Cached
    (kind=rivername) — the stable NAMING pool. Returns
    [(name, [(lon,lat),...], meta), ...] or None on fetch failure."""
    w, s, e, n = bbox
    q = (f'[out:json][timeout:90];'
         f'(way["waterway"]["name"]({s},{w},{n},{e}););out geom;')
    for url in OVERPASS:
        j = cached_get_json(url, {"data": q}, kind="rivername", timeout=120)
        if j and "remark" in j:
            continue   # Overpass 200-with-remark = truncated/timed out — not usable
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


def all_waterways(bbox):
    """ALL OSM waterway ways (named + unnamed) within bbox. Cached
    (kind=riverall) — the GEOMETRY pool. Returns
    [(name_or_empty, [(lon,lat),...], meta), ...] or None on fetch failure."""
    w, s, e, n = bbox
    q = (f'[out:json][timeout:120];'
         f'(way["waterway"~"^(river|stream|canal|drain|ditch)$"]'
         f'({s},{w},{n},{e}););out geom;')
    for url in OVERPASS:
        j = cached_get_json(url, {"data": q}, kind="riverall", timeout=150)
        if j and "remark" in j:
            continue   # truncated/timed-out response — not usable
        if j and "elements" in j:
            out = []
            for el in j["elements"]:
                g = el.get("geometry")
                tags = el.get("tags", {}) or {}
                if not g or len(g) < 2:
                    continue
                meta = {"waterway": tags.get("waterway", "stream"),
                        "underground": _underground(tags)}
                out.append(((tags.get("name", "") or "").strip(),
                            [(p["lon"], p["lat"]) for p in g], meta))
            return out
        time.sleep(2)
    return None


def named_flowlines_nhd(bbox):
    """ALL NHDPlus flowlines (named and unnamed) within bbox (w,s,e,n). Cached +
    paginated. Returns [(gnis_name_or_empty, geojson_geometry), ...], or None on
    total fetch failure."""
    w, s, e, n = bbox
    out, start, PAGE, MAX_PAGES = [], 0, 1000, 8
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
            return None   # partial pagination = failure; never a truncated pool
        feats = j.get("features", []) or []
        for f in feats:
            nm = ((f.get("properties", {}) or {}).get("gnis_name") or "").strip()
            g = f.get("geometry")
            if g:
                out.append((nm, g))
        if len(feats) < PAGE:
            return out          # short page = complete result set
        start += PAGE
    # MAX_PAGES full pages without a short one → the result set may extend
    # beyond the cap. A truncated pool must never feed the naming or the
    # stream mask — fail loudly instead.
    return None


def _match(pt, gdf, sindex):
    """Nearest named waterway to a UTM point within MAX_M, preferring specific
    names. Returns (name, distance_m) or (None, None)."""
    try:
        cand = list(sindex.query(pt.buffer(MAX_M)))
    except Exception:
        cand = list(range(len(gdf)))
    best, best_d, best_score = None, None, None
    for j in cand:
        row = gdf.iloc[j]
        d = row.geometry.distance(pt)
        if d > MAX_M:
            continue
        score = d + (GENERIC_PENALTY_M if row["is_generic"] else 0.0)
        if best_score is None or score < best_score:
            best_score, best, best_d = score, row["name"], d
    return best, best_d


def _waterway_order(ww):
    return {"river": 4, "canal": 3}.get(ww, 2)


def _merge_parts(geoms_utm, drop_fragments=True):
    """Union + line-merge geometries into clean LineString parts (UTM),
    optionally dropping short stray fragments (never the whole set)."""
    if not geoms_utm:
        return []
    u = unary_union(geoms_utm)
    m = linemerge(u) if u.geom_type != "LineString" else u
    parts = list(m.geoms) if m.geom_type == "MultiLineString" else [m]
    parts = [p for p in parts if p.geom_type == "LineString" and p.length > 0]
    if drop_fragments:
        total = sum(p.length for p in parts)
        if total >= MIN_TOTAL_M:
            kept = [p for p in parts if p.length >= MIN_PART_M]
            if kept:
                parts = kept
    return [p.simplify(SIMPLIFY_M) for p in parts]


def _far_from(line, obstacles_union, min_d):
    """True when >=70% of sampled points on `line` are at least min_d away from
    obstacles_union — i.e. the line covers a reach the obstacles don't."""
    if obstacles_union is None or obstacles_union.is_empty:
        return True
    n = max(3, min(15, int(line.length // 200) + 3))
    far = 0
    for i in range(n):
        p = line.interpolate(line.length * (i + 0.5) / n)
        if p.distance(obstacles_union) >= min_d:
            far += 1
    return far >= 0.7 * n


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
    named = named_waterways(fb)
    allw = all_waterways(fb)
    nhd = named_flowlines_nhd(fb)
    if named is None or allw is None or nhd is None:
        # ANY source down → touch nothing. Names, snapped geometry and drawn
        # rivers are derived together; a partial rebuild would strand them.
        kept = sum(1 for f in feats if f["properties"].get("river_name"))
        which = ("overpass-names" if named is None else
                 "overpass-all" if allw is None else "nhd")
        return slug, len(feats), kept, f"{which} fetch failed (kept existing)", {}

    to_utm = pyproj.Transformer.from_crs("EPSG:4326", utm, always_xy=True).transform
    tf_back = pyproj.Transformer.from_crs(utm, "EPSG:4326", always_xy=True)

    # ── NAMING pool (stable rivername cache + named NHD) ──
    pool_names, pool_geoms, pool_gen = [], [], []
    for nm, coords, meta in named:
        try:
            g = LineString(coords)
        except Exception:
            continue
        pool_names.append(nm); pool_geoms.append(g); pool_gen.append(_is_generic(nm))
    for nm, gj in nhd:
        if not nm:
            continue
        try:
            g = shape_of(gj)
        except Exception:
            continue
        pool_names.append(nm); pool_geoms.append(g); pool_gen.append(_is_generic(nm))

    if not pool_geoms:
        # Both sources succeeded but returned nothing named nearby. Null the
        # names and undo previous snapping so nothing points at missing rivers.
        for f in feats:
            p = f["properties"]
            p["river_name"] = None
            if p.get("dem_lat") is not None and p.get("dem_lon") is not None:
                f["geometry"]["coordinates"] = [p["dem_lon"], p["dem_lat"]]
            for k in ("dem_lat", "dem_lon", "snap_dist_m", "on_unnamed_waterway"):
                p.pop(k, None)
        doc["streams"] = {"type": "FeatureCollection", "features": []}
        path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        return slug, len(feats), 0, "no named waterways in bbox", {}

    match_gdf = gpd.GeoDataFrame(
        {"name": pool_names, "is_generic": pool_gen},
        geometry=pool_geoms, crs="EPSG:4326").to_crs(utm)
    sindex = match_gdf.sindex

    # ── Site source coordinates: ALWAYS the DEM position (idempotency) ──
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

    # ── 1. NAME each site ──
    n_named = 0
    stats = {"osm": 0, "nhd": 0, "generic": 0, "t300": 0, "t600": 0, "t1000": 0,
             "snapped": 0, "rivers": 0, "context": 0, "unnamed_ways": 0,
             "snap_unnamed": 0}
    site_river = [None] * len(feats)
    for i, pt in enumerate(sites_utm):
        name, dist = _match(pt, match_gdf, sindex)
        if name:
            feats[i]["properties"]["river_name"] = name
            site_river[i] = name.strip().lower()
            n_named += 1
            if _is_generic(name):
                stats["generic"] += 1
            stats["t300" if dist <= TIERS[0] else
                  "t600" if dist <= TIERS[1] else "t1000"] += 1
        else:
            feats[i]["properties"]["river_name"] = None   # honest null

    # ── 2. DRAW the full network ──
    # Group OSM geometry by river name; collect the unnamed pool. NAMED rivers
    # take geometry from BOTH queries: the riverall pool (5 channel types, any
    # name) AND the rivername pool (ANY waterway type, named) — the naming pool
    # includes named docks/weir channels etc. ("Dry Dock 6") that the 5-type
    # query misses; a site named after one must still get a drawn line.
    # unary_union dedupes ways present in both.
    rivers = {}   # key → {display, surf:[], under:[], ww:{}}
    unnamed_osm = []   # (geom4326, ww)
    for nm, coords, meta in list(allw) + [w for w in named if w[0]]:
        try:
            g = LineString(coords)
        except Exception:
            continue
        if nm:
            k = nm.strip().lower()
            r = rivers.setdefault(k, {"display": nm, "surf": [], "under": [],
                                      "ww": {}})
            (r["under"] if meta["underground"] else r["surf"]).append(g)
            r["ww"][meta["waterway"]] = r["ww"].get(meta["waterway"], 0) + g.length
        else:
            if meta["underground"]:
                continue   # unnamed culverts = pure spaghetti, skip
            unnamed_osm.append((g, meta["waterway"]))
    nhd_by_name, nhd_unnamed = {}, []
    for nm, gj in nhd:
        try:
            g = shape_of(gj)
        except Exception:
            continue
        if nm:
            nhd_by_name.setdefault(nm.strip().lower(), []).append(g)
        else:
            nhd_unnamed.append(g)

    site_keys = {}
    for k in site_river:
        if k:
            site_keys[k] = site_keys.get(k, 0) + 1

    def utm_series(geoms):
        return list(gpd.GeoSeries(geoms, crs="EPSG:4326").to_crs(utm)) if geoms else []

    # All OSM geometry (any name, incl. underground) in UTM — the obstacle set
    # NHD must stay clear of to count as gap-fill rather than a duplicate.
    _all_osm = []
    for _nm, c, _m in allw:
        try:
            _all_osm.append(LineString(c))
        except Exception:
            continue
    all_osm_utm = utm_series(_all_osm)
    all_osm_union = unary_union(all_osm_utm) if all_osm_utm else None

    stream_feats = []
    snap_targets = {}       # riverKey → [parts] (surface + underground, UTM)
    for key in sorted(set(rivers) | set(nhd_by_name)):
        r = rivers.get(key, {"display": None, "surf": [], "under": [], "ww": {}})
        n_sites = site_keys.get(key, 0)
        surf_utm = utm_series(r["surf"])
        under_utm = utm_series(r["under"])
        river_osm_union = unary_union(surf_utm + under_utm) if (surf_utm or under_utm) else None
        # NHD gap-fill: keep only flowlines of this river that OSM doesn't cover.
        fill = [g for g in utm_series(nhd_by_name.get(key, []))
                if _far_from(g, river_osm_union, NHD_DUP_M)]
        display = r["display"] or next(
            (nm for nm, gj in nhd if nm and nm.strip().lower() == key), key)
        surf_parts = _merge_parts(surf_utm) + _merge_parts(fill)
        under_parts = [p for p in _merge_parts(under_utm, drop_fragments=False)
                       if p.length >= MIN_UNDER_M]
        total_m = sum(p.length for p in surf_parts) + sum(p.length for p in under_parts)
        if not surf_parts and not under_parts:
            continue
        if n_sites == 0 and total_m < MIN_CONTEXT_M:
            continue   # tiny site-less named fragment → noise
        ww = max(r["ww"], key=r["ww"].get) if r["ww"] else (
            "river" if key.endswith("river") else "stream")
        if n_sites > 0:
            snap_targets[key] = surf_parts + under_parts
        for parts, under in ((surf_parts, 0), (under_parts, 1)):
            if not parts:
                continue
            back = list(gpd.GeoSeries(parts, crs=utm).to_crs("EPSG:4326"))
            geom = back[0] if len(back) == 1 else MultiLineString(back)
            stream_feats.append({
                "type": "Feature",
                "geometry": _round_coords(geom),
                "properties": {"name": display, "riverKey": key,
                               "waterway": ww, "order": _waterway_order(ww),
                               "underground": under, "n_sites": n_sites,
                               "length_km": round(sum(p.length for p in parts) / 1000.0, 2)},
            })
        stats["rivers" if n_sites else "context"] += 1

    # Unnamed pool: OSM unnamed surface ways (per-type length gates) + NHD
    # unnamed flowlines clear of ALL OSM geometry. These draw as unnamed context
    # and are the snap targets for unnamed (Segment N) sites.
    unnamed_parts = []      # (LineString UTM, ww)
    for g, ww in zip(utm_series([g for g, _ in unnamed_osm]),
                     [ww for _, ww in unnamed_osm]):
        if g.length >= MIN_LEN_M.get(ww, 120):
            unnamed_parts.append((g.simplify(SIMPLIFY_M * 2), ww))  # coarser: faded context
    for g in utm_series(nhd_unnamed):
        if g.length >= 120 and _far_from(g, all_osm_union, NHD_DUP_M):
            unnamed_parts.append((g.simplify(SIMPLIFY_M * 2), "stream"))
    if unnamed_parts:
        back = list(gpd.GeoSeries([g for g, _ in unnamed_parts],
                                  crs=utm).to_crs("EPSG:4326"))
        for (g_utm, ww), g4326 in zip(unnamed_parts, back):
            stream_feats.append({
                "type": "Feature",
                "geometry": _round_coords(g4326),
                "properties": {"name": None, "riverKey": None,
                               "waterway": ww, "order": _waterway_order(ww),
                               "underground": 0, "n_sites": 0,
                               "length_km": round(g_utm.length / 1000.0, 2)},
            })
    stats["unnamed_ways"] = len(unnamed_parts)
    doc["streams"] = {"type": "FeatureCollection", "features": stream_feats}

    unnamed_union = unary_union([g for g, _ in unnamed_parts]) if unnamed_parts else None

    # ── 3. SNAP sites onto drawn geometry (≤ SNAP_M) ──
    for i, f in enumerate(feats):
        p = f["properties"]
        lon0, lat0 = src_lonlat[i]
        pt = sites_utm.iloc[i]
        snapped = False
        target, mark_unnamed = None, False
        key = site_river[i]
        if key and key in snap_targets:
            target = unary_union(snap_targets[key]) if len(snap_targets[key]) > 1 \
                else snap_targets[key][0]
        elif key is None and unnamed_union is not None:
            target, mark_unnamed = unnamed_union, True
        if target is not None and not target.is_empty:
            d = pt.distance(target)
            if d <= SNAP_M:
                on_line = nearest_points(target, pt)[0]
                slon, slat = tf_back.transform(on_line.x, on_line.y)
                f["geometry"]["coordinates"] = [round(slon, 6), round(slat, 6)]
                p["dem_lat"] = lat0
                p["dem_lon"] = lon0
                p["snap_dist_m"] = round(d, 1)
                if mark_unnamed:
                    p["on_unnamed_waterway"] = True
                    stats["snap_unnamed"] += 1
                else:
                    p.pop("on_unnamed_waterway", None)
                stats["snapped"] += 1
                snapped = True
        if not snapped:
            f["geometry"]["coordinates"] = [lon0, lat0]
            for k in ("dem_lat", "dem_lon", "snap_dist_m", "on_unnamed_waterway"):
                p.pop(k, None)

    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return slug, len(feats), n_named, "ok", stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--pace", type=float, default=2.0)
    args = ap.parse_args()
    slugs = ([args.only] if args.only else
             sorted(p.stem for p in REGIONS.glob("*.geojson")))
    tot_named = tot_sites = 0
    agg = {"snapped": 0, "snap_unnamed": 0, "rivers": 0, "context": 0,
           "unnamed_ways": 0, "t300": 0, "t600": 0, "t1000": 0, "generic": 0}
    for i, slug in enumerate(slugs):
        try:
            s, n, n_named, status, st = add_names(slug)
        except Exception as e:
            s, n, n_named, status, st = slug, 0, 0, f"ERROR {type(e).__name__}: {e}", {}
        nullpct = f"{100*(n-n_named)/n:.0f}%" if n else "—"
        extra = ""
        if st:
            extra = (f" [snap {st.get('snapped',0)} ({st.get('snap_unnamed',0)} unnamed) · "
                     f"rivers {st.get('rivers',0)}+{st.get('context',0)}ctx+"
                     f"{st.get('unnamed_ways',0)}unnamed]")
            for k in agg:
                agg[k] += st.get(k, 0)
        print(f"  {s:20s} {n_named:>4}/{n:<4} named · null {nullpct:>4} · {status}{extra}", flush=True)
        tot_named += n_named; tot_sites += n
        if i < len(slugs) - 1:
            time.sleep(args.pace)
    if tot_sites:
        print(f"\n  TOTAL {tot_named}/{tot_sites} named "
              f"({100*(tot_sites-tot_named)/tot_sites:.1f}% null) · "
              f"snapped {agg['snapped']} (of which {agg['snap_unnamed']} onto unnamed ways) · "
              f"rivers {agg['rivers']} bright + {agg['context']} named-context + "
              f"{agg['unnamed_ways']} unnamed ways", flush=True)


if __name__ == "__main__":
    main()
