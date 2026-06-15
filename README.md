# GRIME — Garbage River Interception and Modeling Engine
Video - https://www.youtube.com/watch?v=GsYtVGVTPWM

## 1. Executive Summary

GRIME is a multi-parameter optimization system that identifies optimal locations for deploying trash interception barriers ("nets") on urban waterways. It evaluates candidate sites across **27 geospatial parameters** organized into **6 parameter families**, producing **4 sub-scores** that combine into a single composite ranking per candidate location.

**What makes it technically interesting:**
- A two-level weighted scoring architecture (parameters → sub-scores → composite) that is both interpretable and tunable
- Hydrological pipeline built on real DEM data: pit-filling → depression-filling → flat resolution → D8 flow direction → flow accumulation → stream extraction
- Manning's equation applied to estimate flow velocity from DEM slope and channel geometry, with feasibility gates that eliminate sites where deployment is physically impossible
- Monte Carlo sensitivity analysis via Dirichlet-perturbed weight vectors to assess ranking robustness
- Three-phase candidate placement algorithm: spatial constraint satisfaction → full-parameter scoring → population-scaled risk-percentile filtering
- On-demand real waterway geometry from OpenStreetMap's Overpass API for 89,518 places across 239 countries

**Scope:** Site selection modeling and scoring. GRIME does not design the physical trap, predict trash composition, or model individual debris trajectories.

**Non-goals:** Real-time sensor integration, computer vision trash classification, trap mechanical design, economic cost optimization.

---

## Visualizations

*All surfaces generated in the Wolfram Language from real USGS elevation data and GRIME's scoring functions. See [full documentation](dashboard/docs/documentation.md) for derivations and code.*

### DEM Terrain — Durham, NC

<p align="center">
<img src="dashboard/docs/images/terrain_surface.png" width="700">
</p>

> 3D elevation surface from USGS 3DEP at 10m resolution. Stream valleys visible as low-elevation grooves define where GRIME extracts candidate net sites.

### Composite Score Surface

<p align="center">
<img src="dashboard/docs/images/composite_surface.png" width="700">
</p>

> The final composite score as a function of Generation (trash input) and Impact (downstream consequence), with Flow and Feasibility held constant. The diagonal ridge shows that high scores require *both* trash presence and downstream consequence — neither alone is sufficient.

### Feasibility Score — Width x Velocity

<p align="center">
<img src="dashboard/docs/images/feasibility_surface.png" width="700">
</p>

> Deployment feasibility as a function of channel width and flow velocity. The green plateau marks the sweet spot: narrow, moderate-velocity channels where nets can be spanned and anchored. Red zones are eliminated by hard gates.

### Manning's Equation — V(Slope, Roughness)

<p align="center">
<img src="dashboard/docs/images/manning_surface.png" width="700">
</p>

> Flow velocity estimated from Manning's equation across slope and roughness parameter space. Steep, smooth channels (high slope, low roughness) produce dangerous velocities; flat, rough channels produce stagnant conditions.

### Environmental Justice Index

<p align="center">
<img src="dashboard/docs/images/ej_surface.png" width="700">
</p>

> Environmental justice burden across a synthetic metro region. Peaks identify overburdened communities where trash interception has the highest equity value — GRIME weights these areas higher in the Impact sub-score.

### Dirichlet Sensitivity — Weight Perturbations

<p align="center">
<img src="dashboard/docs/images/dirichlet_simplex.png" width="600">
</p>

> 500 weight vectors sampled from a Dirichlet distribution (κ=10) projected onto a ternary diagram. The red dot is the baseline. GRIME recomputes rankings under each perturbation to test whether top-ranked sites are robust to weight assumptions.

---

## 2. Problem Statement

### The problem

Urban waterways accumulate trash from stormwater runoff, illegal dumping, combined sewer overflows, and bridge crossings. Deploying interception devices (nets, booms, trash traps) requires choosing locations that maximize debris captured per device while remaining physically feasible to install and maintain.

### Why it is hard

A naive approach — placing traps at the largest rivers — fails because:
1. **Large rivers are too wide** (>30m) for stationary nets; debris passes around or damages the device
2. **High-traffic waterways** (navigable canals, shipping channels) cannot be obstructed
3. **Upstream tributaries** with high impervious surface and population density generate more trash per unit area than rural mainstems
4. **Downstream impact varies**: a trap upstream of a drinking water intake has orders of magnitude more public health value than one upstream of an industrial canal
5. **Physical feasibility** (road access, bank slope, flow velocity, land ownership) eliminates many otherwise-optimal locations

### Constraints and assumptions

- All data sources must be free and low-friction (EPA/USGS/OSM are keyless; Census ACS uses a free, instant API key — see `.env.example`)
- The model assumes stationary barrier-style traps deployable in channels ≤30m wide (~100 ft)
- Scoring weights are set by informed heuristic and literature, not supervised learning (no ground-truth dataset of "correct" trap placements exists at scale)
- The system must produce results in <15 seconds per city for interactive demo use

---

## 3. System Overview

### Architecture

```mermaid
graph TD
    A[DEM Raster - USGS 3DEP] --> B[Hydrological Conditioning - pysheds]
    B --> C[Flow Direction - D8 Algorithm]
    C --> D[Flow Accumulation]
    D --> E[Stream Network Extraction]
    E --> F[Candidate Site Generation]

    G[EPA APIs - TRI ECHO SDWIS FRS] --> H[Parameter Computation - 27 params x 6 families]
    I[USGS APIs - NWIS StreamStats PAD-US] --> H
    J[Census APIs - ACS TIGER incl. EJ index] --> H
    K[OSM - Overpass API] --> H
    F --> H

    H --> L[Hard Gate Filtering]
    L --> M[MinMax Normalization]
    M --> N[Weighted Sub-scores x4]
    N --> O[Composite Score]
    O --> P[Risk-Percentile Filtering]
    P --> Q[Ranked Deployment Sites]

    Q --> R[FastAPI Backend]
    R --> S[Mapbox GL JS Dashboard]
    K --> S
```

