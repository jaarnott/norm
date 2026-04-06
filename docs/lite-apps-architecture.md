# Mini Apps Architecture Plan

## Context

Norm currently connects to external SaaS products (BambooHR, Deputy, Xero) via a spec-driven connector system. The goal is to build standalone lite apps (e.g., "HRLite", "RosterLite") that:

1. **Are completely independent products** — their own brand, domain, sign-up, billing. No visible association with Norm from the user's perspective.
2. **Have blast radius isolation** — a change in Norm cannot break HRLite, a change in HRLite cannot break RosterLite or Norm.
3. **Integrate deeply with Norm when a user connects both** — Norm can perform all tasks within HRLite via its existing ConnectorSpec system, just like it connects to BambooHR today.
4. **Can be fully separated** — if HRLite becomes very successful, it should be extractable into its own repo and potentially its own company with minimal friction.

**Key insight:** From Norm's perspective, HRLite is just another external service — like BambooHR but one we built. Norm connects via a ConnectorSpec with template-mode HTTP calls. No changes to Norm's agent system are needed.

---

## Recommended Architecture: Monorepo First, Extract When Shippable

### Strategy: Two Phases

**Phase A (Development):** Build in the Norm monorepo. One Codespace, atomic commits while building the shared SDK + first app together. Fastest way to iterate.

**Phase B (Ship):** Once a lite app is ready to ship, extract it to its own repo with its own Codespace. From that point on, it's a fully independent product.

### Monorepo Structure (During Development)

```
/workspaces/norm/
  apps/
    api/                    # Norm core API (existing, unchanged)
    web/                    # Norm core frontend (existing, unchanged)
    hrlite-api/             # HRLite backend (new)
    hrlite-web/             # HRLite frontend (new, own branding)
  packages/
    app-sdk/                # Shared Python package (NOT Norm-branded)
  infra/
    terraform/
      modules/
        lite-app/           # Reusable Terraform module for lite app infra
        cloud-run/          # Existing (Norm core)
      environments/
        hrlite-production/  # Own GCP project, own state
        hrlite-staging/
        hrlite-testing/
  .github/
    workflows/
      deploy.yml            # Norm CI/CD (existing)
      ci-hrlite.yml         # HRLite CI/CD (path-filtered, independent)
  scripts/
    dev.sh                  # Starts Postgres + Norm API + Norm Web (existing)
    dev-hrlite.sh           # Starts Postgres + HRLite API + HRLite Web
    dev-all.sh              # Starts everything (for integration testing)
```

### Post-Extraction Structure (Steady State)

| Repo | Codespace | What's in it |
|------|-----------|-------------|
| `yourorg/norm` | Norm development | Norm API + Web + infra + tests |
| `yourorg/hrlite` | HRLite development | HRLite API + Web + infra + tests |
| `yourorg/rosterlite` | RosterLite development | Same pattern |
| `yourorg/app-sdk` | SDK development (rare) | Shared Python package |

Each repo has its own `devcontainer.json`, `./scripts/dev.sh`, Docker Compose, etc. Open a Codespace on `hrlite` to work on HRLite — clean, focused, just HRLite code.

### Rules That Ensure Independence (Enable Clean Extraction)

1. **Apps never import from each other.** `apps/hrlite-api` depends on `packages/app-sdk` but never on `apps/api`.
2. **The shared SDK has no Norm branding or Norm-specific logic.** It's a generic FastAPI app toolkit.
3. **Each app has its own GCP project, domain, and Stripe account.** Nothing is shared at the infrastructure level.

---

## 1. Local Development in the Monorepo

### Ports

```
Postgres (Docker)    → localhost:5432  (one container, separate databases)
Norm API             → localhost:8000
Norm Web             → localhost:3000
HRLite API           → localhost:8001
HRLite Web           → localhost:3001
```

### Dev Scripts

| Command | What it starts | When you use it |
|---------|---------------|-----------------|
| `./scripts/dev.sh` | Postgres + Norm API (8000) + Norm Web (3000) | Working on Norm only |
| `./scripts/dev-hrlite.sh` | Postgres + HRLite API (8001) + HRLite Web (3001) | Working on HRLite only |
| `./scripts/dev-all.sh` | Postgres + all services | Testing Norm ↔ HRLite integration |

**Most of the time you only run the product you're working on.** You only spin up everything when testing the Norm ↔ HRLite integration (Norm's agent calling HRLite's API).

