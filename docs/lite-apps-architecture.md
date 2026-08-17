# Norm Apps — Strategic Review & Revised Architecture

*Status: this document replaces the April 2026 "Mini Apps Architecture Plan" (the lite-apps
plan) in its entirety. Reviewed August 2026 against the live codebase, the live config DB,
and the August cost review. The April plan was never implemented — a repo-wide search finds
no trace of it beyond this file's own history — and its premises have since been reversed by
owner decision. Git history preserves the original text.*

---

## Part 1 — Strategic review: what the April plan assumed, and what actually happened

### The April plan in one paragraph

Build standalone "lite apps" (HRLite, RosterLite…) as **completely independent products** —
own brand, domain, sign-up, Stripe account, GCP project, eventually own repo — integrated
back into Norm as just-another-connector via API-key template HTTP calls, with a shared
`packages/app-sdk` toolkit and path-filtered CI. Extraction to a separate company was a
design goal.

### What actually happened (verified, August 2026)

**1. None of it was built.** No `apps/hrlite-*`, no `packages/app-sdk` (`packages/` holds 62
lines of unimported scaffold stubs, absent from the pnpm workspace), no
`infra/terraform/modules/lite-app`, no per-product GCP projects, no path-filtered CI (there
is not a single `paths:` filter in any workflow), one monolithic `scripts/dev.sh`.

**2. Billing shipped the opposite way — and it is the marketplace's embryo.** Norm has live
Stripe billing (`app/services/billing_service.py`, ~520 lines, webhooks, quotas): **one org
subscription with per-agent add-ons** — `AGENT_PRICES_CENTS = {"hr": 1000, "procurement":
500, "reports": 0}`, per-venue pricing, per-agent Stripe price IDs in `config.py`. "HR as a
separately-billed product" already exists as a $10/mo line item on one invoice. But note the
gap this review found: the `Organization.hr_agent_enabled` / `procurement_agent_enabled` /
`reports_agent_enabled` booleans are read **only by billing code** — nothing in
`agents/router.py` or `prompt_builder.py` enforces them at runtime. Entitlement is currently
billing math, not an access control.

**3. The cost posture forbids the plan's infrastructure.** The August 2026 billing outage
(`infra/COST-CUTS-2026-08.md`) revealed ~$700/mo of fixed infrastructure serving zero human
traffic; it was cut to ~$210/mo partly by deleting redundant capacity, and production
Terraform is currently drift-blocked. Per-product GCP projects, load balancers, and Cloud
SQL instances would rebuild that cost floor several times over. **One deployment is a
constraint, not a preference.**

**4. The first "lite app" happened anyway — outside the plan.** The Cook Brothers App: a
separate Supabase/Lovable product carrying **114 tools across five domains** (kitchen 38,
functions 22, training 21, marketing 20, stock 12 — counted from the synced spec in the
config DB). Norm consumes it as a connector — but not by the plan's API-key mechanism: via
`execution_mode="mcp"` + OAuth 2.1 PKCE + dynamic registration + `sync-mcp-tools`
self-description (`scripts/sync_cook_brothers_app.py`, `routers/connector_specs.py`). And it
also exposes itself **directly to claude.ai as its own MCP server** — a topology the plan
never imagined: Claude is the hub; Norm and the CB App are both spokes.

**5. Norm's rails became the real "app SDK".** Since July: 7 config-driven agents over one
tool loop; working documents; display components **single-sourced** to both the web app and
claude.ai (`apps/mcp-ui` imports the same React files — roster, PO, invoice, menu, recipe
editors render inside Claude); an MCP server with scopes, consent, and projection; the
shared config DB; and an org-scoped **generative app platform** (`App`/`AppVersion`/
`AppShare`/`AppCall` tables, `app_runtime.py` sandboxed execution, `app_builder` agent)
whose own docstring anticipates a marketplace. The reusable substrate the plan wanted to
extract into a generic SDK turned out to be Norm itself.

