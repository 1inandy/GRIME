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

## Phase 3 — Core scientific correctness (Python)

| ID | Fix | Status |
|----|-----|--------|
| C1 | `fetch_dem` now reprojects the DEM to UTM (`dem.rio.reproject(UTM_CRS)`) before any hydrology, so `pixel_size` is metres; `run_pipeline` maps UTM candidates straight onto the metric affine. Fixes C1a (catchment area real km²) + C1b (slope reach in metres). | ✅ |
| C1b/M7 | Added `_follow_downstream()` walking ~100 m **along the D8 `fdir` grid** (steepest-descent fallback); `compute_flow_velocity` uses it for slope; `fdir` threaded `build_all_features → compute_flow_features → compute_flow_velocity`. | ✅ |
| C2 | `build_all_features` computes generation **per candidate** on each candidate's own catchment (proxy disc); `compute_subscore` **drops constant columns + renormalizes** surviving weights (logged), all-constant family → neutral 50; added `summarize_provenance(df)` printing "X/27 vary · Y constant" each run. | ✅ |
| H2 | Added `scripts/score_candidates.py` (real `--live` pipeline + deterministic offline mode whose scoring is the real code); regenerated + committed `mock_data/candidates.geojson` (byte-reproducible). Notebook Step 4 now actually scores (was generate-only). | ✅ |
| H3 | "Strahler" renamed to **`stream_order`** everywhere (flow/pipeline/feasibility/api + README/docs) and documented as a confluence-degree heuristic, not true Strahler; endpoint snap tolerance widened 0.1 m → 5 m so the graph stops fragmenting to order 1. | ✅ |
| H5 | `estimate_estuary_distance_km` rewritten as a real haversine to a fixed estuary ref (Pamlico Sound); new distinct `estimate_beach_distance_km` (Wrightsville Beach) replaces `estuary × 1.1`. corr now ~0.35 (was 1.0); estuary varies with latitude. | ✅ |
| M1 | Width fallback `2.5·2.5^order` (order5→244 m) → bounded `min(40, 3·order^1.1)` (order5→17.6 m); never trips the width gate. Noted Leopold 1964 is hydraulic geometry, not this. | ✅ |
| M4 | Continuity estimate now **area-scales** gauge Q to the candidate's catchment (`ELLERBE_DRAINAGE_KM2` ref) so V_continuity is site-specific; docs reworded from "independent cross-check" → "soft blend" (shares the cross-section). | ✅ |
| M5 | Added `velocity_transport_favorability()` (Gaussian peaked at ~0.9 m/s); `compute_subscore` feeds velocity through it for Flow while the raw value stays for the Feasibility gate + display. | ✅ |
| L4 | Runoff docstring + README param 14 fixed ("linear impervious→C", not "NLCD k-means"); WaterGate author order unified to "Anand, Cheng, Rose". | ✅ |
| L5 | `water_intake_score` docstring corrected to "omnidirectional proximity, not flow-gated". | ✅ |
| L6 | `n_years` → `n_peak_records` (count of annual-peak records, not distinct years). | ✅ |

**Verification / proof (synthetic-DEM scratch tests; pysheds/network unavailable here):**
- C1a: `compute_catchment_area(acc=50000, pixel=10m)` → **5.0 km²** (was clamped 0.01).
- C1b/M7: D8 walk from (2,2) with fdir=East ends at col 12 (east); velocity finite/positive on an east-dropping DEM.
- M5: favorability(0,0.9,2.5)=(0.105, 1.0, 0.001) — peaked at 0.9.
- C2: 6-constant+1-varying family → sub-score spans [0,100]; all-constant → 50.0; provenance prints split.
- H5: corr(estuary,beach) = **−0.979** over a candidate grid (<0.99); estuary changes with latitude.
- M1: width by order 1..5 = [3.0, 6.4, 10.0, 13.8, 17.6] — all < 50 m gate.
- H2: `score_candidates.py` run twice → **byte-identical**; regenerated `candidates.geojson` = 26 sites, catchment 3.69–50.69 km², composite 27.26–68.26 (all ∈[0,100]), generation_score 13.9–82.7 (varies — C2), estuary/beach corr 0.35, `stream_order` (no `strahler_order`); API loads it (stats + 27-param detail trees populate). Offline provenance: 27/27 vary.

## Phase 4 — Dashboard honesty & determinism (JS)

