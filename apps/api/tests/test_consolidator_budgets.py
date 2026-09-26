"""Every installer must give a consolidator at least the budget it states.

Each canonical file in config/consolidators/ documents what it needs:

    # Requires consolidator_config: {"max_api_calls": 5}

but the number that actually reaches production is whatever the installer
script writes, and nothing compared the two. get_stock_items said 5 while
its installer wrote 3; a name query makes 4 calls, so 100 of 194 production
calls failed with "Too many API calls (max 3)", one thread retrying 73 times
(27 Aug 2026). Every test of the consolidator passed throughout, because the
tests run the code, not the config row the code runs under.

This reads the requirement from each file header and every
`"max_api_calls"` an installer declares for that action (parsed, not
imported — importing sync scripts would touch the config DB), and fails if
any installer would write less. It also fails if an installer declares a
budget only in a form this cannot see, so the guard can't be dodged by
accident.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

API = pathlib.Path(__file__).resolve().parent.parent
CONSOLIDATORS = API / "config" / "consolidators"
SCRIPTS = API / "scripts"

# A consolidator whose canonical file is named for a SIBLING tool (mirrors
# consolidator_coverage._SHARED_CANONICAL).
SHARED_FILE = {"receive_loadedhub_invoice": "review_and_receive_invoices"}

_BUDGET = re.compile(r'"max_api_calls":\s*(\d+)')


def _required() -> dict[str, int]:
    """file stem → budget its header documents.

    The marker line may carry the dict itself or be followed by it on the
    next comment lines (reconcile's header spans three), so look at the
    marker plus the few lines after it.
    """
    out: dict[str, int] = {}
    for f in sorted(CONSOLIDATORS.glob("*.py")):
        lines = f.read_text().splitlines()
        for i, line in enumerate(lines[:80]):
            if "Requires consolidator_config" not in line:
                continue
            m = _BUDGET.search(" ".join(lines[i : i + 4]))
            if m:
                out[f.stem] = int(m.group(1))
            break
    return out


def _file_for(action: str) -> str:
    if action in SHARED_FILE:
        return SHARED_FILE[action]
    return (
        action[4:]
        if action.startswith("get_") and not (CONSOLIDATORS / f"{action}.py").exists()
        else action
    )


def _declared() -> list[tuple[str, str, int, int]]:
    """(script, action, budget, line) for every dict literal carrying an
    "action" and a consolidator_config.max_api_calls."""
    found = []
    for script in sorted(SCRIPTS.glob("sync_*.py")):
        tree = ast.parse(script.read_text())
        # `"action": ACTION` is common — resolve module-level string constants.
        consts = {
            t.id: n.value.value
            for n in tree.body
            if isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
            for t in n.targets
            if isinstance(t, ast.Name)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {
                k.value: v
                for k, v in zip(node.keys, node.values)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            action_node = keys.get("action")
            cfg = keys.get("consolidator_config")
            if isinstance(action_node, ast.Constant) and isinstance(
                action_node.value, str
            ):
                action = action_node.value
            elif isinstance(action_node, ast.Name) and action_node.id in consts:
                action = consts[action_node.id]
            else:
                continue
            if not isinstance(cfg, ast.Dict):
                continue
            for k, v in zip(cfg.keys, cfg.values):
                if (
                    isinstance(k, ast.Constant)
                    and k.value == "max_api_calls"
                    and isinstance(v, ast.Constant)
                    and isinstance(v.value, int)
                ):
                    found.append((script.name, action, v.value, v.lineno))
    return found


REQUIRED = _required()
DECLARED = _declared()


def test_the_header_parser_still_finds_the_requirements():
    """An empty set would pass every assertion below vacuously."""
    assert len(REQUIRED) >= 8
    assert REQUIRED.get("get_stock") == 8
    assert REQUIRED.get("reconcile_received_invoices") == 120  # multi-line header


@pytest.mark.parametrize("stem", sorted(REQUIRED))
def test_some_installer_declares_a_budget_this_guard_can_see(stem):
    declared = [d for d in DECLARED if _file_for(d[1]) == stem]
    assert declared, (
        f"{stem}.py states max_api_calls={REQUIRED[stem]} but no sync script "
        "declares one as a literal in its tool dict, so nothing checks that "
        "production gets it. Declare it in the installer's consolidator_config."
    )


@pytest.mark.parametrize(
    "script,action,budget,line",
    [d for d in DECLARED if _file_for(d[1]) in REQUIRED],
    ids=lambda v: str(v),
)
def test_no_installer_writes_less_than_the_file_needs(script, action, budget, line):
    need = REQUIRED[_file_for(action)]
    assert budget >= need, (
        f"scripts/{script}:{line} installs {action} with max_api_calls={budget}, "
        f"but config/consolidators/{_file_for(action)}.py needs {need}. Every "
        "call past the budget fails in production with 'Too many API calls'."
    )