**6. A real duplication defect exists.** Hiring lives in both products (Norm:
`HrSetup`/`Job`/`Candidate`/`Application` + `hr_service.py` + HiringBoard; CB App:
`training_*` job openings, interviews, talent pool). Recipes read LoadedHub directly but
write through the CB App passthrough. "Two tools answering the same question" already caused
two production incidents at tool level (see `docs/tool-architecture-strategy.md`) — this is
the same failure mode at product scale.

### Verdict on the April plan

| April premise | August 2026 ruling |
|---|---|
| Independent brands, hidden Norm association | **Reversed by owner decision** — apps are Norm-branded ("Norm HR", "Norm Marketing"…), living in a marketplace inside Norm |
| Separate repos / GCP projects / Stripe accounts | **Retired** — counter-indicated by the cost posture, the live single-subscription billing model, and the one-pipeline invariant |
| `packages/app-sdk` shared toolkit (6–8 week refactor) | **Retired** — never started; the reusable substrate is Norm's own rails, not a generic SDK |
| Lite app = external service integrated by API key + template HTTP | **Superseded twice** — MCP + OAuth self-description shipped instead; and now base apps become first-party apps *inside* Norm, not connectors at all |
| Extraction-readiness as a design goal | **Dropped** — base apps are core product. Apple's built-in apps aren't extractable; that's the point |
| Blast-radius isolation | **Survives, re-mechanized** — entitlement gates, config-driven exposure, and the sandboxed app runtime, instead of repo/project splits |

---

## Part 2 — The revised direction

**Norm is the platform and the brand. Apps are Norm apps, in a marketplace inside Norm —
the Apple App Store model.**

- **Base apps** — the five Cook Brothers domains (kitchen/food safety, functions & events,
  training & hiring, marketing, stock), **migrated into Norm's infrastructure** — play the
  role of Apple's built-in apps (Maps, Notes): included with every Norm subscription,
  enabled by default, toggleable off like any other app. **No Supabase and no Lovable
  survive the migration.**
- **Paid add-on apps** generalize the existing per-agent pricing (hr $10 / procurement $5)
  into per-app SKUs on the same single Stripe subscription.
- **User-built apps** (the generative app platform) are the long tail — org-scoped, built
  conversationally via the `app_builder` agent — sitting in the same "Apps" surface.

The Cook Brothers App's separate claude.ai MCP server retires as its domains migrate;
Claude ends with one server to talk to: Norm's.

---

## Part 3 — Architecture

### 3.1 The unifying decision: base apps ARE platform apps

The Cook Brothers apps lean heavily on Loaded data **and** store a lot of their own — which
is exactly the shape of a real platform app. So rather than building the migrated domains as
bespoke native modules *beside* the app platform, the ruling is **one app model, two trust
tiers**:

- **Every Norm app — base or user-built — is an `App`/`AppVersion` spec**: declared
  `actions` (connector reads), `writes`, `scopes`, UI, and server-side logic. The platform
  runtime (`app/services/app_runtime.py`) already provides governed connector access —
  `call_api` through the declared-action door, authorization as *viewer permissions ∩ app
  scopes*, write actions opted into twice, every call audited in `app_calls`.
- **Base apps run in a first-party trust tier.** Same spec shape, same authorization door,
  but their logic and UI are version-controlled in this repo and synced to the platform —
  the proven `config/consolidators/` pattern (reviewed code, real tests, exec'd under the
  real sandbox namespace in CI) — with relaxed sandbox caps where a domain justifies it.
  User apps stay fully sandboxed. The marketplace cannot tell the tiers apart; trust is an
  implementation property, not a storefront one.
- **Migrating through the app structure is deliberate dogfooding.** Each CB domain rebuilt
  as a platform app forces the platform to grow the primitives real apps actually need, in
  priority order, with ourselves as the first demanding customer. The `app_builder` agent is
  the Norm-native analog of how these apps were Lovable-built in the first place — rebuilt
  conversationally, kept as reviewable specs.
