# Orbit → Norm: Hiring & Training port

*Living record of the port of the Cook Brothers App's (Orbit) Hiring & Training
domains onto the Norm app platform. Companion to `lite-apps-architecture.md`,
which sets the strategy; this doc tracks what is actually built, what is
migrated, the roadmap, and the findings worth not rediscovering.*

*Last updated: 18 Aug 2026.*

---

## 1. Where it stands

**Shipped to production (code):** the app-platform primitives below are on
`main` and deployed. Two apps are **installed in production** (`Training`,
`Hiring`, both under the HR agent, private). **No production data yet** — the
one-off migration write is staged and dry-run-clean but was blocked by a
harness guardrail and is waiting on an explicit go-ahead.

**Working locally with full data:** both apps run against the local dev DB with
all 8,388 migrated records and 868 evidence files. This is the current way to
*see* it.

**P1–P4 admin build — complete (uncommitted, on `autopilot-readiness`).** The
admin-facing half of every phase is built and verified: P1 Training admin, P2
Hiring authoring, P3 manager sign-off queue (with evidence-file viewing), P4
capability frameworks. Gates green — API `ruff` (own files) + `pytest`
(2050 passed, incl. new `TestSignoffQueue`/`TestCapabilityFrameworks`), web
`tsc`/`lint` (0 errors) + 87 unit tests, and every screen browser-verified
end-to-end. **The learner-facing/public/performance-review remainder of P3/P4
is deliberately NOT built** — it needs your sign-off (see the roadmap below).
One shared-host change rode along: `AppRunner`'s `file-url` now returns a
`data:` URL, not a `blob:` URL, because the `sandbox="allow-scripts"` iframe
runs on an opaque origin and cannot load a parent-origin blob — `training.html`
is the only caller.

### P0 primitives — status

| Primitive | Status | Notes |
|---|---|---|
| Storage door (`app_records`) | ✅ done, prod | namespace-keyed, four-check door, audited |
| JSONB + query surface | ✅ done, prod | typed compares, nested paths, operators, ordering, count, GIN index |
| File primitive (`app_files`) | ✅ done, prod | bytes guarded like the record; verified 200/401/404 |
| Correctness set (10 defects) | ✅ done, prod | see §5 |
| **Scheduled runs + email** | ⏸ **deferred** | fully planned; see §6. This is the last P0 item |

### The two apps — what works today

- **Training**: program list, tracker (member × program, 193×16 on real data),
  program authoring (create/edit, modules→sections→content, cascade deletes),
  admin mark-complete/reopen.
- **Hiring**: openings board, pipeline (drag-free, move via dropdown), candidate
  detail (answers/notes/activity), talent pool, hire→people hand-off.

**Both are usable for viewing and admin, but NOT yet a replacement for Orbit.**
The usability gaps that block real daily use are in §4 (the roadmap) — chiefly:
Training has **no enrolment UI** and **no content viewer**; Hiring **cannot
create a job or candidate**. These are P1/P2, not built yet.

---

## 2. What was migrated, and how it reconciles

Read-only against Orbit's Supabase over PostgREST; idempotent (keyed by
`data->>'orbit_id'`); scripts in `apps/api/scripts/migrate_orbit_*.py`.

| Collection | Rows | Source |
|---|---|---|
| people | 208 | active + anyone with training history (of 4,093) |
| programs / modules / sections / content | 18 / 81 / 178 / 340 | full |
| assignments | 580 | full |
| completions | 6,784 | full |
| plans / plan_sections | 110 / 66 | full |
| capability_frameworks | 2 | nested categories + capabilities |
| **evidence files** | 868 (495 MB) | copied out of the **public** `training-media` bucket |
| hiring: jobs/stages/fields/candidates/... | small | Orbit hiring is barely started |

**Reconciliation:** Norm's tracker matches Orbit's own `get_training_tracker_grid`
RPC **359/359 cells identical**. The 199 extra cells Norm shows are 197 inactive
people (Orbit hides them) + 2 enrolments on inactive programs (Orbit filters
them). Nothing missing.

**Why not migrate through Orbit's API:** all 18 programs are group-wide
(`venue_id IS NULL`) and Orbit's own API filters programs by `venue_id IN (...)`,
so that path returns **zero** programs and **zero** of the 580 assignments — a
migration through it would look like a clean run over an empty dataset.

