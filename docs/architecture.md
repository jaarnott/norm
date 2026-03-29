# Norm Architecture Guide

A conceptual overview of how the AI operations platform works, with light implementation details and file references.

---

## 1. Supervisor

The supervisor is the entry point for every user message. It routes messages to the correct domain agent.

**How it works:**
1. User sends a message (e.g. "get me sales for La Zeppa last week")
2. If continuing an existing thread → route to that thread's domain agent
3. If new message → call the **Router** (fast Haiku LLM) to classify the domain
4. Resolve the venue (auto-select if single venue, or ask the user)
5. Delegate to the domain agent (reports, procurement, hr)
6. If domain is "meta" → respond with a capabilities summary

**Example:** "Order 10 cases of tomatoes for Bessie" → Router classifies as `procurement` → ProcurementAgent handles it.

**Key file:** `app/services/supervisor.py` — `handle_message()`

---

## 2. Agents

Agents are domain specialists. Each agent knows how to handle messages for its area (reports, procurement, HR). All agents inherit from `BaseDomainAgent`.

**Three agents:**
- **ReportsAgent** — sales data, charts, analytics
- **ProcurementAgent** — ordering stock, managing purchase orders
- **HrAgent** — employee onboarding, roster management, hiring

**How they work:**
1. Agent checks if connector tools are bound (via Settings)
2. If tools exist → runs the **Tool Loop** (multi-turn LLM with tool calling)
3. If no tools → falls back to single-shot LLM interpretation (legacy)

**Example:** ReportsAgent receives "sales for last week" → tool loop calls `get_sales_data` on LoadedHub → LLM formats the response → optionally renders a chart.

**Key files:**
- `app/agents/base.py` — BaseDomainAgent interface
- `app/agents/reports/agent.py`, `app/agents/procurement/agent.py`, `app/agents/hr/agent.py` — implementations
- `app/agents/registry.py` — agent discovery (`get_agent("reports")`)
- `app/agents/router.py` — LLM-based message classification

---

## 3. Connectors

A connector is a configuration-driven definition of an external API. Connectors are stored in the shared config database and managed via the Settings UI. No code changes are needed to add or modify a connector.

**What a connector spec defines:**
- API base URL (Jinja2 template)
- Authentication type (Bearer, API key, Basic, OAuth2)
- List of **tools** (actions the API supports)
- Credential fields (what the user needs to provide per venue)
- OAuth configuration (authorize URL, token URL, scopes, client ID/secret)

**Current connectors:** LoadedHub, BambooHR, Deputy, Gmail, Microsoft Outlook, Bidfood + internal connectors (norm, norm_reports, norm_email)

**Example:** The LoadedHub connector has 30 tools including `get_roster`, `get_sales_data`, `get_stock_items`, `get_purchase_orders_summary`. Each tool defines the HTTP method, URL path template, required fields, and response transform.

**Key files:**
- `app/db/config_models.py` — ConnectorSpec model
- `app/connectors/spec_executor.py` — renders templates + executes HTTP requests
- `app/connectors/registry.py` — resolves connector for a domain + action

---

## 4. Tools

A tool is a single action within a connector. Tools come in two types:

### External tools (API calls)
Defined in the ConnectorSpec `tools` array. Each tool has: action name, HTTP method, URL path template, required/optional fields, response transform, and optional display component.

**Example:** `get_sales_data` on LoadedHub — GET request to `/api/sales/summaries?from={{start}}&to={{end}}`, returns sales totals.

### Internal tools (local handlers)
Registered via the `@register("connector", "action")` decorator in Python. These execute against the local database, not external APIs.

**Current internal tools (23):**

| Tool | Purpose |
|---|---|
| `norm/resolve_dates` | Convert "last week" to ISO date ranges using a fast LLM call |
| `norm_reports/render_chart` | Build a chart visualization from a prior tool call's data |
| `norm/search_tool_result` | Fuzzy search across large tool results |
| `norm/create_automated_task` | Schedule a recurring agent task |
| `norm/list_automated_tasks` | List scheduled tasks |
| `norm_email/send_notification` | Send system email |
| `gmail/send_email` | Send email via user's Gmail |
| `norm_hr/get_jobs`, `create_job`, etc. | Hiring/recruitment management |

