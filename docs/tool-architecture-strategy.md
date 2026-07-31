# Tool Architecture Strategy

*Status: direction agreed July 2026, revised after measurement on 2026-07-20. Nothing below
has been implemented — Steps 1–5 of the original plan were never executed. This document is
the map, not a work order.*

## The question

Norm's tools are hard to reason about. Two incidents traced back to it: two tools answering
"rostered hours next week" 2.3× apart, and nine GET-method tools that mutate state.

The original framing was *"should we reduce tools per agent, or overall by collapsing many
into fewer consolidators?"* Measuring the live config answered that, and replaced it with a
better question.

**The problem is not how many tools there are. It is that nothing records what a tool is,
whether it should still be used, or how to invoke it — and that Norm's real actions are not
tools at all.**

## Where we are (measured 2026-07-20, live config DB)

### Six different mechanisms define a callable thing

| # | Mechanism | Count | Reachable by a model? |
|---|---|---|---|
| 1 | Raw spec tool (`path_template` + `method`) | 123 | yes |
| 2 | Consolidator (`consolidator_config.function_code`) | 20 | yes |
| 3 | Internal handler (`@register`, `app/agents/internal_tools.py`) | 31 | yes |
| 4 | `response_transform` — post-processing over 1–3 | 26 | n/a (a layer) |
| 5 | `component_api_configs` — UI-component actions | 17 (5 non-GET) | **no** |
| 6 | Bespoke component routers (`app/routers/invoice_fixes.py`) | 4 endpoints | **no** |

On top of that sit **two hand-maintained exposure lists** (`agent_connector_bindings`,
`mcp_capabilities`) and a **hand-maintained `read_only` flag**.

143 spec tools exist in total (123 raw + 20 consolidators).

### The two surfaces

| | Tools |
|---|---|
| Agent-facing union (enabled capabilities, existing tools) | **107** — 18 consolidator, **89 raw** |
| hr · marketing · procurement · reports · time_attendance | 16 · 38 · 40 · 35 · 24 |
| MCP | **38** connector (13 consolidator, 25 raw) + 11 playbook |
| On **both** surfaces | 35 of MCP's 38 |
| Agents only | 72 |
| MCP only | 3 |
| Defined but exposed on neither | 33 |

## The two questions, answered

### 1. Should we stop exposing raw API calls, to agents or to MCP?

**No — and "raw vs consolidated" is the wrong axis.**

**83% of the agent-facing surface (89 of 107) is raw.** A ban means writing ~89 consolidators,
and most would be one-call passthroughs — precisely the anti-pattern quoted below: *"tools
that merely wrap existing software functionality or API endpoints."* It converts a config row
into Python that must then be maintained, for no gain.

Some things genuinely are one call and should stay raw: reference lookups the model uses to
resolve a name to an id (`get_suppliers`, `get_stock_items`, `get_staff_members`), and single
write operations (`create_rostered_shift`, `update_shift`).

The harm was never rawness. It was **two tools answering the same question** — and that is
still live:

| Surface | Base tools still exposed alongside the `_for_period` wrapper that replaced them |
|---|---|
| reports | **6** — `get_cogs_detail`, `get_pos_discounts`, `get_pos_item_sales`, `get_staff_item_orders`, `get_staff_orders`, `get_timeclock_entries` |
| time_attendance | **3** — `get_roster`, `get_roster_vs_actual`, `get_timeclock_entries` |
| MCP | **3** — `get_cogs_detail`, `get_roster`, `get_roster_vs_actual` |

**The rule is one canonical tool per question**, however it is implemented: raw when one call
is the whole answer, a consolidator when the answer needs several calls or trading-day logic.
And nothing is exposed unless it is asked for.

### 2. Should Norm's agents and Claude see the same tools?

**Yes — one catalogue, with both surfaces *derived* from it rather than hand-maintained.**

They are already 92% identical: 35 of MCP's 38 tools are also agent tools, only 3 are
MCP-only. The 72 agent-only tools are three different things:

