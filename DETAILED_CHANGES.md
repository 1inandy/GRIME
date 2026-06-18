# GRIME — What Changed, and Why

This document explains everything on the `audit-fixes-live` branch: the problems a
full code/math/data audit found, what was changed to fix each one, why it matters,
and the honest limits of where the project stands now. It is written to be read
start-to-finish, with a file-by-file reference at the end.

**Scope:** 34 files, +11,525 / −2,048 lines, on top of `main`. The work falls into
three buckets: (1) correcting claims that didn't match the code, (2) fixing the
science so the model actually does what it says, and (3) running the real pipeline
on live data for the first time.

---

## At a glance

- **Every falsifiable claim is now true** — 27 parameters (not "28"), 89,518 places /
  239 countries (not "108,772 / 240"), Bayesian optimization actually implemented,
  dead links and stale branding fixed.
- **The scoring model now works as documented** — the hydrology was running in the
  wrong units, a third of the score did nothing, and one signal was double-counted.
  All three are fixed.
- **The environmental-justice feature was dead** (EPA removed EJSCREEN) — it's now
  reconstructed from live Census data.
- **The real pipeline ran end-to-end for the first time** on Durham/Ellerbe Creek,
  producing 147 real candidate sites — and that run surfaced (and fixed) a series of
  bugs in code that had never actually executed.
- **The interactive explorer is now deterministic and honestly labeled** — scores no
  longer reshuffle on every render, and simulated values are marked as estimates.

---

## 1. Claims and numbers that didn't match the code

**The problem.** Several headline figures were simply wrong against the repo. The
docs and UI said "28 parameters," but the code defines 27 (7 generation + 7 flow +
7 impact + 6 feasibility). The site advertised "108,772 cities / 240 countries," but
`places.json` actually holds 89,518 entries across 239 — and most are procedurally
generated, not real cities. The README claimed "Bayesian weight optimization:
implemented," but no such code existed. The pollution stats were unsourced, the award
links were dead (`href="#"`), and the codename `gARB` lingered in a dozen files.

**The fix.** "28" → **27** everywhere (code, README, docs, landing, explorer).
"108,772 cities" → **89,518 places / 239 countries**, with the procedural generation
disclosed. A real `optimize_weights()` was implemented with scikit-optimize's
`gp_minimize`, so the claim is now true. Stats were re-sourced (Borrelle 2020,
Geyer 2017, Eriksen 2014, Meijer 2021), award links wired to the real pages, and all
`gARB`/`gRIME` → `GRIME`.

**Why it matters.** These are the things a judge can check in ten seconds. A single
"the README says 28 but I count 27" is enough to make a technical reviewer distrust
everything else. Removing every such discrepancy is the highest-leverage, lowest-risk
work in the whole branch.

## 2. The hydrology was computed in degrees, not metres (the biggest science bug)

**The problem.** The pipeline fetched the elevation model in WGS84 (longitude/latitude
degrees) and then ran metre-based math on it. Pixel size came out as ~0.00009 (a
fraction of a degree) but was treated as metres. Two things broke as a result:
catchment area, computed as `cells × pixel²`, collapsed to essentially zero and got
clamped to a 0.01 km² floor **for every single site**; and the "channel slope over a
100 m reach" tried to step ~1.1 million cells and just ran off the bottom edge of the
map, producing a meaningless slope that fed straight into the velocity estimate.

**The fix.** The DEM is now **reprojected to UTM (metres) before any hydrology runs**,
so pixel size is real metres and cells are near-square. Catchment area and slope are
now physically meaningful. A companion fix (`snap_to_channel`) snaps each candidate to
the nearby maximum-accumulation cell, because a candidate interpolated along the
*vector* stream can land a pixel or two off the channel on the raster and still read a
near-zero catchment.

**Why it matters.** Catchment area is one of the model's headline inputs. Before this,
if a judge asked "what's the drainage area at your top site?", running the code gave
0.01 km² for everything while the shipped file said 63.5 — a contradiction that
undercuts the whole hydrology story. After the fix, the live run produces a real
spread of **2.0–115.9 km²**.

## 3. A third of the score did nothing (the inert generation sub-score)

