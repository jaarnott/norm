# Norm Functions

Function bookings for a hospitality group — enquiries, minimum spends and approvals,
events and the client portal, venue mailboxes, proposals and the quote page, web forms,
reports — rebuilt from Orbit's Functions app as a **native Norm module** (Oct 2026).
Design and owner decisions: `~/.claude/plans/norm-functions-port.md` (v2).

To users it is an App like Loaded Stock: catalog App `functions` ("Norm Functions"),
owned by the team member `functions`, pages gated by `app: 'functions'`, tools claimed
through the App Map. "Native" only means the code lives in Norm's main codebase rather
than the app-platform sandbox.

## Layout

| Layer | Where |
|---|---|
| Models | `apps/api/app/db/functions_models.py` (43 `fn_*` tables), migration `fa0b1c2d3e4f_norm_functions.py` |
| Core services | `apps/api/app/services/functions/` — `ctx`, `company`, `entitlement`, `catalog`, `money`, `timeutil`, `status`, `contacts`, `enquiries`, `min_spends`, `events`, `menus` |
| Staff features | `reports`, `templates`, `venue_profile`, `proposals`, `privacy`, `uploads` |
| Mailbox | `services/functions/mailbox/` (connect, sync, mirror, outbox, drafts, display, pricing, mime…) |
| Public | `sanitize`, `public_tokens`, `quote_page`, `portal`, `forms`, `notify`, `retention` |
| Import | `services/functions/importer.py`, `scripts/migrate_orbit_functions.py` |
| HTTP | `routers/functions.py` (staff, `/api/functions/*`), `functions_inbox.py`, `functions_forms.py`, `public.py` (`/api/public/*`, no login) |
| Agent | `app/agents/functions_tools.py` (14 tools, registered from `internal_tools.py`) |
| Web (staff) | `apps/web/app/components/functions/` — `Functions{Dashboard,Enquiries,Events,Inbox,Contacts,Reports,Settings}.tsx` + subfolders; shared helpers in `functions/shared/` |
| Web (customers) | `apps/web/app/q/[token]`, `e/[token]`, `e/[token]/print`, `f/[slug]`; `apps/web/public/embed/{forms,track}.js` |
| Config scripts | `scripts/sync_norm_functions.py`, `scripts/sync_marketplace_catalog.py` (functions entries), `scripts/sync_approval_labels.py`, `scripts/sync_venue_mailbox.py` |

## Rules the code keeps

- **Tenancy in the schema.** Every `fn_*` table has `organization_id NOT NULL`,
  `UNIQUE (organization_id, id)` and composite foreign keys to its parents (venues
  included). A metadata test enforces it. Services take a `FunctionsCtx` built from
  membership; customer contexts (`for_client`, `for_quote`) reach only their own record.
- **Facts, not stored status.** Enquiry stage, event status, min-spend state, next action
  and totals are derived (`status.py` SQL selects with Python twins, tested to agree).
- **A manager's approval only means "OK to offer"** and covers the exact terms approved
  (`terms_key`); any change revokes it. **The customer confirming an option creates the
  event** (quote page, or staff recording an email/phone yes), exactly once.
- **Lowering a booked minimum needs an approver** — the people who could have
  approved the option (approver, override approver, anyone where the area needs no
  approval). The note goes on the event, the sign-off in the approvals log. Raising
  it needs only the note.
- **Money** is integer cents in the company currency (`organizations.currency`).
  **Plans** (event date, times, windows) are venue-local; **facts** are UTC instants.
- **`functions_mode` per org:** `off` (not shown) · `shadow` (Orbit is live; Norm's copy is
  read-only, imported nightly; Gmail read-only; public pages off except forms in
  Orbit-compatible mode) · `live` (everything on; the importer refuses).
  Until the catalog rows exist, the App counts as enabled when mode ≠ off.
- **Proposals are free HTML**, sanitised server-side (nh3 + tinycss2, size and nesting
  caps) and shown in a script-less sandboxed iframe; Confirm/Decline are rendered by Norm
  from the option records, below the HTML.
- **Fonts are Norm's own.** Ten open-licence families (`HOSTED_FONTS` in `sanitize.py`;
  files, stylesheet and OFL licences in `apps/web/public/fonts/brand/`) load from Norm's
  origin in every proposal view and on the hosted form page; a proposal just names the
  family. Remote `@font-face`/`@import` are still removed, so no customer's browser calls
  Google.
- **Drafts** live in Norm and can be handed to Gmail (one owner at a time).
- **Anyone in the org** with access to the venue can create, edit and send; min-spend
  approvals stay with each venue's approver / override approver.

## Background jobs (Cloud Scheduler → `/internal/*`, `X-Scheduler-Secret`)

