"""
GRIME — real per-site data sources for the 16 constant-fallback parameters.

This module ONLY fetches real values and maps them onto the existing 27
parameters. It changes NO weights, curves, thresholds, gates, or scoring math —
scoring is still done by core.scoring exactly as shipped. Every source is free
and keyless (Census ACS aside, which is used by the already-wired pop/EJ path).

Verified sources (endpoint-checked 2026-07-06):
  - NLDI          snap (lat,lon) -> NHDPlus COMID
  - USGS WaterData GeoServer WFS (wmadata:nhdflowline_network) — per-COMID EROM
                  mean/monthly flow, drainage area -> usgs_mean_q_cfs, seasonal_cv
  - EPA StreamCat REST (api.epa.gov) — per-catchment % impervious -> impervious_pct
                  (-> runoff_coeff_C derives automatically)
  - USGS SIR 2014-5030, Table 7, Hydrologic Region 1 (Piedmont) — 10% AEP flood
                  regression -> flood_q10_cfs  (DRNAREA in mi², IMPNLCD06 in %)
  - Bieger et al. 2015, JAWRA 51(3), Table 3, Appalachian Highlands division
                  (contains the NC Piedmont) — bankfull width W(m)=3.12·DA(km²)^0.415
                  -> channel_width_m -> channel_width_score
  - EPA Envirofacts TRI (Durham County) — with the longitude-sign + null fix
                  -> tri_facility_density
  - EPA ECHO bulk downloads — NPDES outfalls -> npdes_points; CSO inventory
                  -> cso_density (truthfully 0 for Durham's separated sewers)
  - FHWA/BTS National Bridge Inventory (NTAD) — bridge points -> bridge_proximity_bonus
  - Durham County parcels (PROPERTY_OWNER) — public/private -> land_ownership
  - NC OneMap public water supply sources — downstream intakes -> water_intake_score

Anything that can't be made real stays a documented fallback (see PROVENANCE in
scripts/wire_real_data.py) — no fabricated values.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import zipfile
from pathlib import Path

import requests

# ── on-disk HTTP cache with exponential backoff ──────────────────────
_CACHE_DIR = Path(os.environ.get("GRIME_WIRE_CACHE", "cache/wire"))
_UA = {"User-Agent": "GRIME-research/1.0 (Stockholm Junior Water Prize project)"}


def _cache_path(kind: str, key: str) -> Path:
    h = hashlib.sha1(key.encode()).hexdigest()[:20]
    d = _CACHE_DIR / kind
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{h}.json"


def cached_get_json(url, params=None, kind="get", retries=4, timeout=45,
                    backoff=3.0, ok_empty=True):
    """GET JSON with a disk cache + exponential backoff. Returns parsed JSON or
    None. Rate-limit-aware (retries on 429/500/502/503/504)."""
    key = url + "?" + json.dumps(params or {}, sort_keys=True)
    cp = _cache_path(kind, key)
    if cp.exists():
        try:
            return json.loads(cp.read_text())
        except Exception:
            pass
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=_UA, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(backoff * (2 ** attempt))
                continue
            if r.status_code != 200:
                return None
            data = r.json()
            # ArcGIS/EPA services wrap failures in an HTTP-200 {"error": ...}
            # body — treat those as retryable and NEVER cache them, or one
            # transient error poisons the key for every later run.
            if isinstance(data, dict) and set(data.keys()) == {"error"}:
                last = f"200-wrapped error: {str(data['error'])[:80]}"
                time.sleep(backoff * (2 ** attempt))
                continue
            cp.write_text(json.dumps(data))
            return data
        except Exception as e:  # network / JSON error
            last = f"{type(e).__name__}: {e}"
            time.sleep(backoff * (2 ** attempt))
    print(f"  [warn] cached_get_json gave up on {url} ({last})")
    return None


def cached_download(url, kind="dl", retries=3, timeout=180, backoff=5.0):
    """Download a (large) binary file to the cache once; return its local path
    or None."""
    cp = _cache_path(kind, url).with_suffix(".bin")
    if cp.exists() and cp.stat().st_size > 0:
        return cp
    for attempt in range(retries):
        try:
            with requests.get(url, headers=_UA, timeout=timeout, stream=True) as r:
                if r.status_code != 200:
                    time.sleep(backoff * (2 ** attempt))
                    continue
                tmp = cp.with_suffix(".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                tmp.rename(cp)
                return cp
        except Exception as e:
            print(f"  [warn] download retry {attempt} for {url}: {e}")
            time.sleep(backoff * (2 ** attempt))
    return None


# ── unit helpers ─────────────────────────────────────────────────────
# NHDPlusV2 EROM mean/monthly flow (qe_ma, qe_01..12) is already in cubic feet
# per second — no cms conversion.
KM2_TO_MI2 = 0.386102159


# ── 1. NLDI: (lat, lon) -> COMID ─────────────────────────────────────

def comid_for_point(lat, lon):
    """Snap a point to its NHDPlus flowline COMID via NLDI. None on failure."""
    url = "https://api.water.usgs.gov/nldi/linked-data/comid/position"
    j = cached_get_json(url, {"coords": f"POINT({lon} {lat})"}, kind="nldi")
    try:
        return int(j["features"][0]["properties"]["comid"])
    except Exception:
        return None


# ── 2. EROM per-COMID (mean + monthly flow, drainage area, width inputs) ──

def erom_for_comids(comids):
    """Batch-fetch EROM attributes for a set of COMIDs from the USGS WaterData
    GeoServer WFS. Returns {comid: {...}}."""
    out = {}
    comids = sorted({int(c) for c in comids if c is not None})
    CHUNK = 40
    for i in range(0, len(comids), CHUNK):
        chunk = comids[i:i + CHUNK]
        cql = "comid IN (" + ",".join(str(c) for c in chunk) + ")"
        url = "https://api.water.usgs.gov/geoserver/wmadata/ows"
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeName": "wmadata:nhdflowline_network",
            "outputFormat": "application/json", "srsName": "EPSG:4326",
            "cql_filter": cql,
        }
        j = cached_get_json(url, params, kind="erom", timeout=90)
        if not j:
            continue
        for feat in j.get("features", []):
            p = feat.get("properties", {})
            try:
                out[int(p["comid"])] = p
            except Exception:
                continue
    return out


def erom_mean_q_cfs(props):
    q = props.get("qe_ma")  # already cfs
    return float(q) if q not in (None, "") else None


def erom_seasonal_cv(props):
    """CV of the 12 EROM monthly mean flows (qe_01..qe_12)."""
    vals = []
    for m in range(1, 13):
        v = props.get(f"qe_{m:02d}")
        if v not in (None, ""):
            vals.append(float(v))
    if len(vals) < 12:
        return None
    mean = sum(vals) / len(vals)
    if mean <= 0:
        return None
    var = sum((x - mean) ** 2 for x in vals) / len(vals)
    return math.sqrt(var) / mean


def erom_drainage_km2(props):
    da = props.get("totdasqkm")
    return float(da) if da not in (None, "") else None


# ── 3. StreamCat: per-catchment % impervious ─────────────────────────

def streamcat_impervious(comids):
    """% impervious (2019 NLCD, whole upstream watershed) per COMID from the
    EPA StreamCat REST API. Returns {comid: pct}. The 'ws' metric is the whole
    upstream watershed, which matches GRIME's catchment-generation construct."""
    out = {}
    comids = sorted({int(c) for c in comids if c is not None})
    CHUNK = 40
    for i in range(0, len(comids), CHUNK):
        chunk = comids[i:i + CHUNK]
        url = "https://api.epa.gov/StreamCat/streams/metrics"
        params = {"name": "pctimp2019", "comid": ",".join(str(c) for c in chunk)}
        j = cached_get_json(url, params, kind="streamcat", timeout=60)
        if not j:
            continue
        for item in j.get("items", []):
            try:
                cid = int(item["comid"])
            except Exception:
                continue
            val = item.get("pctimp2019ws")
            if val in (None, "", -9999):
                val = item.get("pctimp2019cat")
            if val not in (None, "", -9999):
                out[cid] = float(val)
    return out