**How the tool loop uses tools:**
1. LLM decides which tool(s) to call based on the system prompt
2. Read-only tools (GET) auto-execute, results fed back to LLM
3. Write tools (POST/PUT/DELETE) pause for user approval
4. Up to 10 iterations before returning a response

**Key files:**
- `app/agents/tool_loop.py` — `run_tool_loop()`, `_execute_tool_call()`
- `app/agents/internal_tools.py` — `@register` decorator + all internal handlers

---

## 5. Helpers

Helpers are internal tools that assist the LLM with tasks it can't do well on its own.

### resolve_dates
Converts natural language date expressions to precise ISO 8601 date ranges. The LLM is bad at date math, so this tool pre-computes reference dates and uses a fast Haiku call.

**Input:** `"last week"`, `"yesterday"`, `"every Friday 5pm-9pm for 12 weeks"`
**Output:** `{"periods": [{"label": "Mon 23 Mar", "start": "2026-03-23T00:00:00+13:00", "end": "2026-03-29T23:59:59+13:00"}]}`

### search_tool_result
When a tool returns a large dataset (1000+ items), the LLM can't process it all. This tool does fuzzy text search across the result to find relevant items.

**Input:** `{"keyword": "tomato", "tool_call_id": "toolu_01..."}`
**Output:** Filtered subset of the original tool result

### render_chart
Takes data from a prior tool call and builds a chart visualization (bar, line, pie, etc.). The LLM specifies which fields to use for axes and series.

**Key file:** `app/agents/internal_tools.py`

---

## 6. Components

Components are React UI elements that render tool results. They come in three modes:

### a. Inline (in conversation bubbles)
Rendered inside chat messages. Small, read-only visualizations.

| Component | What it shows |
|---|---|
| `generic_table` | Auto-detected table from any array data |
| `roster_table` | Shift summary table |
| `chart` | Bar, line, pie charts (via Recharts) |
| `automated_task_preview` | Single scheduled task card |
| `tool_approval` | Approve/reject pending tool calls |

### b. Full-width split pane (above conversation)
Rendered in the top half of a split view, with the conversation below. These are interactive editors.

| Component | What it shows |
|---|---|
| `roster_editor` | Week/day roster with drag-drop shifts |
| `hiring_board` | Jobs + candidate applications |
| `orders_dashboard` | Purchase order list with expandable line items |
| `report_builder` | Grid-based dashboard with editable charts |
| `purchase_order_editor` | Order form with line items |
| `criteria_editor` | Hiring criteria checklist |

### c. Functional pages (standalone)
Full-page components accessed via sidebar navigation, not from conversation.

| Page | Sidebar location | What it shows |
|---|---|---|
| Roster | HR → Roster | Week roster editor (loads from LoadedHub) |
| Hiring | HR → Hiring | Job board (loads from BambooHR) |
| Orders | Procurement → Orders | Purchase order dashboard (loads from LoadedHub) |
| Reports | Reports → Reports | Saved report list |
| Tasks (×3) | Each agent → Tasks | Automated task board |

**How display blocks are created:**
1. A tool definition in the ConnectorSpec can set `display_component: "chart"`
2. When the tool executes, the tool loop builds a display block: `{component, data, props}`
3. The frontend's `DisplayBlockRenderer` looks up the component in the REGISTRY and renders it

**Key files:**
- `apps/web/app/components/display/DisplayBlockRenderer.tsx` — component registry
- `apps/web/app/components/pages/pageRegistry.ts` — functional page definitions
- `apps/web/app/components/pages/FunctionalPage.tsx` — page data loading + rendering

---

## 7. Response Transforms

Transforms convert raw API responses into LLM-friendly format. Configured per-tool in the ConnectorSpec via the Settings UI.

**What transforms do:**
- **Field selection** — keep only relevant fields, rename them
- **Nested access** — `"supplier.name"` → extracts from nested objects
- **Array flattening** — `flatten: ["lines"]` produces one row per array item
- **Filtering** — drop rows based on conditions (equals, contains, is_empty, etc.)
- **Field options** (pipe syntax):
  - `"price|round:2"` — round to 2 decimal places
  - `"clockinTime|tz"` — normalize to venue timezone
  - `"clockinTime|dow"` — add a `_dayOfWeek` field ("Tuesday")

