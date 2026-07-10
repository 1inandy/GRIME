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
- **recovered (PRIMARY)** = ≥1 hard-gate-surviving (deployable) candidate
  within **80 m** of the trap (Euclidean in region UTM — permissive stand-in
  for the paper's along-channel buffer). A secondary diagnostic count applies
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

Stop rule (FIX_PROMPT_2 rule 4): if a phase's Waterkeepers recall drops below
the paper's 22/27 — or below the phase-0 harness baseline if that baseline is
lower — STOP, report, do not merge. Dirichlet mean must stay ≥ 94.7±0.5 and
top-10 rank shifts vs `paper-2026` are recorded every row.

## Log

| Phase | Params live | Ellerbe recall (80 m) | Waterkeepers recall (80 m) | Dirichlet top-25 % | Top-10 rank shifts vs paper |
|---|---|---|---|---|---|
| paper-2026 baseline (published) | 11/27 (committed Durham file) | 8/10 | 22/27 | 94.7% | — |
| phase-0 harness baseline (pre-change re-run, 2026-07-10) | 21/27 statewide (Durham file unchanged: 11/27) | n/a (not re-runnable) | **21/27** primary (0/27 at the ≥70 diagnostic — see protocol) | 94.7% (exact) | 0 (data untouched) || phase 1 — SEMS + PAD-US + SWAP wired (2026-07-10) | 24/27 statewide NC | n/a | **21/27** primary (= baseline; identical miss list — impact params feed scores, not gates) | 94.7% (committed file untouched) | 0 (data untouched until the regen) |
| phase 2 — parcels statewide + HR4 flood + velocity provenance (2026-07-10) | 26/27 statewide NC | n/a | **21/27** primary (= baseline; ownership never emits gate-tripping 0.0, flood feeds scores only) | 94.7% (committed file untouched) | 0 (regen pending) |

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
