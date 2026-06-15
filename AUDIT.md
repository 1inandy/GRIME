# GRIME — Full Code, Math & Data Audit

**Scope:** Every file in the repo (`core/`, `api/`, `dashboard/` incl. `explore/` + `docs/`, `mock_data/`, `scripts/`, `notebooks/`, `README.md`, deps, repo hygiene). Focus areas: code correctness, mathematical/hydrological soundness, data integrity, claim-vs-code accuracy, security, and competition defensibility (SJWP Stockholm).

**Method:** Static read of all 5,126 LOC + isolated numerical reproduction of the suspect math (run in a sandbox) + web verification of two external factual claims. I did **not** execute the full live pipeline (needs `py3dep`/`pysheds` + network), so the hydrology-CRS findings are from code analysis plus isolated reproduction of the arithmetic, clearly noted where so.

**This is report-only — no code was modified.**

---

## How to read this

Findings are tagged **CRITICAL / HIGH / MEDIUM / LOW** and numbered (C#, H#, M#, L#). Each has: what's wrong, where (`file:line`), why it matters, and a suggested fix. A judge-facing risk note is included where relevant. Positives are at the end — there are many; this is a strong project, which is exactly why the defensibility gaps are worth closing.

### Severity summary

| ID | Severity | One-line |
|----|----------|----------|
| C1 | CRITICAL | Hydrology runs in degrees, not meters → catchment area and channel slope are unit-broken |
| C2 | CRITICAL | Generation sub-score (30% of composite) is identical across all candidates → contributes nothing to ranking |
| C3 | CRITICAL | Dashboard "Impact" sub-score (30%) is mostly `Math.random()`; ~11 of 27 params absent from the demo |
| C4 | CRITICAL | EJSCREEN API was removed by EPA (Feb 5 2025) → the EJ feature is dead against the live endpoint |
| C5 | CRITICAL | "28 parameters" is wrong — code and the taxonomy table have 27 |
| C6 | CRITICAL | "Bayesian weight optimization, implemented in core/scoring.py" does not exist in the code |
| H1 | HIGH | "108,772 cities / 240 countries" — actual `places.json` is 89,518 / 239 |
| H2 | HIGH | No committed code path reproduces the showcased scored `candidates.geojson` |
| H3 | HIGH | "Strahler order" is actually a node-degree heuristic, not Strahler |
| H4 | HIGH | No `.gitignore` exists, yet README claims `config.js`/`.env` are gitignored |
| H5 | HIGH | `estuary_dist` and `beach_dist` are 100% collinear → one signal double-weighted |
| H6 | HIGH | Demo shows synthetic numbers as if they were measured data, no on-screen caveat |
| M1 | MEDIUM | Strahler→width fallback explodes (order 5 → 244 m) and self-trips the 50 m hard gate |
| M2 | MEDIUM | README's stated MinMax failure-mode (0.5) ≠ actual behavior (0.0) — and 0.0 is what causes C2 |
| M3 | MEDIUM | Documented algorithms diverge from the implemented ones (placement, JS↔Python parity) |
| M4 | MEDIUM | Manning "continuity cross-check" isn't independent → doesn't add the claimed robustness |
| M5 | MEDIUM | Raw velocity treated as "higher = better" in Flow, but "higher = worse" in Feasibility |
| M6 | MEDIUM | Documented `/map` endpoint and `.env.example` don't exist → broken docs/setup step |
| M7 | MEDIUM | "Downstream" direction is assumed, not derived from the flow grid (both Python & JS) |
| M8 | MEDIUM | API `CORS allow_origins=["*"]` — fine local, flag before any public deploy |
| L1–L9 | LOW | Naming (`gARB`), stray `uvicorn` file, unsourced hero stats, citation precision, tests, etc. |

---

## CRITICAL

### C1 — Hydrology is computed in geographic coordinates (degrees), so meter-based math is unit-broken

The DEM is fetched in WGS84 (`crs=WGS84`) — `pipeline.py:26` and `notebooks/validate_pipeline.ipynb` Step 1 — and pysheds then runs D8/accumulation on that degree grid. Two concrete downstream breakages (both reproduced numerically):

**C1a — Catchment area is always 0.01 km².** `compute_catchment_area` does `acc_value * (pixel_size_m ** 2) / 1e6` (`pipeline.py:185-189`), but `pixel_size` is `abs(transform[0])` ≈ `0.0000898` **degrees**, not 10 m (`pipeline.py:228`, used at `:243`). Reproduced:

```
acc=   500 cells -> area_km2 = 4.0e-12  -> clamped to 0.01
acc= 50000 cells -> area_km2 = 4.0e-10  -> clamped to 0.01   (correct if 10 m: 5.0 km²)
```

Every candidate gets `catchment_area_km2 = max(…, 0.01) = 0.01`. That's a constant column → see C2 → catchment (Flow weight 0.18) contributes nothing.

**C1b — Channel slope is computed over the whole DEM, not a 100 m reach.** In `compute_flow_velocity` (`flow.py:79-87`), `pixels_downstream = int(reach_len / pixel_size)` = `int(100 / 0.0000898)` ≈ **1,113,200**, so `r_down = min(r + 1.1M, nrows-1)` always clamps to the bottom row. Slope becomes `(elev_here − elev_bottom_of_DEM) / 100`, which is physically meaningless and feeds Manning's velocity.

**Why it matters:** catchment area and slope are headline hydrology. At Stockholm, a judge who asks "what's the catchment area at your top site?" gets `63.5 km²` from the *shipped file* but `0.01 km²` from *running your code* — see H2.

**Fix:** reproject the DEM to UTM (EPSG:32617) *before* pysheds, or pass real pixel sizes in meters. Best practice for D8 is a projected, square-cell grid anyway — degree cells aren't square (N–S vs E–W differ by `cos(lat)` ≈ 0.81 at 36°N), which also distorts flow direction and accumulation.

### C2 — The Generation sub-score (30% of the composite) does not vary across candidates

`build_all_features` computes generation at the **catchment level** and assigns it to every candidate as a whole column: `for k, v in gen_features.items(): df[k] = v` (`scoring.py:185-192`). Worse, `_compute_catchment_generation` (`scoring.py:207-234`) hardcodes 4 of 7 generation params (`population_density=800` via `lambda: 800.0`, `road_density=5.0`, `cso_density=0.5`, `litter=2.0`) and fetches the other 3 as single catchment-wide values.

Then `compute_subscore` MinMax-normalizes each column across the candidate set (`scoring.py:48-65`). A constant column through `sklearn`'s `MinMaxScaler` maps to **0** (reproduced: `[0,0,0,0,0,0]`). So `generation_score = (normed @ w) * 100 = 0` for **every** candidate.

**Net effect:** the 0.30 generation weight is inert — it shifts all composites down by a constant but changes *no rankings*. The "trash generation" question ("Is trash entering here?") — arguably the heart of the model — has zero influence on the Python ranking. The same constant-column problem also zeroes out Flow params that are single-gauge/derived: `usgs_mean_q_cfs`, `seasonal_cv`, often `flood_q10_cfs`, and (via C1a) `catchment_area_km2`. So the real Python ranking is driven by a handful of varying params only.

**Fix:** compute generation per-candidate on each candidate's own upstream catchment (you already delineate flow accumulation — use it to build per-candidate catchments), or at minimum stop collapsing to one value. Until then, the 28-param/4-subscore framing overstates what actually drives the output.

### C3 — The dashboard's scoring (what judges actually click) is largely synthetic, and Impact is mostly random

The live demo scores client-side in `explore/index.html:483-515`. The "parameters" are deterministic functions of position-along-stream (`upstreamFrac`) plus seeded RNG, e.g. `popD=(800+uf*400)*dev+rg()*200`, `vel=.4+uf*.6+rg()*.15`. The sub-scores:

- **Impact (`:500`):** `ips = R()*30*.22 + R()*20*.16 + ej*80*.18 + (1-80/300)*80*.14`. The water-intake and protected-area terms are **pure `Math.random()`**; the estuary term is a **constant** (`80/300` hardcoded); only EJ varies meaningfully. Impact is 30% of the composite — so a third of the score a judge sees is effectively noise.
- **Generation (`:498`):** `gs = popD/20*.18 + imp*.4 + 5*2*.1`. Uses impervious weight **0.4** (docs/Python say 0.20) and a hardcoded constant `5*2*.1 = 1.0`. Only 2 of 7 generation params are present.
- **Absent entirely from the demo:** road density, TRI, NPDES, CSO, litter (gen); flood Q10, seasonal CV, runoff C (flow); beach, tourism, superfund (impact) — **~11 of 27** documented parameters.
- **No normalization:** the JS uses absolute clamped formulas, not the MinMax of `README §5.1`. It also adds `portalBonus`/`wwPenalty` after weighting (`:506-508`).

**Why it matters:** the public site and the demo are the primary judging artifacts. If a judge clicks two adjacent sites and the "Impact 41 vs 36" flips on reload (it will — it's RNG), credibility takes a hit.

**Fix:** either (a) drive the demo from real OSM/derived proxies for at least the varying params and stop using `Math.random()` for named scientific quantities, or (b) clearly relabel demo numbers as illustrative (see H6). At minimum, make Impact deterministic.

### C4 — EJSCREEN (the environmental-justice data source) was removed by EPA and the code's endpoint is dead

`get_ejscreen_index` calls `https://ejscreen.epa.gov/mapper/ejscreenRESTbroker.aspx` (`impact.py:56-82`). EPA **removed EJSCREEN on February 5, 2025** (landing page, tool, data downloads, and the ArcGIS server). So this call now always fails → returns the `0.5` neutral fallback for every candidate → EJ (Impact weight 0.18) becomes a constant → contributes 0 after MinMax (same mechanism as C2).

README/Overview headline this as a differentiator ("explicitly weights environmental justice through EPA EJSCREEN data") — it is no longer operational against the live endpoint.

**Fix:** point at a maintained mirror (e.g. the Public Environmental Data Partners reconstruction at `screening-tools.com/epa-ejscreen`) or CEJST/Census-derived EJ proxies; add a connectivity test for it (Step 5 of the notebook tests NWIS/ECHO/Census but **not** EJSCREEN). Re-verify the other federal endpoints too — EPA's site reorg in 2025 may have moved TRI `efservice`, ECHO, FRS/CERCLIS, and StreamStats paths. Sources: see Appendix.

### C5 — "28 parameters" is off by one; it's 27

Weight dictionaries: Generation 7 (`generation.py:253-261`), Flow 7 (`flow.py:188-196`), Impact 7 (`impact.py:212-220`), Feasibility 6 (`feasibility.py:182-189`) = **27**. The README taxonomy table itself only numbers rows **1–27** (`README.md:545-573`). Yet "28" appears 10× in the README (incl. the footer line 893), plus the landing page (`dashboard/index.html:310,378`), docs HTML (`docs/index.html:165`), `documentation.md`, `Overview.md`, and the explorer stat tile (`explore/index.html:982`).

**Fix:** global replace 28 → 27 everywhere, *or* legitimately add a 28th parameter (you have natural candidates: a real per-candidate trash-generation index, or split velocity's two roles). Whichever you choose, make the count, the table, the code, and the UI agree.

### C6 — The "Bayesian weight optimization" is documented as implemented but isn't in the code

`README §6.4` gives pseudocode and says **"Status: Implemented in `core/scoring.py` but not yet run against real ground-truth data."** `§19` repeats "Bayesian scaffold exists." Reality: no `gp_minimize`, no `optimize_weights`, no `skopt` anywhere in `core/` (grep-confirmed). `requirements-full.txt` installs `scikit-optimize` for nothing.

**Why it matters:** "implemented but not run" is a verifiable claim a judge can check on your public GitHub. Finding it absent reads worse than never claiming it.

**Fix:** either implement the ~15-line `gp_minimize` scaffold so the claim is true, or change the wording to "designed, not yet implemented" and drop `scikit-optimize` from requirements.

---

## HIGH

### H1 — Places database is smaller than claimed

`mock_data/places.json` = **89,518 records across 239 country codes** (counted). Claimed as **108,772 cities / 240 countries** in ~15 places: README (lines 14, 142, 594, 633, 765, 893), landing `index.html` (293, 301, 304, 378, 398, 402), `docs/index.html:168`, `Overview.md` (26, 97), `documentation.md` (20, 1122), and the explorer loading text (`explore/index.html:115`) + animated counters. The explorer's *runtime* stat tiles (`:976-984`) compute the real number from the loaded file, so the page will visibly contradict its own hero copy (89,518 shown after load vs "108,772 cities…" splash).

**Fix:** regenerate `places.json` to actually contain 108,772 (raise `generate_mock.py`'s `population >= 100` cutoff or include more sources), or change every claim to the true counts. Pick one number and make the data, copy, and UI agree.

### H2 — No committed path reproduces the scored `mock_data/candidates.geojson`

The shipped file has realistic, varying values (e.g. `catchment_area_km2: 63.5`, `composite_score`, `robustness_pct`, `rank`). But:

- `run_pipeline` (the CLI, `python -m core.pipeline`) outputs **unscored** candidates and would set `catchment_area_km2 = 0.01` (C1a).
- The notebook's Step 4 is titled "Generate **& Score** Candidates" but only calls `generate_candidates` — **no** `build_all_features`/`compute_composite_score` (`validate_pipeline.ipynb` cell-8). It saves `candidates_raw.geojson`, not the scored file.
- `build_all_features` (`scoring.py:142`) is never invoked by any committed entry point.

So the showcased scored artifact cannot be regenerated from the committed code, and its catchment values contradict what the code produces. For a science competition, reproducibility is a likely judging criterion.

**Fix:** add a `scripts/score_candidates.py` (or a notebook cell) that runs `build_all_features` end-to-end and writes `candidates.geojson`, and make sure the numbers it produces match the file you ship. This will also force C1/C2 to surface.

### H3 — "Strahler order" is a node-degree heuristic, not Strahler order

`compute_strahler_order` (`pipeline.py:145-182`) builds an **undirected** graph and assigns `order = max(degree(start), degree(end))` capped at 5. Real Strahler order requires directed downstream topology and the rule "two order-*i* streams meet → order *i+1*." Degree ≠ Strahler (a long unbranched mainstem stays "order 1" here; a 3-way junction of headwaters becomes "order 3"). The docstring admits "Simplified approach," but the field is named `strahler_order` and presented as Strahler in README param 10, the docs, and the UI.

**Fix:** either compute true Strahler (you have the D8 flow grid; pysheds/`networkx` on the *directed* network gets you there), or rename to `confluence_degree`/`junction_order` and stop citing it as Strahler. Endpoint matching also rounds to 0.1 m (`:162-163`) — if pysheds segments don't share exact endpoints the graph fragments and most segments fall to order 1.

### H4 — No `.gitignore`, but the docs promise secrets are ignored

There is **no `.gitignore`** at the repo root (confirmed). Yet `config.example.js` says "`dashboard/config.js` is gitignored — never commit your real token," and `README §12/§17` say both `config.js` and `.env` are gitignored. The protection described doesn't exist. No token is currently committed (leak-scan clean), but the moment someone creates `config.js`/`.env` with a real `pk.*`/Mapbox token, nothing stops it being committed.

**Fix:** add a `.gitignore` with at least `dashboard/config.js`, `.env`, `data/`, `__pycache__/`, `*.pyc`, `.DS_Store`, `.venv/`. (`.DS_Store` is already present in the tree and should be ignored + removed.)

### H5 — `estuary_dist` and `beach_dist` are the same variable (double-weighted), and the estuary calc has a dead term

`beach_dist_km = estimate_estuary_distance_km(...) * 1.1` (`impact.py:243`) — literally `estuary_dist * 1.1`, so correlation = 1.0 (reproduced). Both get Impact weights (0.14 + 0.12), so one signal is effectively weighted 0.26 while masquerading as two independent parameters (also padding the "27" count). Separately, `estimate_estuary_distance_km` sets `coast_lat = lat`, making `dlat = (lat - coast_lat) = 0` always (`impact.py:185`) — the latitude term is dead code; the result is purely the longitudinal distance to lon −76.5, which mis-models the actual (diagonal) NC coastline.

**Fix:** drop one of the two (or derive beach distance from a real recreational-beach dataset, e.g. the EPA BEACH program you cite for param 19), and replace the estuary heuristic with a real coastline/flow-distance computation, or label it explicitly as a crude proxy.

### H6 — The demo presents synthetic numbers as measured data

The site-detail panel (`explore/index.html:1090`) renders `Pop`, `Imperv`, `Velocity`, `Mean Q`, `Strahler`, `Catchment`, `EJ Index`, `Road`, `Width`, `Slope` as concrete values — but they're generated from `upstreamFrac` + seeded RNG (`:483-515`). This is disclosed in `README §19` and `ADR-3`, but the UI itself gives no hint. A viewer reasonably reads "Mean Q 27.0 cfs" as a measurement.

**Fix:** add a one-line caveat in the panel/footer ("Demo values are model estimates from OSM geometry, not live gauge/Census data — see Docs") or visually mark estimated fields. Cheap, and it converts a credibility risk into a transparency win.

---

## MEDIUM

### M1 — Strahler→width fallback explodes and self-trips the hard gate
`get_channel_width` method 3 returns `2.5 * (2.5 ** order)` (`feasibility.py:80-82`): order 3 → 39 m, order 4 → **98 m**, order 5 → **244 m**. Anything ≥ order 4 exceeds the 50 m width hard gate and gets silently removed — so higher-order (bigger, often higher-trash) streams can be deleted by a *fallback estimate*. The "Leopold 1964" citation is about hydraulic geometry (width ∝ Q^~0.5), not this exponential. Fix: use a calibrated width–order relation (e.g. `a · order^b` with sane bounds) or clamp.

### M2 — README's stated MinMax failure-mode is wrong, and the real one causes C2
`README §16` says "MinMax with identical values → Returns 0.5 for all candidates." The actual `compute_subscore` path uses `sklearn.MinMaxScaler`, which returns **0.0** for a constant column (reproduced). The 0.5 behavior only exists in the *unused* helper `normalize_series` (`__init__.py:51-57`). The 0.0 is precisely why constant columns vanish in C2. Fix: document the real behavior and decide whether constant columns should be neutral (0.5) or null (drop them from the weight renormalization).

### M3 — Documented algorithms don't match the implemented ones
(a) `README §6.2` placement ends `RETURN scored[0:cutoff]` (simple top-N%), but the real JS does **greedy upstream-occlusion** discounting (`explore:517-562`) — a *better* algorithm that the docs don't describe. (b) `ADR-3` says the JS "parallels the Python implementation"; in fact the JS uses absolute clamped formulas with **no MinMax**, different weights (C3), and extra bonus/penalty terms — it's a different model, not a parallel one. Fix: document the occlusion algorithm (it's a selling point) and describe the JS model honestly as a separate heuristic.

### M4 — Manning "continuity cross-check" isn't an independent check
`README §5.2` presents `V_final = sqrt(V_Manning · V_continuity)` as hedging DEM/geometry error. But `V_continuity = Q / A_cross` uses the **basin-wide gauge mean Q** for every candidate (`flow.py:99-100`) and the *same* rectangular `A_cross` Manning uses — so it's not independent of either input, and it injects one gauge's mean flow into every site. Combined with the buggy slope (C1b), the geometric mean blends two weak estimates. Fix: use site-specific discharge (scale by catchment area once C1a is fixed) or drop the "cross-check" framing.

### M5 — Velocity is "good when high" in Flow but "bad when high" in Feasibility
`FLOW_WEIGHTS` includes raw `flow_velocity_ms` (0.16); MinMax makes higher velocity → higher Flow score. But physically, high velocity is *bad* (debris doesn't get intercepted; trap tears) — which the Feasibility `velocity_feasibility` correctly penalizes. So the same variable pushes the composite in opposite directions through two sub-scores. Fix: in Flow, transform velocity through a "transport-favorability" curve (peaked, not monotonic) instead of feeding it raw.

### M6 — Documented `/map` endpoint and `.env.example` don't exist
`README §11` lists `GET /map` → "Serves dashboard HTML," but `api/main.py` has no `/map` route (actual: `/`, `/dashboard`, `/explore`, `/docs`). `README §12/§13` tell users to `cp .env.example .env`, but there's no `.env.example` in the repo → the setup step fails. Fix: add the `/map` route or remove it from the table; add a real `.env.example` (and `.env` to `.gitignore`).

### M7 — "Downstream" is assumed, not derived
Occlusion and slope both assume a flow direction they don't verify. Python's slope uses `r + pixels_downstream` (= south in a north-up raster, `flow.py:85`); JS occlusion treats higher `coordIdx` as downstream (`explore:548`). OSM ways are *conventionally* digitized downstream, and you already compute a real D8 flow grid — use it. Otherwise ~half of streams get occlusion applied the wrong way.

### M8 — API CORS is wide open
`allow_origins=["*"]` with `allow_methods/headers=["*"]` (`api/main.py:30-35`). Fine for a local demo (and you note "local development only"), but lock it to the deploy origin before `grime.world` serves the API publicly.

---

## LOW / polish

- **L1 — Stale codename.** `gARB` appears in 8 files (all of `core/`, `scripts/generate_mock.py`, `start.sh`) while the product is GRIME (and the API title is "gRIME"). Unify to GRIME.
- **L2 — Stray file.** A 0-byte file named `uvicorn` sits at the repo root (likely an accidental `> uvicorn` redirect). Delete it.
- **L3 — Unsourced hero stats + dead links.** Landing `index.html:221-229` shows `11M+`, `91%`, `5T` with no source; the awards row (`:422`) links to `href="#"` (dead) for the SJWP and SMathHacks wins. Add citations and real links, or soften.
- **L4 — Fabricated-sounding provenance.** Runoff C is described as "linear approximation from NLCD k-means" (`flow.py:179`, README param 14) — it's just `0.05 + 0.009·I`; k-means is unrelated. WaterGate author order is inconsistent ("Cheng, Anand, Rose" in prose vs "N. Anand, G. Cheng, T. Rose" in the footnote). Tidy these — judges read citations closely.
- **L5 — `water_intake_score` ignores "downstream".** Docstring says downstream, but it sums `exp(-d/10)` over *all* intakes within 50 km regardless of flow direction (`impact.py:42-51`). Same root cause as M7.
- **L6 — `n_years` mislabel.** `n_years = len(peak_values)` is the count of annual-peak records, not guaranteed distinct years (`flow.py:49`).
- **L7 — "six federal APIs" undercounts.** You also use StreamStats, FRS/CERCLIS, TIGER, and Overpass. Harmless, but say "six core APIs" or list all.
- **L8 — Rivers/plastic stat.** "Over 1,500 rivers = 80%" is *defensible* — Meijer et al. 2021 lists 1,656 rivers — but the headline figure most readers know is ~1,000. Cite Meijer 2021 explicitly (and the 1,656 number) to preempt a "I thought it was 1,000" challenge.
- **L9 — No automated tests.** `README §18` admits this. Add a tiny `pytest`: composite ∈ [0,100]; Manning monotonic in slope; hard gates remove the right rows; `compute_subscore` handles a constant column the way you intend. Cheap insurance and good to show judges.

---

## What's genuinely good (keep / lead with these)

- **Clean, modular architecture.** Four parameter families in separate modules, a shared `safe_call` with sane fallbacks, clear separation of pipeline / scoring / API / dashboard.
- **Two-level interpretable scoring** is a real design strength — sub-scores map to plain-language questions and are tunable. (Just make the implementation live up to it — C2/C3.)
- **Greedy upstream-occlusion selection** (`explore:517-562`, `η=0.65` compounding) is a thoughtful, defensible improvement over naive top-N% and is more sophisticated than what the docs claim. Document it.
- **Dirichlet sensitivity analysis** (`scoring.py:110-137`) is a nice robustness story (even though it perturbs only the 4 sub-weights, not the 27 inner weights — consider extending).
- **Solid dashboard engineering:** progressive pan-autoload with bbox coverage sampling, dedup by OSM id, rate-limiting, river-focus tracing, graceful "no waterway data" state.
- **Good failure posture:** every external call wrapped with fallbacks; the app runs offline / token-less paths degrade gracefully.
- **No secrets leaked** (token via env/`config.js` pattern; scan clean). Correctly implemented Cauchy (`1/(1+(d/h)²)`) and exponential (`exp(-d/10)`) kernels.

---

## Suggested fix order (highest leverage first)

1. **C5, H1, C6, M6** — pure copy/count fixes (27 params, real city counts, remove/implement Bayesian claim, fix `/map` + `.env.example`). Low effort, removes the most "checkable" inaccuracies.
2. **C4, H4** — repoint/disable EJSCREEN; add `.gitignore`. Low effort, real correctness/security.
3. **C1, C2, H2** — fix the CRS/units, make generation per-candidate, add a script that regenerates the scored file. This is the core scientific fix and makes the model actually do what the docs say.
4. **C3, H6** — make the demo deterministic (kill `Math.random()` in Impact) and caveat estimated values.
5. **H3, H5, M1, M4, M5, M7** — the modeling refinements (real Strahler, de-duplicate estuary/beach, width fallback, velocity treatment, downstream direction).
6. **L1–L9** — polish.

---

## Appendix — verification notes & sources

**Reproduced numerically in a sandbox (not just read):** C1a (catchment → 0.01), C1b (slope reach ≈ 1.1M px), C2 (constant column → 0 via `MinMaxScaler`), H5 (estuary/beach corr = 1.0; dlat = 0), M1 (width 2.5·2.5^order table). Counts (`places.json` 89,518/239; 27 params; no `skopt`/`/map`/`.env.example`/`.gitignore`) verified by direct inspection.

**Not executed:** the full live pipeline (`py3dep`/`pysheds` + federal APIs). CRS findings are from code + isolated arithmetic; running the real pipeline would confirm them end-to-end (recommended as part of H2).

**External claims (web-verified, June 2026):**
- EJSCREEN removed by EPA on 2025-02-05; reconstructions exist off-EPA. [EELP tracker](https://eelp.law.harvard.edu/tracker/epa-added-environmental-health-indicators-to-ejscreen/) · [EDGI](https://envirodatagov.org/epa-removes-ejscreen-from-its-website/) · [screening-tools.com mirror](https://screening-tools.com/epa-ejscreen)
- "~80% of riverine ocean plastic" comes from >1,000 rivers (Meijer et al. 2021), with 1,656 rivers cited for the 80% cumulative figure — so "over 1,500" is defensible with citation. [Meijer et al. 2021 (PubMed)](https://pubmed.ncbi.nlm.nih.gov/33931460/) · [TU Delft](https://research.tudelft.nl/en/publications/more-than-1000-rivers-account-for-80-of-global-riverine-plastic-e/) · [Our World in Data](https://ourworldindata.org/grapher/plastics-top-rivers)