# ── 4. Flood Q10 — USGS SIR 2014-5030, Table 7, Hydrologic Region 1 ──

def flood_q10_cfs_sir2014(drainage_km2, impervious_pct):
    """10-percent AEP flood (cfs) for a Piedmont (HR1) site.

    From USGS SIR 2014-5030 (Feaster, Gotvald & Weaver, 2014), Table 7, HR1:
      0.10 mi² ≤ DA ≤ 3 mi²   : Q10 = 381·DA^0.7536 · 10^(0.0076·IMP)
      3 mi²  < DA ≤ 436 mi²   : Q10 = 484·DA^0.5539 · 10^(0.0060·IMP)
    DA = drainage area in mi²; IMP = % impervious (IMPNLCD06). Valid IMP range
    per the report is roughly 0–63%; DA range 0.10–436 mi².
    """
    if drainage_km2 is None or drainage_km2 <= 0:
        return None
    da_mi2 = drainage_km2 * KM2_TO_MI2
    imp = max(0.0, float(impervious_pct or 0.0))
    if da_mi2 < 0.10:
        da_mi2 = 0.10
    if da_mi2 <= 3.0:
        return 381.0 * da_mi2 ** 0.7536 * 10 ** (0.0076 * imp)
    da = min(da_mi2, 436.0)
    return 484.0 * da ** 0.5539 * 10 ** (0.0060 * imp)


