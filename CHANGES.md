# Changes — Audit Fixes + First Live Pipeline Run

This branch brings GRIME from the pre-audit state up to a corrected, honest, and —
for Durham — real-data version. It folds in the fixes from a full code/math/data
audit (`AUDIT.md`, `FIXES.md`) and the first real end-to-end run of the hydrology
pipeline on live USGS/EPA/Census data.

**Scope vs `main`:** 33 files changed, +11,447 / −2,051 · 10 commits.

---

## Headline

- **Every checkable claim is now correct** — 27 parameters (was "28"), 89,518 places / 239 countries (was "108,772 / 240"), Bayesian weight optimization actually implemented, dead links and stale branding fixed.
- **The scoring model now does what the docs say** — the hydrology unit bug, the inert 30%-weight generation sub-score, and a duplicated impact signal are all fixed.
- **First real pipeline run** — `mock_data/candidates.geojson` is now generated from a live 1 m USGS 3DEP DEM + federal APIs for Durham/Ellerbe (was a synthetic placeholder).
- **The explorer is deterministic and honestly labeled** — rankings no longer reshuffle on every render; simulated values are marked as estimates.

---

## Integrity & claims
- `28 parameters` → **27** everywhere (code, README, docs, UI). [C5]
- `108,772 cities / 240 countries` → **89,518 places / 239 countries**; "cities" → "places"; procedural generation disclosed. [H1]
- Bayesian weight optimization is now genuinely **implemented** (`optimize_weights`, scikit-optimize), matching the README claim. [C6]
- Plastic-pollution stats re-sourced (Borrelle 2020 / Geyer 2017 / Eriksen 2014 / Meijer 2021); award links wired (were `href="#"`). [L3, L8]
- `gARB`/`gRIME` → `GRIME`; `/map` route added; `.gitignore` + `LICENSE` + `.env.example` added; stray `uvicorn` file removed. [L1, M6, H4, L2]

## Security & front-end bug fixes
- **XSS**: OSM way names and place names are escaped before DOM insertion. [X1]
- Stale-fetch race in `openCity` fixed — a slow city fetch can no longer overwrite another city's streams. [X2]
- Cluster/map click+hover listeners attached once in `initMap` (were re-registered on every back-navigation → N-fold fetches). [X3]

## Scientific correctness (Python)
- **Hydrology now runs in metres, not degrees** — the DEM is reprojected to UTM before pysheds, so catchment area (km²) and channel slope are physically real (were unit-broken; catchment clamped to 0.01 for every site). [C1]
- **Generation sub-score now varies per candidate** — it was identical across all sites (→ contributed nothing to ranking). Constant columns are now dropped and weights renormalized, with a per-run provenance log. [C2]
- Velocity slope follows the D8 flow grid downstream (was walking due south). [C1b / M7]
- "Strahler order" → `stream_order`, honestly documented as a confluence-degree heuristic (it was never true Strahler). [H3]
- Estuary and beach distances decorrelated (were the same variable, double-weighted). [H5]
- Strahler→width fallback bounded so it no longer explodes past the hard gate. [M1]
- Reproducible end-to-end scoring via `scripts/score_candidates.py` (closes the gap where no committed code reproduced the shipped scored file). [H2]
- Smaller fixes: Manning continuity now area-scaled; velocity fed through a peaked transport-favorability curve in Flow; runoff/`n_years`/`water_intake` docstrings corrected. [M4, M5, L4, L5, L6]

## Environmental justice
- EPA EJSCREEN was decommissioned (Feb 2025); the EJ index is now **reconstructed from live Census ACS** (percentile-ranked low-income + people-of-color demographic index, area-weighted over the catchment) and varies per site. [C4]
- `scripts/healthcheck.py` pings every federal endpoint and surfaces dead/degraded ones instead of silently swallowing them.

## Explorer (dashboard)
- **Deterministic scoring** — per-candidate RNG seeded by the site's own coordinates, so rankings are stable across re-renders and loading more streams only adds sites. [C3]
- Removed `Math.random()` from named Impact quantities.
- Upstream occlusion now discounts genuinely-downstream sites river-wide (`computeWayOrder` chains a river's OSM ways head→tail). [M7-JS]
- On-panel caveat that values are model estimates from OSM geometry, not live measurements. [H6]
- README §6.2 / ADR-3 rewritten to describe the real greedy + occlusion algorithm and to label the explorer as a separate heuristic. [M3]

## Infrastructure, docs & tests
- `model.json` — single source of truth for weights/gates/curves — plus `scripts/check_model.py` drift guard. [SSOT]
- 16-test pytest property suite (`tests/test_grime.py`). [L9]
- Env-driven CORS lock-down option (`GRIME_ALLOWED_ORIGIN`). [M8]
- MinMax failure-mode doc corrected; docs `<div>` imbalance fixed. [M2]
- Real **n=500 Dirichlet robustness** histogram + table (`scripts/robustness_report.py`, `dashboard/docs/exports/robustness_hist.png`).

## Live pipeline run (Durham / Ellerbe Creek)
First real execution of the `--live` path, which required fixing several never-run bugs: pysheds 0.5 API port, the `extract_river_network` mask bug (was producing a ~2 M-segment degenerate network), a pour-point snap so catchment reflects the real channel, the TIGER block-group layer (was querying school districts → population + EJ were broken), the NWIS peaks API rename, DEM retry/backoff, and region-level caching of whole-bbox lookups.
- **147 real candidate sites** on the Ellerbe/Eno network; catchment **2.0–115.9 km²**; composite **17.5–62.7**.

---

## Known limitations (read before the competition)

- **~10 of 27 parameters vary with live data; 17 are constant.** `impervious_pct` — a headline input (weight 0.20) — is a flat 35% fallback because NLCD imperviousness isn't served by the elevation API in use. EPA ECHO, USGS StreamStats, EJSCREEN, and Durham 311 are dead/unavailable, so their params fall back. The provenance log prints the exact split each run.
- **The explorer is still a simulation.** The real `candidates.geojson` is served by `/api/candidates`, but the interactive map doesn't call it yet — so grime.world still shows simulated estimates even for Durham. Wiring the explorer to the live pipeline is the next step.
- **Live output is not byte-reproducible** — it depends on external endpoints that flake/change day-to-day — though the scoring and Dirichlet draws are seeded, so identical inputs give identical output.
- **The paper has not been reconciled** to these changes — it still references 28 parameters, EJSCREEN, composite 87, true Strahler, 25 m spacing, and a 10,000-iteration Monte-Carlo. That reconciliation is outstanding.

## New files
`AUDIT.md`, `FIXES.md`, `FIX_PROMPT.md`, `CHANGES.md`, `LICENSE`, `model.json`,
`scripts/check_model.py`, `scripts/score_candidates.py`, `scripts/healthcheck.py`,
`scripts/robustness_report.py`, `tests/test_grime.py`,
`dashboard/docs/exports/robustness_hist.png`.