### Docker Compose

One Postgres container, multiple databases. Each app connects to its own database — just a different database name in the connection string:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    ports: ["5432:5432"]
    environment:
      POSTGRES_USER: norm
      POSTGRES_PASSWORD: norm
      POSTGRES_DB: norm
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./scripts/init-databases.sql:/docker-entrypoint-initdb.d/init.sql
      # init.sql creates additional databases: CREATE DATABASE hrlite;
```

- Norm connects to `postgresql://norm:norm@localhost:5432/norm`
- HRLite connects to `postgresql://norm:norm@localhost:5432/hrlite`
- Each app runs its own Alembic migrations against its own database

### Local Integration Testing

When running `dev-all.sh`, Norm's ConnectorConfig for HRLite points to `http://localhost:8001`:

```json
{
  "base_url": "http://localhost:8001",
  "api_key": "dev-test-key"
}
```

The same ConnectorSpec works everywhere — only the credentials (base_url + api_key) differ per environment.

---

## 2. Shared SDK: `packages/app-sdk`

A generic FastAPI application toolkit. Contains no Norm business logic, no Norm branding, no Norm-specific concepts.

| Module | What it provides |
|--------|-----------------|
| `auth` | JWT creation/validation, `get_current_user` dependency, API key validation, service-to-service token validation |
| `tenancy` | Base Organization/User SQLAlchemy mixins, membership patterns, venue-aware scoping |
| `middleware` | CORS, rate limiting, request tracing, Sentry integration, metrics |
| `api` | Standard response envelope `{"data": ..., "meta": {...}}`, error format, pagination, health check factory |
| `billing` | Stripe subscription helpers, usage tracking primitives |

**In the monorepo:** Apps depend on it via local path (`packages/app-sdk`).
**After extraction:** Published to GitHub Packages. Apps pin to a specific version and bump when they choose to — an SDK release never forces a deploy.

---

## 3. Lite App Service Structure

Each lite app is a fully self-contained product:

```
apps/hrlite-api/
  Dockerfile                 # Standalone multi-stage build
  pyproject.toml             # Depends on app-sdk
  alembic/                   # Own migrations, own DB
  app/
    main.py                  # FastAPI app — "HRLite" branding
    config.py                # Own Pydantic BaseSettings
    db/
      engine.py              # Own database connection
      models.py              # Job, Application, Candidate, etc.
    routers/
      health.py
      auth.py                # Uses app-sdk auth primitives
      jobs.py
      applications.py
      candidates.py
    services/
      ...
  tests/                     # Own test suite

apps/hrlite-web/
  Dockerfile
  package.json               # NO dependency on apps/web
  app/                       # Own Next.js app — own branding, own domain
    layout.tsx               # HRLite branding, not Norm
    ...
```

### Database: Fully separate

Each lite app has its own database in its own GCP project:

| Product | GCP Project | Database | Domain |
|---------|-------------|----------|--------|
| Norm | norm-production-491101 | norm | bettercallnorm.com |
| HRLite | hrlite-production | hrlite | hrlite.com (example) |
| RosterLite | rosterlite-production | rosterlite | rosterlite.com (example) |

Initially, lite app databases can be on shared Cloud SQL instances for cost. The Terraform module abstracts this so switching to dedicated instances is a config change.

---

## 4. How Norm Integrates (When a User Connects Both)

From Norm's perspective, HRLite is treated identically to BambooHR. The user connects HRLite as a "connector" in Norm's settings, providing their HRLite API credentials.

### ConnectorSpec (stored in Norm's config DB)

```json
{
  "connector_name": "hrlite",
  "display_name": "HRLite",
  "category": "hr",
  "execution_mode": "template",
  "auth_type": "bearer",
  "base_url_template": "{{creds.base_url}}/api/v1",
  "credential_fields": ["api_key", "base_url"],
  "tools": [
    {
      "action": "list_jobs",
      "method": "GET",
      "path_template": "/jobs?status={{status}}",
      "description": "List all job postings",
      "required_fields": [],
      "response_transform": { "enabled": true, "root": "data" }
    },
    {
      "action": "create_job",
      "method": "POST",
      "path_template": "/jobs",
      "request_body_template": "{\"title\": \"{{title}}\", \"department\": \"{{department}}\"}",
      "required_fields": ["title", "department"]
    },
    {
      "action": "list_applications",
      "method": "GET",
      "path_template": "/jobs/{{job_id}}/applications",
      "description": "List applications for a job",
      "required_fields": ["job_id"]
    }
  ]
}
```