- **Principled (~19).** Norm-internal plumbing — `norm__search_tool_result`, display
  components, automated-task creation, the three email connectors — plus live writes. Claude
  has its own email and scheduling.
- **Accidental drift (~8).** The shadowed base tools above. These belong on *neither* surface.
- **Coverage gap (~39).** marketing (orbit, brevo, metricool) and hr (bamboohr) are simply not
  on MCP. A decision nobody made.

**Decision (July 2026): marketing and HR should be on MCP.** 26 of those 39 are already
flagged read-only. That takes MCP from 38 to **61**, past the 30–50 band where tool selection
measurably degrades — so domain-scoped grants (see *Domain surfaces*, below) become a
prerequisite rather than an option, and the surface should shrink before it grows.

## Why maintenance is currently impossible

This is the through-line, and the reason the two answers above are not enough on their own.

**When a consolidator replaces a raw call, nothing records that.** There is no field marking a
tool as superseded, no field saying *why* the consolidator exists — frequently it exists
because the raw endpoint is wrong, not merely inconvenient — and retiring the raw call means
remembering to edit two separate allowlists by hand.

**Consolidators depend on raw tools by action name.**
`function_executor.call_api(connector, action, params)` resolves a *tool action*, not a URL.
So a raw tool that a consolidator uses cannot be deleted — retirement must mean **unexpose**,
never **delete**. That is exactly why a lifecycle field is needed instead of deletion.

**Nothing detects duplicates.** Three `(method, path_template)` collisions exist today:

| Method | Path | Tools |
|---|---|---|
| POST | `/api/time/rostered-shifts` | `loadedhub__add_shift`, `loadedhub__create_rostered_shift` |
| PUT | `/api/time/rostered-shifts/{shift_id}` | `loadedhub__delete_shift`, `loadedhub__update_shift` |
| GET | `/files/{file_id}` | `bamboohr__get_applicant_resume`, `bamboohr__get_company_file` |

Note the second row: **`loadedhub__delete_shift` is defined as a PUT on the update path** — a
tool named "delete" that updates. And `POST /api/time/rostered-shifts` is defined a *third*
time as `component_api_configs: roster_editor.add_shift`.

**`response_transform` is a fourth concept that reads like an alternative but is not.** It is
declarative post-processing — field mapping, nested access, array flattening, timezone
normalisation, filters, recompute — applied to the result of *any* of mechanisms 1–3.

## Norm's actions are not tools

The writes that matter run **only** from a React component, through `component_api_configs` or
a bespoke router:

- `roster_editor.add_shift` / `update_shift` / `delete_shift`
- `orders_dashboard.send_order`
- `purchase_order_editor.create_orders_batch`
- invoice-fixes `/apply`, `/receive`, `/resolve-po`

**No model can reach any of them** — not in-app, not over MCP. The single exception,
`norm__place_stock_order`, is documented as existing *"so the human can press 'Place Order' in
the embedded editor; the click is the approval."*

**Decision (July 2026): Claude must be able to action things, not just read — and no action
may be served through a UI component.** A `display_component` is an optional richer
*presentation* of a tool; it must never be the only way to invoke one.

## Authorization: the model we want already exists

**Decision (July 2026): the actions available to Claude are the same as in Norm, with the same
business guardrails.** Not a separate approval system.

`app/mcp/scopes.py` already states this principle:

> *"**Granting is not having.** An MCP scope is a projection of what the user's role already
> allows, always a subset."*

Every `McpScope` carries `requires` — org permissions from `app/auth/permissions.py` that the
user's role must actually hold — plus consent text written for the human clicking Approve, and
there is deliberately **no platform-admin bypass**. What blocks actions today is one line:

```python
V1_ACCESS_LEVELS = frozenset({ACCESS_READ, ACCESS_DRAFT})   # ACCESS_WRITE exists, barely used
```

`ACCESS_WRITE` is declared and claimed by exactly one scope (`mcp:orders:submit`). So enabling
actions is **admitting `write` per action**, each naming the org permission it requires — not
designing new security machinery.