**Venue mapping is by rule, not hardcoded** (`resolve_venues` in
`migrate_orbit_training.py`): exact name → alias → normalised prefix
("The Glass Goose" == "Glass Goose"), refuses to guess when ambiguous, prints
every decision. Norm venue names differ **between environments** (local
"Bessie & Royals" vs prod "Bessie & Engineers"), which a hardcoded map got
wrong. Orbit's `CBI` has no Norm venue → those rows land group-wide (reported).

---

## 3. Findings that must not be re-lost

- **Orbit is live and moving.** People train in it daily (completions
  6,784→7,958 over one day). It stays the system of record until parity lands;
  migrations are re-runnable for exactly this reason.
- **Orbit's tracker hides data by design** — active members only (106 of 193),
  active programs only (14 of 18). "Missing" on its screen is usually a filter.
- **Reminders would harm on day one.** Orbit's engine, switched on today, would
  email 16 active staff about training overdue since **March** — 44 of the 54
  overdue items belong to plans already `completed` (Orbit never wrote
  plan-section status back). Fix the data first, ship reminders off. Decided:
  close the 44 stale sections; set due dates in the enrol path, no back-fill.
- **Email in Norm is less safe than it looks — for agents, today.** See §6.
- **`scripts/dev.sh` points `PROD_INSTANCE` at the retired, STOPPED
  `norm-production`.** The live instance is **`norm-prod-db`**. Following the
  script to reach prod gives a confusing 409. Worth fixing — the cost-cuts doc
  records that a missed reference after the last DB move silently broke prod
  migrations for 3 days.
- **Orbit defects fixed in the port, not carried:** unnamed section rendered a
  blank badge; a program with structure but no content read as `complete`;
  moving a candidate back to an active stage left the rejection date; hiring
  picked the *last* matching stage; plan-editor save was destroy-and-reinsert.

---

## 4. Roadmap (P1–P4) — the parity build

Gap analysis (17 Aug) measured Norm's 5 admin screens vs Orbit's 24 admin + 7
learner + 2 public. The remaining work, in order. **This is where "still can't
use it" gets fixed.**

### P1 — Training admin parity (the usability gap) ✅ DONE
- **Enrolment UI** ✅ — enrol / bulk-enrol picker (variant groups + venue),
  unenrol. Enrolment sets `due_date = today + program.default_due_days`.
- **Content viewer** ✅ — rich_text / video / quiz / file_upload render in a
  modal; member detail shows per-module progress.
