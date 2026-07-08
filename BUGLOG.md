# GRIME QA Bug Log

Exhaustive "10,000 monkeys" QA crawl of the GRIME static site
(`dashboard/index.html` landing, `dashboard/explore/index.html` map app,
`dashboard/docs/index.html` docs), driven with Playwright + Chromium against the
FastAPI server (`api/main.py`). The real Overpass API was **mocked in the browser**
(never hammered); Mapbox tiles/styles use the real token from `.env`.

Every issue below is: repro steps → expected vs actual → root cause → fix →
verification. Environmental noise that is **not** a bug is listed at the end so the
"clean pass" bar is unambiguous.

## Summary

| # | Bug | Page | Severity | Status |
|---|-----|------|----------|--------|
| 1 | Missing token → infinite loading spinner | explore | high | fixed |
| 2 | Invalid token → silently blank app | explore | high | fixed |
| 3 | Missing/invalid token threw uncaught error (dashboard globe) | landing | med | fixed |
| 4 | Rapid same-city re-open → `already a source "streams"` (map wedged) | explore | high | fixed |
| 5 | Navigate during theme/satellite toggle → `Style is not done loading` | explore | med | fixed |
| 6 | Horizontal layout overflow / clipping on phones | landing, docs | med | fixed |

All fixes are in `dashboard/index.html`, `dashboard/explore/index.html`, and
`dashboard/docs/index.html` (front-end only). No scoring model, weight, curve,
threshold, or dataset was touched.

## Coverage (what was exercised)

- **Systematic:** every nav/footer link, CTA, header button (Waterways / Satellite /
  Light-theme / Docs / home), the search box, place-list rows, candidate rows, map
  cluster/dot clicks, stream clicks (river focus), the site-detail panel, the back
  button, Escape/Enter/Tab keys, the hero WebGL globe (drag), the landing dashboard
  Mapbox globe, count-ups, marquee, docs TOC anchors, docs images, KaTeX.
- **Hotspots:** search fuzzing (empty / whitespace / 5000-char / emoji / Cyrillic /
  CJK / Arabic / `<img onerror>` / `<script>` / SQL / regex metacharacters / lone
  commas / Enter); XSS via injected OSM river names (escaped, never executes); Overpass
  error / abort / malformed / empty responses (graceful "no waterway data", no infinite
  spinner); map races (rapid city switching mid-fetch, pan-autoload storm, zoom 0↔22,
  ocean / poles / antimeridian, city switch mid-river-trace); missing/invalid Mapbox
  token + `/api/config` fallback; responsive layout 320px→2560px.
- **Monkey/fuzz:** 5 seeds × 130 randomized actions (search, place/candidate clicks,
  map clicks/dbl-clicks, pans, zooms, toggles, key presses, viewport resizes, back,
  sidebar scroll), with the live Overpass API mocked so it was never hammered.

## Result

After the fixes, a full pass across every scenario — including the 5-seed monkey fuzz —
produces **0 uncaught exceptions, 0 unhandled promise rejections, 0 app-originated
console errors, and 0 failed/unhandled app fetches**. Remaining console lines are
environmental (see the end of this file).

---

## FIXED

### BUG-1 — Missing Mapbox token → explore app stuck on an infinite spinner
- **Page:** `/explore`
- **Repro:** Start the server with no `MAPBOX_TOKEN` (empty `.env`), or otherwise let
  `getMapboxToken()` resolve to `''` (config.js empty **and** `/api/config` 500). Load `/explore`.
- **Expected:** A clear message that the map can't load.
- **Actual:** The `#loading` overlay ("Loading GRIME — 89,518 places…") spins **forever**.
  Console shows an uncaught `Error: An API access token is required to use Mapbox GL`.
- **Root cause:** `initMap()` did `mapboxgl.accessToken = await getMapboxToken()` then
  `new mapboxgl.Map(...)`. With an empty token the `Map` constructor **throws synchronously**,
  so `initMap()` rejects, `await initMap()` in `init()` rejects, and the rest of `init()`
  (which hides the spinner and sets `#app.ready`) never runs.
- **Fix:** `dashboard/explore/index.html` — `initMap()` now resolves the token first; on an
  empty token it calls a new `showFatalError(title, detail)` (hides the spinner, shows a clear
  terminal overlay) and returns `false`. The `Map` constructor is wrapped in `try/catch`.
  `init()` now bails if `initMap()` returns falsy, so it never touches an undefined `map`.
