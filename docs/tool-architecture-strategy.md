# Tool Architecture Strategy

*Status: agreed direction, July 2026. Each step below is executed separately —
this document is the map, not a work order.*

## The question

Norm has 145 connector tools. Agents funnel them through per-agent bindings
(reports sees 33, procurement 38); the MCP server serves a general catalog of
49. Two incidents traced back to this surface: two tools answering "rostered
hours next week" 2.3× apart, and nine GET-method tools that mutate state.

Should we reduce tools **per agent**, or **overall** by collapsing many tools
into fewer, versatile consolidators? And should the MCP server expose curated
workflow tools or raw endpoints?

## Where we are (measured July 2026)

| Fact | Value |
|---|---|
| Spec tools defined | 145 (128 never called in local history) |
| Agent-facing surface | reports 33 tools / ~8.8k tokens **per turn**; procurement 38 / 7.1k; time_attendance 22 / 5.5k |
| Overlapping pairs visible to one agent | reports **7**, T&A 3, procurement 1 (`get_sales_data` *and* `get_sales_for_period`, …) |
| `*_for_period` consolidators | 12 tools sharing **byte-identical** function_code; only config differs |
| MCP surface | 38 connector tools + 11 playbook workflow tools, single `/mcp` endpoint |
| MCP catalog projection | already per-principal (`app/mcp/projection.py::project_tools`) — filters by granted scopes + consented venues |
| Sibling servers | `CBHG_Kitchen` and `CBHG_Marketing` are separate, domain-scoped MCP servers outside this repo |

## What Anthropic's guidance says

From three primary sources (links at the end), fetched July 2026:

**1. Don't wrap endpoints — build workflow tools.**
*"A common error we've observed is tools that merely wrap existing software
functionality or API endpoints."* Tools should *"consolidate functionality,
handling potentially multiple discrete operations (or API calls) under the
hood"*; *"build a few thoughtful tools targeting specific high-impact
workflows… and scale up from there."*

**2. Overlap is a named harm.**
*"Too many tools or overlapping tools can distract agents from pursuing
efficient strategies."* This is our rostered-hours incident in one sentence.

**3. The concrete thresholds.**
*"Claude's ability to pick the right tool degrades once you exceed 30–50
available tools."* Use deferred loading / tool search at 10+ tools or >10k
tokens of definitions. Reports and procurement sit inside the degradation band
today; the MCP catalog (49) is just past it.

**4. Medium-grained, not mega.**
Good tools are *"distinct operations that perform a complete, meaningful unit
of work, accepting parameters that modify behavior"* (their example:
`query_database`). Too coarse = *"combining unrelated operations or requiring
complex conditional logic."* So: one query-shaped tool per **family** (sales
reporting with `dataset`/`group_by`/`period` parameters) is on-guidance; one
tool spanning rosters *and* invoices *and* recipes is the named failure mode.

**5. The direction of travel is progressive disclosure + code execution.**
Their newest guidance has agents *"read tool definitions on-demand, rather
than reading them all up-front"*, and write **code** against tool APIs instead
of chaining tool calls through the model (their example saved 98.7% of
tokens). Norm's consolidator sandbox (`function_code` + `call_api`) **is this
pattern** — `review_and_receive_invoices` is 49k chars of code making up to 6
API calls, surfaced as one tool. We built the destination independently; the
strategy is to lean into it, not away.

