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
- When a commit *is* requested: **commit directly on `main` and push** — that is
  the straight-to-prod flow, and it needs no feature branch and no PR. Do **not**
  spin up a `ship/…` branch or open a PR as a matter of course; that ceremony is
  not wanted here. The one hard rule is the bullet above — **stage only your own
  files** (`git add <paths>`, never `git add -A`) so a push never sweeps up
  another session's work — and end the message with the co-author trailer. A
  short-lived branch is worth it in exactly one case: several agents are editing
  this working tree at once and you need to isolate your files from a tangle you
  can't cleanly stage around. Otherwise, go straight to `main`.

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
**Staging** gives its admin no organisation or venues either, so a chat there
proves the plumbing only. **Production** has the data, and on `norm-dev` you can
log into it: `TOKEN=$(norm-prod-token)` (see *Where secrets live*). Production
is live: reads and chat questions are fine, but any write needs the user's
go-ahead, and a chat you send appears in production's threads — say so when you
report. Local is still the place to test writes.

## Quick Start

```bash
./scripts/dev.sh   # starts postgres, runs migrations, launches API (8000) + Web (3000)
```

### Where you are running

Two environments exist. **`norm-dev`, a GCE VM, is the primary one** (Sep 2026);
the Codespace still works but is being retired.

| | Codespace | `norm-dev` (GCE) |
|---|---|---|
| path | `/workspaces/norm` | `~/projects/norm` |
| GCP credentials | `norm-config-sa.json` key file | **attached service account, no key on disk** |
| reached by | browser / VS Code | VS Code Remote-SSH over IAP |

On `norm-dev`:

- **It shuts itself down after 45 minutes idle** — that is the cost model
  (~$41/mo instead of ~$139). "Idle" counts logged-in sessions, CPU load, and
  running `claude`/`pytest`/`vitest`/`next dev`/`uvicorn`, so ordinary work
  keeps it awake. For a long unattended job those checks would miss, run
  `keepawake on` (and `keepawake off` after).
- Start it from a laptop with
  `gcloud compute instances start norm-dev --zone=australia-southeast1-a`,
  or `scripts/devbox.sh up|ssh|code|down`, or the Google Cloud phone app.
- **`gh` is NOT authenticated here.** Anything that watches CI or a deploy
  needs `gh auth login` first — it will otherwise fail in a way that looks
  like the pipeline is broken.
- The Cloud SQL proxy for the config DB runs as a systemd service
  (`cloudsql-config.service`, port 5433). **The test suite needs it**:
  `tests/conftest.py` imports `app.db.engine`, which refuses to load when
  `CONFIG_DATABASE_URL` is unreachable, so without it `pytest` cannot even
  collect. `dev.sh` starts its own proxy too; both are harmless.
- `dev.sh` is deliberately NOT auto-started on boot — it would make the
  idle-shutdown think the box is permanently busy.
- Setup lives in `infra/terraform/dev-vm/` and can be rebuilt from it.

## Endpoints and tools (the vocabulary)

Use these words exactly; they are how the database is split (`connector_specs.endpoints` /
`connector_specs.tools`). Full decision and the built-in audit: `docs/tool-architecture-strategy.md`.

- **API endpoint** — one call to an outside system (an HTTP template, or one of Orbit's MCP
  functions). A building block. **Never shown to an LLM**, and returns raw data — no
  `response_transform`. A consolidator shapes what it reads with its own
  `consolidator_config.shapes`.
- **Tool** — the only thing an LLM sees. Either a **consolidator** (config Python over
  endpoints) or a **built-in** (Norm code, `@register` in `app/agents/internal_tools.py`) —
  and a built-in may only work on Norm itself; anything reaching an outside system is a
  consolidator.
- **Exposure** is the App Map: a tool reaches the agent or MCP only when an App claims it.
- `app/connectors/spec_rows.py` is the one place that decides a row's kind and reads the two
  lists — don't loop over `spec.tools` directly. Anything LLM-facing reads `spec_rows.tools()`.
- Saved report charts call tools too (`script["rows"]` names the list to plot).
- `validate_config` (`check_endpoints_and_tools`) fails on an endpoint in an App claim, an MCP
  capability, the tools list, or carrying a transform.

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

The manual procedure below is a **fallback** — for when the migrate job is
missing/broken, or you need to inspect or repair schema state by hand.

**The version that was here until Sep 2026 could not work.** It patched an
instance named `norm-production`, which was stopped on 9 Aug and **deleted on
31 Aug 2026** — `gcloud sql instances describe norm-production` now returns
404. It also read the password from Terraform state, which is not initialised
to the production prefix in a fresh checkout and silently yields an empty
string. And it briefly gave the production database a **public IP**, which the
proxy makes unnecessary.

