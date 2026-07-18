# GRIME worldwide expansion study

**Research artifact only — no proposal in this document is implemented.**

**Study date:** 2026-07-18

## Executive answer

GRIME can be made runnable for any city, but it cannot truthfully run the same
27-parameter US model everywhere from global datasets alone. This audit finds:

- **7 direct global equivalents:** population, roads/road access, protected
  areas, beaches, tourism amenities, and bridges;
- **6 parameters with no defensible harmonized global source:** CSOs, litter
  complaints, drinking-water intakes, the ACS-style EJ index, contaminated
  sites/Superfund, and parcel ownership; and
- **14 derived or conditional parameters:** imperviousness, industrial releases,
  permitted discharges, all seven flow variables, estuary distance, channel
  width, velocity feasibility, and bank slope. These work only where source
  scale and semantics fit the urban stream being scored.

That classification is an engineering judgment from the source audit below,
not a claim made by any data publisher. It applies the project's no-proxy rule:
a wastewater-treatment plant is not silently relabelled an NPDES permit, an
industrial release is not a contaminated site, an administrative boundary is
not a parcel, and built-up surface is not automatically impervious surface.

The practical product is therefore a **global core plus country adapters**, with
per-parameter real/fallback receipts in every output. A worldwide launch should
not advertise the current Durham result of 25/27 varying parameters as global
coverage. The recommended
first country is the **Netherlands**, followed by a held-out transfer test in
Klang, Malaysia: the Netherlands combines national 0.5 m lidar, European
pollution/discharge systems and a monitored litter interceptor, while Klang
provides a distinctly non-European Interceptor test. Neither should be used as
both the tuning and final validation set.

## Source-selection rules

“Resolution” below means the source's native grid or feature scale, not the
precision of a displayed map. “Cadence” is the publisher's update cadence where
one exists. “Direct” means the source measures the present parameter's construct;
“conditional” means the construct or spatial scale is incomplete. A successful
query returning no records is a real zero. A missing, inaccessible, unlicensed,
or semantically different source remains the parameter's documented fallback.

Licensing is part of feasibility. In particular, OpenStreetMap requires ODbL
attribution and database-sharing compliance, while the World Database on
Protected and Conserved Areas (formerly WDPA) prohibits commercial use and
redistribution without permission. A technically working commercial service
would therefore need a WDPCA agreement or national protected-area adapters, not
just a downloader.

## The 27-parameter source map

### Generation

