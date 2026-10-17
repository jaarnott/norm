"""An invoice Norm can't make add up is emailed to the platform admins, once.

Autopilot stops on such an invoice, which the owner wants never to happen
(17 Oct 2026) — so each one is reported, to be fixed at its cause.
"""

from app.config import settings
from app.db.models import EmailLog
from app.services.totals_alert import alert_totals_unresolved

from .org_helpers import make_org, make_user, make_venue
from .supplier_fakes import bind_sessions

INFO = {
    "invoice_id": "inv-1",
    "reference_number": "CCTN3779394",
    "supplier_name": "Alsco",
    "came_to": 196.98,
    "total": 228.5,
    "tried": [
        {"answer": {"why": "the charges are 14.33"}, "problem": "comes to 212.25"}
    ],
}


def test_the_admins_hear_once_per_invoice(db_session, monkeypatch):
    bind_sessions(monkeypatch, db_session, "SessionLocal")
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")  # logged, never sent
    venue = make_venue(db_session, "Bessie & Engineers", make_org(db_session, "O"))
    admin = make_user(db_session, role="admin")
    test_admin = make_user(db_session, role="admin")
    test_admin.email = "e2e@norm.test"
    make_user(db_session)
    db_session.flush()

    alert_totals_unresolved(venue.id, INFO)
    alert_totals_unresolved(venue.id, INFO)  # a re-review of the same draft

    [log] = (
        db_session.query(EmailLog)
        .filter(EmailLog.template_name == "totals_unresolved")
        .all()
    )
    assert admin.email in log.to_addresses
    assert test_admin.email not in log.to_addresses
    assert log.subject == (
        "Norm couldn't make Alsco CCTN3779394 add up (Bessie & Engineers)"
    )
    assert "196.98" in log.html_body and "228.50" in log.html_body
    assert "the charges are 14.33" in log.html_body
