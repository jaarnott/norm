"""Draft suggested prompts for the Home screen with Haiku, for a person to review.

Reads each team member's Apps, tools and skills from the config DB, asks Haiku
for short prompts that only use what those tools can do, and writes the drafts
to a JSON file. Nothing is saved anywhere else: the reviewed prompts go into
app/services/suggestions/catalog.py by hand.

One Haiku call per team member (about seven calls, a few cents). The key is the
shared production one (system_secrets), so keep runs deliberate.

    uv run python scripts/draft_suggestion_prompts.py --out /tmp/drafts.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MEMBERS = {
    "reports": "Reports — sales, labour, budgets and cost of goods reporting",
    "procurement": "Procurement — stock, orders, supplier invoices and stocktakes",
    "time_attendance": "Time & Attendance — rosters and timesheets",
    "executive_chef": "Executive Chef — recipes, menus and margins",
    "hr": "HR — hiring, training, performance and staff records",
    "marketing": "Marketing — email campaigns and social media",
    "app_builder": "App Builder — builds small apps for the venue by description",
}

PROMPT = """You write the suggested prompts that appear under the message box of Norm,
an AI operations manager for hospitality venues (pubs, bars, restaurants, cafes in
New Zealand and Australia). A manager clicks one and it is sent to Norm as their message.

Team member: {member}

What this team member can actually do (its tools and skills — never suggest anything
outside these):
{capabilities}

Write 14 prompts. Rules:
- Each is something a busy venue manager or owner would genuinely want to ask, and that
  the tools above can answer or do. Favour questions that surface money: sales against
  budget, cost of goods, wages, supplier prices, waste, margins.
- First person, plain words, the way a hospo manager talks. No jargon, no emoji.
- At most 70 characters. Use {{venue}} where the venue's name belongs.
- Mix: most are regular questions; 3-4 help someone discover a capability they may not
  know about.
- For each, say when it is most useful: "any", or days of the week (mon..sun), and
  optionally a part of the day (morning | afternoon | evening).

Answer with JSON only:
{{"prompts": [{{"text": "...", "kind": "regular"|"discover", "days": ["mon"] or "any",
"part_of_day": "morning"|"afternoon"|"evening"|null, "why": "one short line"}}]}}"""


def _capabilities(config_db) -> dict[str, str]:
    from app.db.config_models import ConnectionSpec, MarketplaceApp, Playbook

    apps = (
        config_db.query(MarketplaceApp)
        .filter(MarketplaceApp.status == "active", MarketplaceApp.tier == "app")
        .all()
    )
    tool_desc: dict[str, str] = {}
    for spec in config_db.query(ConnectionSpec).all():
        for t in spec.tools or []:
            key = f"{spec.connector_name}.{t.get('action')}"
            first = (t.get("description") or "").split(". ")[0]
            tool_desc[key] = first[:240]
    skills = {p.slug: p for p in config_db.query(Playbook).all()}

    out: dict[str, list[str]] = {m: [] for m in MEMBERS}
    for app in apps:
        comp = app.composition or {}
        member = comp.get("member")
        if member not in out:
            continue
        out[member].append(f"App {app.name}: {app.description or ''}".strip())
        for key in comp.get("tools") or []:
            if key in tool_desc:
                out[member].append(f"  tool {key}: {tool_desc[key]}")
        for slug in comp.get("skills") or []:
            pb = skills.get(slug)
            if pb:
                out[member].append(
                    f"  skill {pb.display_name}: {pb.description or ''}"[:300]
                )
    return {m: "\n".join(lines) for m, lines in out.items() if lines}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--member", action="append", help="limit to these members")
    args = ap.parse_args()

    import anthropic

    from app.config import settings
    from app.db.engine import _ConfigSessionLocal
    from app.main import _load_system_secrets
    from app.services.billing_service import FALLBACK_MODEL

    _load_system_secrets()
    config_db = _ConfigSessionLocal()
    try:
        caps = _capabilities(config_db)
    finally:
        config_db.close()

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    drafts: dict[str, list] = {}
    tokens_in = tokens_out = 0
    for member, label in MEMBERS.items():
        if args.member and member not in args.member:
            continue
        if member not in caps:
            print(f"skip {member}: no tools or skills in the catalogue")
            continue
        msg = client.messages.create(
            model=FALLBACK_MODEL,
            max_tokens=3000,
            messages=[
                {
                    "role": "user",
                    "content": PROMPT.format(member=label, capabilities=caps[member]),
                }
            ],
        )
        tokens_in += msg.usage.input_tokens
        tokens_out += msg.usage.output_tokens
        raw = msg.content[0].text
        start, end = raw.find("{"), raw.rfind("}")
        try:
            drafts[member] = json.loads(raw[start : end + 1])["prompts"]
        except (ValueError, KeyError):
            drafts[member] = [{"unparsed": raw}]
        print(f"{member}: {len(drafts[member])} prompts")

    Path(args.out).write_text(
        json.dumps({"capabilities": caps, "drafts": drafts}, indent=2)
    )
    # Haiku 4.5: $1 / M input, $5 / M output.
    cost = tokens_in / 1e6 * 1 + tokens_out / 1e6 * 5
    print(f"tokens in {tokens_in}, out {tokens_out}, about ${cost:.3f}")


if __name__ == "__main__":
    main()
