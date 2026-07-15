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

# ── 3b. E2E deps + Playwright browser ────────────────────────────
echo "Installing E2E dependencies …"
(cd "$ROOT/apps/e2e" && npm install --silent)
if ! npx --prefix "$ROOT/apps/e2e" playwright install --dry-run chromium >/dev/null 2>&1; then
  echo "Installing Playwright Chromium …"
  npx --prefix "$ROOT/apps/e2e" playwright install --with-deps chromium
fi

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

# ── 7a. Cloud SQL Auth Proxy for the central config DB ────────────
# The API reads agents/connectors/playbooks/templates/secrets from the
# shared norm-config Cloud SQL instance. When CONFIG_DATABASE_URL points
# at the local proxy port, ensure the Auth Proxy is running first — the
# API tests config-DB connectivity at import time and exits if it can't.
PROXY_PID=""
if [[ "${CONFIG_DATABASE_URL:-}" == *"127.0.0.1:5433"* ]]; then
  CONFIG_SA_KEY="${CONFIG_SA_KEY:-$ROOT/norm-config-sa.json}"
  CONFIG_INSTANCE="${CONFIG_INSTANCE:-norm-production-491101:australia-southeast1:norm-config}"
  PROXY_BIN="$(command -v cloud-sql-proxy || echo "$HOME/.local/bin/cloud-sql-proxy")"

  if (exec 3<>/dev/tcp/127.0.0.1/5433) 2>/dev/null; then
    exec 3>&- 3<&-
    echo "Cloud SQL proxy already running on 127.0.0.1:5433 — reusing."
  elif [ ! -x "$PROXY_BIN" ]; then
    echo "  WARNING: cloud-sql-proxy not found (looked at '$PROXY_BIN')."
    echo "           Install it from https://cloud.google.com/sql/docs/postgres/sql-proxy"
    echo "           or the API will fail to reach the central config DB."
  elif [ ! -f "$CONFIG_SA_KEY" ]; then
    echo "  WARNING: service-account key not found at $CONFIG_SA_KEY."
    echo "           The API will fail to reach the central config DB."
  else
    echo "Starting Cloud SQL Auth Proxy for $CONFIG_INSTANCE …"
    "$PROXY_BIN" --address 127.0.0.1 --port 5433 \
      --credentials-file "$CONFIG_SA_KEY" "$CONFIG_INSTANCE" &
    PROXY_PID=$!
    for _ in $(seq 1 30); do
      if (exec 3<>/dev/tcp/127.0.0.1/5433) 2>/dev/null; then exec 3>&- 3<&-; break; fi
      sleep 0.5
    done
    echo "Config DB proxy ready on 127.0.0.1:5433."
  fi
fi

(cd "$API_DIR" && .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000) &
API_PID=$!

# ── 8. Start Frontend ────────────────────────────────────────────
# Clean stale lock files and corrupt Turbopack cache
rm -f "$WEB_DIR/.next/dev/lock"
if [ -d "$WEB_DIR/.next" ] && ! (cd "$WEB_DIR" && pnpm next --version > /dev/null 2>&1); then
  echo "Clearing corrupt .next cache …"
  rm -rf "$WEB_DIR/.next"
fi
(cd "$WEB_DIR" && pnpm dev) &
WEB_PID=$!

echo ""
echo "  Frontend : http://localhost:3000"
echo "  API      : http://localhost:8000"
echo "  Swagger  : http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop."

trap "kill $API_PID $WEB_PID $PROXY_PID 2>/dev/null" EXIT
wait