The guardrails then apply for free, because it is the *same tool* running through the *same*
`tool_loop`: the approval pause (`status="awaiting_tool_approval"`), the per-user run mode, and
the `Approval` audit row. Where an action pauses in-app it pauses over MCP too;
`app/mcp/workflows.py` already returns `pending_approval` with a link, and that remains the
honest answer for genuinely gated actions.

**Invariant for whoever implements this:** `app/mcp/__init__.py` forbids importing
`require_permission` (not org-aware; returns early for platform admins) or
`venue_service.get_user_venues` (fails open — a user with no access rows gets every venue).
Both are enforced by `TestNoFailOpenImports` in `tests/test_mcp_execution.py`. (The
`app/mcp/__init__.py` docstring cites `tests/test_mcp_imports.py`, which does not exist —
harmless, but it is the same species of drift this document is about.) Express permission
requirements through `McpScope.requires`.

## What Anthropic's guidance says

From three primary sources (links at the end), fetched July 2026:

**1. Don't wrap endpoints — build workflow tools.** *"A common error we've observed is tools
that merely wrap existing software functionality or API endpoints."* Tools should
*"consolidate functionality, handling potentially multiple discrete operations (or API calls)
under the hood"*.

**2. Overlap is a named harm.** *"Too many tools or overlapping tools can distract agents from
pursuing efficient strategies."* This is the rostered-hours incident in one sentence.

**3. The concrete thresholds.** *"Claude's ability to pick the right tool degrades once you
exceed 30–50 available tools."* Reports and procurement sit inside that band today; MCP at 61
would be past it.

**4. Medium-grained, not mega.** Good tools are *"distinct operations that perform a complete,
meaningful unit of work, accepting parameters that modify behavior"*. One query-shaped tool
per **family** is on-guidance; one tool spanning rosters *and* invoices *and* recipes is the
named failure mode.

**5. Progressive disclosure + code execution.** Newer guidance has agents *"read tool
definitions on-demand"* and write **code** against tool APIs rather than chaining calls
through the model. Norm's consolidator sandbox (`function_code` + `call_api`) **is** this
pattern — `review_and_receive_invoices` is 49k chars of code making up to 6 API calls behind
one tool. We built the destination independently; lean into it.

**6. Token-efficient responses.** `response_format` enums (`concise`/`detailed`), pagination,
and filtering with sensible defaults.

## Proposed direction

Not built. Recorded so the next person does not have to re-derive it.

### A. Give every tool a lifecycle
Add to each tool in the spec:

- `status`: `published` (model-facing) · `building_block` (callable only by consolidator code
  via `call_api`, never exposed) · `retired`
- `superseded_by`: the `connector__action` that replaced it
- `superseded_reason`: one line — *"raw endpoint ignores venue scope"* — capturing **why** the
  consolidator exists, which today is unrecoverable once the author moves on.

Retiring becomes a one-field edit that takes effect everywhere at once. `call_api` keeps
working for `building_block` and `retired` tools, but logs when consolidator code depends on
something retired.

### B. Derive both surfaces from `status`
`agent_connector_bindings` and `mcp_capabilities` become filters over the published set, not
independent lists — reusing `app/mcp/projection.py::project_tools`, which already filters per
principal by granted scope and consented venue. Per-row description/name overrides and an
explicit opt-out stay. The consequences fall out automatically: shadowed bases become
`superseded_by` their wrapper, the 33 orphans become `building_block`, and the `read_only`
flags get set in the same pass.

### C. Name the pipeline
The concepts are stages, not alternatives. Saying so removes most of the confusion:

> **fetch** (raw · consolidator · internal handler) → **shape** (`response_transform`,
> `summary_fields`, `max_result_chars`) → **present** (`display_component`,
> `working_document`)

Worth a small `explain_tool` script that prints, for any action: its resolved pipeline, status,
what it supersedes or is superseded by, which agents and MCP grants see it, and which
consolidators depend on it. That last line is what makes retiring safe.

### D. Actions become first-class tools
Promote the component-API writes to spec tools; the UI component then calls the same tool
through the existing component-api path. One implementation, two callers. Dedupe the
rostered-shift definitions and fix `delete_shift`'s method as part of it.