| # | Parameter | US source used now | Best worldwide path | Coverage, resolution, cadence, access, licence, and limits |
|---:|---|---|---|---|
| 1 | `population_density` | Census ACS 2022 block groups, area-weighted | **Direct:** WorldPop Global2; GHSL GHS-POP is the conservative production alternative | WorldPop R2025A covers 242 countries at 100 m, annually for 2015–2030, through GeoTIFF/STAC and generally CC BY 4.0, but its release statement labels it alpha and cell area must be handled correctly ([release statement](https://data.worldpop.org/repo/prj/Global_2015_2030/R2025A/doc/Global2_Release_Statement_R2025A_v1.pdf), [STAC API](https://api.stac.worldpop.org/api.html), [licensing FAQ](https://www.worldpop.org/faq/)). GHSL provides global 100 m population epochs and authorizes reuse with attribution ([GHSL downloads](https://human-settlement.emergency.copernicus.eu/download.php?ds=pop)). Use one release consistently; do not mix denominators city by city. |
| 2 | `impervious_pct` | EPA StreamCat `pctimp2019` by NHDPlus catchment | **Conditional:** GAIA binary impervious extent, aggregated to catchment percent | GAIA is global 30 m, annual 1985–2018, CC BY 4.0, and accessible in Earth Engine; it identifies pixels whose impervious fraction exceeds 50%, so catchment percentage is coarser than StreamCat's fractional construct ([GAIA catalog](https://developers.google.com/earth-engine/datasets/catalog/Tsinghua_FROM-GLC_GAIA_v10)). NASA GMIS supplies an exact 30 m fractional percentage but only for 2010 ([GMIS catalog](https://catalog.data.gov/dataset/global-man-made-impervious-surface-gmis-dataset-from-landsat)). GHSL EMC-BUILT is newer, global 10 m built surface for 2022, but buildings/built surface are not the same construct as all impervious cover ([GHSL datasets](https://human-settlement.emergency.copernicus.eu/datasets.php)). No audited product is simultaneously current, global, and construct-identical. |
| 3 | `road_density_km_km2` | OSM drive network clipped to catchment | **Direct:** OSM roads | Global vector coverage; source edits replicate minutely and free Geofabrik country extracts are normally refreshed daily. ODbL; completeness and road classification vary ([OSM copyright/licence](https://www.openstreetmap.org/copyright/en), [Geofabrik extracts](https://download.geofabrik.de/)). Bulk runs should use versioned PBF extracts, not live per-candidate queries. |
| 4 | `tri_facility_density` | EPA TRI open facilities | **Conditional:** national PRTRs; E-PRTR for Europe | The European Industrial Emissions Portal publishes locations and annual regulated releases/transfers for Europe's large industrial complexes as bulk datasets ([EEA dataset](https://industry.eea.europa.eu/industrial-emissions/dataset)); the UNECE map inventories PRTR systems worldwide ([UNECE PRTR map](https://prtr.unece.org/prtr-global-map)). EEA material is normally reusable under its data policy, subject to item-specific notices ([EEA data policy](https://www.eea.europa.eu/en/datahub/eea-data-policy)). Chemicals, thresholds, facility scope and reporting years differ among PRTRs, so values are not automatically comparable; OECD explicitly treats harmonization as a substantive task ([OECD harmonized-list report](https://www.oecd.org/content/dam/oecd/en/publications/reports/2022/06/harmonised-list-of-pollutants-for-global-pollutant-release-and-transfer-registers-prtrs_53f030a2/39657758-en.pdf)). Countries without a usable registry retain fallback. |
| 5 | `npdes_points` | EPA ECHO active NPDES outfalls at outfall coordinates | **Conditional:** national discharge-permit registers; HydroWASTE or EU UWWTD only for the WWTP subset | There is no global permit/outfall register with NPDES-equivalent scope. HydroWASTE v1 is a static CC BY 4.0 point database of 58,502 wastewater-treatment plants with many modelled attributes/outfall locations, not all permitted discharges ([HydroWASTE](https://www.hydrosheds.org/products/hydrowaste), [peer-reviewed description](https://essd.copernicus.org/articles/14/559/2022/)). The EU Urban Waste Water Treatment Directive layer gives European discharge points at nominal 100 m and is updated biannually under CC BY 4.0 ([EEA UWWTD layer](https://www.eea.europa.eu/en/datahub/datahubitem-view/21874828-fa7a-4e7e-8a0a-52ec7d92f99f)). These are useful typed subsets, not permission to relabel every plant as `npdes_points`. |
| 6 | `cso_density` | EPA ECHO open CSO/TCS outfalls | **No global source:** municipal/national overflow adapters | England's official Event Duration Monitoring return is one usable adapter: annual spreadsheets, 2020 onward, under the Open Government Licence ([data.gov.uk EDM returns](https://www.data.gov.uk/dataset/19f6064d-7356-466f-844e-d20ea10ae9fd/event-duration-monitoring-storm-overflows-annual-returns)). Coverage, coordinates and definitions elsewhere are local. General wastewater or permit points are not a CSO proxy. Unsupported cities retain 0.0 fallback. |
| 7 | `litter_complaint_density` | Configured Charlotte, Raleigh and Greensboro complaint feeds; 0 elsewhere | **No global source:** city service-request adapters | Open311 GeoReport v2 standardizes a request API shape and WGS84 location fields, but JSON is optional and each provider controls categories, retention, cadence and licence ([Open311 specification](https://wiki.open311.org/GeoReport_v2/), [known servers](https://wiki.open311.org/GeoReport_v2/Servers/)). Only feeds with explicit litter/illegal-dumping categories qualify. Successful empty queries are real zeros; unsupported or failed feeds stay fallback. |

### Flow

| # | Parameter | US source used now | Best worldwide path | Coverage, resolution, cadence, access, licence, and limits |
|---:|---|---|---|---|
| 8 | `usgs_mean_q_cfs` | NHDPlus EROM `qe_ma` per snapped COMID | **Conditional:** GloFAS daily climatology for large rivers; HydroRIVERS long-term estimate; GRDC for calibration | GloFAS historical data are global except Antarctica (90°N–60°S), daily from 1979 to near-present, about 0.05°/5 km, downloadable as GRIB/NetCDF through EWDS under the CEMS licence ([dataset](https://ewds.climate.copernicus.eu/datasets/cems-glofas-historical?tab=download), [v4 documentation](https://confluence.ecmwf.int/spaces/CEMS/pages/388505179/GloFAS%2Bv4.0), [licence](https://cds.climate.copernicus.eu/licences/cems-floods)). GloFAS v4 calibration is restricted to basins at least 500 km², far larger than many GRIME catchments ([calibration report](https://confluence.ecmwf.int/spaces/CEMS/pages/340755428/GloFAS%2Bv4%2Bcalibration%2Bhydrological%2Bmodel%2Bperformance)). HydroRIVERS is a static ~15 arc-second vector network for catchments ≥10 km² or flow ≥0.1 m³/s and carries estimated long-term discharge; scientific, educational and commercial use is allowed subject to its terms ([HydroRIVERS](https://www.hydrosheds.org/products/hydrorivers)). GRDC has more than 11,000 stations, but no general API, often delayed records, research-use controls and redistribution restrictions; use it for licensed calibration, not as a shippable cache ([portal](https://grdc.bafg.de/data/data_portal/), [FAQ](https://grdc.bafg.de/help/faq/)). |
| 9 | `flow_velocity_ms` | Manning estimate from 10 m DEM slope/width, blended with EROM continuity | **Derived/conditional:** same computation using conditioned DEM, typed width and valid discharge | There is no audited global, urban-stream velocity layer or gauge network that can reproduce the current cross-check. GLO-30 slope plus OSM/GRWL width and GloFAS discharge is only defensible on rivers those products resolve; using 5 km GloFAS flow on a small drain fabricates precision. National gauges or a documented fallback are required below that scale. |
| 10 | `stream_order` | GRIME DEM-network confluence heuristic | **Derived:** recompute the same heuristic from a conditioned global DEM | GLO-30 can support a same-code D8 derivation worldwide. HydroRIVERS supplies river order at ~15 arc-seconds, but it is a static large-river topology reference, not necessarily the same order definition or threshold, so it should be a QA cross-check rather than silently replacing this parameter ([HydroRIVERS fields](https://www.hydrosheds.org/products/hydrorivers)). |
| 11 | `catchment_area_km2` | 10 m 3DEP D8 flow accumulation × cell area | **Derived:** conditioned GLO-30 D8 accumulation; HydroBASINS/HydroRIVERS QA | Global 30 m coverage is available, but the Copernicus DEM is a surface model and must be hydrologically conditioned/burned around buildings, canopy and bridges. HydroRIVERS omits the small urban channels GRIME targets. Resolution cost is quantified below. |
| 12 | `flood_q10_cfs` | NC-domain USGS SIR 2014-5030 regional regressions | **Conditional:** GloFAS annual maxima/return-period products for eligible large rivers; national equations elsewhere | The US regression cannot cross borders. GloFAS exposes 1.5- to 500-year thresholds, and CEMS flood-inundation maps include 10-, 20-, 50- and higher-year events, but the maps cover rivers with upstream area above 500 km² ([threshold documentation](https://global-flood.emergency.copernicus.eu/react/technical-information/glofas-30day/), [inundation-map scope](https://confluence.ecmwf.int/spaces/CEMS/pages/340774762/CEMS-Flood%2Bflood%2Binundation%2Bmaps)). Small urban basins need a country regression or fallback. Replacing a regression with GloFAS is a model-input change requiring fresh validation. |
| 13 | `seasonal_cv` | CV of NHDPlus EROM monthly flows | **Conditional:** CV of 12 monthly GloFAS climatological means | Daily GloFAS supports monthly climatology over its 1979-present archive, under the coverage and basin-scale limits in row 8. It is not a valid small-channel seasonal signal merely because a grid cell exists. |
| 14 | `runoff_coeff_C` | Frozen formula derived from `impervious_pct` | **Derived:** keep the formula; inherit row 2's source status | No separate source is required, but the value is only as real and comparable as the impervious input. The frozen relationship must not be retuned as part of data globalization. |

### Impact

| # | Parameter | US source used now | Best worldwide path | Coverage, resolution, cadence, access, licence, and limits |
|---:|---|---|---|---|
| 15 | `water_intake_score` | NC OneMap/NC DEQ SWAP surface-water intakes | **No global source:** water-utility or regulator point adapters | The audit found no harmonized worldwide surface-water intake-point register. OSM `water_works` or treatment plants are not intake coordinates and are not acceptable proxies. Outside verified national/local inventories, retain the documented zero fallback. |
| 16 | `protected_area_score` | USGS PAD-US 4.1 | **Direct, licence-constrained:** WDPCA/WDPA; national inventories where commercial use is needed | Protected Planet's database is global, vector, downloadable and updated monthly ([database description](https://www.protectedplanet.net/en/thematic-areas/wdpa)). Its licence forbids commercial use without written permission and forbids redistribution of the data without permission ([WDPCA licence](https://www.unep-wcmc.org/en/wdpa-data-license)). Non-commercial research can pin a monthly release; a public/commercial product needs an agreement or country sources. Designation mapping still needs a documented crosswalk to GRIME's frozen weights. |
| 17 | `ej_index` | ACS C17002 low-income + B03002 people-of-color reconstruction, percentile-ranked within region | **No construct-identical global source:** country census/deprivation adapter; World Bank GSAP as a coarse poverty comparator; WorldPop poverty surfaces only where validated | The current two-component index is US demographic policy, not a universally meaningful schema. The World Bank's Global Subnational Poverty Atlas (GSAP) provides line-up poverty estimates for more than 168 economies at first administrative level for 2010, 2019, 2021 and 2023; the October 2025 vintage is public, downloadable as spreadsheet/API and year-specific ZIPs, and CC BY 4.0 ([GSAP catalog](https://datacatalog.worldbank.org/search/dataset/0042041/global-subnational-poverty-atlas-gsap), [poverty portal](https://pipmaps.worldbank.org/en/data/datatopics/poverty-portal/poverty-geospatial)). The catalog labels periodicity annual, but the published line-up years are discrete, so production should pin a vintage rather than assume a new annual observation. GSAP's representative survey regions are normally ADM1 provinces/states and sometimes national or survey-specific regions ([World Bank methodology note](https://blogs.worldbank.org/en/opendata/introducing-second-edition-world-banks-global-subnational-atlas-poverty)); assigning one ADM1 value to every catchment in a city erases within-city deprivation and supplies poverty only, not the current race/ethnicity component or within-region percentile. It is therefore a coverage flag/coarse comparator, not a drop-in `ej_index`. WorldPop's high-resolution poverty surfaces are modelled country products rather than one current, globally complete replacement ([WorldPop poverty methods](https://hub.worldpop.org/resources/docs/pdf/WorldPop-poverty-mapping-methods.pdf)); GHSL provides population and settlement exposure, not the same vulnerability construct. A replacement would be a separately defined and validated model version; until then, use compatible country inputs or neutral fallback. |
| 18 | `estuary_dist_km` | Haversine distance to a configured regional estuary/outlet reference | **Derived/conditional:** verified country/city receiving-water reference; HydroRIVERS ocean-outlet distance as QA | HydroRIVERS includes downstream distance to an ocean outlet, globally for its ≥10 km²/≥0.1 m³/s network ([HydroRIVERS](https://www.hydrosheds.org/products/hydrorivers)). Ocean outlet is not always an estuary, and the network misses small drains. OSM coast/bay features can help locate references but require human/source validation. Do not convert a generic coastline distance into an estuary label. |
| 19 | `beach_dist_km` | Haversine distance to one configured regional beach reference | **Direct:** nearest OSM `natural=beach` geometry, with national coastline QA | Global vector, ODbL, continuously edited/daily extracts ([OSM licence](https://www.openstreetmap.org/copyright/en), [extracts](https://download.geofabrik.de/)). Completeness varies and a mapped polygon does not assert bathing-water status. Country bathing-water registers can supersede it where open. |
| 20 | `tourism_amenity_density` | OSM `leisure`/`tourism` within 2 km | **Direct:** same OSM tags and radius | Global vector, ODbL, daily extract path. Tagging density is contributor-dependent, so within-region ranking is safer than comparing absolute density between countries. |
| 21 | `superfund_score` | EPA SEMS non-archived georeferenced sites | **No global source:** national contaminated-land registries | No audited global point inventory has SEMS-equivalent status and coordinates. E-PRTR is an operating-facility release register and is already the industrial-release input in row 4; reusing it here would double-count one source and violate the no-proxy rule. Unsupported countries retain 0.0 fallback. |

### Feasibility

| # | Parameter | US source used now | Best worldwide path | Coverage, resolution, cadence, access, licence, and limits |
|---:|---|---|---|---|
| 22 | `road_access_score` | Distance to OSM drive network | **Direct:** same OSM network | Global vector, ODbL, daily extracts. Country profiles must normalize road classes only for network construction; the frozen distance-to-score curve remains unchanged. Informal/service-road mapping completeness is a QA issue. |
| 23 | `channel_width_score` | Bieger US hydraulic-geometry curves from drainage area, then the frozen score curve | **Conditional:** GRWL for Landsat-visible rivers; OSM/national width or a calibrated country hydraulic-geometry curve for small channels | GRWL provides a static global 30 m raster and centerline measurements with wetted width under CC BY 4.0 ([dataset and licence](https://zenodo.org/records/1269595), [peer-reviewed source](https://www.science.org/doi/10.1126/science.aat0636)). It cannot reliably resolve GRIME's 0.5–15 m preferred urban channels. OSM `width=*` is sparse. The US Bieger coefficients cannot simply be called global; country calibration or fallback is needed for narrow streams. |
| 24 | `velocity_feasibility` | Frozen step curve applied to `flow_velocity_ms` | **Derived:** same frozen curve | No new data source. It inherits all provenance and scale limits of row 9; the report does not change the curve. |
| 25 | `land_ownership` | Durham/NC parcel owner names mapped to public 1.0 or unknown 0.5 | **No global source:** open national/local cadastre adapters; otherwise unknown 0.5 | GADM supplies administrative boundaries, not parcel geometry or ownership, and its default licence allows academic/non-commercial use but not redistribution or commercial use without permission ([GADM licence](https://gadm.org/license.html)). WDPCA also identifies protection, not ownership. Most cities therefore remain the explicit neutral 0.5 unless an authoritative cadastre exposes both geometry and usable tenure/owner class. |
| 26 | `bank_slope_score` | NC 0.953 m lidar cross-sections; 10 m 3DEP gradient fallback | **Conditional:** national lidar/DTM adapters; no adequate global raster | GLO-30 is too coarse to distinguish opposite banks of many target channels. The Netherlands is exceptional: the cited **AHN3** release provides nationwide 0.5 m and 5 m DTM/DSM GeoTIFFs as open data, including COG access through PDOK ([AHN dataroom](https://www.ahn.nl/dataroom), [PDOK AHN3 service](https://www.pdok.nl/ogc-webservices/-/article/actueel-hoogtebestand-nederland-ahn3)). A production adapter should pin its exact AHN release and service terms. Other countries need audited lidar portals. Where none exists, record the coarse DEM fallback and expect weak discrimination; do not present it as equivalent to lidar. |
| 27 | `bridge_proximity_bonus` | FHWA/BTS National Bridge Inventory within 50 m | **Direct, completeness-limited:** OSM `bridge=*` / `man_made=bridge`; national bridge registers where open | Global vector, ODbL, daily extracts. OSM bridges describe the same physical construct but completeness and node/way geometry vary. Deduplicate structures before applying the frozen 50 m rule. |

## GLO-30 versus 3DEP 10 m: quantified resolution cost

USGS describes its 1/3-arc-second 3DEP product as approximately 10 m and
distributes it as GeoTIFF/COG in the public domain
([USGS elevation-products FAQ](https://www.usgs.gov/faqs/what-types-elevation-datasets-are-available-what-formats-do-they-come-and-where-can-i-download)).
Copernicus GLO-30 is global 1-arc-second (~30 m), a DSM derived primarily from
2011–2015 TanDEM-X acquisitions, freely licensed and available through the
Copernicus Data Space browser, APIs and STAC
([product page](https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM),
[STAC documentation](https://documentation.dataspace.copernicus.eu/APIs/STAC.html)).
Its handbook specifies absolute vertical accuracy below 4 m at 90% linear error
and relative vertical accuracy below 2 m on slopes up to 20% (below 4 m on
steeper slopes), but accuracy specifications do not restore missing spatial
detail ([product handbook](https://dataspace.copernicus.eu/sites/default/files/media/files/2024-06/geo1988-copernicusdem-spe-002_producthandbook_i5.0.pdf)).

| Quantity | 10 m 3DEP | 30 m GLO-30 | Consequence |
|---|---:|---:|---|
| Cell area | 100 m² | 900 m² | One 30 m cell aggregates nine 10 m cells. |
| Pixels per km² | 10,000 | 1,111 | GLO-30 has **88.9% fewer pixels**; raster work is nominally 9× smaller. |
| Pixels in a 100 km² reference bbox | 1,000,000 | 111,111 | Useful transparent unit for city cost comparisons. |
| Half-cell diagonal | 7.1 m | 21.2 m | A raster-derived centerline has a three-times-larger quantization scale before DEM error or conditioning. |
| Current regional threshold `acc > 20,000` | minimum 2.0001 km² | minimum 18.0009 km² | Copying the cell threshold erases many small urban streams. A near-area-equivalent 30 m setting is `acc > 2,222` (minimum 2.0007 km²), which is a configuration translation, not a science retune. |
| Flagship/CLI threshold `acc > 500` | minimum 0.0501 km² | minimum 0.4509 km² | A near-area-equivalent 30 m setting is `acc > 55` (minimum 0.0504 km²). |
| 100 m downstream slope walk | 10 raster steps | about 3 steps | Elevation noise, buildings/canopy and bridge decks have much more leverage on the slope. |

The current hydrology function materializes pit-filled, depression-filled,
flat-resolved, direction and accumulation arrays
([`core/pipeline.py`](core/pipeline.py)). A local dtype inspection of that exact
sequence found a **52 bytes/cell visible-array lower bound** while all five major
arrays coexist. That is about 52 MB for the 100 km² 10 m reference bbox versus
5.8 MB at 30 m, excluding the source raster, masks, GeoTIFF buffers, Python/GDAL
overhead and peak copies. It is a memory-accounting measurement, not an RSS
benchmark. Cell-linear raster stages should approach a 9× work reduction;
end-to-end wall time will not, because downloads, vector overlay and source APIs
remain. A planning range of **3–9× faster for the DEM stage** is an estimate,
not a measured promise.

The accuracy price is more important than the compute saving. GLO-30 is a DSM,
so buildings, canopy and bridges can create false dams in flat cities. A global
pipeline needs depression handling plus hydrography burning/culvert rules, and
must compare the extracted network with OSM/HydroRIVERS. An OSM mask can reject
obvious false channels; it cannot recover a channel that the 30 m raster never
resolved. Country lidar or 10 m DEMs should override GLO-30 whenever licensed.

## What breaks outside the United States

1. **Environmental justice is not portable.** The present ACS race/ethnicity and
   poverty construction encodes US categories and a region-relative percentile.
   A global poverty or deprivation surface would change the construct and its
   uncertainty, not merely fill cells. It belongs in a separately validated
   model version, with country-appropriate definitions and leave-country-out
   testing.

2. **Flood frequency is calibration-domain-bound.** NC's USGS regression must
   become a country/region equation, or a GloFAS return-period value only on
   GloFAS-eligible rivers. A nominal GloFAS pixel on a 2 km² drain is not evidence
   of valid Q10.

3. **Navigability is a legal gate, not a visible-waterway tag.** The present
   USACE/BTS National Waterway Network gate is US-only. OSM `waterway=*`,
   `boat=*` and `motorboat=*` help screen globally, but do not establish legal
   navigability. In Europe, the UNECE Blue Book provides a continuously updated
   inventory of E-waterways and comparable navigation parameters
   ([UNECE Blue Book](https://unece.org/where-navigate-network-inland-waterways-europe-and-its-parameters));
   CEMT class appears in European transport-network data. Elsewhere the gate
   needs a competent authority layer or must remain unknown, never false-safe.

4. **Velocity loses its independent check.** GloFAS and HydroRIVERS are modelled
   products; using them in both the Manning/continuity blend and its “check” is
   not independent. GRDC observations are valuable but too restricted and sparse
   for a live worldwide API. Local gauges are a country-adapter responsibility.

5. **Operational feasibility is the most local family.** Parcel ownership,
   narrow-channel width, bank geometry, bridge inventories, permits and device
   access are governed and measured locally. Global rasters can rank watersheds;
   they cannot by themselves certify a buildable trap site.

6. **Licences can break a public product after the analysis succeeds.** WDPCA
   and GADM default terms are not compatible with unrestricted commercial
   redistribution. GRDC observations cannot simply be bundled. Each adapter
   must store the source version, retrieval date, terms and whether derived
   output may be published.

## Production architecture for the top 500 cities

### Region and adapter generation

Use the European Commission's GHS Urban Centre Database R2024A as the reproducible
city universe. It contains 11,422 quality-controlled urban centres, is available
as CSV/XLS/GPKG under CC BY 4.0 without registration, and provides harmonized
boundaries and attributes
([JRC catalogue](https://data.jrc.ec.europa.eu/dataset/1a338be6-7eaf-480c-9664-3a8ade88cbcd),
[overview](https://human-settlement.emergency.copernicus.eu/ghs_ucdb_2024.php)).
Select the top 500 by a pinned population epoch, not by an unversioned web list.

For each centre, generate a `regions.json`-compatible record containing the
UCDB ID and geometry version, ISO country and first-order administrative unit,
analysis bbox, equal-area/UTM-like working CRS, coastal/endorheic status, DEM
choice, flood method, source-adapter IDs and explicit fallbacks. Derive the bbox
from the urban-centre polygon plus a hydrologic buffer rather than using one
fixed angular box at every latitude. Split centres crossing antimeridian, UTM
zones or national source jurisdictions deterministically.

Country adapters should be declarative manifests, not country-specific branches
inside scoring code. Each manifest needs:

- source URL/API and exact semantic mapping to a GRIME parameter;
- spatial/temporal coverage, native CRS and authoritative resolution;
- licence, attribution, redistribution and commercial-use flags;
- version/cadence, retrieval timestamp, checksum and expiry policy;
- typed outcomes: real records, real empty, unavailable, out of coverage, or
  licence-blocked; and
- a QA fixture with known source records and expected transformed geometry.

The scoring engine should continue to stamp the actual real/fallback split into
each GeoJSON. Cross-city composite scores remain unsafe to compare directly
while parameters are min-max normalized within each region; the worldwide
catalog should expose coverage and within-city rank, not imply a global absolute
suitability scale.

### Tiling, caching and OSM load

1. Build a coverage index first, then fetch only intersecting immutable source
   tiles. Key cache paths by publisher, product, version, tile and checksum.
   Store raw files separately from projected/conditioned derivatives.
2. Mosaic DEM/population/impervious tiles once per overlapping metro cluster;
   cache conditioned DEM, flow direction and accumulation by source hash and
   projected grid. Adjacent centres can reuse these products.
3. Download country or sub-country OSM PBF extracts once and query a local
   spatial database. Geofabrik normally refreshes extracts daily
   ([download service](https://download.geofabrik.de/)). Apply replication only
   if the product truly needs sub-daily freshness.
4. Do not send the 500-city batch to public Overpass. The main public instance's
   published comfort limit is below 10,000 queries/day and 1 GB/day, and other
   instances ask large users to coordinate
   ([public-instance policies](https://wiki.openstreetmap.org/wiki/OSM_Server_Side_Script)).
   PBF/PostGIS is cheaper and reproducible for scheduled builds. A self-hosted
   Overpass instance is justified only for interactive arbitrary-city queries;
   it is not required for the fixed top-500 batch.
5. Rate-limit all governmental APIs, checkpoint by source rather than by city,
   and never convert a timeout into a real zero. A source snapshot failure should
   be restartable without rerunning DEM work.

### Current measured baseline and top-500 estimate

The baseline below is the repository state inspected on 2026-07-12, before the
single final Part A regeneration. It distinguishes measurements, arithmetic
projections, and budgeting assumptions. It is retained as a **historical
pre-regeneration baseline**, not the final delivery receipt.

| Item | Value | Status / derivation |
|---|---:|---|
| Configured US regions | 449 | **Measured** from [`scripts/regions.json`](scripts/regions.json): 23 non-town + 426 town; the Part A baseline identifies 374 not yet run. |
| Completed regional outputs | 75 regions, 6,367 sites | **Measured** from [`mock_data/regions/index.json`](mock_data/regions/index.json). |
| Recorded runtime | 90.2 min total; 1.203 min mean; 0.7 min median; 0.3–18.1 min range | **Measured** from the 75 `runtime_min` receipts. Cache warmth and source availability are not recorded well enough to call this a clean benchmark. |
| Existing regional GeoJSON | 10,188 KiB total; 135.8 KiB mean | **Measured** across 75 files. |
| Top 500 at measured mean | 601 min = **10.0 serial hours**; ~66 MiB GeoJSON | **Arithmetic projection**, same bbox/source mix and no new failures. |
| Top 500 at the task's 374-town/11-hour planning rate | **14.7 serial hours** | **Arithmetic projection:** 11 h / 374 × 500. This is the more conservative current-pipeline planning floor. |
| Worldwide initial scoring | **25–75 worker-hours**, 3–10 h wall time with eight workers | **Estimate:** 2–5× the 10–15 h floor for larger/variable bboxes, country transformations, retries and provenance. Pre-download is separate. |
| Worldwide source working set | **0.25–1.0 TB** | **Estimate:** versioned OSM extracts, DEM/population tiles, conditioned hydrology, national layers and retry headroom; final GeoJSON is negligible. |

#### Final Part A regeneration receipt

These final measurements come only from the single Fix 3 regeneration and remain
separate from the historical baseline above. Cache warmth and public-source waits
make the p95 scenario a budgeting bound rather than a guaranteed service level.

| Final Part A field | Post-regeneration value | Authoritative receipt |
|---|---:|---|
| Completed regions / configured regions | **449/449**: 23 metro + 426 town; 13 honest zero-candidate outputs; zero final failures | [`mock_data/regions/index.json`](mock_data/regions/index.json), [`scripts/regions.json`](scripts/regions.json), and `cache/validation/fix3-final-region-audit.json` |
| Total regional sites; output bytes | **9,903 sites; 19,376,169 bytes (18.48 MiB)** across region GeoJSON, plus a 240,237-byte index | Final region audit and generated files |
| Runtime total / mean / median / range | **860.9 min total** across 436 nonzero regions; mean 1.975, median 0.3, range 0.2–108.9, p95 8.2 min; 19.505 h end-to-end wall time including one execution-environment transition/resume | Final `runtime_min` audit; no failed region remains, and the supervisor did not preserve an exact retry counter |
| Durham flagship sites; live/varying parameters | **147 sites; 25/27 varying**; truthful constants are CSO zero and unsupported municipal litter complaints | Regenerated [`mock_data/candidates.geojson`](mock_data/candidates.geojson) and `cache/validation/fix3-final-flagship-audit.json` |
| Waterkeepers 80 m recall | **21/27 primary**, 0/27 at the ≥70 diagnostic, zero run failures | Final [`VALIDATION_LOG.md`](VALIDATION_LOG.md) row and `cache/validation/waterkeepers_recall_fix3-final-regeneration.json` |
| Dirichlet top-25 stability | **94.1%** (exact 94.112%; min 75.2, max 100.0, 9/10 >90%); 0.088 point below the ledger's 94.2 tolerance floor | Seeded 10,000-draw `cache/validation/dirichlet_fix3-final-regeneration.json`; no score tuning or second regeneration was used to conceal the miss |
| Spearman rank delta vs prior flagship | **ρ = 0.989807** over the same 147 coordinates; 8/10 prior top-ten stay top ten, 10/10 stay top 25, top-25 overlap 23; largest prior top-ten shift 8→4 | Final flagship audit versus previous live artifact at `7589221` |
| Revised top-500 projection | **987.3 min = 16.45 serial h** at the observed 1.975-min mean; **4,100 min = 68.33 serial h** at the observed 8.2-min p95 | Arithmetic: `500 × 1.9746 / 60` and `500 × 8.2 / 60`; public-source waits and cache state dominate |

For another transparent compute lens, assume 500 non-overlapping 100 km² bboxes.
That is 500 million DEM cells at 10 m versus 55.6 million at 30 m, or about
2.0 GB versus 0.22 GB for one uncompressed float32 layer across all cities.
Processing arrays and source caches multiply that figure; overlap-aware mosaics
reduce it. Peak RAM is per concurrent bbox, not the global cell sum.

### Compute and operations budget

These are **planning assumptions, not vendor quotes**: $0.20 per 4-vCPU/16-GB
worker-hour and $0.023 per GB-month object storage. For labour, one
engineer-week is 40 hours and one engineer-month is four engineer-weeks (160
hours). A fully loaded engineer-month is assumed to cost $15,000, hence $3,750
per engineer-week. Phase labour below is
`effort × $15,000/month × 1.20`, with a uniform 20% contingency, rounded to the
nearest $1,000. Infrastructure ranges are scenario envelopes and do **not** get
an additional contingency multiplier.

| Cost centre | Quantity assumption | Budget estimate | Main uncertainty |
|---|---:|---:|---|
| One top-500 batch compute | 25–75 worker-hours | **$5–$15/run** | Source waits and retries dominate CPU; vendor egress may exceed compute. |
| Versioned working storage | 250 GB–1 TB | **$6–$24/month** | OSM country scope, retained DEM versions and national lidar dominate. |
| Fixed-batch OSM | Country PBF + local PostGIS | **Included in worker/storage budget** | Import CPU and disk IOPS; no public Overpass dependency. |
| Optional interactive global Overpass | 16–32 cores, 128 GB RAM, 2–4 TB NVMe | **$300–$800/month estimate** | Current planet size, update history, redundancy and operator time. |
| Monitoring/backups/modest egress allowance | 20% of the selected monthly storage + server subtotal | **$1–$5/month** fixed-batch only; **$61–$165/month** with optional Overpass | Arithmetic: 20% × $6–$24, or 20% × ($6–$24 + $300–$800). This allowance covers metrics/logs, extra snapshots of critical manifests/outputs and modest downloads; it excludes a full duplicate of the raw working cache, staff/on-call time and high-volume public egress. |

Cloud execution cost is not the blocker. Source semantics, licences, country
adapter engineering and validation are orders of magnitude more expensive.

## Validation outside the United States

There is promising operator and monitoring ground truth. One official
machine-readable source is sufficient for a provisional day-one 80 m location
test, but not yet for definitive multi-network validation.

- The Ocean Cleanup dashboard embeds an official machine-readable
  [`realtime-data-river.json`](https://s3.eu-west-1.amazonaws.com/data.theoceancleanup.com/systems-dashboard/realtime-data-river.json)
  feed containing system ID, name, latitude/longitude, location, status and
  status/update dates; the [dashboard](https://theoceancleanup.com/dashboard/)
  also warns that automated catch numbers can be revised after verification.
  **Dated retrieval observation:** fetched 2026-07-12 20:51 EDT; feed generation time
  `Mon, 13 Jul 26 00:05:03 +0000`; SHA-256
  `55202d3fd83d61ecc5e5d993a9d01bed2f78d322bea7616f045cecdfc37677b1`;
  22 coordinate-bearing systems: 18 `in_operation`, one `operation_paused`,
  one `in_maintenance`, and two `installed_for_testing`. This is current
  operator status/location evidence, not independent performance validation.
  The digest documents the bytes observed during this research pass, but those
  source bytes are not committed as a standalone immutable fixture; the live URL
  must never be queried later and called the same benchmark.
- Plastic Fischer reports seven projects in India and one in Indonesia and uses
  TrashBooms in rivers/tributaries ([impact page](https://plasticfischer.com/impact)).
  The public page does not provide a versioned coordinate/deployment table.
- CLEAR RIVERS documents more than twenty international installations since
  2017 and names actual European traps: three in Rotterdam (Coolhaven,
  Delfshaven and Persoonhaven), Schiedam, Westdorpe, Rozenburg, Breda, The
  Hague, Brussels, and four Romanian rivers. Its operator page gives the
  Brussels address, twice-weekly emptying and an average 1.5 m³/month, and
  states weekly emptying for Breda, but does not publish a versioned coordinate
  table, deployment/status history or per-cleanout dataset
  ([CLEAR RIVERS locations](https://www.clearrivers.eu/litter-traps)). These are
  real European device leads. Brussels is the only entry on that page with a
  street address; named harbors/canals can be geocoded provisionally, but all
  need capture-line/operator confirmation for an 80 m benchmark.
- The European Commission JRC's Riverine Litter Observation Network established
  a harmonized visual method for floating macrolitter above 2.5 cm: 32
  institutions regularly observed 54 rivers, and the app records item, size and
  geolocation ([JRC record and peer-reviewed method](https://publications.jrc.ec.europa.eu/repository/handle/JRC104431)).
  The current [Floating Litter Monitoring portal](https://floating-litter-monitoring.jrc.ec.europa.eu/)
  supports ship/bridge surveys in rivers, lakes and seas. This is independent
  litter-flux/standing-observation evidence, **not a trap network**. The audited
  public pages expose a survey map and method but no documented bulk download
  or public observation API, so exact station coordinates and time series require
  a JRC/data-provider request before they can validate GRIME.
- Amsterdam's Westerdok Bubble Barrier has a stronger public evidence chain: the
  water authority confirms the commissioned location and continuous operating
  design ([Waterschap AGV](https://www.agv.nl/werk-in-uitvoering/bubble-barrier/));
  Amsterdam publishes an independent evaluation based on operator, Waternet and
  city data ([evaluation](https://openresearch.amsterdam/en/page/114046/evaluatie-pilot-the-great-bubble-barrier-westerdok));
  and the project reports monitored catch totals ([impact page](https://thegreatbubblebarrier.com/bubble-barrier-amsterdam/)).
  Even here, an operator-confirmed point and active dates are preferable to
  digitizing a map.
- EU Mission projects add potential European use cases, but “pilot city” is not
  the same as a stable device coordinate and operating interval; INSPIRE reports
  eight European use cases for detection/collection/prevention technologies
  ([European Commission project summary](https://projects.research-and-innovation.ec.europa.eu/en/funding/funding-opportunities/funding-programmes-and-open-calls/horizon-europe/eu-missions-horizon-europe/restore-our-ocean-and-waters/innovative-solutions-plastic-free-european-rivers)).

**Could the current 80 m recall test run on day one? Yes, provisionally.** Freeze
the Ocean Cleanup JSON bytes and receipt above, build regions without inspecting
GRIME ranks, and run the current candidate-presence metric first on the 18
`in_operation` points. Report the paused, maintenance and testing systems as
separate status strata, not failures. The result must be labeled
**operator-dashboard coordinate recall**, because the feed does not state
coordinate accuracy, whether a marker is the device or the full capture line,
or the site's selection history; devices and waterway scales are heterogeneous,
and worldwide source gaps can prevent a like-for-like pipeline. Thus a day-one
number is a legitimate provisional stress test, but not an independent or final
validation claim. CLEAR RIVERS, JRC and Westerdok additions still require exact
station/device data and active intervals.

Before freezing an international benchmark, obtain a source-controlled table
from each operator with device ID, longitude/latitude and CRS, capture-line
geometry if applicable, coordinate accuracy, install/removal and downtime dates,
device type, cleanout dates, total wet/dry mass and sampling definitions. Freeze
the device locations before examining GRIME ranks. Report both the existing
80 m primary metric and distance-to-nearest-candidate distributions, and keep
whole cities/countries out of any future calibration.

## Recommended phased rollout

### Pilot recommendation: Netherlands first, Klang held out

The Netherlands is the best first end-to-end country, despite not being globally
representative. AHN 0.5 m terrain resolves banks and flat canals; European PRTR
and wastewater layers are available; OSM is mature; UNECE/CEMT sources improve
navigability screening; and Westerdok supplies a documented interceptor with an
independent public evaluation. Its main blockers are small-canal discharge and
velocity, intake points, CSO semantics, contaminated land and parcel ownership.
Those gaps are useful: the pilot tests whether the pipeline can preserve honest
fallbacks instead of forcing 27 live columns.

Do not tune the model to Westerdok and declare transfer. Keep Klang, Malaysia as
the immediate held-out test because The Ocean Cleanup documents deployed Klang
systems and the city's hydrology/data regime is different. Jakarta is a second
valuable transfer city but adds more difficult small-drain, monsoon, subsidence
and data-access conditions. Indonesia and India then provide Plastic Fischer
sites if exact operator coordinates can be licensed for validation.

### Phases, effort and blockers

All labour/cost figures below use the defined 40-hour week, four-week month,
$15,000 engineer-month and 20% labour contingency. They are **estimates**, not
quotations, and exclude device deployment, field surveying and paid proprietary
data. Infrastructure ranges are separate scenario allowances.

| Phase | Deliverable and exit gate | Estimated effort | Labour / infrastructure allowance | Honest blockers |
|---|---|---:|---:|---|
| 0 — global-core proof | UCDB top-500 generator; GLO-30/WorldPop-or-GHSL/HydroRIVERS/GloFAS/OSM cached adapters; licence manifest; 10-city dry run with per-parameter receipts | 6–8 engineer-weeks | **$27k–$36k labour; $1k–$3k infra** | Hydrologic conditioning, dateline/CRS cases, GloFAS scale enforcement, WDPCA terms. |
| 1 — Netherlands pilot | Amsterdam plus 4–9 contrasting Dutch cities; AHN bank slope; EU/national adapters; Westerdok frozen validation record; all fallbacks audited | 8–12 engineer-weeks | **$36k–$54k labour; $2k–$5k infra** | Canal discharge/velocity, legal navigability, operator coordinates, intake/parcel/CSO access, protected-area commercial licence. |
| 2 — held-out transfer | Klang first, then Jakarta plus 1–2 Indian Plastic Fischer cities; no retuning before recall; source/coverage comparison with Netherlands | 8–16 engineer-weeks | **$36k–$72k labour; $3k–$8k infra** | Dashboard coordinate/capture-line confirmation, monsoon temporal mismatch, narrow-drain DEM errors, sparse permits/CSOs/ownership. |
| 3 — data-rich tier | 10 countries with usable DEM/lidar, discharge, pollution and authority layers; country QA fixtures; annual rebuild | 4–8 engineer-months | **$72k–$144k labour; $5k–$15k/year infra** | Registry semantics and licences, translation, country-specific flood/width calibration, support load. |
| 4 — top-500 catalog | Global core for all 500; adapters where available; public coverage labels; repeatable batch and monitoring | 12–24 engineer-months cumulative | **$216k–$432k labour; $15k–$50k/year infra** | The six no-global-source parameters remain fallback in many cities; validation coverage, not compute, limits defensible claims. |

Suggested service tiers:

- **Tier A — real local:** high-resolution terrain, observed/model-appropriate
  discharge, pollution/discharge registries, authority layers and an external
  trap benchmark. Eligible for the full research pipeline with a published
  coverage receipt.
- **Tier B — global-plus:** global core plus several country adapters; eligible
  for exploratory within-city ranking, with scale-limited flow and explicit
  unknown ownership.
- **Tier C — screening only:** GLO-30/OSM/population with flow or impact gaps;
  useful for finding watersheds to investigate, not for claiming deployment-ready
  sites.

The go/no-go gate for expansion should be evidence, not a target live-parameter
count: source licences clear for the intended product; no invalid cross-scale
GloFAS use; all no-source inputs visibly fallback; an operator-confirmed external
benchmark frozen in advance; and the existing merge/validation tests rerun for
every implemented adapter in a separately approved code phase.
