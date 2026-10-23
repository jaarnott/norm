"""Defects the security review of the Finance build found (20 Oct 2026),
each pinned by the test that failed before its fix:

* the onboarding form's "about" step changed the record's email BEFORE the
  code, and the code went to the new address — a forwarded link could
  redirect pay;
* the one-time code was in the email's subject line, so it sat in
  ``email_logs`` / ``ent_email_links`` and on the Emails page for anyone with
  venue access;
* anyone with venue access could change an accepted performer's email on the
  Register, where the code and the "details changed" notice go;
* ``files/store.file_bytes`` found a file by id in ANY organisation (a Xero
  attachment from another tenant);
* ``REKEY.csv`` wrote cells a spreadsheet runs as formulas;
* an absurd invoice amount on the portal was a 500, not a 400;
* an uploaded filename reached the ``Content-Disposition`` header uncleaned;
* the importer stored Orbit's invoice bytes without the active-content check
  a portal upload gets.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io

import pytest

from app.db.entertainers_models import EntEmailLink, EntFile, EntPerformance
from app.db.models import EmailLog
from app.services import caller_scope
from app.services.entertainers import tokens as tokens_mod
from app.services.files import store
from tests.entertainers_world import (
    add_performer,
    approved,
    join_link,
    make_world,
    portal_link,
    verified,
)
from tests.org_helpers import make_org

UTC = dt.timezone.utc


def _pdf() -> bytes:
    from pypdf import PdfWriter

    out = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def w(db_session):
    return make_world(db_session)


@pytest.fixture
def invited(w):
    performer = add_performer(
        w.db, w.org, status="invited", lane=None, bank=None, ird=None, declaration=False
    )
    token, _url = join_link(w.db, w.org, performer)
    return performer, token


# ---------------------------------------------------------------------------
# The code goes to the INVITED address — nothing on the form moves it first
# ---------------------------------------------------------------------------


class TestCodeAddress:
    def test_the_about_step_cannot_change_the_email_before_the_code(
        self, w, client, invited
    ):
        performer, token = invited
        body = {"name": "Sam Smith", "email": "forwarded@evil.test", "phone": "021 1"}
        r = client.post(f"/api/public/join/{token}/steps/about", json=body)
        assert r.status_code == 400, r.text
        assert "email" in r.json()["detail"]["errors"]
        w.db.refresh(performer)
        assert performer.email == "sam@example.test"
        # The same address (any case) is not a change.
        r = client.post(
            f"/api/public/join/{token}/steps/about",
            json={**body, "email": "Sam@Example.test"},
        )
        assert r.status_code == 200, r.text
        # Once the code sent to the invited address is verified, the person
        # has proved they hold that mailbox and may correct the address.
        verified(w.db, token)
        r = client.post(f"/api/public/join/{token}/steps/about", json=body)
        assert r.status_code == 200, r.text
        w.db.refresh(performer)
        assert performer.email == "forwarded@evil.test"

    def test_the_code_is_in_no_subject_or_stored_body(
        self, w, client, invited, monkeypatch
    ):
        _performer, token = invited
        monkeypatch.setattr(
            tokens_mod, "request_code", lambda db, row, now=None: "424242"
        )
        r = client.post(f"/api/public/join/{token}/code/request")
        assert r.status_code == 202
        logs = w.db.query(EmailLog).filter(EmailLog.template_name == "ent_code").all()
        assert logs, "the code email was not logged"
        for log in logs:
            assert "424242" not in (log.subject or "")
            assert "424242" not in (log.html_body or "")
        links = w.db.query(EntEmailLink).filter(EntEmailLink.kind == "ent_code").all()
        assert links
        for link in links:
            assert "424242" not in (link.subject or "")
        # The Emails page (venue access) never shows it either.
        r = client.get("/api/entertainers/emails", headers=w.headers(w.team_member))
        assert r.status_code == 200
        assert "424242" not in r.text
        link_id = links[0].id
        r = client.get(
            f"/api/entertainers/emails/{link_id}", headers=w.headers(w.team_member)
        )
        assert r.status_code == 200 and "424242" not in r.text

    def test_venue_access_cannot_change_an_accepted_performers_email(self, w, client):
        performer = add_performer(w.db, w.org)  # accepted, bank on file
        body = {"email": "new@example.test", "version": performer.version}
        r = client.put(
            f"/api/entertainers/performers/{performer.id}",
            json=body,
            headers=w.headers(w.team_member),
        )
        assert r.status_code == 403, r.text
        w.db.refresh(performer)
        assert performer.email == "sam@example.test"
        # A payroll person may; so may a booker fixing an invited performer's.
        r = client.put(
            f"/api/entertainers/performers/{performer.id}",
            json=body,
            headers=w.headers(w.payroll_person),
        )
        assert r.status_code == 200, r.text
        fresh = add_performer(
            w.db,
            w.org,
            name="New Act",
            email="typo@example.test",
            status="invited",
            lane=None,
            bank=None,
            ird=None,
            declaration=False,
        )
        r = client.put(
            f"/api/entertainers/performers/{fresh.id}",
            json={"email": "fixed@example.test", "version": fresh.version},
            headers=w.headers(w.team_member),
        )
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def _file(db, org, data=b"%PDF-1.4 bill", kind="invoice", filename="bill.pdf"):
    row = EntFile(
        organization_id=org.id,
        sha256=hashlib.sha256(data).hexdigest(),
        kind=kind,
        filename=filename,
        content_type="application/pdf",
        size_bytes=len(data),
        data=data,
    )
    db.add(row)
    db.flush()
    return row


class TestFileStoreTenancy:
    def test_file_bytes_never_cross_organisations(self, db_session):
        org_a = make_org(db_session, "A Co")
        org_b = make_org(db_session, "B Co")
        theirs = _file(db_session, org_b)
        with caller_scope.use(caller_scope.Scope(org_a.id, user_id="u")):
            assert store.file_bytes(db_session, theirs.id) == (b"", "")
        with caller_scope.use(caller_scope.Scope(org_b.id, user_id="u")):
            assert store.file_bytes(db_session, theirs.id)[0] == b"%PDF-1.4 bill"
        # A scope that names no organisation is nobody's: refused too.
        with caller_scope.use(caller_scope.Scope(org_id=None)):
            assert store.file_bytes(db_session, theirs.id) == (b"", "")
        # No scope at all is a system caller (a job), as before.
        assert store.file_bytes(db_session, theirs.id)[0] == b"%PDF-1.4 bill"


class TestUploadedFilenames:
    def test_a_filename_is_cleaned_for_the_download_header(self, w, client):
        performer = add_performer(w.db, w.org)
        r = client.post(
            f"/api/entertainers/performers/{performer.id}/documents",
            files={"file": ('a"b\r\nX-Evil: yes.pdf', _pdf(), "application/pdf")},
            data={"kind": "company_document"},
            headers=w.headers(w.payroll_person),
        )
        assert r.status_code == 201, r.text
        stored = r.json()["file"]["filename"]
        assert '"' not in stored and "\r" not in stored and "\n" not in stored
        file_id = r.json()["file"]["id"]
        r = client.get(
            f"/api/entertainers/performers/{performer.id}/documents/{file_id}",
            headers=w.headers(w.payroll_person),
        )
        assert r.status_code == 200
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert "X-Evil" not in r.headers
        assert "\n" not in r.headers["Content-Disposition"]


class TestPortalAmount:
    def test_an_absurd_invoice_amount_is_a_400_not_a_500(self, w, client):
        performer = add_performer(w.db, w.org, lane="invoice")
        past = approved(w.db, w.org, w.venue, performer, date=dt.date(2026, 10, 10))
        token, _url = portal_link(w.db, w.org, performer)
        for amount in ("1e400", "99999999999999999", "nan"):
            r = client.post(
                f"/api/public/entertainers/{token}/invoices",
                files={"file": ("inv.pdf", _pdf(), "application/pdf")},
                data={"performance_ids": f'["{past.id}"]', "invoice_amount": amount},
            )
            assert r.status_code == 400, (amount, r.status_code, r.text[:200])
        assert w.db.query(EntFile).count() == 0


class TestImporterFiles:
    def test_a_pdf_with_active_content_from_orbit_is_not_stored(self, db_session):
        from tests.test_finance_importer import (
            INVOICE_URL,
            S4,
            _fake,
            _id,
            _one,
            _rows,
            _run,
        )
        from tests.test_finance_importer import make_world as importer_world

        w = importer_world(db_session)
        fake = _fake(w)
        fake.files[INVOICE_URL.split("?", 1)[0]] = (
            b"%PDF-1.4\n/OpenAction << /S /JavaScript /JS (app.alert(1)) >>"
        )
        result, _ = _run(w, fake)
        assert result.ok, result.report.gate
        assert _rows(w, EntFile) == []
        s4 = _one(w, EntPerformance, _id(w, "ent_performances", S4))
        # The performance keeps its number and amount; only the file is left out.
        assert s4.invoice_file_id is None


# ---------------------------------------------------------------------------
# REKEY.csv
# ---------------------------------------------------------------------------


class TestRekeyCsv:
    def test_cells_that_would_run_as_formulas_are_guarded(self, db_session):
        from app.services.payroll import exports as exports_svc
        from app.services.payroll import inputs as inputs_svc
        from app.services.payroll import weeks as weeks_svc
        from tests.payroll_world import MONDAY
        from tests.payroll_world import make_world as payroll_world

        p = payroll_world(db_session)
        week = weeks_svc.get_or_create(p.db, p.ctx(), p.laz.id, MONDAY)
        inputs_svc.add_adjustment(
            p.db,
            p.ctx(),
            week,
            label='=HYPERLINK("http://evil.test","PH top-up")',
            amount_cents=-2500,
            report_target="wages",
            notes="@SUM(1)",
        )
        pack = exports_svc.fallback_pack(p.db, p.ctx(), week)
        lines = pack["files"]["REKEY.csv"].split("\r\n")
        row = next(line for line in lines if line.startswith("adjustment,"))
        cells = next(iter(csv.reader([row])))
        label = cells[5]
        assert label.startswith("'=")
        assert cells[9].startswith("'@")
        # Numbers are numbers: a negative amount is not a formula.
        assert cells[7] == "-2500"


class TestCreateBillAttachmentNeedsPayroll:
    """A held file is a pay record: attaching one to a Xero bill needs
    payroll:write, not just the orders:write that creating a bill needs."""

    def _run(self, holds: bool):
        from tests.test_xero_consolidators import ACCOUNTS, TAX, Api, run_bill

        class _Api(Api):
            def call_api(self, connector, action, params=None):
                if (connector, action) == ("norm", "caller_holds"):
                    self.calls.append((connector, action, dict(params or {})))
                    return {"holds": holds}
                return super().call_api(connector, action, params)

        api = _Api(
            {
                "list_contacts": {"Contacts": []},
                "list_tax_rates": TAX,
                "list_accounts": ACCOUNTS,
            }
        )
        capture: list = []
        out = run_bill(api, capture=capture, attachment_file_id="f1")
        return out, api.calls, capture

    def test_refused_without_payroll_write(self):
        out, calls, capture = self._run(holds=False)
        assert "payroll" in out.get("error", "")
        assert not capture  # never reached the card, let alone Xero
        assert not any(
            a in ("create_invoice", "attach_to_invoice") for _, a, _ in calls
        )

    def test_reaches_the_card_with_payroll_write(self):
        out, _, capture = self._run(holds=True)
        assert "payroll" not in str(out.get("error", ""))
        assert capture  # got as far as the preview card
