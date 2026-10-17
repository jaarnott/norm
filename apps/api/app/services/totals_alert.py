"""Tell the platform admins when Norm can't make an invoice add up.

The totals work-out (``invoice_review._work_out_the_lines``) closes any gap
it can see on the copy. When none of its answers adds up the "doesn't add
up" blocker stands and Autopilot stops on that invoice — which the owner
wants never to happen (17 Oct 2026), so each one is emailed to be fixed at
its cause, the way unpriced charge lines were found on Alsco CCTN3779394.
"""

import datetime
import logging

logger = logging.getLogger(__name__)

TEMPLATE = "totals_unresolved"
#: One email per invoice in this window — a re-review of the same draft
#: doesn't send it again.
REPEAT_AFTER = datetime.timedelta(days=7)


def _money(v: object) -> str:
    try:
        return f"{float(v):,.2f}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"


def _tried(tried: list[dict]) -> list[str]:
    out = []
    for t in tried or []:
        answer = t.get("answer") if isinstance(t.get("answer"), dict) else {}
        why = str(answer.get("why") or "").strip()
        out.append(f"{why or 'An answer'} — {t.get('problem')}")
    return out


def alert_totals_unresolved(venue_id: str | None, info: dict) -> None:
    """Email the platform admins about one invoice Norm couldn't make add
    up. Its own session, so the review's transaction is never touched; never
    raises."""
    from app.db.engine import SessionLocal
    from app.db.models import EmailLog, User, Venue
    from app.services.email_service import send_system_email

    db = SessionLocal()
    try:
        venue = (
            db.query(Venue).filter(Venue.id == venue_id).first() if venue_id else None
        )
        venue_name = venue.name if venue else "a venue"
        number = info.get("reference_number") or info.get("invoice_id")
        supplier = info.get("supplier_name") or "a supplier"
        subject = f"Norm couldn't make {supplier} {number} add up ({venue_name})"
        since = datetime.datetime.now(datetime.timezone.utc) - REPEAT_AFTER
        if (
            db.query(EmailLog)
            .filter(
                EmailLog.template_name == TEMPLATE,
                EmailLog.subject == subject,
                EmailLog.created_at >= since,
            )
            .first()
        ):
            return
        to = [
            u.email
            for u in db.query(User).filter(User.role == "admin").all()
            if u.email and not u.email.endswith((".test", ".local"))
        ]
        if not to:
            return
        send_system_email(
            TEMPLATE,
            to,
            {
                "subject": subject,
                "venue_name": venue_name,
                "supplier_name": supplier,
                "reference_number": number,
                "invoice_id": info.get("invoice_id"),
                "came_to": _money(info.get("came_to")),
                "total": _money(info.get("total")),
                "tried": _tried(info.get("tried") or []),
            },
            db,
            organization_id=venue.organization_id if venue else None,
        )
        db.commit()
    except Exception:  # noqa: BLE001 — an alert never fails a review
        logger.exception("totals alert for %s failed", info.get("invoice_id"))
        db.rollback()
    finally:
        db.close()
