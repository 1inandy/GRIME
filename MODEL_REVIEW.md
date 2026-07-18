# GRIME model review: what is supported, what is not, and what to test next

**Status:** research report only. No proposal in this document has been
implemented. Part A regenerated inputs and outputs, but kept model curves, weights,
thresholds, and gates frozen.

**Review date:** 2026-07-18.
**Final Part A baseline:** the validation ledger reports **21/27 reconstructed
Waterkeepers recall**, 22/27 published Waterkeepers recall, 8/10 published Ellerbe
recall, and **147 Durham sites with 94.1% Dirichlet top-25 stability** (exact
94.112%; minimum 75.2%, maximum 100.0%, 9/10 baseline top-ten sites above 90%).
The final flagship has 25/27 varying parameters. Its 94.1% Dirichlet result is
0.1 percentage point below the ledger's 94.2% tolerance floor; this report records
that miss rather than treating internal robustness as external accuracy.

## Executive finding

GRIME is presently a **transparent, reproducible screening and relative-ranking
system**, not a calibrated predictor of litter capture. Its strongest external
result is that its candidate generator places at least one post-gate model candidate
within 80 m of 21 of 27 reconstructed North Carolina Trash Trout locations.
That is useful evidence of *location agreement in one state*.
It is not precision, it does not
show that an unoccupied high-scoring site contains much litter, and it does not
predict kilograms captured.

The final 94.1% Dirichlet result is a useful but imperfect internal robustness
receipt: the leading Durham candidates usually remain in the top 25 when the four
family weights move, while one baseline top-ten site retains the top 25 in only
75.2% of draws.
It does not propagate input, curve, temporal, missing-data, or model-structure
uncertainty. The 8/10 Ellerbe result is encouraging but cannot be reproduced because
the underlying ten coordinates are unavailable; the local audit trail records the
missing-coordinate search and limits that result to a historical claim
([validation ledger](VALIDATION_LOG.md)). There has been no held-out city or
region, no out-of-North-Carolina field test, and no calibration of the explorer's
occlusion efficiency, η = 0.65.

The honest overall evidence grade is therefore **C: a credible research prototype
for prioritizing where to inspect, with limited external location-recall evidence**.
The route to a B is a preregistered precision/field campaign plus a truly held-out
city or region. The route to an A is prospective, multi-city validation against standardized
cleanout or flux observations.

## 1. Evidence language and grading rubric

This report distinguishes the state of every claim:

- **Measured (M):** a field or operator observation exists, such as a trap
  coordinate or time-stamped cleanout. “Measured” does not by itself mean
  independent, unbiased, or suitable for the target being evaluated.
- **Reconstructed (R):** a published result or protocol has been recreated from
  available code/data, with documented differences from the original.
- **Proposed (P):** a study, model change, or protocol described here but not run.
- **Unknown (U):** the necessary observation does not exist or was not available.

Grades evaluate evidence for the *specific claim*, not software quality:

| Grade | Minimum meaning |
|---|---|
| **A** | Prospective or untouched external test; standardized measured outcome; reproducible data and protocol; uncertainty reported. |
| **B** | Independent held-out observations with a reproducible protocol, but limited geography, sample size, or target fidelity. |
| **C** | Reconstructed or non-independent external agreement, or strong internal evidence that does not directly measure the intended outcome. |
| **D** | Mechanistic plausibility, literature analogy, operator summary, or an internal assumption without target-specific calibration. |
| **U** | No empirical evidence for the claim. |

### Current-state scorecard

“Computational status” is deliberately separate from the A–U evidence grade. Exact
reproduction can prove that a number was calculated consistently; it cannot promote
an internal sensitivity result to independent model evidence.

