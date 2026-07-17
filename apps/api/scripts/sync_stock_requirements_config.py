"""Sync the calculate_template_stock_requirements consolidator into the config DB.

The function_code lives in the config DB, where no test or review can see it —
the "config blind spot" (docs/architecture.md §13). The canonical source is
config/consolidators/calculate_template_stock_requirements.py, which CI execs
under the real sandbox namespace; this script copies it into the row verbatim.

Idempotent — safe to re-run; reports whether anything changed.

Usage:
    .venv/bin/python scripts/sync_stock_requirements_config.py --dry-run
    .venv/bin/python scripts/sync_stock_requirements_config.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

FUNCTION_CODE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "calculate_template_stock_requirements.py"
)

CONNECTOR = "loadedhub"
ACTION = "calculate_template_stock_requirements"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    code = FUNCTION_CODE_PATH.read_text(encoding="utf-8")
    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            sys.exit(f"No connector spec named {CONNECTOR}")

        tools = list(spec.tools or [])
        for i, tool in enumerate(tools):
            if tool.get("action") != ACTION:
                continue

            cfg = dict(tool.get("consolidator_config") or {})
            if cfg.get("function_code") == code:
                print(f"{ACTION}: already up to date ({len(code)} chars)")
                return

            cfg["function_code"] = code
            tool = dict(tool)
            tool["consolidator_config"] = cfg
            tools[i] = tool

            if args.dry_run:
                print(f"{ACTION}: WOULD update function_code ({len(code)} chars)")
                return

            spec.tools = tools
            spec.version = (spec.version or 0) + 1
            db.commit()
            print(
                f"{ACTION}: function_code updated ({len(code)} chars), "
                f"spec version -> {spec.version}"
            )
            return

        sys.exit(f"No tool named {ACTION} on the {CONNECTOR} spec")
    finally:
        db.close()


if __name__ == "__main__":
    main()
