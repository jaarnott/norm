"""Install approvals stage 4 in config: one tool to change a preference.

Approvals stage 4 (Oct 2026, plan: ~/.claude/plans/structured-launching-cook.md).
What Norm may do without asking is now each person's own setting
(``User.approval_preferences``, app/services/approvals.py). In config:

- ``norm.set_workflow_mode`` becomes ``norm.set_approval_preference``: one tool
  for any write ("you don't need to ask me before updating stock items"),
  and for the invoice tools' levels and switches. Always asks.
- Its App claim moves from Loaded Stock to Norm Core — it is about every
  write now, not just invoices.
- The two invoice playbooks stop telling the model to wait for a run mode
  ("unset" no longer exists: a level defaults to the one that writes
  nothing) and point at Settings → Preferences / set_approval_preference
  instead of the venue setting and set_workflow_mode.
- ``review_and_receive_invoices`` reports the person's setting as the source
  of its mode, not the venue's.
- Agent bindings that named ``set_workflow_mode`` name the new tool, switched
  on or off as before (added after the first run, 6 Oct 2026: validate_config
  flagged procurement's).

**Run AFTER the stage-4 code is deployed.** Code that predates it has no
handler for set_approval_preference, and still reads the venue's receiving
setting the reworded playbook no longer mentions.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_approval_preferences.py --dry-run
    uv run python scripts/sync_approval_preferences.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.sync_approval_labels import WRITES  # noqa: E402
from scripts.tool_row_updates import (  # noqa: E402
    BACKUP_DIR,
    RowUpdate,
    apply_row_updates,
    code_of,
)

OLD = ("norm", "set_workflow_mode")
NEW_ACTION = "set_approval_preference"
NEW_KEY = f"norm.{NEW_ACTION}"
FROM_APP, TO_APP = "loaded-stock", "norm-core"

NEW_ROW = {
    "action": NEW_ACTION,
    "method": "POST",
    "read_only": False,
    "description": (
        "Change what Norm may do without asking this person, for one write "
        "tool. Use when they say Norm needn't ask before something (\"you "
        "don't need to ask me before updating stock items\"), want to be asked "
        "again, or want receiving or reconciling invoices to go further or "
        "less far. The change is shown to them to approve; it only ever "
        "applies to them."
    ),
    "required_fields": ["tool"],
    "optional_fields": ["setting", "options"],
    "field_descriptions": {
        "tool": (
            "The write tool — its key (e.g. 'loadedhub.manage_stock_item') or "
            "its name ('update stock items')."
        ),
        "setting": (
            "'ask' or 'always' for an ordinary write. For receiving or "
            "reconciling invoices, the level: approve_all, approve_fixes or "
            "autopilot."
        ),
        "options": (
            "Receiving only: switches to turn on (true) or off (false) at "
            'autopilot, e.g. {"auto_create_units": true}.'
        ),
    },
    "field_schema": {
        "options": {"type": "object", "additionalProperties": {"type": "boolean"}}
    },
    "effect": "write",
    "approval": WRITES[NEW_KEY],
}

RECONCILE_OLD_HEAD = "RUN MODE — DO THIS FIRST, before reconciling."
RECONCILE_NEW = (
    "LEVEL — how far Norm goes is the person's own setting, already in your "
    'context under "What this person lets you do without asking": **Ask me** '
    "(nothing is written — the report is for review), **Tick the clean ones** "
    "(exact matches are marked reconciled; creating a missing statement still "
    "needs their OK) or **Autopilot** (also creates missing statements). Run "
    "the tool straight away — never wait on or ask for a level, and never pass "
    "a mode. If they want Norm to do more or less, they change it in Settings "
    "→ Preferences, or ask you: call set_approval_preference with tool "
    '"loadedhub.reconcile_received_invoices" and the level (approve_all, '
    "approve_fixes, autopilot) — they approve the change on a card.\n\n"
)
RECEIVE_OLD_HEAD = "RUN MODE — receiving runs at the VENUE's setting"
RECEIVE_NEW = (
    "LEVEL — how far Norm goes is the person's own setting, already in your "
    'context under "What this person lets you do without asking":\n'
    "   • **Ask me** — Norm changes nothing without their OK (everything is "
    "presented on cards to approve);\n"
    "   • **Receive clean invoices** — Norm receives the exact matches; "
    "anything needing a fix waits on a card;\n"
    "   • **Autopilot** — Norm applies every suggested change from its own "
    "reading of the invoice copy (each recorded on the card) and receives "
    "every invoice with no blocking issues, within the switches they have "
    "turned on; anything it can't be confident about still waits for them.\n"
    "   Run the tool straight away — never wait on or ask for a level. If they "
    "want it changed, they do it in Settings → Preferences, or ask you: call "
    'set_approval_preference with tool "loadedhub.review_and_receive_invoices", '
    "the level (approve_all, approve_fixes, autopilot) and, for a switch, "
    "`options` — they approve the change on a card.\n\n"
)

#: (playbook slug, start of the old section, the text after it, replacement,
#:  other phrases to reword).
PLAYBOOKS = [
    (
        "reconcile_received_invoices",
        RECONCILE_OLD_HEAD,
        "1. Call reconcile_received_invoices",
        RECONCILE_NEW,
        [
            ("AND the user's run mode", "AND the person's level"),
            ("the run mode alone governs", "the level alone governs"),
            ("(the run mode is approve all)", "(their level is Ask me)"),
        ],
    ),
    (
        "receive_loadedhub_invoices",
        RECEIVE_OLD_HEAD,
        "1. Call review_and_receive_invoices",
        RECEIVE_NEW,
        [
            ("AND the user's run mode", "AND the person's level"),
            ("the run mode alone governs", "the level alone governs"),
        ],
    ),
]


def updates() -> list[RowUpdate]:
    return [
        RowUpdate(
            "loadedhub",
            "review_and_receive_invoices",
            code=code_of("review_and_receive_invoices.py"),
        )
    ]


def reword(text: str, head: str, until: str, new: str, others) -> str:
    """The playbook with its mode section replaced; unchanged if already done."""
    if head in text:
        start = text.index(head)
        end = text.index(until, start)
        text = text[:start] + new + text[end:]
    for old, replacement in others:
        text = text.replace(old, replacement)
    return text


def also(db, dry_run: bool) -> list[str]:
    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import (
        AgentConnectionBinding,
        ConnectionSpec,
        MarketplaceApp,
        Playbook,
    )

    lines = []
    norm = (
        db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == "norm")
        .with_for_update()
        .one()
    )
    if spec_rows.find_tool(norm, NEW_ACTION) is None:
        lines.append(f"{NEW_KEY}: add tool row")
        if not dry_run:
            norm.tools = [*(norm.tools or []), NEW_ROW]
            flag_modified(norm, "tools")
            norm.version = (norm.version or 0) + 1

    apps = {
        a.slug: a
        for a in db.query(MarketplaceApp)
        .filter(MarketplaceApp.slug.in_([FROM_APP, TO_APP]))
        .with_for_update()
        .all()
    }
    old_key = f"{OLD[0]}.{OLD[1]}"
    for slug, drop, add in ((FROM_APP, old_key, None), (TO_APP, None, NEW_KEY)):
        app = apps[slug]
        comp = dict(app.composition or {})
        tools = list(comp.get("tools") or [])
        changed = False
        if drop and drop in tools:
            tools.remove(drop)
            changed = True
            lines.append(f"app {slug}: unclaim {drop}")
        if add and add not in tools:
            tools.append(add)
            changed = True
            lines.append(f"app {slug}: claim {add}")
        if changed and not dry_run:
            comp["tools"] = tools
            app.composition = comp
            flag_modified(app, "composition")

    backup = {
        "apps": {slug: app.composition for slug, app in apps.items()},
        "playbooks": {},
        "bindings": {},
    }

    # Agent bindings that list the old tool by name: the same capability under
    # its new name, switched on or off as it was. validate_config flags a
    # binding naming a tool that no longer exists — the agent silently loses
    # it (procurement had set_workflow_mode switched on).
    for binding in (
        db.query(AgentConnectionBinding)
        .filter(AgentConnectionBinding.connector_name == "norm")
        .with_for_update()
        .all()
    ):
        caps = list(binding.capabilities or [])
        renamed = [
            {
                **c,
                "action": NEW_ACTION,
                "label": NEW_ROW["description"],
            }
            if isinstance(c, dict) and c.get("action") == OLD[1]
            else c
            for c in caps
        ]
        if renamed != caps:
            lines.append(f"binding {binding.agent_slug}.norm: {OLD[1]} → {NEW_ACTION}")
            backup["bindings"][binding.agent_slug] = caps
            if not dry_run:
                binding.capabilities = renamed
                flag_modified(binding, "capabilities")
    for slug, head, until, new, others in PLAYBOOKS:
        pb = db.query(Playbook).filter(Playbook.slug == slug).with_for_update().one()
        text = reword(pb.instructions or "", head, until, new, others)
        if text != pb.instructions:
            lines.append(f"playbook {slug}: reword the run-mode section")
            backup["playbooks"][slug] = pb.instructions
            if not dry_run:
                pb.instructions = text
    if lines and not dry_run:
        # The tool rows are backed up by apply_row_updates; these are not.
        import datetime as dt
        import json

        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = BACKUP_DIR / f"approval-preferences-config-{stamp}.json"
        path.write_text(json.dumps(backup, indent=1, default=str))
        print(f"backup {path}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    apply_row_updates(
        updates(),
        dry_run=args.dry_run,
        label="approval-preferences",
        delete=(OLD,),
        also=also,
    )


if __name__ == "__main__":
    main()
