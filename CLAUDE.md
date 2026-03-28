# Norm — AI Operations Platform for Hospitality

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
  → CI (lint, 228 tests, typecheck, docker build)
  → Build & push Docker images to Artifact Registry
  → Deploy to testing (testing.bettercallnorm.com)
  → Run E2E test suite
  → Deploy to staging (staging.bettercallnorm.com)
```

### Production deploy (manual)

Production requires a manual trigger. Use the GitHub CLI:

```bash
# First, ensure GITHUB_TOKEN doesn't override CLI auth:
unset GITHUB_TOKEN

# Deploy the latest SHA to production:
gh workflow run deploy.yml -f environment=production -f image_tag=$(git rev-parse HEAD)
```

Or from GitHub Actions UI: Actions → Deploy → Run workflow → select `production` → paste the git SHA.

### Running migrations on production

Migrations run automatically on testing/staging via the deploy pipeline. For production, migrations must be run manually via Cloud SQL proxy:

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
uv run ruff check app/          # lint
uv run ruff format --check app/  # format check
uv run pytest tests/ -v          # 228 tests

cd apps/web
pnpm lint                        # ESLint (0 errors expected)
pnpm exec tsc --noEmit          # TypeScript check
```

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
