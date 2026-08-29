"""Mark the Cook Brothers App's read actions `read_only`.

Why this exists: the CB App is an **mcp-mode** connector, and MCP carries every
call as POST. Spec discovery infers GET only from a leading `get_`
(`connectors/mcp_executor.convert_mcp_tools_to_spec`), which never fires on
domain-prefixed names like `training_get_job_opening` — so all 114 actions land
as POST. The app platform's door treats a non-GET as a write
(`services/app_runtime`), which would force an app that merely LISTS job
openings through the write-approval gate.

`read_only: true` is the explicit signal the door honours for mcp-mode
connectors. Nothing else changes: `delegation.is_read_only_tool` requires the
flag AND a GET method, so flagging these POST actions does not make any of them
consultable by a sub-agent.

What counts as a read here is deliberately conservative — the action name must
carry a `list_`/`get_` segment, and anything whose name suggests a mutation is
left alone regardless.

    uv run python scripts/sync_cb_app_read_only_flags.py --dry-run
    uv run python scripts/sync_cb_app_read_only_flags.py

The config DB is shared across every environment, so committing this reaches
production immediately. Dry-run first. Idempotent — safe to re-run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CONNECTOR = "cook_brothers_app"

#: A name that contains any of these segments only fetches.
_READ_SEGMENTS = ("_list_", "_get_", "list_", "get_")

#: …unless it also contains one of these, which always means a mutation. Belt
#: and braces: a future action called `get_or_create_x` must not slip through.
_WRITE_SEGMENTS = (
    "create",
    "update",
    "delete",
    "add",
    "remove",
    "set_",
    "move",
    "mark",
    "sign_off",
    "send",
    "approve",
    "trigger",
    "log_",
    "complete",
)


def is_read(action: str) -> bool:
    name = str(action or "")
    if any(seg in name for seg in _WRITE_SEGMENTS):
        return False
    return any(seg in name for seg in _READ_SEGMENTS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            print(f"{CONNECTOR} spec not found")
            return 1
        if (spec.execution_mode or "") != "mcp":
            # The door only honours the flag for mcp-mode connectors, so
            # setting it anywhere else would be a no-op that reads as a grant.
            print(f"{CONNECTOR} is {spec.execution_mode!r}, not mcp — refusing")
            return 1

        tools = list(spec.tools or [])
        changed, already, skipped = [], [], []
        for i, tool in enumerate(tools):
            if not isinstance(tool, dict):
                continue
            action = tool.get("action")
            if not is_read(action):
                skipped.append(action)
                continue
            if tool.get("read_only") is True:
                already.append(action)
                continue
            tools[i] = {**tool, "read_only": True}
            changed.append(action)

        print(f"{CONNECTOR}: {len(tools)} actions")
        print(f"  reads to flag : {len(changed)}")
        print(f"  already flagged: {len(already)}")
        print(f"  left as writes : {len(skipped)}")
        for a in sorted(skipped):
            print(f"      write: {a}")

        if not changed:
            print("already up to date")
            return 0
        if args.dry_run:
            print("\n--dry-run: nothing written")
            return 0

        spec.tools = tools
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(f"\nflagged {len(changed)} read actions (spec v{spec.version})")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