**The problem.** "Generation" (is trash entering here?) is 30% of the composite. But
the code computed it once at the whole-catchment level and copied the same value to
every candidate. A column that's identical for every row normalizes to zero in the
MinMax step — so generation contributed **nothing to the ranking**. The same fate hit
flow parameters that are single-gauge constants. In effect, the model was ranking on a
handful of parameters, not 27.

**The fix.** `build_all_features` now computes generation **per candidate** on each
candidate's own upstream catchment. And `compute_subscore` was hardened: instead of
letting a constant column silently vanish to zero, it **drops constant columns and
renormalizes the remaining weights** (logging which it dropped), and returns a neutral
50 if an entire family is constant. A new `summarize_provenance()` prints, every run,
how many of the 27 parameters actually vary versus fall back — so this kind of silent
collapse can't hide again.

**Why it matters.** This is the difference between "27 parameters" being a real
description of the model versus a label. It also makes the provenance honest: the run
now tells you exactly how much of the score is live signal.

## 4. The environmental-justice data source was dead

**The problem.** GRIME headlines environmental justice as a differentiator and sourced
it from EPA's EJSCREEN. But EPA **decommissioned EJSCREEN on 2025-02-05** (and the
White House pulled the related CEJST tool weeks earlier). The code was calling a dead
endpoint, so the EJ index silently fell back to a constant 0.5 for every site — which,
per problem #3, then contributed nothing.

**The fix.** EJ is now **reconstructed from live Census ACS data**, which is exactly
what EJSCREEN's demographic index is built from. The code pulls % low-income (table
C17002) and % people-of-color (B03002) per block group, percentile-ranks them within
the county, and area-weights the result over each candidate's catchment — so EJ varies
per site and runs on a stable, maintained API the project controls. A new
`healthcheck.py` pings every federal endpoint and reports which are alive, so a dead
source is surfaced loudly instead of swallowed.

**Why it matters.** It turns a dead, falsely-described feature into a working,
honestly-sourced one — and the "we reconstruct EJSCREEN's methodology from live ACS
because EPA removed the tool" framing is a stronger story than "we call a tool that no
longer exists."

## 5. Other modeling corrections

- **Velocity slope walked the wrong way (C1b/M7).** The slope step went due south
  regardless of where water actually flowed. It now follows the D8 flow-direction grid
  downstream (with a steepest-descent fallback).
- **"Strahler order" wasn't Strahler (H3).** It's a confluence node-degree heuristic,
  not true Strahler order. It's been renamed `stream_order` and documented honestly,
  the value is now actually carried onto candidates, and the endpoint-snapping
  tolerance was widened (0.1 m → 5 m) so the stream graph stops fragmenting.
- **Estuary and beach distance were the same number (H5).** `beach = estuary × 1.1`,
  and the estuary calc had a dead latitude term — so one signal was weighted twice.
  Both are now real haversine distances to distinct reference points.
- **The width fallback exploded (M1).** `2.5 × 2.5^order` reached 244 m at order 5,
  which tripped the width hard gate and silently deleted large streams. It's now a
  bounded `min(40, 3·order^1.1)` that never self-trips.
- **The Manning "cross-check" wasn't independent (M4).** It reused one gauge's
  basin-wide mean flow for every site; it now area-scales discharge to each
  candidate's catchment, so it's site-specific.
- **Velocity pulled the score in two directions (M5).** High velocity was "good" in
  Flow but "bad" in Feasibility. Flow now passes velocity through a peaked
  transport-favorability curve (best around 0.9 m/s) instead of treating
  faster-as-better.

## 6. Security and interaction bugs in the explorer

- **Cross-site scripting (X1).** OSM way names and place names were written straight
  into the page. An OSM feature named `<img src=x onerror=…>` would execute in every
  visitor's browser. All third-party strings are now HTML-escaped via a small `esc()`
  helper.
- **Stale-fetch race (X2).** Clicking city A then quickly city B could let A's slow
  network response overwrite B's data. `openCity` now awaits into a local variable and
  checks it's still the active city before committing.
