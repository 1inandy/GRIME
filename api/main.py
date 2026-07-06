"""
GRIME API — FastAPI backend
Serves scored candidate sites, score breakdowns, and real-time updates via WebSocket.

Run: uvicorn api.main:app --reload --port 8000
"""

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import json
import asyncio
from pathlib import Path
from typing import Optional
import os

load_dotenv()

app = FastAPI(
    title="GRIME API",
    description="Garbage River Interception and Modeling Engine",
    version="3.0.0",
    docs_url="/api/swagger",
    redoc_url="/api/redoc",
)

# CORS (M8). Open by default for the public-data local demo. Before grime.world
# serves this API publicly, set GRIME_ALLOWED_ORIGIN (comma-separated) to lock it
# to the deploy origin(s) instead of "*".
_allowed = os.getenv("GRIME_ALLOWED_ORIGIN", "").strip()
_origins = [o.strip() for o in _allowed.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Data paths ───────────────────────────────────────────────────────
# Resolve relative to the project root (one level up from api/)
BASE_DIR = Path(__file__).resolve().parent.parent
MOCK_DIR = BASE_DIR / "mock_data"

# Cache loaded candidates in memory
_candidates_cache = None


def load_candidates(force_mock=False):
    """Load the scored candidates GeoJSON.

    ``scored_candidates.geojson`` is an optional local-override slot that no
    committed script writes; in a stock checkout the loader always serves
    ``candidates.geojson`` — the frozen live-pipeline output.
    """
    global _candidates_cache
    if _candidates_cache is not None and not force_mock:
        return _candidates_cache

    paths = [
        MOCK_DIR / "scored_candidates.geojson",
        MOCK_DIR / "candidates.geojson",
    ]
    for p in paths:
        if p.exists():
            with open(p) as f:
                _candidates_cache = json.load(f)
            print(f"Loaded candidates from {p}")
            return _candidates_cache

    # No data at all — return empty GeoJSON
    return {"type": "FeatureCollection", "features": []}


# ── REST Endpoints ───────────────────────────────────────────────────

@app.get("/healthz", include_in_schema=False)
async def healthcheck():
    """Lightweight liveness check for the hosting platform."""
    return {"status": "ok"}


@app.get("/api/config")
def get_config():
    token = os.getenv("MAPBOX_TOKEN", "")
    if not token:
        raise HTTPException(status_code=500, detail="Mapbox token not configured")
    return {"mapbox_token": token}


@app.get("/dashboard/config.js", include_in_schema=False)
def get_dashboard_config():
    """Serve deployment config without requiring a tracked token file."""
    token = os.getenv("MAPBOX_TOKEN", "")
    return Response(
        content=f"const MAPBOX_TOKEN = {json.dumps(token)};\n",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/candidates")
async def get_candidates(
    min_score: Optional[float] = Query(None, description="Minimum composite score"),
    top_n: Optional[int] = Query(None, description="Return top N candidates"),
    subscore: Optional[str] = Query(None, description="Sort by this sub-score instead"),
):
    """
    Return all scored candidate sites as GeoJSON.
    Optional filters: min_score, top_n, subscore sort.
    """
    data = load_candidates()
    # Sorting must not mutate the shared cache (list copied before sort).
    features = list(data.get("features", []))

    if min_score is not None:
        features = [
            f for f in features
            if f.get("properties", {}).get("composite_score", 0) >= min_score
        ]

    sort_key = subscore or "composite_score"
    features.sort(
        key=lambda f: f.get("properties", {}).get(sort_key, 0),
        reverse=True,
    )

    if top_n is not None:
        features = features[:top_n]

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "total": len(features),
            "sort_by": sort_key,
            # A feature's durable identifier is its `rank` property (1-based,
            # by composite score in the shipped dataset) — list positions in a
            # sorted/filtered response are NOT identifiers.
            "id_field": "rank",
            "detail_endpoint": "/api/candidates/{rank}",
        },
    }