# ── 5. Channel width — Bieger et al. 2015, Table 3, Appalachian Highlands ──

def bankfull_width_m_bieger(drainage_km2):
    """Bankfull width (m) from Bieger et al. 2015, JAWRA 51(3):842-858, Table 3,
    Appalachian Highlands Division (the division containing the NC Piedmont):
      W(m) = 3.12 · DA(km²)^0.415     (R²=0.87, n=377)
    Used in place of the paywalled Doll 2002 urban-Piedmont coefficients: this is
    peer-reviewed, metric, and its predictions (4–22 m over DA 2–116 km²) are
    physically sensible for these streams. Documented, not fabricated.
    """
    if drainage_km2 is None or drainage_km2 <= 0:
        return None
    return 3.12 * drainage_km2 ** 0.415


# ── 6. EPA Envirofacts TRI (Durham County) — with the sign + null fix ─

def tri_points_durham():
    """All TRI facility points in Durham County. FIX: the Envirofacts response
    gives latitudes/longitudes as bare positive magnitudes (e.g. 78.89 for a
    Durham longitude that is really -78.89), and some rows have null coords —
    the old code fed those straight into Point(), so every facility landed in the
    eastern hemisphere and counted as zero-in-catchment. Returns [(lat, lon)]."""
    url = "https://data.epa.gov/efservice/tri_facility/state_abbr/NC/county_name/DURHAM/JSON"
    j = cached_get_json(url, kind="tri", timeout=90)
    if not j:
        return []
    pts = []
    for f in j:
        lat = f.get("pref_latitude") or f.get("fac_latitude")
        lon = f.get("pref_longitude") or f.get("fac_longitude")
        if lat in (None, "", 0) or lon in (None, "", 0):
            continue
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            continue
        # Sign fix: Durham is ~36 N, ~-78.9 W. Envirofacts drops the minus sign
        # on longitude (and occasionally latitude); restore it for the US.
        if lon > 0:
            lon = -lon
        if lat < 0:
            lat = -lat
        # sanity window for the Durham region
        if 35.0 <= lat <= 37.0 and -80.0 <= lon <= -78.0:
            pts.append((lat, lon))
    return pts


# ── 7. EPA ECHO bulk — NPDES outfalls & CSO inventory ────────────────

def _load_zip_member_csv(zip_path, name_contains):
    import csv
    if zip_path is None:
        return None, None
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")
                     and name_contains.lower() in n.lower()]
            if not names:
                names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names:
                return None, None
            with z.open(names[0]) as fh:
                text = (line.decode("latin-1") for line in fh)
                reader = csv.DictReader(text)
                rows = list(reader)
        return rows, names[0]
    except Exception as e:
        print(f"  [warn] zip read {zip_path}: {e}")
        return None, None


def npdes_outfall_points(bbox):
    """NPDES permitted outfall points within bbox (west,south,east,north) from
    the ECHO bulk download. Returns [(lat, lon)]."""
    zp = cached_download("https://echo.epa.gov/files/echodownloads/npdes_outfalls_layer.zip")
    rows, _ = _load_zip_member_csv(zp, "outfall")
    if not rows:
        return []
    w, s, e, n = bbox
    out = []
    for r in rows:
        lat = r.get("LATITUDE83") or r.get("FAC_LAT") or r.get("LATITUDE")
        lon = r.get("LONGITUDE83") or r.get("FAC_LONG") or r.get("LONGITUDE")
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            continue
        if s <= lat <= n and w <= lon <= e:
            out.append((lat, lon))
    return out


def cso_points_nc(bbox):
    """CSO outfall points within bbox from the ECHO CSO inventory. Returns
    [(lat, lon)] — expected to be EMPTY for Durham (separated sewer system), a
    truthful zero rather than a dead-endpoint fallback."""
    zp = cached_download("https://echo.epa.gov/files/echodownloads/ALL_CSO_downloads.zip")
    rows, _ = _load_zip_member_csv(zp, "cso")
    if rows is None:
        return None  # download failed → unknown, not a confirmed zero
    w, s, e, n = bbox
    out = []
    for r in rows:
        st = (r.get("STATE") or r.get("FAC_STATE") or "").strip().upper()
        lat = r.get("LATITUDE") or r.get("FAC_LAT")
        lon = r.get("LONGITUDE") or r.get("FAC_LONG")
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            continue
        if s <= lat <= n and w <= lon <= e:
            out.append((lat, lon))
    return out