**Example config:**
```json
{
  "fields": {
    "id": "id",
    "supplier.name": "supplier",
    "total|round:2": "total"
  },
  "flatten": ["lines"],
  "filters": [{"field": "status", "operator": "equals", "value": "active"}]
}
```

**Key file:** `app/connectors/response_transform.py`

---

## 8. Consolidators

A consolidator is a meta-tool that chains multiple API calls together. It's configured as `consolidator_config` on a tool definition — the LLM calls one tool, but the backend executes a multi-step pipeline.

**What consolidators do:**
1. Execute steps in sequence (each step calls a connector action)
2. Pass data between steps using `{{step_id.field}}` templates
3. Filter intermediate results
4. Search across results by keyword
5. Select output fields

**Example:** A "get stock with supplier details" consolidator might:
1. Step 1: Call `get_stock_items` → returns 1000 items
2. Step 2: Filter to items matching search keyword
3. Step 3: Call `get_suppliers` for each unique supplier ID
4. Return combined data with supplier names attached

**Template variables available:**
- `{{today_iso}}`, `{{one_week_ago_iso}}` — auto-computed dates
- `{{input_param}}` — from the LLM's tool call input
- `{{step_id.field.path}}` — data from a prior step
- `{{step_id}}` — entire dataset from a prior step

**Key file:** `app/agents/internal_tools.py` — `execute_consolidator()` (line ~1094)

---

## 9. Working Documents

Working documents are an edit/sync layer between frontend components and external APIs. They enable interactive editing (roster shifts, order lines, hiring criteria) with optimistic concurrency and background sync.

**How it works:**
1. A tool call creates a working document with data from an external API
2. The frontend component receives a `working_document_id` instead of raw data
3. User edits create **operations** (add_shift, update_line, delete_item)
4. Operations are sent via PATCH with a version number (optimistic locking)
5. The backend resolves operations to connector API calls via **operation mappings**
6. Sync happens either automatically (`sync_mode: "auto"`) or on user submit (`sync_mode: "submit"`)

**Sync states:** `synced` → `dirty` → `syncing` → `synced` (or `error`)

**Version conflict:** If the frontend sends `version: 5` but the DB has `version: 6`, the PATCH returns 409 Conflict.

**Example:** User drags a shift in the RosterEditor → PATCH with `{op: "update_shift", shift_id: "...", fields: {clockin_time: "..."}}` → backend maps to LoadedHub `update_shift` API call → sync status updates to `synced`.

**Key files:**
- `app/routers/working_documents.py` — CRUD + PATCH + submit endpoints
- `app/services/document_sync.py` — resolves operations to connector calls
- `app/db/models.py` — WorkingDocument model (data, version, sync_status, pending_ops)

---

## 10. Prompt Builder

The prompt builder dynamically constructs the system prompt for each agent based on its connector bindings, available tools, venue context, and timezone. No hardcoded prompts — everything is built from configuration.

