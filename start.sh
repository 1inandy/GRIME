#!/usr/bin/env bash
# GRIME — quick-start
# Installs deps, regenerates mock data, and boots the API on :8000.

set -e

echo "═══════════════════════════════════════════"
echo "  GRIME — Quick Start"
echo "═══════════════════════════════════════════"
echo ""

# 1. Check Python
echo "[1/5] Checking Python..."
python3 --version || { echo "FATAL: Python 3 not found"; exit 1; }

# 2. Install deps (should be cached from pre-hackathon)
echo "[2/5] Installing dependencies..."
pip install -r requirements.txt --quiet 2>/dev/null || pip install -r requirements.txt

# 3. Safety net: create mock_data/places.json ONLY if it is missing (the script
# skips when the committed dataset exists — regeneration drifts from it). Note
# the scored candidates.geojson has no regeneration safety net here: it is the
# frozen live-pipeline output; see scripts/score_candidates.py for the
# (network-bound) --live path and the offline demo path.
echo "[3/5] Checking mock data + endpoint health..."
python3 scripts/generate_mock.py
python3 scripts/healthcheck.py || echo "  (a required data source is down — see above)"

# 4. Start API in background
echo "[4/5] Starting API on port 8000..."
python3 -m uvicorn api.main:app --host 0.0.0.0 --port 8000 &
API_PID=$!
sleep 2

# 5. Test API — hit a JSON endpoint (/ serves HTML, which would break json.tool under `set -e`)
echo "[5/5] Testing API..."
curl -s http://localhost:8000/api/stats | python3 -m json.tool
echo ""

echo "═══════════════════════════════════════════"
echo "  ✓ GRIME is running"
echo ""
echo "  Landing:    http://localhost:8000/"
echo "  Explorer:   http://localhost:8000/explore"
echo "  Candidates: http://localhost:8000/api/candidates"
echo "  WebSocket:  ws://localhost:8000/ws (snapshot + ping/refresh)"
echo ""
echo "  API PID: $API_PID (kill $API_PID to stop)"
echo "═══════════════════════════════════════════"