# ── 8. National Bridge Inventory (NTAD ArcGIS) — bridge points ───────

def nbi_bridge_points(bbox):
    """NBI bridge points within bbox from the BTS/NTAD hosted feature service.
    Coordinates come as decimal degrees (LAT_016/LONG_017). Returns [(lat, lon)]."""
    w, s, e, n = bbox
    url = ("https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
           "NTAD_National_Bridge_Inventory/FeatureServer/0/query")
    params = {
        "where": "1=1",
        "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
        "inSR": "4326", "outSR": "4326", "outFields": "LAT_016,LONG_017",
        "returnGeometry": "false", "f": "json", "resultRecordCount": 2000,
    }
    j = cached_get_json(url, params, kind="nbi", timeout=60)
    if not j:
        return []
    out = []
    for feat in j.get("features", []):
        a = feat.get("attributes", {})
        lat, lon = a.get("LAT_016"), a.get("LONG_017")
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        # NTAD already stores decimal degrees; guard against raw DMS just in case.
        if lat > 90:  # looks like DDMMSSss
            lat = _dms_to_dd(lat)
            lon = -_dms_to_dd(abs(lon))
        if lon > 0:
            lon = -lon
        if s - 0.2 <= lat <= n + 0.2 and w - 0.2 <= lon <= e + 0.2:
            out.append((lat, lon))
    return out


def _dms_to_dd(v):
    v = abs(float(v))
    deg = int(v // 1000000)
    minutes = int((v % 1000000) // 10000)
    seconds = (v % 10000) / 100.0
    return deg + minutes / 60.0 + seconds / 3600.0


# ── 9. Durham County parcels — public/private land ownership ─────────

_PUBLIC_OWNER_HINTS = (
    "CITY OF", "COUNTY OF", "DURHAM CITY", "DURHAM COUNTY", "STATE OF",
    "NC DEPT", "NORTH CAROLINA", "UNITED STATES", "USA", "FEDERAL",
    "PARK", "RECREATION", "OPEN SPACE", "GREENWAY", "WATER", "SEWER",
    "UNIVERSITY", "SCHOOL", "BOARD OF EDUCATION", "HOUSING AUTHORITY",
    "TRANSIT", "DEPARTMENT OF", "US GOVERNMENT", "MUNICIPAL",
)


def parcel_owner_at(lat, lon):
    """Owner name of the Durham County parcel containing (lat, lon), or None."""
    url = ("https://services2.arcgis.com/G5vR3cOjh6g2Ed8E/arcgis/rest/services/"
           "Parcels_NEW/FeatureServer/0/query")
    params = {
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint",
        "inSR": "4326", "spatialRel": "esriSpatialRelIntersects",
        "outFields": "PROPERTY_OWNER", "returnGeometry": "false", "f": "json",
    }
    j = cached_get_json(url, params, kind="parcel", timeout=45)
    try:
        return j["features"][0]["attributes"].get("PROPERTY_OWNER")
    except Exception:
        return None


def land_ownership_from_owner(owner):
    """Map a parcel owner name to GRIME's land_ownership value. Public land -> 1.0,
    otherwise -> 0.5 (unknown-permission). We deliberately do NOT return 0.0
    ("confirmed private, no permission"): parcel ownership alone doesn't establish
    a deployment refusal, and 0.0 would trip the hard gate and drop the site,
    changing the candidate set. Public vs unknown still makes the parameter vary."""
    if not owner:
        return 0.5
    up = owner.upper()
    return 1.0 if any(h in up for h in _PUBLIC_OWNER_HINTS) else 0.5


# ── 10. NC OneMap public water supply sources — downstream intakes ───

def nc_water_source_points(bbox_wide):
    """Public water supply source points within a wide bbox from NC OneMap.
    Returns [(lat, lon)]. Used the same way the shipped model scores intakes:
    exp(-d/10 km) summed over sources within 50 km (core.impact.water_intake_score)."""
    w, s, e, n = bbox_wide
    url = ("https://services.nconemap.gov/secure/rest/services/"
           "NC1Map_Water_Sources/MapServer/1/query")
    params = {
        "where": "1=1", "geometry": f"{w},{s},{e},{n}",
        "geometryType": "esriGeometryEnvelope", "inSR": "4326", "outSR": "4326",
        "outFields": "source_typ", "returnGeometry": "true", "f": "json",
        "resultRecordCount": 2000,
    }
    j = cached_get_json(url, params, kind="intake", timeout=60)
    if not j:
        return []
    out = []
    for feat in j.get("features", []):
        g = feat.get("geometry", {})
        lon, lat = g.get("x"), g.get("y")
        if lat is None or lon is None:
            continue
        out.append((float(lat), float(lon)))
    return out
