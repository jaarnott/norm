"""The Functions domain errors, and the one translation from database errors.

Services raise these; routers map them to HTTP with ``err.status_code`` (or
``http_exception(err)``), agent tools turn them into a refusal the model can
read. Keeping HTTP out of the services is what lets the importer, the
scheduler, the public pages and the agent share them.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError


class FunctionsError(Exception):
    """Base class. ``code`` is stable and machine-readable; the message is for
    people."""

    status_code = 400
    code = "functions_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(FunctionsError):
    """Missing — or outside the caller's organisation or venues. The two are
    deliberately indistinguishable."""

    status_code = 404
    code = "not_found"


class Forbidden(FunctionsError):
    """The caller can see the record but may not do this to it (e.g. approve
    a minimum spend without being the venue's approver)."""

    status_code = 403
    code = "forbidden"


class Conflict(FunctionsError):
    """Someone else changed it first (stale version), or the record's facts
    rule the action out (confirming an enquiry that is already booked)."""

    status_code = 409
    code = "conflict"


class NotLive(FunctionsError):
    """The organisation's Functions are not live (off or shadow), so nothing
    may be written."""

    status_code = 409
    code = "not_live"


class Invalid(FunctionsError):
    """The request itself is wrong: a missing reason, an unknown booking type,
    a venue without a timezone."""

    status_code = 422
    code = "invalid"


def http_exception(err: FunctionsError):
    """``HTTPException`` for a router to raise. Imported lazily so services
    never depend on FastAPI."""
    from fastapi import HTTPException

    return HTTPException(
        status_code=err.status_code, detail={"code": err.code, "message": err.message}
    )


def flush(db) -> None:
    """``db.flush()`` with the two database refusals a service expects
    translated: a stale ``version`` (two people saved the same row) and a
    unique/foreign-key violation (e.g. an email another contact already has,
    a second live event for one enquiry)."""
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
    "uq_fn_contact_emails_org_email": "Another contact already uses that email address.",
    "uq_fn_events_live_enquiry": "This enquiry already has a booked event.",
    "uq_fn_min_spends_event": "That event already has a confirmed minimum spend.",
    "uq_fn_proposals_enquiry": "This enquiry already has a proposal.",
    "uq_fn_thread_links_live_enquiry": "That thread is already linked to this enquiry.",
    "uq_fn_thread_links_live_event": "That thread is already linked to this event.",
}
