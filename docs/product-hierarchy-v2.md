# Product hierarchy v2: AI Team Members → Apps → Components → Connections

*Approved 27 Sep 2026 (plan-mode review with the owner; Phase A code built the same day). Supersedes the apps/marketplace hierarchy of
`docs/apps-marketplace-plan.md` (Aug 2026), which predates the one-agent
runtime (1f8ab7a, d9e1788, 8e59e32) and the consolidator migration.*

## Context

The Aug-2026 marketplace made *apps* the purchasable unit and connectors
marketplace items. Since then the runtime became ONE capable agent holding
the full union of tools (the seven domains survive as identities/prompts/
tabs, not capability gates) and raw endpoints were consolidated (loadedhub
77 tools → 26 agent-visible). The owner is rebuilding the hierarchy around
**AI Team Members as the thing customers hire**, with **Apps in the middle**
owning the user-facing experience — so the model never has to be unwound
when App Builder and the component library grow.

## The product model (owner-corrected, 27 Sep)

```
Organisation
   ↓
AI Team Members   = the jobs you hire Norm to perform
   ↓
Apps              = complete pieces of functionality unlocked by a team member
   ↓
Components        = reusable building blocks Apps are made of (HIDDEN from
   ↓                normal users — internal + App Builder only)
Connections       = external systems an App's components need to work
```

Underneath, **implementation only, never user-facing**: skills, playbooks,
tools, bindings, endpoints.

Rules:
- **A component may require zero, one or more Connections. An App inherits
  the combined Connection requirements of its Components.** A team member
  uses one or more Apps — so "Procurement" doesn't require Loaded; its
  Loaded App does. A member may use several Apps with completely different
  connection requirements, including none (a Norm-native App runs on
  Norm's own storage). Canonical example — HR uses **Norm HR** (hiring &
  training records, Norm storage, no Connections) and **BambooHR** (needs
  the BambooHR Connection).
- **There is ONE kind of App.** Apps may be Norm-built, integration-backed,
  or customer/community-built — BambooHR, Norm HR and a customer-built
  Training App all behave consistently in the UI: a piece of functionality
  a team member uses, made of components, with its own Connection needs.
  Users see Apps, never components.
- **Every App has an enabled state. Hiring a Team Member enables its
  bundled Apps by default. Additional Apps can be added or removed. Some
  Apps may also have their own price.** An App is ON iff: a hired team
  member uses it AND the App itself is entitled (explicit row wins, else
  its bundled default).
- Capability gating (unchanged in spirit): a tool/App is on for the org iff
  a hired team member carries/uses it; carried by two → survives while
  either is hired. One Norm runtime; org-wide gating; connections are
  plumbing, never products.

### Locked decisions
1. The marketplace sells **AI Team Members + Apps**; integrations
   become Connections (Settings plumbing, per-venue, "connected" never
   "enabled"). Apps are one concept regardless of author (Norm-built,
   integration-backed, customer/community-built).
2. Free base assistant **Norm** (chat, connections, general help) always on;
   **Reports and App Builder stay separate, free, always-included team
   members with their own tabs** (not hireable, not retirable).
3. Five hireable: **HR $10/mo, Procurement $5/mo, Executive Chef $0,
   Time & Attendance $0, Marketing $0** ($0 ones repriced later, data-only).
4. Buttons: **Hire / Retire**. Display names: plain job titles; only the
   base assistant is named "Norm". User-facing noun: **AI Team Members**
   ("agent" stays an internal/implementation term).

### Explainer copy (top of the hire page)
*"AI Team Members are the jobs you hire Norm to perform. Each one unlocks
the Apps and capabilities needed to do that job. Every organisation gets
**Norm** free — chat, reports and connection setup are always included.
Apps work inside the systems you already use: connect each system once per
venue under **Connections**, and every App that works in it is ready. Hired
but not yet connected? The Apps stay visible and tell you exactly what to
connect. Retire a team member any time — your conversations and records
stay."*

### The card (per team member)

