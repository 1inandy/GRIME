# Detailed Changes — `audit-fixes-live` vs `main`

Every change on the branch, file by file. Tags in brackets map to the audit findings
(`AUDIT.md` / `FIX_PROMPT.md`) and commit phases. Totals: 34 files, +11,525 / −2,048.

---

## New files

- **`LICENSE`** — MIT license text (README claimed MIT; no file existed). [H4]
- **`model.json`** — single source of truth for the 27 weights, sub-score weights, hard gates, spacing, occlusion η, runoff formula, feasibility curves, EJ source. [SSOT]
- **`scripts/check_model.py`** — drift guard: asserts the Python constants/curves match `model.json`, fails loudly on divergence. [SSOT]
- **`scripts/score_candidates.py`** — reproducible end-to-end scorer with `--live` (real DEM + APIs) and a deterministic offline mode. Closes the gap where no committed code reproduced the shipped scored file. [H2]
- **`scripts/healthcheck.py`** — pings every federal endpoint (NWIS, ECHO, TRI, FRS, StreamStats, Census ACS, TIGERweb, EJSCREEN) and reports up/down so dead sources are surfaced, not swallowed. [C4]
- **`scripts/robustness_report.py`** — runs the real n=500 Dirichlet sensitivity and writes the rank-stability histogram + top-sites table. [Phase 6]
- **`tests/test_grime.py`** — 16 pytest property tests (composite ∈ [0,100], gates monotone, occlusion non-increasing, constant-column drop, etc.). [L9]
- **`dashboard/docs/exports/robustness_hist.png`** — the real robustness figure (28 KB). [Phase 6]
- **`AUDIT.md`, `FIXES.md`, `FIX_PROMPT.md`, `CHANGES.md`, `DETAILED_CHANGES.md`** — audit + fix-log + summary docs.
- **Removed:** the stray 0-byte `uvicorn` file [L2] and 20 committed `__pycache__/*.pyc` files.

## `core/__init__.py`
- `gARB` → `GRIME` docstring. [L1]
- New `census_api_key()` — reads `CENSUS_API_KEY` (ACS now rejects keyless requests). [C4]
- New `osm_drive_graph(bbox)` — fetches the bbox drive network **once** and caches it (UTM nodes + total km); shared by road density and road access. [perf]
- New `ELLERBE_DRAINAGE_KM2 = 21.2` constant for area-scaling gauge discharge. [M4]
- Added a region-scope note on the Durham-specific constants.