| Claim | State | Computational status | What the evidence actually establishes | Model-evidence grade |
|---|---:|---|---|---:|
| Published Waterkeepers recall = **22/27 (81.5%) at 80 m** | M + historical analysis | **Partial:** the exact June candidate generator was not preserved. | Installed-trap coordinates provide a real reference. The result supports NC site-location agreement only; the fixture construction and exclusions are recorded in the [validation ledger](VALIDATION_LOG.md). | **C** |
| Current Waterkeepers recall = **21/27 (77.8%) at 80 m** | R | **Exact on the reconstruction:** fixture, current harness, protocol and misses are local and rerunnable; the final receipt has zero run failures. | The benchmark is repeatedly used as a regression gate, so it is no longer untouched. The primary metric checks presence of a post-gate model candidate, not high rank, field-confirmed deployment feasibility, or captured litter. See [the validation ledger](VALIDATION_LOG.md). | **C** |
| Ellerbe recall = **8/10 (80%)** | M, not locally available | **Not rerunnable:** the ten coordinates are absent, as documented in the [validation ledger](VALIDATION_LOG.md). | It is corroborating local site agreement, but cannot serve as an auditable merge gate. | **C** |
| Dirichlet top-25 stability = **94.1%** on **147 sites** | R, internal | **Exact on the current artifact:** 10,000 seeded draws; range 75.2–100.0%; 9/10 baseline top-ten sites exceed 90% top-25 retention. The exact 94.112% mean is 0.088 point below the 94.2% ledger tolerance floor. | It demonstrates stability to one family-weight distribution. Dirichlet/SMAA produces rank-acceptability measures ([Lahdelma and Salminen 2001](https://doi.org/10.1287/opre.49.3.444.11220)); it is not accuracy validation, and the tolerance miss should not be hidden. | **D** |
| Final rank agreement vs previous live flagship | R, internal | **Exact on the same 147 coordinates:** Spearman ρ = 0.989807; 8/10 prior top-ten sites remain top ten, all 10 remain top 25, and top-25 overlap is 23/25. | It shows that the final input refresh mostly preserved the prior ordering. It does not show that either ordering predicts field yield or precision. | **D** |
| The four-family architecture is relevant to interceptor siting | Literature + practice | **Auditable mapping:** criteria and citations are explicit. | The closest peer-reviewed analog used a practitioner-informed Bayesian network over flow, width, bank, flashiness, population, imperviousness, access, and permitting ([Battawi et al. 2022](https://doi.org/10.3390/su14106147)). This supports plausibility, not GRIME's numeric mapping. | **D** |
| Precision of high-scoring non-trap sites | U | **No calculation is possible.** | No top-ranked unoccupied site has a standardized field inspection; precision, precision@k, and false-discovery rate are unknown. | **U** |
| Capture mass, item flux, or avoided emissions | U | **No calculation is possible.** | Scores are relative candidate-pool rankings, not probabilities or mass predictions. No score-to-kilogram calibration exists. | **U** |
| Transfer outside North Carolina | U | **No held-region receipt exists.** | National input coverage is not validation. | **U** |
| Transfer across storms and seasons | U | **No event-held-out receipt exists.** | The output is essentially static; river-plastic transport is strongly event-dependent ([van Emmerik et al. 2022](https://doi.org/10.1029/2022EF002811)). | **U** |
| Occlusion **η = 0.65** | Assumption | **The constant is reproducibly applied in the explorer.** | It is not a calibrated universal capture efficiency. A 21-trap NC study notes bypass and calls capture/escape rates a research need ([Lauer et al. 2025](https://doi.org/10.1029/2024CSJ000122)). | **D** |

### Claims that are safe today

- GRIME reproducibly generates post-gate model candidates near 21/27 sites in a
  frozen public NC trap fixture under its documented 80 m reconstruction.
- The final Durham leading pool is insensitive to many, but not all,
  perturbations from one explicit family-weight distribution; mean top-25
  retention is 94.1%, with a 75.2% minimum among the baseline top ten.
- The code, constants, data provenance, fallbacks, and validation misses are
  auditable.
- The model is suitable for **screening and prioritizing field review**.

It is not yet safe to call GRIME “calibrated,” “optimal,” predictive of capture
tonnage, precise, or geographically transferable. A score of 80 is relative to the
candidate pool because each varying column is MinMax-normalized; it is not an 80%
probability or 80% of a physical maximum.

## 2. Weak constants and structural assumptions, ranked

The following is the complete register of constants or fixed design choices found
in this review to be unsupported, only approximately supported, or attached to the
wrong construct. Supported device gates and formulas are listed separately below.
“Effect” describes what would happen *if a later approved version changed the
choice*; nothing here changes it.

| Rank | Constant or fixed choice | Evidence verdict | Effect of an approved change | Effort | Risk to published claims |
|---:|---|---|---|---:|---:|
| **1** | Flow transport Gaussian `exp(-((v-0.9)/0.6)^2)` | No literature anchors a delivery optimum at 0.9 m/s or width 0.6. Across GRIME's observed Durham velocity range it behaves mostly as “faster is better,” while measured plastic transport generally rises with discharge and peaks during events ([van Emmerik et al. 2022](https://doi.org/10.1029/2022EF002811)). High-velocity device failure belongs in the separate feasibility curve. | Would directly change Flow scores, composite ranks, Spearman deltas, and Dirichlet retention. It should not change candidate generation or the post-gate candidate-presence recall of 21/27, but could change any score-threshold diagnostic; the historical paper result cannot be assumed invariant. | Medium | **High** |
| **2** | `seasonal_cv` is inverted, with weight 0.10 in Flow | The sign rewards steady flow and penalizes flashy streams. Literature instead shows delivery concentrated in wet seasons and floods: monthly runoff is a core river-emission driver ([Lebreton et al. 2017](https://doi.org/10.1038/ncomms15611)), and floods mobilize stored plastic ([Hurley et al. 2018](https://doi.org/10.1038/s41561-018-0080-1)). Steadiness may help operations, but that is feasibility, not delivery. | Now that monthly flows vary by reach, reversing/moving this term would alter Flow scores and ranks. Candidate locations and hard gates remain unchanged. | Low–medium | **High** |
| **3** | TRI 0.18 and generic NPDES 0.12 in Generation; Superfund 0.08 in Impact | TRI and SEMS/Superfund describe chemical-management pathways, not litter generation. Generic permitted outfalls are not equivalent to MS4 storm outfalls or CSOs. Trash-specific literature supports land use, mismanaged waste, precipitation/runoff, distance, and discharge ([Meijer et al. 2021](https://doi.org/10.1126/sciadv.aaz5803)); California's formal trash program prioritizes trash-generating land uses and full-capture drainage areas ([California Water Boards](https://www.waterboards.ca.gov/water_issues/programs/stormwater/trash_implementation.html)). | Replacing—not merely reweighting—these proxies with land-use composition, MS4 delivery points, and direct litter observations would change Generation/Impact ranks. National wiring makes the current proxies real, but not more litter-specific. | High | **High** |
| **4** | All 27 within-family weights and family priors **0.30 / 0.25 / 0.30 / 0.15** | They are expert-like priors without an expert panel, elicitation record, consistency test, or outcome fit. Comparable litter-device siting used practitioner-informed conditional probabilities ([Battawi et al.](https://doi.org/10.3390/su14106147)). SMAA can handle uncertain preferences, but does not validate the center weights. | Any change rewrites composite scores, ranks, robustness centers, and potentially score-threshold recall. Post-gate candidate-presence recall should not move. Because constant columns are dropped and remaining weights renormalized, the effective weights also change from city to city. | High for calibration | **Very high** |
| **5** | `water_intake_score` weight 0.22, the largest Impact prior | Protecting drinking-water infrastructure is a plausible policy value, but no located evidence connects capture of floating macro-litter to intake benefit strongly enough to justify the family's largest weight. It also risks conflating chemical/microplastic and macro-litter pathways. | Reducing it changes Impact and overall ranks wherever intake distance varies. The direction of effect is city-specific. | Low to test; high to justify | **High** |
| **6** | Explorer occlusion **η = 0.65** | Device efficiency is site-, event-, debris-, and maintenance-specific. Osprey reports an initial 80+% floating-litter retention rate for one device family, not a universal all-weather value ([Osprey Initiative](https://osprey.world/what-is-a-litter-gitter)); the NC participatory-science study documents bypass uncertainty ([Lauer et al.](https://doi.org/10.1029/2024CSJ000122)). | This does **not** alter the Python flagship scores or current validation harness. It changes the separate explorer's greedy sequence and how strongly downstream illustrated sites are discounted. | Medium–high to calibrate | Medium |
| **7** | Candidate/placement spacing: 200 m pipeline; 500 m same-stream and 300 m cross-stream in the explorer | No published universal spacing rule was located. Baltimore places wheels at stream mouths and major outfalls ([operator program](https://www.mrtrashwheel.com/trash-interception)), but that practice does not establish a universal Euclidean redundancy distance. Connectivity and occlusion should be measured rather than asserted from spacing alone. | Pipeline spacing changes the candidate universe and can change 80 m recall mechanically; it is one of the highest-risk constants to published recall. Explorer spacings change only the illustration. | Medium | **Very high** |
| **8** | Dirichlet concentration `α0 = 10` | Dirichlet weight sampling is defensible, and concentration represents confidence in the center weights ([Gasparini et al. 2017](https://doi.org/10.1002/bimj.201600113)); GRIME did not elicit that confidence. | Changes reported stability intervals and top-k retention, not baseline ranks. An α0 sweep may make the final 94.1% headline higher or lower. | Low | Medium |
| **9** | Municipal complaint Cauchy decay scale = 500 m | A decaying catchment-distance influence is reasonable, but 500 m is not calibrated to storm-drain routing or reporting behavior. 311 complaints are also subject to reporting bias, so spatial precision should not be confused with litter truth ([Boxer et al. 2025](https://doi.org/10.1214/24-AOAS2003)). | Alters Generation values only in cities with a supported complaint feed; the Durham flagship remains on fallback unless a real feed becomes available. | Medium | Medium |
| **10** | Road-access breakpoints (<200 m best, >2,000 m near-minimum) | The litter-device siting literature includes road crossings, access, safety, and permitting as criteria ([Battawi et al. 2022](https://doi.org/10.3390/su14106147)), but it does not anchor GRIME's exact distance breakpoints. A crane-serviced wheel and a hand-emptied Trash Trout plausibly require different access envelopes. | Changes Feasibility and rank; should be tested by device class rather than globally asserted. | Medium | Medium–high |
| **11** | Bank cross-section windows and bank-slope score bins | The high-resolution input is now measured rather than constant. Common stormwater guidance uses approximate 3:1 and 2:1 side-slope constraints ([Minnesota Pollution Control Agency](https://stormwater.pca.state.mn.us/index.php/Design_criteria_for_stormwater_ponds)), but that does not validate GRIME's candidate profile length, bank-search distance, steepest-rise window, or litter-device outcomes. | Metric-window changes can move sites between frozen slope bins and alter Feasibility ranks; scoring-bin changes would be a separate, higher-risk model change. | Medium | Medium |
| **12** | Validation tolerance 80 m, coupled to 200 m candidate spacing; composite ≥70 diagnostic | The 80 m tolerance is a reproducible matching rule, not an interception-effect radius. With 200 m spacing it creates a geometric ceiling and several current misses are near misses. The ≥70 threshold does not transfer across batch-relative MinMax candidate pools; it should be deprecated as a current model diagnostic and retained only in historical receipts. | Radius/spacing changes alter validation numbers without necessarily improving the model. Show recall-versus-radius and never select the radius that maximizes the headline. Take no model action based on the ≥70 value. | Low | **Very high to the validation claim** |

Roadmap trace: weak-constant ranks **1→priority 19**, **2→18**, **3→17**,
**4→23**, **5→15**, **6→11**, **7→16**, **8→3**, **9→12**,
**10→13**, **11→14**, and **12→priorities 5 and 6**. No constant is hidden
inside a bundled “review constants” item.

### Constants with materially stronger support

These are not priorities for change without contrary field evidence:

- The feasibility velocity plateau's 1.5 m/s upper edge closely matches the
  1.52 m/s maximum design velocity in the California certification application
  for in-line netting ([CASQA/SWRCB application](https://www.casqa.org/wp-content/uploads/2023/03/cswrcb_trashtrap_in-line_2022_application_-_05-04-22_2.pdf)).
  The 3.0 m/s hard gate is close to the 3.05 m/s rating of a heavy commercial
  Bandalong trap ([manufacturer specification](https://www.stormwatersystems.com/bandalong-litter-trap)).
- The runoff relation `0.05 + 0.009 × impervious_pct` is Schueler's
  [Simple Method](https://www.stormwatercenter.net/monitoring%20and%20assessment/simple%20meth/simple.htm)
  volumetric runoff coefficient. It should not be presented as a peak-flow
  Rational Method coefficient.
- The 0.5–50 m width hard gates and 2–15 m preferred band are consistent with
  GRIME's small-device screening scope. The Litter Gitter siting study reports
  width, flow, bank steepness, access, and permitting as material criteria
  ([Battawi et al.](https://doi.org/10.3390/su14106147)).

## 3. External networks that can test GRIME

An installed trap is not automatically a “true positive”: sites reflect permits,
funding, access, advocacy, and operator judgment, and low-yield installations may
remain in a network. Coordinates test siting agreement; cleanout events test yield;
upstream/downstream flux observations are required to identify capture efficiency.
Those targets must not be merged into one label.

| Network | Public evidence available now | Best use | Main limitation | Evidence state |
|---|---|---|---|---|
| **Waterkeepers Carolina / Trash Trout** | Peer-reviewed participatory-science work reports 21 traps, seven organizations, and 150,750 recorded litter pieces during 2021–2024 ([Lauer et al. 2025](https://doi.org/10.1029/2024CSJ000122)); GRIME also has the frozen 27-coordinate fixture used for recall. | Immediate NC cleanout/count calibration and present benchmark reconstruction. | It is already the regression set, capture can bypass traps, and records need exposure/downtime normalization. Not an untouched test. | M + R |
| **Asheville GreenWorks Trash Trout** | GreenWorks identifies nine western-NC stream sites and maintains a cleanup reporting program ([StreamKeepers](https://www.ashevillegreenworks.org/streamkeepers)). Its public form records site, date, and waste fields, but not submitted observations. | Strong **held-region** test for western North Carolina; obtain raw logs and exact coordinates through a data-sharing agreement. Treat distinct municipalities/watersheds explicitly rather than calling the whole program one city. | Same broad device family and state; public pages are not the event dataset. | M locations; logs proposed |
| **Osprey Initiative / Litter Gitter** | Osprey reports 31 active traps across five states and says it compiles item/type/brand data ([operator page](https://osprey.world/what-is-a-litter-gitter)); the peer-reviewed siting study reports 43 deployments at its 2022 snapshot ([Battawi et al.](https://doi.org/10.3390/su14106147)). | Multi-city Southeast transfer test and device-stratified cleanout outcomes. | Operator siting logic overlaps GRIME's predictors, creating selection/circularity risk; cumulative summaries are weaker than raw event logs. | M/operator-reported |
| **Baltimore Mr. Trash Wheel family** | The four wheels have a public [per-dumpster collection workbook](https://docs.google.com/spreadsheets/d/1b8Lbe-z3PNb3H8nSsSjrwK2B0ReAblL2/edit?usp=sharing); the operator says about 90% of collection is rain-driven and reports event-scale dumpster surges ([program page](https://www.mrtrashwheel.com/trash-interception)). | Best public event-level temporal dataset: mass/items by wheel and date, paired with rain/discharge. A demanding tidal/large-outfall transfer test. | Only four fixed sites, a different device scale, and no uninstalled-site labels. Mass includes wet organic debris unless filtered. | M/operator dataset |
| **The Ocean Cleanup Interceptors** | The operator publishes deployment locations and changing catch totals on a global [dashboard](https://theoceancleanup.com/dashboard/) and describes a site-specific survey using drones, trackers, cameras, river dimensions, flow, debris, seasonality, and tides ([river program](https://theoceancleanup.com/rivers/)). | Geographic stress test and a future external reserve set, stratified by device class and city. | Fine-grained operations, downtime, incoming flux, and selection records are not all public; device/site heterogeneity is extreme. Archive dated snapshots. | M/operator summaries |
| **Anacostia/DC traps and California full-capture systems** | The Anacostia Watershed Society documents traps at tributary/outfall pathways and redesign prompted by organic-debris clogging ([primary program page](https://www.anacostiaws.org/what-we-do/river-restoration-projects/pollution-reduction/trash-traps.html)); California certifies full-capture systems against a 1-year/1-hour design storm ([California Water Boards](https://www.waterboards.ca.gov/water_issues/programs/stormwater/trash_implementation.html)). | Secondary engineering-envelope and site-selection consistency checks. | Deployment/permit evidence is easier to obtain than standardized yield time series. | M/operator + policy |

Recommended order: (1) Waterkeepers event records and Baltimore's public
dumpster series; (2) Asheville and Osprey raw-data agreements; (3) preserve Ocean
Cleanup cities as the final geographic test rather than consuming them during model
selection.

## 4. Validation upgrades

### 4.1 Desk-precision protocol

This is a **proposed screening study**, not a substitute for field precision.
Street/aerial imagery can assess persistent geometry, access, outfalls, barriers,
and sometimes visible accumulation, but transient litter is poorly observed in
dated imagery. Street-view validation studies specifically find lower validity for
changeable features such as litter ([Vanwolleghem et al. 2014](https://doi.org/10.1186/1476-072X-13-19)). The output must therefore be called
“desk-confirmation rate,” not precision, until field visits occur.

1. **Freeze before looking.** Archive the code commit, source snapshots, region
   configuration, candidate table, and a preregistered decision rule. Do not tune
   on the reviewed sites.
2. **Define two outcomes.** Reviewers separately label (a) *physical deployment
   feasibility* and (b) *visible evidence of chronic litter input/retention*.
   “Unknown” is a valid label; obscured water, stale imagery, or absent street view
   is never a negative.
3. **Sample by city, not convenience.** In each study city select all top-20
   non-trap candidates, 20 candidates sampled from the middle score strata, and
   20 from the bottom strata. Mask scores, rank, and stratum during review. Keep
   known traps as positive-control sites, not as part of non-trap precision.
4. **Use two independent reviewers.** Apply a fixed checklist: stream present;
   channel continuity; approximate width; bank/maintenance access; road or bridge;
   outfall/choke point; overhead/underground ambiguity; navigability; visible debris;
   vegetation/wood blockage; image provider/date/season; and confidence. Report
   raw agreement and Cohen's κ, then adjudicate disagreements without revealing
   score.
5. **Time-match and archive permitted evidence.** Prefer imagery preceding the
   scoring snapshot and record capture month/year. Do not infer absence from a
   single dry-weather view. Store URLs/IDs and reviewer fields, subject to imagery
   licensing rather than copying restricted pixels into the repository.
6. **Report rank-aware results.** Publish desk-confirmation@5, @10, and @20 with
   exact binomial intervals, the full top/middle/bottom contingency table, uncertain
   rate, and results by city. A positive gradient is evidence of discrimination;
   it is still not capture-yield validation.
7. **Convert to field evidence.** From each stratum randomly choose at least ten
   accessible sites per city for blinded field surveys, including sites desk-labeled
   negative or unknown. Use a standardized debris and site-characterization form
   modeled on NOAA's reproducible survey guidance
   ([MDMAP protocol](https://doi.org/10.25923/g720-2n18)), with repeated post-storm
   and dry-weather visits. Predefine the litter/feasibility endpoint with operators.
8. **Estimate actual precision only then.** Report precision@k against the
   preregistered field endpoint, continuous debris count/mass or flux by rank, and
   false discoveries. Preserve all inaccessible sites in an attrition table rather
   than silently dropping them.

A practical **logistics starting point**, not a statistical evidence minimum, is two
cities with 60 desk sites and 30 repeated field sites each. The final sample must be
set by a preregistered power/precision analysis using the expected effect, prevalence,
within-watershed correlation, attrition, and repeated visits. This pilot will not
prove transferability, but it can test whether high scores contain more
field-confirmed physically feasible, litter-relevant sites than blinded controls.

### 4.2 Calibration without spatial leakage: leave one city out

Random candidate-level folds are invalid here because candidates in one watershed
share terrain, hydrology, Census, municipal reporting, and operator selection.
Ignoring spatial structure can seriously underestimate prediction error
([Roberts et al. 2017](https://doi.org/10.1111/ecog.02881)); in environmental
case studies, leave-location-out performance can be far worse than random-fold
performance ([Meyer et al. 2018](https://doi.org/10.1016/j.envsoft.2017.12.001)).

Here “city” means a genuinely independent deployment geography, not a convenient
program label. For Asheville GreenWorks, either hold out the entire western-NC
program as one region or preregister municipality/watershed groups; do not relabel
its multi-municipality network as one city fold.

**Proposed v5 design:**

1. Build a site-event table with city, watershed, device class, installation and
   operating dates, cleanout mass/items, exposure days, rainfall/discharge, downtime,
   overflow/censoring, and all GRIME predictors frozen to information available at
   that date.
2. Do not call arbitrary unoccupied candidates “absences.” Installed sites are
   presence/selection observations; background candidates may contain litter and
   naive presence/background logistic regression yields biased absolute
   probabilities. Case-control results are safest as relative odds unless the
   sampling fractions or prevalence are known ([Keating and Cherry 2004](https://pubs.usgs.gov/publication/5224326)). Field-audited non-sites or measured
   flux provide the stronger negative/continuous outcome.
3. Fit two deliberately simple baselines before any flexible learner:
   - a regularized logistic or conditional-logistic model for field-confirmed
     suitability/high-flux versus matched available sites; and
   - a hierarchical negative-binomial or lognormal model for standardized
     event-level count/mass, with device and city effects.
   A pairwise learning-to-rank model is a v5 alternative when within-city relative
   yield is reliable but cross-device mass is not comparable.
4. Make **city** the outer fold. Hold out every site, candidate, cleanout, scaler,
   imputation statistic, and feature-selection decision from one city. Fit on the
   others, predict the untouched city, and repeat. Within a training fold, use
   watershed-blocked/nested validation for regularization. Never MinMax-normalize
   the held-out city jointly with training cities.
5. Report every city separately and pooled: recall at fixed geometric tolerances;
   precision@k and area under the precision-recall curve; NDCG or rank correlation
   for yield; Brier/log score and a reliability plot only if probabilities are
   estimated. Proper scores assess probabilistic calibration and sharpness
   ([Gneiting and Raftery 2007](https://doi.org/10.1198/016214506000001437)).
6. Compare against frozen v4, equal weights, population-only, impervious-only,
   and simple discharge/catchment baselines. Report bootstrap intervals over cities,
   not just thousands of correlated candidates.
7. Lock the selected v5 model, then run once on an untouched external network—ideally
   an Ocean Cleanup city or a withheld Osprey metro. Leave-one-city-out guards
   against overfitting among sampled cities; it does not prove worldwide transfer.

The principal risk is small effective sample size: 500 cleanouts from four devices
are closer to four spatial units than 500 independent sites. If fewer than about
five genuinely distinct cities with usable outcomes are available, treat coefficient
fitting as exploratory and prefer shrinkage, predeclared signs, and wide intervals.

## 5. Model-structure research

### 5.1 Two-pass HEC-RAS screen on top-k candidates

**Feasibility verdict: technically feasible, scientifically useful, and too expensive
and data-hungry for the first-pass statewide pipeline.** HEC-RAS can compute 1D/2D
unsteady hydraulics and map depth, velocity, water surface, and shear
([USACE RAS Mapper output capabilities](https://www.hec.usace.army.mil/confluence/rasdocs/r2dum/latest/viewing-2d-or-1d-2d-output-using-hec-ras-mapper/overview-of-ras-mapper-output-capabilities)). Its official
workflow requires a terrain model, computational mesh/breaklines, Manning roughness,
and upstream/downstream boundary conditions such as flow/stage hydrographs or rating
curves ([USACE 2D workflow](https://www.hec.usace.army.mil/confluence/rasdocs/r2dum/6.5/introduction/developing-a-2d-or-1d-2d-unsteady-flow-model),
[boundary conditions](https://www.hec.usace.army.mil/confluence/rasdocs/r2dum/latest/boundary-and-initial-conditions-for-2d-flow-areas)). Fine cells and rapidly
changing hydraulics require smaller time steps, so runtime depends on domain, mesh,
event, and output frequency rather than a universal per-site number
([USACE grid/time-step guidance](https://www.hec.usace.army.mil/confluence/rasdocs/r2dum/6.3/running-a-model-with-2d-flow-areas/selecting-an-appropriate-grid-size-and-time-step)).

**Proposed two-pass design:**

- **Pass 1:** run frozen GRIME citywide. Retain the top 5–20 sites plus a small
  number near hard-gate boundaries and known traps/controls.
- **Pass 2 domain:** model a short reach around each cluster, not one model per point.
  Use lidar terrain with surveyed/bathymetric channel corrections, structures and
  culverts, land-cover Manning's n, observed or regionalized base/storm hydrographs,
  and defensible downstream stage/tide boundaries.
- **Events:** at minimum dry/base flow, an observed moderate storm, and the relevant
  design event. Calibrate stage/velocity where gauges or high-water marks exist;
  label ungauged runs “screening-grade.”
- **Extract:** candidate cross-section depth and velocity distributions, peak and
  duration above device limits, shear, recirculation/strand zones, inundated bank
  access, structure blockage, and sensitivity to roughness/hydrograph.
- **Decision use:** reject sites whose local hydraulics contradict the coarse
  first-pass width/velocity assumptions; re-rank the surviving top-k only after a
  separately approved model version.

The documented HEC-RAS 2D outputs are hydraulic, not a validated floating-litter or
trap-capture component. This review found no documented basis for using HEC-RAS alone
to predict debris flux, boom interaction, clogging, or kilograms captured. Particle
or debris transport would require a separately validated module or postprocessor.
A 2D pass also does not remove the need for an engineering survey,
utility/permit review, geotechnical anchoring design, or operations plan.

Before promising scale, benchmark three representative reach clusters with fixed
domain length, mesh strategy, events, time step, and stored outputs. Record analyst
hours as well as compute hours; terrain correction, structures, boundary data, and
calibration will dominate many sites.

### 5.2 Temporal, storm-pulsed scoring

The current static rank suppresses the strongest recurring signal in the literature.
Global emission models use precipitation/runoff and discharge
([Meijer et al. 2021](https://doi.org/10.1126/sciadv.aaz5803)); Baltimore's operator
reports that roughly 90% of wheel collection is storm-driven
([Mr. Trash Wheel](https://www.mrtrashwheel.com/trash-interception)); observed river
plastic transport can rise dramatically at peak discharge
([van Emmerik et al. 2022](https://doi.org/10.1029/2022EF002811)).

A proposed temporal model should output both `score(site, event/time)` and an annual
expected operational value, not replace one static score with another average.
Inputs should include rainfall intensity and duration, antecedent dry period,
rising/falling stage, event discharge, season/leaf load, tide/backwater where relevant,
device downtime and cleanout capacity. Train and test on complete storm events, never
split records from the same event across folds. Publish dry-weather, typical-storm,
and extreme-event rank changes and flag capacity/overflow censoring.

This is a v5 yield-model problem. A safer v4.x precursor is shadow-mode scenario
scoring that leaves the frozen baseline rank untouched and checks whether known
cleanout surges align with the temporal features.

### 5.3 Full uncertainty propagation

GRIME currently perturbs only four family weights. Environmental uncertainty analysis
should represent both variability and knowledge uncertainty, transparently and
reproducibly; EPA explicitly recognizes Monte Carlo methods for this purpose when
inputs and assumptions are credible ([EPA Guiding Principles](https://www.epa.gov/risk/guiding-principles-monte-carlo-analysis)).

**Proposed uncertainty ledger:**

| Layer | Examples | Representation |
|---|---|---|
| Measurement/source | DEM vertical error, coordinate precision, imagery/data vintage, complaint under-reporting | Source-specific error models, bootstrap or bounded scenarios; never replace unknown with false zero. |
| Temporal variability | Rainfall, discharge, seasonal vegetation, tide, downtime | Event resampling or observed hydrograph ensemble. |
| Parameter/curve | Gaussian center/width, distance decay, hydraulic regression error, η | Literature/field-informed distributions with provenance; scenario bounds where data cannot support a probability law. |
| Preference | Family and within-family weights | Dirichlet or elicited distributions; sweep concentration rather than fixing α0=10. |
| Structural | Gaussian versus saturating delivery, seasonal metric choices, candidate spacing, HEC-RAS/no HEC-RAS | Separate model scenarios, not a falsely precise single distribution. |
| Missingness | Unsupported municipal feeds, unavailable parcel/intake layers | Explicit “unknown” scenarios and missingness indicators; do not sample around a fabricated point estimate. |

Sample correlated variables jointly: imperviousness and runoff coefficient are
deterministically linked; discharge, area, stream order, and flood flow are correlated;
complaints and socioeconomic reporting propensity are not independent. Propagate each
draw through **candidate generation, hard gates, normalization, sub-scores, and rank**.
Perturbing only the final weighted sum misses threshold crossings and changing
candidate pools.

Publish median and 50/90/95% intervals for scores where meaningful, probability of
passing each gate, expected rank, `P(rank ≤ 5)`, `P(rank ≤ 10)`, pairwise
out-ranking probabilities, and the full rank-acceptability matrix. Report separate
panels for input uncertainty, preference sensitivity, and structural scenarios so a
stable weight analysis cannot hide uncertain data. SMAA's intended outputs include
rank acceptability and central weights under uncertain criteria/preferences
([Tervonen and Figueira 2008](https://doi.org/10.1016/j.ejor.2006.12.064)).

### 5.4 Calibrating occlusion η

Observed trap mass alone cannot identify capture efficiency:

`observed catch = incoming capturable load × efficiency × uptime`,

with storage change, overflow, and measurement error layered on top. A high mass can
mean high incoming load rather than high η. Public Baltimore dumpster data, NC
Waterkeepers counts, Osprey totals, and Ocean Cleanup catch totals are valuable yield
observations, but none supplies the missing incoming-load denominator by itself.

**Proposed calibration protocol:**

1. At representative device/site classes, pair time-stamped cleanout dry mass and
   item composition with exposure duration, device state, overflow, rainfall and
   discharge.
2. Measure incoming and bypass flux using synchronized upstream/downstream bridge
   cameras or repeated cross-section visual counts. Standardized long-term camera
   monitoring can recover floating macroplastic dynamics and surface velocity
   ([Pinson and Vollering 2026](https://doi.org/10.1038/s41598-026-48630-z)). Controlled
   release/recovery tests may supplement, but never introduce litter that cannot be
   fully recovered.
3. Estimate event efficiency `captured / (captured + observed bypass)` with uncertainty,
   stratified by device type, debris class, velocity/depth, storm phase, capacity, and
   maintenance state. Use a hierarchical beta-binomial or logit model so sparse sites
   shrink toward a device-class mean rather than one global constant.
4. Validate on held-out sites/events. For the explorer, propagate the posterior η
   distribution through each upstream/downstream placement sequence and show how often
   the selected set changes.

Until then, retain 0.65 only as a clearly labeled scenario. Do not infer it from a
ratio of cleanout mass to GRIME score, and do not use operator-reported “percent
captured” across a different device class as a universal point estimate.

## 6. Prioritized unified roadmap

This table ranks every proposal in this report. “Claim risk” means risk that an
approved implementation changes or weakens a published number; a high risk is not a
reason to avoid a scientifically necessary study, but it requires versioning and a
new validation baseline. Effort is relative: **S** (days), **M** (weeks), **L**
(months/data agreements), **XL** (multi-partner research).

| Priority | Horizon | Proposal | Effort | Expected evidence/decision impact | Claim risk | Required exit condition before adoption |
|---:|---|---|---:|---:|---:|---|
| **1** | **Quick win** | Publish the evidence scorecard and constrain language to screening/location agreement; expose measured/reconstructed/unknown provenance. | S | High | None | Docs and presentation use the same claim language and current receipts. |
| **2** | **Quick win** | Run the preregistered, blinded desk-confirmation pilot with top/middle/bottom samples and dual reviewers. | S–M | High | None | Archived sample, image dates, uncertainty labels, agreement, confirmation@k and intervals. |
| **3** | **Quick win** | Expand robustness reporting: α0 sweep, ≥10,000 seeded draws, full rank-acceptability, expected-rank intervals, top-k curves, and equal-weight baseline. | S | Medium–high | Medium to the final 94.1% headline | Reproducible convergence/Monte Carlo error report; old result retained as historical. |
| **4** | **Quick win** | Acquire and freeze Waterkeepers event records and Baltimore's public collection workbook; write a common data dictionary for mass/items, exposure, rain, device and downtime. | M | High | None | Provenance, licenses, missingness and unit harmonization audited. |
| **5** | **Quick win** | Publish sensitivity of Waterkeepers recall to the matching radius without selecting a favorable radius. Keep 80 m as the frozen comparison. | S | Medium | High to validation wording only | Full recall-versus-radius curve and geometric limitations reported. |
| **6** | **Quick win — no model action** | **Deprecate the composite ≥70 diagnostic** for current evaluation. Preserve it only in historical receipts; do not tune, gate, or make claims from it. | S | High for clarity | None to model; lowers a misleading claim risk | Current docs stop presenting ≥70 as transferable; validation ledger remains intact. |
| **7** | **v4.x candidate** | Execute repeated blinded field surveys for stratified high/mid/low candidates; estimate precision@k and continuous debris differences. | M–L | **Very high** | Low (new evidence may be unfavorable) | Standard protocol, preregistered endpoint, attrition table, city/region-specific intervals. |
| **8** | **v4.x candidate** | Partner for Asheville-region and Osprey exact locations and raw cleanouts; reserve at least one independently defined region or city untouched. | M–L | High | None | Data-sharing terms, timestamps, device state, municipality/watershed groups, and usable outcome fields confirmed. |
| **9** | **v4.x candidate** | Propagate input, curve, gate, missingness and preference uncertainty through the full pipeline; publish rank bands and gate probabilities. | M–L | High | Medium | Correlations and distributions justified; structural scenarios separated; convergence checked. |
| **10** | **v4.x candidate** | Run shadow temporal scores against Baltimore/NC cleanout events without changing baseline ranks. | M | High | None in shadow mode | Event-held-out improvement over static and simple rainfall/discharge baselines. |
| **11** | **v4.x candidate** | Calibrate η with upstream/bypass monitoring; replace the explorer point estimate with device/site posterior scenarios only if data identify efficiency. | L | Medium | Medium to explorer only | Held-out event/site calibration; incoming-load denominator measured. |
| **12** | **v4.x candidate** | Test the **500 m complaint-decay scale** alone in shadow sensitivity and reporting-bias scenarios. | M | Medium | Medium in supported-feed cities | Storm-drain/field or held-region evidence plus a rank/claim delta report; otherwise retain as labeled heuristic. |
| **13** | **v4.x candidate** | Test **road-access distance breakpoints** by device/service class. | M | Medium | Medium–high | Operator/field access classes, held-region comparison, and rank delta justify any replacement. |
| **14** | **v4.x candidate** | Test **bank-profile measurement windows** separately from the frozen slope-score bins. | M | Medium | Medium | Survey/profile comparison and rank delta support any metric change; scoring curve remains separately versioned. |
| **15** | **v4.x candidate** | Reassess the **water-intake Impact weight (0.22)** in a standalone ablation and stakeholder-value review. | M | Medium–high | **High** | Direct macro-litter benefit rationale or elicited policy value, plus held-region and rank deltas. |
| **16** | **v4.x candidate** | Test **pipeline 200 m and explorer 500/300 m spacing** as separate topology/occlusion scenarios. Do not choose spacing to maximize recall. | M | High for candidate coverage | **Very high** | Recall-radius-spacing surface, topology analysis, field/operator rationale, and separate pipeline/explorer decisions. |
| **17** | **v4.x candidate** | Replace TRI/NPDES/Superfund influence with trash-specific land use, MS4 delivery, and direct observation in an experimental branch. | L | High | **High** | Data coverage audit, ablation against frozen v4, no proxy substitution, held-region gain. |
| **18** | **v5 research** | Replace or relocate **inverted `seasonal_cv`**; test event-flow alternatives in shadow mode before any later adoption gate. | M–L | High | **High** | Literature-consistent sign, held-region/event benefit, and no unexplained NC regression. |
| **19** | **v5 research** | Test the **Flow velocity Gaussian center/width and alternative curve forms** independently of `seasonal_cv` and the feasibility curve before any later adoption gate. | M–L | High | **High** | Held-region/event benefit, ablation versus frozen curve, rank delta, and preserved device-feasibility semantics. |
| **20** | **v5 research** | Fit regularized logistic/choice, yield, or learning-to-rank models with nested leave-one-city/region-out validation; compare to frozen/equal/simple baselines. | L–XL | **Very high** | **Very high** | Enough independent groups; all preprocessing inside folds; per-group metrics; untouched external test. |
| **21** | **v5 research** | Benchmark two-pass HEC-RAS on three representative top-k reach clusters, then test top 5–20 per city. | L–XL | High for feasibility | High if used to re-rank | Terrain/channel/boundary calibration, sensitivity, analyst+compute cost, field engineering review. |
| **22** | **v5 research** | Build a storm-pulsed, device-aware annual yield model with capacity/downtime and probabilistic outputs. | XL | **Very high** | **Very high** | Prospective event validation, proper scores/calibration, uncertainty, and operational utility demonstrated. |
| **23** | **v5 research** | Re-estimate family and all within-family weight priors from outcomes and practitioner elicitation, with city/region holdout and shrinkage. | XL | High | **Very high** | External-group improvement over frozen v4 without losing interpretability or equity audit. |

## Bottom line

The next dollar should not buy a more elaborate composite first. It should buy
observations that distinguish a good-looking rank from a useful site: blinded
non-trap field checks, standardized cleanouts, incoming/bypass flux, storm timing,
and independent cities. Quick robustness and desk work can begin immediately, but
the first model-changing release should wait until it can be evaluated city/region-out,
against a frozen v4 baseline, with precision or yield—not only recall near devices
whose locations are already known.
