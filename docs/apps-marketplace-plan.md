# Apps unification: marketplace, bindings, and the supplier-tenders exemplar

*Status: approved 29 Aug 2026; implementation in progress. Companion to
`lite-apps-architecture.md` (this plan makes its Part 4 real and extends it —
connectors become marketplace apps, full rename, owner-gated marketplace).
Keep this document updated as phases land.*

*Progress:*
- *Phases 0–4 SHIPPED to production 29 Aug (commit `529e930`, pipeline green
  testing→E2E→staging→prod; migration `p1q2r3s4t5u6` applied; entitlement
  resolver verified against the prod org — full catalog entitled, nothing
  blocked, day-one neutral).*
- *Phase 5 + billing generalization — landed 29 Aug (uncommitted):*
  - *Validator: `check_binding_actions` (an enabled binding cap must resolve
    to an agent-visible tool on the bound spec — the CB-drift class, plus
    engine-only caps and bindings to spec-less connectors),
    `check_display_components` (free-text display_component must be platform
    chrome or catalog-declared), `check_component_api_row` (connector must
    have a spec; component must be declared — rows are otherwise
    self-contained, action_name is NOT a spec tool). Day-one catch: four
    enabled `microsoft_outlook` send_email bindings (Mar-2026 relic, no spec
    anywhere) — disabled in the shared config DB. 24 pre-existing findings
    remain for triage (marketing/orbit playbook filters naming dead tools,
    loadedhub allowed_write_actions drift, edit_recipe path_template,
    mcp.loadedhub.get_stock_item).*
  - *prompt_builder page labels now read from catalog compositions (platform
    chrome keeps a 5-entry fallback map; the old map never knew invoices /
    menu-engineering / supplier-tenders existed).*
  - *Billing generalized: `get_agent_apps` prices agent bundles from the
    catalog (owns_agents rows), `PUT /billing/{org}/agents` writes
    org_app_entitlements (same row the marketplace writes — billing and
    access ride ONE switch), Stripe items resolve via stripe_price_key.
    The three Organization booleans are DROPPED (`q2r3s4t5u6v7`; no data
    copy — they were display-only and unenforced; prod verified all-True).
    Response keeps the `agents.{hr,procurement,reports}` shape so
    BillingTab is unchanged, plus a general `agent_apps` list.*
  - *ui_apps.TOOL_COMPONENT + emit.mjs SOURCES documented as the two
    deliberate MCP-surface hand-lists.*
  - *Deferred from Phase 5 (deliberately): FULL_WIDTH_COMPONENTS /
    COMPONENT_META into the catalog (adds an async fetch to the chat render
    hot path for two rarely-changing lists), the four binding-walk
    unification (pure refactor risk), AppVersion.spec dead keys.*
