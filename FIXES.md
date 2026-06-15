# GRIME — Audit Fix Log

Tracks every finding from `AUDIT.md` / `FIX_PROMPT.md` (audit performed against
commit `d2a0a33`). One row per finding: ID · one-sentence fix · status · proof.

Status legend: ✅ done · ⏭️ skipped (with reason) · 🔜 deferred (with reason)

**Decisions honored:** 27 parameters (7 gen / 7 flow / 7 impact / 6 feas); EJ
re-sourced from Census ACS; code is ground truth for docs; explorer stays an
honest, deterministic, separately-labeled heuristic.

---

## Baseline verification (reproduced before any fix — `/tmp/verify_audit.py`)

```
C5  param counts gen=7 flow=7 impact=7 feas=6 = 27   → "28" is WRONG (19 textual hits)
H1  places.json = 89,518 places / 239 countries      (claim 108,772 / 240; 17 hits)
C2  constant column through MinMaxScaler → [0,0,0,0,0,0]   (README claims 0.5 → M2)
C1a acc=50000 × (0.0000898°)²/1e6 = 4.03e-10 → clamped to 0.01 km²  (10 m grid → 5.0 km²)
C1b pixels_downstream = int(100 / 0.0000898) = 1,113,585 → slope clamps to DEM bottom row
H5  corr(estuary,beach)=1.000000; dlat term = 0.0 (coast_lat=lat); shipped beach/estuary ratio = 1.1
M1  width 2.5·2.5^order: o3=39.1 o4=97.7 o5=244.1 m → trips 50 m gate at order ≥ 4
C4  ejscreenRESTbroker.aspx → HTTP 000 (endpoint dead, EPA removed EJSCREEN 2025-02-05)
shipped candidates.geojson: catchment max = 63.5 km² (audit's cited value), perfectly
     linear 0.5→63.5 step 2.25 + beach=estuary×1.1  → confirms it is a synthetic showcase
gARB/gRIME: 13 hits in core/scripts/api/start.sh
div balance: index 57/57 ok · explore 80/80 ok · docs 102/100 UNBALANCED (2 unclosed)
Environment note: pysheds/py3dep/rioxarray NOT installed here; Census ACS now returns
     "Missing Key" (needs free CENSUS_API_KEY); TIGERweb + general net reachable; EJSCREEN dead.
     → Phase 3/H2 verified via synthetic-DEM scratch tests (as the audit did); Phase 2 EJ math
       verified on synthetic block groups; live calls documented + key-gated.
```

---

## Phase 0 — Carried-over bugs (XSS + JS races)

| ID | Fix | Status |
|----|-----|--------|
| X1 | Added `esc()` (escapes `& < > " '`) and wrapped every third-party string before `innerHTML` insertion: place names + country names (`placeItem`, no-data panel), OSM stream names + waterway (`renderSelectedCandHtml`), `focusedRiverName` + per-site names (`renderRiverFocusHtml`, `renderCityView`). `textContent` sites left inert. | ✅ |
| X2 | `openCity` now `await`s `fetchRealStreams` into a **local** `fetched`, runs the `activeCityIdx!==idx` staleness guard, and only then assigns the global `activeStreams` — a slow city-A fetch can no longer clobber city B. | ✅ |
| X3 | Moved the `clusters`/`unclustered` click + hover handlers out of `loadWorldView` (re-run on every "← All cities"/search/theme reload → leaked a listener each time → N-fold `openCity`) into `initMap` (attached once). | ✅ |

**Verification / proof:**
- `node --check` on extracted inline JS → **SYNTAX OK**.
- `esc('<img src=x onerror=alert(1)>')` → `&lt;img src=x onerror=alert(1)&gt;` → **PASS**.
- `grep` for unescaped `${p.n|c.stream|focusedRiverName|cName}` in templates → only 3 hits, all `textContent` (inert).
- `on('click','clusters'|'unclustered')` now appears only inside `initMap` (lines 840–841); none in `loadWorldView`.
- div balance unchanged (explore 80/80).