**6. Token-efficient responses.**
Give tools a `response_format` enum (`concise` / `detailed` — their Slack
tools' concise mode used ~⅓ of the tokens); implement pagination and
filtering with sensible defaults.

## The strategy

Anthropic's answer to "fewer per agent or fewer overall" is **both, at
different layers**:

1. **Design layer — fewer, better tools.** Workflow-shaped, medium-grained,
   zero overlap. Raw endpoints stop being agent-facing; they remain as
   internal building blocks that consolidator code and component-API configs
   call. Target: **≤25 tools / ≤6k definition tokens per agent**.
2. **Serving layer — the catalog may grow; per-request context must not.**
   In-app, per-agent bindings are the funnel (they already work). On MCP,
   claude.ai already defer-loads our tools; the remaining wins are selection
   accuracy, consent blast-radius, and non-deferring clients.
3. **MCP stays curated.** Consolidators + playbooks are the public surface
   (`norm_playbook__*` is exactly Anthropic's "workflow tool"). Raw endpoints
   are never exposed wholesale.
4. **Per-domain MCP *surfaces*, not per-domain *deployments*.** The ecosystem
   already runs domain servers (Kitchen, Marketing). Norm achieves the same
   funnel by filtering its existing per-principal projection by a domain
   grant chosen at consent — one endpoint, one OAuth registration, one audit
   trail. A kitchen user connecting Claude never sees ordering tools.
   Cross-domain questions stay answerable in one connection because agents
   can delegate to each other in-app.
5. **Evals before restructuring.** Tools should *"match your evaluation
   tasks"* — so the eval suite comes before any collapse, and every
   restructuring step is judged against it.

## Implementation steps

Each step is independent, separately approved, and has its own gate.

### Step 1 — De-overlap (config-only; the immediate win)
For every base tool shadowed by a `*_for_period` wrapper, remove the **base**
from agent bindings and MCP capabilities. The wrapper (trading-day-aware,
window-echoing) becomes the only agent-facing version; the base remains as an
internal building block. Sync script under `apps/api/scripts/` (follow
`sync_read_only_flags.py`'s dry-run-first shape). Add a `config_validator`
rule: error when one agent's surface contains both a base tool and its
wrapper.
*Removes 7 tools from reports, 3 from T&A, 1 from procurement, plus MCP
duplicates. Gate: token/count measurement before vs after; the rostered-hours
question gets one canonical answer; full API suite.*

### Step 2 — Tool-selection evals
~10 scripted questions per agent with expected tool sequences, run against a
live local stack (same method as the delegation live-test). Store under
`apps/api/tests/evals/`, excluded from CI's default run (needs credentials).
*This is the gate for everything below.*

### Step 3 — Pilot one family consolidator
`get_sales_report(dataset, group_by, period, response_format)` covering the
sales / product-sales / staff-sales family — built in the existing
consolidator sandbox, replacing ~6 agent-facing tools for the reports agent
only. Include the `concise`/`detailed` response enum.
*Gate: eval pass-rate ≥ Step 2 baseline; reports' tokens/turn down; concise ≤
⅓ of detailed on a real week. Extend to the roster/attendance family only on
this evidence.*

### Step 4 — Response-size hygiene
`summary_fields` / `max_result_chars` (already supported by
`_slim_tool_result`) on the most-called tools; audit against the ~25k-token
response norm.

### Step 5 — Domain surfaces on MCP
Tag capabilities with a domain (playbooks inherit `Playbook.agent_slug`;
connector capabilities get a column on `McpCapability`). Consent screen groups
by domain; the grant records chosen domains; `project_tools` filters by them
exactly as it filters scopes today. Path-based virtual mounts
(`/mcp/procurement`) stay in reserve if a client ever needs a distinct server
*identity* — the projection seam makes that thin.

## What we will not do

- **No global megatool** — medium-grained by family, per the guidance.
- **No raw-endpoint MCP exposure** — the documented anti-pattern, and
  non-Claude clients have no tool search to absorb it.
- **No N deployments of Norm's MCP server** — five OAuth registrations and
  consent flows for what a projection filter already does.
- **No in-app tool search yet** — at ≤25 well-chosen tools per agent it buys
  nothing; our problem is overlap, not catalog size.
- **No restructuring ahead of the evals.**

## Sources

- [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) — Anthropic engineering
- [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) — Anthropic engineering
- [Tool search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) — Claude platform docs (the 30–50 / 10k thresholds)
- Claude Code MCP docs — "medium-grained" tool guidance

## Related incidents (why this exists)

- `get_roster` vs `get_staff_attendance`: 332.25h vs 146.5h for the same week —
  overlapping tools with different venue scoping. Fixed July 2026
  (`scripts/sync_roster_venue_scope.py`, `recompute` in
  `app/connectors/response_transform.py`).
- Nine GET-method tools that mutate state — now flagged `read_only: false`
  (`scripts/sync_read_only_flags.py`) and excluded from delegated sub-agents.