- *Phase 3 — landed 29 Aug: (0) the connections/apps split implemented —
  compositions now carry `connections[]` + `tool_actions[]`, entitlements
  resolve app-level ("a connection stays available while ANY entitled app
  declares it", pinned in tests), catalog re-seeded; (1) the Marketplace UI —
  AppsDashboard now renders the catalog (tier badges, pricing, enable/disable
  for owners, expandable pages + per-venue connection readiness dots) above
  the team's own apps; (2) submission — `POST /api/marketplace/submit`
  (owner) → pending row (visible to submitter + platform admins) →
  `/approve` (platform admin) → live; connections derived from the app
  version's declared actions; (3) settings app-lens — ComponentsPanel shows
  the owning App, ConnectorSpecsPanel shows "used by <apps>". Deferred:
  billing generalization (booleans still drive billing).*
- *Phase 4 — code + surface rename landed 29 Aug: `ConnectionSpec` /
  `Connection` / `AgentConnectionBinding` across the API (94 files; old names
  kept as transitional aliases in the model modules), user-visible labels
  Connectors→Connections. **Deliberate residue for the follow-up drop
  release** (zero-downtime choice — physical renames only after all envs run
  the new code): table names (`connector_specs`, `connector_configs`,
  `agent_connector_bindings`), column names (`connector_name`), and REST
  paths (`/api/connectors*`, `/api/connector-specs*`).*
- *29 Aug (model revision): **Connections split from Apps** — see the revised
  Target model. Decided after the spine demo caught the CB tool-consolidation
  drift (the chef's recipe write orphaned + `recipe_save` broken by a renamed
  pipe tool — both fixed same day) and the tenders app-ownership flip; both
  were symptoms of conflating pipe with functionality. Phase 3 now opens by
  implementing the split; Phase 4's rename target became connector→Connection.*
- *Phase 0 — REVERSED 29 Aug: Loaded's `cookbrothers` OAuth client cannot be
  granted the `stock:tenders` scopes, so the direct path is dead. Scopes
  removed again (`sync_loadedhub_tender_scopes.py` now enforces removal).
  Tenders follow the recipe-write precedent instead: through the **Cook
  Brothers App**, whose stored Loaded session carries the Stock permission.
  Requires the CB App to ship four tools (contract below) + a
  `sync-mcp-tools` re-discovery + re-run of `sync_tender_actions.py`.*

*CB App tender tool — SHIPPED 29 Aug as ONE consolidated tool (the CB house
style), discovered + bound to procurement: `stock_loadedhub_tender` with
`action: list | get | update` (get → `data.tender`, list → `data.tenders`;
update PUTs the complete tender document; `review` GETs Loaded's own
tender-review report for a period — added 29 Aug, so the earlier Norm-side
stock-received join was deleted and the bridge is now a pure passthrough of
Loaded's report). Verified live on La Zeppa's Bidfood tender: 25 review lines
all with delivery orders, on-tender vs over-tender deltas rendering correctly
on the page.
Known friction: the consolidated tool is write-capable, so AGENT reads through
it also pause for approval — if that grates, split a read-only
`stock_loadedhub_tender_read` off CB-side and rebind.*
- *Phase 1 — landed 29 Aug: `MarketplaceApp` (config) + `OrgAppEntitlement`
  (main, migration `p1q2r3s4t5u6`), `services/entitlements.py` (one resolver;
  agent gate reads `owns_agents` only), enforcement in supervisor routing +
  `_collect_tools` (MCP projection inherits it), `routers/marketplace.py`
  (browse open; enable/disable gated on `billing:manage` = Owner), catalog
  seeded via `sync_marketplace_catalog.py` (14 apps, all `bundled` — day-one
  neutral, verified live). Deferred within Phase 1: billing generalization
  (booleans still drive billing; price metadata already on the agent-bundle
  rows).*
- *Phase 2 — REVISED to the CB path, landed 29 Aug: the loadedhub tender
  tools/binding/component-api rows are REMOVED (`sync_tender_actions.py` now
  enforces that end state and binds the CB tools to procurement once
  discovered). The page reads through `/api/supplier-tenders/{list,review}`
  (`routers/supplier_tenders.py`, reusing the recipe-write `_cb_context`/
  `execute_spec` plumbing — the component-api door is HTTP-only and can't call
  MCP). `SupplierTenders.tsx` (list → detail → price review) unchanged in UX;
  its owning app in the catalog MOVED from `loaded` to `cook-brothers-app` —
  no CB connection, no tenders, and the marketplace says so. Verified live:
  CB-connected venue → clean 501 "CB App doesn't expose tenders yet" until
  the CB tools ship; non-CB venue → connect prompt.*

## Context

Three asks, one architecture: (1) a supplier-tenders endpoint + page from Loaded;
(2) make page↔connector binding explicit — today it is scattered across 13
implicit seams; (3) unify "connectors" and "apps" into ONE user-facing **App**
concept in a marketplace: Loaded and Tanda appear exactly like the Hiring app,
org owners enable apps, users can submit apps, every component belongs to exactly
one app and is bound to an agent, all visible in admin. Direction: simplify and
consolidate — no parallel new machinery.

This makes `docs/lite-apps-architecture.md` Part 4 real (it already specifies
`marketplace_apps` + `org_app_entitlements` + three enforcement filters) and
extends it per owner decisions today: **connectors become marketplace apps too**,
**full terminology/schema rename now**, **tenders read+write**, **marketplace
gated to organisation owners**.

Key verified facts the plan builds on:
- Loaded's tenders API exists: `GET/POST /1.0/stock/internal/tenders`,
  `GET/PUT /tenders/{id}`, `GET /tenders/{id}/review?startTime&endTime`
  (from `loadedreports/LoadedAPI` `stock-api/Controllers/Stock/Internal/TendersApi.cs`;
  model `{id, supplierId, supplierName, name, datestampStart, datestampEnd,
  lines[]}`). The endpoints gate on the "Stock" permission OR the API scopes
  `stock:tenders:r`/`stock:tenders:rw` — there is no tenders-specific user auth
  (confirmed with the user). **Our 403 is self-inflicted: the `cookbrothers`
  OAuth client's `oauth_config.scopes` on the loadedhub spec requests 32 scopes
  but not the two tenders scopes**, so issued tokens can read items yet not
  tenders.
- Today's "enable" is eight different mechanisms (ConnectorConfig.enabled string,
  ConnectorSpec.enabled, three `Organization.*_agent_enabled` billing booleans
  read by nothing at runtime, AppShare, App.visibility, per-user pins,
  McpCapability). The three enforcement filters exist nowhere yet.
- Component→app/agent binding half-exists already: `App.agent` (models.py:1173)
  and `AppVersion.spec.components[]` (declared, never read — dead surface to
  wire, not new code).
- The binding seams inventory (component_api rows incl. 2 components that exist
  ONLY as untracked DB rows; free-text `display_component`; hand-lists
  FULL_WIDTH_COMPONENTS, COMPONENT_META, prompt_builder's stale page-label map,
  ui_apps.TOOL_COMPONENT) is the consolidation hit-list.

## Target model (the noun set)

*Revised 29 Aug (owner decision): **Connections are separated from Apps.** The
original model made each integration connector an app; that conflated the pipe
with the functionality, and reality broke it twice in one week — Supplier
Tenders had to change owning app when its plumbing moved from Loaded to the CB
App, and the recipe write broke when its pipe was renamed. The platform-app
half of the codebase already works the split way (an AppVersion declares
`actions` across ANY connectors), and user-built apps will always pull from
multiple sources — so the catalog converges on that model. This is a
subtraction, not an addition: app↔app dependencies (`requires`/`enhanced_by`)
never get built; the only dependency edge is app→connection.*

- **Connection** — a pure pipe to an external system: the spec (raw endpoints
  /tools — today `ConnectorSpec`) plus the per-venue credential (today
  `ConnectorConfig`). Loaded, Cook Brothers, BambooHR, Deputy, Gmail, Bidfood,
  Brevo, Metricool. Lives in Settings → Connections; NOT a marketplace item;
  never billed. Internal pseudo-connectors (norm, norm_email, norm_reports)
  are platform built-ins.
- **App** — the ONE user-facing/marketplace unit: components, pages,
  consolidators and agent tools, declaring the connections it needs. A catalog
  row (`marketplace_apps`, config DB) with slug, name, icon, description, tier
  (`platform` | `user`), `bundled`, price, status, and a **composition**:
  `{connections: [<spec name>, ...], app_slug?, agents: [..], owns_agents?,
  components: [{key, agent, page: {id,label,icon}, full_width, description}],
  tool_actions?: ["<connector>.<action>", ...], playbooks: [..], mcp_domain}`.
  A multi-source feature (recipe editor: Loaded reads + CB writes) is simply an
  app declaring two connections. Enabling an app whose connections aren't all
  connected for a venue shows a connect checklist, and features degrade
  per-connection with actionable prompts — never blankly.
- **Entitlement** — `org_app_entitlements` (main DB): org X has app Y enabled.
  Absorbs and retires the three `Organization.*_agent_enabled` booleans.
  Enable (org-level, an app) and connect (venue-level, a connection) are
  deliberately distinct acts.
- **Component** — belongs to exactly ONE app (declared in that app's
  composition), bound to one agent, optionally mounting a page. A component may
  consume any of its app's declared connections. The catalog is the single
  source the admin screen renders: "enable app → these pages/components/tools
  appear for these agents, needing these connections".
- **Initial slicing stays 1:1 to avoid bikeshedding**: today's catalog rows
  keep their names ("Loaded" the app initially = what the Loaded connection
  lights up, `connections: ["loadedhub"]` — plus `cook_brothers_app` where
  its components genuinely need it). Re-bundling into domain apps
  ("Purchasing", "Kitchen") is later, optional product work the model already
  supports.

## State check (28 Aug, before implementation)

Re-verified against the live tree + config DB: on `main` @ `0a84362`, tree
clean apart from 4 files belonging to another session (invoice/dojo work — do
not touch). Nobody has started any marketplace/tenders/enforcement work: no
`marketplace_apps`/`org_app_entitlements` anywhere, no tender code, no
entitlement reads in router/prompt_builder/projection. Tenders scopes still
absent from `oauth_config.scopes` (loadedhub spec now v276, 74 tools — the
domain-tools consolidation renamed several tools since the original inventory,
so the catalog seed must be generated from the LIVE spec rows, not from the
week-old inventory). Billing booleans still exist; 21 static pages; 3 fixture
apps. Plan is current and greenfield.

## Phases

### Phase 0 — unlock tenders scopes (config + reconnect, no Loaded change)
1. Append `stock:tenders:r stock:tenders:rw` to the loadedhub spec's
   `oauth_config.scopes` (small idempotent sync script — the field is one
   string in the shared config DB).
2. Reconnect loadedhub for the venue(s) (existing Settings → Connectors OAuth
   flow) so a token carrying the new scopes is issued — scope sets are fixed at
   authorization, so a refresh alone won't widen them. (Several venues already
   need a reconnect anyway — Dunedin/Glass Goose/Freeman & Grey refresh tokens
   are currently expired.)
