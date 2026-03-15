#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Starting Norm development servers..."

# Ensure Docker services (Postgres, etc.) are running
docker compose -f "$ROOT/docker-compose.yml" up -d

# API
(cd "$ROOT/apps/api" && .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000) &
API_PID=$!

# Frontend
(cd "$ROOT/apps/web" && pnpm dev) &
WEB_PID=$!

echo ""
echo "  Frontend : http://localhost:3000"
echo "  API      : http://localhost:8000"
echo "  Swagger  : http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop."

trap "kill $API_PID $WEB_PID 2>/dev/null" EXIT
wait
