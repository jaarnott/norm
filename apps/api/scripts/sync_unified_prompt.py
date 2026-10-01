"""One Norm prompt: retire the per-agent system prompts (Apps v3, 28 Sep 2026).

See docs/prompt-consolidation-plan.md. Every line of the eight per-agent
prompts has a destination:

  * shared rules / calendar / formatting -> the Norm prompt (base row) — writing
    it is also the SWITCH: build_tool_definitions goes unified once the base
    row carries a prompt (clear it to roll back)
  * a member's focus -> a light personality line (agent_configs.persona)
  * tool know-how -> the tool's own description (appended below)
  * App standing rules -> composition.note (catalog seed)
  * App Builder's authoring guide -> the `build_an_app` skill
  * obsolete lines (Orbit Marketing tools, shift writes, the [Tool] prefix,
    "use date formats exactly as shown") -> dropped

The per-agent prompts themselves are left untouched for one release as the
rollback. The router's stale hand-typed "Domain capabilities" list is removed
— its domain list is generated from agent descriptions + the App Map.

Idempotent; the config DB is shared by every environment. Dry-run first.

Usage:
    .venv/bin/python scripts/sync_unified_prompt.py --dry-run
    .venv/bin/python scripts/sync_unified_prompt.py --skip-router  # before deploy
    .venv/bin/python scripts/sync_unified_prompt.py                # after deploy

Everything but the router step is inert for code that predates the unified
prompt. The router step is not: older code builds the router menu from bare
agent descriptions, so removing the capability list before the App-line menu
is deployed weakens routing. Hence --skip-router until the deploy lands.
"""

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

NORM_PROMPT = """You are Norm, the operations assistant for a hospitality group — calm, quick and practical, the one who knows where everything is.
Today's date is {{today}}.

## Rules
- Only present data returned by tool calls. Never fabricate or estimate data.
- Dates: pass periods in plain English ('last week', 'yesterday', 'every Friday for 12 weeks') — tools resolve them against each venue's trading calendar. Never calculate dates yourself.
- The business calendar isn't the civil one: a business day starts at the venue's configured start time (typically 5–7am), not midnight, and a business week runs from that time on Monday to the same time the following Monday.
- For read-only tools (GET), proceed immediately. For tools that change data (POST/PUT/DELETE), describe what you plan to do — the user approves before it runs.
- When a tool returns an interactive card (a purchase order, invoice, roster, recipe or menu editor), hand it to the user and say what's waiting — their click in the card is the approval.
- Match names fuzzily — venues, suppliers, staff and items ("zeppa" = "La Zeppa").
- Prefer action over clarification. Make reasonable assumptions for read operations.
- When you need several independent reads, make all the calls together in one turn rather than one at a time.
- Each conversation sits with one of your team roles (below). Take on its voice — the rules here always apply.

## Formatting
- Use markdown tables for tabular data. Be concise.
- Use **bold** labels for confirmations.
- Show times in the venue's timezone, 12-hour (9:00am – 5:00pm)."""

PERSONAS = {
    "procurement": "In here you're the sharp-eyed buyer: you watch every dollar, keep suppliers honest and never let the walk-in run dry.",
    "executive_chef": "In here you're the exec chef: precise about recipes and costs, proud of the menu, allergic to waste.",
    "time_attendance": "In here you're the roster wrangler: fair, organised, and quick to spot a no-show or a blown-out shift.",
    "hr": "In here you're the people person: warm with candidates, thorough with records, discreet always.",
    "marketing": "In here you're the storyteller: upbeat, on-brand, and always thinking about what gets people through the door.",
    "reports": "In here you're the numbers person: clear, plain-spoken, and you always say what a figure actually means.",
    "app_builder": "In here you're the builder: curious about the job, and you turn it into something the team can use tomorrow.",
}

# Tool know-how that lived in agent prompts, now carried by the tool itself.
# (connector, action) -> (sentence to append, marker that means "already there")
TOOL_GUIDANCE = {
    ("loadedhub", "get_sales"): [
        (
            "Ask ONCE for the whole period per venue — not month by month — unless the user wants a per-month breakdown.",
            "Ask ONCE for the whole period",
        ),
        (
            "For 'top N items' use breakdown 'items', ranked by revenue unless the user asks for quantity.",
            "top N items",
        ),
    ],
    ("loadedhub", "get_labour"): [
        (
            "Ask ONCE for the whole period per venue — not month by month.",
            "Ask ONCE for the whole period",
        ),
        (
            "On attendance, flag no-shows (rostered, never clocked in), unrostered clock-ins, and hours more than 20% over roster.",
            "flag no-shows",
        ),
    ],
    ("loadedhub", "get_received_items_for_period"): [
        (
            "Ask ONCE for the whole period per venue — not month by month.",
            "Ask ONCE for the whole period",
        ),
    ],
    ("loadedhub", "get_budgets"): [
        (
            "Ask ONCE for the whole period — not month by month.",
            "Ask ONCE for the whole period",
        ),
    ],
    # get_cogs_detail_for_period carried "Split multi-month periods into
    # monthly calls." until 1 Oct 2026 — no basis: a 3-month window worked and
    # one month already overflows the result cap (consolidator review).
    ("loadedhub", "get_stock"): [
        ("Never guess an item id — look it up here first.", "Never guess an item id"),
    ],
    ("loadedhub", "manage_stock_item"): [
        (
            "Changing a unit also needs its paired ratio (units via get_stock view 'reference', kind 'units'); keep exactly one defaultForSupplier=true per supplier.",
            "paired ratio",
        ),
    ],
    ("norm", "show_roster"): [
        (
            "Shifts are edited in this editor by the user — never write shifts directly.",
            "never write shifts directly",
        ),
    ],
    ("bamboohr", "get_jobs"): [
        (
            "Hiring questions start here: find the role, then its candidates with get_applications.",
            "Hiring questions start here",
        ),
    ],
    ("bamboohr", "get_applications"): [
        (
            "Candidates for a role — find the role with get_jobs first.",
            "find the role with get_jobs first",
        ),
    ],
}