- **Escape hatch, stated up front:** a domain that outgrows the sandbox (think
  invoice-engine-scale logic) graduates to a native module — `apps/api/app/` code like
  hiring is today — without touching the catalog, entitlements, or the marketplace. The
  storefront doesn't care how an app is implemented.

### 3.2 Storage: the app data layer (the Supabase replacement)

This is the platform's missing primitive — `app_runtime.py` today offers apps **no storage
at all**; their only data reach is `call_api`. It is also the direct answer to "how does
storage work in Norm's world":

- **Norm's posture changes.** Norm currently stores very little (the August production DB
  export was 5.3 MiB) because the systems of record are external — Loaded, the CB App's
  Supabase. Migrating the CB domains makes **Norm the system of record** for them: FCP
  cards, temperature/cooling logs, training records, event enquiries, stocktakes. Volumes
  remain small by database standards — operational logs for one hospitality group — and the
  August cost review found the old disk 40,000× oversized, so the existing `db-g1-small`
  main Postgres carries this comfortably for a long time.
- **New primitive: app-scoped collections in Norm's main Postgres.** A single `app_records`
  table — `(app_slug, organization_id, venue_id, collection, id, data JSONB, created_at,
  updated_at)` with appropriate indexes — plus **declarative collection schemas in the app
  spec**, with CRUD/query exposed to app logic and app UI through the same governed runtime
  door as `call_api`. JSONB-first is deliberate: it is the Supabase-shaped surface the CB
  apps were built against, it needs no per-app migrations, and a hot collection can graduate
  to a real table later if a domain earns it.
- **User apps get the same storage API**, scoped to their own app — this is what makes the
  CB migration the proving ground for user apps generally. Cross-app data access is only
  ever through the owning app's published tools and scopes — call it the **MapKit rule**:
  nobody reads another app's tables, base apps included. Third-party apps don't read Apple
  Maps' database; they call MapKit.
- **Roadmap primitives after storage**, in the order the CB domains will demand them:
  **scheduled app runs** (the CB App has cron edge functions; Norm's automated-tasks
  scheduler — Cloud Scheduler + `SELECT … FOR UPDATE SKIP LOCKED` — is the rail to lean on),
  then **notifications/email** through the existing `email_service` as a governed primitive.

### 3.3 Tools and the AI surface

- Each migrated domain re-publishes its tool surface from the app spec: app actions become
  Norm tools, exposed to claude.ai via `McpCapability` rows tagged with a **`domain`**
  column — which lands the domain-surfaces work `docs/tool-architecture-strategy.md` §E
  already proposed, keeping each consented surface inside the 30–50 tool band.
- As a domain migrates, its tools are disabled in the synced `cook_brothers_app` connector
  spec (marked superseded per the lifecycle proposal, never deleted). When the last domain
  lands, the Supabase project and the CB App's separate claude.ai MCP server retire.
- One canonical tool per question remains the law. The migration *ends* the current
  worst violation (hiring existing in two products).

### 3.4 UI

**Rebuilt, not ported.** No Lovable React survives. Each domain's UI is rebuilt on Norm's
patterns — working documents for drafts, display blocks, split-pane functional pages — and
earns a place inside claude.ai via the `apps/mcp-ui` single-source build **only** where it
does something Claude cannot do natively (editing, dragging, approving — the existing
curation rule; Claude draws tables better than an iframe does).

### 3.5 Identity, tenancy, and infrastructure

- **One repo, one API service, one web app, one pipeline, one main DB per environment.** A
  Norm app is a spec plus (for base apps) synced first-party code — never a service. This
  keeps the $210/mo posture intact and keeps the deploy invariant: one commit → one image
  pair → all environments.