**Apps are named after the product they are** ("Norm Procurement",
"Loaded", "BambooHR") — never feature words like "Purchasing", which sound
like components. Each App on the card carries its own switch and, where
priced, its own monthly price.

```
Procurement                                  $5/month · all venues
Handles purchasing, suppliers and ordering.
APPS         [✓] Norm Procurement   included · runs on Norm
             [✓] Loaded             included · uses the Loaded connection ●●○
             [ ] Bidfood            $4/mo · uses the Bidfood connection ○○○
WORKS WITH   Loaded ●●○ · Bidfood ○○○
                                                      [ Hire ]

HR                                           $10/month · all venues
Runs hiring, training and employee records.
APPS         [✓] Norm HR            included · runs on Norm
             [✓] BambooHR           included · uses the BambooHR connection ●●○
WORKS WITH   BambooHR ●●○
                                                      [ Hire ]
```

Every App has an enabled state. Hiring a Team Member enables its bundled
Apps by default. Additional Apps can be added or removed. Some Apps may
also have their own price. An App that runs on Norm lists nothing to
connect. Components/tools/playbooks are never shown.

## Current state (verified by exploration, 27 Sep — trust this)

- **Runtime**: `_collect_tools` (prompt_builder.py:126-135) unions ALL
  enabled AgentConnectionBindings, no agent filter; empty caps list =
  wildcard (one exists: executive_chef→norm). Interactive chat = full
  union; 7 domains are labels/prompts/tabs. Per-agent narrowing survives
  only for unattended runs (`default_tool_filter`,
  agent_config_service.py:102-118; task_scheduler.py:392,
  supervisor.py:112). Delegation deleted. `_ALWAYS_INCLUDE={show_connect}`.
- **Entitlements**: marketplace_apps (config DB) + org_app_entitlements
  (main DB); explicit row wins → bundled default → fail-open (5 documented
  fail-open points). `owns_agents` on 3 rows only → 4 of 7 agents
  ungateable. Connector/action filters live in _collect_tools (MCP inherits
  via projection.py:343-345); supervisor agent gate trims router menu only,
  SKIPPED for page-context chat (:319-323) and existing threads.
- **Web is entitlement-blind**: Sidebar.AGENTS static (:15-24);
  FUNCTIONAL_PAGES static (pageRegistry.ts, 22 pages, each {agent,
  component, loadAction}); ThreadList.tsx:150 filters p.agent===activeAgent
  only. Home tab shows all threads (retire story is free).
- **Catalog today**: 14 rows all bundled; `loaded` declares 10 components
  (each {key, agent, page}); hr-agent/procurement-agent/reports-agent carry
  owns_agents + prices; hiring/training/weekly-venue-performance rows carry
  app_slug (read by nothing). Seeded by scripts/sync_marketplace_catalog.py.
- **Billing**: get_agent_apps pivots on owns_agents; BillingTab hardcodes
  hr/procurement rows+prices (:320-323); PUT /billing/{org}/agents body
  hardcodes the two; marketplace enable/disable writes the same
  OrgAppEntitlement row (marketplace.py:82-136).
- **Components**: component_api_configs = private namespace (28 rows, 6
  components, all loadedhub); registry has 26 components (14 platform
  chrome via COMPONENT_META); 2 pages load engine_only tools through the
  working-doc loader (roster, orders) — pre-existing wart.
- **Custom apps**: App/AppVersion per-version declared reach; one door
  (app_runtime.call_action); org-bounded shares; marketplace submit derives
  connections from declared actions (tier=user, pending→approve). Entirely
  outside entitlements today. Fixtures: hiring/training (storage-only,
  agent=hr), weekly-venue-performance (loadedhub.get_sales).
- **Connect ceremony**: ConnectorConnectCard (per-venue status, OAuth
  popup/API-key form, wrong-company detection) + connect-info endpoint +
  show_connect tool + deterministic backstop — the reusable pattern.

## Architecture

### A1. Catalog: three row kinds in `marketplace_apps` (no schema change)

`tier` is free-text (config_models.py:206). Target rows:

- **`tier='agent'`** — one per hireable team member. Composition:
  `{"owns_agents": ["procurement"], "tagline": "...",
    "unlocks": ["purchasing", "receiving", "supplier-tenders"]}`.
  NO components on agent rows — Apps sit in the middle.
- **`tier='app'`** — Apps, the user-facing unit. ONE concept, two
  implementation shapes a composition may carry (both exist today):
  - `{"components": [{key, page:{id,label,icon}, full_width, description,
    connections:[...]?}]}` — a bundle of registry components (how today's
    integration-row pages ship). Components keep today's shape minus the
    per-component `agent` key (validator tolerates both during transition;
    prompt_builder's page-label loop at :811-825 and the validator's
    component collection iterate every row's components, so they work
    unmodified).
  - `{"app_slug": "hiring"}` — a pointer at an App-platform row
    (App/AppVersion), for Norm-native storage apps and anything App Builder
    makes. Connection needs derive from the version's declared actions
    (may be ZERO — Norm HR).
  The long-term direction (Aug strategy: "base apps ARE platform apps") is
  everything converging on the second shape; the first is the stepping
  stone that ships without rebuilding today's pages.
- **`tier='user'`** — published community apps: same App concept, authored
  outside Norm, pending→approve flow unchanged. Displayed identically.
  hiring/training/weekly-venue-performance platform rows: RE-TIER to 'app'
  (they are the first Norm-native, zero-connection Apps).

Integration rows are deleted. `agent_configs` stays runtime truth (slug,
prompt, bindings); catalog rows are storefront truth. Keying all new code
on tiers that exist nowhere today ('agent'/'app') is the dark-launch switch
for the shared config DB.