- **Verified:** Empty-token load now shows "Map unavailable — No Mapbox token is configured…";
  spinner hidden; `page_errors = 0`. Happy path unchanged (`#app.ready`, 80 cities, 0 errors).

### BUG-2 — Invalid/rejected Mapbox token → silently blank app
- **Page:** `/explore`
- **Repro:** Serve a well-formed but invalid token (`pk.invalidfaketoken123`). Load `/explore`.
- **Expected:** A clear message that the token was rejected.
- **Actual:** Spinner hides and `#app` becomes "ready", but the **sidebar is empty (0 cities)**
  and the **map is blank** — because the style request 401/403s, `map.loaded()` stays false,
  and the `map.on('load', …)` that calls `loadWorldView()` never fires. Two console errors, no
  explanation.
- **Root cause:** No handling of Mapbox style auth failure; the world view is gated entirely on
  the `load` event, which never comes for a rejected token.
- **Fix:** `initMap()` attaches `map.on('error', …)` that, on a one-shot 401/403, calls
  `showFatalError('Map unavailable', 'The Mapbox token was rejected (HTTP …)…')`. Transient tile
  errors (no 401/403 status) are ignored.
- **Verified:** Invalid-token load now shows the rejection overlay; `page_errors = 0`. (The
  browser still logs the underlying 401 network response — that is the real, correct signal that
  the token is bad; it is now handled with a clear message instead of a blank screen.)

### BUG-3 — Landing page: missing/invalid token threw an uncaught error from the dashboard globe
- **Page:** `/` (landing)
- **Repro:** No/invalid token; scroll the decorative Mapbox globe (`#dash-globe`) into view.
- **Expected:** The decorative globe quietly does nothing; the rest of the page is fine.
- **Actual:** `new mapboxgl.Map()` threw `An API access token is required…` (uncaught, inside the
  IntersectionObserver callback). Rest of the landing page rendered OK, but the console error is
  noise and the failure is unhandled.
- **Root cause:** Same as BUG-1 but for the decorative globe; no empty-token guard / try-catch.
- **Fix:** `dashboard/index.html` — resolve token first, `if(!token)return;` (skip the decorative
  globe silently), and wrap the `Map` constructor in `try/catch`.
- **Verified:** No/invalid token on the landing page → `page_errors = 0`; the page renders fully
  without the globe.

### BUG-4 — Rapid re-open of the same city → `There is already a source with ID "streams"` (map wedged)
- **Page:** `/explore`
- **Repro:** Rapidly trigger `openCity()` for the same city more than once before the first
  completes (e.g. two `openCity` calls for the same index while an Overpass/region fetch is in
  flight — reachable by re-clicking the same city dot / list row during load). Reliably reproduced
  by firing a burst of `openCity` calls and one more after 200 ms.
- **Expected:** Latest open wins; map renders once; no error.
- **Actual:** Two `openCity` flows both pass the stale-guard and both call `addMapLayers()`; the
  second `map.addSource('streams', …)` throws `Error: There is already a source with ID "streams"`,
  which wedges the map (layers half-added).
- **Root cause:** The stale-guard was `if(activeCityIdx!==idx)return;`. It distinguishes a switch to
  a *different* city, but two concurrent opens of the *same* `idx` both satisfy `activeCityIdx===idx`,
  so both proceed past every `await` and both add layers.
- **Fix:** `dashboard/explore/index.html` —
  1. **Root cause:** a monotonic `openCityToken`. Each `openCity()` does `const myToken=++openCityToken`
     and every post-`await` guard is now `if(myToken!==openCityToken)return;` (the background
     region-naming `.then` too). A newer open — even of the same city — makes the older flow abort.
  2. **Defense-in-depth:** `addMapLayers()` is now idempotent — it removes any existing
     `streams`/`candidates` layers+sources before adding, so a duplicate-source throw can never wedge
     the map even if some future path double-calls it.
- **Verified:** Rapid mid-fetch switching across 6 cities + repeated same-city opens → consistent
  final state, `page_errors=0`, `unhandledrejections=0`. Happy path and double-click unaffected.

### BUG-5 — "Style is not done loading" throw when navigating during a theme/satellite toggle
- **Page:** `/explore`
- **Repro (deterministic):** Found by the monkey fuzz (seed 2024, step 121) and replayed:
  the action sequence `toggle Satellite → click a city → toggle Satellite → click "← All cities"`.
  Generally: click **Waterways/Satellite/Light-theme** (which calls `map.setStyle()`), then — before
  the new style finishes loading — navigate (back button → `loadWorldView`, open a city →
  `addMapLayers`, or toggle the OSM overlay → `addWaterwayOverlay`).
