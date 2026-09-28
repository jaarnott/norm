# One Norm prompt: retiring the per-agent system prompts

*Drafted 28 Sep 2026. Follows Apps v3 (docs/product-hierarchy-v2.md). Not started.*

## Why

The runtime has one agent holding every tool its org's Apps allow, but each
conversation still loads **its routed domain's** system prompt. So a
conversation filed under Procurement has recipe tools and no recipe guidance.
The eight prompts are mostly one shared "Rules" block that has drifted between
copies (only one line is still identical across all of them), plus a few lines
of domain guidance each. Some of that guidance describes tools that no longer
exist.

## Target: what every conversation's prompt is made of

| Part | Source | Loaded when |
|---|---|---|
| **Norm prompt** | • one shared prompt (identity, rules, calendar, formatting)<br>• owned by Norm Core, edited in one place | • every conversation |
| **Member line** | • one or two sentences giving the tab's team member a light personality and its focus (owner, 28 Sep) | • every conversation, for its member only |
| **App notes** | • ≤ 2 lines per App: only standing rules that apply to any use of that App<br>• new `composition.note`, validator caps it at 300 characters | • only for Apps that are **on** |
| **Skill menu** | • unchanged: one line per skill, full text loaded on demand | • only skills whose App is on (already built) |
| **Tool descriptions** | • how to use each tool, carried by the tool itself | • only while the tool's App is on (already true) |
| **Code-built sections** | • venues, page context, memory, workflow modes, tasks, date: unchanged | • as today |

## Where every line goes

### → Norm prompt (shared rules, harmonised once)
| Today | Becomes |
|---|---|
| • "Only present data returned by tool calls…" (all) | • kept verbatim |
| • "Dates: pass periods in plain English… never calculate dates" (all, drifted examples) | • kept, one wording |
| • "GET proceed; writes describe, user approves" (all) | • kept |
| • "When a tool returns an editor card, hand it over — the click is the approval" (Chef only) | • **promoted to everyone**: it applies to every card (PO, invoice, roster, recipe) |
| • "Match entity names fuzzily" with hardcoded examples ("jb" = "Jim Beam") | • kept as a rule, examples dropped (venue list is already injected) |
| • "Prefer action over clarification…" (all) | • kept |
| • "Week runs 7am Mon → 7am Mon" (Reports); "business day starts at the venue's day_start_time" (T&A) | • merged into one calendar rule for everyone |
| • "Show times in the venue's timezone, 12-hour" (HR, T&A) | • formatting rule for everyone |
| • "Markdown tables, concise, bold labels for confirmations" (5 of 7) | • formatting rule for everyone |

