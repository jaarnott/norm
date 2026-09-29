"""Retire transformed endpoints: move each endpoint's response_transform into
the consolidators that call it (Sep 2026 — endpoints return raw data; shaping
belongs to the tool; docs/tool-architecture-strategy.md).

Two behaviour-neutral steps, because the config DB is shared by every
environment and live:

1. ``--apply-shapes`` — copy each endpoint's transform into
   ``consolidator_config.shapes["connector.action"]`` of every consolidator
   whose code calls that endpoint. The sandbox's ``call_api`` applies a
   consolidator's own shape INSTEAD OF the endpoint's transform
   (function_executor), so outputs are identical by construction.
2. ``--strip`` — remove ``response_transform`` from the endpoints. Refused
   while any transformed endpoint has a consumer without the shape, or any
   consolidator calls ``call_api`` with an action that isn't a literal
   (it could reach a transformed endpoint this script can't see).

Also writes the canonical record, ``config/consolidators/shapes.json``
(consolidator -> endpoint -> transform), with ``--write-canonical``.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_move_transforms_to_tools.py              # dry run
    uv run python scripts/sync_move_transforms_to_tools.py --apply-shapes
    uv run python scripts/sync_move_transforms_to_tools.py --strip
"""

from __future__ import annotations

import argparse
import ast
import copy
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

API = pathlib.Path(__file__).resolve().parents[1]
CANONICAL = API / "config" / "consolidators" / "shapes.json"
BACKUP_DIR = pathlib.Path.home() / "norm-split-backups"


def _dynamic_calls(code: str, wraps: str | None = None) -> list[int]:
    """Line numbers of call_api(...) whose target this script can't see.

    Two patterns are safe and skipped: the generic fan-out helper
    ``[call_api(c, a, p) for (c, a, p) in calls]`` (its targets are literal
    tuples elsewhere in the code, which calls_in_code reads), and the
    ``for_period`` wrapper's ``call_api("loadedhub", wraps, …)`` when the row
    declares ``wraps``.
    """
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return []
    fanout_calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ListComp) and isinstance(node.elt, ast.Call):
            gen = node.generators[0]
            if isinstance(gen.target, ast.Tuple) and isinstance(gen.iter, ast.Name):
                names = {e.id for e in gen.target.elts if isinstance(e, ast.Name)}
                args = node.elt.args[:2]
                if all(isinstance(a, ast.Name) and a.id in names for a in args):
                    fanout_calls.add(id(node.elt))
    lines = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "call_api"
            and len(node.args) >= 2
        ):
            continue
        if all(
            isinstance(a, ast.Constant) and isinstance(a.value, str)
            for a in node.args[:2]
        ):
            continue
        if id(node) in fanout_calls:
            continue
        a1 = node.args[1]
        if wraps and isinstance(a1, ast.Name) and a1.id == "wraps":
            continue
        lines.append(node.lineno)
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--apply-shapes", action="store_true")
    ap.add_argument("--strip", action="store_true")
    ap.add_argument("--write-canonical", action="store_true")
    args = ap.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal
    from app.services.spec_inventory import calls_in_code

    import app.agents.internal_tools  # noqa: F401 — registers built-ins

    db = _ConfigSessionLocal()
    try:
        specs = (
            db.query(ConnectionSpec)
            .order_by(ConnectionSpec.connector_name)
            .with_for_update()
            .all()
        )
        connectors = {s.connector_name for s in specs}

        transforms: dict[str, dict] = {}
        for s in specs:
            for r in spec_rows.endpoints(s):
                rt = r.get("response_transform")
                if isinstance(rt, dict) and rt:
                    transforms[f"{s.connector_name}.{r['action']}"] = rt

        consumers: dict[str, list[str]] = {k: [] for k in transforms}
        planned: dict[
            str, dict[str, dict]
        ] = {}  # tool key -> {endpoint key: transform}
        dynamic: dict[str, list[int]] = {}
        tool_rows: dict[str, tuple] = {}
        for s in specs:
            for r in spec_rows.tools(s):
                if not spec_rows.is_consolidator(r):
                    continue
                cfg = r["consolidator_config"]
                key = f"{s.connector_name}.{r['action']}"
                tool_rows[key] = (s, r)
                refs = {
                    f"{c}.{a}"
                    for c, a in calls_in_code(
                        cfg.get("function_code") or "", connectors
                    )
                }
                if cfg.get("wraps"):
                    refs.add(f"{s.connector_name}.{cfg['wraps']}")
                dyn = _dynamic_calls(cfg.get("function_code") or "", cfg.get("wraps"))
                if dyn:
                    dynamic[key] = dyn
                for ref in sorted(refs):
                    if ref in transforms and transforms[ref].get("enabled"):
                        planned.setdefault(key, {})[ref] = transforms[ref]
                        consumers[ref].append(key)

        print("Transformed endpoints and the consolidators that call them:")
        for ep, rt in sorted(transforms.items()):
            state = "enabled" if rt.get("enabled") else "NOT enabled (never ran)"
            print(
                f"  {ep:48s} {state:24s} <- {', '.join(consumers[ep]) or '(no consolidator)'}"
            )
        if dynamic:
            print(
                "Consolidators calling call_api with a non-literal action (check by hand):"
            )
            for k, lines in sorted(dynamic.items()):
                print(f"  {k}: lines {lines}")

        missing = []
        for tool_key, wanted in sorted(planned.items()):
            s, r = tool_rows[tool_key]
            have = r["consolidator_config"].get("shapes") or {}
            todo = {ep: rt for ep, rt in wanted.items() if have.get(ep) != rt}
            if todo:
                missing.append(tool_key)
                print(
                    f"{tool_key}: {'adds' if args.apply_shapes else 'needs'} shapes for {', '.join(sorted(todo))}"
                )
                if args.apply_shapes:
                    cfg = dict(r["consolidator_config"])
                    shapes = dict(cfg.get("shapes") or {})
                    shapes.update({ep: copy.deepcopy(rt) for ep, rt in todo.items()})
                    cfg["shapes"] = shapes
                    r["consolidator_config"] = cfg
                    flag_modified(s, "tools")
                    s.version = (s.version or 0) + 1

        if args.write_canonical:
            record = {k: dict(sorted(v.items())) for k, v in sorted(planned.items())}
            CANONICAL.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
            print(f"wrote {CANONICAL.relative_to(API)}")

        if args.apply_shapes:
            db.commit()
            print("shapes applied")
            missing = []

        if args.strip:
            blockers = []
            if missing:
                blockers.append(
                    "consolidators still missing shapes: " + ", ".join(missing)
                )
            for k, lines in dynamic.items():
                blockers.append(
                    f"{k} calls call_api with a non-literal action (lines {lines})"
                )
            if blockers:
                raise SystemExit("refusing to strip:\n  " + "\n  ".join(blockers))
            BACKUP_DIR.mkdir(exist_ok=True)
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = BACKUP_DIR / f"transforms-{stamp}.json"
            backup.write_text(json.dumps(transforms, indent=1))
            stripped = 0
            for s in specs:
                changed = False
                for r in spec_rows.endpoints(s):
                    if "response_transform" in r:
                        r.pop("response_transform")
                        changed = True
                        stripped += 1
                if changed:
                    flag_modified(s, "endpoints")
                    s.version = (s.version or 0) + 1
            db.commit()
            print(
                f"stripped response_transform from {stripped} endpoint(s); backup {backup}"
            )
        elif not args.apply_shapes:
            db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