- CB App's Supabase users map to Norm users/orgs/venues — they are the same staff. Migrated
  data lands org/venue-scoped in the app data layer. (Mechanics are a migration-planning
  concern, deliberately out of this document's scope.)

---

## Part 4 — The marketplace

Two small tables, three thin enforcement filters, no new machinery beyond them:

- **`marketplace_apps`** (config DB, beside `agent_configs`) — the global catalog: slug,
  name, description, icon, composition (app slug / agent slugs / playbooks / bindings / MCP
  domain / nav pages), `price_cents` + Stripe price key, **`bundled` flag**, status.
- **`org_app_entitlements`** (main DB, beside `subscriptions`) — per-org state: enabled,
  Stripe subscription item id, timestamps. Absorbs and retires the three
  `Organization.*_agent_enabled` booleans.
- **Base apps**: `bundled: true` — in every subscription, on by default, toggleable off.
  Paid apps: the existing `PUT /billing/{org}/agents` + Stripe-subscription-item machinery,
  generalized to iterate the catalog instead of a hardcoded dict. User-built apps: same
  surface, org-scoped, unpriced.
- **Enforcement — three filters over one catalog** (this closes the real gap found in
  review, where entitlement was billing math only):
  1. **Hard gate at agent/app entry** (`agents/router.py`, app launch) — the security gate;
  2. **`prompt_builder._collect_tools`** skips bindings whose app is unentitled;
  3. **`mcp/projection.py::project_tools`** filters capabilities by entitled domains — so
     claude.ai honors marketplace toggles with no extra work.
- **Disabling an app is a billing/visibility act, never a deletion.** Data is retained;
  re-enabling restores the app over its own data.

---

## Part 5 — Sequencing principles

*(Principles, not a work plan — migration mechanics get their own document.)*

1. **Storage primitive first.** Nothing migrates until `app_records` + spec-declared
   collections exist, because every domain needs them and user apps inherit them.
2. **Training migrates first.** It resolves the hiring duplication by landing the domain in
   exactly one place — one Norm Training app supersedes both Norm's native hiring tables
   (frozen read-only, then retired) and the CB App's `training_*` pipeline — and at 21
   tools it is mid-sized: big enough to prove the primitives, small enough to finish.
3. **Then kitchen / functions / marketing / stock, by value.** Each migration follows the
   same shape: rebuild as first-party platform app → re-point the tool surface → disable the
   CB connector's superseded tools → migrate data → shrink the CB App's own claude.ai
   surface.
4. **The CB App retires by evaporation**, not by a cutover event. When the last domain
   lands, the Supabase project and its MCP server switch off.
5. **Explicitly parked:** the recipe write-passthrough (Norm reads LoadedHub directly but
   writes through the CB App). This is a limitation of Loaded's API surface, not of Norm or
   the CB App — it gets solved in the LoadedHub connector work, on its own track.

---

## Part 6 — Key files and tables

| Concern | Where it lives today |
|---|---|
| App platform runtime (authorization door, sandbox, `call_api`) | `apps/api/app/services/app_runtime.py`, `apps/api/app/routers/apps.py` |
| App tables | `App`, `AppVersion`, `AppShare`, `AppCall` in `apps/api/app/db/models.py` |
| Billing to generalize | `apps/api/app/services/billing_service.py` (`AGENT_PRICES_CENTS`), `apps/api/app/routers/billing.py`, per-agent price IDs in `apps/api/app/config.py` |
| Entitlement gate points | `apps/api/app/agents/router.py`, `apps/api/app/agents/prompt_builder.py`, `apps/api/app/mcp/projection.py` |
| First-party code sync pattern | `apps/api/config/consolidators/` + `apps/api/scripts/sync_*.py` |
| CB App connector (to be progressively disabled) | `connector_specs` row `cook_brothers_app` (config DB), `apps/api/scripts/sync_cook_brothers_app.py` |
| Single-source components for web + Claude | `apps/mcp-ui/src/registry.ts`, `apps/api/app/mcp/ui/` |
| Tool lifecycle / domain surfaces context | `docs/tool-architecture-strategy.md` |
| Cost constraints | `infra/COST-CUTS-2026-08.md` |
