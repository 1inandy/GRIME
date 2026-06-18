# GRIME — Audit Fix Implementation Prompt (v2)

**You are Claude Code working in the GRIME repo** (FastAPI + Python scoring pipeline in `core/` and `api/`; static landing/explorer/docs in `dashboard/`; mock data in `mock_data/`; `scripts/`, `notebooks/`). A full code/math/data audit is reproduced at the bottom of this file (the `C#/H#/M#/L#` findings). Your job is to drive **every** finding to resolution before the SJWP international final in Stockholm, where technical judges will read the README and click around grime.world.

Work through the phases **in order, in one pass** — do not stop for review between phases. Commit per phase with the finding IDs in the message. Update `FIXES.md` as you go.

---

## Decisions already made (do NOT re-litigate these)

1. **Parameter count is 27, not 28.** The canonical taxonomy is in the "Canonical 27 parameters" section below. Make the code, the docs, and every UI surface agree on 27 and on this exact list/weights.
2. **Environmental justice is re-sourced from Census ACS.** EPA EJSCREEN was decommissioned 2025‑02‑05 and CEJST was pulled 2025‑01‑22; neither has an official live endpoint. Rebuild the EJ index from Census ACS demographics (full spec in Phase 2). Do **not** keep calling the dead EJSCREEN broker as the primary source.
3. **The code is ground truth for the docs.** Where docs and code disagree, fix the docs to match the code (then improve the code). The explorer is a **separate, honestly-labeled heuristic**, not a "parallel" of the Python model — document it as such.
4. **The explorer stays a simulation, but an honest and stable one.** Don't fake "real data"; do make its numbers deterministic and clearly labeled (Phase 4).
5. **The audit was performed on this exact commit (`d2a0a33`, the current `main` HEAD after the revert).** Every `file:line` reference in the audit is **accurate to the code in front of you**, and **every finding is live** — nothing is pre-fixed (no `.gitignore`, no `LICENSE`, no `esc()`, "28 parameters" still everywhere). Use the line numbers directly; you are not chasing drift.
6. **There is a `backup-phases` branch (on origin too) with a prior fix attempt.** It fixed the claims/counts/hygiene findings well — you may reference *those* diffs to move fast. But its explorer-scoring changes **regressed** site selection and left the rankings **non-deterministic** (they re-randomized on every pan/zoom). Do **not** copy its explorer scoring approach; use the determinism + way-chaining-occlusion approach specified in Phase 4 instead. `git show backup-phases:FIXES.md` and `git diff d2a0a33 backup-phases -- <file>` are your references.

---

## Ground rules

- **Verify, then fix.** The audit was run on the current commit (`d2a0a33`), so the `file:line` references are accurate and every finding is present — you are not chasing drift or re-checking whether something is already done. Still reproduce the numeric claims (div counts, 27-param count, 89,518 places, constant-column → 0) with a scratch script and paste the result into `FIXES.md` as proof, so each fix is grounded rather than assumed.
- **Integrity over cosmetics.** For false/overclaimed statements, make them *true* — don't just soften. Preferred order: wire up the real thing → label it honestly as simulated/illustrative/roadmap → only then delete.
- **Don't break what works.** The "What's good — do not break" list at the end is off-limits unless a fix requires touching it. Keep the app fully functional offline and signed-out.
- **Small, reviewable changes.** Don't refactor or rename outside the scope of a finding. Explain your approach for anything non-trivial before writing it.
- **No co-authorship trailer.** Do **not** add Claude as a co-author or any `Co-Authored-By` line to commits.
- **Track everything in `FIXES.md`** at repo root: one row per finding — ID, one-sentence fix, status (done / skipped+why / deferred), and the proof snippet.
- **Each phase ends with a verification step** (run the checks listed under that phase) before committing.

## Run / verify commands

```bash
# Python + API
pip install -r requirements.txt
python3 -m py_compile core/*.py api/main.py scripts/*.py
python3 scripts/generate_mock.py
python3 -m uvicorn api.main:app --port 8000   # then: curl http://localhost:8000/api/stats

# Explorer / landing JS: extract inline <script> and syntax-check
#   (no build step — it's a single HTML file with one inline script)
node --check <(python3 - <<'PY'
import re;print("\n;\n".join(re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>',open('dashboard/explore/index.html').read(),re.S)))
PY
)

# HTML div balance (must be equal per file)
for f in dashboard/index.html dashboard/explore/index.html dashboard/docs/index.html; do
  echo "$f $(grep -o '<div' $f|wc -l) $(grep -o '</div>' $f|wc -l)"; done
```

---

## Canonical 27 parameters (the single source of truth)

Sub-score weights: **Generation 0.30 · Flow 0.25 · Impact 0.30 · Feasibility 0.15.**
Make `core/*.py`, `api/main.py /api/weights`, `model.json` (create if missing), the README taxonomy table, `dashboard/docs/*`, and the explorer all match this exactly.