- **Plans list + editor** ✅ as a real upsert (reschedule keeps completion —
  Orbit's delete+reinsert reset it), **reordering, tracker venue+program
  filters + member drill-through** ✅. Print deferred (see below).

### P2 — Hiring parity ✅ DONE
- **Authoring** ✅: new job (default 7 stages + 6 standard fields), job editor
  (publish/close, add/edit/delete/reorder stages, add/delete fields), pipeline
  with add-candidate, candidate detail (rating stars, reject-with-reason, hire,
  schedule interview, talent-pool toggle). Templated emails + careers settings
  deferred (see below).

### P3 — Learner experience + public — admin half ✅ DONE
- **Manager sign-off queue** ✅ (this session): lists submitted-but-unsigned
  completions (the JSONB `is_null` query), approve / reject-with-reason, and
  **evidence-file viewing** — the migrated Orbit bytes served through Norm's
  permission-checked file endpoint, handed to the sandbox as a `data:` URL.
- **Still gated on a user decision — NOT built autonomously:** trainee accounts
  (121 active staff; only 732 of 4,093 have an email — needs a provisioning
  plan), the trainee module player (3 quiz types + evidence upload from the
  learner side), my-reviews, and `/careers` (needs an anonymous surface Norm
  lacks). These change who can log in and expose a public page — they need
  your sign-off before I build them.

### P4 — Performance + AI surface — admin half ✅ DONE
- **Capability frameworks** ✅ (this session): list (category counts), full
  detail view (categories → capabilities → L1/L2/L3 descriptors, migrated whole
  from Orbit), create + edit name/role/active. Category-level editing is stubbed
  with a note pointing at the trainee release.
- **Still gated on a user decision — NOT built autonomously:** performance
  reviews, cycles + scheduler with goal carry-forward (depends on P0 scheduled
  runs), and re-publishing the domain as Norm MCP tools so Claude regains the
  reach Orbit's 21 tools gave it.

### Deferred features (tracked, not lost)
Auto-enrolment (filter semantics: empty include group matches everyone;
`loadedhub_role`/`team_member` have no branch and silently match nobody),
reminder emails, Google Calendar interviews, candidate emails with per-stage
automation, PDF/print reports, careers-page bot defences (honeypot + <2500 ms
reject + knockout eval), performance-review PDFs.

---

## 5. The 10 correctness defects (fixed, in prod)

Found by attacking the platform, not trusting it:

1. **exec() sandbox was not a boundary** — `json.__builtins__` reached real
   builtins, `().__class__.__base__.__subclasses__()` reached 644 classes → any
   app author could read every venue's credentials, unaudited. Source is now
   parsed and refused; consolidators unaffected.
2. **`call_api_parallel` skipped the door** — a 4th parameter was enough to call
   any action on any connector with no allowlist, no audit.
3. **Venue scoping was opt-in** — omitting a venue returned the whole org;
   by-id reads were never venue-checked.
4. **Audit write was what committed the operation** — its rollback-on-failure
   discarded the very write it was recording.
5. **`store.list` silently truncated at 1,000** — computed a tracker from 1,000
   of 6,784 completions, reporting trained people as untrained.
6. **`where` never matched non-strings** (`{"is_active": True}` vs `true`).
7. **JSON not JSONB** despite the docstring — no index, no nested paths.
8. **`max_api_calls: 0` meant 20** (`or 20` on a falsy 0).
9. **`store_get` unaudited**; lost-update race in the one browser-side write.
10. **`hire` picked the last hired stage**; UI dropped role/start date; icons
    rendered raw lucide names; two "Hiring" menu entries.

---

## 6. DEFERRED PLAN — scheduled app runs + email (the last P0 item)

*This is the full plan for the deferred work, kept here so it is not lost. It
was researched and decided but not yet implemented.*

### Decisions taken

- **Rail:** automated tasks, plus the idempotency guard it lacks.
- **Cron:** committed to Terraform, applied later — deliberately not live.
- **Stale sections:** close the 44 on completed plans; 22 stay open.
- **Due dates:** set in the enrol path when P1 builds it; no back-fill.
- **Email approval:** fix `_is_read_only` to honour the `read_only` flag —
  interactive sends now prompt, `test_mode` now simulates instead of sending —
  and treat a *scheduled* task's send as pre-authorised by the task owner, so
  existing report emails keep working. Apps always take the strict door.
- **Environment gating: leave the existing agent path as-is** (user's call).
  BUT the new app-email door does not inherit that risk: it resolves
  recipients from `people` and never accepts a free-text address, so app code
  cannot mail an arbitrary customer regardless of environment. A dev box with
  the shared key can still mail via the *agent* tools exactly as it can today —
  unchanged, documented, not widened by this work.

### 1. Scheduled app runs

`execute_task_now` (`services/task_scheduler.py:336`) is the single waist where
"a task is due" becomes "an agent prompt runs". Branch **before**
`get_agent(task.agent_slug)` at :372 so an app task runs `run_logic` instead,
reusing everything upstream: the per-minute Cloud Scheduler job, the
`FOR UPDATE SKIP LOCKED` claim, `AutomatedTaskRun` history, and the tasks board.

- **Principal:** `AutomatedTask.created_by` — already how every run is
  identified. It is nullable, so a null is refused, and the run is refused and
  reported if that user has since lost the permission. No synthetic superuser.
- **Idempotency, which the rail lacks:** `next_run_at` advances at *claim*
  time, so a slot can be re-entered and a long run can overlap itself. Add a
  `scheduled_for` slot stamp on the run row with a uniqueness guard, so one
  slot runs once.
- **Venue:** `AutomatedTask.venue_id` exists and is **read and written by
  nothing**. An app schedule is the first venue-scoped scheduled work — set it,
  and carry it to the execution thread so `resolve_dates` stops silently using
  the group default.
- Approval-pending runs currently record as `success`; an app run must not
  inherit that — it reports what it did.

### 2. Email as a governed primitive

Server-side only, reachable from `run()`, never from the iframe. But the rails
below do not exist yet, so most of this section is building them:

- **One choke point.** Every send goes through one `_deliver()` — including
  `routers/email.py`'s retry, which today calls Resend inline and would bypass
  anything added to the service.
- **Recipients are resolved, never free text.** An app may mail only people it
  can already see — resolved from `people` — with a max-recipients cap. This
  is what makes the app door safe without touching the shared environment
  question: app code has no way to name an arbitrary address at all.
- **Rate limit + per-recipient throttle + idempotency key**, counted off
  `EmailLog`, so a re-run cannot re-mail anyone.
- **Attribution:** populate `organization_id`, `venue_id`, `sender_user_id` and
  a new `app_id` on `EmailLog` (all currently always NULL), and index it.
- **A real permission** for sending, declared in the spec's `writes` so the
  existing double opt-in applies.

### 3. The email-approval fix (decided)

`tool_loop._is_read_only` will consult `tool_def["read_only"]` — the way
delegation and MCP already do — instead of the HTTP method. Effect:

- interactive sends move from the auto-executed read batch to the
  approval-pending branch (`tool_loop.py:775`), so a human sees them;
- `test_mode` simulates them, because they are now in `write_blocks`;
- a **scheduled** task's send is pre-authorised: the task owner set it up, so
  `execute_task_now` passes a flag that lets a declared send proceed without an
  interactive approval, and existing report emails keep working;
- app sends always take the strict door (declared in `writes` + approved).

This is a change to shared agent-loop behaviour, so it needs its own tests and
a note that it narrows what auto-executes — a good narrowing, but a behaviour
change reviewers should see named.

### 4. Data cleanup, before any reminder can fire

One idempotent script, dry-run first, reporting every row: for each
`plan_sections` row whose plan is `completed`, set `status="completed"` and mark
`closed_with_plan: true` — visible and reversible. **No invented
`completed_at`**: we do not know when it happened. Scope is exactly 44 rows
across 19 plans, leaving 22 open, all on active plans.

### 5. What ships switched off

The reminder op lands with sending disabled and a dry-run that names who would
be mailed and why. It is switched on only once enrolment sets real due dates
(P1) and the dry-run reads sensibly. Building it now is what proves both
primitives.

### Files

- `apps/api/app/services/task_scheduler.py` — branch at the waist; slot stamp
- `apps/api/app/services/app_runtime.py` — `run_scheduled`, principal rule,
  the email door, spec validation for `schedules` + the email scope
- `apps/api/app/services/email_service.py` + `routers/email.py` — one choke
  point, gating, limits, attribution
- `apps/api/app/db/models.py` + one Alembic revision (after `d6e7f8a9b0c1`) —
  slot stamp, `EmailLog.app_id`, indexes
- `apps/api/app/auth/permissions.py` — a real send permission
- `infra/terraform/scheduler.tf` — the job, committed, not applied
- `apps/api/scripts/close_stale_plan_sections.py`
- `apps/api/app/fixtures/apps/training.{py,json}` — `send_reminders`, disabled
- Tests: `tests/test_app_schedules.py` (new), extend `test_app_platform.py`

### Verification

- Gates: `ruff check app/`, `ruff format --check app/`, `pytest tests/ -q`; web
  `pnpm lint && pnpm exec tsc --noEmit && pnpm test`; clean worktree before push.
- **Attack the principal:** a schedule whose owner lost the permission refuses
  and says so; it cannot reach a venue that user cannot.
- **Attack the email door:** app code cannot mail an invented address, exceed
  its cap, or re-mail inside the throttle; non-production cannot mail out.
- **Attack the slot guard:** the same slot claimed twice runs once.
- Cleanup: dry-run shows exactly 44 rows / 19 plans, leaves 22 open, second run
  reports zero changes.
- Reminder dry-run on real data: ~10 overdue items on active plans, mails nobody.

### Notes

- Nothing committed until asked. Production has both apps installed and **no
  data** — the migration write was blocked by the harness guardrail and is
  waiting on your go-ahead.
- `scripts/dev.sh` still points `PROD_INSTANCE` at the retired, stopped
  `norm-production`; the live instance is `norm-prod-db`.
- Unrelated but found: `/internal/mcp-gc` has no Cloud Scheduler job, so it
  never runs in production.


---

## 7. Key files

| Concern | Where |
|---|---|
| Storage + file door | `apps/api/app/services/app_runtime.py` |
| App HTTP surface | `apps/api/app/routers/apps.py` |
| Models | `App/AppVersion/AppShare/AppCall/AppRecord/AppFile` in `apps/api/app/db/models.py` |
| The two apps | `apps/api/app/fixtures/apps/{training,hiring}.{json,py,html}` |
| Migrations (data) | `apps/api/scripts/migrate_orbit_{training,hiring,files}.py` |
| Install an app | `apps/api/scripts/install_fixture_app.py` |
| Sandbox | `apps/api/app/connectors/function_executor.py` |
| Strategy | `docs/lite-apps-architecture.md` |
| Scheduler rail | `apps/api/app/services/task_scheduler.py`, `infra/terraform/scheduler.tf` |
| Email | `apps/api/app/services/email_service.py`, `routers/email.py` |

---

## 8. Gap analysis (19 Aug 2026) — built vs spec vs Orbit

Full audit of the two built apps against Orbit's live Supabase schema (row
counts below are live) and the `lite-apps-architecture.md` intent. Grouped by
severity. **The migration is one-shot and has not yet run against prod, so the
Tier-A data gaps are cheap to fix now and effectively irreversible after
cutover.** The migration's own contract — *"a silent skip reads as 'there was
nothing there'"* — is currently violated by every NOT-MIGRATED row below.

### Tier A — live Orbit data with NO home in the port (would be lost at cutover)

1. **Performance reviews & review cycles (Training) — entire subsystem absent.**
   `performance_reviews` (37, mostly `employee_in_progress`),
   `performance_review_capability_ratings` (1), `review_cycles` (3),
   `review_cycle_assignments` (2). No collection, no op, no UI; the migration
   never reads them. This is a **manager-facing appraisal** subsystem, *not*
   trainee self-service — so "trainees stay on Orbit" does not cover it. It was
   filed under P4 but reads as a genuine scope gap. **Needs an explicit
   keep/drop decision.** Ratings/free-text live in JSON blobs
   (`employee_reflection`, `manager_feedback`, `next_goals`), plus
   `summary_email_html` + `pdf_storage_path`.
2. **Hiring — seven feature-areas the migration never reads (silent drop):**
   `scorecard_templates` (1) / `scorecard_criteria` (5) / `scorecard_ratings`,
   `job_opening_approvals` (10), `job_hiring_team` (3),
   `hiring_email_templates` (7 active), `candidate_documents`,
   `job_description_templates` (0), `careers_page_settings` (1). No Norm
   collection exists for any of them, which is the root cause — there is
   nowhere to migrate into.
3. **Training automation config:** `training_auto_enroll_rules` (2),
   `training_reminder_settings` (1, `{enabled:true, interval_days:3}`),
   `training_reminder_emails` (10, historical log). Not migrated. Note the *real*
   auto-enrol mechanism is `programs.auto_enroll_filter` (an include/exclude
   rule engine on `loadedhub_role`/`team_member`) — that column **is** carried
   across, but nothing reads it.
4. **Column-level losses inside migrated tables (behaviourally relevant):**
   - `training_content.effective_from` (set on all 340 rows) — drives Orbit's
     effective-dating/grandfathering; dropped. Historical grandfathered
     completions were materialised as 1,181 rows and *are* carried, so the
     snapshot is intact, but the rule can't be reproduced forward (see B8).
   - `training_content_completions.completed_by` — who completed each of 7,984
     items, dropped (only `completed_at` kept).
   - Hiring `job_openings`: `benefits_html`, `about_html`, `hours_per_week`,
     `hiring_manager_id`, `owner_id`, `pay_public`, `auto_email_on_stage`,
     `published_at`, `closed_at` dropped. `job_pipeline_stages`:
     `scorecard_template_id`, `email_template_id` (per-stage automation) dropped.
     `candidate_applications.availability` dropped.
   - **Candidate-only notes/activity silently dropped**: `migrate_orbit_hiring`
     does `if not application: continue`, so any `candidate_notes`/
     `candidate_activity` row without an `application_id` (and `notes.mentions`)
     vanishes with no `skipped[]` entry.

### Tier B — real bugs in what IS built (verified in source)

1. **Plan-led programs can never be completed from Norm.** `plan` op line ~730:
   `"plan_section_id": ps.get("orbit_id") and None` — always evaluates to
   `None`, so the UI never receives a plan-section id; `set_plan_section_status`
   is also unwired and there is no op to set a whole plan's status. Since the
   tracker trusts `plan.status == "completed"`, migrated `active` plans show
   perpetually in-progress with no way to finish them.
2. **`hire` never increments `job_openings.positions_filled`** (only ever
   initialised to 0). The pipeline header (`hiring.html`) shows `0/N filled`
   forever.
3. **Tracker venue filter can't match.** The `tracker` op builds cells with
   `status`/`label`/`instance`/`scheduled` but **no `venue_id`**, while
   `showTracker` filters on `cell.venue_id === state.trkVenue`. Filtering falls
   back to `person.venues` only; a venue-scoped assignment for a person with no
   `venues` array won't appear under that venue.
4. **Section & content bodies cannot be authored/edited in the app.**
   `update_section` and `update_content` ops exist but **no UI calls them**, and
   `add_content` stores `body = params.get("body")` while the UI never sends a
   body. Result: content created in Norm is an empty shell (quiz with no
   questions, video with no URL); sections render as static text. This is the
   biggest *authoring* hole — the admin can reorder and delete but not write
   lesson content.
5. **A capability framework created in-app can never gain a category.**
   `save_framework` accepts only `name`/`role_label`/`is_active`/
   `baseline_prerequisites`; `categories` is never accepted and there is no
   category/capability/descriptor editor (the UI literally says "Full category
   editing comes with the trainee release"). "+ New framework" yields a
   permanently empty framework. Orbit's real depth is
   `capability_frameworks` → `capability_categories` (10) → `capabilities` (31,
   each with L1/L2/L3 descriptors) — view-only in Norm.
6. **Knockout screening is carried-but-dead (Hiring).** The migration copies
   `knockout_enabled`/`knockout_values` (live: `right_to_work` knocks out on
   "No"), but nothing evaluates them — `add_candidate` never sets
   `applications.knockout_flag`, `add_field` can't set knockout, and the
   pipeline's `knockout` pill never lights for anyone entered via Norm.
7. **No grandfathering on `add_content`.** Orbit materialises a
   `{grandfathered:true}` completion for already-finished enrollees when content
   is added to a program. `add_content` does not, so adding content post-cutover
   silently regresses completed people to incomplete in the tracker.
8. **New-enrolment `variant_id` format differs from migrated.** The enrol UI
   builds composite `"<groupId>:<optionId>|..."` strings; migrated assignments
   carry Orbit's own `variant_id`. De-dupe is on exact match, so a Norm-created
   enrolment for the "same" logical variant won't match a migrated one →
   duplicate-instance risk. Low blast radius (only 1 of 18 programs uses
   variants today), but real.
9. **Interviews are date-only and thin.** `schedule_interview` persists
   type/scheduled_at/location/status; the UI collects the time from a bare
   `<input type="date">` (no time-of-day), and there is no way to set
   interviewers, `meeting_url`, `instructions`, invite-candidate, or a scorecard.

### Tier C — partial builds (feature present, incomplete)

- **Application-field editing:** add/delete only — no edit, reorder,
  enable/disable, `options` editor for selects, `help_text`, or knockout config.
- **Job editor UI** sends only title/department/employment_type/positions;
  `description_html`/`requirements_html`/`pay_*` are accepted by `update_job` but
  not exposed, and `benefits_html`/`about_html` are supported nowhere.
- **Section CRUD:** add/delete only; rename/intro/instructions/times not
  editable in UI (op exists — see B4).
- **Plans:** create/schedule/delete, but no mark-complete/cancel (see B1).
- **Cross-application candidate view:** modelled correctly (`candidates` 1→many
  `applications`) but every entry point is application-scoped, so a candidate
  who applied twice has two unlinked cards.

### Tier D — the AI/tool surface is entirely absent (strategy requirement)

Both specs declare `actions: []`, `writes: []` — so the apps expose **no Norm
MCP tools**. Orbit exposes **21** (`Cook_Brothers_Hospitality`): 15 reads
covering the whole manager surface (programs/modules/content, assignments +
completions + sign-offs, plans, frameworks, and the full hiring funnel) and 6
writes (`create_training_assignment`, `update_assignment_status`,
`mark_content_completed`, `sign_off_module`, `add_candidate_note`,
`move_candidate_stage`). `lite-apps-architecture.md` §3.3 requires each migrated
domain to **re-publish its tool surface** so Claude keeps that reach and Orbit's
connector can be retired. Until this exists, **Orbit cannot actually go away**,
even after data migrates. (The empty `actions`/`writes` is *correct* for the
storage-door model — those govern connector calls, which these apps don't make —
so this is a missing mechanism, not a spec defect.)

### Tier E — intentional deferrals (confirmed), with a data caveat

Known and acknowledged: trainee/learner self-service, public `/careers` +
anonymous apply + bot defences, candidate emails + per-stage automation (waits
on the P0 email primitive), Google Calendar interviews, full framework
category/capability editing. **Caveat:** deferring a *feature* is not a reason to
drop its *data* — the careers config row, the 7 email templates, and the
framework categories/capabilities are live and would still be lost by today's
migration. Keep the data; defer the UI.

For scoping the eventual **trainee module player**, Orbit content is:
`file_upload` 146 / `rich_text` 136 / `video` 27 (YouTube id + duration) /
`quiz` 31. Quizzes carry **three** question types — `multiple_choice` (often
multi-answer), `match_pairs`, `ordering` — with `pass_percentage` 80 or 100.
Completion `result` blobs already hold quiz score/answers, video watched-%, and
file-upload evidence URLs + the `awaiting_signoff`/`signoff_*` fields the
sign-off queue reads.

### Tier F — spec/build hygiene

- **`signoffs` collection is declared but unused** (`training.json`). Sign-off is
  modelled on `completions` flags instead; `training_module_signoffs` (0 rows)
  is unmigrated. Wire it or drop the declaration.
- `scopes: ["mcp:hr:read"]` on apps that write storage heavily — by design (the
  storage door governs writes, not scopes), noted for confirmation.

### What is demonstrably right (so the audit is balanced)

`candidates`↔`applications` 1→many preserved; `_apply_stage` fixes an Orbit bug
(clears `hired_at`/`rejected_at` on revive) and picks the first hired/rejected
stage; effective-completion rule (awaiting-signoff / rejected don't count)
matches Orbit; due-date derivation (`today + default_due_days`) faithful;
migrated grandfathered completions preserved and counted; sections'
time/intro/instructions, program `variants`/`default_due_days`, assignment
variant fields, and completion `result` blobs all carried; evidence bytes
migrated and permission-checked.

### Suggested order of attack

1. **Before any prod migration (Tier A):** decide performance-reviews keep/drop;
   add collections + migration reads for the Hiring seven and the Training
   automation config (or consciously drop, recorded in `skipped[]`); carry the
   dropped columns; fix the candidate-only-notes silent skip. Cheap now.
2. **Make the admin build trustworthy (Tier B):** the plan-completion bug (B1),
   `positions_filled` (B2), tracker venue filter (B3), and **content/section
   body authoring (B4)** — B4 is what stops the app being a real authoring tool.
3. **Then** framework category editing (B5), knockout eval (B6), grandfathering
   (B7), and the Tier-C polish.
4. **Tool surface (Tier D)** whenever Orbit retirement gets scheduled — it's the
   gate on actually shutting Orbit off.

---

## 9. Tier A & Tier B — resolved (19 Aug 2026)

All of §8 Tier A and Tier B done, uncommitted on `autopilot-readiness`, verified
against the live Orbit schema on the local DB and unit-tested. Gates green: API
`ruff` + `pytest` (2106 passed / 4 skipped; new `TestPlanCompletion`,
`TestTrackerVenueFilter`, `TestGrandfathering`, `TestFrameworkCategoryEditing`,
`TestPositionsFilled`, `TestKnockout`, `TestInterviewDepth`), web `tsc`/`lint`
(0 errors) + 87 unit tests. Framework editor and knockout editing also
browser-verified end-to-end against migrated data.

### Tier A — data now has a home (migration + specs)
- **Performance reviews + review cycles carried** (`performance_reviews` 37,
  `review_cycles` 3, ratings/goals/assignments nested). New collections
  `review_cycles`, `performance_reviews`. Ratings resolve to framework
  capabilities because nested categories/capabilities now keep their Orbit id.
- **Hiring seven added** — collections + reads for `scorecard_templates`
  (criteria nested), `job_approvals`, `hiring_team`, `email_templates`,
  `careers_settings`, `candidate_documents`, `hiring_emails`, `jd_templates`.
  Empty transactional scorecard tables are now recorded in `skipped[]`, not
  dropped silently.
- **Training automation carried** — `auto_enroll_rules`, `reminder_settings`,
  `reminder_emails`.
- **Dropped columns recovered** — `content.effective_from`,
  `completions.completed_by`, `assignments.assigned_by`, `plans.created_by`;
  hiring `job_openings` (benefits/about/hours/manager/owner/pay_public/
  auto_email/published_at/closed_at), `pipeline_stages` scorecard/email links,
  `applications.availability`, note/activity `mentions`/`detail`.
- **Candidate-only notes/activity no longer silently skipped** — linked by
  candidate when there is no application; a real drop now lands in `skipped[]`.
- The dead `signoffs` collection was removed from the Training spec (sign-off is
  modelled on completion flags).

### Tier B — bugs fixed, with UI
- **Plan completion (B1):** `plan_section_id` returns the real id; new
  `set_plan_status` op; the tracker keeps completed plans (shows them done
  rather than dropping them); plan screen gains per-section Done/Undo and
  plan-level Complete/Reopen/Cancel.
- **positions_filled (B2):** derived from hired applications via `_recount_fill`
  on hire/move/reject — can't drift, can't double-count.
- **Tracker venue filter (B3):** cells now carry `venue_id`.
- **Content & section authoring (B4):** the content viewer gains an editor
  (rich_text HTML, video id+duration, quiz JSON, file_upload); sections gain
  inline-name editing and a details editor (intro/instructions/times).
- **Framework category editing (B5):** `save_framework` accepts the whole
  `categories` tree and stamps ids on new nodes; the framework screen is a full
  category/capability/descriptor editor.
- **Knockout (B6):** `add_field`/`update_field` author knockout + options;
  `add_candidate` evaluates answers and sets `knockout_flag`; the field list
  shows a knockout pill and an edit button.
- **Grandfathering (B7):** `add_content` writes a `{grandfathered:true}`
  completion for already-complete enrollees.
- **Interviews (B9):** `schedule_interview` takes a datetime, duration, meeting
  URL, briefing and interviewers; the modal collects them.

**Deliberately not done here (still deferred, per §8 Tier D/E):** the AI/tool
surface (both specs still declare no actions/writes — Orbit's 21 tools have no
Norm equivalent yet, so Orbit can't be retired), the trainee module player,
public `/careers`, candidate emails, and Google Calendar. B8 (variant_id format
drift) is left as-is — it affects only the one variant-using program and needs a
migrated↔new id-mapping decision.

**Nothing committed.** The one-shot prod migration still hasn't run (prod has no
data); the enriched scripts are dry-run-clean locally and ready when you are.

---

## 10. Variants + editor usability pass (19 Aug 2026)

Uncommitted on `autopilot-readiness`, all browser-verified. Gates green (API
2106 passed / 4 skipped; web tsc/lint 0 errors + 87 tests).

### Variants — now usable (was B8)
- Orbit itself has **0 assignments/plans using a variant** (the feature is
  defined on one program — Kitchen Chef: Larder/Fryer/Grill — but never applied),
  so there was no migrated variant data to conflict with. B8's format-drift risk
  was moot; the real gap was that **nothing let you define variant groups**.
- Added a **variant-group editor** to the program editor: add/remove manual
  groups and their options, plus a "by venue" group; ids are stamped
  client-side; saved through `save_program` (which already accepted `variants`).
  Browser round-trip verified against the migrated Kitchen program (loaded
  Larder/Fryer/Grill, added + removed an option, restored to original). The
  enrol picker and tracker already tracked per variant, so enrolment-by-variant
  now works end to end.

### Content editors — rebuilt (no more raw JSON)
- **Quiz editor is now structured** — the JSON textarea is gone. It builds all
  three Orbit question types: multiple-choice with per-option correct-answer
  checkboxes (multi-answer supported), match-pairs (left ↔ right), and ordering
  (with reorder arrows); add/remove questions and options; pass-%. Verified
  against the migrated "Quiz - Health & Sickness" (3 questions, correct answers
  pre-ticked) and by switching a question through all three types.
- **rich_text** gains a live HTML preview; **video** takes a full YouTube URL
  (auto-extracts the id) with an embedded preview; **file_upload** keeps clean
  instructions/max-files/sign-off fields. The content **viewer** now renders
  match-pairs and ordering too (previously only multiple-choice).
- Sections already gained inline-name + a details editor in §9.

### Hiring — easier to use
- **Job editor** now edits pay (type + min/max), **description** and
  **requirements** — as plain text, converted to/from the stored HTML
  (`htmlToText`/`textToHtml`), so a manager writes prose, not markup. You could
  not write a job posting before. Verified: the migrated Freeman & Grey
  description round-trips as readable text.
- **Pipeline is drag-and-drop** — drag a candidate card between stage columns to
  move them (with a drop-zone highlight); click still opens the candidate.
  Cards show rating, source, knockout, and applied-ago. Verified by dragging a
  test candidate across stages and confirming the move persisted.
- **New-job modal** takes a department and explains it seeds a standard pipeline
  + form.

### Note on the Orbit UI comparison
I logged into the live Orbit app far enough to confirm it is a **PIN-gated**
"Cook Brothers Hospitality" app, but did **not** browse its authenticated
screens: the owner's own account is not head-office (which gates content
authoring), and I would not use other people's accounts or risk locking one by
guessing PINs on a production system overnight. The editors above are built from
the **exact content-type data shapes** captured in the live-data audit (§8), and
enhanced beyond a like-for-like port (live previews, structured quiz builder,
drag-and-drop) rather than copied.