```bash
export PATH="$HOME/google-cloud-sdk/bin:$PATH"

# 1. Proxy to the live instance — norm-prod-db, NOT norm-production.
#    On the GCE dev box no --credentials-file is needed (attached service
#    account); in the Codespace add --credentials-file norm-config-sa.json.
cloud-sql-proxy --address 127.0.0.1 --port 5435 \
  norm-production-491101:australia-southeast1:norm-prod-db &

# 2. Password from Secret Manager, not Terraform state.
DB_URL=$(gcloud secrets versions access latest --secret=DATABASE_URL_DIRECT \
  --project=norm-production-491101)          # postgresql://norm:<pw>@10.31.0.38:5432/norm
DB_PASSWORD=$(python3 -c "
import sys;from urllib.parse import urlparse, unquote
print(unquote(urlparse('''$DB_URL''').password))")

# 3. Run migrations through the proxy. No public IP is ever enabled.
cd apps/api
DATABASE_URL="postgresql://norm:${DB_PASSWORD}@127.0.0.1:5435/norm" \
  uv run python -m alembic upgrade head
```

**After moving or renaming a database, grep every secret for the old host.**
Repointing Cloud Run was not enough in Aug 2026: the migrate job reads a
separate secret, `DATABASE_URL_DIRECT`, which still held the old private IP —
so migrations failed silently for three days while the pipeline stayed green.

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
| **production** | bettercallnorm.com | norm-production-491101 | Cloud SQL `norm-prod-db` (db-g1-small, ZONAL — not HA since the Aug-2026 cost cuts) |

## Key Configuration

- All config in `apps/api/app/config.py` (Pydantic BaseSettings)
- Secrets stored in GCP Secret Manager, injected as env vars to Cloud Run

### Where secrets live

| What | Where |
|---|---|
| Deployed app secrets (`DATABASE_URL`, API keys, …) | GCP Secret Manager in each environment's project, injected into Cloud Run as env vars |
| System secrets shared by every environment | config DB `system_secrets` table, loaded at startup by `_load_system_secrets()` |
| Local app settings | `.env` at the repo root (git-ignored; template `.env.example`) |
| Logins and keys for agents on this machine | `.local/` at the repo root (git-ignored via `/.local/`, files `chmod 600`) |

What `.local/` holds:

- `norm-credentials.json` — Norm admin logins for production, staging and local.
  `norm-prod-token [production|staging|local]` (in `~/.local/bin` on `norm-dev`)
  logs in and prints a bearer token; production is the default.
- `loadedhub-credentials.json` — LoadedHub browser and integration-test logins
  (Loaded's test environment and production).
- `orbit-supabase.json` — Orbit's Supabase service key and read-only Postgres URL.
- `prod-db-readonly.json` — the `norm_ro` login on the **production** app
  database. `norm-prod-psql` (in `~/.local/bin`) opens it through the 5435
  proxy. **Reach for this first.** It holds SELECT only and every session
  starts read-only, so a diagnosis cannot change live data; `CONNECTION
  LIMIT 3` keeps it off the API's budget, because `max_connections` on
  `norm-prod-db` is only 50 and the app's own pools already run close to it.
  A production WRITE needs the `norm` password from `DATABASE_URL_DIRECT` in
  Secret Manager — a separate, deliberate act that wants the user's go-ahead.

`.local/` is per machine. It is in neither git nor Terraform, so a rebuilt box or
a new Codespace starts without it: copy it across. On `norm-dev` it is
`~/projects/norm/.local`; some older scripts still hardcode the Codespace path
`/workspaces/norm/.local`. Never copy its values into the repo, memory, commits
or logs.

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
uv run pytest tests/ -q          # ~2,660 tests, ~4 min

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

**A red CI just after midnight UTC is probably not your change.**
`test_task_scheduler.py::TestTemporalGrounding::test_history_from_previous_days_is_date_prefixed`
builds a message `now - 6 minutes` and asserts it counts as "today". It does not
freeze the clock, so **any CI run in the first ~6 minutes after UTC midnight
fails it** — the message really did land yesterday. That is mid-evening NZ time,
a plausible moment to push. It blocked a deploy on 27 Sep 2026.

Before assuming you broke something: check the run's timestamp, and check
whether your diff touches Python at all. Re-running works, but only once the
clock is past the window — re-running inside it fails identically and looks
like a real failure. The fix is to freeze the clock in that test.

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
- 30 permission scopes — 27 an org role can hold, 3 platform-admin (defined in `app/auth/permissions.py`)

## Key Files

| Area | Files |
|---|---|
| Auth | `app/auth/dependencies.py`, `app/auth/permissions.py`, `app/auth/security.py` |
| Config | `app/config.py`, `app/db/config_models.py` |
| Agent | `app/agents/norm.py` (the one agent — no router), `app/agents/tool_loop.py`, `app/services/supervisor.py` |
| LLM | `app/interpreter/llm_interpreter.py` |
| Email | `app/services/email_service.py`, `app/templates/email/` |
| Invoice units | `docs/unit-resolution.md` (how a product's delivered unit is chosen — self-healing), `app/services/supplier_catalog.py`, `app/services/unit_resolver.py`, `app/services/invoice_replica.py` |
| Deploy | `.github/workflows/deploy.yml`, `.github/workflows/deploy-env.yml` |
| Infra | `infra/terraform/main.tf`, `infra/terraform/modules/` |