### → Tool descriptions (appended where not already present)
| Guidance | Tool(s) |
|---|---|
| • ask ONCE for the whole period per venue, not month by month | • get_sales<br>• get_labour<br>• get_received_items_for_period<br>• get_budgets |
| • …except split multi-month periods into monthly calls | • get_cogs_detail_for_period |
| • group-wide / venue comparisons: `venues='all'` in one call | • get_sales<br>• get_labour<br>• get_received_items_for_period |
| • "top N items" = breakdown 'items', ranked by revenue unless quantity is asked | • get_sales |
| • attendance view is the default; `group_by='day'` for daily summaries; `view='staff'` for names, roles, rates | • get_labour |
| • flag no-shows, unrostered clock-ins, variance over 20% | • get_labour (attendance view) |
| • query first, then item_id; never scan the full list; views on_hand / reference / minimums; never guess ids | • get_stock |
| • get each item's default variant first (detail 'summary') | • create_purchase_order |
| • create = full item; update = changed fields only (server merges); unit change needs its paired ratio; one defaultForSupplier per supplier; set_variant_unit for one variant | • manage_stock_item |
| • get_menus finds/reads; manage_menu create = full menu, update = deltas only; lines reference a recipe or stock item | • get_menus<br>• manage_menu |
| • find the role first, then its candidates | • BambooHR tools (then the planned get_candidates) |
| • shifts are edited in the roster editor, never written directly | • show_roster (then get_labour's roster view) |

### → App notes (short, only while the App is on)
| App | Note |
|---|---|
| **Loaded Kitchen** | • Recipes, menus and stock items live in Loaded. A recipe create needs a name, yield unit and at least one line; an update needs the recipe id and version_id from get_recipes. A draft recipe can be extracted from an uploaded PDF, image or Word document.<br>• (Here rather than on kitchen_record_recipe: that CB tool's description is overwritten on every MCP rediscovery.) |
| **Loaded Stock** | • Stock-on-hand, received-stock and stocktake questions need item ids from get_stock — never guess them. |
| **Loaded Time** | • Answer roster questions grouped by day, sorted by start time: staff, role, start–end, hours. |
| **App Builder** | • To build or change an App, open the `build_an_app` skill first. |
| **Brevo / Metricool** | • none until they have tools again |

### → Skills
| Today | Becomes |
|---|---|
| • App Builder's 45-line authoring guide (what an App is, writing ui_source/logic_source, skeleton, boundaries) | • a `build_an_app` skill owned by the App Builder App, loaded only when someone is building |

### → Delete (obsolete)
| Line(s) | Why |
|---|---|
| • Marketing's "Orbit Marketing" section, guidance, status emojis and calendar/post tables | • those tools exist on no connector; they return with the Orbit port as a proper App |
| • T&A "for shift management (create, update, delete), always confirm…" | • there are no shift write tools; the roster editor does it |
| • "Use date formats exactly as shown in each tool's field description" | • tools take plain-English periods; formats live in field descriptions |
| • "You are the X agent for Norm…" (each) | • replaced by one identity plus the member line |
| • "Start tool calls with a '[Tool]' prefix" (4 of 7) | • removed everywhere (owner, 28 Sep) |

### → Router prompt (stays: it's the classifier)
| Change | Why |
|---|---|
| • generate its "domain capabilities" list from each member's tagline plus its Apps | • the static list is stale: **no Executive Chef at all**, and recipes still routed to Procurement |

## Build steps

1. **Prompt assembly** (`prompt_builder.build_tool_definitions`): replace
   `get_system_prompt(domain)` with Norm prompt + member line + notes for Apps
   that are on + the existing skill menu. Keep the old path behind a config
   switch (`prompt_mode: per_agent | unified`, default per_agent).
2. **Storage:** the Norm prompt goes in the `base` agent_configs row (empty
   today). Member lines go in each member's `description`. App notes go in
   `composition.note` in the catalog seed. The validator caps notes at 300
   characters and total notes per conversation at about 1,500.
3. **Tool descriptions:** a sync script appends the guidance above to the
   spec rows, and to `config/consolidators/*.py` where the file is canonical
   (the coverage drift check keeps them aligned).
4. **Skills:** create `build_an_app` from App Builder's current prompt; add it
   to the App Builder App.
5. **Router:** build its capability list from the App Map at request time.
6. **Admin:** the Agents tab edits the Norm prompt (on the Norm row) and each
   member's one-line focus. Per-agent prompt editors go.

## Rollout and proof

1. Ship the code with `prompt_mode: per_agent` (no behaviour change).
2. **Replay eval before switching:** take about 30 recent real prod
   conversations (about 5 per member, including cross-domain ones such as a
   recipe question filed under Procurement). Run the first user turns through
   both modes with the same tools and compare tool choices and answers side
   by side (a model-graded comparison plus your review). Compare prompt size
   too; it should shrink.
3. Flip to `unified` in the shared config DB. Keep the per-agent prompts one
   release as the rollback.
4. Delete the per-agent prompts and the switch.

## Verification

- **Unit:**
  - App notes appear only for Apps that are on;
  - the member line follows the thread's member;
  - note and total size caps are enforced;
  - the skill menu is still filtered;
  - the router list is generated from the App Map.
- **Replay eval** results reviewed before the flip.
- **Full gates** plus validate-config at 0; commit only when asked.

## Member lines (light personality — drafts for review)

| Member | Line |
|---|---|
| **Norm** (Home) | • You're Norm — calm, quick and practical, the one who knows where everything is. |
| **Procurement** | • In here you're the sharp-eyed buyer: you watch every dollar, keep suppliers honest and never let the walk-in run dry. |
| **Executive Chef** | • In here you're the exec chef: precise about recipes and costs, proud of the menu, allergic to waste. |
| **Time & Attendance** | • In here you're the roster wrangler: fair, organised, and quick to spot a no-show or a blown-out shift. |
| **HR** | • In here you're the people person: warm with candidates, thorough with records, discreet always. |
| **Marketing** | • In here you're the storyteller: upbeat, on-brand, and always thinking about what gets people through the door. |
| **Reports** | • In here you're the numbers person: clear, plain-spoken, and you always say what a figure actually means. |
| **App Builder** | • In here you're the builder: curious about the job, and you turn it into something the team can use tomorrow. |

Personality sets tone only. It never changes rules, tools or approvals,
which all come from the shared Norm prompt and the Apps. The lines live in
each member's agent_configs row and are editable on the Agents tab.