## `core/pipeline.py` (the hydrology — heaviest change)
- **`fetch_dem`**: now **reprojects the DEM to UTM** before any hydrology, so pixel size is metres — fixes catchment-area collapse to 0.01 km² and the bogus slope reach. [C1] Added **retry/backoff** around the flaky USGS 3DEP 502s. [live-run]
- **`process_hydrology`**: rewritten for the modern pysheds API (`Grid.from_raster` + `Raster` objects via a temp GeoTIFF); the old `Grid().add_gridded_data(...)` string-dataset API was removed in pysheds ≥0.4. Returns `(grid, fdir, acc, elevation, transform)`. [live-run / 2.6]
- **`extract_streams`**: now passes a **boolean `acc > threshold` mask** as the 2nd arg. The old call passed raw `acc` (every cell read as a channel → a ~2 M-segment, 438 MB degenerate network) with `threshold=` silently ignored. [live-run bug]
- **`generate_candidates`**: carries `stream_order` onto every candidate (was never copied → scorer always saw the default). [H3]
- **`compute_strahler_order` → `compute_stream_order`**: renamed, docstring corrected to "confluence node-degree heuristic, not true Strahler," endpoint snap widened 0.1 m → 5 m (the old tolerance fragmented the graph so most segments fell to order 1). Backwards-compat alias kept. [H3]
- **New `snap_to_channel`**: snaps each candidate pixel to the local max-accumulation cell so catchment area reflects the real channel (was clamping to the 0.01 km² floor). [C1a / live-run]
- **`run_pipeline`**: accepts a precomputed `hydrology` tuple + `return_hydrology` (so `run_live` doesn't fetch/condition the DEM twice); maps UTM candidates straight onto the metric affine (no WGS84 round-trip). [C1 / live-run]
- `gARB` → `GRIME`.

## `core/flow.py`
- New `D8_DIRMAP` (pysheds direction codes → row/col steps). [C1b]
- New **`_follow_downstream`** — walks ~100 m **down the D8 `fdir` grid** (steepest-descent fallback); the old code stepped due south. [C1b / M7]
- New **`velocity_transport_favorability`** — Gaussian peaked at ~0.9 m/s, so velocity is "good in a mid-range" in Flow instead of monotonic-good (which fought the Feasibility gate). [M5]
- **`get_discharge_stats`**: `get_peaks` → `get_discharge_peaks` (renamed in dataretrieval ≥1.0) and made **non-fatal** so live daily-value gauge stats survive a peaks failure; `n_years` → `n_peak_records`. [L6 / live-run]
- **`compute_flow_velocity`**: slope now follows `fdir`; continuity Q is **area-scaled** to the candidate's catchment (site-specific, not one gauge mean for all). [C1b, M4]
- `estimate_runoff_coefficient`: docstring "NLCD k-means" → honest "linear impervious→C." [L4]
- `FLOW_WEIGHTS`: `strahler_order` → `stream_order`. [H3]

## `core/generation.py`
- New cached **`_block_group_density`**: **TIGER layer 10 → 8** (layer 10 is school districts with no COUNTY field — population *and* EJ were broken) and **quoted STATE/COUNTY** string fields (unquoted → ArcGIS "Unable to complete operation"); adds the Census key. [C4 / live-run bug]
- `get_population_density`: uses the cached block-group layer + area-weighted overlay.
- Region caches added for TRI, NPDES, NLCD imperviousness, and litter (each whole-bbox lookup fetched once, not per candidate). [perf]
- `get_road_density`: now uses the shared `osm_drive_graph` (was the single slowest per-candidate call). [perf]
- TRI/NPDES fetchers filter to geocoded records before building geometry. [2.2]
- `gARB` → `GRIME`.

## `core/impact.py`
- **EJSCREEN re-sourced** [C4]: `get_ejscreen_index` is now a deprecated 0.5 stub; new `_fetch_county_demographics` pulls ACS `C17002` (% low-income) + `B03002` (% people-of-color), **percentile-ranks within county**, caches; new `_area_weighted_index` + `get_ej_index` area-weight it over the catchment so EJ varies per site.
- **Estuary/beach decorrelated** [H5]: new `_haversine_km`; `estimate_estuary_distance_km` rewritten as a real haversine (the old `coast_lat = lat` made the latitude term identically 0); new distinct `estimate_beach_distance_km` (was `estuary × 1.1`).
- Region caches: `_bbox_osm_features`, `_SUPERFUND_CACHE`; `get_protected_area_score` + `get_tourism_amenity_density` take `bbox` and reuse one OSM fetch. [perf]
- `water_intake_score` docstring corrected to "omnidirectional proximity, not flow-gated." [L5]

## `core/scoring.py`
- **`compute_subscore`**: drops constant columns and renormalizes the surviving weights (was MinMax-mapping them to 0 and silently deleting their weight); all-constant family → neutral 50; feeds velocity through the M5 favorability curve. [C2, M5]
- New **`summarize_provenance(df)`** — prints "X/27 parameters vary (live) · Y constant (fallback)" each run. [C2 / 2.3]
- New **`optimize_weights`** — real Bayesian weight optimization via `skopt.gp_minimize`. [C6]
- New `_candidate_catchment_polygon` + **`build_all_features` now computes generation per candidate** on each candidate's own catchment (was one catchment-level value broadcast to every row → 30 % of the score did nothing); threads `fdir` and `bbox` through. [C2]
- `gARB` → `GRIME`; "28" → "27" docstrings. [C5]

## `core/feasibility.py`
- New **`road_access_distance_cached`** — nearest road via the cached bbox drive network (replaces an ~18 s/candidate `graph_from_point` with a metre-accurate lookup). [perf]
- **`get_channel_width`**: order→width fallback bounded to `min(40, 3·order^1.1)` (order 5 → 17.6 m); the old `2.5·2.5^order` hit 244 m and self-tripped the width gate, deleting big streams. [M1]
- `strahler_order` → `stream_order`; `compute_feasibility_features` takes `bbox`.

## `api/main.py`
- `gRIME` → `GRIME`.
- **CORS** now env-driven via `GRIME_ALLOWED_ORIGIN` (defaults to `*` for the local demo). [M8]
- New **`/map`** route serving the dashboard HTML (README §11 listed it but it didn't exist). [M6]
- `strahler_order` → `stream_order` in the breakdown + weights endpoints. [H3]

## `dashboard/explore/index.html` (explorer)
- **`esc()`** HTML-escaper added; OSM/place names escaped before `innerHTML` insertion. [X1]
- **Per-candidate RNG** (`hash01(lat,lon)`-seeded `pr()`) replaces the single shared sequential `R` — scores are stable across re-render. [C3]
- **Impact terms made deterministic** (water-intake/protected-area were pure `Math.random()`). [C3]
- New **`computeWayOrder`** — chains a river's OSM ways head→tail so occlusion discounts genuinely-downstream sites river-wide. [M7-JS]
- Cluster/`unclustered` click + hover handlers moved into `initMap` (were re-registered every back-navigation → N-fold fetches). [X3]
- `openCity` awaits into a local + `activeCityIdx` staleness guard (no wrong-city streams). [X2]
- On-panel caveat: "model estimates from OSM geometry … not live measurements"; explorer labeled a separate heuristic. [H6, M3]
- "28" → "27"; place counts corrected. [C5, H1]

## `dashboard/index.html` (landing)
- "28 parameters" → 27; "108,772 cities" → "89,518 places / 239 countries". [C5, H1]
- "Real waterway data" reworded; CV/LoRa/"Live on Ellerbe" reframed as roadmap. [1.5, 1.9]
- Mock composite 87 → realistic range; hero stats sourced; award links wired (were `href="#"`). [1.6, L3]

## `dashboard/docs/` (`Overview.md`, `documentation.md`, `index.html`)
- EJSCREEN → Census ACS; Strahler → stream order; formula tables reconciled to the code; MinMax failure-mode doc corrected; two unclosed `<div>`s fixed; counts corrected. [D2, M2, M3, C5, H1]

## `README.md`
- "28" → 27; place counts; Bayesian status now true; `/map` + `.env.example` documented; EJ source = ACS; stats re-sourced (Borrelle/Geyer/Eriksen/Meijer); "1,500 rivers" → Meijer 2021; references expanded; runtime reconciled; Strahler → stream order; MinMax behavior corrected. [C5, H1, C6, M6, C4, L3, L7, L8, M2, H3]

## `start.sh`
- `gARB`/`gRIME` → `GRIME`; runs `healthcheck.py` in step 3; step 5 curls `/api/stats` (was `/` → HTML broke `json.tool` under `set -e`). [L1, C4, 2.9]

## Other
- `requirements-full.txt`: added `pytest`.
- `scripts/generate_mock.py`: `gARB` → `GRIME`.
- `notebooks/validate_pipeline.ipynb`: `gARB` → `GRIME`; Step 4 now actually scores (was generate-only). [H2]
- **`mock_data/candidates.geojson`**: regenerated from the live pipeline — **147 real sites**, catchment 2.0–115.9 km², `stream_order` (no `strahler_order`), note = "live pipeline." [H2 / live-run]
- `.gitignore` / `.env.example`: union of yours + your co-author's hygiene commit (merged).

---

## Honest coverage note
On the live Durham run, **10 of 27 parameters vary with real data; 17 are constant** — `impervious_pct` (weight 0.20) is a 35 % fallback (NLCD isn't served by the elevation API), and EPA ECHO, StreamStats, EJSCREEN, and Durham 311 are dead. The explorer is still a labeled simulation (not yet wired to `/api/candidates`). The paper still references 28 params / EJSCREEN / composite 87 and has not been reconciled.
