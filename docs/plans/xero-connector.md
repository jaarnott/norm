# Xero connector

Status: **plan, decisions 2.1–2.3 and 2.5–2.6 taken 17 Oct 2026** (new "Norm"
Xero app; supplier bills first; Loaded does not push to Xero, so no double-up;
scopes wide — §2.3; a new **Finance** team member owned by a **Xero App** —
§2.5/§4.6; a **bills view** in the first build — §4.7; one connector covers NZ
and AU — §2.6). Nothing built yet.

Build from `origin/main` (`aa1ab69`, Norm Functions, 16 Oct) in a clean
worktree under `~/projects`: the main checkout is 25 commits behind it, and the
Functions rollout is the template for the member, the App and the pages.

## 0. Where it stands (17 Oct 2026, end of day)

**Built and verified locally, uncommitted, in `~/projects/norm-xero`** (a
worktree off `origin/main`). Config is live in the shared config DB. See §8
for the production run order and §9 for what is deliberately not done.

| Phase | State |
|---|---|
| 0 — Xero app, spec, scope check | done; 22 scopes accepted on the first consent |
| 1 — connect hook, refresh policy, disconnect | done; 12 tests; verified live |
| 2 — reads, Finance member, Xero App, bills view | done; 4 read consolidators (15 tests), Finance hired locally, Bills page + chat block render live data |
| 3 — `create_bill` | done; 10 tests; previewed live against the real organisation (reads real, writes captured); **no real bill created yet** — that wants the Demo Company |
| 4 — bills from the invoice pipeline | done; `services/xero_bills.py` + both receive paths (8 tests); off until a venue ticks "Create a bill in Xero when Norm receives an invoice" |

Verification run (17 Oct): `ruff check` / `ruff format --check app/` clean;
full API suite on an isolated DB: 4,809 passed, 8 failed, all 8 resolved —
five were the consolidator budget guard wanting action-named files (renamed),
one a test that used "finance" as its example non-member (changed), two the
MCP enabled-gate tests tripped by `MCP_ENABLED=true` sourced from `.env` into
the test environment (pass with a clean env); the affected files re-run green
(927 + 71 + 13). Web: lint 0 errors, `tsc` clean, vitest 356 passed.

What the live checks showed: `get_bills` through the UI route and through
the Finance member in chat (one turn: "80 unpaid bills, 20 overdue, $14,403.87"
with the overdue suppliers tabled from the tool result); an unconnected venue
is refused before any Xero call; the settings panel loads the chart of
accounts and saves the mapping by merging into the venue's connection.

Goal: a `xero` connector in Norm that connects each venue to its Xero
organisation, reads accounting data, and **creates invoices** (supplier bills
first, sales invoices after). It reuses Norm's existing OAuth2 connector
machinery and ports what is worth keeping from Orbit's Xero integration.

---

## 1. What already exists

### In Norm (reuse, don't rebuild)

Norm already has a complete browser-redirect OAuth2 flow with PKCE, refresh,
rotation handling and a scheduled keep-alive. LoadedHub, Gmail, Outlook and the
Cook Brothers App all use it.

| Piece | Where |
|---|---|
| Connector spec (config DB) | `ConnectionSpec`, table `connector_specs`, `apps/api/app/db/config_models.py:45-71`. `auth_type="oauth2"` + `oauth_config` {`authorize_url`, `token_url`, `client_id`, `client_secret`, `scopes`, `pkce`, `token_endpoint_auth_method`} |
| Per-venue tokens | `Connection`, table `connector_configs`, `apps/api/app/db/models.py:434-467`. Unique on (connector, venue). `config` JSON is `{{ creds.* }}` in templates; `access_token`, `refresh_token`, `token_expires_at`, `oauth_metadata`, `needs_reconnect` |
| Authorize + callback | `apps/api/app/routers/oauth.py` — `GET /oauth/authorize/{connector}?venue_id=` (:60), `GET /oauth/callback` (:221). Callback URL = `{base}/api/oauth/callback`; cloud URLs registered at :45-49 |
| Token service | `apps/api/app/services/oauth_service.py` — `build_authorize_url` (:121), `exchange_code` (:178), `refresh_access_token` (:283, row lock, rotating refresh tokens, sets `needs_reconnect` on 4xx), `get_valid_access_token` (:406), `refresh_all_tokens` (:437) |
| Keep-alive | Cloud Scheduler `norm-refresh-tokens-{env}` every 15 min → `POST /internal/refresh-tokens` (`infra/terraform/scheduler.tf:133-160`). **Force-redeems every enabled oauth2 row** |
| Request auth | `spec_executor._apply_auth` (`apps/api/app/connectors/spec_executor.py:81-151`): `oauth2` → bearer from `get_valid_access_token(venue_id=…)`; 401 → one forced refresh + retry (:665-740) |
| Per-row headers | each endpoint row carries its own `headers`, e.g. LoadedHub's `{"x-loaded-company-id": "{{ creds.x_loaded_company_id }}"}`. There is no spec-level default header |
| Connect UI | `apps/web/app/components/display/ConnectorConnectCard.tsx` — per-venue Connect/Reconnect button, popup, `oauth-complete` postMessage. Shows "connected as" from `oauth_metadata.venue_name`/`userName` (`routers/connectors.py:88-91`) |
| Spec authoring | sync scripts in `apps/api/scripts/` (`sync_cook_brothers_app.py` is the newest OAuth spec; `sync_menus_config.py` has a read+write tool pair), consolidator code in `apps/api/config/consolidators/`, rules in that folder's `README.md` |
| Writes + approvals | tool row `effect: read|draft|write` + `approval: {default, allow_auto, label}` (`spec_rows.py:119-142`), labels installed by `scripts/sync_approval_labels.py`, gate in `services/approvals.py:67-95`, previews via `preview(card)` in the consolidator (`services/previews.py`) |
| Exposure | an App row in `marketplace_apps` must claim `xero.<tool>` for the agent or MCP to see it (`scripts/sync_marketplace_catalog.py`) |

Connector-specific callback logic is **hardcoded by connector name** in
`oauth_callback` (LoadedHub company mapping :270-383, Gmail userinfo :386-418).
Xero needs a third branch; see §4.2.

There is no Xero connector today. The only mentions are a stale
`procurement-dashboard` template calling `xero.get_monthly_spend`
(`app/db/dashboard_templates.py:32,84`) that was re-pointed to Loaded.

### In Orbit (port what's useful)

Repo `jaarnott/orbit-app` (readable via `gh api`). Supabase edge functions:

| Function | What it does | Port? |
|---|---|---|
| `_shared/xeroClient.ts` | constants, AES-GCM refresh-token encryption, `getValidAccessToken` (refresh within 60 s of expiry, marks `needs_reconnect` on `invalid_grant`), `xeroFetch` (adds `Xero-tenant-id`), `logSync` | Norm already has the equivalents; the **scope list** and the **tenant header** are the facts to carry over |
| `xero-oauth-start` / `xero-oauth-callback` | authorize URL with `prompt=consent`; callback exchanges code, `GET /connections`, **upserts every tenant** with the same refresh token | Norm's flow replaces both. Do **not** copy the "write every tenant" behaviour (see §3, token sharing) |
| `xero-keepalive` | refreshes every active connection | Norm's scheduler already does this |
| `xero-list-accounts`, `xero-list-contacts` | `GET /Accounts`, `GET /Contacts` | yes → read endpoints |
| `xero-push-entertainer-invoice` | creates an **ACCPAY bill**: `POST /Invoices` with `Type`, `Contact.ContactID`, `Date`, `DueDate`, `LineAmountTypes` (`Exclusive`/`NoTax`), `Reference`, `Status` (`DRAFT`/`AUTHORISED`), `LineItems[{Description, Quantity, UnitAmount, AccountCode, TaxType}]`; then `PUT /Invoices/{id}/Attachments/{name}?IncludeOnline=true` with the PDF; voids a previous bill with `Status: VOIDED` on re-push | the payload shape is the template for `create_bill`. Entertainers themselves are out of scope (user: no entertainers in Norm) |
| `xero-push-wage-journal`, `-preview-`, `-void-`, `-refresh-…-status` | manual journals from Loaded labour, per-venue account mapping (`xero_account_mappings`, `line_type` → account code) | **later**, decision §2.2 |
| `xero-push-intercompany` / `-preview-` | ACCREC + ACCPAY pairs between entities | **later**, decision §2.2 |
| `xero-disconnect` | `DELETE /connections/{id}` to free the tenant slot, then removes the row | yes, as the Xero branch of Norm's disconnect |

