# GRIME — Validation log

Receipt ledger for FIX_PLAN_2: every score-changing phase (1, 2, 3) re-runs the
paper's validation protocol and appends a row, so "did wiring real data change
the results?" is always answered with a number. Protocol constants are frozen in
`scripts/validate_paper.py`; per-run artifacts land in `cache/validation/`
(gitignored) with the run label in the filename.

## The published figures (SJWP paper, June 23 2026 USA submission)

| Figure | Value | Ground truth | Locally re-runnable? |
|---|---|---|---|
| Ellerbe recall (80 m) | **8/10 (80%)** | ECWA debris-hotspot log 2014–2025 (unpublished) | **No** — the 10 hotspot coordinates exist nowhere on this machine (searched repo, paper sources, vault, mail). Carried as published; misses would be invisible, so regression tracking rests on the other three columns. |
| Waterkeepers recall (80 m) | **22/27 (81.5%)** | Waterkeepers Carolina "Trash Trout" ArcGIS layer (public) | **Yes (reconstructed)** — layer re-fetched 2026-07-09 (29 features), frozen to the paper's 27 by excluding ObjectId 23 (Fort Mill — South Carolina; the paper set is NC-only) and ObjectId 29 (Raleigh–Walnut Creek — post-submission addition: highest ObjectId, no Install_Date, single Raleigh marker in the paper's Fig. 8). Fixture: `tests/fixtures/waterkeepers_trash_trout_locations.geojson`. |
| Dirichlet top-25 stability | **94.7%** (min 85, max 100, 9/10 sites >90%) | committed `mock_data/candidates.geojson` (147 live candidates) | **Yes (exact)** — reproduced 2026-07-10: mean 94.7, min 85.3, max 100.0, 9/10 >90% (`validate_paper.py --dirichlet`, n=10,000, seed 42, α=10×[.30,.25,.30,.15]). |

**Tag:** `paper-2026` → commit `648063d` (v3.0.4, June 22 2026) — the last commit
before submission; its `candidates.geojson` is byte-identical to today's
(MD5 `13db8a39fa39bd96f31d4e3da67a90f0`). Tag is local only (FIX_PROMPT_2 rule 7:
no pushes).

## Re-run protocol (what a row means)

The paper's statewide analysis predates `scripts/run_regions.py` and its exact
code was not preserved, so `scripts/validate_paper.py` reconstructs the paper's
documented protocol on the current pipeline (full rationale in the script
docstring):

- candidates at the **paper's density** (§2.2: 200 m spacing, >500-cell ≈
  0.05 km² accumulation threshold at 10 m), confined to mapped waterways (the
  shipped stream mask) — **not** the statewide runner's 1500 m / 2 km² config,
  which is a shortlist product, not the validation universe;
- one fixed ±0.03° bbox per trap, identical across phases;
- parameters wired by the **current** `wire_region_parameters` (the thing the
  fix phases change), scored by the shipped `compute_composite_score`,
  **default weights, no per-site calibration**;
- **recovered (PRIMARY)** = ≥1 post-hard-gate model candidate within **80 m**
  of the trap; this is not field confirmation of deployment feasibility.
  Distance is Euclidean in region UTM, a permissive stand-in for the paper's
  along-channel buffer. A secondary diagnostic count applies
  the paper's 0.70 threshold (composite ≥ 70 on the 0–100 scale) on top; that
  cutoff does not transfer to the pipeline's batch-relative MinMax composites
  (baseline shows in-buffer candidates at composite 40–60, never ≥70), so it
  is reported but not gated on. The primary metric was frozen from the phase-0
  baseline run alone, before any fix phase produced a number. Per-trap
  nearest/best diagnostics are stored in the run JSON so every miss is
  auditable.

Notes on comparability: with 200 m candidate spacing an 80 m buffer covers at
most ~80% of on-channel trap positions — the paper's own recall regime (80%,
81.5%) sits exactly at this geometric ceiling. Rows below compare like-for-like
(same harness, same bboxes); the paper row is the published anchor.

Stop rule (GPT_SOL_PROMPT standing rule 4): if a step's Waterkeepers recall
drops below the reconstructed phase-0 baseline of **21/27**, STOP, report, do
not merge. Dirichlet mean must stay ≥ 94.7±0.5 and
top-10 rank shifts vs `paper-2026` are recorded every row.

## Log

