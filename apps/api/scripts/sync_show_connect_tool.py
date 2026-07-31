"""Add the ``show_connect`` tool to the ``norm`` connector spec.

Lets an agent surface the in-conversation connect/reconnect card when a user
asks ("connect LoadedHub", "reconnect Loaded"). Mirrors how ``show_roster`` /
``show_orders`` are defined: an internal, GET, read-only ``norm`` tool whose
``display_component`` names the React card the tool loop renders its result into.

The handler lives in app code (``internal_tools._show_connect``); this only adds
the definition so the tool is offered to the model and mapped to the
``connector_connect`` component. The card itself fetches per-venue status and
drives the OAuth/credential flow.

The config DB is shared across every environment, so committing this reaches
production immediately — apply it as part of the coordinated deploy, once the
handler and the frontend component are also live. Dry-run first.

Usage:
    .venv/bin/python scripts/sync_show_connect_tool.py --dry-run
    .venv/bin/python scripts/sync_show_connect_tool.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TOOL = {
    "action": "show_connect",
    "method": "GET",
    "read_only": True,
    "display_component": "connector_connect",
    "description": (
        "Show the connect / reconnect card for an external system (a "
        "connector) so the user can authorize it. Use this when the user asks "
        "to connect or reconnect a system such as LoadedHub, or when a data "
        "tool failed because a connector's authorization has expired. Pass the "
        "connector's name in connector_name (e.g. 'loadedhub'). The card lets "
        "the user connect each venue they have access to."
    ),
    "required_fields": ["connector_name"],
    "field_schema": {
        "connector_name": {
            "type": "string",
            "description": "The connector to connect, e.g. 'loadedhub'.",
        }
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == "norm")
            .first()
        )
        if not spec:
            print("no 'norm' connector spec found — aborting")
            return

        tools = list(spec.tools or [])
        existing = next((t for t in tools if t.get("action") == "show_connect"), None)
        if existing == TOOL:
            print("show_connect already present and identical — nothing to do")
            return

        if existing:
            tools = [t for t in tools if t.get("action") != "show_connect"]
            print("updating existing show_connect entry")
        else:
            print("adding show_connect entry")
        tools.append(TOOL)

        if args.dry_run:
            print(
                f"norm spec would have {len(tools)} tools (was {len(spec.tools or [])})"
            )
            print("(dry run — nothing written)")
            return

        spec.tools = tools
        flag_modified(spec, "tools")
        db.commit()
        print("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