- **Expected:** The map rebuilds and the navigation renders; no error.
- **Actual:** Uncaught `Error: Style is not done loading` (plus Mapbox sprite/image load errors),
  and the world view can be left without its `places` layer (blank map) until the next action.
- **Root cause:** The satellite and dark/light Mapbox styles have different sprites, so `setStyle()`
  can't diff them and **rebuilds the style from scratch**, leaving `map.isStyleLoaded()` false for a
  few hundred ms. `loadWorldView()`, `addMapLayers()`, and `addWaterwayOverlay()` call
  `map.addSource`/`map.addLayer` unconditionally; when one runs inside that window (any navigation
  overlapping a toggle) Mapbox throws.
- **Fix:** `dashboard/explore/index.html` — added `styleReady()` (= `map.isStyleLoaded()`). All three
  builders now defer their layer work to `map.once('style.load', …)` when the style is reloading. All
  three are idempotent (`loadWorldView` clears first; `addMapLayers` removes stream/candidate
  layers+sources first; `addWaterwayOverlay` guards on `getSource`), so the deferred run plus
  `reloadMap()`'s own post-reload render can't double-add. `loadWorldView` also updates the sidebar
  chrome *before* the guard so the UI stays correct even while the map layers wait for the reload.
- **Verified:** Seed-2024 replay (the deterministic trigger) → `page_errors=0`; targeted
  toggle-during-load / toggle-then-search / rapid-fire-toggle scenarios → `page_errors=0`.

### BUG-6 — Horizontal layout overflow / clipping on mobile (landing + docs)
- **Pages:** `/` (landing) and `/docs`
- **Repro:** Load at a phone width (320–375px). Measure `documentElement.scrollWidth` vs
  `innerWidth`.
- **Expected:** Content fits the viewport (no clipped right edge).
- **Actual:** Landing overflowed ~148px at 375px / ~203px at 320px; docs overflowed ~68px / ~123px.
  Because both pages set `body{overflow-x:hidden}`, the excess isn't scrollable — it's **clipped**
  (right edge of stats/reach cards and wide data tables cut off). `/explore` was already fully
  responsive (0 overflow at every size).
- **Root cause:**
  - **Landing** had **no media queries at all** — the desktop layout (three-column `.stats-grid`
    / `.how-cards` / `.reach-nums` plus `section{padding:112px 64px}`) can't fit a phone.
  - **Docs** already collapses below 900px, but its wide `<table>`s (`width:100%` with wide
    content) still overflowed and were clipped (math/`pre` blocks already had `overflow-x:auto`).
- **Fix (minimal, mobile-only — desktop untouched):**
  - `dashboard/index.html`: one `@media(max-width:640px)` block collapsing the three grids to a
    single column, converting the dividing borders to horizontal, and tightening section padding.
  - `dashboard/docs/index.html`: added `table{display:block;overflow-x:auto}` to the existing
    `@media(max-width:900px)` so wide tables scroll horizontally instead of clipping.
- **Verified:** All three pages now report **0 horizontal overflow** at 320/375/768/1440/1920/2560px;
  desktop (≥768px) rendering is byte-for-byte unchanged (media queries only apply below their
  breakpoints).

---

## NOT A BUG (environmental noise — excluded from the "clean pass" bar)

- **`net::ERR_ABORTED` on `api.mapbox.com/v4/…` tiles** — Mapbox cancels in-flight tile requests
  whenever the camera moves (`flyTo`/`easeTo`/pan). Expected and normal; hundreds appear during
  animation-heavy sessions.
- **`GL Driver Message … GPU stall due to ReadPixels`** — headless Chromium software-GL
  performance warning, not emitted by app code.
- **`Unable to perform style diff: Unimplemented: setSprite.. Rebuilding the style from scratch`** —
  Mapbox internal notice when `setStyle()` runs (theme / satellite toggle). Benign.
- **`/api/config` 500 and Mapbox style 401/403 console lines in the no/invalid-token tests** —
  the browser reporting genuinely failed requests in a deliberately-misconfigured environment.
  These are now *handled* (fallback taken, clear overlay shown); the log line is the browser's own
  network reporting and reflects reality (the server/token really is misconfigured).