| ID | Fix | Status |
|----|-----|--------|
| C3 | Replaced the single sequential `R=seededRand(seed+7777)` with a **per-candidate RNG seeded by the site's own coordinates** (`hash01(lat,lon)`), so a site's values are identical across re-renders and loading more streams only adds sites. Killed the pure-`Math.random` Impact terms (water-intake/protected-area now deterministic from geometry + per-candidate `pr()`). Fixed the Generation impervious weight 0.40 → documented **0.20** (+ added a road-density term at 0.10). | ✅ |
| M7-JS | Added `computeWayOrder(streams)` chaining each river's OSM ways head→tail by endpoint matching (≤60 m); occlusion now discounts genuinely-downstream sites by `(wayOrder, coordIdx)` river-wide, and portals neither occlude nor get occluded. | ✅ |
| H6 | Added an on-panel caveat: "Demo values are model estimates from OSM geometry … not live gauge/Census data," linking to Docs; landing dash-sub already reworded to "Real OSM waterway geometry · Upstream-occlusion scoring"; hero SVG mock composites lowered 87/74/71 → 66/61/57 to the explorer's real range. | ✅ |
| M3 | README §6.2 rewritten: greedy + multiplicative upstream occlusion `(1−η)^k`, η=0.65 (the real algorithm, a selling point), real spacing (500 m same / 300 m cross), and an explicit "separate honestly-labeled heuristic, not the Python model" note; ADR-3 reworded from "parallels the Python implementation" to "a separate simplified heuristic." | ✅ |
| H3 (UI) | Detail-panel chip "Strahler" → "Stream order"; candidate property `strahler` → `stream_order`. | ✅ |

**Verification / proof:**
- `node --check` clean (landing + explorer); div balance even (index 57/57, explore 81/81).
- Determinism test (functions extracted from the file): same `(lat,lon)` → **identical** `pr()` draws regardless of set size / interleaving.
- Occlusion direction test: 2-way river with the **downstream way created first** → `computeWayOrder` ranks upstream way 0, downstream 1; all true-downstream sites occluded, **no** upstream site wrongly occluded.
- `grep` confirms no `Math.random()` anywhere in the explorer and no orphan `R()`/`c.strahler`.

## Phase 5 — Reproducibility, SSOT, tests, deploy hygiene

| ID | Fix | Status |
|----|-----|--------|
| SSOT | Created `model.json` (27 weights, sub-score weights, hard gates, spacing, occlusion η, runoff formula, feasibility curves, EJ source) + `scripts/check_model.py` asserting the Python constants/curves match it (fails loudly on drift). Makes M2/M3 contradictions structurally hard to reintroduce. | ✅ |
| L9 | Added `tests/test_grime.py` — 16 pytest property tests over the real scoring code (see list). | ✅ |
| M8 | CORS: default `["*"]` for the local public-data demo, but now reads `GRIME_ALLOWED_ORIGIN` (comma-separated) to lock to the deploy origin(s); commented + in `.env.example`. | ✅ |
| M2 | README §16 failure-mode table corrected: constant column → sklearn maps to **0** (not 0.5) → `compute_subscore` drops + renormalizes (all-constant → 50); also updated the Overpass "procedural fallback" row (real behavior = "no waterway data" state), added Census-key + dead-EJSCREEN rows. README §18 testing section rewritten to reflect the real suite. | ✅ |
| Docs | Fixed the docs/index.html div imbalance (closed 2 unclosed `<div>`s after the Manning + debris figures → 102/102); runtime claims already reconciled in §15 (dashboard ~3 s · pipeline 30–90 s + 3–5 min). | ✅ |

**Verification / proof:**
- `python3 scripts/check_model.py` → "model.json matches code: 27 parameters, weights + gates + curves consistent."
- `pytest tests/` → **19 passed**.
- `py_compile core/*.py api/main.py scripts/*.py` clean; div balance even (index 57/57, explore 81/81, **docs 102/102**).

## Phase 6 — Strategic additions (ranked, then high-payoff/low-risk only)

Ranked addition × effort × judge payoff × risk (full table in the summary). Implemented
the integrity/reproducibility ones; deferred the high-risk/invisible ones.