**What it builds:**
1. Loads the agent's system prompt from the config DB (AgentConfig.system_prompt)
2. Collects all enabled tools from bound connectors (AgentConnectorBinding → ConnectorSpec.tools)
3. Injects venue context (name, timezone, today's date)
4. Formats tool descriptions for the Anthropic API's tool-use format
5. Adds domain-specific instructions (date handling rules, formatting guidelines)

**Gate logic:** A tool is only included if:
- The agent has a binding to the connector
- The capability for that action is enabled in the binding
- The connector has credentials configured for the venue (external connectors only)

**Example:** The ReportsAgent prompt is built from: base prompt (from config DB) + LoadedHub tools (get_sales_data, get_roster, etc.) + norm_reports tools (render_chart) + norm tools (resolve_dates, search_tool_result) + venue context ("La Zeppa, timezone Pacific/Auckland, today is Monday 29 Mar 2026").

**Key file:** `app/agents/prompt_builder.py` — `build_dynamic_prompt()`, `build_tool_definitions()`

---

## 11. Config Database

All system configuration lives in a dedicated shared Cloud SQL instance (`norm-config`) that all environments read from. Edit a connector spec or agent prompt once — it's immediately available in testing, staging, and production.

**What's in the config DB:**

| Table | Content |
|---|---|
| `connector_specs` | Tool definitions, auth types, OAuth config, response transforms |
| `agent_configs` | Agent system prompts, display names, descriptions |
| `agent_connector_bindings` | Which agents can use which connectors, with per-capability enable/disable |
| `system_secrets` | API keys, OAuth credentials, JWT secret (loaded at startup) |

**What's NOT in the config DB (stays in per-environment DB):**
- Threads, messages, users, organizations, venues
- ConnectorConfig (per-venue credentials, OAuth tokens)
- Roles, permissions, approvals, orders

**No fallback:** If the config DB is unreachable, the API fails at startup with a clear error. There is no silent fallback to the local database — this prevents stale config from being used accidentally.

**Key files:**
- `app/db/config_models.py` — ConfigBase + ConnectorSpec, AgentConfig, AgentConnectorBinding, SystemSecret
- `app/db/engine.py` — `get_config_db()`, `get_config_db_rw()`, startup connectivity test
- `app/config.py` — `CONFIG_DATABASE_URL` setting

---

## 12. Automated Tasks (Saved Threads)

Automated tasks are threads that can be saved and re-run on a schedule. A user creates a conversation (e.g. "generate a daily sales report for Bessie"), saves it as a task, and it runs automatically via APScheduler.

**How it works:**
1. User creates a normal thread via conversation
2. User clicks "Save as Task" → creates an AutomatedTask record with the prompt, schedule, and agent
3. APScheduler triggers the task on the cron schedule
4. The scheduler creates a new conversation thread and runs the agent with the saved prompt
5. Results are stored as a normal thread conversation

**Task states:** `active` (running on schedule), `paused` (user paused), `draft` (not yet scheduled)

**Schedule format:** Cron expressions (e.g. `0 8 * * 1-5` = 8am weekdays)

**Example:** "Bessie Morning Sales Check" — runs daily at 8am, calls the ReportsAgent with "Generate a sales report for Bessie & Engineers for yesterday", emails the result.

**Key files:**
- `app/db/models.py` — AutomatedTask, AutomatedTaskRun models
- `app/services/task_scheduler.py` — APScheduler integration, `schedule_task()`, `execute_task_now()`
- `app/routers/automated_tasks.py` — CRUD + run/pause/resume endpoints
- `app/agents/internal_tools.py` — `create_automated_task`, `list_automated_tasks`, `toggle_automated_task`

---

## 13. Auth & Permissions

Two-tier authorization: **platform admin** (system-wide) and **organization roles** (per-org, granular).

### Platform admin
`User.role = "admin"` — set on the first user to register. Has full access to everything: deployments, system config, connector specs, E2E tests. Bypasses all org permission checks.

### Organization roles
Stored in the `roles` table with a JSON `permissions` array. Each user's org membership links to a role.

**Standard roles:**

| Role | Key permissions |
|---|---|
| **Owner** | Everything in the org (23 scopes) |
| **Manager** | Everything except billing and custom roles |
| **Team Member** | Read + create tasks only |
| **Payroll Admin** | HR + roster read/write |

**Custom roles:** Managers/Owners can create custom roles with any combination of the 23 permission scopes across 8 categories (Tasks, Orders, Roster, HR, Reports, Billing, Organization, Settings).

**How it's enforced:**
- FastAPI dependency: `require_permission("tasks:read")` checks the user's org role
- Platform admin scopes (`admin:deployments`, `admin:tests`, `admin:system`) checked via `User.role == "admin"`
- Frontend hides UI elements based on `user.permissions` from `/auth/me`

**Key files:**
- `app/auth/permissions.py` — 23 scopes, standard role definitions, permission groups
- `app/auth/dependencies.py` — `require_permission()`, `require_role()`
- `app/routers/roles.py` — role CRUD + member assignment

---

## 14. SSE Streaming

Messages are processed via Server-Sent Events (SSE) so the frontend shows real-time progress: routing decisions, thinking steps, tool executions, and the final response.

**How it works:**
1. Frontend calls `POST /api/messages/stream`
2. Backend returns `StreamingResponse` with `text/event-stream`
3. Processing runs in a background thread (`asyncio.to_thread`)
4. Events are pushed via a queue: `on_event({"type": "...", ...})`
5. Frontend reads events and updates the UI progressively

**Event types:**

| Event | When | Frontend action |
|---|---|---|
| `routing` | Supervisor classified the domain | Show "Routed to reports agent" |
| `thinking` | Agent is reasoning | Show thinking step in timeline |
| `tool_call_start` | Tool execution begins | Show "Fetching sales data..." |
| `tool_call_complete` | Tool finished | Show result summary |
| `display_block` | Tool produced a visual | Render chart/table/editor |
| `thread_created` | New thread created | Update thread list |
| `complete` | Final response ready | Show the message + display blocks |
| `error` | Something failed | Show error message |
| `quota_exceeded` | Token limit hit | Show upgrade prompt |

**Key files:**
- `app/routers/messages.py` — `post_message_stream()` endpoint
- `app/agents/tool_loop.py` — `_emit_event()`, `set_event_callback()`
- `apps/web/app/lib/api.ts` — `apiStream()` client-side SSE reader

---

## 15. Display Blocks

Display blocks are the mechanism for rendering rich UI from tool results. They bridge the backend tool loop and the frontend component registry.

**Lifecycle:**
1. A tool definition sets `display_component: "chart"` in the ConnectorSpec
2. The tool executes and returns data (e.g. sales figures)
3. `_build_display_block()` in the tool loop creates: `{component: "chart", data: {...}, props: {...}}`
4. The block is attached to the assistant message's `display_blocks` array
5. The frontend's `DisplayBlockRenderer` looks up "chart" in the REGISTRY → renders `<Chart data={...} />`

**Two rendering modes:**
- **Inline blocks** — rendered inside chat message bubbles (chart, table, task preview)
- **Full-width blocks** — rendered above the conversation in a split pane (roster_editor, report_builder, orders_dashboard)

**Working document blocks:** Instead of passing raw data, the block passes `{working_document_id: "..."}`. The component fetches the document and subscribes to changes.

**Dynamic props:** Tools can pass runtime configuration via `_chart_props` in the result payload — these get merged into the block's `props`.

**Key files:**
- `app/agents/tool_loop.py` — `_build_display_block()` (line ~1362)
- `apps/web/app/components/display/DisplayBlockRenderer.tsx` — REGISTRY, FULL_WIDTH_COMPONENTS
- `apps/web/app/components/tasks/ThreadDetail.tsx` — renders blocks in conversation + split pane

---

## Data Flow Summary

```
User message
  → Supervisor (routing)
    → Router LLM (classify domain)
    → Agent (reports/procurement/hr)
      → Tool Loop (up to 10 iterations)
        → Internal tool (@register handler)
          OR
        → External tool (ConnectorSpec → spec_executor → HTTP)
          → Response Transform (field mapping, filtering)
        → Display Block (component + data)
      → LLM response (text + display blocks)
  → Frontend renders conversation + components
```

---

## File Map

| Concept | Key Files |
|---|---|
| **Supervisor** | `app/services/supervisor.py` |
| **Agents** | `app/agents/base.py`, `app/agents/{domain}/agent.py`, `app/agents/registry.py` |
| **Router** | `app/agents/router.py` |
| **Tool Loop** | `app/agents/tool_loop.py` |
| **Internal Tools** | `app/agents/internal_tools.py` |
| **Connectors** | `app/db/config_models.py`, `app/connectors/spec_executor.py`, `app/connectors/registry.py` |
| **Transforms** | `app/connectors/response_transform.py` |
| **Prompt Builder** | `app/agents/prompt_builder.py` |
| **Components** | `apps/web/app/components/display/DisplayBlockRenderer.tsx`, `apps/web/app/components/pages/` |
| **Working Documents** | `app/routers/working_documents.py`, `app/services/document_sync.py` |
| **Config DB** | `app/db/config_models.py`, `app/db/engine.py` (get_config_db) |
| **Automated Tasks** | `app/services/task_scheduler.py`, `app/routers/automated_tasks.py` |
| **Auth & Permissions** | `app/auth/permissions.py`, `app/auth/dependencies.py`, `app/routers/roles.py` |
| **SSE Streaming** | `app/routers/messages.py`, `apps/web/app/lib/api.ts` (apiStream) |
| **Display Blocks** | `app/agents/tool_loop.py` (_build_display_block), `DisplayBlockRenderer.tsx` |
