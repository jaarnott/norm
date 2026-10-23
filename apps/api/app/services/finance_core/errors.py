"""The Finance domain errors, and the one translation from database errors.

The same shapes as ``functions/errors.py`` (a stable ``code``, a message for
people, ``status_code`` for the routers' one handler), kept separate so the
Finance modules never import Functions' event-bound code. Services raise
these; routers map them to HTTP with ``http_exception``; agent tools turn
them into a refusal the model can read.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError


class FinanceError(Exception):
    """Base class. ``code`` is stable and machine-readable; the message is for
    people."""

    status_code = 400
    code = "finance_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(FinanceError):
    """Missing — or outside the caller's organisation or venues. The two are
    deliberately indistinguishable."""

    status_code = 404
    code = "not_found"


class Forbidden(FinanceError):
    """The caller may not do this: a scope they lack, a venue they cannot
    see, a performer's link used for another performer."""

    status_code = 403
    code = "forbidden"


class Conflict(FinanceError):
    """Someone else changed it first (stale version), or the record's facts
    rule the action out (closing a week with no close snapshot)."""

    status_code = 409
    code = "conflict"


class NotLive(FinanceError):
    """The module is not live for the organisation (off or shadow), so this
    write is refused."""

    status_code = 409
    code = "not_live"


class Invalid(FinanceError):
    """The request itself is wrong: an unknown lane, a share split that does
    not add up, a week that is not a Monday."""

    status_code = 400
    code = "invalid"


def http_exception(err: FinanceError):
    """``HTTPException`` for a router to raise. Imported lazily so services
    never depend on FastAPI."""
    from fastapi import HTTPException

    return HTTPException(
        status_code=err.status_code, detail={"code": err.code, "message": err.message}
    )


def flush(db) -> None:
    """``db.flush()`` with the two database refusals a service expects
    translated: a stale ``version`` (two people saved the same row) and a
    unique or check violation (a second live push for the week, a payout
    keyed twice)."""
    try:
        db.flush()
    except StaleDataError as exc:
        raise Conflict(
            "This was changed by someone else since you opened it — reload and try again."
        ) from exc
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        raise Conflict(
            _INTEGRITY_MESSAGES.get(
                constraint, "That conflicts with an existing record."
            )
        ) from exc


_INTEGRITY_MESSAGES = {
    "uq_pay_weeks_venue_week": "That pay week already exists for this entity.",
    "uq_pay_leave_payouts_request_week": (
        "A payout for that leave request and week is already recorded."
    ),
    "uq_pay_xero_pushes_live": (
        "A live Xero document already exists for that week — void it before pushing again."
    ),
    "uq_pay_xero_pushes_key": "That push was already sent (same idempotency key).",
    "uq_pay_ace_inputs_week": "ACE inputs for that week are already recorded.",
    "uq_pay_accruals_week": "An accrual row for that week already exists.",
    "uq_pay_entities_venue": "That venue is already a payroll entity.",
    "uq_ent_public_tokens_hash": "That link already exists.",
    "uq_ent_performance_shares_member": "That member already has a share of this performance.",
    "uq_ent_performer_xero_contacts_tenant": (
        "That performer already has a Xero contact for this organisation."
    ),
    "uq_ent_settings_org": "Entertainer settings already exist for this organisation.",
    "uq_fin_budget_days_date": "That day is already in this budget version.",
    "uq_fin_budget_lines_item": "That line item is already set for the month.",
    "uq_fin_venue_settings_venue": "That venue already has finance settings.",
    "uq_fin_job_runs_slot": "That job run is already claimed.",
}
