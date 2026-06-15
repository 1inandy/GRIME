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

## Phase 1 — Claims, counts & hygiene

| ID | Fix | Status |
|----|-----|--------|
| C5 | "28" → **27** everywhere: README (prose, mermaid, ℝ²⁸→ℝ²⁷, §6.2/§17 pseudocode, footer), landing hero SVG + meta line, explorer stat tile, docs index/Overview.md/documentation.md, `core/scoring.py` docstrings. Weight dicts verified 7/7/7/6 = 27, each family sums to 1.0. | ✅ |
| H1 | "108,772 cities / 240 countries" → **89,518 places / 239 countries** and "cities"→"places" in all ~17 locations (README, landing, explorer splash+counter, docs ×3); added a procedural-generation provenance note to the README data table. | ✅ |
| C6 | Implemented real `optimize_weights(candidates_df, known_good_indices)` in `core/scoring.py` (lazy `skopt.gp_minimize`, objective = mean rank of known-good sites); README §6.4 status reworded to match. | ✅ |
| M6 | Added `GET /map` route (serves dashboard HTML) so README §11 is truthful; created `.env.example` (`MAPBOX_TOKEN`, `CENSUS_API_KEY`, `GRIME_ALLOWED_ORIGIN`). | ✅ |
| H4 | Created `.gitignore` (config.js, .env, data/, *.gpkg, __pycache__/, *.pyc, .venv/, .DS_Store, /uvicorn); `git rm --cached .DS_Store uvicorn`. Added `LICENSE` (MIT, © Soham Kela 2026). | ✅ |
| L1 | `gARB`/`gRIME` → **GRIME** in all of `core/`, `scripts/generate_mock.py`, `api/main.py` title, `start.sh`. | ✅ |
| L2 | Removed the stray 0-byte `uvicorn` file (untracked + gitignored). | ✅ |
| L3 | Hero stats re-sourced with real citations (Borrelle 2020 / Geyer 2017 / Eriksen 2014; fixed the 91% misattribution to Geyer's actual recycling stat); awards link to wef.org/sjwp + smathhacks.ncssm.edu (was `href="#"`). | ✅ |
| L7 | "six federal APIs" → "several federal APIs" / "five live APIs + PAD-US dataset" in Overview.md + documentation.md. | ✅ |
| L8 | Plastic-rivers stat now cites Meijer et al. 2021 (1,656 rivers, ~80%) in Overview.md + documentation.md. | ✅ |
| start.sh | Step 5 curls `/api/stats` (JSON) instead of `/` (HTML) — the old pipe to `json.tool` crashed under `set -e`; dropped "Hackathon" framing. | ✅ |

**Verification / proof:**
- `grep "28 param|108,772|240 countries|gARB|gRIME|1,500 rivers"` across README+dashboard+core+scripts+api → **CLEAN**; no `href="#"`.
- 27 params: gen=7 flow=7 impact=7 feas=6; each `*_WEIGHTS` sums to 1.000.
- `optimize_weights` runs: synthetic set where known-good = highest-impact sites → optimizer returns impact weight 0.80 (largest), weights sum 1.0.
- `git check-ignore` resolves config.js/.env/data//__pycache__//.DS_Store/uvicorn; `.gitignore` + `.env.example` + `LICENSE` all exist; `.DS_Store`/`uvicorn` no longer tracked.
- `node --check` clean (landing + explorer); `py_compile core/*.py api/main.py scripts/*.py` clean; div balance unchanged (index 57/57, explore 80/80).
- `/map` now present among `@app.get` routes.

> M2 (MinMax failure-mode doc) is intentionally deferred to Phase 5 — it must document the *post-C2* behavior (constant columns dropped + weights renormalized), which doesn't exist until Phase 3.
> docs/index.html div imbalance (102/100) is fixed in Phase 5 (docs reconciliation).

## Phase 2 — Environmental justice re-source + endpoint health (C4)

| ID | Fix | Status |
|----|-----|--------|
| C4 | EJSCREEN broker is dead (HTTP down). Added `get_ej_index(catchment_polygon)` in `core/impact.py` reconstructing EJSCREEN's **two-component core demographic index** from live Census ACS (`C17002` % low-income + `B03002` % people-of-color, percentile-ranked within county, area-weighted over the catchment via cached `_fetch_county_demographics`). Wired into `compute_impact_features` with a per-candidate catchment proxy so EJ **varies** per site. `get_ejscreen_index` retained as a deprecated neutral-0.5 stub. | ✅ |
| C4 (key) | Added `census_api_key()` helper + `CENSUS_API_KEY` to `.env.example`; threaded into both `get_ej_index` and `get_population_density` (Census ACS now rejects keyless requests). | ✅ |
| C4 (health) | Added `scripts/healthcheck.py` pinging NWIS, ECHO, TRI, FRS/CERCLIS, StreamStats, Census ACS (required), TIGERweb, and the dead EJSCREEN broker (expected DOWN) — wired into `start.sh` step 3. Surfaces dead endpoints instead of silently swallowing them. | ✅ |
| C4 (docs) | README §5.9 rewritten (ACS reconstruction + removal sources + formula); mermaid, taxonomy param 17, §11 API table, Overview.md, documentation.md, docs/index.html all updated from "EPA EJSCREEN data" → "Census ACS reconstruction (EPA decommissioned EJSCREEN 2025)"; keyless-data constraint softened. | ✅ |

**Verification / proof:**
- Synthetic 4-block-group county: catchment over the low-EJ cell → `EJ=0.100`; catchment over high-EJ cells → `EJ=0.771` → **varies across catchments**, both ∈ [0,1].
- Demographic recipe sanity: `C17002` → %low-income 0.40; `B03002` → %POC 0.60 (matches hand calc).
- `get_ejscreen_index(36,-78.9)` → 0.5 (broker dead).
- Live `scripts/healthcheck.py`: EJSCREEN broker **DOWN (ConnectionError)** as expected; Census ACS flagged DOWN with "no CENSUS_API_KEY" hint; NWIS/TRI/FRS/TIGERweb UP; ECHO + StreamStats currently 404 (surfaced, not swallowed — both fall back via `safe_call`).
- `grep` for live "EPA EJSCREEN data" claims across README+docs → **CLEAN**.
