# Norm

AI operations assistant for hospitality.

Norm interprets natural-language requests, routes them to the correct
specialist handler, asks follow-up questions when information is missing,
and presents structured draft actions for human approval.

---

## Quick start (Codespaces)

### 1. Start Postgres

```bash
docker compose up -d
```

### 2. API setup

```bash
cd apps/api
uv venv                                          # one-time
uv pip install -e ".[dev]"                        # one-time
.venv/bin/python -m alembic upgrade head          # run migrations
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Frontend

```bash
cd apps/web
pnpm install                                      # one-time
pnpm dev
```

### 4. Run both together

```bash
./scripts/dev.sh
```

Starts the API on port 8000 and the frontend on port 3000.
Swagger docs available at http://localhost:8000/docs.

### 5. Default login

The migration seeds a default admin account:

| Email | Password | Role |
|-------|----------|------|
| `admin@norm.local` | `changeme123` | admin |

The first user registered via the UI also receives the `admin` role. Subsequent registrations are assigned the `manager` role.

---

## Authentication

Norm uses email/password authentication with JWT bearer tokens and two roles:

| Role | Permissions |
|------|-------------|
| **admin** | Full access including connector management, agent config, and connector specs |
| **manager** | Can create tasks, approve/reject, but cannot manage connectors or agents |

- All API endpoints (except `/health` and `/api/auth/*`) require a valid `Authorization: Bearer <token>` header.
- Tokens expire after 24 hours.
- The frontend stores the token in `localStorage` and auto-attaches it to all requests. A 401 response clears the token and forces re-login.

### Auth endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/register` | Create account (first user = admin, rest = manager) |
| POST | `/api/auth/login` | Sign in, returns JWT + user info |
| GET | `/api/auth/me` | Validate token, returns current user |

---

## Interpretation

Norm uses LLM interpretation (Anthropic Claude) for all message understanding
and routing. The `ANTHROPIC_API_KEY` environment variable is **required** --
the app will warn at startup if it is not set (it can also be configured at
runtime via the Settings panel).

### What interpreters do vs do NOT do

| Interpreters are responsible for | Backend services handle |
|----------------------------------|------------------------|
| Detecting domain (procurement, HR, reports) | Creating/updating tasks in Postgres |
| Extracting entities (venue, product, qty, name, role) | Resolving entities against DB records |
| Identifying missing fields | Managing order/HR/report lifecycle and statuses |
| Generating clarification questions | Persisting messages and conversation state |
| Producing a confidence score | Approvals, rejections, submissions |
| Summarizing the request | All external integrations |

### LLM call logging

Every LLM call (routing, interpretation, spec generation) is logged to the `llm_calls` table with:
- System and user prompts sent
- Raw response and parsed JSON
- Model used, duration, and success/error status
- Associated task ID (back-filled after task creation)

This data is included in task detail API responses under the `llm_calls` key.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | - | **Required.** Anthropic API key for LLM interpretation |
| `LLM_INTERPRETER_MODEL` | `claude-sonnet-4-20250514` | Model override for interpretation |
| `DATABASE_URL` | `postgresql://norm:norm@localhost:5432/norm` | Postgres connection |
| `JWT_SECRET` | `dev-secret-change-in-production` | Secret key for signing JWT tokens |
| `BAMBOOHR_SUBDOMAIN` | - | BambooHR company subdomain (enables real HR connector) |
| `BAMBOOHR_API_KEY` | - | BambooHR API key (required with subdomain) |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | API URL for the frontend |

---

## Connector Specs

Connector specs define how Norm talks to external APIs as configuration rather than code. Each spec declares:

- **Auth type** -- bearer, api_key_header, basic, or oauth2
- **Base URL template** -- Jinja2 template with credential substitution (e.g. `https://{{subdomain}}.bamboohr.com/api/gateway.php/{{subdomain}}/v1`)
- **Operations** -- a list of actions (e.g. `create_employee`, `check_stock`) each with HTTP method, path template, field mappings, request body template, and expected status codes
- **Credential fields** -- what secrets the connector needs (rendered in the Connectors settings tab)
- **Execution mode** -- `template` (deterministic Jinja2 rendering) or `agent` (LLM generates the HTTP request from API docs)

### Seeded specs

| Spec | Category | Auth | Operations |
|------|----------|------|------------|
| BambooHR | hr | basic | create_employee, terminate_employee |
| Deputy | hr | bearer | create_roster, list_rosters |
| Bidfood | procurement | api_key_header | create_order, check_stock |
| LoadedHub | hr | oauth2 | create_roster, list_rosters, create_shift, update_shift, delete_shift |

### AI-assisted spec generation

`POST /api/connector-specs/generate` accepts API documentation text and uses Claude to produce a complete spec JSON. The generated spec can be reviewed and saved via the UI.

---

## Agent Configuration

Each domain agent (procurement, HR, reports, router) has an admin-configurable profile:

- **System prompt** -- editable with a "Reset to Default" option that reverts to the hardcoded prompt
- **Description** -- short summary shown in the UI
- **Connector bindings** -- which connectors the agent can use, with per-capability toggles

### Capability sync

Binding capabilities are merged at read time with the operations defined in the matching `ConnectorSpec`. When new operations are added to a spec, they automatically appear as unchecked capabilities in the agent's binding. Existing capability enabled/disabled states are preserved. Connector labels are read from `ConnectorSpec.display_name` rather than a hardcoded map.

The `available_connectors` field in the agent response lists specs matching the agent's category that aren't yet bound, powering an "Add Connector" dropdown in the UI.

---

## OAuth 2.0

Norm supports the OAuth 2.0 Authorization Code flow for connectors that require it (e.g. LoadedHub).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/oauth/authorize/{connector}` | Start OAuth flow, returns authorization URL |
| GET | `/api/oauth/callback` | Provider callback (no auth, validates state) |
| GET | `/api/oauth/status/{connector}` | Check connection status and token expiry |
| POST | `/api/oauth/disconnect/{connector}` | Revoke stored tokens |

Tokens are stored in `ConnectorConfig` and automatically refreshed (with a 60-second buffer) during spec execution. The UI opens the authorization URL in a popup and listens for a `postMessage` callback on completion.

---

## Demo script

### 1. Login

Open the app -- you'll see a login form. Sign in with `admin@norm.local` / `changeme123`, or register a new account.

### 2. Procurement -- one-shot

```
> Order 3 cases of Jim Beam for La Zeppa
```
Full draft order, status **awaiting approval**.

### 3. Procurement -- clarification loop

```
> Order 3 cases of Jim Beam
  Norm: "I need a bit more info -- which venue?"
> La Zeppa
  Norm: "Got it. Draft order is ready for your approval."
```

### 4. HR -- clarification loop

```
> New employee starting Monday at La Zeppa
  Norm: "I still need the employee's name and what role."
> Sarah Jones, bartender
  Norm: "Thanks. Employee setup is ready for your approval."
```

### 5. Reports

```
> Show me a sales summary for La Zeppa
```
Generates a report with mock data, status **awaiting approval**.

### 6. Approval flow

1. Click **Approve** -- status becomes **approved**, approval record saved with user identity.
2. Click **Submit** -- connector runs, status becomes **submitted**.
   Confirmation shows order reference (e.g. `SUP-20260314-a1b2`) and connector status.
3. Click **Reject** -- status becomes **rejected**, approval record saved.

If the connector fails the task stays **approved** and can be retried.

### 7. Role-based access

- Sign in as admin -- Settings tab visible, can manage connectors, agents, and specs.
- Sign in as manager -- Settings tab hidden, PUT/DELETE on connectors/agents returns 403.

---

## Architecture

```
User message
      |
      v
[Auth Layer]  <-- JWT Bearer token validation
      |
      v
[LLM Router]  <-- Anthropic Claude (logged to llm_calls)
      |
      v
InterpretationResult {
  domain, intent, confidence,
  extracted_fields, missing_fields,
  clarification_needed, clarification_question,
  is_followup
}
      |
      v
[Supervisor]  -- routes to domain agent
      |
      +---> [ProcurementAgent]  -- order_service -> Task, Order, OrderLine
      +---> [HrAgent]           -- hr_service -> Task, HrSetup
      +---> [ReportsAgent]      -- report planner + mock tools -> Task
      |
      v  (on submit)
[Spec Executor]  -- template or agent mode
      |
      +-- Template mode: Jinja2 sandbox renders HTTP request from extracted fields
      +-- Agent mode: LLM generates HTTP request from API docs
      |
      v
[Connector]  -- adapter per supplier/system (spec-driven or legacy mock/real)
      |
      v
[IntegrationRun logged]  -- rendered request (auth redacted), response, status, duration
      |
      v
[Approval record saved]  -- user email, user_id, action, timestamp
      |
      v
API response (task dict with conversation, status, entities, llm_calls)
```

---

## API endpoints

All endpoints except `/health` and `/api/auth/*` require authentication.

### Auth

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/auth/register` | None | Create account, returns JWT |
| POST | `/api/auth/login` | None | Sign in, returns JWT |
| GET | `/api/auth/me` | Bearer | Current user info |

### Tasks

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/messages` | Bearer | Send a message; returns task with conversation |
| GET | `/api/tasks` | Bearer | List tasks scoped to current user |
| GET | `/api/tasks/{id}` | Bearer | Task detail |
| POST | `/api/tasks/{id}/approve` | Bearer | Approve a task |
| POST | `/api/tasks/{id}/reject` | Bearer | Reject a task |
| POST | `/api/tasks/{id}/submit` | Bearer | Submit an approved task |

### Orders

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/orders` | Bearer | List orders scoped to current user |
| GET | `/api/orders/{id}` | Bearer | Order detail |
| POST | `/api/orders/{id}/approve` | Bearer | Approve an order |
| POST | `/api/orders/{id}/reject` | Bearer | Reject an order |
| POST | `/api/orders/{id}/submit` | Bearer | Submit an approved order |

### Reference data

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/venues` | Bearer | List known venues |

### Connectors (admin only for mutations)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/connectors` | Bearer | List available connectors |
| PUT | `/api/connectors/{name}` | Admin | Create/update connector config |
| DELETE | `/api/connectors/{name}` | Admin | Remove connector config |
| POST | `/api/connectors/{name}/test` | Admin | Test connector credentials |

### Connector Specs (admin only)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/connector-specs` | Bearer | List all specs |
| POST | `/api/connector-specs` | Admin | Create a new spec |
| GET | `/api/connector-specs/{name}` | Bearer | Get spec detail |
| PUT | `/api/connector-specs/{name}` | Admin | Update a spec |
| DELETE | `/api/connector-specs/{name}` | Admin | Delete a spec |
| POST | `/api/connector-specs/{name}/dry-run` | Admin | Preview rendered request without executing |
| POST | `/api/connector-specs/{name}/test` | Admin | Execute a test request against the real API |
| POST | `/api/connector-specs/generate` | Admin | AI-generate spec from API docs |

### Agents (admin only)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/agents` | Admin | List all agents with bindings and available connectors |
| GET | `/api/agents/capabilities` | Bearer | Summary of enabled capabilities across all agents |
| GET | `/api/agents/{slug}` | Admin | Get agent config + bindings |
| PUT | `/api/agents/{slug}` | Admin | Update agent prompt, description, or display name |
| POST | `/api/agents/{slug}/reset-prompt` | Admin | Revert to hardcoded default prompt |
| GET | `/api/agents/{slug}/bindings` | Admin | List connector bindings |
| PUT | `/api/agents/{slug}/bindings/{connector}` | Admin | Upsert binding with capabilities |
| DELETE | `/api/agents/{slug}/bindings/{connector}` | Admin | Remove binding |

### OAuth

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/oauth/authorize/{connector}` | Bearer | Start OAuth flow, returns authorization URL |
| GET | `/api/oauth/callback` | None | OAuth provider callback |
| GET | `/api/oauth/status/{connector}` | Bearer | Check OAuth connection status |
| POST | `/api/oauth/disconnect/{connector}` | Bearer | Revoke stored OAuth tokens |

### Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | None | Health check |

---

## Frontend

Built with **Next.js 16** and **React 19** (TypeScript). Three-column layout with auth gate:

1. **Login/Register** -- shown when not authenticated
2. **Sidebar** -- domain/agent navigation with task counts, user avatar, role badge, logout
3. **Task list** -- filterable list of tasks with status badges
4. **Task detail** -- tabbed view (Conversation, Details, Activity)

Key components:

| Component | Purpose |
|-----------|---------|
| `LoginForm` | Email/password login and registration |
| `Sidebar` | Domain navigation, user info, admin-only settings, logout |
| `TaskList` | Filterable task list with selection |
| `TaskCard` | Task preview with status badge and delete |
| `TaskDetail` | Full task view with Conversation / Details / Activity tabs |
| `ActivityTimeline` | Status transition history |
| `SubmissionConfirmation` | Order reference, connector status, retry on failure |
| `HomePanel` | Message input when no task is selected |
| `RoutingIndicator` | Shows interpretation routing status |
| `SettingsPanel` | Tabbed admin panel: Connectors, Agents, Connector Specs |
| `ConnectorSpecsPanel` | Spec CRUD with dry-run, test, and AI generation |

### Settings tabs

The Settings panel (admin only) has three tabs:

- **Connectors** -- configure credentials for each connector (API keys, OAuth connections)
- **Agents** -- edit system prompts, descriptions, and toggle per-capability connector bindings; add new connector bindings from available specs
- **Connector Specs** -- create/edit/delete spec definitions, dry-run templates, test against real APIs, or generate specs from API docs using AI

### Auth flow

- On mount, checks `localStorage` for an existing token and validates via `GET /api/auth/me`
- If invalid or missing, renders the `LoginForm`
- On successful login/register, stores token and user in `localStorage`
- All API calls go through `apiFetch()` which auto-attaches the Bearer token
- 401 responses clear the token and force re-login
- Logout clears stored credentials and resets app state

---

## Project structure

```
apps/
  web/                          Next.js frontend (React 19, TypeScript)
    app/
      page.tsx                  Main page with auth gate + three-column layout
      types.ts                  Shared type definitions
      lib/
        api.ts                  apiFetch() helper with auto Bearer token
      components/
        auth/
          LoginForm.tsx         Login/register form
        layout/
          Sidebar.tsx           Navigation, user info, logout
        home/
          HomePanel.tsx         Welcome screen with message input
        tasks/
          TaskList.tsx          Filterable task list
          TaskCard.tsx          Task preview card
          TaskDetail.tsx        Full task view with tabs
        routing/
          RoutingIndicator.tsx  Shows domain routing animation
        settings/
          SettingsPanel.tsx     Tabbed admin panel (Connectors, Agents, Specs)
          ConnectorSpecsPanel.tsx  Spec editor with dry-run, test, AI generate
  api/
    alembic/                    Database migrations
    tests/
      test_llm_interpreter.py   LLM response parsing tests
      test_bamboohr_connector.py BambooHR connector + registry tests
    app/
      main.py                   FastAPI app entry point
      auth/                     Authentication package
        security.py             Password hashing (bcrypt) + JWT (python-jose)
        dependencies.py         get_current_user, require_role() FastAPI deps
        schemas.py              Auth request/response Pydantic models
      interpreter/
        llm_interpreter.py      Anthropic Claude interpreter + LlmCall logging
      db/
        engine.py               SQLAlchemy engine + session
        models.py               ORM models (User, Task, Order, HrSetup, LlmCall,
                                 ConnectorSpec, AgentConfig, AgentConnectorBinding, etc.)
      agents/
        base.py                 Abstract domain agent interface
        router.py               LLM-based message routing
        registry.py             Agent lookup by domain
        procurement/            Procurement domain agent + context + prompt
        hr/                     HR domain agent + context + prompt
        reports/                Reports domain agent + context + planner + tools
      routers/
        auth.py                 POST /api/auth/register, login, GET /api/auth/me
        messages.py             POST /api/messages (auth required)
        tasks.py                Task lifecycle endpoints (auth required)
        orders.py               Order endpoints (auth required)
        venues.py               GET /api/venues (auth required)
        connectors.py           Connector CRUD (GET: any user, mutations: admin only)
        connector_specs.py      Spec CRUD + dry-run, test, AI generate (admin only)
        agents.py               Agent config + binding management (admin only)
        oauth.py                OAuth 2.0 authorization code flow
        health.py               GET /health (no auth)
      connectors/
        base.py                 Abstract connector interface
        mock_supplier.py        Mock supplier connector (generates references)
        mock_hr.py              Mock HR system connector
        bamboohr.py             BambooHR API connector (real)
        spec_executor.py        Spec-driven executor (template + agent modes)
        registry.py             Connector lookup by domain
      services/
        supervisor.py           Orchestrator (routes messages to domain agents)
        order_service.py        Procurement lifecycle (DB)
        hr_service.py           HR lifecycle (DB)
        integration_service.py  Runs connectors, logs IntegrationRun records
        agent_config_service.py Agent config + binding CRUD, capability summaries
        oauth_service.py        OAuth token exchange, refresh, credential storage
        spec_generator.py       AI-assisted connector spec generation
        venue_resolver.py       Fuzzy venue matching against DB
        product_resolver.py     Product alias matching against DB
      data/
        seed.py                 Seed data definitions (specs, agents, bindings)
scripts/
  dev.sh                        Start API + frontend together
docs/
  ordering-flow.md              API contract for ordering
```

---

## Database

PostgreSQL 16 (Alpine) via Docker. Core models:

- **User** -- email, hashed password, full name, role (admin/manager), active flag
- **Task** -- central orchestration record with user_id, domain, intent, status, extracted/missing fields
- **Message** -- conversation history (user + assistant messages per task)
- **Order** / **OrderLine** -- procurement draft with venue, supplier, product, quantities
- **HrSetup** -- employee setup with name, role, venue, start date
- **Approval** -- tracks who approved/rejected (user email + user_id), with timestamp
- **IntegrationRun** -- logs each connector call with request/response payloads, status, and duration
- **LlmCall** -- logs every LLM invocation with prompts, response, model, duration, and status
- **ConnectorConfig** -- stored connector credentials, enabled state, and OAuth tokens
- **ConnectorSpec** -- config-driven connector definitions (auth, operations, templates, credential fields)
- **AgentConfig** -- per-agent display name, custom system prompt, description, enabled flag
- **AgentConnectorBinding** -- links agents to connectors with per-capability enabled/disabled toggles
- **OAuthState** -- temporary state storage for OAuth authorization code flow validation
- **Venue**, **Supplier**, **Product**, **ProductAlias** -- reference data

Seed data includes three Auckland venues (La Zeppa, Mr Murdoch's, Freeman & Grey),
two suppliers (Bidfood, Generic Supplier), two products with aliases
(Jim Beam, Corona), one admin user (`admin@norm.local`), four agent configs,
and four connector specs (BambooHR, Deputy, Bidfood, LoadedHub).

---

## Running tests

```bash
cd apps/api
.venv/bin/python -m pytest tests/ -v
```

Tests cover:
- LLM response parsing (clean JSON, markdown-fenced JSON, invalid JSON)
- BambooHR connector field mapping, submission (success, auth error, validation error, timeout, network error)
- Connector registry fallback (BambooHR with env vars, mock without)

---

## What is real vs mocked

| Layer | Status |
|-------|--------|
| Frontend UI | **Real** |
| Authentication (email/password + JWT) | **Real** -- bcrypt hashing, role-based access |
| LLM interpreter | **Real** -- requires Anthropic API key |
| LLM call logging | **Real** -- every call logged with prompts and responses |
| Supervisor orchestration | **Real** |
| Clarification loops | **Real** |
| User-scoped tasks | **Real** -- tasks filtered by authenticated user |
| API endpoints and task lifecycle | **Real** -- Postgres-backed |
| Database persistence | **Real** -- PostgreSQL + Alembic migrations |
| Venue/product resolution | **Real** -- fuzzy DB queries |
| Connector spec system | **Real** -- config-driven with template + agent execution modes |
| OAuth 2.0 flow | **Real** -- authorization code grant with token refresh |
| AI spec generation | **Real** -- Claude generates specs from API docs |
| Agent configuration | **Real** -- editable prompts, capability sync with specs |
| Supplier submission | **Real** -- connector adapter with mock supplier (generates references, logs integration runs) |
| HR system submission | **Real** -- BambooHR connector when configured, mock fallback otherwise |
| Reports | **Functional** -- mock data sources, real planning/aggregation pipeline |
| Audit trail (approvals + integration runs) | **Real** -- includes user identity |

---

## Limitations

- LLM adds ~2-5s latency per interpretation
- Name extraction needs a capitalized first letter
- Mock connectors always succeed -- no real supplier API connected yet
- BambooHR connector requires valid API credentials; falls back to mock without them
- JWT secret defaults to a dev value -- must be changed in production
- No password reset flow yet
- No email verification on registration

---

## Next engineering priorities

1. **Real supplier integration** -- Connect to Bidfood or similar API
   for product catalog sync and actual order submission.

2. **Password reset and email verification** -- Complete the auth lifecycle.

3. **Connector tests + error scenarios** -- Retry logic, timeout handling,
   and failure simulation for integration runs.

4. **Multi-venue scoping** -- Restrict task visibility based on user's assigned venues.
