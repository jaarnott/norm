#!/bin/bash
# Run the E2E test suite against the local dev server.
# Logs in as admin@norm.local to get a JWT for the runner.
set -e
cd "$(dirname "$0")"

TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@norm.local","password":"changeme123"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))")

if [ -z "$TOKEN" ]; then
  echo "Failed to log in as admin@norm.local — is the API running at localhost:8000?"
  exit 1
fi

BASE_URL=http://localhost:3000 \
API_URL=http://localhost:8000 \
API_TOKEN=$TOKEN \
ENVIRONMENT=local \
node runner.mjs