# Stale wording to correct in place.
TOOL_FIXES = {
    ("loadedhub", "get_stock"): [
        (
            "call update_stock_item with only the fields to change",
            "call manage_stock_item (op 'update') with only the fields to change",
        )
    ],
}

BUILD_SKILL_DESCRIPTION = "Build or change a custom App for the team — how Apps work, the spec, and how to write the screen and logic."


def plan(db, dry_run: bool, router: bool = True) -> list[str]:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConfig, ConnectionSpec, Playbook

    changes: list[str] = []
    rows = {r.agent_slug: r for r in db.query(AgentConfig).all()}

    # 1. the Norm prompt (the switch)
    base = rows.get("base")
    if base is None:
        changes.append("create base row with the Norm prompt")
        if not dry_run:
            db.add(
                AgentConfig(
                    agent_slug="base",
                    display_name="Norm Assistant",
                    system_prompt=NORM_PROMPT,
                )
            )
    elif (base.system_prompt or "") != NORM_PROMPT:
        changes.append("write the Norm prompt to the base row (unified mode ON)")
        if not dry_run:
            base.system_prompt = NORM_PROMPT

    # 2. personalities
    for slug, line in PERSONAS.items():
        row = rows.get(slug)
        if row is not None and (getattr(row, "persona", None) or "") != line:
            changes.append(f"persona {slug}")
            if not dry_run:
                row.persona = line

    # 3. App Builder's authoring guide becomes the build_an_app skill
    guide_src = (
        (rows.get("app_builder").system_prompt or "") if rows.get("app_builder") else ""
    )
    guide = re.sub(
        r"^.*?\n(Today's date is \{\{today\}\}\.\n)?",
        "",
        guide_src,
        count=1,
        flags=re.S,
    ).strip()
    pb = db.query(Playbook).filter(Playbook.slug == "build_an_app").first()
    if guide and pb is None:
        changes.append("create skill build_an_app from App Builder's prompt")
        if not dry_run:
            db.add(
                Playbook(
                    slug="build_an_app",
                    agent_slug="app_builder",
                    display_name="Build an App",
                    description=BUILD_SKILL_DESCRIPTION,
                    instructions=guide,
                    enabled=True,
                )
            )

    # 4. router: drop the stale hand-typed capability list
    router_row = rows.get("router") if router else None
    if router_row and "Domain capabilities:" in (router_row.system_prompt or ""):
        new = re.sub(
            r"\nDomain capabilities:\n.*?(?=\nTitle guidelines:)",
            "\n",
            router_row.system_prompt,
            flags=re.S,
        )
        if new != router_row.system_prompt:
            changes.append("router: remove the stale 'Domain capabilities' list")
            if not dry_run:
                router_row.system_prompt = new

    # 5. tool know-how onto tool descriptions
    for spec in db.query(ConnectionSpec).all():
        touched = []
        tools = [dict(t) for t in spec.tools or []]
        for t in tools:
            key = (spec.connector_name, t.get("action"))
            desc = t.get("description") or ""
            for old, new in TOOL_FIXES.get(key, []):
                if old in desc:
                    desc = desc.replace(old, new)
                    touched.append(f"{t['action']} (fix)")
            for sentence, marker in TOOL_GUIDANCE.get(key, []):
                if marker not in desc:
                    desc = (desc.rstrip() + " " + sentence).strip()
                    touched.append(t["action"])
            t["description"] = desc
        if touched:
            changes.append(
                f"tool guidance {spec.connector_name}: {', '.join(sorted(set(touched)))}"
            )
            if not dry_run:
                spec.tools = tools
                flag_modified(spec, "tools")
                spec.version = (spec.version or 0) + 1
    return changes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-router",
        action="store_true",
        help="leave the router's capability list until the code that replaces it "
        "(App lines in describe_domains) is deployed",
    )
    args = parser.parse_args()
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        changes = plan(db, args.dry_run, router=not args.skip_router)
        if not changes:
            print("unified prompt up to date")
            return
        for c in changes:
            print(f"  {c}")
        if args.dry_run:
            print("(dry run — nothing written)")
            return
        db.commit()
        print(f"committed {len(changes)} change(s)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
