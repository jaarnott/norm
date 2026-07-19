# Norm — AI Operations Platform for Hospitality

## Working agreement (read this first)

**Do not commit or push unless you are asked to.**

This is not a style preference. Every push to `main` that goes green deploys
**straight to production** (see Deployment Pipeline below) — there is no manual
gate. So an agent that commits on its own initiative has shipped to customers
before anyone read the diff.

What to do instead: make the change, verify it (below), then **report what you
changed and what you verified, and stop**. The user commits when they're ready.

- Finishing a step in a multi-step task is **not** a reason to commit. Leave the
  work in the working tree and keep going.
- "Committing to be safe" is the opposite of safe here.
- Uncommitted work is fine. This repo is often worked on by more than one agent
  at once, so also: only ever stage **your own** files (`git add <paths>`, never
  `git add -A`), and never push commits you didn't author.
- When a commit *is* requested, follow the conventions further down: branch off
  `main` if you're on it, and end the message with the co-author trailer.

## Verify before you say it's done

Claiming something works means you ran it. The full set:

```bash
cd apps/api && uv run ruff check app/ && uv run pytest tests/ -q
cd apps/web && pnpm lint && pnpm exec tsc --noEmit && pnpm test
```

**Run the API suite even for a web-only change.** `apps/api/app/mcp/ui/display-block.html`
is a committed build artifact bundled from web components — the list is `SOURCES`
in `apps/mcp-ui/scripts/emit.mjs` (the roster components, `lib/datetime.ts`,
`lib/rosterTime.ts`, `roster/grid.ts`). Edit any of those without running:

```bash
pnpm --filter @norm/mcp-ui build
```

and `tests/test_mcp_ui.py` fails in CI with "display-block.html is STALE" — a
failure that is invisible from the web checks alone.

## You can test connector actions yourself — do it

Don't report a connector action as "unverified, needs a real environment". You
have one. The local API reads the **shared** config DB (so a spec action you just
synced is already live) and the **local** database, which holds a real LoadedHub
token. So a local call hits the real LoadedHub API.

```bash
# 1. Session — same credentials CI's E2E job uses (apps/e2e/run-local.sh)
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@norm.local","password":"changeme123"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 2. Which venue has credentials
curl -s http://localhost:8000/api/venues -H "Authorization: Bearer $TOKEN"

# 3. Run a READ action for real
curl -s -X POST http://localhost:8000/api/connector-specs/loadedhub/test \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"tool_action":"get_staff_roles","extracted_fields":{},"venue_id":"<venue-id>"}'
```

