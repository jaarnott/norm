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
| **Config DB** | `app/db/config_models.py`, `app/db/engine.py` (get_config_db) |