- **Leaking map listeners (X3).** Click/hover handlers were re-registered on every
  "back to all cities" navigation, so after N round-trips one click fired N parallel
  fetches. They're now attached once.

## 7. The explorer scored randomly and presented it as real

**The problem.** The interactive map is a client-side simulation. Three issues made it
look untrustworthy: the "Impact" sub-score used literal `Math.random()` for named
quantities, so two clicks on the same site gave different numbers; the whole scoring
ran off one shared random generator consumed in sequence, so **loading more streams
re-randomized every site and the rankings reshuffled on every render**; and the panel
presented all of it as if measured.

**The fix.** Each candidate now gets its **own random generator seeded by its
coordinates**, so a site's values are identical across re-renders and panning only
adds sites — it never reshuffles existing ones. The `Math.random()` Impact terms were
made deterministic. The upstream-occlusion logic was corrected so it discounts
genuinely-downstream sites river-wide (chaining a river's OSM segments head-to-tail)
rather than by an arbitrary id order. And the panel now carries a clear caveat that the
values are model estimates from OSM geometry, not live measurements, with the explorer
labeled a separate heuristic from the Python pipeline.

**Why it matters.** The explorer is the primary thing a judge clicks. Stable, coherent,
honestly-labeled numbers are the difference between "a thoughtful demo" and "the scores
change when I reload."

## 8. Nothing reproduced the results — and the real pipeline had never run

**The problem.** The shipped `candidates.geojson` had realistic, varying values, but no
committed code path could regenerate it — the command-line pipeline produced unscored
candidates (with the 0.01 km² bug), and the scoring function was never actually
invoked. For a science competition, that's a reproducibility gap.

**The fix, part one.** A new `scripts/score_candidates.py` runs the real pipeline
end-to-end and writes the scored file, and the validation notebook now actually scores
(it previously only generated).

**The fix, part two — the live run.** The `--live` path was then executed for the first
time, on the real Ellerbe/Eno network. Because that code had never run, it surfaced a
chain of real bugs, each fixed:

- The pysheds API had changed; `process_hydrology` was rewritten for the modern
  raster-object interface.
- `extract_river_network` was being handed raw accumulation instead of a boolean
  channel mask, so it returned a ~2-million-segment, 438 MB degenerate network and the
  `threshold` argument was silently ignored. Fixed to pass `accumulation > threshold`.
- The TIGER block-group query was hitting **layer 10 (school districts)** instead of
  layer 8 (block groups), and wasn't quoting the state/county fields — which is why
  population *and* EJ had been quietly failing. Fixed.
- The USGS peaks API had been renamed (`get_peaks` → `get_discharge_peaks`); the call
  was updated and made non-fatal so the live gauge stats survive.
- The 3DEP elevation server intermittently returns 502s; a retry/backoff was added.
- Whole-region lookups (the road network, block groups, EPA queries, NLCD) were being
  re-fetched once per candidate; they're now fetched once and cached, turning a
  multi-hour run into minutes.

**The result.** 147 real candidate sites; catchment 2.0–115.9 km²; composite 13.4–61.9;
fully reproducible from `score_candidates.py`.

## 9. Engineering foundation

- **`model.json`** is now the single source of truth for weights, gates, spacing, and
  curves, with `check_model.py` asserting the code matches it (so docs and code can't
  silently drift apart again).
- **A 19-test pytest suite** checks invariants and integration regressions
  (composite ∈ [0,100], gates monotone, occlusion non-increasing, constant-column
  handling, flow→feasibility propagation, stable API candidate IDs, and
  catchment-clipped road density).
- **CORS** is now lockable to a deploy origin via an env var.
- A real **n=500 Dirichlet robustness** figure replaces any mock.
- `.gitignore`, `LICENSE`, and `.env.example` were added (the README referenced all
  three; none existed).

---

## The data-coverage reality (read this before the competition)

The pipeline now runs on real data, but **only 11 of the 27 parameters vary per site;
16 are constant.** That breaks down honestly as:

- **Live and varying (11):** population density, catchment-clipped road density,
  flow velocity, stream order, catchment area, EJ index, estuary distance, beach
  distance, tourism density, road access, and velocity feasibility.
- **Live but basin-wide constant (3):** gauge mean flow, seasonal CV, flood Q10 (one
  gauge value for the whole catchment).
- **Saturated or derived-constant (4):** protected-area score, runoff coefficient, and
  a few discretized feasibility scores that all land in one bucket.
- **Dead/unavailable endpoints (the rest):** EPA ECHO (NPDES/CSO/water intake),
  StreamStats (flood), Durham 311 (litter), and — notably — **`impervious_pct`, the
  highest-weighted generation input (0.20), which is a flat 35% fallback** because NLCD
  imperviousness isn't served by the elevation API in use.

Two more open items:

- **The explorer is still a simulation.** The real `candidates.geojson` is served at
  `/api/candidates`, but the interactive map doesn't call it yet — so grime.world still
  shows simulated estimates even for Durham. Wiring the explorer to the live pipeline is
  the next high-value step.
- **The paper has not been reconciled.** It still references 28 parameters, EJSCREEN,
  composite 87, true Strahler, 25 m spacing, and a 10,000-iteration Monte-Carlo — all of
  which the repo now contradicts.

---

## File-by-file reference

**New files:** `LICENSE`, `model.json`, `scripts/check_model.py`,
`scripts/score_candidates.py`, `scripts/healthcheck.py`, `scripts/robustness_report.py`,
`tests/test_grime.py`, `dashboard/docs/exports/robustness_hist.png`, plus the audit/fix
docs (`AUDIT.md`, `FIXES.md`, `FIX_PROMPT.md`, this file). **Removed:** the stray
`uvicorn` file and 20 committed `__pycache__/*.pyc` files.

- **`core/__init__.py`** — `census_api_key()`; cached `osm_drive_graph()`;
  `ELLERBE_DRAINAGE_KM2`; branding.
- **`core/pipeline.py`** — UTM reprojection + 3DEP retry in `fetch_dem`; pysheds-modern
  `process_hydrology`; the `extract_streams` mask fix; `stream_order` carried onto
  candidates; `compute_stream_order` rename + wider endpoint snap; `snap_to_channel`;
  DEM-reuse in `run_pipeline`.
- **`core/flow.py`** — D8 downstream walk (`_follow_downstream`); peaked
  `velocity_transport_favorability`; `get_discharge_peaks` fix + `n_peak_records`;
  area-scaled continuity; `stream_order`; runoff docstring.
- **`core/generation.py`** — cached `_block_group_density` with the TIGER layer-8 +
  quoting fix; region caches for TRI/NPDES/NLCD/litter; road density via the shared
  drive graph; filtered EPA fetchers.
- **`core/impact.py`** — EJSCREEN → Census-ACS reconstruction (`_fetch_county_demographics`,
  `_area_weighted_index`, `get_ej_index`); real haversine estuary + distinct beach;
  OSM feature + superfund caches; corrected water-intake docstring.
- **`core/scoring.py`** — constant-column drop + renormalize in `compute_subscore`;
  per-candidate generation in `build_all_features`; `summarize_provenance`;
  `optimize_weights`.
- **`core/feasibility.py`** — cached road-access distance; bounded width fallback;
  `stream_order`; bbox threading.
- **`api/main.py`** — env-driven CORS; new `/map` route; `stream_order`.
- **`dashboard/explore/index.html`** — `esc()` escaping; coordinate-seeded per-candidate
  RNG; deterministic Impact; river-wide `computeWayOrder` occlusion; listeners-once;
  openCity race guard; estimate caveat; count fixes.
- **`dashboard/index.html`** — counts, honest mock numbers, sourced stats, real award
  links, roadmap reframing.
- **`dashboard/docs/*`, `README.md`** — formula/claim/count reconciliation; EJ source;
  references; MinMax behavior; Strahler → stream order.
- **`start.sh`** — branding; healthcheck step; `/api/stats` curl.
- **`mock_data/candidates.geojson`** — regenerated from the live pipeline (147 real
  sites). **`notebooks/validate_pipeline.ipynb`** — branding; Step 4 now scores.
- **`.gitignore` / `.env.example`** — added, merged with the co-author's hygiene commit.