**Generation (0.30) — "is trash entering here?"**
1. population_density `0.18` · 2. impervious_pct `0.20` · 3. road_density_km_km2 `0.10` · 4. tri_facility_density `0.18` · 5. npdes_points `0.12` · 6. cso_density `0.12` · 7. litter_complaint_density `0.10`

**Flow (0.25) — "will water carry it past this point?"**
8. usgs_mean_q_cfs `0.22` · 9. flow_velocity_ms `0.16` · 10. stream_order `0.14` *(rename from `strahler_order` unless you implement true Strahler — see H3)* · 11. catchment_area_km2 `0.18` · 12. flood_q10_cfs `0.14` · 13. seasonal_cv `0.10` · 14. runoff_coeff_C `0.06`

**Impact (0.30) — "who/what is harmed downstream?"**
15. water_intake_score `0.22` · 16. protected_area_score `0.16` · 17. ej_index `0.18` *(now from Census ACS — C4)* · 18. estuary_dist_km `0.14` · 19. beach_dist_km `0.12` *(must be genuinely distinct from #18 — H5)* · 20. tourism_amenity_density `0.10` · 21. superfund_score `0.08`

**Feasibility (0.15) — "can we actually deploy here?"**
22. road_access_score `0.25` · 23. channel_width_score `0.20` · 24. velocity_feasibility `0.20` · 25. land_ownership `0.15` · 26. bank_slope_score `0.10` · 27. bridge_proximity_bonus `0.10`

> If you ever want a true 28th parameter, the clean candidate is a **per-candidate trash-generation index** (helps C2) or splitting velocity's two roles (M5). Until then it is **27** everywhere.

---

# Phase 0 — Carried over from the prior audit (real bugs THIS audit missed)

These three are **not** in the audit appendix below, but the earlier audit caught them and they are real. Fix them first — the XSS one is a genuine security hole and a tiny fix.

- **X1 — XSS via unescaped OSM/place names.** Third-party strings (OSM `tags.name` → stream/river names, `places.json` names/countries) are interpolated into `innerHTML` unescaped in the explorer sidebar, river-focus panel, and detail panel. An OSM way named `<img src=x onerror=alert(1)>` executes in every visitor's browser. Add a tiny `esc()` helper (`String(s).replace(/[&<>"']/g, …)`) and wrap **every** third-party string before `innerHTML` insertion. (Sites already using `textContent` are inert — leave them.) Verify: `esc('<img src=x onerror=alert(1)>')` → `&lt;img …&gt;`; grep shows no unescaped `${…name|stream|country…}` inside `innerHTML` assignments.
- **X2 — Stale-fetch race in `openCity()`.** `activeStreams = await fetchRealStreams(...)` assigns the **global** before the staleness guard runs, so a slow fetch for city A can overwrite city B after B already rendered. Fix: `await` into a **local**, check `activeCityIdx !== idx`, then assign the global.
- **X3 — Duplicate map listeners accumulate.** `loadWorldView()` re-registers `map.on('click','clusters',…)` / `'unclustered'` + hover handlers on **every** call (every "← All cities", every search-in-city, every theme reload). Mapbox adds a new listener each time → after N round-trips, one click fires `openCity()` N times → N parallel Overpass fetches. Fix: attach these handlers **once** in `initMap`, not in `loadWorldView`.

**Phase 0 verify:** `node --check` clean; the `esc()` unit test passes; cluster/unclustered click handlers appear only in `initMap`.

---

# Phase 1 — Claims, counts & hygiene (highest "checkable" leverage, low effort)

A judge can falsify each of these in seconds. Fix them everywhere they appear.

- **C5 — "28 parameters" → 27.** Global-replace the count in `README.md` (incl. footer), `dashboard/index.html`, `dashboard/explore/index.html` (stat tile + splash), `dashboard/docs/index.html`, `dashboard/docs/documentation.md`, `dashboard/docs/Overview.md`, and `core/scoring.py` docstrings. Verify: `grep -rn "28 param\|28 geospatial\|ℝ²⁸\|All 28"` → no hits; weight dicts sum to 7/7/7/6 = 27.
- **H1 — "108,772 cities / 240 countries" → real counts.** Count `mock_data/places.json` (`python3 -c "import json;d=json.load(open('mock_data/places.json'));print(len(d), len({p['c'] for p in d}))"`). Use the true numbers and the word **"places"** (not "cities," since most are procedurally generated — disclose that in the README data section). Replace in all ~15 locations. Verify: `grep -rn "108,772\|108772\|240 countries"` → none.
- **C6 — "Bayesian weight optimization: implemented."** Either (a) implement the ~15-line `optimize_weights()` scaffold in `core/scoring.py` using `skopt.gp_minimize` (lazy import; objective = push known-good site indices toward the top of the rank), so the README §6.4 / §19 claim becomes true; **or** (b) change the status to "designed, not yet implemented" and drop `scikit-optimize` from `requirements-full.txt`. Prefer (a). Verify: `python3 -c "from core.scoring import optimize_weights"` and the claim wording matches reality.
- **M6 — `/map` route + `.env.example`.** `api/main.py` has no `/map` route (it serves `/`, `/dashboard`, `/explore`, `/docs`). Either add the route or remove the row from the README §11 endpoint table. Create a real `.env.example` (`MAPBOX_TOKEN=`) matching the README setup steps. Verify: README endpoint table matches `@app.get(...)` decorators; `ls .env.example`.
- **H4 — `.gitignore`.** Create one covering `dashboard/config.js`, `.env`, `data/`, `__pycache__/`, `*.pyc`, `.DS_Store`, `.venv/`, and the stray `uvicorn` file. Then `git rm --cached .DS_Store uvicorn` if tracked. Verify: `git check-ignore dashboard/config.js .env` resolves.
- **M2 — README MinMax failure-mode doc.** README says "identical values → 0.5"; the real `sklearn.MinMaxScaler` path returns **0.0** for a constant column. After Phase 3 changes the behavior, document the *real* behavior (constant columns are dropped + weights renormalized — see C2). Verify the doc matches `compute_subscore`.
- **L1 — `gARB` codename → GRIME** across `core/*.py`, `scripts/generate_mock.py`, `start.sh`, and the API title ("gRIME"→"GRIME"). Verify: `grep -rn "gARB\|gRIME" core scripts api start.sh` → none.
- **L2 — stray `uvicorn` file** at repo root: `git rm --cached uvicorn && rm -f uvicorn` (covered by `.gitignore` from H4).
- **L3 — hero stats + award links.** Landing shows `11M+ / 91% / 5T` with no source and awards link to `href="#"`. Source the stats (see L8) and link the real SJWP + SMathHacks pages, or soften. Verify: no `href="#"` in the awards row.
- **L7 — "six federal APIs"** undercounts (you also use StreamStats, FRS/CERCLIS, TIGER, Overpass). Reword to "five core APIs + PAD‑US dataset" (or list all).
- **L8 — rivers/plastic stat.** "Over 1,500 rivers = 80%" is defensible via Meijer et al. 2021 (1,656 rivers) — cite it explicitly and add the source link to preempt the "I thought it was ~1,000" challenge.

**Phase 1 verify:** all greps above clean; div balance unchanged; README endpoint table matches routes; `node --check` clean.

---

# Phase 2 — Environmental justice re-source + endpoint health (C4)

**Context (web-verified, June 2026).** EPA removed EJSCREEN on **2025‑02‑05** (the tool, the data downloads, and the `ejscreenRESTbroker.aspx` ArcGIS server), and the White House removed **CEJST** on **2025‑01‑22** when the Justice40 executive order was rescinded. There is **no official federal replacement**, and the lawsuit to restore EJSCREEN was dismissed (standing) on 2026‑03‑13. So `get_ejscreen_index` (`impact.py:56-82`) hits a dead endpoint and returns `0.5` for every candidate → EJ is a constant → 0 after MinMax. EJ is headlined as a differentiator, so re-sourcing it from a live, maintained source is both a correctness fix and a defensibility win — do it properly.

**Rebuild `ej_index` from Census ACS (primary).** You already have the ACS plumbing in `core/generation.py::get_population_density` (it hits the Census ACS API and joins TIGER block-group geometries) — reuse it. EJSCREEN's demographic index *is* percentile-ranked ACS demographics, so this reconstructs the real methodology on a stable API you control, not a third-party mirror that could vanish.

**Spec for the new `get_ej_index(catchment_polygon, ...)` in `core/impact.py`:**
Compute EJSCREEN's demographic index from ACS 5‑year block-group data, area-weighted over the catchment, then percentile-rank within the county and average. Components:
- **% low-income** — table `C17002` (ratio of income to poverty < 2.0).
- **% people of color** — `1 − (non‑Hispanic white / total)` from `B03002`.
- **% limited-English households** — `C16002` (or `S1602`).
- **% less-than-high-school** — `B15003` (sum of categories below HS diploma / total ≥25).
- **% under 5 and % over 64** — `B01001`.

Return a 0–1 index = mean of the percentile ranks (EJSCREEN's "supplemental demographic index" uses all six; its core index uses the first two — either is defensible, pick one and document it). Because this is computed per block group and area-weighted, it **varies across catchments** (also helps the C2 spirit).

Also:
- **Add a connectivity test** for the EJ source to the notebook's Step 5 (which currently tests NWIS/ECHO/Census but not EJ) and to any `start.sh`/healthcheck path.
- **Re-verify the other federal endpoints** after EPA's 2025 reorg: TRI `efservice`, ECHO, FRS/CERCLIS, StreamStats. For each, on failure, the `safe_call` fallback must be surfaced by the provenance summary (see C2), not silently swallowed.
- **Update the README/Overview** claim from "EPA EJSCREEN data" to "EJSCREEN demographic index reconstructed from live Census ACS (EPA decommissioned EJSCREEN in 2025)." Keep the honesty note.
- **Fallback sources (document, don't depend on as primary):** **CDC/ATSDR Social Vulnerability Index** — a still-maintained *federal* tool, downloadable by census tract, a clean socioeconomic substitute; or the **Public Environmental Data Partners** EJScreen v2.3 reconstruction at `screening-tools.com` / the EDGI CEJST mirror — closest to the original, but third-party, so confirm a programmatic endpoint exists (vs. bulk download) before relying on it.

**Phase 2 verify:** `get_ej_index` returns a varying value across ≥2 distinct block groups in a scratch test; connectivity test exists; README no longer claims a live EPA EJSCREEN call.

**Sources to cite in the README EJ note:** EJSCREEN removal — [EELP tracker](https://eelp.law.harvard.edu/tracker/epa-added-environmental-health-indicators-to-ejscreen/), [EDGI](https://envirodatagov.org/epa-removes-ejscreen-from-its-website/); CEJST removal — [EELP tracker](https://eelp.law.harvard.edu/tracker/ceqs-climate-economic-justice-screening-tool-removed/); reconstruction mirror — [screening-tools.com](https://screening-tools.com/epa-ejscreen). Methodology: percentile-ranked ACS demographics per EPA's EJSCREEN Technical Documentation.

---

# Phase 3 — Core scientific correctness (Python) — the heart of the fix

- **C1 — Hydrology in degrees, not meters.** In `core/pipeline.py::fetch_dem`, reproject the DEM to **UTM (EPSG:32617)** *before* pysheds (`dem.rio.reproject(UTM_CRS)`), so pixel size is metres. In `run_pipeline`, map UTM candidates straight onto the metric affine (no WGS84 round-trip); `pixel_size = abs(transform[0])` is now metres. This fixes **C1a** (catchment area `acc·pixel²/1e6` → real km², no longer clamped to 0.01) and **C1b** (slope reach). Verify with a synthetic 10 m UTM DEM: catchment area for `acc=50000` ≈ 5 km² (not 0.01).
- **C1b / M7 (Python) — slope follows the flow grid, not "due south."** In `core/flow.py::compute_flow_velocity`, walk ~100 m **downstream along the D8 `fdir` grid** (add `_follow_downstream(r, c, dem, pixel_m, fdir, reach=100)` that steps via the D8 dirmap `{64:N,128:NE,1:E,2:SE,4:S,8:SW,16:W,32:NW}`), with steepest-descent fallback when `fdir` is absent. Thread `fdir` through `build_all_features → compute_flow_features → compute_flow_velocity`. Verify on a synthetic DEM dropping east with `fdir=East`: the walk ends east and slope is positive.
- **C2 — Generation must vary per candidate.** Stop the catchment-level collapse (`for k,v in gen_features.items(): df[k]=v`). Instead compute generation features **per candidate**: sample impervious % at the candidate's NLCD pixel; population density from the candidate's block group; TRI/NPDES/CSO/litter counts within the candidate's **own upstream catchment** (use pysheds `grid.catchment(x, y, fdir, ...)` — you already have `fdir`). If full per-candidate delineation is too slow, bin candidates by sub-catchment (group by nearest accumulation node) so generation still varies — but it must not be one constant column. Then in `compute_subscore`, **exclude constant columns** (a constant column → 0 under `MinMaxScaler`, silently deleting its weight) and **renormalize the surviving weights** with a logged warning; all-constant family → neutral 50, not 0. Verify: on a 6-constant + 1-varying synthetic family, sub-scores span `[0..100]`; provenance summary (below) reports how many of 27 params actually vary.
- **Provenance summary (C2/C4 support).** Add `summarize_provenance(df)` printing a loud "X/27 parameters vary per-candidate (live) · Y constant (fallback): …" line, called in `build_all_features`. This makes dead endpoints and constant columns visible every run.
- **H2 — Reproducible scored file.** Add `scripts/score_candidates.py` that runs `run_pipeline → build_all_features → compute_composite_score` and writes `mock_data/candidates.geojson` (and update the notebook's Step 4, which is titled "Generate **& Score**" but only generates). Run it for Durham/Ellerbe and commit the regenerated file so the shipped numbers match what the code produces. (This will surface C1/C2 — that's the point.) Verify: the committed `candidates.geojson` is byte-reproducible from the script.
- **H3 — "Strahler" is a node-degree heuristic.** Either compute **true Strahler** on the directed D8 network (you have `fdir`; use `networkx` on the directed stream graph with the merge rule "two order‑i meet → i+1"), **or rename** the field to `stream_order` / `confluence_degree` everywhere (code, README param 10, docs, UI) and stop calling it Strahler. Pick one; the canonical list above assumes the rename unless you implement true Strahler. Also widen the endpoint-matching tolerance (currently rounds to 0.1 m) so segments that don't share exact endpoints don't all collapse to order 1.
- **H5 — estuary_dist and beach_dist are the same variable.** `beach_dist = estuary_dist × 1.1` → correlation 1.0. Fix both halves: (1) rewrite `estimate_estuary_distance_km` as a real **haversine** to a fixed estuary reference (the current `coast_lat = lat` makes the `dlat` term identically 0); (2) make beach distance genuinely distinct — haversine to a real recreational-beach reference (or the EPA BEACH dataset you cite for param 19). Keep both params (preserves 27) but they must be decorrelated. Verify: corr(estuary, beach) < 0.99 across a candidate set; estuary distance changes with latitude.
- **M1 — Strahler→width fallback explodes.** `get_channel_width` method 3 returns `2.5·2.5^order` (order 4 → 98 m, order 5 → 244 m), which self-trips the width hard gate and silently deletes big streams. Replace with a calibrated, bounded width–order relation (e.g. `min(W_max, a·order^b)` with sane `a,b`, or a per-waterway-type table consistent with the explorer's `{river:20,canal:8,stream:3,drain:2,ditch:2}`), and note that Leopold 1964 is hydraulic geometry (W ∝ Q^~0.5), not this exponential. Verify: no order ≤5 estimate exceeds the width gate.
- **M4 — Manning "continuity cross-check" isn't independent.** `V_continuity = Q/A` uses the basin-wide gauge mean Q and the *same* `A_cross` as Manning, so the geometric mean isn't a real cross-check. Either scale discharge to the candidate's catchment area (now that C1a gives real km²) so `V_continuity` is site-specific, or drop the "independent cross-check" framing in the docs.
- **M5 — velocity is "good high" in Flow but "bad high" in Feasibility.** Don't feed raw `flow_velocity_ms` into Flow as monotonic-good. Transform it through a **peaked transport-favorability curve** (optimal mid-range, low at both extremes) before weighting in Flow, so it doesn't fight the Feasibility `velocity_feasibility` gate. Document the curve.
- **L4 — provenance wording.** Runoff `C = 0.05 + 0.009·I` is not "NLCD k-means" — fix the docstring/README param 14 to describe what it actually is (linear impervious→C approximation). Fix the WaterGate author-order inconsistency in the references.
- **L5 — `water_intake_score` "downstream".** It sums `exp(-d/10)` over all intakes within 50 km regardless of direction. Either gate by flow direction / network distance, or correct the docstring + README to say "omnidirectional proximity (not flow-gated)."
- **L6 — `n_years`** is `len(peak_values)` (count of annual-peak records), not guaranteed distinct years — relabel.

**Phase 3 verify:** `python3 -m py_compile core/*.py`; the scratch tests above pass; `scripts/score_candidates.py` reproduces `candidates.geojson`; provenance summary prints a sane live/constant split.

---

# Phase 4 — Dashboard honesty & determinism (JS) (C3, H6, M3, M7‑JS)

- **C3 + determinism — make the explorer deterministic and stop using `Math.random()` for named quantities.** Two parts:
  1. **Per-candidate RNG (stability).** Today the explorer scores from one seeded RNG (`R = seededRand(seed+7777)`) consumed **sequentially across all candidates**, so when pan/zoom loads more streams the whole set re-randomizes and **rankings churn on every render**. Replace it with a **per-candidate RNG seeded by the site's own coordinates** (e.g. `const pr = seededRand(Math.floor(hash01(pos.lat.toFixed(5)+','+pos.lon.toFixed(5))*2147483647)+1+seed)`), and use `pr()` for every draw. Then a site's values are identical across re-renders, and loading more streams only adds sites — it never reshuffles existing ones. Verify: the same `(lat,lon)` yields identical draws regardless of set size.
  2. **Kill the pure noise in Impact.** `ips = R()*30*.22 + R()*20*.16 + …` makes ~40% of Impact pure noise. Drive the water-intake and protected-area terms from geometry/`upstreamFrac` like the other params (deterministic), or at minimum from the per-candidate `pr()` so they're stable. Fix the Generation weight too (`imp*.4` should be the documented 0.20). Document the explorer's formula as a **separate heuristic** (see M3), not the Python MinMax model.
- **H6 — caveat on synthetic numbers.** Ensure the site-detail panel and the landing dash clearly label the values as model estimates from OSM geometry, not live measurements (e.g. "Demo values are model estimates from OSM geometry — not live gauge/Census data. See Docs."). Confirm it renders.
- **M7 (JS) — downstream direction for occlusion.** The greedy occlusion must discount **genuinely downstream** sites. Don't rely on global creation `id` or raw `coordIdx` across multiple OSM ways of one river. Add a `computeWayOrder(streams)` that chains a river's ways head→tail by matching endpoints (≤~60 m), then occlude by `(wayOrder, coordIdx)`; portals are point anchors that neither occlude nor get occluded. Verify on a 2-way river (downstream way created first): occlusion hits all true-downstream points, none upstream.
- **M3 — document the algorithms honestly.** README §6.2 shows static top-N% but the real selection is **greedy + multiplicative upstream occlusion `(1−η)^k`, η=0.65** — document that (it's a selling point). And state plainly that the explorer is a separate clamped-formula heuristic, not a MinMax "parallel" of the Python model (fix ADR-3).

**Phase 4 verify:** `node --check` clean; div balance unchanged; a `(lat,lon)` determinism scratch test passes; occlusion direction test passes.

---

# Phase 5 — Reproducibility, single-source-of-truth, tests, deploy hygiene

- **`model.json` single source of truth.** Create `model.json` holding the 27 weights, sub-score weights, hard gates, spacing, feasibility curves, and runoff formula. Add `scripts/check_model.py` that asserts the Python constants match it (fail loudly on drift) and run it in the verify step. This makes every docs↔code contradiction (M2/M3) structurally impossible to reintroduce.
- **L9 — automated tests.** Add a small `pytest` suite: composite ∈ [0,100]; Manning velocity monotonic in slope; hard gates remove the right rows; `compute_subscore` drops a constant column and renormalizes as intended; occlusion is non-increasing along a river. Cheap insurance and a checkbox for judges.
- **M8 — CORS.** Leave `allow_origins=["*"]` for the public-data demo, but add a comment + an env-driven allow-list option so it can be locked to the deploy origin before grime.world serves the API publicly.
- **Docs reconciliation pass.** Regenerate the README/`documentation.md`/`docs/index.html`/`Overview.md` formula tables from `model.json`; caption any illustrative figures "illustrative, not pipeline output"; fix KaTeX/citation nits; reconcile the runtime claim (state the real dashboard vs full-pipeline timings).

**Phase 5 verify:** `python3 scripts/check_model.py` passes; `pytest` green; div balance + `node --check` + `py_compile` all clean.

---

## Final verification checklist (run before the last commit)

- [ ] `grep -rn "28 param\|108,772\|240 countries\|gARB\|1,500 rivers"` → no hits
- [ ] weight dicts sum to 27 params (7/7/7/6); `model.json` matches code (`scripts/check_model.py`)
- [ ] `python3 -m py_compile core/*.py api/main.py scripts/*.py` clean
- [ ] explorer + landing inline JS `node --check` clean; div balance equal per file
- [ ] `scripts/score_candidates.py` reproduces `mock_data/candidates.geojson`; catchment area is real km² (not 0.01)
- [ ] provenance summary shows the EJ index + generation params **varying** (not constant)
- [ ] explorer rankings are stable across re-render (same `(lat,lon)` → same score); Impact has no live `Math.random()`
- [ ] README endpoint table matches `api/main.py` routes; `.env.example` + `.gitignore` + `LICENSE` exist
- [ ] `pytest` green
- [ ] `FIXES.md` has a row for every C#/H#/M#/L# with status + proof

---

## What's good — DO NOT break

- Clean modular architecture (four parameter families, shared `safe_call` with fallbacks, clear pipeline/scoring/API/dashboard separation).
- Two-level interpretable scoring (sub-scores map to plain-language questions). Make the implementation live up to it (C2/C3) — don't remove the framing.
- **Greedy upstream-occlusion selection** with compounding `(1−η)^k` — the most original part of the system. Keep it; just fix its downstream-direction logic (M7) and document it (M3).
- Dirichlet sensitivity analysis (consider extending to the 27 inner weights, but keep it).
- Dashboard engineering: progressive pan-autoload with bbox coverage, dedup by OSM id, rate-limiting, river-focus tracing, graceful "no waterway data" state.
- Graceful failure posture (every external call wrapped; offline/token-less paths degrade).
- No secrets leaked; correct Cauchy `1/(1+(d/h)²)` and exponential `exp(−d/10)` kernels.

---

## After all phases

Update `FIXES.md` and give one consolidated summary: what changed per phase, what you verified (with the numeric proofs), and anything you skipped or deferred and why. Then run the **Final verification checklist** one more time and confirm every box.

---

# Phase 6 — Strategic additions (BE SMART HERE — propose, prioritize, then build)

The phases above make GRIME *correct and honest*. This phase makes it *win*. **Do not** mechanically dump features. First produce a short ranked table (addition · effort · judge-facing payoff · risk), then implement only the high-payoff, low-risk ones, newest-value first. Stay inside the "do not break" list — no rewriting working systems.

Candidate additions, roughly in priority order (use judgment, justify your picks):

1. **Wire US cities to the real pipeline (highest leverage).** For Durham and other US cities, have the explorer call your own `/api/candidates` (real Python output) and render real catchment/velocity/EJ with a visible "real data" badge; fall back to the labeled simulation elsewhere. This is the single change that converts "a judge clicks Durham and sees simulated proxies" into "a judge clicks Durham and sees the real model." Depends on Phase 3 (the pipeline must actually produce trustworthy numbers first) — sequence it last among the big items.
2. **Property tests (turns "no tests" into a checked box).** ~50 lines of `pytest`: composite ∈ [0,100]; hard gates monotone; occlusion non-increasing along a river; `compute_subscore` drops a constant column and renormalizes. (Overlaps L9 — make it real, not token.)
3. **Real robustness figure.** Run the Dirichlet sensitivity at n=500 and publish the actual rank-stability histogram in the docs, replacing any synthetic/Beta-mock figure (caption honestly).
4. **One slide of ground truth.** Compare GRIME's top Ellerbe Creek sites against 3–5 real, photographed accumulation points. One slide of validation beats any amount of methodology text at a science fair.
5. **Performance.** Parallelize the sequential per-candidate EPA calls (`ThreadPoolExecutor`/`asyncio`), cache `padus.gpkg` and StreamStats per catchment, pause the landing globes off-screen. Only if it doesn't risk correctness.

For each addition you implement: explain the approach first, keep it reversible, and add a `FIXES.md` row. For each you skip: say why (effort/risk/scope) in one line. **Bias: integrity and reproducibility before flash.**

---

---

# APPENDIX — The audit (source of every finding above)

> The full "GRIME — Full Code, Math & Data Audit," performed against **this exact commit (`d2a0a33`)**. The `file:line` references are accurate to the current code — use them directly.

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

### CRITICAL

**C1 — Hydrology is computed in geographic coordinates (degrees), so meter-based math is unit-broken.** DEM fetched in WGS84 (`pipeline.py:26`, notebook Step 1); pysheds runs D8/accumulation on a degree grid. **C1a:** `compute_catchment_area` does `acc·pixel_size_m²/1e6` (`pipeline.py:185-189`) but `pixel_size=abs(transform[0])≈0.0000898°` (`:228`,`:243`) → every candidate clamps to `0.01 km²`. **C1b:** in `compute_flow_velocity` (`flow.py:79-87`), `pixels_downstream=int(100/0.0000898)≈1,113,200` → `r_down` clamps to the bottom DEM row → slope is `(elev_here−elev_bottom)/100`, meaningless. Fix: reproject DEM to UTM (EPSG:32617) before pysheds.

**C2 — Generation sub-score does not vary across candidates.** `build_all_features` assigns catchment-level generation to every row (`scoring.py:185-192`); `_compute_catchment_generation` hardcodes 4 of 7 params (`scoring.py:207-234`). `compute_subscore` MinMax-normalizes (`scoring.py:48-65`); a constant column → 0 under `MinMaxScaler` → `generation_score=0` for all. Same kills constant Flow params (`usgs_mean_q_cfs`, `seasonal_cv`, often `flood_q10_cfs`, and via C1a `catchment_area_km2`). Fix: per-candidate generation on each candidate's upstream catchment.

**C3 — Dashboard scoring is largely synthetic; Impact is mostly random.** `explore/index.html:483-515`. Impact (`:500`) `ips=R()*30*.22+R()*20*.16+ej*80*.18+(1-80/300)*80*.14` — water-intake & protected-area terms pure `Math.random()`, estuary term constant. Generation (`:498`) uses impervious weight 0.4 (docs say 0.20). ~11 of 27 params absent. No MinMax. Reloading flips scores. Fix: make Impact deterministic / drive from real proxies, or relabel.

**C4 — EJSCREEN removed by EPA, endpoint dead.** `get_ejscreen_index` calls `ejscreenRESTbroker.aspx` (`impact.py:56-82`); EPA removed EJSCREEN 2025‑02‑05 → always 0.5 fallback → EJ constant → 0 after MinMax. Fix: Census ACS / maintained mirror; add a connectivity test; re-verify other federal endpoints.

**C5 — "28 parameters" is 27.** 7+7+7+6 (`generation.py:253-261`, `flow.py:188-196`, `impact.py:212-220`, `feasibility.py:182-189`). "28" appears 10× in README + landing (`index.html:310,378`) + docs + explorer (`:982`). Fix: 28→27 everywhere, or add a real 28th.

**C6 — "Bayesian weight optimization: implemented" is absent.** No `gp_minimize`/`optimize_weights`/`skopt` in `core/`; `scikit-optimize` installed for nothing. Fix: implement the scaffold or relabel "designed, not implemented."

### HIGH

**H1 — places.json is 89,518/239, claimed 108,772/240** in ~15 places (README 14/142/594/633/765/893; landing 293/301/304/378/398/402; docs 168; Overview 26/97; documentation 20/1122; explorer 115). Runtime tiles show the real number → page contradicts itself. Fix: regenerate to 108,772 or correct every claim to true counts; say "places."

**H2 — No committed path reproduces the scored `candidates.geojson`.** CLI outputs unscored (and 0.01 catchment); notebook Step 4 only generates; `build_all_features` (`scoring.py:142`) never invoked. Fix: `scripts/score_candidates.py` end-to-end; make shipped numbers match.

**H3 — "Strahler" is node-degree.** `compute_strahler_order` (`pipeline.py:145-182`) uses an undirected graph, `order=max(deg(start),deg(end))` capped 5 — not Strahler. Fix: true Strahler on the directed D8 net, or rename `confluence_degree`/`stream_order`. Endpoint rounding to 0.1 m fragments the graph.

**H4 — No `.gitignore`** though docs promise `config.js`/`.env` ignored. `.DS_Store` already tracked. Fix: add `.gitignore` (config.js, .env, data/, __pycache__/, *.pyc, .DS_Store, .venv/).

**H5 — estuary_dist ≡ beach_dist.** `beach_dist=estuary·1.1` (`impact.py:243`), corr 1.0, double-weighted 0.26. `estimate_estuary_distance_km` sets `coast_lat=lat` → `dlat≡0` (`:185`), longitude-only. Fix: real haversine + a genuinely distinct beach reference/dataset.

**H6 — Demo shows synthetic as measured.** Site-detail panel (`explore/index.html:1090`) renders Pop/Imperv/Velocity/Mean Q/Strahler/Catchment/EJ/Road/Width/Slope from `upstreamFrac`+RNG with no UI caveat. Fix: add a one-line caveat / mark estimated fields.

### MEDIUM

**M1 — width fallback `2.5·2.5^order`** (`feasibility.py:80-82`): order 4→98 m, 5→244 m → trips the width hard gate, deletes big streams. "Leopold 1964" is hydraulic geometry, not this exponential. Fix: calibrated/bounded width–order relation.

**M2 — README MinMax "0.5"** ≠ actual 0.0 (`MinMaxScaler`); 0.5 only in unused `normalize_series` (`__init__.py:51-57`). Fix: document real behavior; decide neutral (0.5) vs drop.

**M3 — docs ≠ code.** README §6.2 shows top-N%; real is greedy occlusion (`explore:517-562`). ADR-3 claims JS "parallels Python"; it's a different clamped-formula model. Fix: document occlusion; describe JS as a separate heuristic.

**M4 — Manning "continuity cross-check" not independent.** `V_continuity=Q/A` uses basin-wide gauge mean Q + same `A_cross` (`flow.py:99-100`). Fix: site-specific discharge (scale by catchment) or drop the framing.

**M5 — velocity "good high" in Flow, "bad high" in Feasibility.** Raw `flow_velocity_ms` (0.16) monotonic-good in Flow contradicts `velocity_feasibility`. Fix: peaked transport-favorability curve in Flow.

**M6 — `/map` route + `.env.example` missing.** README §11 lists `/map` (no route); §12/§13 say `cp .env.example .env` (no file). Fix: add/remove route; add `.env.example`.

**M7 — "Downstream" assumed.** Python slope uses `r+pixels_downstream` (= south, `flow.py:85`); JS occlusion treats higher `coordIdx` as downstream (`explore:548`). Fix: derive from the D8 flow grid / way chaining.

**M8 — CORS `allow_origins=["*"]`** (`api/main.py:30-35`). Fine local; lock to deploy origin before public.

### LOW
**L1** `gARB` in 8 files → GRIME. **L2** stray 0-byte `uvicorn` file → delete. **L3** hero stats `11M+/91%/5T` unsourced + awards `href="#"` dead. **L4** runoff "NLCD k-means" is just `0.05+0.009·I`; WaterGate author order inconsistent. **L5** `water_intake_score` sums over all intakes within 50 km regardless of direction (`impact.py:42-51`). **L6** `n_years=len(peak_values)` mislabel (`flow.py:49`). **L7** "six federal APIs" undercounts. **L8** "over 1,500 rivers = 80%" — cite Meijer 2021 (1,656). **L9** no automated tests — add a small pytest.