**Starter App slicing** (catalog data — owner-adjustable any time; Apps are
named after the product they are, each listing its OWN connection needs;
one App slug per member×product so switching Procurement's Loaded off never
touches the Chef's):
- Procurement uses: **Norm Procurement** (order workflows/tasks — runs on
  Norm), **Loaded** [loaded-procurement] (orders page + orders_dashboard,
  purchase_order_editor, invoices page + invoices_dashboard,
  receive_invoice_editor, stock_picker, supplier_tenders + page → Loaded,
  Norm Kitchen Sync), **Bidfood** (ordering → Bidfood; ships disabled —
  caps are off today).
- Executive Chef uses: **Loaded** [loaded-kitchen] (recipes/menus/
  menu-engineering pages, recipe_editor, menu_editor, menu_engineering →
  Loaded, Norm Kitchen Sync).
- Time & Attendance uses: **Loaded** [loaded-rostering] (roster page,
  roster_editor, roster_table → Loaded).
- HR uses: **Norm HR** (app_slug → the hiring + training storage apps —
  NO connections) and **BambooHR** (hiring_board + hiring page → BambooHR).
- Reports (included) uses: **Norm Reports** (report_builder,
  saved_reports_board + pages — runs on Norm, reads whatever's connected)
  and **Weekly venue performance** (app_slug → the fixture — Loaded).
- Marketing: no Apps yet — chat capabilities only (Brevo/Metricool tools);
  card says so until Brevo/Metricool Apps exist.
- Per-team-member workspace chrome (dashboard-*, tasks-* pages) rides the
  team member directly, not an App — it's their office, not a product.

### A2. Entitlements: same table, new resolver (entitlements.py)

- `ALWAYS_INCLUDED_AGENTS = frozenset({"base","router","reports","app_builder"})`
- `hired_agent_slugs(org, db, cdb) -> set[str] | None` — None = gating
  inactive (no tier='agent' rows, or no org) = fail open; else
  ALWAYS_INCLUDED ∪ owns_agents of entitled agent rows.
- `apps_on(org, db, cdb) -> set[str] | None` — tier='app'/'user' rows that
  are (used by a hired team member) ∧ (in `entitled_slugs` — explicit row
  wins, else bundled default). None when gating inactive. This is the
  per-App enable/disable: an org can switch one App off under a hired
  member; a paid App ships bundled=false and must be enabled (a billing
  act, same OrgAppEntitlement row).
- `agent_entitled()` keeps its signature, reimplemented on the hired set.
- **`unentitled_tool_actions` is KEPT and fed by App rows** (not deleted as
  originally drafted): an App may declare `tool_actions` (typically
  `connector.*` wildcards, e.g. the BambooHR App claims `bamboohr.*`) so
  disabling the App also removes its chat capabilities, with the existing
  kept-while-any-entitled-claimer semantics. `unentitled_connectors`
  likewise stays, fed by App-declared connections. Both filters are live
  and tested today — they just change diet from integration rows to App
  rows.
- Custom apps: runtime enforcement stays app_runtime.call_action's
  per-version allowlist; unpublished = share-driven (unchanged); published
  rows join `apps_on` like any App.

### A3. Tool gating (`prompt_builder._collect_tools`)

```python
_hired = hired_agent_slugs(_org, db, _cdb)
if _hired is not None:
    bindings = [b for b in bindings if b.agent_slug in _hired]
```
Tools remain bound to team members (bindings) — implementation-only, per
the model. Union semantics give "carried by two, survives while either
hired". **Second layer, unchanged code**: the existing
`unentitled_tool_actions`/`unentitled_connectors` filters right beside this
now express per-App disable — a disabled App's claimed `tool_actions`
(e.g. `bamboohr.*` on the BambooHR App) drop out of the union unless
another entitled App claims them. Two filters, two questions: "is the
member hired?" (bindings) and "is the App on?" (claims). Details:
- Empty-caps wildcard binding re-homed executive_chef→norm ⇒ base→norm in
  the rollout script (new `base` agent_configs row); validator forbids
  wildcards on hireable slugs thereafter.
- Router ∈ ALWAYS_INCLUDED (its 3 norm caps always present).
- MCP inherits via projection.py:343-345; ALWAYS_EXPOSE resolve_dates
  intact; machine tokens without user_id fail open (follow-up: thread org).
- Unattended runs: scheduler (~task_scheduler.py:370) + Run-Now
  (supervisor.py:90-123) skip a fired member's tasks with a
  `skipped_unhired` run record.
- Supervisor holes closed: page-context domain (:319-323) validated against
  hired set; router-menu gate (:334-338) switches to hired set. Existing
  threads on a fired member continue degraded (tools already gone from the
  union); re-hire restores.

### A4. Apps own pages; connections computed at App level

New `app/services/agent_catalog.py`:
- `required_connections_for_app(app_slug, cdb)`: union over the app's
  components of (component_api_configs connectors) ∪ (explicit
  `connections:[...]` on the component entry — needed for self-loading
  components like supplier_tenders→cook_brothers_app,
  invoices_dashboard→loadedhub). Components carry the requirement; the App
  inherits it. 60s TTL cache.
- `works_with(agent_slug)`: union of its unlocked Apps' requirements (the
  card's WORKS WITH line). Chat-only connectors not carried by any App
  (e.g. gmail send) are handled reactively by the existing
  show_connect/ConnectorConnectCard path — deliberately NOT shown on cards.
- New endpoint `GET /api/team` (routers/agents.py): `{gating_active,
  always_included:[...], members:[{slug, catalog_slug, name, icon, hired,
  hireable, price_cents, tagline, apps:[{slug, name, enabled, bundled,
  price_cents, pages:[{id,label,icon}], required_connections:[{connector,
  display_name, per-venue status}]}]}]}` — `enabled` is the per-App switch
  state. `GET /api/marketplace` keeps serving the storefront rows.

Web:
- `useTeam` hook (one fetch; error or gating_active=false → everything on).
- Sidebar.tsx: AGENTS stays the icon registry; render home + reports +
  app_builder (always) + hired members + "**+ Hire**" → the team page.
- Pages resolve through Apps: FUNCTIONAL_PAGES entries gain `app` (the app
  slug; workspace chrome pages keep `agent`); ThreadList filters a page in
  iff (its app ∈ apps_on) or (chrome ∧ member hired) — so switching one App
  off removes exactly its pages. Pinned custom-app
  pages whose App.agent is unhired fall back to the Norm/home menu
  (appPageConfig fallback 'app_builder' → 'home') — an app is never
  invisible, never a reason to hire.
- page.tsx snaps activeAgent/activePage to home when its owner leaves the
  hired set (state-driven nav = the whole guard surface).
- **Degraded state**: pages never blank — FunctionalPage gains a connection
  guard using the App's required_connections and renders the shared
  ConnectionPanel ("Orders needs Loaded connected for Bessie's"), the
  proactive twin of the existing norm:connector-auth reactive swap
  (page.tsx:146-165).

### A5. The team page (marketplace, reborn)

AppsDashboard.tsx → **TeamPage.tsx** ("Your AI Team"), own destination via
the sidebar "+ Hire"; everyone can view; owners get live buttons;
**Settings→Apps tab dies**. Three shelves:
1. *Your team* — Norm + Reports + App Builder pinned "Included free ·
   always on" (no retire), then hired members (with "Needs connections"
   badge where an enabled App has unconnected requirements). **The member's
   card is the PRIMARY place Apps are managed.** Expanding a hired member
   shows every App available to that role — built-in, marketplace, or
   customer-built, one consistent list:

   ```
   HR
   ├── Norm HR        [On]
   ├── BambooHR       [On]
   ├── Training App   [Off]
   └── + Add App
   ```

   Switches are owner-gated (same marketplace enable/disable endpoints);
   a priced App shows its price beside the switch. "+ Add App" opens a
   picker over the same catalog the discovery shelf shows (community +
   team-built Apps suited to the role). Switching an App off removes its
   pages and chat capabilities only.
2. *Available to hire* — the cards (format above: one-liner, INCLUDES =
   Apps, WORKS WITH = per-venue dots via /api/connectors/{name}/
   connect-info), price "$X/month · your whole organisation, all venues",
   [Hire]. Hire → inline Stripe card capture if none (reuse
   PaymentMethodForm) → POST /marketplace/{slug}/enable → connection
   checklist (shared ConnectionPanel per App requirement) → tabs/pages
   appear, toast "Procurement has joined your team."
3. *More apps* — a DISCOVERY shelf, not the management surface (that's the
   member card): "Built by your team" (open inline, Publish) + "From the
   community" (tier='user'; admin Approve stays). SAME species as the Apps
   on the member cards: each shows which member it appears under ("Appears
   under: HR" — App.agent, changeable) and its own connection needs; adding
   one from here is the same act as "+ Add App" on the member card. Line
   atop the shelf: "Your team members come with their Apps. Add more here —
   or build your own by chatting with Norm."

Retire: confirm dialog naming consequences (Apps + tab leave, routines
pause, threads/records stay readable from Home, "Loaded stays connected —
Executive Chef still uses it" computed from used-by data, billing stops
next cycle).

### A6. Connections for civilians

Settings→Connections becomes a connections×venues status matrix (Connected /
Needs attention / Not connected) with **Used-by chips listing Apps** (not
team members): "Used by: Purchasing, Receiving + 1 of your apps". The admin
spec editor moves to an admin-only "Connector Specs" tab. Venues tab keeps
per-venue credentials with the same chips. **ConnectionPanel** extracted
from ConnectorConnectCard.tsx, reused by chat card, hire checklist, matrix,
page guards. cook_brothers_app is never a user-facing brand: shown as
"Norm Kitchen Sync — managed by Norm", surfaced only when broken.
Components stay hidden: ComponentsPanel remains admin-only (already is).

### A7. Billing — team members AND Apps are both billable

- get_agent_apps pivots on `tier=='agent'` (owns_agents fallback during
  transition). NEW sibling `get_priced_apps(db, org, cdb)`: entitled
  tier='app'/'user' rows with price_cents>0 whose team member is hired.
  get_billing_info's monthly cost = plan + Σ entitled member prices +
  Σ on-and-priced App prices + $10×venues; cost_breakdown gains an `apps`
  line; create_subscription emits Stripe items for both via
  stripe_price_key (a priced App needs a key before launch — validator
  warns on priced-row-without-key).
- Enable/disable for BOTH levels = the existing marketplace endpoints
  (marketplace.py:82-136), `billing:manage`-gated, writing the one
  OrgAppEntitlement row that is simultaneously access and the charge.
- BillingTab: hardcoded rows (:320-323) and summary (:302) → dynamic
  read-only "Your team" (members + any paid Apps, real prices from the
  API) + "Manage on the team page →".
- Legacy PUT /billing/{org}/agents generalizes to dict[str,bool] for one
  release, then dies. Deferred: mid-cycle Stripe item add/remove (wire
  stripe_subscription_item_id when repricing).

### A8. Validator additions (config_validator.py)

V1 agent rows' owns_agents exist in agent_configs; every non-included agent
with enabled bindings owned by exactly one agent row. V2 every enabled
binding's slug is always-included or owned. V3 empty-caps wildcards only on
always-included slugs. V4 every `unlocks` entry names an existing tier='app'
row; every app row's components exist in the registry mirror; every
component with a page belongs to an app that some team member unlocks (else
the page is unreachable). V5 (cleanup) no integration-tier rows remain.

## Rollout (config DB shared by ALL envs — every step neutral both ways)

**Phase A — code, dormant (normal deploy):** resolvers (hired_agent_slugs,
apps_on) + ALWAYS_INCLUDED; _collect_tools hired filter beside
the old filters; supervisor/task gates; agent_catalog.py + GET /api/team +
marketplace required_connections; billing generalization; web useTeam/
Sidebar/ThreadList/page-guard/TeamPage (all fail-open on
gating_active=false); validator V1–V4; rewrite sync_marketplace_catalog.py
to the target catalog (repo only, not run).

**Phase B — one idempotent config-DB script**
(`scripts/sync_team_marketplace_rollout.py`, --dry-run first):
a. upsert agent_configs row `base`; move the executive_chef→norm empty-caps
binding to base→norm; b. create tier='app' rows (Purchasing, Receiving,
Supplier Tenders, Recipes & Menus, Menu Engineering, Rostering, Hiring,
Reports) with components migrated off the integration rows; re-tier
hiring/training/weekly-venue-performance to 'app'; c. flip hr-agent/
procurement-agent to tier='agent' (names → "HR"/"Procurement", prices
unchanged) + set `unlocks`; create executive-chef-agent,
time-attendance-agent, marketing-agent (bundled, $0, plain titles, unlocks
set); d. delete reports-agent row (Reports is always-included; orphan
entitlement rows are ignored by design); e. delete the 8 integration rows
LAST (components already re-homed). Neutrality: new code → all bundled →
hired = everything → tool union byte-identical (prove by snapshot diff);
old code (any lagging env) → owns_agents still readable, connector filters
lose their claims → block nothing (fail-open), cosmetic only.

**Phase C — verify** (below); nothing flips on day one. Unbundling/pricing
is later data-only work by the owner.

**Phase D — cleanup deploy:** `unentitled_connectors`/
`unentitled_tool_actions` are RETAINED (now fed by App rows — the per-App
disable path); drop the owns_agents billing fallback; delete legacy
PUT /billing/{org}/agents; remove per-component `agent` key tolerance;
validator V5.

## Verification

1. Unit: entitlements hired/apps_on matrix (no agent rows→None; bundled
   default; explicit-off fires member + its apps; disabling ONE App under a
   hired member removes its pages + claimed tool_actions while sibling apps
   survive; paid App bundled=false stays off until enabled; always-included
   immune; catalog exception→open); binding filter incl. wildcard-on-base +
   two-owner survival; supervisor page-context + router gates; scheduler
   skipped_unhired; billing: dynamic agent_apps + priced-Apps line +
   priced-row-without-stripe-key validator warning; /api/team shape +
   app-level required_connections (component_api + explicit component
   connections) + per-App enabled state; validator V1–V4 fixtures; MCP:
   fired member's tools leave projection, resolve_dates stays.
2. Phase-B neutrality proof: sorted (connector,action) snapshot of
   _collect_tools for a real org before/after the seed — empty diff;
   /api/team shows everyone hired.
3. POST /internal/validate-config clean in every env after Phase B.
4. Playwright (staging): retire Procurement → tab + Purchasing/Receiving/
   Supplier-Tenders pages gone, PO editor absent from chat, tools absent,
   billing line off; re-hire restores; hire dialog shows the Purchasing
   App's Loaded checklist with real per-venue state.
5. Full gates: API suite + web lint/tsc/vitest; sync scripts dry-run first
   (shared config DB is live everywhere); commit only when asked.

## Out of scope / follow-ups

- Repricing chef/T&A/marketing + Stripe price creation (data-only later).
- Mid-cycle Stripe item add/remove on hire/retire.
- Migrating first-party Apps onto the App/AppVersion platform proper (the
  Aug "base apps ARE platform apps" strategy) — the tier='app' catalog rows
  are the stepping stone, not the end state.
- Threading org explicitly through _collect_tools for MCP machine tokens.
- Admin-UI lifecycle redesign (Agent Configs / Connector Specs) — separate
  track. Marketing's first App (Campaigns). Per-venue pricing (no
  primitive; org-level stands).

## Build notes — deviations from the approved plan (Phase A, 27 Sep)

1. **One script, not two**: `sync_marketplace_catalog.py` was rewritten as
   both the target-catalog source of truth AND the Phase-B rollout (base
   agent row, wildcard-binding re-home, retired-row deletes) — one script
   can't drift from itself. Verified with `--dry-run` against the live
   config DB; NOT applied.
2. **`/api/team` carries the hire checklist**; the marketplace router was
   left untouched (the plan optionally added required_connections there).
3. **Hire ships without inline Stripe card capture** — all newly hireable
   members launch at $0 and hr/procurement already bill through the existing
   subscription flow; mid-cycle Stripe item add/remove was already deferred.
4. **Apps under always-included members aren't catalog rows yet**: Reports'
   pages stay member chrome (no "Norm Reports" App row), and
   weekly-venue-performance is re-tiered but in nobody's `unlocks` — it
   works through App-platform pinning as before. Add an `included_with`
   composition key when an always-included member needs a switchable App.
5. **BambooHR App naming**: the catalog row slug is `bamboohr-app`
   (`bamboohr` was the retired integration row); Norm HR keeps slug
   `hiring`, Training keeps `training` (both re-tiered + renamed) — no
   entitlement-row migration needed anywhere.

## Pre-push adversarial review (83 agents, 27 Sep) — what it changed

26 raw findings, 19 confirmed by 3-refuter panels, resolving to 8 issues:
1. **The FunctionalPage connection guard was REMOVED before shipping.** It
   would have walled every Tasks page on deploy day (the internal `norm`
   connector never reads "connected") and broken the working BambooHR Hiring
   page in prod (global basic-auth row has no OAuth token) — connect-info's
   per-venue status is OAuth-shaped and cannot describe internal/global/
   api-key connectors. The reactive norm:connector-auth path (which works
   today) remains the degraded-state story. Follow-up: reintroduce the guard
   only after connect-info exposes a truthful `configured` signal, and make
   the OAuth completion actually dismiss the wall.
2. Scheduler's `skipped_unhired` early-exit now returns the function's dict
   contract, not an ORM object.
3. `PUT /billing/{org}/agents` now binds org_id to the caller's own org.
   Follow-up: the OLDER billing endpoints never did (pre-existing) — audit.
4. **The seed ships NO `tool_actions` and NO top-level `connections`** on any
   row: with integration rows gone, a single-claimer row would let one App
   switch black-hole a whole connector org-wide (the
   weekly-venue-performance/loadedhub footgun). An App switch gates its
   pages and components; chat tools gate by team member. Follow-up: per-App
   chat-tool claims with exact action lists. `component_connections` carries
   display-only requirements.
5. Settings → Connections gated on settings:connectors (connect-info's own
   gate); pre-seed empty-state copy no longer sells hiring.
6. Mobile: page list is App-gated like desktop, and a team button +
   destination exist.
7. Accepted as designed: existing threads / follow-up continuation on a
   retired member run degraded (their tools are gone from the union) rather
   than being blocked.