| # | Addition | Status |
|---|----------|--------|
| 2 | Property tests (pytest) | ✅ done in Phase 5 (16 tests) |
| 3 | **Real robustness figure** — `scripts/robustness_report.py` runs the real Dirichlet sensitivity at **n=500** and writes an actual rank-stability histogram (`dashboard/docs/exports/robustness_hist.png`) + a top-sites table; README §6.3 cites the real numbers (top 4 sites 81–99% top-5 retention). Replaces any synthetic robustness mock. | ✅ implemented |
| 5a | **Globe perf** — hero Three.js `animate()` and Mapbox `spinDash()` now skip their frame/repaint when off-screen (IntersectionObserver) or the tab is hidden. | ✅ implemented |
| 4 | **Validation candidates** — robustness report prints the top real Ellerbe sites as field-validation targets; README §6.3 adds an honest validation-roadmap note. (Photographed ground-truth slide can't be fabricated → noted as next step.) | ✅ partial (honest) |
| 1 | Wire US cities to the real `/api/candidates` pipeline | 🔜 deferred — needs pysheds + network + Census key, unverifiable offline, and risks the "do not break" explorer; the path already exists (`score_candidates.py --live` + `/api/candidates`). |
| 5b | Parallelize per-candidate EPA calls | 🔜 deferred — threading correctness risk > invisible payoff. |
| 5c | Cache PAD-US/StreamStats per catchment | 🔜 deferred — low judge payoff (EJ county cache already added in Phase 2). |

**Verification / proof:**
- `scripts/robustness_report.py` (n=500, seeded): top site South Ellerbe = 99.2% robust; 4 sites ≥80%; histogram PNG (28 KB) written. Reproducible.
- `candidates.geojson` (now n=500) byte-reproducible across runs.
- `node --check` landing clean after the IntersectionObserver gating; div balance 57/57.

---

## Final verification checklist (all green)

- [x] `grep "28 param|108,772|240 countries|gARB|1,500 rivers|ℝ²⁸"` → **0 hits**
- [x] weight dicts 7/7/7/6 = **27**; `scripts/check_model.py` → model.json matches code
- [x] `python3 -m py_compile core/*.py api/main.py scripts/*.py` → clean
- [x] explorer + landing inline JS `node --check` clean; **div balance equal** (57/57, 81/81, 102/102)
- [x] `scripts/score_candidates.py` reproduces `candidates.geojson` byte-for-byte; catchment **3.69–50.69 km²** (real, not 0.01)
- [x] provenance: EJ index + generation params **vary** (26 distinct values each)
- [x] explorer rankings stable across re-render (same `(lat,lon)` → identical draws); **0 live `Math.random`** in scoring
- [x] README endpoint table ⊆ `@app` routes; `.env.example` + `.gitignore` + `LICENSE` exist
- [x] `pytest tests/` → **19 passed**
- [x] FIXES.md has a row for **all 32** findings (X1–X3, C1–C6, H1–H6, M1–M8, L1–L9)

## Consolidated summary

- **Phase 0** (XSS/JS races): `esc()` + escaped all third-party strings; `openCity` stale-fetch guard; cluster listeners attached once.
- **Phase 1** (claims/counts): 28→27, 108,772→89,518 places/239, real `optimize_weights`, `/map` route, `.gitignore`/`.env.example`/`LICENSE`, gARB→GRIME, sourced stats + award links, Meijer 2021.
- **Phase 2** (EJ/C4): `get_ej_index` reconstructs EJSCREEN's demographic index from live Census ACS (varies 0.10↔0.77 across catchments); `healthcheck.py` surfaces the dead EJSCREEN broker; docs de-claim live EPA EJSCREEN.
- **Phase 3** (Python core): UTM reproject (catchment 50000·100/1e6 = **5.0 km²** vs 0.01); D8 downstream slope walk; per-candidate generation + constant-column drop/renormalize + provenance; reproducible `score_candidates.py`; stream_order rename; decorrelated estuary/beach (corr −0.98); bounded width; area-scaled continuity; peaked velocity curve.
- **Phase 4** (explorer): per-candidate coord-seeded RNG (stable across re-render); killed `Math.random` Impact noise; impervious weight 0.40→0.20; `computeWayOrder` river-wide occlusion (verified: upstream ranked first, all true-downstream occluded); on-panel "model estimates" caveat; README §6.2 + ADR-3 honesty.
- **Phase 5** (SSOT/tests): `model.json` + `check_model.py` drift guard; pytest regression suite; env-driven CORS; corrected MinMax failure-mode doc; closed 2 docs `<div>`s.
- **Phase 6** (additions): real n=500 Dirichlet robustness histogram + table (top site 99% robust); off-screen globe pausing; field-validation roadmap. Deferred: live US pipeline wiring, EPA parallelization, PAD-US cache (risk/low payoff).

**Environment caveats (honest):** pysheds/py3dep/rioxarray not installed and Census ACS needs a free key in this sandbox, so C1/C2/H2 were verified with synthetic-DEM scratch tests + the deterministic offline `score_candidates` path (its *scoring* is the real shipped code; raw params are labeled synthetic estimates), and the EJ math on synthetic block groups. The `--live` path + `CENSUS_API_KEY` produce field values on a full environment. ECHO + StreamStats currently return 404 (surfaced by `healthcheck.py`, handled by `safe_call`).
