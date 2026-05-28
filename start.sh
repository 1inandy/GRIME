#!/usr/bin/env bash
# gARB — Hackathon quick-start
# Run this when you sit down at the hackathon. Everything should already work.

set -e

echo "═══════════════════════════════════════════"
echo "  gRIME — Quick Start"
echo "═══════════════════════════════════════════"
echo ""

# 1. Check Python
echo "[1/5] Checking Python..."
python3 --version || { echo "FATAL: Python 3 not found"; exit 1; }

# 2. Install deps (should be cached from pre-hackathon)
echo "[2/5] Installing dependencies..."
pip install -r requirements.txt --quiet 2>/dev/null || pip install -r requirements.txt

# 3. Generate mock data (safety net)
echo "[3/5] Generating mock data..."
python3 scripts/generate_mock.py

# 4. Start API in background
echo "[4/5] Starting API on port 8000..."
python3 -m uvicorn api.main:app --host 0.0.0.0 --port 8000 &
API_PID=$!
sleep 2

# 5. Test API
echo "[5/5] Testing API..."
curl -s http://localhost:8000/ | python3 -m json.tool
echo ""

echo "═══════════════════════════════════════════"
echo "  ✓ gRIME is running"
echo ""
echo "  API:       http://localhost:8000"
echo "  Candidates: http://localhost:8000/api/candidates"
echo "  Dashboard:  Open dashboard/index.html"
echo "  WebSocket:  ws://localhost:8000/ws"
echo ""
echo "  API PID: $API_PID (kill $API_PID to stop)"
echo "═══════════════════════════════════════════"
