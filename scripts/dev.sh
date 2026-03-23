#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_DIR="$ROOT/apps/api"
WEB_DIR="$ROOT/apps/web"

echo "Starting Norm development servers..."

# ── 1. Check .env ──────────────────────────────────────────────────
if [ ! -f "$ROOT/.env" ]; then
  echo "Creating .env from .env.example …"
  cp "$ROOT/.env.example" "$ROOT/.env"
fi

# ── 2. Python venv & deps ─────────────────────────────────────────
echo "Installing API dependencies …"
(cd "$API_DIR" && uv sync)

# ── 3. Frontend deps ──────────────────────────────────────────────
echo "Installing frontend dependencies …"
(cd "$WEB_DIR" && pnpm install --frozen-lockfile 2>/dev/null || pnpm install)

# ── 4. Docker services (Postgres) ─────────────────────────────────
# Clean up stale containers from previous Codespace sessions
docker compose -f "$ROOT/docker-compose.yml" rm -f postgres 2>/dev/null || true
docker compose -f "$ROOT/docker-compose.yml" up -d postgres

echo "Waiting for Postgres …"
until docker compose -f "$ROOT/docker-compose.yml" exec -T postgres pg_isready -U norm -q 2>/dev/null; do
  sleep 1
done
echo "Postgres is ready."

# ── 5. Run database migrations ────────────────────────────────────
echo "Running Alembic migrations …"
(cd "$API_DIR" && .venv/bin/alembic upgrade head)

# ── 6. OAuth redirect URI (Codespaces / Gitpod / local) ──────────
if [ -n "$CODESPACE_NAME" ]; then
  export OAUTH_REDIRECT_URI="https://${CODESPACE_NAME}-3000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}/api/oauth/callback"
  echo "  OAuth redirect: $OAUTH_REDIRECT_URI"
fi

# ── 7. Start API ──────────────────────────────────────────────────
# Load .env file if it exists (exports vars like LLM_INTERPRETER_MODEL)
if [ -f "$ROOT/.env" ]; then
  set -a
  source "$ROOT/.env"
  set +a
fi
(cd "$API_DIR" && .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000) &
API_PID=$!

# ── 8. Start Frontend ────────────────────────────────────────────
(cd "$WEB_DIR" && pnpm dev) &
WEB_PID=$!

echo ""
echo "  Frontend : http://localhost:3000"
echo "  API      : http://localhost:8000"
echo "  Swagger  : http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop."

trap "kill $API_PID $WEB_PID 2>/dev/null" EXIT
wait
