"""The one harness the consolidator tests share.

Every consolidator test execs the canonical code from config/consolidators/
under the REAL sandbox namespace — that is what makes them worth having
(CLAUDE.md: they stand between a config edit and a sandbox failure in
production). Each test file used to copy the same five lines to do it; this
module is the single copy, so the namespace and the calling convention can't
drift from app/connectors/function_executor.py in one file and not another.

- ``source(name)`` is the text of config/consolidators/<name>.py.
- ``call(code, params, call_api, ...)`` refuses the source with the
  production ``_reject_unsafe_source`` check, execs it in a fresh namespace
  built the way ``execute_function`` builds it, and calls ``run`` widened by
  its arity exactly as the executor does: (params, call_api, log), then
  call_api_parallel, then options.
- ``run(code, api, **params)`` is ``call`` for the usual scripted fake — an
  object with ``call_api`` and, when the code wants them, ``log`` and
  ``call_api_parallel``.

The guard and the compile happen once per source per test session; each call
still gets a fresh namespace, as each production call does.
"""

from __future__ import annotations

import functools
import inspect
import pathlib
from typing import Any

from app.connectors.function_executor import (
    _SAFE_BUILTINS,
    _SAFE_MODULES,
    _reject_unsafe_source,
)

CONSOLIDATORS = (
    pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
)

#: source text -> its file, so a traceback points at the real line.
_FILES: dict[str, str] = {}


def source(name: str) -> str:
    """The canonical source of config/consolidators/<name>.py."""
    path = CONSOLIDATORS / f"{name}.py"
    code = path.read_text(encoding="utf-8")
    _FILES.setdefault(code, str(path))
    return code


@functools.cache
def compiled(code: str):
    """The production guard, then the compile — once per distinct source."""
    _reject_unsafe_source(code)
    return compile(code, _FILES.get(code, "<consolidator>"), "exec")


def namespace(**extra: Any) -> dict[str, Any]:
    """A fresh sandbox namespace, as execute_function builds it.

    ``extra`` adds the globals a caller's door supplies in production
    (``extract_document``, ``store`` ...).
    """
    return {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES, **extra}


def _quiet(_message: str) -> None:
    return None


def call(
    code: str,
    params: dict,
    call_api,
    log=None,
    call_api_parallel=None,
    options=None,
    **extra: Any,
):
    """Exec ``code`` and call its ``run`` the way execute_function does."""
    ns = namespace(**extra)
    exec(compiled(code), ns)
    fn = ns["run"]
    arity = len(inspect.signature(fn).parameters)
    log = log or _quiet
    if arity >= 5:
        return fn(params, call_api, log, call_api_parallel, options)
    if arity >= 4:
        return fn(params, call_api, log, call_api_parallel)
    return fn(params, call_api, log)


def run(code: str, api, /, **params):
    """``call`` with the doors taken from a scripted fake."""
    return call(
        code,
        params,
        api.call_api,
        log=getattr(api, "log", None),
        call_api_parallel=getattr(api, "call_api_parallel", None),
    )