### Connection flow (from user's perspective)

1. User already uses HRLite standalone (posts jobs, manages applications)
2. User signs up for Norm separately
3. In Norm's connector settings, user adds "HRLite" connector with their HRLite API key
4. Norm's HR agent now has HRLite tools available in its tool loop
5. User can say "Show me all open positions in HRLite" and Norm calls the API

This is identical to how a user connects BambooHR today. Norm doesn't "know" it's a first-party app — it's just another connector.

### End-to-end flow

1. User tells Norm: "Post a new kitchen manager role"
2. HR agent tool loop calls `hrlite.create_job`
3. `spec_executor.py` renders Jinja2 template → HTTP POST to `https://api.hrlite.com/api/v1/jobs`
4. Request includes user's HRLite API key as Bearer token
5. HRLite API validates, creates job, returns result
6. Tool loop processes result, responds to user

---

## 5. Authentication Architecture

### User-facing auth (each app independent)

Each lite app manages its own user authentication. Two options:

**Option A: Shared identity provider (recommended initially)**
Use Firebase Auth / Auth0 / Clerk across all products. Users don't see this — each product has its own login page and branding, but the underlying identity is shared. This enables:
- Single email = single identity across products (convenient for users who happen to use multiple)
- No visible coupling (each product's UI is fully independent)
- Easy to migrate away from if an app is extracted (switch to another auth provider or build your own)

**Option B: Fully independent auth per app**
Each app has its own user table and auth system (via `app-sdk` auth module). No shared identity.
- Maximum independence
- Account linking requires explicit OAuth integration if needed later
- More work per app but simpler overall

**Recommendation:** Start with Option A for development speed, but design so that switching to Option B per-app is straightforward (the `app-sdk` auth module should support both patterns).

### Norm-to-lite-app auth (API integration)

When a Norm user connects HRLite in their Norm settings, they provide an **HRLite API key**. This is a standard API key that HRLite generates for any user (not just Norm users). The key is stored in Norm's `ConnectorConfig` table as a credential, identical to how BambooHR API keys are stored today.

HRLite doesn't need to know about Norm at all. It just sees an API request with a valid API key.

---

## 6. Billing (Fully Independent)

Each product has its own Stripe account, pricing, and subscription:

| Product | Billing | Stripe Account |
|---------|---------|---------------|
| Norm | Own subscription, AI token-based | norm-stripe |
| HRLite | Own subscription, seat-based or flat rate | hrlite-stripe |
| RosterLite | Own subscription | rosterlite-stripe |

No bundle discounts or cross-product billing complexity. Each product is financially independent. If bundling is desired later, it can be handled via marketing (promo codes) rather than technical coupling.

---

## 7. Infrastructure: Reusable Terraform Module

### `infra/terraform/modules/lite-app/`

```hcl
module "hrlite" {
  source      = "./modules/lite-app"
  app_name    = "hrlite"
  gcp_project = "hrlite-production"
  region      = "australia-southeast1"
  environment = "production"
  api_image   = "...hrlite-api:${var.image_tag}"
  web_image   = "...hrlite-web:${var.image_tag}"
  domain      = "hrlite.com"
  db_tier     = "db-f1-micro"   # start small
}
```

Creates: own Cloud Run services, own database, own secrets, own DNS, own monitoring. Completely independent from Norm's infrastructure.

### GCP Project Isolation

Each lite app gets its own GCP project. This provides:
- Billing isolation (see exactly what each product costs to run)
- IAM isolation (HRLite team can't accidentally affect Norm infra)
- Clean separation for extraction (just transfer the GCP project)

---

## 8. CI/CD: Path-Filtered in Monorepo, Fully Separate After Extraction

### During Monorepo Phase

Each app has its own CI workflow, triggered only by changes to its directory:

```yaml
# .github/workflows/ci-hrlite.yml
name: CI - HRLite
on:
  push:
    paths:
      - 'apps/hrlite-api/**'
      - 'apps/hrlite-web/**'
      - 'packages/app-sdk/**'
```

- Changes to `apps/api/` (Norm) do NOT trigger HRLite CI
- Changes to `apps/hrlite-api/` do NOT trigger Norm CI
- Changes to `packages/app-sdk/` trigger CI for ALL apps (the one shared surface)
- Each app deploys to its own GCP project independently

**Releasing is completely separate even in the monorepo.** A PR that only touches HRLite code will only build, test, and deploy HRLite.

### After Extraction

Each repo has its own CI/CD — naturally independent. The `app-sdk` is a versioned package dependency. Bumping the SDK version is an explicit choice per app, not an automatic trigger.

---

## 9. Release Cycle (Completely Separate Per Product)

Each product has its own testing → staging → production pipeline. Releasing HRLite does not touch Norm. Releasing Norm does not touch HRLite. This is true both during the monorepo phase (via path filters) and after extraction (naturally).

### Per-Product Environments

| Product | Testing | Staging | Production | GCP Project |
|---------|---------|---------|------------|-------------|
| **Norm** | testing.bettercallnorm.com | staging.bettercallnorm.com | bettercallnorm.com | norm-production-491101 |
| **HRLite** | testing.hrlite.com | staging.hrlite.com | hrlite.com | hrlite-production |
| **RosterLite** | testing.rosterlite.com | staging.rosterlite.com | rosterlite.com | rosterlite-production |

Each has its own: GCP project, CI/CD pipeline, database (own Cloud SQL), secrets (own Secret Manager), domain and SSL, Artifact Registry.

### Release Flow Per App

```
Push to main (touching only hrlite paths)
  → CI: lint, test, typecheck, docker build (HRLite only)
  → Build & push images to HRLite's Artifact Registry
  → Deploy to testing.hrlite.com
  → Run E2E tests
  → Deploy to staging.hrlite.com
  → (manual trigger) Deploy to hrlite.com
```

This mirrors Norm's existing pipeline. The reusable Terraform module (`modules/lite-app/`) and reusable GitHub Actions workflow make standing up a new product's pipeline a config exercise.

---

## 10. Standard API Contract for Lite Apps

All lite apps follow the same API patterns (enforced by `app-sdk`):

```
# Standard REST contract
GET    /api/v1/{resources}?page=1&per_page=20&filter[status]=active
POST   /api/v1/{resources}
GET    /api/v1/{resources}/{id}
PUT    /api/v1/{resources}/{id}
DELETE /api/v1/{resources}/{id}

# Response envelope
{"data": [...], "meta": {"page": 1, "per_page": 20, "total": 42}}

# Error format
{"error": {"code": "NOT_FOUND", "message": "Job not found"}}

# API key auth
Authorization: Bearer {api_key}
```

This standard contract means:
- ConnectorSpecs for lite apps are predictable and can be auto-generated from OpenAPI schemas
- Norm's agent gets consistent, LLM-friendly responses
- Each app can evolve its API independently as long as versioned endpoints maintain compatibility

---

## 11. Extraction Playbook (When a Lite App is Ready to Ship)

### Step 1: Create new repo (~1 day)
```bash
# Copy app code
cp -r apps/hrlite-api/ ~/hrlite/apps/api/
cp -r apps/hrlite-web/ ~/hrlite/apps/web/
cp -r packages/app-sdk/ ~/hrlite/packages/app-sdk/

# Copy infra
cp -r infra/terraform/modules/lite-app/ ~/hrlite/infra/terraform/modules/
cp -r infra/terraform/environments/hrlite-*/ ~/hrlite/infra/terraform/environments/
```

### Step 2: Set up independent repo (~1 day)
- Add `devcontainer.json` for Codespace support
- Add `./scripts/dev.sh` (Postgres + HRLite API + HRLite Web)
- Add `docker-compose.yml` (Postgres only)
- Change `app-sdk` dependency from local path to published GitHub Package
- Copy and adapt CI workflow from monorepo

### Step 3: Verify (~1 day)
- Run full test suite in new repo
- Deploy from new repo's CI
- Verify API, frontend, and database all work
- Open a Codespace on new repo — confirm dev experience works

### Step 4: Remove from monorepo
- Delete `apps/hrlite-api/`, `apps/hrlite-web/`, `infra/terraform/environments/hrlite-*/`
- Remove HRLite CI workflow
- Norm's `hrlite` ConnectorSpec continues to work — it's just an HTTP connector pointing at HRLite's domain

**Total extraction effort: ~2-3 days.** The key enabler is that HRLite has zero code dependencies on Norm.

### What doesn't change after extraction
- HRLite's domain, database, GCP project, users — all unchanged
- Norm's ConnectorSpec for HRLite — still works, it's just an HTTP endpoint
- Users of both products — unaffected, their API key connection still works

---

## 12. Blast Radius Matrix

| Change | Norm | HRLite | RosterLite |
|--------|------|--------|------------|
| Norm code change | Affected | Unaffected | Unaffected |
| HRLite code change | Unaffected | Affected | Unaffected |
| RosterLite code change | Unaffected | Unaffected | Affected |
| `app-sdk` change | CI runs all | CI runs all | CI runs all |
| Norm DB migration | Affected | Unaffected | Unaffected |
| HRLite DB migration | Unaffected | Affected | Unaffected |
| Norm deploy | Deploys | No deploy | No deploy |
| HRLite deploy | No deploy | Deploys | No deploy |
| HRLite extracted to own repo | No impact | Now independent | Unaffected |

---

## Implementation Phases

### Phase 1: Create Shared SDK + First Lite App in Monorepo (6-8 weeks)
1. Create `packages/app-sdk/` — extract generic auth, tenancy, middleware from `apps/api/`
2. Refactor `apps/api/` to use `app-sdk` — all 228 existing tests must pass
3. Create `apps/{app}-api/` + `apps/{app}-web/` using `app-sdk`
4. Design data model, REST API, standalone frontend with own branding
5. Create `infra/terraform/modules/lite-app/` reusable module
6. Create `scripts/dev-{app}.sh` and `scripts/dev-all.sh`
7. Add `scripts/init-databases.sql` to create the app database in Docker Postgres
8. Create path-filtered CI workflow
9. Write test suite
10. Create GCP project, deploy to testing/staging

### Phase 2: Norm Integration (1 week)
1. Define ConnectorSpec in Norm's config DB
2. Create `AgentConnectorBinding` linking it to the relevant agent
3. Test locally with `dev-all.sh`: Norm agent calls lite app API on localhost:8001
4. Test on staging: Norm connects to staging.{app}.com

### Phase 3: Identity, Auth & Billing (2-3 weeks)
1. Build API key generation in the lite app (for Norm and third-party integrations)
2. Choose auth approach (shared identity provider or independent per app)
3. Set up own Stripe account and billing

### Phase 4: Extract to Own Repo + Ship (1 week)
1. Publish `app-sdk` to GitHub Packages
2. Create own repo with `devcontainer.json`, `dev.sh`, CI/CD
3. Create `app-sdk` repo
4. Remove lite app code from Norm monorepo
5. Verify: Codespace works, Norm tests pass, integration still works
6. Deploy production

### Phase 5: Next Lite Apps (3-4 weeks per app)
Each new app is built in the monorepo during development, then extracted when shippable. The `app-sdk` + `modules/lite-app/` + CI template make each subsequent app faster. First app repo serves as the reference implementation.

---

## Key Files (Norm Side — No Changes Needed to Agent System)

| File | Role |
|------|------|
| `apps/api/app/connectors/spec_executor.py` | Executes HTTP calls to external APIs — lite apps use this same path |
| `apps/api/app/db/config_models.py` | ConnectorSpec model — defines the integration contract |
| `apps/api/app/agents/tool_loop.py` | Agent tool loop — already handles HTTP connectors, no changes needed |
| `apps/api/app/agents/prompt_builder.py` | Builds tool definitions from ConnectorSpecs — auto-includes lite app tools |
| `infra/terraform/modules/cloud-run/main.tf` | Template for the new `lite-app` Terraform module |
| `.github/workflows/deploy-env.yml` | Template for reusable lite app deployment workflow |
| `scripts/dev.sh` | Template for lite app dev scripts |

---

## Verification

1. **Phase 1 (SDK + Lite App)**:
   - `uv run pytest tests/ -v` — all 228 Norm tests pass after SDK refactoring
   - Lite app has its own passing test suite
   - `./scripts/dev-{app}.sh` starts the app independently on ports 8001/3001
   - `./scripts/dev-all.sh` starts everything, all services accessible
   - No Norm branding visible anywhere in the lite app
2. **Phase 2 (Integration)**:
   - Norm user adds lite app connector with API key
   - Norm agent can perform all CRUD operations via tool loop
   - Works identically to BambooHR connector
3. **Phase 4 (Extraction)**:
   - Lite app Codespace works independently
   - Lite app deploys from its own repo's CI
   - Norm is completely unaffected by extraction
   - Norm �� lite app integration still works (ConnectorSpec points to same domain)
