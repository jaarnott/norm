"""Retention for the three Finance modules (plan §6.5 item 2), run daily per
organisation by ``payroll.retention`` on the tick:

* public tokens and one-time codes as Functions keeps them — a code's hash
  and counters are cleared a day after they could last be used; an expired
  or revoked token is deleted a day later;
* ``ent_performance_changes``: the salted IP hash and the user agent are
  blanked after six months (the declaration snapshot's are kept — it is the
  IR330C record);
* a performer who was invited and never submitted is deleted after 90 days
  (one with performances or shares is kept — they are pay records);
* pay facts, exports, payouts, pushes and gratuities are kept at least seven
  years (ERA 2000 s 130, Holidays Act s 81, Tax Administration Act s 22) and
  purged after seven years plus one (IPP 9: no indefinite keep). Nothing
  under the floor is deleted by a job.

``purge`` flushes and returns counts; the caller commits.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import exists, or_
from sqlalchemy.orm import Session

from app.db.entertainers_models import (
    EntPerformance,
    EntPerformanceChange,
    EntPerformanceShare,
    EntPerformer,
    EntPublicToken,
)
from app.db.models import _now
from app.db.payroll_models import (
    PayGratuityBatch,
    PayLeavePayout,
    PayWeek,
    PayXeroPush,
)

OTP_KEEP = dt.timedelta(hours=24)
TOKEN_KEEP = dt.timedelta(hours=24)
CHANGE_DETAIL_KEEP = dt.timedelta(days=183)
INVITED_KEEP = dt.timedelta(days=90)
#: Seven years the law asks for, plus one: the floor nothing under is touched.
PAY_RECORDS_KEEP = dt.timedelta(days=365 * 8 + 2)


def floor_date(now: dt.datetime) -> dt.date:
    """The oldest week start a job may purge."""
    return (now - PAY_RECORDS_KEEP).date()


def purge(db: Session, org_id: str, *, now: dt.datetime | None = None) -> dict:
    now = now or _now()
    otp_cutoff = now - OTP_KEEP
    codes = (
        db.query(EntPublicToken)
        .filter(
            EntPublicToken.organization_id == org_id,
            or_(
                EntPublicToken.otp_hash.is_not(None),
                EntPublicToken.otp_window_started_at.is_not(None),
            ),
            or_(
                EntPublicToken.otp_expires_at.is_(None),
                EntPublicToken.otp_expires_at < otp_cutoff,
            ),
            or_(
                EntPublicToken.otp_window_started_at.is_(None),
                EntPublicToken.otp_window_started_at < otp_cutoff,
            ),
        )
        .update(
            {
                EntPublicToken.otp_hash: None,
                EntPublicToken.otp_expires_at: None,
                EntPublicToken.otp_attempts: 0,
                EntPublicToken.otp_sends_in_window: 0,
                EntPublicToken.otp_window_started_at: None,
            },
            synchronize_session=False,
        )
    )
    tokens = (
        db.query(EntPublicToken)
        .filter(
            EntPublicToken.organization_id == org_id,
            or_(
                EntPublicToken.expires_at < now - TOKEN_KEEP,
                EntPublicToken.revoked_at < now - TOKEN_KEEP,
            ),
        )
        .delete(synchronize_session=False)
    )
    changes = (
        db.query(EntPerformanceChange)
        .filter(
            EntPerformanceChange.organization_id == org_id,
            EntPerformanceChange.changed_at < now - CHANGE_DETAIL_KEEP,
            or_(
                EntPerformanceChange.ip_hash.is_not(None),
                EntPerformanceChange.user_agent.is_not(None),
            ),
        )
        .update(
            {EntPerformanceChange.ip_hash: None, EntPerformanceChange.user_agent: None},
            synchronize_session=False,
        )
    )
    has_performance = exists().where(
        (EntPerformance.organization_id == EntPerformer.organization_id)
        & (EntPerformance.performer_id == EntPerformer.id)
    )
    has_share = exists().where(
        (EntPerformanceShare.organization_id == EntPerformer.organization_id)
        & (EntPerformanceShare.member_performer_id == EntPerformer.id)
    )
    invited = (
        db.query(EntPerformer)
        .filter(
            EntPerformer.organization_id == org_id,
            EntPerformer.onboarding_status == "invited",
            EntPerformer.submitted_at.is_(None),
            EntPerformer.created_at < now - INVITED_KEEP,
            ~has_performance,
            ~has_share,
        )
        .all()
    )
    for performer in invited:
        db.delete(performer)

    floor = floor_date(now)
    old_weeks = (
        db.query(PayWeek)
        .filter(PayWeek.organization_id == org_id, PayWeek.week_start < floor)
        .all()
    )
    pushes = 0
    for week in old_weeks:
        # Pushes reference the week without cascading: they go first.
        pushes += (
            db.query(PayXeroPush)
            .filter(
                PayXeroPush.organization_id == org_id, PayXeroPush.week_id == week.id
            )
            .delete(synchronize_session=False)
        )
        db.delete(week)  # snapshots, facts, exports, inputs cascade
    pushes += (
        db.query(PayXeroPush)
        .filter(
            PayXeroPush.organization_id == org_id,
            PayXeroPush.week_id.is_(None),
            PayXeroPush.pushed_at < now - PAY_RECORDS_KEEP,
        )
        .delete(synchronize_session=False)
    )
    payouts = (
        db.query(PayLeavePayout)
        .filter(
            PayLeavePayout.organization_id == org_id, PayLeavePayout.week_start < floor
        )
        .delete(synchronize_session=False)
    )
    batches = (
        db.query(PayGratuityBatch)
        .filter(
            PayGratuityBatch.organization_id == org_id,
            PayGratuityBatch.week_start < floor,
        )
        .all()
    )
    for batch in batches:
        db.delete(batch)
    db.flush()
    return {
        "otp_cleared": codes,
        "tokens_deleted": tokens,
        "changes_scrubbed": changes,
        "invited_deleted": len(invited),
        "weeks_purged": len(old_weeks),
        "pushes_purged": pushes,
        "payouts_purged": payouts,
        "gratuity_batches_purged": len(batches),
        "floor": floor.isoformat(),
    }