@app.get("/api/candidates/{candidate_id}")
async def get_candidate_detail(candidate_id: int):
    """Return detailed score breakdown for a single candidate.

    ``candidate_id`` is the candidate's ``rank`` property (1-based; rank 1 =
    highest composite in the shipped dataset), NOT a position in the (re-
    sortable) /api/candidates list. ``segment_id`` cannot serve as the key:
    several candidates can sit on the same stream segment.
    """
    data = load_candidates()
    features = data.get("features", [])

    feature = next(
        (f for f in features
         if f.get("properties", {}).get("rank") == candidate_id),
        None,
    )
    if feature is None:
        return JSONResponse(status_code=404, content={
            "error": "Candidate not found",
            "hint": "candidate_id is the feature's 'rank' property "
                    f"(1-based); this dataset has {len(features)} candidates",
        })

    props = feature.get("properties", {})

    breakdown = {
        "candidate_id": candidate_id,
        "rank": props.get("rank"),
        "segment_id": props.get("segment_id"),
        "composite_score": props.get("composite_score", 0),
        "subscores": {
            "generation": {
                "score": props.get("generation_score", 0),
                "weight": 0.30,
                "parameters": {
                    k: props.get(k, 0)
                    for k in [
                        "population_density", "impervious_pct", "road_density_km_km2",
                        "tri_facility_density", "npdes_points", "cso_density",
                        "litter_complaint_density",
                    ]
                },
            },
            "flow": {
                "score": props.get("flow_score", 0),
                "weight": 0.25,
                "parameters": {
                    k: props.get(k, 0)
                    for k in [
                        "usgs_mean_q_cfs", "flow_velocity_ms", "stream_order",
                        "catchment_area_km2", "flood_q10_cfs", "seasonal_cv",
                        "runoff_coeff_C",
                    ]
                },
            },
            "impact": {
                "score": props.get("impact_score", 0),
                "weight": 0.30,
                "parameters": {
                    k: props.get(k, 0)
                    for k in [
                        "water_intake_score", "protected_area_score", "ej_index",
                        "estuary_dist_km", "beach_dist_km", "tourism_amenity_density",
                        "superfund_score",
                    ]
                },
            },
            "feasibility": {
                "score": props.get("feasibility_score", 0),
                "weight": 0.15,
                "parameters": {
                    k: props.get(k, 0)
                    for k in [
                        "road_access_score", "channel_width_score",
                        "velocity_feasibility", "land_ownership",
                        "bank_slope_score", "bridge_proximity_bonus",
                    ]
                },
            },
        },
        "location": feature.get("geometry", {}).get("coordinates", []),
    }

    return breakdown


NOMINAL_PARAMETER_WEIGHTS = {
    "generation": {
        "population_density": 0.18, "impervious_pct": 0.20,
        "road_density_km_km2": 0.10, "tri_facility_density": 0.18,
        "npdes_points": 0.12, "cso_density": 0.12,
        "litter_complaint_density": 0.10,
    },
    "flow": {
        "usgs_mean_q_cfs": 0.22, "flow_velocity_ms": 0.16,
        "stream_order": 0.14, "catchment_area_km2": 0.18,
        "flood_q10_cfs": 0.14, "seasonal_cv": 0.10,
        "runoff_coeff_C": 0.06,
    },
    "impact": {
        "water_intake_score": 0.22, "protected_area_score": 0.16,
        "ej_index": 0.18, "estuary_dist_km": 0.14,
        "beach_dist_km": 0.12, "tourism_amenity_density": 0.10,
        "superfund_score": 0.08,
    },
    "feasibility": {
        "road_access_score": 0.25, "channel_width_score": 0.20,
        "velocity_feasibility": 0.20, "land_ownership": 0.15,
        "bank_slope_score": 0.10, "bridge_proximity_bonus": 0.10,
    },
}


def compute_effective_weights(features):
    """Mirror core.scoring.compute_subscore's constant-column handling: a
    parameter that is constant (or missing) across the served dataset is
    dropped and the surviving weights renormalized — so its effective weight
    on the served scores is 0.0. Returns (effective_weights, inert_params),
    or (None, None) when there is no data to derive them from."""
    if not features:
        return None, None
    effective, inert = {}, {}
    for fam, weights in NOMINAL_PARAMETER_WEIGHTS.items():
        varying = {}
        for param, w in weights.items():
            values = {f.get("properties", {}).get(param) for f in features}
            if len(values) > 1:
                varying[param] = w
        total = sum(varying.values())
        effective[fam] = {
            p: (round(w / total, 6) if p in varying and total else 0.0)
            for p, w in weights.items()
        }
        inert[fam] = sorted(p for p in weights if p not in varying)
    return effective, inert


@app.get("/api/weights")
async def get_weights():
    """Return scoring weights — both nominal (the model.json design weights)
    and effective (what actually drove the served dataset).

    core.scoring drops constant columns and renormalizes the survivors, so a
    parameter that was a constant fallback in the served run (dead endpoint,
    single-gauge broadcast, ...) contributed nothing regardless of its design
    weight. `parameter_weights` keeps the nominal values for backward
    compatibility; read `parameter_weights_effective` to understand the
    served scores.
    """
    effective, inert = compute_effective_weights(
        load_candidates().get("features", [])
    )
    return {
        "subscore_weights": {
            "generation": 0.30,
            "flow": 0.25,
            "impact": 0.30,
            "feasibility": 0.15,
        },
        "parameter_weights": NOMINAL_PARAMETER_WEIGHTS,
        "parameter_weights_note": (
            "Nominal design weights (match model.json). See "
            "parameter_weights_effective for what drove the served scores."
        ),
        "parameter_weights_effective": effective,
        "inert_parameters": inert,
        "effective_note": (
            "Effective weights after core.scoring dropped constant columns "
            "and renormalized the survivors for the currently served "
            "dataset; inert_parameters lists the dropped (constant-fallback) "
            "parameters per family. flow_velocity_ms enters the Flow "
            "sub-score through a peaked transport-favorability curve, not "
            "raw (see model.json)."
        ),
    }


