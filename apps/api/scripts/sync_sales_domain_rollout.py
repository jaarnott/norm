"""Roll every config-DB reference over to `get_sales`, then retire the old rows.

Phase 1 of the domain-tools arc (24 Aug 2026). Ordering matters — the
whole sequence, each step idempotent:

    1. deploy the API (norm.list_venues + the venue='all' refusal ship
       with it; ui/fixture/test changes ride along),
    2. uv run python scripts/sync_sales_config.py       (install get_sales)
    3. uv run python scripts/sync_sales_domain_rollout.py   (THIS: swap
       bindings, playbook filters + prose, prompts, MCP rows)
    4. uv run python scripts/sync_for_period_config.py  (prunes the five
       sales wrapper rows — they carry `wraps` and left WRAPPED)
    5. uv run python scripts/sync_periodic_reports_config.py  (deletes the
       three norm_reports periodic rows)

Running 4/5 before 3 would leave bindings/filters naming deleted tools —
the silent-drop incident class the validator now catches. This script
re-runs the playbook-filter validation at the end and fails loudly if
anything still points at a missing tool.

Needles were verified against the LIVE rows on 24 Aug 2026 (prompts:
reports, app_builder; playbooks: weekly_sales_report,
product_sales_analysis, staff_sales_performance, cogs_analysis,
sales_comparison; bindings: reports/procurement/app_builder loadedhub +
reports/norm_reports; MCP: 5 loadedhub + 3 norm_reports rows).

Usage:
    uv run python scripts/sync_sales_domain_rollout.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

RETIRED_LOADEDHUB = [
    "get_sales_for_period",
    "get_pos_item_sales_for_period",
    "get_staff_orders_for_period",
    "get_staff_item_orders_for_period",
    "get_pos_discounts_for_period",
]
RETIRED_NORM_REPORTS = [
    "get_periodic_sales",
    "get_periodic_product_sales",
    "get_periodic_staff_sales",
]
RETIRED = RETIRED_LOADEDHUB + RETIRED_NORM_REPORTS

#: Sentence-level swaps (never token swaps). Applied in order; each is
#: skipped when absent, so re-runs are no-ops.
PLAYBOOK_PATCHES: dict[str, list[tuple[str, str]]] = {
    "weekly_sales_report": [
        (
            "### Step 1: Resolve dates\n"
            "Always start by calling `resolve_dates` to convert the user's "
            "date expression into precise ISO date ranges. Never guess dates.",
            "### Step 1: Dates\n"
            "Pass the user's period phrase ('last week', 'yesterday') "
            "straight to `get_sales` — it resolves it against the venue's "
            "trading calendar itself. Never work out dates yourself.",
        ),
        (
            "- Call `get_sales_for_period` for the resolved date range. If "
            "the user mentions multiple venues, call in parallel for each "
            "venue.\n"
            "- For product-level breakdowns, call "
            "`get_pos_item_sales_for_period` with the same date range.\n"
            "- For staff performance context, call "
            "`get_staff_orders_for_period` if relevant.",
            "- Call `get_sales` with breakdown 'daily' for the period. For "
            "multiple venues pass venues as a list, or 'all' for the whole "
            "group — one call, never one per venue.\n"
            "- For product-level breakdowns, call `get_sales` with "
            "breakdown 'items' for the same period.\n"
            "- For staff performance context, use breakdown 'staff' if "
            "relevant.",
        ),
        (
            "For budget vs actual questions, call `get_budgets` (takes a "
            "period in plain English or from_date/to_date; dates are "
            "corrected to the day each budget is FOR) alongside the sales "
            "figures.",
            "For budget vs actual questions, pass compare 'budget' (and/or "
            "'last_year') to `get_sales` — the joins and variance columns "
            "are computed in the engine. `get_budgets` remains for "
            "budget-only questions.",
        ),
    ],
    "product_sales_analysis": [
        (
            "Use `get_periodic_product_sales` for all product queries:\n"
            "- `category`: filter (e.g. 'Beers', 'Wines', 'Spirits', "
            "'Mains')\n"
            "- `group`: filter by group ('Food', 'Beverage')\n"
            "- `sort_by`: 'sales' (revenue) or 'quantity' (units)\n"
            "- `top_n`: top N products (e.g. 10, 20)\n"
            "- `group_by`: 'total' or 'month' for trends\n"
            "- `time_windows`: for lunch vs dinner product mix",
            "Use `get_sales` with breakdown 'items' for all product "
            "queries:\n"
            "- `period`: plain English ('last month') — resolved against "
            "the venue's trading day\n"
            "- `category`: filter (e.g. 'Beers', 'Wines', 'Spirits', "
            "'Mains')\n"
            "- `group`: filter by group ('Food', 'Beverage')\n"
            "- `sort_by`: 'sales' (revenue) or 'quantity' (units)\n"
            "- `top`: top N products (default 25; the rest roll into "
            "'(others)' so totals stay honest)\n"
            "- `group_by`: 'month' for per-month trend rows\n"
            "- `time_windows`: for lunch vs dinner product mix\n"
            "- `venues`: a list or 'all' for group-wide questions",
        ),
    ],
    "staff_sales_performance": [
        (
            "Use `get_periodic_staff_sales` for all staff performance "
            "queries:\n"
            "- Without `interval`: returns per-staff totals (who sold the "
            "most)\n"
            "- With `interval` (e.g. '00:30:00'): returns totals + interval "
            "winners (who had the best 30-min slot)\n"
            "- `top_n`: limit to top N staff\n"
            "- `group_by`: 'each' (per slot) or 'day'",
            "Use `get_sales` with breakdown 'staff' for all staff "
            "performance queries:\n"
            "- `period`: plain English ('last week') — resolved against the "
            "venue's trading day\n"
            "- Without `interval`: per-staff totals (who sold the most)\n"
            "- With `interval` (e.g. '00:30:00', one venue): totals + "
            "interval winners (who had the best 30-min slot)\n"
            "- `staff_name`: drill into one person's product mix\n"
            "- `top`: limit to top N staff",
        ),
    ],
    "sales_comparison": [
        (
            "Use `get_periodic_sales` — one call handles everything:",
            "Use `get_sales` with `time_windows` — one call handles everything:",
        ),
        (
            "### For period-over-period comparisons (this month vs last "
            "month)\n"
            "Call `resolve_dates` then `get_sales_for_period` for each "
            "period in parallel.",
            "### For period-over-period comparisons (this month vs last "
            "month)\n"
            "Call `get_sales` once per period phrase, in parallel — it "
            "resolves the dates itself. For budget or last-year baselines "
            "pass compare ('budget', 'last_year') instead: the joins are "
            "computed in the engine, on the same baseline for every venue.",
        ),
        (
            "For budget vs actual questions, call `get_budgets` (takes a "
            "period in plain English or from_date/to_date; dates are "
            "corrected to the day each budget is FOR) alongside the sales "
            "figures.",
            "For budget vs actual questions, pass compare 'budget' (and/or "
            "'last_year') to `get_sales` — the joins and variance columns "
            "are computed in the engine. `get_budgets` remains for "
            "budget-only questions.",
        ),
    ],
}

PROMPT_PATCHES: dict[str, list[tuple[str, str]]] = {
    "reports": [
        (
            "use get_pos_item_sales_for_period and rank by revenue",
            "use get_sales with breakdown 'items' and rank by revenue",
        ),
    ],
    "app_builder": [
        (
            "window.norm.call('loadedhub', 'get_sales_for_period', "
            "{ interval: '1.00:00:00', period: 'last week' })",
            "window.norm.call('loadedhub', 'get_sales', "
            "{ breakdown: 'daily', period: 'last week' })",
        ),
        ("// render res.data ...", "// render res.rows ..."),
    ],
}


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import (
        AgentConfig,
        AgentConnectorBinding,
        McpCapability,
        Playbook,
    )
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changes: list[str] = []
    try:
        # ── 1. Bindings: retired names → get_sales (loadedhub), trio out ──
        for b in (
            db.query(AgentConnectorBinding)
            .filter(AgentConnectorBinding.enabled == True)  # noqa: E712
            .all()
        ):
            caps = [dict(c) for c in (b.capabilities or [])]
            if not caps:
                continue
            new_caps: list[dict] = []
            swapped = False
            for c in caps:
                action = c.get("action")
                if b.connector_name == "loadedhub" and action in RETIRED_LOADEDHUB:
                    if not any(x.get("action") == "get_sales" for x in new_caps):
                        new_caps.append({**c, "action": "get_sales"})
                    swapped = True
                elif (
                    b.connector_name == "norm_reports"
                    and action in RETIRED_NORM_REPORTS
                ):
                    swapped = True  # dropped — get_sales lives on loadedhub
                else:
                    new_caps.append(c)
            if swapped:
                b.capabilities = new_caps
                flag_modified(b, "capabilities")
                changes.append(
                    f"binding {b.agent_slug}/{b.connector_name}: "
                    f"{len(caps)} -> {len(new_caps)} caps"
                )

        # ── 2. Playbooks: tool_filter swaps + prose needles ──────────────
        for p in db.query(Playbook).all():
            filt = list(p.tool_filter or [])
            new_filt: list[str] = []
            touched = False
            for name in filt:
                if name in RETIRED:
                    if "get_sales" not in new_filt:
                        new_filt.append("get_sales")
                    touched = True
                else:
                    new_filt.append(name)
            if touched:
                p.tool_filter = new_filt
                flag_modified(p, "tool_filter")
                changes.append(f"playbook {p.slug}: filter -> {new_filt}")
            text = p.instructions or ""
            for old, new in PLAYBOOK_PATCHES.get(p.slug, []):
                if old in text:
                    text = text.replace(old, new)
                    changes.append(f"playbook {p.slug}: prose swapped")
            if text != (p.instructions or ""):
                p.instructions = text

        # ── 3. Prompts ───────────────────────────────────────────────────
        for slug, patches in PROMPT_PATCHES.items():
            a = db.query(AgentConfig).filter(AgentConfig.agent_slug == slug).first()
            if not a or not a.system_prompt:
                continue
            text = a.system_prompt
            for old, new in patches:
                if old in text:
                    text = text.replace(old, new)
                    changes.append(f"prompt {slug}: needle swapped")
            if text != a.system_prompt:
                a.system_prompt = text

        # ── 4. MCP rows: retire old, ensure get_sales ────────────────────
        for m in db.query(McpCapability).all():
            if (m.target == "loadedhub" and m.action in RETIRED_LOADEDHUB) or (
                m.target == "norm_reports" and m.action in RETIRED_NORM_REPORTS
            ):
                db.delete(m)
                changes.append(f"mcp row deleted: {m.target}/{m.action}")
        has_get_sales = (
            db.query(McpCapability)
            .filter(
                McpCapability.target == "loadedhub",
                McpCapability.action == "get_sales",
            )
            .first()
        )
        if not has_get_sales:
            db.add(
                McpCapability(
                    kind="connector",
                    target="loadedhub",
                    action="get_sales",
                    scopes=["mcp:reports:read"],
                    enabled=True,
                )
            )
            changes.append("mcp row added: loadedhub/get_sales")

        if dry_run:
            db.rollback()
            print("DRY RUN — would apply:")
        else:
            db.commit()
            print("Applied:")
        for line in changes or ["  (nothing to do)"]:
            print(f"  {line}")

        # ── 5. Validate: no filter/binding/MCP row may name a missing or
        # engine-only tool (run AFTER steps 2–5 of the sequence too).
        if not dry_run:
            from app.services.config_validator import validate_config

            summary = validate_config(config_db=db)
            errors = [i for i in summary["issues"] if i.get("severity") == "error"]
            if errors:
                print("\nVALIDATION ERRORS (fix before walking away):")
                for i in errors:
                    print(f"  {i['where']}: {i['problem']}")
                sys.exit(1)
            print(
                f"\nconfig validation: clean ({summary['issue_count']} non-error notes)"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