### Subsystems

| Subsystem | Location | Purpose |
|-----------|----------|---------|
| DEM Pipeline | `core/pipeline.py` | Fetch elevation data, extract stream network, generate candidate points |
| Generation Params | `core/generation.py` | Compute trash generation indicators (population, land use, industrial sources) |
| Flow Params | `core/flow.py` | Compute hydraulic transport parameters (discharge, velocity, flood frequency) |
| Impact Params | `core/impact.py` | Compute downstream consequence indicators (drinking water, EJ, protected areas) |
| Feasibility Params | `core/feasibility.py` | Compute deployment constraint parameters (road access, channel width, slope) |
| Scoring Engine | `core/scoring.py` | Normalize, weight, composite, sensitivity analysis |
| API Server | `api/main.py` | REST + WebSocket endpoints serving scored GeoJSON |
| Dashboard | `dashboard/index.html` | Interactive map with on-demand OSM waterway fetching and client-side scoring |
| Places Database | `mock_data/places.json` | 89,518 place records across 239 countries (~6MB compact JSON) |

> **About the places database.** The 89,518 records come from `geonamescache` (real
> cities/towns with population ≥ 100) plus procedurally generated nearby townships
> (`scripts/generate_mock.py`, seeded for reproducibility). They exist only to give
> the explorer a global set of clickable starting points — waterway geometry and
> all scoring are computed live from OpenStreetMap at click time, so the procedural
> names never enter a score. We say "places," not "cities," for this reason.

### Data flow

Two execution modes exist:

**Mode 1 — Full Python pipeline (research/validation):**
DEM fetch → pysheds hydrology → stream extraction → candidate generation → API-based parameter computation → composite scoring → GeoJSON output

**Mode 2 — Dashboard on-demand (demo/interactive):**
User clicks city → Overpass API returns real waterway geometry → client-side JS generates candidate positions with spatial constraints → client-side scoring using simplified parameter model → Mapbox GL renders results

Mode 2 exists because Mode 1 takes 3–5 minutes per watershed and requires installing pysheds (which has C dependencies that fail on some Windows machines). Mode 2 runs in <5 seconds anywhere with a browser.

---

## 4. Core Technical Ideas

### 4.1 Two-level weighted scoring

The 27 raw parameters are not directly comparable (population density in persons/km² vs flow velocity in m/s vs a binary land ownership flag). The system handles this through two-level aggregation:

1. **Parameter level:** Each raw parameter is MinMax-normalized to [0, 1] independently within the candidate set, then multiplied by its within-family weight. The weighted sum produces a sub-score in [0, 100].

2. **Sub-score level:** The four sub-scores are combined via a second set of weights into the composite score in [0, 100].

This two-level structure has a specific advantage: it makes the model **interpretable at the sub-score level**. A judge or engineer can look at a candidate and immediately see "high generation, low feasibility" without needing to parse 28 individual numbers.

### 4.2 Hard gates vs soft scoring

Some parameters act as binary disqualifiers rather than continuous scores. A channel wider than 50m cannot hold a net regardless of how much trash flows through it. These are implemented as **hard gates** that remove candidates before scoring, separate from the **soft scoring** that ranks survivors:

| Gate | Condition | Rationale |
|------|-----------|-----------|
| Velocity | V > 3.0 m/s | Trap will be damaged or torn loose |
| Channel width | W > 50m or W < 0.5m | Too wide to span or too narrow for meaningful accumulation |
| Land ownership | Confirmed private, no permission | Legal barrier to deployment |

### 4.3 Placement as constraint satisfaction + optimization

Candidate placement is not random scatter. It is a three-phase algorithm:

1. **Constraint satisfaction:** Generate all positions that pass spatial, width, and traffic constraints
2. **Full scoring:** Evaluate every surviving position on the composite model
3. **Risk-percentile selection:** Keep only the top N% by score, where N scales with city population

This separates "can we physically put a net here?" (phase 1) from "should we?" (phases 2–3).

### 4.4 Population-scaled risk thresholds

A city of 20 million people needs more nets than a town of 10,000, but not linearly more. The risk percentile threshold scales in steps:

| Population | Percentile kept | Rationale |
|-----------|----------------|-----------|
| >10M | Top 35% | Mega-cities have extensive waterway networks; more sites are genuinely high-risk |
| >1M | Top 30% | Large cities still have substantial catchments |
| >100K | Top 25% | Mid-size cities, moderate network complexity |
| <100K | Top 20% | Small towns, fewer waterways, tighter selection |

A minimum floor of 5 deployed sites ensures the model always produces enough output to demonstrate ranking behavior.

---

## 5. Mathematical Foundations

### 5.1 Composite scoring function

Let **x** ∈ ℝ²⁷ be the raw parameter vector for a candidate site. The composite score S(**x**) is:

```
S(x) = Σ(k=1..4) ωk · Gk(x)
```

where ω = [0.30, 0.25, 0.30, 0.15] are the sub-score weights and each sub-score Gk is:

```
Gk(x) = 100 · Σ(j ∈ Fk) wj · x̂j
```

where Fk is the set of parameter indices belonging to family k, wj is the within-family weight for parameter j (renormalized to sum to 1 after filtering unavailable parameters), and x̂j is the MinMax-normalized value:

```
x̂j = (xj - min(xj)) / (max(xj) - min(xj))
```

For distance-based parameters where lower is better (estuary distance, beach distance), the normalization is inverted: x̂j = 1 − x̂j.

**Important implementation detail:** Normalization is computed across the candidate set, not against a global reference. This means scores are relative rankings, not absolute measures. A score of 80 means "top of this candidate pool," not "80% of some theoretical maximum."