### E. Domain surfaces on MCP
Tag capabilities with a domain (playbooks inherit `Playbook.agent_slug`; connector
capabilities get a column on `McpCapability`), group the consent screen by domain, record the
chosen domains on the grant, and filter in `project_tools` exactly as scopes are filtered
today. Then admit marketing/HR. Ordering matters: a venue-ops connection sees ~35 tools, a
marketing connection ~20 — each inside the safe band — instead of one 61-tool catalogue.

Path-based virtual mounts (`/mcp/procurement`) stay in reserve if a client ever needs a
distinct server *identity*; the projection seam makes that thin.

### F. Guard rules in `config_validator`
So none of this can drift back:

- a tool exposed on any surface must be `status: published`;
- a `published` tool must have an explicit `read_only`;
- no two enabled tools may share `method` + `path_template`;
- a tool with `superseded_by` may not be exposed, and must name a tool that exists;
- a `component_api_configs` write action must map to a published tool;
- one agent's surface may not contain both a base tool and its `_for_period` wrapper.

### G. Evals before restructuring
Tools should *"match your evaluation tasks"*. ~10 scripted questions per agent with expected
tool sequences, run against a live local stack, stored under `apps/api/tests/evals/` and
excluded from CI's default run (they need credentials). This is the gate for any collapse of
tool families.

## What we will not do

- **No global megatool** — medium-grained by family, per the guidance.
- **No wholesale raw-endpoint exposure.** Note this is *not* "no raw tools": 25 of MCP's 38
  connector tools are raw today and that is fine. What is excluded is publishing an entire API
  surface without curation.
- **No deleting raw tools that consolidators call** — `call_api` resolves by action name.
  Retire by status, never by deletion.
- **No N deployments of Norm's MCP server** — a projection filter already does what separate
  deployments would.
- **No in-app tool search yet** — at ≤25 well-chosen tools per agent it buys nothing; the
  problem is overlap and lifecycle, not catalogue size.
- **No separate approval model for Claude** — same actions as Norm, same guardrails.

## Open defects (consequences of having no lifecycle)

- **12 of 20 consolidators have no `read_only` flag** (6 are correctly `true`; 2 —
  `review_and_receive_invoices`, `reconcile_received_invoices` — are correctly `false`).
  `delegation.is_read_only_tool()` requires `read_only is True`, so **a consulted sub-agent
  cannot use a single `*_for_period` consolidator** and falls back to the raw base tools — the
  ones behind the rostered-hours discrepancy.
- **12 shadowed base tools remain exposed** (reports 6, time_attendance 3, MCP 3).
- **33 tools are exposed on neither surface** — dead weight with no marker saying so.
- **3 duplicate `(method, path)` pairs**, one of which (`delete_shift`) has the wrong method.

## Related incidents (why this document exists)

- `get_roster` vs `get_staff_attendance`: 332.25h vs 146.5h for the same week — overlapping
  tools with different venue scoping. Fixed July 2026 (`scripts/sync_roster_venue_scope.py`,
  `recompute` in `app/connectors/response_transform.py`).
- Nine GET-method tools that mutate state — flagged `read_only: false`
  (`scripts/sync_read_only_flags.py`) and excluded from delegated sub-agents.

## Sources

- [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) — Anthropic engineering
- [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) — Anthropic engineering
- [Tool search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) — Claude platform docs (the 30–50 / 10k thresholds)
- Claude Code MCP docs — "medium-grained" tool guidance

## Reproducing the measurements

Every count above comes from the shared config DB (`CONFIG_DATABASE_URL`, reachable locally
through the cloud-sql-proxy on `127.0.0.1:5433`). Tool counts come from
`connector_specs.tools`; surfaces from `agent_connector_bindings.capabilities` (filtering on
each capability's own `enabled` flag — it is a list of objects, not strings) and
`mcp_capabilities`; UI-only actions from `component_api_configs`. Figures are a 2026-07-20
snapshot; re-measure before relying on them.