| Job | Schedule | Endpoint |
|---|---|---|
| `norm-sync-mailboxes-<env>` | every minute | `/internal/sync-mailboxes` — per-mailbox lease, history cursor, 503 when a mailbox lags > 10 min |
| `norm-functions-retention-<env>` | daily 03:40 UTC | `/internal/functions-retention` — OTP state > 24 h, expired portal sessions, old form IP/user-agent |
| `norm-mcp-gc-<env>` | daily 03:30 UTC | `/internal/mcp-gc` — was never scheduled before; the public rate limits share its table |

Defined in `infra/terraform/scheduler.tf`; CI does not apply Terraform — apply by hand.
Kill switch: `gcloud scheduler jobs pause norm-sync-mailboxes-<env>`.

## Ship-day runbook

1. **Rebase and migrate.** Rebase onto `origin/main`; set `fa0b1c2d3e4f.down_revision` to
   main's current head; `alembic heads` must show one head; verify upgrade/downgrade on a
   throwaway database. Full gates (API + web + `test_mcp_ui.py`).
2. **Deploy** (push to main → production). The code is inert until an org's mode changes.
3. **Config scripts** (config DB is shared with production — `--dry-run` each first):
   1. `uv run python scripts/sync_norm_functions.py` — member `agent_configs`, the
      `norm_functions` spec with its tool rows, skills (off), superseded markers on Orbit's
      `functions_*` endpoints.
   2. `uv run python scripts/sync_marketplace_catalog.py` — creates `functions-agent` and
      `functions` (refuses before step 1).
   3. `uv run python scripts/sync_norm_functions.py` again — switches the skills on.
   4. `uv run python scripts/sync_approval_labels.py --dry-run` → "up to date".
   5. MCP exposure rows, if shipped (see `sync_norm_functions.py`).
   `validate_config` should report ok after step 3.
4. **Secrets and infra (owner):**
   - `TOKEN_ENCRYPTION_KEYS` per environment in Secret Manager (Fernet keys, newest
     first), then `scripts/encrypt_connection_tokens.py --dry-run` / `--apply`
     (`--decrypt` is the rollback before deploying older code). Without a key, tokens stay
     plaintext — nothing breaks.
   - Google OAuth client (External; CBHG Workspace admin marks it Trusted), Gmail API
     enabled, redirect URIs `<env>/api/oauth/callback` and
     `<env>/api/functions/inbox/mailboxes/oauth/callback`; then
     `scripts/sync_venue_mailbox.py` (scopes `gmail.modify` + userinfo; set scopes BEFORE any
     venue connects — refresh sends the spec's scopes).
   - `terraform apply` the three scheduler jobs.
   - Point-in-time recovery on `norm-prod-db` (restart window) and web
     `min_instances = 1` (customers must not hit a cold start).
   - Recommended: restrict the API's Cloud Run ingress to the load balancer (the run.app URL
     lets a caller forge `X-Forwarded-For`); retarget the scheduler jobs first.
5. **Import (go-ahead needed for production writes):** set the org to `shadow`;
   `uv run python scripts/migrate_orbit_functions.py --org <slug> --dry-run` against
   production; review the report (people mapping by email, unmapped venues — e.g. Orbit's
   CBI has no Norm venue — booking-type mapping, the "Orbit marked confirmed without a
   booking" lists); then the real run. Nightly re-import while in shadow.
6. **Mailboxes:** a manager connects each venue mailbox in Functions → Settings →
   Mailboxes (shadow: read-only sync). Watch the health tile for two weeks; check
   replied/quoted parity against Orbit.

## Cut-over (one evening, whole group)

1. Final import → `functions_mode = live` (Company settings).
2. Orbit Functions read-only and its outbound sweep off; Orbit's quote page and portal
   say "this link has moved — the venue will send you a new one".
3. Send Norm portal links / proposals to customers with active Orbit links (list them first).
4. Point the claude.ai skills at Norm (or use the Functions member in Norm).
5. Re-embed the web forms on the venue websites with Norm's snippet (until then Orbit's
   forms keep emailing the venue inboxes, so no lead is lost).
6. Smoke test: form → inbox, threaded reply, quote confirm → event, portal sign-in code.
7. Orbit read-only for 30 days. Orbit's forecasting/wages/kitchen screens stop counting
   new functions from here (accepted).

## Testing

`apps/api/tests/test_functions_*.py` (+ `test_company_settings.py`,
`test_token_encryption.py`); fixtures in `tests/functions_world.py`,
`functions_mailbox_fakes.py`, `functions_importer_fixtures.py`. Customer-facing routes
(portal, quote, forms, sanitiser, public files) are covered by blocking CI tests — there is
no production deploy gate. Web logic tests sit next to the components (`*.test.ts`).