### 5.2 Manning's velocity equation

Flow velocity at a candidate site is estimated via Manning's equation:

```
V = (1/n) · R^(2/3) · S^(1/2)
```

where:
- **n** is Manning's roughness coefficient (dimensionless), selected by channel type:
  - Clean straight: 0.030
  - Winding with pools (typical urban creek): 0.040
  - Sluggish, weedy: 0.070
  - Urban concrete-lined: 0.015
  - (Source: Chow, V.T., 1959, *Open-Channel Hydraulics*)
- **R** is the hydraulic radius (m) = A_cross / P_wetted, approximated as rectangular channel: R = (W × D) / (W + 2D), where depth D ≈ 0.3W (bankfull approximation)
- **S** is the channel slope (dimensionless), computed from DEM as elevation difference over a 100m reach: S = (Z_here − Z_downstream) / 100, clamped to minimum 0.0001

A continuity estimate is blended in: V_continuity = Q_site / A_cross, where Q_site is the gauge discharge **area-scaled to the candidate's own catchment** (M4: site-specific, now that catchment area is real km²) and converted to m³/s. The final velocity is the geometric mean of the Manning and continuity estimates (a soft blend, not a fully independent measurement):

```
V_final = sqrt(V_Manning · V_continuity)
```

This hedges against errors in either the DEM slope (which can be noisy at 10m resolution) or the channel geometry assumption (rectangular approximation).

### 5.3 Velocity feasibility function

The velocity feasibility score is a piecewise function mapping velocity to a deployment viability multiplier:

```
f(V) =
  0.3   if V < 0.05 m/s     (stagnant — debris doesn't concentrate)
  0.7   if 0.05 ≤ V < 0.30  (slow but workable)
  1.0   if 0.30 ≤ V ≤ 1.50  (optimal interception range)
  0.5   if 1.50 < V ≤ 2.50  (fast — heavy anchoring needed)
  0.1   if V > 2.50          (too fast — trap damage likely)
```

Sites with V > 3.0 m/s are removed entirely by the hard gate before scoring.

### 5.4 Runoff coefficient estimation

The rational method runoff coefficient C is estimated from impervious surface percentage via a linear model:

```
C = 0.05 + 0.009 · I
```

where I is the NLCD impervious surface percentage [0, 100]. This yields C ∈ [0.05, 0.95], ranging from forest (≈5% runoff) to fully paved (≈95% runoff). This is the same linearization used in the WaterGate methodology.

### 5.5 Inverse distance scoring

Several parameters (CSO proximity, Superfund proximity) use an inverse-distance kernel to compute influence from point sources:

```
score = Σ(i=1..N) 1 / (1 + (di / h)²)
```

where di is the Euclidean distance (in UTM meters) from the candidate to source i, and h is the half-decay distance (500m default). This is a Cauchy kernel that gives full weight at distance 0 and half weight at distance h.

### 5.6 Drinking water intake scoring

Proximity to downstream drinking water intakes uses an exponential decay:

```
score = Σ(i=1..N) exp(-di / 10)
```

where di is the distance in km. Intakes within 10km get weight ≈0.37, within 5km ≈0.61, within 1km ≈0.90. Intakes beyond 50km are ignored.

### 5.7 Sensitivity analysis via Dirichlet perturbation

To assess whether the top-ranked sites are robust to weight uncertainty, the system performs Monte Carlo sensitivity analysis:

1. Sample N = 50 perturbed weight vectors from a Dirichlet distribution: ω' ~ Dir(α), where α = 10 × [0.30, 0.25, 0.30, 0.15]
2. The α scaling factor (×10) controls perturbation magnitude — higher α concentrates samples closer to the baseline weights
3. For each perturbed weight vector, recompute composite scores and record which sites appear in the top 5
4. The **robustness percentage** for each site is: (count of times in top 5) / N × 100%

A site with robustness > 80% is ranked highly regardless of reasonable weight changes. A site at 30% is sensitive to weight assumptions.

### 5.8 Haversine distance (placement spacing)

The minimum-spacing constraint uses the haversine formula for geodesic distance:

```
a = sin²(Δφ/2) + cos(φ₁) · cos(φ₂) · sin²(Δλ/2)
d = 2R · atan2(√a, √(1−a))
```

where R = 6,371,000 m. This is used instead of Euclidean distance because the candidate set can span several kilometers, where flat-earth approximation introduces meaningful error at high latitudes.

### 5.9 Environmental justice index (reconstructed from Census ACS)