3. Verify: `GET /1.0/stock/internal/tenders` returns 200 (and the user's example
   tender `6644e8f4…` loads) via `/connector-specs/loadedhub/test`. If Loaded's
   server refuses to issue the scopes for the `cookbrothers` client, only then
   is a Loaded-side client tweak needed.

### Phase 1 — catalog + entitlement + enforcement (the spine)
1. `marketplace_apps` table (config DB, beside `agent_configs`;
   `app/db/config_models.py`) + `org_app_entitlements` (main DB alembic
   migration, beside `subscriptions`).
2. Seed script `scripts/sync_marketplace_catalog.py` (idempotent, dry-run):
   one row per existing integration app + the three platform apps. Compositions
   written from today's reality — this **replaces** the implicit binding seams:
   every page in `pageRegistry.ts`, every component in
   `DisplayBlockRenderer.REGISTRY`, every component_api component gets exactly
   one owning app in the catalog (also scripts the two untracked component_api
   row-sets: `purchase_order_editor`, `orders_dashboard`).
3. The three enforcement filters (per the architecture doc, now written):
   - agent/app entry gate in `agents/router.py` + app launch;
   - `prompt_builder._collect_tools` skips bindings whose app is unentitled;
   - `mcp/projection.py::project_tools` filters by entitled apps.
   Default entitlement: bundled apps ON for every org (backfill), so behaviour
   is unchanged on day one.