@app.get("/api/stats")
async def get_stats():
    """Return summary statistics of the scored candidates."""
    data = load_candidates()
    features = data.get("features", [])

    if not features:
        return {"total": 0}

    scores = [f.get("properties", {}).get("composite_score", 0) for f in features]

    return {
        "total_candidates": len(features),
        "score_range": [min(scores), max(scores)],
        "score_mean": sum(scores) / len(scores),
        "top_5": [
            {
                "rank": f.get("properties", {}).get("rank", i + 1),
                "segment_id": f.get("properties", {}).get("segment_id"),
                "score": f.get("properties", {}).get("composite_score", 0),
                "location": f.get("geometry", {}).get("coordinates", []),
                "generation": f.get("properties", {}).get("generation_score", 0),
                "flow": f.get("properties", {}).get("flow_score", 0),
                "impact": f.get("properties", {}).get("impact_score", 0),
                "feasibility": f.get("properties", {}).get("feasibility_score", 0),
            }
            for i, f in enumerate(
                sorted(features, key=lambda x: x.get("properties", {}).get("composite_score", 0), reverse=True)[:5]
            )
        ],
    }


# ── WebSocket for real-time updates ──────────────────────────────────

connected_clients = set()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Serve the current scored snapshot over a WebSocket.

    What this endpoint actually does: sends the static candidates GeoJSON
    once on connect, answers {"type": "ping"} with a pong, and re-reads the
    file from disk on {"type": "refresh"}. Nothing in the repo calls
    broadcast(), so no unsolicited "live pipeline" updates are ever pushed —
    this is a snapshot + ping/refresh channel, not a real-time feed.
    """
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        data = load_candidates()
        await websocket.send_json({
            "type": "candidates",
            "data": data,
        })

        while True:
            msg = await websocket.receive_text()
            # A malformed frame must not leak the socket out of connected_clients
            # (the old code let JSONDecodeError propagate past the discard).
            try:
                cmd = json.loads(msg)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "error": "invalid JSON"})
                continue
            if not isinstance(cmd, dict):
                await websocket.send_json({"type": "error", "error": "expected a JSON object"})
                continue

            if cmd.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

            elif cmd.get("type") == "refresh":
                global _candidates_cache
                _candidates_cache = None
                data = load_candidates()
                await websocket.send_json({
                    "type": "candidates",
                    "data": data,
                })

    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.discard(websocket)


async def broadcast(message):
    """Broadcast a message to all connected WebSocket clients.

    Currently unused: no pipeline producer is wired to this — kept as the
    hook a future live pipeline would call."""
    for ws in connected_clients.copy():
        try:
            await ws.send_json(message)
        except Exception:
            connected_clients.discard(ws)


# ── Serve dashboard & static files ───────────────────────────────────

mock_dir = MOCK_DIR
if mock_dir.exists():
    app.mount("/mock_data", StaticFiles(directory=str(mock_dir)), name="mock_data")

dashboard_dir = BASE_DIR / "dashboard"


@app.get("/")
async def serve_landing():
    """Landing page — serves the dashboard."""
    landing = dashboard_dir / "index.html"
    if landing.exists():
        return FileResponse(landing)
    return JSONResponse(status_code=404, content={"error": "Landing page not found"})


@app.get("/dashboard")
async def serve_dashboard():
    """Dashboard page."""
    landing = dashboard_dir / "index.html"
    if landing.exists():
        return FileResponse(landing)
    return JSONResponse(status_code=404, content={"error": "Dashboard not found"})


@app.get("/map")
async def serve_map():
    """Alias that serves the dashboard HTML (documented in README §11)."""
    landing = dashboard_dir / "index.html"
    if landing.exists():
        return FileResponse(landing)
    return JSONResponse(status_code=404, content={"error": "Dashboard not found"})


@app.get("/explore")
async def serve_explore():
    """App / map explorer."""
    explore = dashboard_dir / "explore" / "index.html"
    if explore.exists():
        return FileResponse(explore)
    return JSONResponse(status_code=404, content={"error": "Explorer not found"})


@app.get("/docs")
async def serve_docs():
    """GRIME technical documentation."""
    docs_file = dashboard_dir / "docs" / "index.html"
    if docs_file.exists():
        return FileResponse(docs_file)
    return JSONResponse(status_code=404, content={"error": "Docs not found"})


# Static assets for dashboard (images, etc.)
if dashboard_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard_static")

# Explore static assets
explore_dir = dashboard_dir / "explore"
if explore_dir.exists():
    app.mount("/explore/static", StaticFiles(directory=str(explore_dir)), name="explore_static")

# Docs static assets
docs_dir = dashboard_dir / "docs"
if docs_dir.exists():
    app.mount("/docs/static", StaticFiles(directory=str(docs_dir)), name="docs_static")
