"""
GRIME API — FastAPI backend
Serves scored candidate sites, score breakdowns, and real-time updates via WebSocket.

Run: uvicorn api.main:app --reload --port 8000
"""

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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

# CORS — allow the dashboard to connect from any origin during dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Data paths ───────────────────────────────────────────────────────
# Resolve relative to the project root (one level up from api/)
BASE_DIR = Path(__file__).resolve().parent.parent
MOCK_DIR = BASE_DIR / "mock_data"
DATA_DIR = BASE_DIR / "data"

# Cache loaded candidates in memory
_candidates_cache = None


def load_candidates(force_mock=False):
    """Load scored candidates GeoJSON. Falls back to mock data."""
    global _candidates_cache
    if _candidates_cache is not None and not force_mock:
        return _candidates_cache

    # Try live data first, then mock
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

@app.get("/api/config")
def get_config():
    token = os.getenv("MAPBOX_TOKEN", "")
    if not token:
        raise HTTPException(status_code=500, detail="Mapbox token not configured")
    return {"mapbox_token": token}


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
    features = data.get("features", [])

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
        },
    }


@app.get("/api/candidates/{candidate_id}")
async def get_candidate_detail(candidate_id: int):
    """Return detailed score breakdown for a single candidate."""
    data = load_candidates()
    features = data.get("features", [])

    if candidate_id < 0 or candidate_id >= len(features):
        return JSONResponse(status_code=404, content={"error": "Candidate not found"})

    feature = features[candidate_id]
    props = feature.get("properties", {})

    breakdown = {
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


@app.get("/api/weights")
async def get_weights():
    """Return current scoring weights for all parameters."""
    return {
        "subscore_weights": {
            "generation": 0.30,
            "flow": 0.25,
            "impact": 0.30,
            "feasibility": 0.15,
        },
        "parameter_weights": {
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
        },
    }


@app.get("/api/stats")
async def get_stats():
    """Return summary statistics of the scored candidates."""
    data = load_candidates()
    features = data.get("features", [])

    if not features:
        return {"total": 0}

    scores = [f["properties"].get("composite_score", 0) for f in features]

    return {
        "total_candidates": len(features),
        "score_range": [min(scores), max(scores)],
        "score_mean": sum(scores) / len(scores),
        "top_5": [
            {
                "rank": i + 1,
                "score": f["properties"].get("composite_score", 0),
                "location": f["geometry"]["coordinates"],
                "generation": f["properties"].get("generation_score", 0),
                "flow": f["properties"].get("flow_score", 0),
                "impact": f["properties"].get("impact_score", 0),
                "feasibility": f["properties"].get("feasibility_score", 0),
            }
            for i, f in enumerate(
                sorted(features, key=lambda x: x["properties"].get("composite_score", 0), reverse=True)[:5]
            )
        ],
    }


# ── WebSocket for real-time updates ──────────────────────────────────

connected_clients = set()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for live score updates during pipeline runs."""
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
            cmd = json.loads(msg)

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
        connected_clients.discard(websocket)


async def broadcast(message):
    """Broadcast a message to all connected WebSocket clients."""
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