4. Billing: generalize `AGENT_PRICES_CENTS`/`PUT /billing/{org}/agents` to
   iterate the catalog; migrate the three booleans into entitlement rows, then
   drop them.
5. API: `GET /api/marketplace` (catalog + org state + per-app "what it lights
   up"), `POST /api/marketplace/{slug}/enable|disable` — **Owner org-role
   gated** (`require_permission` with the Owner-only scope).

### Phase 2 — supplier tenders, built as the first exemplar
1. Loadedhub spec tools via `scripts/sync_tender_actions.py` (pattern:
   `sync_menu_actions.py`): `get_tenders`, `get_tender`, `get_tender_review`
   (reads) + `create_tender`, `update_tender` (writes, `{{ tender | tojson }}`
   passthrough, describe→approve flow). Bind reads+writes to `procurement`.
2. Component `SupplierTenders.tsx` (self-loading, pattern: MenuEngineering):
   tender list → detail (lines, supplier, date window) → the review view
   (tender vs actual prices for a period); create/edit via the approve-gated
   writes. Register in `DisplayBlockRenderer` + page entry (`agent:
   'procurement'`).
3. **Declared in the catalog from birth**: the Loaded app's composition gains
   the `supplier_tenders` component + page + the five tools. The admin
   marketplace screen shows it; no hand-list edits beyond the two registries
   the code still needs (see Phase 4 cleanup for their future).
4. Component-api rows for the reads via the same sync script if the component
   uses `callComponentApi` (else the tools ride working-document loads).

### Phase 3 — the connections/apps split, then the marketplace UI (owners)
0. **Implement the model tweak first** (cheapest moment — no UI built yet):
   - composition `spec: X` → `connections: [X, ...]` (Loaded's recipe/menu
     components declare `["loadedhub", "cook_brothers_app"]`; Supplier Tenders
     `["cook_brothers_app"]`); optional `tool_actions` list per app for
     agent-tool gating (unclaimed action = allowed, as today);
   - `services/entitlements.py`: replace the whole-connector block (which
     assumed one owning app per connector) with app-level gating —
     `unentitled_connectors` derives from connections claimed ONLY by disabled
     apps; `tool_actions` gating rides the same resolver;
   - re-run `sync_marketplace_catalog.py`; entitlement/marketplace tests
     updated to pin the new semantics (multi-app connections: disabling one
     app never blocks a connection another entitled app declares).
1. Evolve `AppsDashboard` into the **Marketplace**: every catalog app with
   tier badge, enabled state, price, and an expandable "what you get": pages +
   components per agent, tool count, and **per-venue readiness of each declared
   connection** (reuse `/connectors/{name}/connect-info`) with connect buttons
   launching the existing OAuth/credential ceremony. Enable/disable for owners.
2. Submission: "Publish to marketplace" on a user-built app (owner-gated) →
   catalog row `tier: user, status: pending` → approve flow (platform admin) —
   the "deliberate promotion" `models.py:1130-1135` anticipated. Reuses
   `save_app`/`AppVersion` as-is; its declared `actions` connectors surface as
   the app's `connections`.
3. Settings consolidation: Connections tab (today's Connectors) + Components
   tab + Agents tab gain the app lens (each row shows which app(s) use it);
   `COMPONENT_META` moves into catalog compositions.

### Phase 4 — the full rename (last, with compat)
Terminology *(revised with the split — cleaner than the original connector→App
plan)*: **connector → Connection** everywhere; users end with two nouns, Apps
(marketplace) and Connections (settings). Internal: `ConnectorSpec →
ConnectionSpec` (config table `connector_specs → connection_specs`),
`ConnectorConfig → Connection` (main table `connector_configs → connections`),
`AgentConnectorBinding → AgentConnectionBinding` (`agent_connector_bindings →
agent_connection_bindings`). Mechanics, in order, because the config DB is
shared by ALL environments at once:
1. Rename tables + **create updatable views under the old names** (config DB via
   its manual-migration path; main DB via alembic) — old deployed code keeps
   working through the window.
2. Ship the code rename (models, imports, routes — keep old REST paths as
   aliases for one release), UI labels, sync scripts, docs, CLAUDE.md.
3. Drop the compat views + route aliases in a follow-up release once all envs
   are on the new code.
Column renames (`connector_name → connection_name`) ride the same pattern. The
`apps/` monorepo-directory ambiguity is noted in docs, not renamed.

### Phase 5 — consolidation cleanup (the "no extra code" dividend)
Retire the duplicated seams the inventory found: prompt_builder's stale
hardcoded page-label map → catalog lookup; `FULL_WIDTH_COMPONENTS` +
`COMPONENT_META` → catalog composition fields; free-text `display_component`
validated against the registry in `config_validator.py` (+ validate page
loadActions exist and aren't `engine_only` — the Roster page loads a demoted
tool today); the four parallel agent-binding walks unified on
`get_agent_actions`; `ui_apps.TOOL_COMPONENT`/mcp-ui registry documented as the
two lists they are; wire or drop the dead `AppVersion.spec` keys
(`playbooks`/`components`/`params`); `install_fixture_app` grows an
`--all-orgs`/idempotent mode driven by the catalog (`bundled` apps).

## Files (representative)
- New: `config_models.py::MarketplaceApp`, alembic `org_app_entitlements`,
  `scripts/sync_marketplace_catalog.py`, `scripts/sync_tender_actions.py`,
  `apps/web/.../display/SupplierTenders.tsx`, marketplace router + UI panel.
- Edit: `agents/router.py`, `prompt_builder.py`, `mcp/projection.py` (filters);
  `billing_service.py`/`routers/billing.py` (generalize); `AppsDashboard.tsx`
  (marketplace); `pageRegistry.ts` (+tenders page); `DisplayBlockRenderer.tsx`;
  settings panels (app lens); then the Phase-4 rename sweep.

## Verification
- Per phase: `ruff` + full API suite + web lint/tsc/vitest; sync scripts
  dry-run first (shared config DB = live everywhere).
- Phase 1: with all bundled apps entitled, tool lists/pages byte-identical
  before/after (snapshot `build_tool_definitions` per agent); disabling an app
  as owner removes its tools (agent), pages (nav), MCP projection.
- Phase 2: live tenders list/review on a real venue after the Loaded grant;
  create+update behind approve on a test tender; e2e through the procurement
  page.
- Phase 4: staging soak with compat views before dropping them; grep-zero for
  user-facing "connector" strings.

## Risks
- **Shared config DB renames** are the big one — compat views + ordered
  rollout, rename LAST after the catalog is proven.
- Widening OAuth scopes requires re-authorizing each venue's Loaded connection
  (a brief human ceremony per venue); until reconnected, tenders 403s continue
  on old tokens.
- Entitlement backfill must default everything ON to avoid a day-one outage;
  enforcement lands dark behind the backfill.
- Concurrent agent sessions in this repo — stage only own files, per practice.
