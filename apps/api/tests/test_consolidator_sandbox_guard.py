"""Every canonical consolidator passes the sandbox check production runs.

``execute_function`` refuses a source with ``_reject_unsafe_source`` before it
execs it: no imports anywhere (a nested ``import`` inside a helper too), no
private or dunder attribute access (``x.__class__``). The per-consolidator
tests used to exec the code directly, so an edit that production refuses —
every call failing — passed all of them. This runs the real check over every
file the sync scripts install from config/consolidators/, then execs it under
the real namespace and checks there is a ``run`` the executor can call: it
always passes (params, call_api, log), so ``run`` must take at least three.
"""

import inspect

import pytest

from app.connectors.function_executor import _reject_unsafe_source
from tests import consolidator_harness as harness

SOURCES = sorted(p.stem for p in harness.CONSOLIDATORS.glob("*.py"))

# An empty list would make the test below pass by not running at all.
assert len(SOURCES) >= 19, f"found only {SOURCES} in {harness.CONSOLIDATORS}"


@pytest.mark.parametrize("name", SOURCES)
def test_production_accepts_and_can_call_it(name):
    code = harness.source(name)
    _reject_unsafe_source(code)  # raises ValueError naming the construct
    ns = harness.namespace()
    exec(compile(code, f"{name}.py", "exec"), ns)
    if name.endswith("_common"):
        # A shared helper an installer prepends to its consolidators
        # (xero_common.py — the sandbox allows no imports, so sharing code
        # means concatenation). Production execs it as part of each tool, so
        # it must pass the same check; it is not a tool and defines no run().
        assert "run" not in ns, f"{name}.py is a helper, not a consolidator"
        return
    run = ns.get("run")
    assert callable(run), f"{name}.py defines no run()"
    assert len(inspect.signature(run).parameters) >= 3, (
        f"{name}.py's run() can't take (params, call_api, log)"
    )