**Always run a new read action before believing its `response_transform`.** Field
names taken from documentation, from another codebase's types, or from a similar
endpoint are routinely wrong, and a wrong mapping fails *silently* — the field is
just missing. Two of five transforms written from Loaded's TypeScript types were
wrong on first contact with the real payload (`reason` was actually `note`; a
`leaveTypeName` that doesn't exist at all).

**For WRITE actions use dry-run, not test.** It renders the request — URL, headers,
body — without sending it, so you can check a body template without changing a
venue's data:

```bash
curl -s -X POST http://localhost:8000/api/connector-specs/loadedhub/dry-run \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"tool_action":"update_shift","extracted_fields":{...}}'
```

Only fire a real write when the side effect is understood and acceptable —
`publish_roster` notifies staff; a shift write mutates a live roster. The local
token points at a **real** LoadedHub venue, not a sandbox.

Environment notes: **testing** has no venues, so it cannot exercise connectors.
**Production** has the data but you don't have its admin credentials. Local is
the place to test.

## Quick Start (Codespaces)

```bash
./scripts/dev.sh   # starts postgres, runs migrations, launches API (8000) + Web (3000)
```

## Architecture

- **Frontend**: Next.js 16 (React 19, TypeScript) — `apps/web/`
- **Backend**: FastAPI (Python 3.12) — `apps/api/`
- **Database**: PostgreSQL 16
- **LLM**: Anthropic Claude (via `anthropic` SDK)
- **Infra**: GCP Cloud Run + Cloud SQL, Terraform in `infra/terraform/`

## Deployment Pipeline

### Automatic flow (push to main)

```
Push to main
  → CI (lint, tests, typecheck, docker build)
  → Build & push Docker images to Artifact Registry
  → Deploy to testing (testing.bettercallnorm.com)
  → Run E2E test suite
  → Deploy to staging (staging.bettercallnorm.com)
  → Deploy to production (bettercallnorm.com)   ← automatic
```

**Production deploys automatically.** Every green build ships all the way to
production — there is no manual gate. The gates are CI and the E2E suite against
testing; if either fails the pipeline stops before production. Migrations run
first, automatically (see below).

This is deliberate while Norm has no live end users. **When real users are on
it, reinstate a gate**: either drop `deploy-production` in
`.github/workflows/deploy.yml` back to `workflow_dispatch` only, or add a
required reviewer to the `production` GitHub environment (which pauses the job
for approval without any workflow change).

### Deploying a specific SHA / rolling back

The manual path still exists for pinning a build or rolling back:

```bash
# Ensure GITHUB_TOKEN doesn't override CLI auth:
unset GITHUB_TOKEN

gh workflow run deploy.yml -f environment=production -f image_tag=<git-sha>
```

Or GitHub Actions UI: Actions → Deploy → Run workflow → `production` → paste the SHA.

To roll back fast without the pipeline, point the Cloud Run service at a previous
image tag directly (images are tagged by git SHA in the `norm-testing` registry):

```bash
gcloud run services update norm-api-production \
  --project=norm-production-491101 --region=australia-southeast1 \
  --image=australia-southeast1-docker.pkg.dev/norm-testing/norm/norm-api:<git-sha> --quiet
# same for norm-web-production / norm-web
```

### Running migrations on production

**Migrations run automatically on every environment, including production** — the
deploy pipeline executes the `norm-migrate-<env>` Cloud Run job (e.g.
`norm-migrate-production`) before switching traffic to the new image, so schema
changes land ahead of the code that needs them. You do not normally need to do
anything.

The manual Cloud SQL proxy procedure below is a **fallback** — for when the
migrate job is missing/broken, or you need to inspect or repair schema state by
hand:

```bash
export PATH="$HOME/google-cloud-sdk/bin:$PATH"

# 1. Get DB password from Terraform state
cd infra/terraform
terraform init -reconfigure -backend-config="bucket=norm-tfstate-491101" -backend-config="prefix=production"
DB_PASSWORD=$(terraform show -json | python3 -c "
import sys, json
data = json.load(sys.stdin)
for r in data.get('values',{}).get('root_module',{}).get('child_modules',[]):
  for res in r.get('resources',[]):
    if res.get('type') == 'random_password':
      print(res.get('values',{}).get('result',''))
      break
")

# 2. Temporarily enable public IP
gcloud sql instances patch norm-production --project=norm-production-491101 --assign-ip --quiet
MY_IP=$(curl -s ifconfig.me)
gcloud sql instances patch norm-production --project=norm-production-491101 --authorized-networks="$MY_IP/32" --quiet
PUBLIC_IP=$(gcloud sql instances describe norm-production --project=norm-production-491101 --format="value(ipAddresses[0].ipAddress)")

# 3. Run migrations
cd /workspaces/norm/apps/api
DATABASE_URL="postgresql://norm:${DB_PASSWORD}@${PUBLIC_IP}:5432/norm" .venv/bin/python -m alembic upgrade head

# 4. Disable public IP
gcloud sql instances patch norm-production --project=norm-production-491101 --clear-authorized-networks --no-assign-ip --quiet
```

### Setting secrets on production

```bash
export PATH="$HOME/google-cloud-sdk/bin:$PATH"
echo -n "value" | gcloud secrets versions add SECRET_NAME --data-file=- --project=norm-production-491101

# Then restart API to pick up new secret:
gcloud run services update norm-api-production \
  --project=norm-production-491101 \
  --region=australia-southeast1 \
  --update-env-vars="DEPLOY_TIMESTAMP=$(date +%s)" --quiet
```

## Environments

| Environment | Domain | GCP Project | DB |
|---|---|---|---|
| **local** | localhost:3000 | — | Local Postgres (docker) |
| **testing** | testing.bettercallnorm.com | norm-testing | Cloud SQL (micro) |
| **staging** | staging.bettercallnorm.com | norm-staging | Cloud SQL (small) |
| **production** | bettercallnorm.com | norm-production-491101 | Cloud SQL (HA) |

## Key Configuration

- All config in `apps/api/app/config.py` (Pydantic BaseSettings)
- Secrets stored in GCP Secret Manager, injected as env vars to Cloud Run

### Centralized Config Database

All environments share a single config database for system-level configuration:

- **Setting**: `CONFIG_DATABASE_URL` in `apps/api/app/config.py`
- **Production**: Shared Cloud SQL instance `norm-config` in the `norm-production-491101` project
- **Tables**: `connector_specs`, `agent_configs`, `agent_connector_bindings`, `system_secrets`
- **Behavior**: All environments (local, testing, staging, production) read from the same config DB
- **Secrets**: Loaded at startup via `_load_system_secrets()` and injected into the application environment

## Testing

```bash
cd apps/api
uv run ruff check app/           # lint
uv run ruff format --check app/  # format check
uv run pytest tests/ -q          # ~690 tests, ~95s

cd apps/web
pnpm lint                        # ESLint (0 errors expected)
pnpm exec tsc --noEmit           # TypeScript check
pnpm test                        # vitest — pure logic (time math, grid geometry)

# E2E tests (requires dev servers running)
cd apps/e2e
./run-local.sh                   # fetch saved tests from local API, run them, report results
npx playwright test tests/foo.ts # run a specific generated spec file
```

**The E2E stage in CI is advisory, not a gate.** `.github/workflows/e2e-tests.yml`
sets `continue-on-error: true` on the test run, so the job reports success even
when tests fail and can never block a deploy — and the suite is currently a
single smoke test. Do not read a green pipeline as "E2E passed". Worth making a
real gate (drop the flag, add a few deterministic smoke tests) once Norm has
real users.

**What the tests are for.** Much of this suite exists because of specific
production incidents, and those tests carry docstrings saying so — an empty
`Bearer ` token reaching the wire, sales reading `$0` for a Saturday, venues
becoming undeletable. The two largest files exec the **real** consolidator code
from `config/consolidators/` under the real sandbox namespace, and are the only
thing standing between a config edit and a sandbox failure in production. Before
deleting a test, check whether it names an incident.

## Browser Access (Playwright MCP)

Claude has access to a headless Chromium browser via the Playwright MCP server (configured in `.mcp.json`).
Use it to visually verify UI changes on `http://localhost:3000`:

- Navigate to a page and take a screenshot to verify layout
- Click through user flows to test interactions
- Run `npx playwright test` in `apps/e2e/` for the full E2E suite

## Auth & Permissions

- **Platform admin**: `User.role = "admin"` — access to deployments, system config, connector specs
- **Org roles**: Owner, Manager, Team Member, Payroll Admin (stored in `roles` table)
- **Custom roles**: Created per-org with specific permission scopes
- **Permission check**: `require_permission("scope")` dependency in FastAPI
- 23 permission scopes across 8 categories (defined in `app/auth/permissions.py`)

## Key Files

| Area | Files |
|---|---|
| Auth | `app/auth/dependencies.py`, `app/auth/permissions.py`, `app/auth/security.py` |
| Config | `app/config.py`, `app/db/config_models.py` |
| Agents | `app/agents/base.py`, `app/agents/tool_loop.py`, `app/agents/router.py` |
| LLM | `app/interpreter/llm_interpreter.py` |
| Email | `app/services/email_service.py`, `app/templates/email/` |
| Deploy | `.github/workflows/deploy.yml`, `.github/workflows/deploy-env.yml` |
| Infra | `infra/terraform/main.tf`, `infra/terraform/modules/` |