> **Why this changed.** EPA removed EJSCREEN — the tool, the data downloads, and the
> `ejscreenRESTbroker.aspx` ArcGIS server — on **2025-02-05**, and the White House
> removed CEJST on **2025-01-22**; neither has an official live replacement (the
> suit to restore EJSCREEN was dismissed on standing, 2026-03-13). Because EJSCREEN's
> demographic index *is* percentile-ranked ACS demographics, GRIME reconstructs it
> directly from the **live Census ACS 5-year API** (`core.impact.get_ej_index`),
> which we control, instead of calling a dead endpoint. Sources:
> [EELP tracker](https://eelp.law.harvard.edu/tracker/epa-added-environmental-health-indicators-to-ejscreen/),
> [EDGI](https://envirodatagov.org/epa-removes-ejscreen-from-its-website/),
> [CEJST removal](https://eelp.law.harvard.edu/tracker/ceqs-climate-economic-justice-screening-tool-removed/),
> reconstruction mirror [screening-tools.com](https://screening-tools.com/epa-ejscreen).

We compute EJSCREEN's two-component **core demographic index** per block group:

```
demographic_index_bg = mean( pct_rank(% low-income) , pct_rank(% people of color) )
EJ = area-weighted mean of demographic_index_bg over the catchment        ∈ [0, 1]
```

- **% low-income** — ACS table `C17002` (income-to-poverty ratio < 2.0).
- **% people of color** — `1 − (non-Hispanic white / total)` from `B03002`.
- Percentile-ranked within the county, then area-weighted over the candidate's
  catchment — so the index **varies across catchments** (no longer a constant).

The six-component *supplemental* index (adding limited-English `C16002`,
< high-school `B15003`, under-5 and over-64 `B01001`) is a documented extension;
the two-component core index above is EPA's headline definition. The deprecated
`get_ejscreen_index` is retained only so old callers degrade to a neutral 0.5.

---

## 6. Algorithms

### 6.1 DEM Hydrological Conditioning Pipeline

**Purpose:** Convert raw elevation data into a hydrologically consistent surface from which flow direction and stream networks can be extracted.

**Input:** DEM raster from USGS 3DEP (10m resolution)

**Output:** Flow direction grid, flow accumulation grid, stream network GeoJSON

**Steps:**

```
FUNCTION condition_dem(dem):
    pit_filled    ← fill_pits(dem)              // remove single-cell sinks
    flooded       ← fill_depressions(pit_filled) // fill multi-cell depressions
    inflated      ← resolve_flats(flooded)       // assign gradient to flat areas
    flow_dir      ← D8_flowdir(inflated)         // each cell → 1 of 8 neighbors
    accumulation  ← flow_accumulation(flow_dir)  // count upstream cells per cell
    RETURN flow_dir, accumulation
```

**Stream extraction:** Cells where accumulation exceeds a threshold (default 500 cells = 500 × 10m × 10m = 0.05 km²) are classified as stream cells. Connected stream cells are vectorized into LineString geometries.

**Complexity:** O(n) for each step where n is the number of DEM cells. For the Ellerbe Creek bbox at 10m resolution: approximately 3000 × 1500 = 4.5M cells. Total conditioning time: ~30–60 seconds.

**Why pysheds:** It operates entirely in-memory on NumPy arrays without requiring ArcGIS or GRASS GIS. The D8 algorithm assigns each cell exactly one of 8 cardinal/diagonal flow directions based on steepest descent, which is the standard approach for stream extraction in computational hydrology.

**Known limitation:** The 10m DEM resolution means channels narrower than ~10m may not be resolved. This is acceptable because channels that narrow are well within the deployable range and will be identified by other means (NHD, OSM).

### 6.2 Candidate Placement Algorithm (Client-side)

**Purpose:** Given a set of waterway geometries and city metadata, produce a set of spatially valid, risk-ranked candidate sites for trap deployment.

**Input:** Array of stream geometries (from Overpass API), city population, country code

**Output:** Ranked array of candidate objects with scores and parameters

> **This is a separate, honestly-labeled heuristic, not the Python model.** The
> explorer scores client-side with clamped closed-form formulas (no MinMax), using
> OSM-estimated widths and **deterministic per-candidate values seeded by each
> site's own coordinates** (so rankings are stable across pan/zoom — no
> `Math.random()` for any named quantity). It is a fast interactive approximation;
> the Python pipeline (§5–6.1) is the real 27-parameter MinMax model.

```
FUNCTION generate_candidates(streams, pop, country):
    // ── Phase 1: Constraint satisfaction ──
    SAME_STREAM_SPACE ← 500m   // wide gaps along one waterway (occlusion makes
    CROSS_STREAM_SPACE ← 300m  // a 2nd net 500m downstream largely redundant)
    MAX_WIDTH ← 30m
    placed ← []
    valid_positions ← []

    FOR EACH stream IN streams:
        IF stream.width > MAX_WIDTH: CONTINUE
        cap ← waterway_capacity(stream)
        count_on_stream ← 0
        dist_since_last ← SAME_STREAM_SPACE

        FOR EACH point IN stream.coords:
            dist_since_last += haversine(previous_point, point)
            IF dist_since_last < SAME_STREAM_SPACE: CONTINUE
            IF any p in placed where haversine(p, point) < CROSS_STREAM_SPACE: CONTINUE
            IF count_on_stream >= cap: CONTINUE
            placed.add(point); count_on_stream++; dist_since_last ← 0
            valid_positions.add(point with metadata)

    // ── Phase 2: Score every valid position (deterministic, coord-seeded) ──
    FOR EACH pos IN valid_positions:
        pr ← seeded_rng(hash(pos.lat, pos.lon) + seed)   // STABLE per site
        compute clamped sub-scores + composite using pr() (no Math.random)

    // ── Phase 3: Greedy selection with multiplicative upstream occlusion ──
    // NOT a static top-N%. Each time we place a net we discount every still-unplaced
    // candidate that is DOWNSTREAM on the same river (chained across OSM ways by
    // computeWayOrder, ordered by (wayOrder, coordIdx)) — a net already catches most
    // debris, so a downstream net only sees the passthrough (1 − η)^k of trash.
    η ← 0.65                                  // catch efficiency per net
    target ← min(250, max(5, ceil(len * population_percentile)))
    WHILE selected < target:
        sort remaining by current composite; best ← pop highest; selected.add(best)
        FOR EACH downstream c on best's river:
            c.generation ← c.raw_generation × (1 − η)^(nets_upstream_on_river)
            recompute c.composite
    RETURN selected
```

**Complexity:** Phase 1 is O(n × m); Phase 2 is O(k); the greedy Phase 3 is
O(target × k). In practice k < 250 and the whole function runs in <100 ms.
Upstream occlusion (η = 0.65 compounding) is the original part of the system — it
spreads nets across waterways instead of clustering them on the single highest-trash
reach. See §6.3 for the robustness analysis.

### 6.3 Sensitivity Analysis (Dirichlet Monte Carlo)

```
FUNCTION sensitivity_analysis(candidates, n_perturbations=50):
    baseline ← compute_composite_score(candidates)
    top5_counts ← zeros(len(candidates))

    REPEAT n_perturbations TIMES:
        α ← [3.0, 2.5, 3.0, 1.5]
        ω' ← sample_dirichlet(α)
        composite' ← ω'[0]·gen + ω'[1]·flow + ω'[2]·impact + ω'[3]·feas
        top5 ← indices of 5 highest composite'
        top5_counts[top5] += 1

    robustness ← top5_counts / n_perturbations × 100
    RETURN baseline with robustness column
```

**Real results (reproducible).** `python3 scripts/robustness_report.py` runs this at
**n = 500** on the scored candidates and writes an actual rank-stability histogram to
`dashboard/docs/exports/robustness_hist.png` (not a synthetic mock). On the shipped
Ellerbe candidate set, the top 4 sites retain a top-5 position in **81–99 %** of
perturbed-weight runs, so the leading recommendations are robust to reasonable weight
changes while lower-ranked sites are weight-sensitive (as expected).

**Field-validation candidates (roadmap).** The top sites the model flags — South
Ellerbe (composite 68, 99 % robust) and the upper Sandy Creek reaches — are the
natural targets for ground-truthing against photographed accumulation points; that
validation is the next step before any deployment claim.

### 6.4 Bayesian Weight Optimization (Optional Enhancement)

When ground-truth trap locations are available, weights can be optimized via scikit-optimize:

```
FUNCTION optimize_weights(candidates, known_good_sites):
    FUNCTION objective(weights):
        w ← normalize(weights)
        scored ← recompute with w
        penalty ← sum of ranks of known_good_sites in scored
        RETURN penalty

    search_space ← [Real(0.05, 0.60)] × 4
    result ← gp_minimize(objective, search_space, n_calls=50)
    RETURN normalize(result.x)
```

**Status:** Implemented in `core/scoring.py` as `optimize_weights(candidates_df, known_good_indices)` — a Gaussian-process `gp_minimize` over the four sub-score weights whose objective minimizes the mean rank of known-good sites. Not yet run against real ground-truth trap locations (no validated dataset yet), so the shipped weights remain the heuristic defaults.

---

## 7. Architecture and Design Decisions

### ADR-1: Two-level scoring instead of flat weighted sum

**Decision:** Aggregate 27 parameters into 4 sub-scores, then combine sub-scores into a composite.

**Context:** A flat 28-weight sum is opaque — changing one weight has a non-obvious effect.

**Alternatives considered:** (1) Flat weighted sum. (2) PCA dimensionality reduction. (3) Random forest classifier.

**Chosen approach:** Two-level weighted sum. Sub-scores map to real questions ("how much trash?", "how does it move?", "does it matter?", "can we deploy?").

**Consequences:** Interpretable and tunable, but assumes linear parameter contributions within each family. Non-linear interactions are not captured.

### ADR-2: MinMax normalization instead of Z-score or rank

**Decision:** Use MinMax scaling to [0, 1] per parameter across the candidate set.

**Alternatives considered:** Z-score, percentile rank, log-transform + MinMax.

**Chosen approach:** MinMax. Simple, bounded, interpretable.

**Consequences:** Outliers dominate. A single candidate with extremely high population density compresses all others toward 0 on that parameter. Acknowledged limitation.

### ADR-3: Client-side scoring in the dashboard

**Decision:** The dashboard computes scores in JavaScript, not by calling the Python backend.

**Context:** pysheds/rasterio have C dependencies that fail on Windows. Dashboard must work by opening one HTML file.

**Chosen approach:** A **separate, simplified JS heuristic** — not a parity port of the Python model. It uses clamped closed-form formulas (no MinMax normalization), OSM-estimated widths, and deterministic per-candidate values seeded by each site's own coordinates (stable across re-render). It shares the *framing* — four sub-scores and the greedy upstream-occlusion placement — but the numbers are model estimates from OSM geometry, not the Python pipeline's API-driven, MinMax-normalized output. The explorer UI labels them as such, and the Python pipeline remains the source of truth for real scoring.

**Consequences:** Dashboard scores are approximate. Python pipeline is the authoritative scoring implementation.

### ADR-4: OpenStreetMap Overpass for waterway geometry

**Decision:** Fetch real waterway geometry from the Overpass API on each city click.

**Alternatives considered:** Pre-generated GeoJSON per city (storage), procedural random-walk rivers (alignment), NHD (US-only).

**Chosen approach:** Overpass API with 12s timeout and procedural fallback.

**Consequences:** Requires internet. Coverage varies globally. Fallback doesn't align with terrain.

---

## 8. Data Model and Schemas

### Candidate site (GeoJSON Feature)

```json
{
  "type": "Feature",
  "geometry": {"type": "Point", "coordinates": [-78.898, 35.994]},
  "properties": {
    "id": 0,
    "city": "durham",
    "city_name": "Durham, NC",
    "stream_name": "Ellerbe Creek",
    "composite_score": 47.08,
    "generation_score": 42.5,
    "flow_score": 38.1,
    "impact_score": 35.2,
    "feasibility_score": 82.0,
    "population_density": 1424.3,
    "impervious_pct": 51.8,
    "usgs_mean_q_cfs": 37.0,
    "flow_velocity_ms": 1.377,
    "stream_order": 5,
    "catchment_area_km2": 63.5,
    "channel_width_m": 13.1,
    "ej_index": 0.595,
    "road_access_m": 366.4,
    "bank_slope_deg": 19.6,
    "robustness_pct": 72.6,
    "rank": 1
  }
}
```

### Places database record (compact JSON)

```json
{"n":"Durham","c":"US","p":278993,"la":35.994,"lo":-78.8986}
```

Fields: **n**=name, **c**=ISO country code, **p**=population, **la**=latitude, **lo**=longitude.

### Parameter taxonomy (all 28)

| # | Parameter | Unit | Family | Default Weight | Data Source |
|---|-----------|------|--------|---------------|-------------|
| 1 | Population density | persons/km² | Generation | 0.18 | US Census ACS |
| 2 | Impervious surface % | % | Generation | 0.20 | NLCD 2021 |
| 3 | Road density | km/km² | Generation | 0.10 | Census TIGER / OSMnx |
| 4 | EPA TRI facility count | facilities/km² | Generation | 0.18 | EPA TRI API |
| 5 | NPDES discharge points | count | Generation | 0.12 | EPA ECHO API |
| 6 | CSO/storm outfall density | points/km² | Generation | 0.12 | EPA ECHO |
| 7 | Litter complaint density | reports/km² | Generation | 0.10 | Durham 311 / local GIS |
| 8 | USGS mean discharge Q | cfs | Flow | 0.22 | USGS NWIS |
| 9 | Flow velocity | m/s | Flow | 0.16 | Manning's eq from DEM |
| 10 | Stream order (confluence-degree heuristic) | ordinal | Flow | 0.14 | Network topology (H3: not true Strahler) |
| 11 | Catchment area A | km² | Flow | 0.18 | pysheds DEM analysis |
| 12 | Flood return period Q10 | cfs | Flow | 0.14 | USGS StreamStats |
| 13 | Seasonal flow variability | CV | Flow | 0.10 | USGS NWIS annual stats |
| 14 | Runoff coefficient C | dimensionless | Flow | 0.06 | Linear impervious→C (C=0.05+0.009·I) |
| 15 | Drinking water intake proximity | exp(-d/10) | Impact | 0.22 | EPA SDWIS / ECHO |
| 16 | Protected area proximity | score | Impact | 0.16 | USGS PAD-US |
| 17 | Environmental justice index | [0,1] | Impact | 0.18 | Census ACS (EJSCREEN demographic-index reconstruction) |
| 18 | Ocean/estuary proximity | km (inverted) | Impact | 0.14 | NHD terminus |
| 19 | Recreational beach proximity | km (inverted) | Impact | 0.12 | EPA BEACH Program |
| 20 | Tourism/recreation value | amenity count | Impact | 0.10 | OSM amenity density |
| 21 | Superfund site proximity | score | Impact | 0.08 | EPA FRS/CERCLIS |
| 22 | Road access distance | m | Feasibility | 0.25 | OSMnx routing |
| 23 | Channel width | m | Feasibility | 0.20 | NHD VAA + NBI span |
| 24 | Flow velocity (penalty) | m/s | Feasibility | 0.20 | Manning's eq |
| 25 | Land ownership | binary | Feasibility | 0.15 | USGS PAD-US |
| 26 | Bank slope stability | degrees | Feasibility | 0.10 | DEM gradient |
| 27 | Bridge/structure proximity | bonus | Feasibility | 0.10 | FHWA NBI |

---

## 9. Codebase Structure

```
grime/
├── core/                       # Python scoring pipeline (core deliverable)
│   ├── __init__.py             # Constants, safe_call(), helpers
│   ├── pipeline.py             # DEM → pysheds → stream extraction → candidates
│   ├── generation.py           # 7 trash generation parameters + API integrations
│   ├── flow.py                 # 7 flow parameters, Manning's equation, USGS data
│   ├── impact.py               # 7 downstream impact parameters, EJ scoring
│   ├── feasibility.py          # 6 deployment feasibility parameters, hard gates
│   └── scoring.py              # Normalization, weighting, composite, sensitivity
├── api/
│   └── main.py                 # FastAPI: REST + WebSocket + static serving
├── dashboard/
│   └── index.html              # Mapbox map, Overpass integration, client-side scoring
├── mock_data/
│   └── places.json             # 89,518 places, 239 countries (6MB)
├── scripts/
│   └── generate_mock.py        # Builds places.json from geonamescache
├── notebooks/
│   └── validate_pipeline.ipynb # Pipeline validation notebook
├── requirements.txt
├── start.sh
└── README.md
```

**Where critical logic lives:**

| Logic | File | Function |
|-------|------|----------|
| Composite scoring formula | `core/scoring.py` | `compute_composite_score()` |
| Manning's velocity | `core/flow.py` | `compute_flow_velocity()` |
| Hard gate filtering | `core/scoring.py` | `apply_hard_gates()` |
| Sensitivity analysis | `core/scoring.py` | `sensitivity_analysis()` |
| Client-side placement | `dashboard/index.html` | `generateCandidates()` |
| Overpass waterway fetch | `dashboard/index.html` | `fetchRealStreams()` |
| Sub-score normalization | `core/scoring.py` | `compute_subscore()` |

---

## 10. Execution Flow

### Python pipeline (research mode)

1. User runs: `python -m core.pipeline --bbox "-79.05,35.90,-78.75,36.05"`
2. `py3dep.get_map('DEM', bbox, resolution=10)` fetches 3DEP raster
3. pysheds: fill_pits → fill_depressions → resolve_flats → flowdir → accumulation
4. `extract_river_network(threshold=500)` → stream GeoJSON
5. `generate_candidates(spacing=200m)` → candidate points along streams
6. For each candidate: compute pixel coords, elevation, catchment area from DEM
7. Output: `mock_data/candidates.geojson`

### Dashboard (demo mode)

1. Browser opens `dashboard/index.html`
2. Fetches `places.json` (6MB) → parses 89,518 places
3. Mapbox GL JS renders clustered city markers
4. User clicks city → `openCity(idx)` fires
5. POST to Overpass API → returns waterway geometry as JSON
6. `fetchRealStreams()` filters: tidal=no, width≤30m, top 15–25 by length
7. `generateCandidates()` runs 3-phase placement + scoring
8. Mapbox renders: stream lines (cyan) + candidate dots (color-coded by score)
9. User clicks candidate → sidebar shows score breakdown + parameters

---

## 11. APIs and Interfaces

### REST endpoints

| Method | Path | Parameters | Response |
|--------|------|-----------|----------|
| GET | `/api/candidates` | `?min_score=N` `?top_n=N` `?subscore=field` | GeoJSON FeatureCollection |
| GET | `/api/candidates/{id}` | — | Score breakdown with 4 sub-score parameter trees |
| GET | `/api/weights` | — | All parameter and sub-score weights |
| GET | `/api/stats` | — | Count, score range, mean, top 5 |
| GET | `/map` | — | Serves dashboard HTML |
| WS | `/ws` | — | Real-time candidate updates |

### External APIs consumed

| API | Auth | Rate Limit | Timeout | Fallback |
|-----|------|-----------|---------|----------|
| Overpass API | None | Informal | 12s | Procedural generation |
| USGS NWIS | None | None published | 30s | Hardcoded Ellerbe Creek stats |
| EPA ECHO | None | None published | 30s | Empty GeoDataFrame |
| ~~EPA EJSCREEN~~ (decommissioned 2025-02-05) | — | — | — | EJ index reconstructed from Census ACS instead (see §5.9) |
| Census ACS | None | None published | 30s | Durham average (500/km²) |
| USGS 3DEP | None | None published | 60s | Fatal — no fallback |

Every external API call is wrapped in `safe_call()` with a default fallback value.

---

## 12. Configuration

| Setting | Location | Default | Notes |
|---------|----------|---------|-------|
| `MAPBOX_TOKEN` | `dashboard/config.js` (gitignored) and `.env` | Placeholder | **Must replace** — free at mapbox.com. Copy `dashboard/config.example.js` → `dashboard/config.js`, and `.env.example` → `.env` |
| `ELLERBE_BBOX` | `core/__init__.py` | `(-79.05, 35.90, -78.75, 36.05)` | Ellerbe Creek watershed |
| `ELLERBE_GAUGE` | `core/__init__.py` | `"02086849"` | USGS gauge site number |
| `UTM_CRS` | `core/__init__.py` | `"EPSG:32617"` | UTM zone 17N (Durham, NC) |
| DEM resolution | `core/pipeline.py` | 10m | Passed to py3dep |
| Accumulation threshold | `core/pipeline.py` | 500 cells | Stream extraction sensitivity |
| Candidate spacing | `core/pipeline.py` | 200m | Along-stream distance |
| Composite weights | `core/scoring.py` | [0.30, 0.25, 0.30, 0.15] | Gen, Flow, Impact, Feas |
| Min spacing (dashboard) | `dashboard/index.html` | 120m | Haversine between nets |
| Max width (dashboard) | `dashboard/index.html` | 30m | Skip channels wider |
| Risk percentile | `dashboard/index.html` | 20–35% | Population-scaled |
| Min deploy floor | `dashboard/index.html` | 5 | Always at least this many |

---

## 13. Installation and Setup

### First-time setup (macOS / Linux)

macOS only ships `python3` (no bare `python`). The cleanest setup is a venv, which gives you a local `python` + `pip` that won't collide with system Python or other projects.

```bash
cd ~/Downloads/GRIME

# 1. Create + activate the venv (you'll see (.venv) in your prompt after activation)
python3 -m venv .venv
source .venv/bin/activate

# 2. Install web-server deps (FastAPI, uvicorn, websockets, etc.)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 3. Configure secrets (both files are gitignored)
cp dashboard/config.example.js dashboard/config.js   # then paste your Mapbox pk.* token
cp .env.example .env                                  # then set MAPBOX_TOKEN=...

# 4. Generate the places database (one-time)
python scripts/generate_mock.py

# 5. Start the API (this also serves the dashboard)
python -m uvicorn api.main:app --reload --port 8000
```

Open **http://localhost:8000/** — landing page. **/explore** for the map app. **/api/swagger** for API docs.

### Run again next session

Once setup is done, every subsequent run is just two commands:

```bash
cd ~/Downloads/GRIME
source .venv/bin/activate
python -m uvicorn api.main:app --reload --port 8000
```

### Without a venv

If you'd rather skip the venv, substitute `python3` and `python3 -m pip` everywhere:

```bash
python3 -m pip install -r requirements.txt
python3 -m uvicorn api.main:app --reload --port 8000
```

### Windows / full hydrology pipeline

For the full Python pipeline (rasterio, fiona, pysheds), use conda — pip wheels for these often fail on Windows:

```powershell
conda install -c conda-forge rasterio fiona geopandas pysheds
pip install -r requirements-full.txt
```

### Common gotchas

| Error | Cause | Fix |
|---|---|---|
| `command not found: python` | macOS only has `python3` | Activate the venv, or use `python3` |
| `No module named uvicorn` | Deps not installed in the active Python | `python -m pip install -r requirements.txt` (note `python -m pip`, not bare `pip`) |
| `Mapbox token not configured` (500 from `/api/config`) | `.env` missing or `MAPBOX_TOKEN=` empty | `cp .env.example .env` and fill in the token |
| Port 8000 in use | Old uvicorn still running | `lsof -i :8000` to find PID, or pass `--port 8001` |
| Empty candidates / blank map | Mock data not generated | `python scripts/generate_mock.py` |

---

## 14. Usage

### Dashboard demo (90-second path)

1. Open map → 89,518 places visible as clustered dots
2. Search "Durham" → click → fly to Durham, NC
3. Wait 3s → real Ellerbe Creek geometry appears with scored candidate dots
4. Click top-ranked site → score breakdown panel shows 4 sub-scores
5. Toggle "Waterways" → OSM overlay confirms alignment
6. Toggle "Light theme" → clean beige presentation mode
7. Explain the 27-parameter model and show weight sensitivity

### Python pipeline on custom watershed

```bash
python -m core.pipeline --bbox "-79.05,35.90,-78.75,36.05" --resolution 10 --threshold 500
```

### API queries

```bash
curl http://localhost:8000/api/candidates?top_n=10
curl http://localhost:8000/api/candidates/3
curl http://localhost:8000/api/weights
```

---

## 15. Performance Characteristics

| Operation | Time | Bottleneck |
|-----------|------|-----------|
| Dashboard initial load | ~3s | Parsing ~6MB places.json |
| Overpass API query | 2–8s | Network + OSM server |
| Client-side placement + scoring | <100ms | Haversine collision checks |
| Python DEM pipeline (full) | 30–90s | py3dep HTTP fetch + pysheds |
| Python parameter computation | 3–5 min | Sequential EPA/USGS API calls |

No formal benchmarks have been run. Times above are observed during development.

---

## 16. Reliability and Failure Modes

| Failure | Impact | Mitigation |
|---------|--------|-----------|
| Overpass API down/slow | No real waterway data | Explorer shows an explicit "no waterway data" state (no procedural rivers) |
| EPA/USGS API timeout | Missing parameter values | `safe_call()` returns fallback defaults; `summarize_provenance` flags the resulting constant columns each run |
| Census ACS needs a key | Population/EJ fall back | `CENSUS_API_KEY` (free) enables live values; otherwise neutral defaults |
| EPA EJSCREEN endpoint dead | EJ would be constant | EJ reconstructed from live Census ACS instead (C4, §5.9) |
| Mapbox token missing | Map blank | Fatal for dashboard; replace token |
| pysheds DEM fetch fails | No stream network | Fatal for Python pipeline; mock data works |
| MinMax on a **constant column** | sklearn maps it to **0** (not 0.5), silently deleting its weight | `compute_subscore` **drops constant columns and renormalizes** the surviving weights (logged); an all-constant family returns a neutral 50 |

---

## 17. Security Considerations

- No authentication on the API — local development only
- Mapbox token is a publishable (`pk.*`) client-side token. It lives in `dashboard/config.js` (gitignored) and in `.env` (also gitignored). Use `dashboard/config.example.js` and `.env.example` as templates. Restrict the token by URL in the Mapbox dashboard before deploying anywhere public.
- Overpass queries use numeric interpolation only — no injection risk
- places.json contains only public geographic data, no PII
- All external APIs are read-only and keyless

---

## 18. Testing Strategy

**Automated tests:** `pytest tests/` — 16 property tests over the real scoring code:
composite ∈ [0, 100]; family weights sum to 1; `compute_subscore` drops a constant
column + renormalizes (and an all-constant family → neutral 50); hard gates remove
the right rows; Manning velocity is monotonic in slope; the velocity favorability
curve is peaked; occlusion `(1−η)^k` is non-increasing; estuary/beach distances are
decorrelated; the width-order fallback never trips the gate; the EJ area-weighting
varies across catchments; `optimize_weights` returns a valid simplex point.

**Model drift guard:** `python3 scripts/check_model.py` asserts the Python constants
match `model.json` (the single source of truth for weights, gates, and curves).

**Other validation:**
1. `notebooks/validate_pipeline.ipynb` — pipeline verification + endpoint health
2. `scripts/healthcheck.py` — external endpoint connectivity (incl. the EJ source)
3. `scripts/score_candidates.py` — reproducibly regenerates the scored GeoJSON
4. Dashboard visual QA — verify rivers align with satellite imagery

---

## 19. Limitations

1. **Scores are relative, not absolute.** MinMax normalization means scores across cities are not comparable.
2. **Client-side scoring is approximate.** Dashboard uses heuristics; Python pipeline uses real API data.
3. **Weight values are heuristic.** Not optimized against ground truth. Bayesian scaffold exists but hasn't been run.
4. **OSM coverage varies.** Excellent in US/Europe, variable in developing nations.
5. **Channel width is estimated.** <5% of OSM waterways have width tags. Estimated from type heuristic.
6. **No temporal modeling.** Scores are static snapshots, not seasonal.
7. **US-centric data sources** for the Python pipeline. Dashboard bypasses this with population-scaled heuristics globally.
8. **120m spacing threshold is arbitrary** — tuned by inspection, not engineering spec.

---

## 20. Future Work

- Ground-truth validation against actual trap deployment locations (Durham Stormwater Services)
- Bayesian weight optimization with real data
- Temporal scoring with USGS real-time discharge
- Computer vision trash detection from satellite/drone imagery
- Multi-city normalized scoring for global prioritization
- Cost modeling (deployment cost, maintenance frequency)
- Research publication: extend WaterGate paper, contact Wolfram Research authors
- University collaboration: Duke Environmental Engineering

---

## 21. Appendix

### A. References

1. Chow, V.T. (1959). *Open-Channel Hydraulics.* McGraw-Hill.
2. Leopold, L.B. (1964). *Fluvial Processes in Geomorphology.*
3. Manning, R. (1891). *On the flow of water in open channels and pipes.*

### B. Glossary

| Term | Definition |
|------|-----------|
| **Candidate site** | A point on a waterway evaluated for trap deployment |
| **Composite score** | Final weighted combination of 4 sub-scores (0–100) |
| **CSO** | Combined Sewer Overflow |
| **D8** | Deterministic 8-direction flow algorithm |
| **DEM** | Digital Elevation Model |
| **EJ** | Environmental Justice |
| **Hard gate** | Binary feasibility check that eliminates a candidate |
| **NPDES** | National Pollutant Discharge Elimination System |
| **NBI** | National Bridge Inventory |
| **Stream order** | Confluence-degree heuristic (H3): order 1 = headwater/leaf, higher = more junctions. Not true Strahler. |
| **Sub-score** | Intermediate score (0–100) for one parameter family |
| **TRI** | Toxic Release Inventory |

### C. License

MIT.

---

*GRIME · 27 parameters · 6 families · 89,518 places · 239 countries*
