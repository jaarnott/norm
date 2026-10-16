"""Retention for the personal data the public surface collects (the plan's
privacy rules), across every organisation:

* **One-time codes** — a code lives 10 minutes; its hash, attempt count and
  send-window counters are cleared once they are a day old.
* **Portal sessions** — deleted once expired, or a day after being revoked.
* **Web-form submissions** — the salted IP hash and the user agent are
  blanked after six months; the answers and attribution stay (they are the
  lead, and its source).

``purge`` flushes and returns counts; the caller commits (the scheduled job
the coordinator wires, e.g. daily).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.functions_models import FnFormSubmission, FnPortalAccess, FnPortalSession
from app.db.models import _now

OTP_KEEP = dt.timedelta(hours=24)
REVOKED_SESSION_KEEP = dt.timedelta(hours=24)
SUBMISSION_DETAIL_KEEP = dt.timedelta(days=183)


def purge(db: Session, *, now: dt.datetime | None = None) -> dict:
    now = now or _now()
    otp_cutoff = now - OTP_KEEP
    codes = (
        db.query(FnPortalAccess)
        .filter(
            or_(
                FnPortalAccess.otp_hash.is_not(None),
                FnPortalAccess.otp_window_started_at.is_not(None),
            ),
            or_(
                FnPortalAccess.otp_expires_at.is_(None),
                FnPortalAccess.otp_expires_at < otp_cutoff,
            ),
            or_(
                FnPortalAccess.otp_window_started_at.is_(None),
                FnPortalAccess.otp_window_started_at < otp_cutoff,
            ),
        )
        .update(
            {
                FnPortalAccess.otp_hash: None,
                FnPortalAccess.otp_expires_at: None,
                FnPortalAccess.otp_attempts: 0,
                FnPortalAccess.otp_sends_in_window: 0,
                FnPortalAccess.otp_window_started_at: None,
            },
            synchronize_session=False,
        )
    )
    sessions = (
        db.query(FnPortalSession)
        .filter(
            or_(
                FnPortalSession.expires_at < now,
                FnPortalSession.revoked_at < now - REVOKED_SESSION_KEEP,
            )
        )
        .delete(synchronize_session=False)
    )
    submissions = (
        db.query(FnFormSubmission)
        .filter(
            FnFormSubmission.submitted_at < now - SUBMISSION_DETAIL_KEEP,
            or_(
                FnFormSubmission.ip_hash.is_not(None),
                FnFormSubmission.user_agent.is_not(None),
            ),
        )
        .update(
            {FnFormSubmission.ip_hash: None, FnFormSubmission.user_agent: None},
            synchronize_session=False,
        )
    )
    db.flush()
    return {
        "otp_cleared": codes,
        "sessions_deleted": sessions,
        "submissions_scrubbed": submissions,
    }
