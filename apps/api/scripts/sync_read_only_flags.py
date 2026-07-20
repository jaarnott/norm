"""Mark which connector actions are genuinely read-only.

Norm uses a tool's HTTP method to decide whether it needs **approval**
(`_is_read_only(method)` -> `method == "GET"`, tool_loop.py). That is not the
same question as whether the tool is **safe**, and seven GET tools mutate real
state:

    norm_email  send_report_email      norm  create_purchase_order
    norm        create_automated_task  norm  set_workflow_mode
    norm        update_task_config     norm  set_override
    norm        update_thread_summary

A delegated sub-agent is supposed to be read-only. If "read-only" were inferred
from the method, a consulted agent could email a report or raise a purchase
order. So it is declared explicitly instead, on the tool definition:

    read_only: true

**Absent means false** — the delegation filter fails closed, so a new action is
non-consultable until someone says otherwise. That is the safe direction: the
cost of a missing flag is "the sub-agent couldn't answer", not "the sub-agent
spent money".

This script sets the flag from the method plus a mutating-verb check, and never
sets it true for anything on DENY. Read-only; changes no behaviour on its own.

Usage:
    .venv/bin/python scripts/sync_read_only_flags.py --dry-run
    .venv/bin/python scripts/sync_read_only_flags.py --show      # full classification
    .venv/bin/python scripts/sync_read_only_flags.py
"""

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# GET-method actions that nonetheless change state. Belt and braces: the verb
# check below would already catch most of these, but naming them means a future
# rename can't silently reclassify a spending action as safe.
#
# The last two are consolidators: method GET, read-sounding names, and their
# sandboxed code calls real writes (receive_invoice; create/update_supplier_
# statement). They are also caught structurally by _consolidator_writes below;
# listed here so the intent survives a rewrite of that detection.
DENY = {
    ("norm_email", "send_report_email"),
    ("norm", "create_purchase_order"),
    ("norm", "create_automated_task"),
    ("norm", "update_automated_task"),
    ("norm", "run_automated_task"),
    ("norm", "set_workflow_mode"),
    ("norm", "update_task_config"),
    ("norm", "set_override"),
    ("norm", "update_thread_summary"),
    ("loadedhub", "review_and_receive_invoices"),
    ("loadedhub", "reconcile_received_invoices"),
}

# Verbs that mean "this changes something", regardless of HTTP method.
MUTATING_PREFIXES = (
    "create_",
    "update_",
    "delete_",
    "set_",
    "save_",
    "add_",
    "remove_",
    "publish_",
    "send_",
    "run_",
    "trigger_",
    "approve_",
    "reject_",
    "receive_",
    "submit_",
    "cancel_",
    "assign_",
    "import_",
    "sync_",
)

# Actions that read but whose name starts with a mutating verb by coincidence.
# None found today; kept so the exception has an obvious home.
ALLOW: set[tuple[str, str]] = set()


def _consolidator_writes(tool: dict) -> list[str]:
    """Actions a consolidator's sandboxed code calls that are not themselves reads.

    A consolidator declares method GET because it needs no approval to *run*,
    but its `function_code` may `call_api(connector, action, ...)` anything —
    including writes. `review_and_receive_invoices` receives invoices;
    `reconcile_received_invoices` creates supplier statements. Classifying
    those by their own method or name would call both read-only.

    Matches the literal action name in any call_api / call_api_parallel
    argument, which is how the sandbox names tools
    (function_executor.call_api, line 358).
    """
    cfg = tool.get("consolidator_config") or {}
    code = cfg.get("function_code") or ""
    if not code:
        return []
    found = set()
    for name in re.findall(r"""['"]([a-z][a-z0-9_]{3,})['"]""", code):
        if name.startswith(MUTATING_PREFIXES):
            found.add(name)
    return sorted(found)


def classify(
    connector: str, action: str, method: str, tool: dict | None = None
) -> bool:
    """True when this action only reads. Errs toward False."""
    if (connector, action) in DENY:
        return False
    if (connector, action) in ALLOW:
        return True
    if (method or "GET").upper() != "GET":
        return False
    if action.startswith(MUTATING_PREFIXES):
        return False
    # A read-named consolidator that calls a write is not a read.
    return not _consolidator_writes(tool or {})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--show", action="store_true", help="print every tool")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        changed = 0
        n_ro = 0
        n_rw = 0
        for spec in db.query(ConnectorSpec).order_by(ConnectorSpec.connector_name):
            dirty = False
            for tool in spec.tools or []:
                action = tool.get("action") or ""
                method = (tool.get("method") or "GET").upper()
                ro = classify(spec.connector_name, action, method, tool)
                n_ro += ro
                n_rw += not ro
                if args.show:
                    mark = "READ " if ro else "     "
                    print(f"  {mark} {spec.connector_name:16} {action:34} {method}")
                elif ro is False and method == "GET":
                    # The interesting cases: GET but not safe.
                    why = _consolidator_writes(tool)
                    detail = f"  calls {', '.join(why[:4])}" if why else ""
                    print(f"  MUTATES (GET) {spec.connector_name:16} {action}{detail}")
                if tool.get("read_only") != ro:
                    tool["read_only"] = ro
                    dirty = True
                    changed += 1
            if dirty and not args.dry_run:
                flag_modified(spec, "tools")

        print(
            f"\nread_only=True: {n_ro}   read_only=False: {n_rw}   updated: {changed}"
        )
        if args.dry_run:
            print("(dry run — nothing written)")
            return
        db.commit()
        print("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
