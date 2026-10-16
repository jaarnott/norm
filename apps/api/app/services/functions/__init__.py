"""Norm Functions — every rule of the module lives under here.

Routers, agent tools, public pages, the importer and the mailbox sync all call
these services rather than querying ``fn_*`` tables with their own rules, so
"what stage is this enquiry in" or "who may approve this minimum spend" has one
answer. Conventions:

* Every call takes a ``FunctionsCtx`` (``ctx.py``) — the organisation, the
  venues the caller may touch, and the org's ``functions_mode``.
* Every write calls ``require_live(ctx)``: in shadow mode Functions is
  read-only everywhere.
* Services ``flush``; the caller commits (one request, one transaction).
* Failures are ``errors.FunctionsError`` subclasses, never HTTP exceptions.
"""

from app.db import functions_models  # noqa: F401 — complete Base.metadata
from app.services.functions.errors import (
    Conflict,
    Forbidden,
    FunctionsError,
    Invalid,
    NotFound,
    NotLive,
)

__all__ = [
    "Conflict",
    "Forbidden",
    "FunctionsError",
    "Invalid",
    "NotFound",
    "NotLive",
]