Orbit's scopes: `offline_access accounting.manualjournals accounting.contacts
accounting.invoices accounting.settings.read
accounting.reports.profitandloss.read`. So the answer to "we may already have
invoice scopes" is **yes** — Orbit's app already requests `accounting.invoices`,
which is what bills and sales invoices need. It does **not** request
`accounting.attachments`, yet it uploads attachments (the failure is caught and
logged, so it may have been silently failing).

---

## 2. Decisions needed before building

Recommendations first.

### 2.1 Separate Xero app for Norm (recommended) vs reuse Orbit's client

**Decided 17 Oct: new app named "Norm" in the same Xero developer account.**
Zero cost, takes minutes. Reasons:

- Xero scopes are **requested at authorize time, not configured on the app**,
  so sharing a client buys nothing on scopes.
- A disconnect in Orbit (`DELETE /connections/{id}`) revokes that app's access
  to the tenant; with a shared client it would kill Norm's connection too.
- Separate client secret, separate redirect URIs, separate branding on the
  consent screen, and Orbit can be retired without touching Norm.
- Each uncertified app has its own 25-connection cap (§3).

Caveat that matters: **apps created on or after 2 March 2026 cannot request
the old broad scopes** (`accounting.transactions`, `accounting.reports.read`);
they must use the granular ones. Orbit's list is already granular, so a new app
is fine. Verify on the first consent.

### 2.2 First write use-case

**Decided 17 Oct: supplier bills (option 1) first.** Loaded does **not** push
received invoices to Xero for these venues (user), so Norm creating bills is
not a double-up. Functions invoices (2) and the Orbit ports (3) follow.

The connector and `create_bill` / `create_invoice` tools are the same either
way; this decides the integration work in Phase 4.

1. **Supplier bills (ACCPAY) from the invoice pipeline** — Norm already
   extracts and reviews supplier invoices (`services/invoice_replica.py`,
   `received_invoice.py`) and receives them into Loaded. Creating the
   matching bill in Xero, with the PDF attached, is the accounts-payable leg
   and fits the "remove Loaded entirely" direction.
   **Blocking question: does Loaded already push received invoices to Xero
   for these venues?** If yes, Norm creating bills would double them up until
   Loaded's Xero sync is switched off per venue (or Norm only handles invoices
   Loaded never sees, e.g. non-stock invoices).
2. **Sales invoices (ACCREC) for functions/events** — deposits and balances
   for the Functions port (`docs/plans/norm-functions-port.md` has `events`
   with `invoice-to contact` and `deposit`). Needs a Xero contact per client.
3. **Port Orbit's wage journal / intercompany pushes** — these are the
   things Orbit's Xero integration is used for today. Wage journals need
   `accounting.manualjournals`; adding a scope later forces every venue to
   re-consent (§3), so **decide now whether to include it in the scope list**.

Recommendation: build the connector + reads + `create_bill` (Phases 0–3) and
exercise it on Xero's Demo Company; choose between 1 and 2 for Phase 4 once
the Loaded question is answered. Include `accounting.manualjournals` in the
scope list now if a wage-journal port is likely within months.

### 2.3 Scope list (decide once; changes mean re-consent for every venue)

**Decided 17 Oct: go wide**, because a later addition forces every venue to
reconsent. "Wide" has to mean *every open scope*, not *every scope*: a
request that includes a scope the app is not entitled to fails the whole
authorize step with `invalid_scope`, so nothing connects at all.

Proposed string (one line in `oauth_config.scopes`):

```
openid profile email offline_access
accounting.invoices accounting.payments accounting.banktransactions
accounting.manualjournals accounting.contacts accounting.attachments
accounting.settings accounting.budgets.read
accounting.reports.profitandloss.read accounting.reports.balancesheet.read
accounting.reports.aged.read accounting.reports.banksummary.read
accounting.reports.executivesummary.read accounting.reports.trialbalance.read
accounting.reports.taxreports.read
files assets projects
```

- Write scopes include read, so no `.read` twins are needed.
- `openid` is for the id_token's authentication event id, used to pick the
  tenant the user just authorised (§4.2). `offline_access` is required for a
  refresh token.
- `accounting.invoices` covers invoices, credit notes, purchase orders, quotes,
  repeating invoices and items (bills are `Type: ACCPAY` invoices).
- `accounting.settings` (write, not just read) lets Norm create a tracking
  option per venue and read accounts and tax rates.
- `accounting.payments` / `banktransactions` are included only for width;
  Norm has no endpoint rows for them, so the agent cannot reach them until
  someone adds tools.

**Deliberately excluded** (requesting them breaks the connect):

- `accounting.transactions`, `accounting.reports.read` — deprecated; a new
  app gets `invalid_scope`.
- `accounting.journals.read` — Xero's Advanced tier plus explicit use-case
  approval ("Do not request accounting.journals.read or bankfeeds without
  prior approval from Xero").
- `bankfeeds`, `paymentservices`, `finance.*` — financial-services contracts /
  certification.
- `payroll.*` — approval status unconfirmed and payroll lives in Loaded.
- `accounting.classicexpenses` — a deprecated endpoint.

**Verify the exact string on the Demo Company before any venue connects.**
The granular report scope names come from third-party docs that disagree
(`balancesheet` vs `balancesheets`), and `files`/`assets`/`projects`
approval status is not stated anywhere I could read. One consent attempt
settles it: on `invalid_scope`, bisect the list. This costs minutes and
belongs in Phase 0.

**Downsides of going wide, so they're on record:**

1. **Blast radius.** Norm stores tokens in plain text (§2.4). A leaked Xero
   refresh token is 60 days of *write* access to payments, bank transactions
   and settings, not just invoices. Wide scopes make encryption at rest
   (§2.4) more urgent, not optional.
2. **Nothing else widens.** In Norm a scope exposes nothing by itself; only
   endpoint rows, App claims and the approval gate decide what the agent can
   do. So wide scopes don't make Norm more capable or more dangerous *through
   the agent*; the risk is the token.
3. **Consent screen.** The person authorising each org sees the full list
   ("manage bank transactions, payments, settings…"). Fine for Cook Brothers'
   own entities; a future external customer's accountant may ask why.
4. **Certification review.** Past the uncertified cap Xero's app review asks
   you to justify every scope requested. Unused wide scopes are friction then,
   and narrowing later is itself a re-consent.

### 2.4 Token storage encryption — built, not switched on in production

Correction (17 Oct): `origin/main` already encrypts `Connection.access_token`
and `refresh_token` at rest (`app/db/encrypted.py`, `EncryptedText`, landed
with Functions in `aa1ab69`). It is keyed by `TOKEN_ENCRYPTION_KEYS`
(comma-separated Fernet keys, newest first); with no key set it writes plain
text and logs one warning, and `scripts/encrypt_connection_tokens.py --apply`
back-fills existing rows once a key exists.

**Production's API has no `TOKEN_ENCRYPTION_KEYS`** (checked 17 Oct with
`gcloud run services describe norm-api-production`), so every connector's
tokens there are still plain text. A Xero refresh token under the wide scope
list is 60 days of write access to the books, so before the first production
venue connects to Xero: generate a Fernet key, add it as a Secret Manager
secret in `norm-production-491101`, wire it into the API's env (the
`secrets` Terraform module), restart, then run the back-fill. That is a
deliberate production change for the user to green-light, not part of this
plan's code.

### 2.5 Finance member and Xero App (user, 17 Oct)

**Decided: a new team member "Finance"** (slug `finance`), owned by a new
**Xero App** (slug `xero`) that claims the `xero.*` tools and the bills
component. The pattern is the Functions rollout on `origin/main`
(`scripts/sync_norm_functions.py` + `services/functions/catalog.py` +
`sync_marketplace_catalog.py`); details in §4.6.

One sub-decision is open — **hireable or bundled?** `_member()` in
`sync_marketplace_catalog.py:129-149` has no `bundled` option, so a member
row is on for every organisation the moment the catalog is seeded, at the
default `AGENT_PRICE_CENTS` ($10), and `sync_agent_count` pushes that to
Stripe. **Recommend** adding `bundled=` to `_member` (it already exists on
`_app`), seeding Finance `bundled=False`, and hiring it for Cook Brothers from
the Team page (`POST /api/marketplace/finance-agent/enable`). Functions used a
per-organisation mode instead; that is heavier than Finance needs.

### 2.6 Xero AU — no separate connector

**One Xero app, one connector, one scope string serve every region.**
`login.xero.com`, `identity.xero.com` and `api.xero.com` are global. An
organisation's country, base currency, GST rates and tax-type codes are
properties of the **tenant**, which Norm reads once on connect from
`GET /Organisation` (`CountryCode`, `BaseCurrency`, `ShortCode`, `Timezone`)
and keeps in `Connection.config` (§4.5). One Xero login can hold NZ and AU
organisations side by side, and the per-venue Connect button binds one per
venue. The only region-split Xero APIs are Payroll (AU, NZ and UK have
different scopes and base paths), which §2.3 leaves out.

Two things do differ per tenant and the consolidators must never hardcode
them: tax types (NZ `INPUT2` at 15% vs AU `INPUT` at 10%, read from
`GET /TaxRates`) and the currency on every amount Norm shows. Venues carry a
`timezone` but no country or currency (`models.py:137`), so the tenant is the
source of both.

---

## 3. Xero facts that shape the design

Verified against Xero's published identity OpenAPI spec
(`XeroAPI/Xero-OpenAPI/xero-identity.yaml`) and secondary sources; Xero's own
doc pages render only in a browser, so **each "verify" below is a Phase 1
check**, not a blocker.

| Fact | Consequence |
|---|---|
| Access token lives **30 min**; refresh token is **single-use, rotated on every use**, expires after **60 days unused** | Norm's `refresh_access_token` already handles rotation under a row lock and persists the new token before returning. Keep-alive cadence is irrelevant to liveness here (anything under 60 days keeps it alive) |
| Keep-alive force-redeems every 15 min | A Xero venue would rotate its refresh token ~96×/day. Works, but wasteful and every rotation is a chance to lose the chain if the DB write fails. Add `oauth_config.refresh_policy: "expiring"` (refresh only when the access token is within ~10 min of expiry) and use it for Xero; LoadedHub keeps the default `force` (its refresh tokens die in ~24 h) |
| One refresh token per (app, user) consent. Orbit copies the same token onto every tenant returned by `/connections` | **Never share a token chain between venue rows.** In Norm each venue connects separately and gets its own consent → its own chain. The callback must bind only the tenant from *this* consent to *this* venue, and must never write other venues' rows |
| `GET https://api.xero.com/connections` returns `[{id, tenantId, tenantName, tenantType, authEventId, createdDateUtc, updatedDateUtc}]`; `?authEventId=` filters to one consent. `authEventId` = "identifier shared across connections authorised at the same time" | With `openid`, the id_token carries the authentication event id (claim name to verify on first callback; expected `authentication_event_id`). Filter `/connections` by it to get exactly the tenant(s) the user just picked. Fallback: newest `updatedDateUtc` |
| The consent screen lets the user pick **one organisation per consent**; connecting another org means running the flow again (verify) | Matches Norm's per-venue Connect button exactly |
| Every API call needs header `xero-tenant-id: <tenantId>` | Each endpoint row: `"headers": {"xero-tenant-id": "{{ creds.xero_tenant_id }}", "Accept": "application/json"}`; the callback writes `xero_tenant_id` into `Connection.config` |
| `DELETE /connections/{id}` (the connection `id`, not `tenantId`) frees a tenant slot; 204 | Xero branch of disconnect, so slots don't leak |
| **Uncertified apps are capped at 25 connected organisations**; certification needs real customer connections | Fine for Cook Brothers' entities. **Not fine once Norm sells to other groups** — Xero app certification / App Partner becomes a prerequisite for selling the Xero feature |
| Rate limits: **60 calls/min and 5,000/day per tenant per app, 5 concurrent** | Consolidators must batch (`GET /Invoices?page=`/`where=`), not loop per record. Fan-out cap (8) + `max_api_calls` already bound this |
| Token endpoint `https://identity.xero.com/connect/token`: docs show HTTP Basic client auth; Xero runs IdentityServer, which normally also accepts `client_id`/`client_secret` in the body | Norm only sends the secret in the body. **Verify on first exchange**; if rejected, add `token_endpoint_auth_method: "client_secret_basic"` support to `exchange_code` and `refresh_access_token` (10 lines, generic) |
| Norm sends `scope` on refresh when configured (LoadedHub needs it) | Xero accepts `scope` on refresh if it's a subset; verify. If Xero objects, gate that line on a spec flag |
| PKCE is Xero's public-client flow; a web app uses the client secret | Set `pkce: false` in `oauth_config` to avoid surprises |
| `Idempotency-Key` header (≤128 chars) on POST/PUT; `PUT /Invoices` strictly creates, `POST` creates-or-updates; `?unitdp=4` for 4-dp unit amounts; `?summarizeErrors=false` returns per-item validation errors | `create_bill` sends an idempotency key derived from the Norm invoice id, uses `PUT` for create, and `unitdp=4` |
| NZ GST on purchases is `TaxType: INPUT2` (15%); AU is `INPUT` (10%). Tax types are per tenant country | Read `GET /TaxRates` per tenant once and map; don't hardcode `INPUT2` (Norm has AU venues) |
| Xero gives every Xero login a resettable **Demo Company** | Local dev connects a test venue to the Demo Company; all write testing happens there, never on a live org |
| One global API for every region; country, currency, tax rates and `ShortCode` are per **tenant** (`GET /Organisation`); only Payroll has per-country APIs | One connector for NZ and AU (§2.6). Store `xero_country`, `xero_currency`, `xero_short_code` on connect; tax types from `GET /TaxRates` |
| Deep links (per Xero's own MCP server, `deeplinks.ts`): bill = `https://go.xero.com/organisationlogin/default.aspx?shortcode={ShortCode}&redirecturl=/AccountsPayable/Edit.aspx?InvoiceID={InvoiceID}`; sales invoice = `https://go.xero.com/app/{ShortCode}/invoicing/view/{InvoiceID}` | The bills view's "Open in Xero" link. Verify both in a browser once |

---

## 4. Design

### 4.1 Connector spec (config DB, via a sync script)

`apps/api/scripts/sync_xero_connector.py` modelled on `sync_cook_brothers_app.py`
(same `_PRESERVE_OAUTH_KEYS` pattern so re-runs never clobber the secret):

```python
CONNECTOR = "xero"
spec = ConnectionSpec(
    connector_name="xero", display_name="Xero", category="accounting",
    execution_mode="template", auth_type="oauth2",
    auth_config={"token_field": "access_token"},
    base_url_template="https://api.xero.com/api.xro/2.0",
    oauth_config={
        "authorize_url": "https://login.xero.com/identity/connect/authorize",
        "token_url": "https://identity.xero.com/connect/token",
        "scopes": "<§2.3 list>",
        "pkce": False,
        "refresh_policy": "expiring",        # new, see §4.3
        # client_id / client_secret pasted in Settings → Connector spec editor,
        # or passed to the script once; preserved on re-run.
    },
    credential_fields=[
        # Written by the callback hook, never typed: the connect card shows a
        # credential form only for NON-OAuth connectors, so per-venue settings
        # such as account codes cannot live here (see §4.5).
        {"key": "xero_tenant_id", "label": "Xero organisation id (set when the venue connects)",
         "secret": False, "required": False},
    ],
    endpoints=[...§4.4...], tools=[...§4.4...], enabled=True,
)
```

**Done 17 Oct** — `apps/api/scripts/sync_xero_connector.py` (in the worktree
`~/projects/norm-xero`, off `origin/main`) created the row: 22 scopes, 10
engine-only endpoints, `tools=[]`, no client id or secret. `validate_config`
reports nothing about Xero (7 pre-existing issues elsewhere). The dev servers
now run from that worktree, and the dev DB was migrated forward to
origin/main's head (`fa0b1c2d3e4f`); the stale main checkout's API no longer
matches the dev DB.

The config DB is shared, so the spec is live in every environment the moment
the script commits. That's harmless: nothing can call it until a venue
connects, and nothing is exposed to the agent until an App claims a tool.

### 4.2 Connect flow (per venue) and the Xero callback branch

Unchanged: Settings → Connections (or the chat connect card) → Connect for a
venue → popup → Xero login + org picker → redirect to `/api/oauth/callback`
→ `exchange_code` stores tokens **on that venue's row**.

New, in `routers/oauth.py` next to the LoadedHub and Gmail branches
(better: a small `connectors/xero_hooks.py` the router calls, to stop the
router growing):

1. Take `authentication_event_id` from the **access token** (a JWT;
   base64-decode the payload — it came straight from Xero over TLS, so no
   signature check is needed for this purpose). Verified 17 Oct: the id_token
   does **not** carry the claim; the access token does, and it equals
   `authEventId` on `/connections`. `id_token` is never stored (dropped in
   `_store_tokens`).
2. `GET https://api.xero.com/connections?authEventId=<id>` with the new
   access token. Fallback when the claim is missing: the whole list sorted by
   `updatedDateUtc` desc, take the first.
3. Expect **exactly one** tenant. Zero → failure page ("no organisation was
   authorised"). More than one → failure page listing them ("authorise one
   organisation per venue").
4. Mirror `_reject_wrong_company_token` (:131): if this venue's `config`
   already has a `xero_tenant_id` and it differs from the one just
   authorised, wipe the just-stored tokens, flag `last_auth_error` naming both
   organisations, and return the failure page. A first connect adopts the
   tenant.
5. Write `config.xero_tenant_id`, `config.xero_connection_id` (for
   disconnect), and `oauth_metadata.tenant_name`; `flag_modified`; commit.
6. `connect-info` (`routers/connectors.py:88-91`) also reads
   `oauth_metadata.tenant_name` for "connected as" (one-line generic change),
   and `wrong_company` compares `config.xero_tenant_id` for Xero.

Disconnect: in `oauth_disconnect` (:526), a Xero branch calls
`DELETE /connections/{config.xero_connection_id}` before clearing tokens, and
tolerates 404.

Redirect URIs to register on the Xero app (exact match, no wildcards; Xero
allows plain http only for localhost):

```
https://bettercallnorm.com/api/oauth/callback
https://staging.bettercallnorm.com/api/oauth/callback
https://testing.bettercallnorm.com/api/oauth/callback
http://localhost:3000/api/oauth/callback
```

The cloud three are `_CLOUD_CALLBACK_URIS` in `routers/oauth.py:45-49`. The
local one is what `.env` pins in `OAUTH_REDIRECT_URI` (port 3000, not 8000:
the Next dev server proxies `/api/*` to the API). The devtunnel host is not
needed: the API always sends the pinned local URI.

### 4.3 Token refresh

No change to the locking/rotation logic. Two small generic additions in
`oauth_service.py`:

- `oauth_config.refresh_policy` — `"force"` (default, today's behaviour) or
  `"expiring"`: `refresh_all_tokens` calls `refresh_access_token(force=False)`
  for `"expiring"` rows, so a Xero row refreshes about every 30 minutes
  instead of every 15. The docstrings there explain why LoadedHub must stay on
  `force`; keep that.
- `oauth_config.token_endpoint_auth_method: "client_secret_basic"` — send the
  secret as an `Authorization: Basic` header instead of body fields. **Not
  needed:** Xero accepted body auth on the first exchange (17 Oct), and
  `scope` on refresh.

Verified 17 Oct: a forced refresh rotated the token; the keep-alive then
skipped the row as fresh ("xero (Glass Goose)" in `skipped`) with 22 minutes
left, exactly the `expiring` policy.

### 4.4 Endpoints and tools

Endpoints (raw, `engine_only: true`, never shown to the LLM; each with
`headers: {"xero-tenant-id": "{{ creds.xero_tenant_id }}", "Accept": "application/json"}`):

| action | method | path | notes |
|---|---|---|---|
| `list_accounts` | GET | `/Accounts?where=Status=="ACTIVE"` | chart of accounts |
| `list_tax_rates` | GET | `/TaxRates` | tax type per tenant |
| `list_contacts` | GET | `/Contacts?where={{ where }}&page={{ page }}` | search by name / `IsSupplier` |
| `create_contact` | PUT | `/Contacts` | body: `{"Contacts":[{Name, EmailAddress, TaxNumber, IsSupplier}]}` |
| `list_invoices` | GET | `/Invoices?where={{ where }}&page={{ page }}` | bills and invoices; `Type`, `Status`, `DateString`, `Reference`, `Contact.Name` |
| `get_invoice` | GET | `/Invoices/{{ invoice_id }}` | |
| `create_invoice` | PUT | `/Invoices?unitdp=4&summarizeErrors=false` | header `Idempotency-Key: {{ idempotency_key }}`; body `{"Invoices":[...]}` |
| `update_invoice` | POST | `/Invoices/{{ invoice_id }}` | status changes incl. `VOIDED` |
| `attach_to_invoice` | PUT | `/Invoices/{{ invoice_id }}/Attachments/{{ filename }}?IncludeOnline=true` | binary body; **check `spec_executor` can send a non-JSON body** — if not, this is the one place needing executor support (or a built-in helper) |
| `profit_and_loss` | GET | `/Reports/ProfitAndLoss?fromDate={{ from }}&toDate={{ to }}` | for dashboards |
| `get_organisation` | GET | `/Organisation` | country, currency, short code, timezone; called once by the connect hook |

Tools (consolidators in `apps/api/config/consolidators/`, LLM-facing):

| tool | effect | what it does |
|---|---|---|
| `xero.get_contacts` | read | search suppliers/customers by name; returns id, name, email, tax number |
| `xero.get_accounts` | read | active chart of accounts (code, name, type, tax type) |
| `xero.get_bills` | read | bills for a venue by status (Xero's `Statuses=` param; `page=` loop), date range or supplier, with amount due, due date, currency and a deep link. **Carries `display_component: "xero_bills"`** (§4.7), so the answer renders as the bills view at zero tokens. Gives "what's unpaid with Bidfood" answers and the reconciliation check for 2.2(1) |
| `xero.get_profit_and_loss` | read | P&L for a period; replaces the dead `get_monthly_spend` on the procurement template |
| `xero.create_bill` | **write**, `approval.default: "ask"`, `allow_auto: true` | resolves the supplier to a contact (search → create if missing), maps lines to account codes (§4.5) and tax types, builds the ACCPAY payload, calls `preview(card)` with supplier, date, due date, lines, totals and GST, then `create_invoice` + `attach_to_invoice`. Idempotency key = Norm invoice id. Returns Xero invoice id/number/status |
| `xero.create_invoice` (ACCREC) | write, ask | same shape for sales invoices; Phase 4 if 2.2(2) is chosen |

Every tool row needs `field_schema` (the model sends strings otherwise), and
`check_endpoints_and_tools` must pass (`uv run python -m app.services.config_validator`
or the existing `validate_config` call at the end of the sync script).

Approval labels go in `scripts/sync_approval_labels.py` (`WRITES["xero.create_bill"] = _ask("Create a bill in Xero")`).

Exposure: the **Xero App** (§4.6) claims every `xero.*` tool. There is no
`required_connections` to declare — `agent_catalog.required_connections_for_app`
derives it from the claimed tools' connectors, which is what puts Xero in
Settings → Connections. Reads can also become `mcp_capabilities` rows
(`{"kind": "connector", "target": "xero", "action": …, "scopes": ["mcp:finance:read"]}`);
writes only via a playbook.

### 4.5 Mappings Norm has to own

- **Supplier → Xero contact**, per tenant. Store on the Norm `Supplier` row
  (`models.py:149`) as `xero_contacts: {tenant_id: contact_id}` (alembic
  migration; check `origin/main` and `alembic heads` before naming the id —
  three sessions collided on 12 Oct). `create_bill` fills it on first use after
  a name search; the preview card shows "new contact will be created" when it
  must.
- **Line → account code.** Three per-venue codes (Food / Beverage / Other)
  keyed off the item category Norm already knows from the invoice review.
  They need their own home: the connect card renders a credential form only
  for non-OAuth connectors, so `credential_fields` can't hold them. Store them
  as a `xero_mapping` block in `Connection.config` and edit them from a small
  settings panel on the Finance → Bills page (a picker fed by `get_accounts`),
  built in Phase 3 alongside `create_bill`. Orbit's `xero_account_mappings`
  table (`line_type` → account) is the same idea for wage journals.
- **Tax type**: from `list_tax_rates` per tenant (NZ `INPUT2`, AU `INPUT`),
  cached in `Connection.config` on connect. Norm's per-line GST (shipped
  572179c) lets lines carry `TaxType` individually; a GST-exempt line uses the
  tenant's no-GST rate.
- **Bill status**: create as `DRAFT` by default; `AUTHORISED` only via an
  explicit option on the approval card (Orbit exposes the same choice).
- **Tracking categories.** Orbit's `xero_account_mappings` also carries
  `tracking_category_id` / `tracking_category_option_id` per venue, i.e. lines
  are tagged with a Xero tracking option (venue or department) as well as an
  account code. Carry that as two more optional keys in the same
  `xero_mapping` block (`tracking_category`, `tracking_option`) and put
  `Tracking: [{TrackingCategoryID, TrackingOptionID}]` on each line when set.
  It is what makes **two venues sharing one Xero organisation** reportable:
  each venue still connects on its own (own consent, own token chain, same
  tenant id) and the wrong-tenant check only compares against that venue's
  own stored tenant, so sharing an org is allowed.
- **Organisation facts on connect.** The callback hook also calls
  `GET /Organisation` and stores `xero_country`, `xero_currency`,
  `xero_short_code` (deep links) and `xero_timezone` in `Connection.config`.
  Every consolidator reads currency and tax types from there, never from a
  constant (§2.6).

### 4.6 Finance member and Xero App

What the Functions rollout wrote, translated to Finance. Everything below is
config or one-line code registrations; nothing new in the agent loop.

```python
# config DB: agent_configs — written by sync_xero_connector.py
AgentConfig(
    agent_slug="finance", display_name="Finance",
    description="Keeps the books in step with the venues: bills, suppliers, "
                "accounts and P&L in Xero.",
    persona="In here you're the finance assistant: exact about amounts, dates "
            "and GST; you read before you quote; a bill is created only from "
            "an approval card and lands in Xero as a draft.",
    enabled=True,            # system_prompt stays NULL — the unified prompt is on
)

# config DB: marketplace_apps — sync_marketplace_catalog.py
_member("finance-agent", owns="finance", name="Finance", icon="🧾",
        description="Watches the books when you ask: bills, suppliers, "
                    "chart of accounts and P&L from Xero.",
        tagline="Bills, suppliers, accounts and P&L from Xero.")   # + bundled=False (§2.5)

_app("xero", name="Xero", member="finance", icon="📒",
     description="Bills, contacts, chart of accounts and P&L from Xero; "
                 "creates supplier bills from reviewed invoices.",
     tools=["xero.get_bills", "xero.get_contacts", "xero.get_accounts",
            "xero.get_profit_and_loss", "xero.create_bill"],
     components=[_c("xero_bills",
                    page=_p("xero-bills", "Bills", "Receipt"),
                    full_width=True, connections=["xero"],
                    description="Bills in Xero for a venue: outstanding, "
                                "overdue, by supplier, with links into Xero.")],
     note="Amounts, due dates and accounts come from the xero tools — never "
          "quote one you haven't read. Creating a bill goes through an "
          "approval card and lands in Xero as DRAFT.")            # ≤ 300 chars
```

Code registrations (each a one-liner next to Functions' entry):

| What | Where |
|---|---|
| member slug, so unattended tasks don't get the read-only scope | `apps/api/app/agents/registry.py` `MEMBERS` |
| rail tab (icon + colour); the rail shows only members in this list | `apps/web/app/components/layout/Sidebar.tsx` `AGENTS` — `{ id: 'finance', label: 'Finance', icon: Landmark, color: colors.textSecondary }` (no new `lib/theme.ts` key: the MCP bundle hashes that file) |
| name in running text | `apps/web/app/lib/memberNames.ts` `MEMBER_NAMES` |
| Bills page | `apps/web/app/components/pages/pageRegistry.ts` `FUNCTIONAL_PAGES` (§4.7) |
| approval labels | `scripts/sync_approval_labels.py` — `READS` += the four reads, `WRITES["xero.create_bill"] = _ask("Create a bill in Xero")` |
| MCP reads | `mcp_capabilities` rows for the four reads, scope `mcp:finance:read` |

No `agent_connector_bindings` row: the App claim is the exposure (bindings
only still matter for unattended-task scope and the "unowned tools" warning).

**Run order** (as `docs/norm-functions.md:73-87`): deploy the code →
`sync_xero_connector.py` (spec + `agent_configs`) → `sync_marketplace_catalog.py --dry-run`
and read the whole diff, then run → `sync_approval_labels.py --dry-run`. The
catalog seed refuses with `unknown agent 'finance'` until the `agent_configs`
row exists, and it applies **every** pending catalog diff in one commit.

**Two budgets to respect:** the App `note` is capped at 300 characters and all
App notes together at 1,500 (`check_app_notes`), so Finance's working rules
have to be short and live mostly in tool descriptions. And `NormAgent.domain`
is always `norm` in a new chat, so the member `persona` only reaches threads
already filed under Finance; check whether the Bills page's
`pageContext.agent` changes that, and if not treat it as an existing gap, not
one this plan fixes.

### 4.7 The bills view — `xero_bills`

One React component, two surfaces, like `invoices_dashboard`:

1. **In chat**: `xero.get_bills` carries `display_component: "xero_bills"`,
   `display_props: {"title": "Bills in Xero"}`. The tool loop's
   `_build_display_block` emits `{"component": "xero_bills", "data": <payload>,
   "props": {..., "activeVenueId"}}`, saved on `Message.display_blocks`. The
   component renders from the stored payload; the model only reads the
   slimmed summary, so the table costs no tokens.
2. **As the Finance → Bills page**: a `FUNCTIONAL_PAGES` entry
   `{ id: 'xero-bills', app: 'xero', connections: ['xero'], label: 'Bills',
   icon: Receipt, agent: 'finance', component: 'xero_bills',
   loadAction: { connector: 'xero', action: 'get_bills', defaultParams: () => ({ status: 'unpaid' }) } }`.
   `FunctionalPage` loads it through `POST /api/working-documents/from-connector`
   with `persistVenue`, and the page shows only while the Xero App is on.

Payload the `get_bills` consolidator returns (also what the LLM's slimmed
view summarises):

```json
{"venue": "Bessie & Engineers", "tenant_name": "Cook Brothers Bars Ltd",
 "currency": "NZD", "as_of": "2026-10-17",
 "filters": {"status": "unpaid", "from": null, "to": null, "supplier": null},
 "summary": {"count": 42, "total": 18234.50, "amount_due": 9120.00,
             "overdue": 2210.00, "due_7_days": 3400.00},
 "by_supplier": [{"supplier": "Bidfood", "count": 6, "amount_due": 2210.00, "overdue": 0}],
 "bills": [{"invoice_id": "…", "number": "INV-1023", "reference": "…",
            "supplier": "Bidfood", "date": "2026-10-01", "due_date": "2026-10-20",
            "status": "AUTHORISED", "total": 1234.50, "amount_due": 1234.50,
            "currency": "NZD", "overdue": false, "has_attachment": true,
            "url": "https://go.xero.com/organisationlogin/default.aspx?shortcode=…&redirecturl=/AccountsPayable/Edit.aspx?InvoiceID=…"}]}
```

Xero returns 100 invoices a page: `where=Type=="ACCPAY"`,
`Statuses=AUTHORISED,SUBMITTED` for unpaid, `order=DueDate`, `page=` loop
inside `max_api_calls`; `If-Modified-Since` for refreshes later.

Component (`apps/web/app/components/display/XeroBills.tsx`): three summary
tiles (outstanding, overdue, due in 7 days), filter chips (status, supplier,
period), a sortable table (supplier, number, date, due, status, total, due
amount, attachment, "Open in Xero"), venue following `props.activeVenueId`.
Styled per the Oct-2026 UI decisions (Figtree, line icons, neutral accent);
plain tiles, no charts, in the first cut.

Registration checklist — the tests enforce each line:

| Step | Where | Enforced by |
|---|---|---|
| add to `REGISTRY` and `FULL_WIDTH_COMPONENTS` | `apps/web/app/components/display/DisplayBlockRenderer.tsx:58-99` | |
| add to `REGISTERED_COMPONENTS` | `apps/api/app/services/agent_catalog.py:190-228` | `tests/test_app_map.py:290-298` pins it against the web file |
| claimed by exactly one App's `components` (the Xero App above) | `sync_marketplace_catalog.py` | `tests/test_app_map.py:300-317`; `check_display_components` fails a tool that names an unclaimed component |
| metadata for Settings → Components | `apps/web/app/components/settings/ComponentsPanel.tsx:21` `COMPONENT_META` | |
| page entry | `pageRegistry.ts` `FUNCTIONAL_PAGES` | add a catalog↔page parity test like `tests/test_functions_catalog.py` |

Not in the first build, listed under Later: the **MCP bundle** (the view is
web-only; `display-block.html` is untouched so `test_mcp_ui.py` stays green)
and a **dashboard tile** (`DashboardView.tsx:30-37` is a fixed list).

---

## 5. Phases

Each phase ends with the verification listed, then **stop and report** (no
commit unless asked).

### Phase 0 — Xero app + spec (½ day; needs the user in the Xero portal)

User: developer.xero.com → New app → *Web app*, name "Norm", company URL
`https://bettercallnorm.com`, the four redirect URIs from §4.2; copy client id
+ generate a client secret. Paste both into the spec via Settings → Connector
specs (masked in API responses) or hand them to the script once.

Agent: `sync_xero_connector.py` creating the spec with scopes, `pkce: false`,
empty tools, endpoints from §4.4 marked `engine_only`. Dry-run, then run.
Verify: spec appears in `/api/connector-specs`; `validate_config` clean.

**Scope check first:** before any hook code exists, open the authorize URL
for a local test venue with the full §2.3 string and complete consent on the
Demo Company. `invalid_scope` → bisect and fix the string in the spec; a
successful consent (tokens land on the venue row) → the list is final.

### Phase 1 — Connect a venue end-to-end against the Demo Company (1 day)

Build the Xero callback branch (§4.2), `refresh_policy` (§4.3), connect-info
`tenant_name`, disconnect branch. Tests in `tests/test_oauth*.py` with faked
Xero responses (token response with id_token, `/connections` list, the
wrong-tenant case, the two-tenants case, 404 on disconnect).

Verify live, locally: connect a local test venue → Demo Company (NZ). Then:

- token exchange succeeded with body auth (or flip to Basic);
- `Connection.config.xero_tenant_id` set, `oauth_metadata.tenant_name` =
  "Demo Company (NZ)", connect card says "connected as Demo Company (NZ)";
- `POST /internal/refresh-tokens` rotates the token (log line "issued a
  rotated refresh token") and a second run within 10 min **skips** it;
- reconnect choosing a different org is refused with the names in the message;
- disconnect returns the slot (`GET /connections` no longer lists it).

**Done 17 Oct (code in the `norm-xero` worktree, uncommitted):**
`app/connectors/xero_hooks.py` (event id from the access token,
`/connections?authEventId=`, `/Organisation`, wrong-organisation refusal,
revoke on disconnect), the Xero branch in `oauth_callback` and
`oauth_disconnect`, `id_token` kept off the page and out of
`oauth_metadata`, `refresh_policy: expiring` in `refresh_all_tokens`
(redeem within 20 min of expiry), connect-info reads `tenant_name` /
`xero_tenant_id`. `tests/test_oauth_xero.py`: 12 tests, green with the
existing OAuth suites (59). Verified live on the consent the user gave: body
auth accepted by Xero's token endpoint, all 22 scopes granted, refresh
rotates the token and Xero accepts `scope` on refresh, the hook bound the
organisation and the connect card shows it. **Not the Demo Company:** the
consent was for a live organisation (Cook Brothers Bars Federal Street
Limited) — see the note in §7. Disconnect is tested, not yet run live.

### Phase 2 — Reads, Finance member, Xero App, bills view (2 days)

1. Endpoints + the four read consolidators + shapes + `field_schema`. Run
   each read against the Demo Company through
   `POST /api/connector-specs/xero/test` **before trusting any shape**
   (CLAUDE.md: field names from docs are routinely wrong).
2. Finance member: `agent_configs` row, `MEMBERS`, rail tab, member name
   (§4.6). `bundled=` on `_member`, Finance seeded `bundled=False` (§2.5).
3. Xero App row claiming the tools and `xero_bills`; approval labels; MCP
   read capabilities.
4. `XeroBills.tsx` + every line of the §4.7 checklist; the Bills page entry;
   `get_bills` gets its `display_component`.
5. Catalog: `sync_marketplace_catalog.py --dry-run`, read the whole diff,
   run; hire Finance for the local org from the Team page.
6. Re-point the procurement template's two `xero.get_monthly_spend` scripts
   at `get_profit_and_loss` and fix `tests/test_chart_rows.py:173`.

Verify: Finance appears in the rail only once hired; Finance → Bills renders
the Demo Company's bills with totals that match Xero's Awaiting Payment
screen; in chat, "what bills are unpaid for <venue>?" answers with the bills
view and the model's text agrees with the tiles; "show the chart of
accounts" matches the Demo Company UI; `validate_config` and
`tests/test_app_map.py` clean; API + web suites green.

### Phase 3 — `create_bill` (1–2 days)

Consolidator with `allowed_write_actions: [create_contact, create_invoice,
attach_to_invoice]`, `preview(card)`, idempotency key, DRAFT default. Check
the executor can send the binary attachment body; add support if not.
Verify: dry-run renders the exact request; then a **real** create on the
Demo Company from local chat via the approval card; the bill appears in the
Demo Company with lines, GST and the PDF attached; a second approval of the
same invoice does not create a duplicate (idempotency key); voiding via
`update_invoice` works. Run the full suite (API + web) — the mcp-ui bundle is
not touched, but `test_mcp_ui.py` runs anyway.

### Phase 4 — First business use (depends on §2.2)

- (1) bills from the invoice pipeline: a step after review that calls
  `xero.create_bill` for accepted invoices, respecting Autopilot (Norm decides;
  no holds), with the Loaded-sync overlap resolved per venue first; the
  `InvoiceAutopilotOutcome` row records the Xero invoice id.
- (2) ACCREC for functions: `create_invoice` + customer contact handling in
  the Functions App.
- Production rollout: connect each entity's Xero org from Settings (the
  person with Xero admin rights for each org does the consent); watch the
  keep-alive summary the next morning; one real bill per venue reviewed by a
  human before enabling always-allow.

### Later

- Port wage journals / intercompany from Orbit (manual journals; needs the
  scope decided in §2.3).
- `xero_bills` in the MCP bundle for Claude: import in `apps/mcp-ui/src/registry.ts`,
  add its imports to `SOURCES` in `apps/mcp-ui/scripts/emit.mjs` and
  `tests/test_mcp_ui.py`, map `("xero", "get_bills")` in
  `app/mcp/ui_apps.py`, rebuild with `pnpm --filter @norm/mcp-ui build`.
- `xero_bills` as a dashboard tile (`DashboardView.tsx:30-37`).
- Token encryption at rest (§2.4).
- Xero app certification before the Xero feature is sold to another group
  (25-connection cap).

---

## 6. Files this touches

| Change | File |
|---|---|
| New sync script (spec, endpoints, tools, the `norm.get_connection_settings` row, the Finance member) | `apps/api/scripts/sync_xero_connector.py` |
| Consolidators (as built) | `apps/api/config/consolidators/get_bills.py`, `get_contacts.py`, `get_accounts.py`, `get_profit_and_loss.py`, `create_bill.py` — named for their actions, as the budget guard (`tests/test_consolidator_budgets.py`) and the coverage drift check require — plus `xero_common.py`, prepended to each by the installer (the connected-venue guard, envelope/date/money helpers, deep links, period resolution); `services/consolidator_coverage.py` now accepts a `*_common.py` prefix when comparing a row to its file |
| Strict per-venue settings built-in | `apps/api/app/agents/internal_tools.py` (`norm.get_connection_settings`) |
| Bills after receive | `apps/api/app/services/xero_bills.py`; call sites in `services/received_invoice.py` (`receive_from_draft`) and `services/invoice_review.py` (autopilot) |
| UI execute route runs GET consolidators | `apps/api/app/routers/connectors.py` (`execute_connector_action`) |
| Catalog seed `--only <slugs>` | `apps/api/scripts/sync_marketplace_catalog.py` |
| Settings panel | `apps/web/app/components/display/XeroSettingsPanel.tsx` |
| Tests (as built) | `tests/test_oauth_xero.py` (12), `tests/test_xero_consolidators.py` (25), `tests/test_xero_bills.py` (8), `tests/test_connector_execute_route.py` (+2) |
| Callback / disconnect hooks | `apps/api/app/routers/oauth.py` (+ new `apps/api/app/connectors/xero_hooks.py`) |
| `refresh_policy`, optional Basic auth | `apps/api/app/services/oauth_service.py` |
| connect-info `tenant_name` / `wrong_company` for Xero | `apps/api/app/routers/connectors.py:82-105` |
| Approval label | `apps/api/scripts/sync_approval_labels.py` |
| Supplier → contact mapping column | `apps/api/app/db/models.py` + alembic migration |
| Dashboard template | `apps/api/app/db/dashboard_templates.py:32,84`, `tests/test_chart_rows.py:173` |
| Tests | `apps/api/tests/test_oauth_xero.py` (new), consolidator exec tests alongside the existing ones |
| Finance member registration | `apps/api/app/agents/registry.py` (`MEMBERS`), `apps/web/app/components/layout/Sidebar.tsx` (`AGENTS`), `apps/web/app/lib/memberNames.ts` |
| Catalog rows: member, Xero App, component claim; `bundled=` on `_member` | `apps/api/scripts/sync_marketplace_catalog.py` |
| Bills component | `apps/web/app/components/display/XeroBills.tsx` (new), `DisplayBlockRenderer.tsx` (`REGISTRY`, `FULL_WIDTH_COMPONENTS`), `apps/web/app/components/settings/ComponentsPanel.tsx` (`COMPONENT_META`), `apps/api/app/services/agent_catalog.py` (`REGISTERED_COMPONENTS`) |
| Bills page | `apps/web/app/components/pages/pageRegistry.ts` (`FUNCTIONAL_PAGES`) |
| MCP read capabilities | `mcp_capabilities` rows, written by the sync script |
| Tests | `tests/test_app_map.py` parity, a catalog↔page parity test for the Xero App, `tests/test_xero_catalog.py` (new) |
| Docs | this plan; `docs/architecture.md` §3 gets a Xero paragraph once shipped |

Web changes are the rail tab, the Bills page entry and the `xero_bills`
component. The connect card, Settings → Connections and the approval card
need nothing.

---

## 8. Production run order (when the code is committed and deployed)

The config side is already live in the shared config DB (the spec, the five
tools, `norm.get_connection_settings`, the Finance `agent_configs` row, the
`finance-agent` and `xero` catalog rows). The validator will flag
`xero_bills` as unknown until the web code is deployed; harmless.

1. Commit and push the `norm-xero` worktree (the user's call). CI → testing
   → staging → production.
2. Production: set `TOKEN_ENCRYPTION_KEYS` first (§2.4) if the first Xero
   venue is a live organisation.
3. Team page: hire **Finance** for Cook Brothers (`finance-agent` is seeded
   `bundled=False`, $10/agent like the others).
4. For each entity: Settings → Connections → Xero → Connect for the venue;
   the person with Xero admin on that organisation gives the consent.
5. Finance → Bills → Xero settings: default / food / beverage / other
   accounts, bills land as Draft, tick "Create a bill in Xero when Norm
   receives an invoice" only once a hand-checked bill has landed right.
6. Watch the keep-alive summary the next morning: Xero rows should appear in
   `skipped` most runs and in `refreshed` about twice an hour.

If the catalog seed is run in full later (`sync_marketplace_catalog.py`
without `--only`), it still refuses until Norm Functions' config is synced —
that is the Functions workstream's step, not this one's.

## 9. Deliberately not done

- **A real bill has not been created.** The one write was previewed live
  (reads real, writes captured). The first real `create_bill` should go to the
  Demo Company (NZ), which means reconnecting the local test venue to it —
  the scope-check consent bound a live organisation (§7).
- **Attachments** (`PUT /Invoices/{id}/Attachments/…`): needs a binary body
  through the executor; the bill carries `InvoiceNumber` and the Loaded
  invoice id as its idempotency key, so the PDF can follow later.
- **MCP bundle** for Claude and a **dashboard tile** for `xero_bills` (§4.7).
- **`procurement-dashboard` template** still names `xero.get_monthly_spend`;
  `chart_tools` already re-points it, so nothing breaks. Swap it for
  `get_profit_and_loss` (`months` rows) when that dashboard is next touched.
- **Sales invoices (ACCREC)** for Functions: the same consolidator shape with
  `Type: ACCREC`; not started.
- **Orbit's wage journal / intercompany pushes**: `accounting.manualjournals`
  is in the scope list, so no reconsent is needed when they are ported.

## 7. Risks and gotchas

- **Scope list is a one-shot decision.** Adding a scope later means every
  venue reconnects. Decided wide (§2.3); the exact string still has to pass
  one Demo Company consent, because a single unentitled scope fails the
  whole connect with `invalid_scope`.
- **Shared token chains kill connections.** Never copy one venue's
  refresh token to another row; never let two code paths refresh the same row
  outside the existing lock.
- **Loaded's own Xero export** could duplicate every bill Norm creates. Answer
  that before Phase 4(1).
- **The config DB is shared.** The spec goes live everywhere on sync; keep
  tools unclaimed until Phase 2 is verified.
- **Production tokens are real company books.** Writes in production only
  after the user's go-ahead, DRAFT first, and never from a test venue.
- **Field names will be wrong on first contact.** Run every read for real
  before trusting a shape.
- **25 connections** on an uncertified app; certification is a product
  prerequisite, not a dev task.
- **Local re-auth steals nothing here** (unlike LoadedHub): Xero chains are
  per consent, so connecting the Demo Company locally cannot affect
  production. Still, never connect a *live* org from local.
- **A bundled member bills every organisation $10** and switches on
  everywhere the moment the catalog is seeded. Seed Finance `bundled=False`
  (§2.5) unless that is wanted.
- **The App note shares a 1,500-character budget** with every other App note
  (`check_app_notes`); keep Xero's under 300 and don't crowd the others.
- **`sync_marketplace_catalog.py` applies every pending diff** in one
  commit. Dry-run first and make sure nothing else is queued (16 Oct: a
  held-back pricing change went live this way).
- **Member persona may not reach new chats** (`NormAgent.domain` is always
  `norm`). Finance's working rules belong in the App note and tool
  descriptions.
- **This checkout is behind origin/main.** Functions (the template) landed
  16 Oct; build in a clean worktree from `origin/main`, not here.
- **A live organisation is bound to a local venue (17 Oct).** The scope
  check consent was given for Cook Brothers Bars Federal Street Limited, not
  the Demo Company, so the local Glass Goose row holds a 60-day refresh
  token for real books, in plain text (no `TOKEN_ENCRYPTION_KEYS` locally).
  Nothing has written to it and no write tool exists yet. Recommended: run
  the new disconnect (which revokes at Xero and is the live test of that
  path), then reconnect choosing **Demo Company (NZ)** — it has to have been
  opened once in My Xero to appear on the consent screen.