| Phase | Params live | Ellerbe recall (80 m) | Waterkeepers recall (80 m) | Dirichlet top-25 % | Top-10 rank shifts vs paper |
|---|---|---|---|---|---|
| paper-2026 baseline (published) | 11/27 (committed Durham file) | 8/10 | 22/27 | 94.7% | — |
| phase-0 harness baseline (pre-change re-run, 2026-07-10) | 21/27 statewide (Durham file unchanged: 11/27) | n/a (not re-runnable) | **21/27** primary (0/27 at the ≥70 diagnostic — see protocol) | 94.7% (exact) | 0 (data untouched) |
| phase 1 — SEMS + PAD-US + SWAP wired (2026-07-10) | 24/27 statewide NC | n/a | **21/27** primary (= baseline; identical miss list — impact params feed scores, not gates) | 94.7% (committed file untouched) | 0 (data untouched until the regen) |
| phase 2 — parcels statewide + HR4 flood + velocity provenance (2026-07-10) | 26/27 statewide NC | n/a | **21/27** primary (= baseline; ownership never emits gate-tripping 0.0, flood feeds scores only) | 94.7% (committed file untouched) | 0 (regen pending) |
| phase 3 — USACE-NWN navigability gate (2026-07-10) | 26/27 statewide NC | n/a | **21/27** primary (= baseline) — gate ACTIVE with zero recall cost: removed 19–55 near-navigable candidates in 5 coastal trap areas (Washington, Kinston, 3× Wilmington), no inland site touched | 94.7% (committed file untouched) | 0 (regen pending) |
| regen — single fix-pass-2 regeneration (2026-07-10) | **24/27** flagship + regions (constants: truthful CSO zero, bank slope, litter) | n/a | 21/27 (unchanged — the harness generates its own candidates; code identical to phase 3) | **96.5%** on the re-scored flagship (min 82.8, max 100, 9/10 >90%); paper file at tag: 94.7% | Spearman ρ 0.893 over the same 147 sites; 8/10 of paper top-10 stay top-10, 9/10 top-25; largest shift rank 9→38 |
| fix3 step 1 — native-resolution NC lidar bank cross-sections (2026-07-12) | bank slope live in NC harness; committed flagship remains 24/27 until the one final regen | n/a | **21/27** primary (= baseline; 0/27 at ≥70 diagnostic; zero run failures) | 96.5% (committed flagship intentionally untouched) | 0 (flagship intentionally untouched until single regen) |
| fix3 step 2 — exact-category municipal litter complaints (2026-07-13) | Charlotte/Raleigh/Greensboro feeds live in harness; committed flagship remains 24/27 until the one final regen | n/a | **21/27** primary (= baseline; 0/27 at ≥70 diagnostic; zero run failures) | 96.5% (committed flagship intentionally untouched) | 0 (flagship intentionally untouched until single regen) |
| fix3 step 3 — national EPA/PAD-US coverage lift (2026-07-13) | current/open TRI/SEMS/ECHO inputs + state PAD-US/service fallback live; committed outputs remain untouched until one final regen | n/a | **21/27** primary (= baseline; 0/27 at ≥70 diagnostic; zero run failures) | 96.5% (committed flagship intentionally untouched) | 0 (flagship intentionally untouched until single regen) |
| fix3 step 4a — remove assumed OSM tourism fallback (2026-07-13) | a failed real OSM query is now an explicit constant 0.0 fallback, never an assumed 1.0 or per-site retry; committed outputs still await the single regen | n/a | **21/27** primary (= baseline; 0/27 at ≥70 diagnostic; zero run failures) | 96.5% (committed flagship intentionally untouched) | 0 (flagship intentionally untouched until single regen) |
| fix3 step 4b — honest zero-region + official 3DEP export fallback (2026-07-13) | zero-candidate mask receipts now write correctly; exhausted WMS retries use the filtered official 1/3-arc-second ImageServer at ~10 m, with cached provenance | n/a | **21/27** primary (= baseline; 0/27 at ≥70 diagnostic; zero run failures) | 96.5% (committed flagship intentionally untouched) | 0 (flagship intentionally untouched until single regen) |
| fix3 step 4c — complete-source failure semantics (2026-07-16) | failed OSM road and NBI queries are explicit fallbacks; successful empty NBI is a real zero; cross-state PAD-US never scores a partial inventory | n/a | **21/27** primary (= baseline; 0/27 at ≥70 diagnostic; zero run failures) | 96.5% (committed flagship intentionally untouched) | 0 (flagship intentionally untouched until single regen) |

**Phase-0 baseline reading.** The reconstruction lands within ONE trap of the
published 22/27 despite a different candidate generator (Python pipeline at
paper density vs the June browser tool) — strong corroboration. The six misses
are auditable in `cache/validation/waterkeepers_recall_phase0-baseline.json`:
three geometric near-misses just past the buffer (Third Fork 97.5 m, South
Buffalo Trib 93.6 m — the 200 m-spacing coverage ceiling) and three small
urban tributaries the DEM/stream-mask pipeline doesn't carry a candidate onto
(Duffyfield Canal 194 m; Rankin St 630 m; McCumbers 586 m; Squash Branch
362 m) — the same miss mode the paper describes for its five misses. Rule-4
reference for phases 1–3: recall must stay ≥ 21/27 on this harness.
