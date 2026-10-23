"""The invoice check with a fake reader: each of the six findings raised by
its case and only by it; a matched invoice's own lines on the bill; a query
never billed without a person; nothing rescaled; a second check of the same
file not calling the reader; the job's daily cap; the reader's attribution
(no organisation — Norm's cost).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.entertainers_models import EntFile
from app.db.finance_models import FinJobRun
from app.services.entertainers import bills, invoice_check, invoice_reader, jobs
from app.services.entertainers.invoice_check import findings, lines_add_up
from app.services.finance_core.errors import Conflict
from tests.entertainers_world import add_performer, approved, make_world


def _performer(**kw):
    from app.db.entertainers_models import EntPerformer

    base = dict(
        name="Sam Smith",
        legal_name="Samuel Smith",
        trading_name="Sam Sounds",
        company_name=None,
        lane="invoice",
        gst_registered=False,
        gst_number=None,
    )
    base.update(kw)
    return EntPerformer(**base)


def _perf(date=dt.date(2026, 10, 10), cents=40_000):
    from app.db.entertainers_models import EntPerformance

    return EntPerformance(performance_date=date, actual_cents=cents)


GOOD = {
    "payee_name": "Sam Sounds",
    "gst_number": None,
    "invoice_number": "INV-1",
    "invoice_date": "2026-10-11",
    "lines": [
        {"description": "DJ set Brew Bar", "date": "2026-10-10", "amount": 400.0}
    ],
    "subtotal": 400.0,
    "gst": None,
    "total": 400.0,
}


class TestFindings:
    def _run(
        self, extracted, performer=None, perfs=None, previous=frozenset(), gst_bp=1500
    ):
        return findings(
            extracted,
            performer=performer or _performer(),
            performances=perfs or [_perf()],
            gst_bp=gst_bp,
            previous_numbers=set(previous),
        )

    def test_a_clean_invoice_has_no_findings(self):
        assert self._run(GOOD) == []

    def test_total_more_than_a_dollar_off(self):
        out = self._run(
            {**GOOD, "total": 401.5, "lines": [{"description": "x", "amount": 401.5}]}
        )
        assert [f["code"] for f in out] == ["total"]
        assert out[0]["expected"] == 40_000 and out[0]["found"] == 40_150
        assert self._run({**GOOD, "total": 400.99}) == []  # inside $1

    def test_total_includes_gst_for_a_registered_performer(self):
        registered = _performer(gst_registered=True, gst_number="049091850")
        out = self._run(
            {**GOOD, "gst_number": "49-091-850", "gst": 60.0, "total": 460.0},
            performer=registered,
        )
        assert out == []
        out = self._run(
            {**GOOD, "gst_number": "49-091-850", "total": 400.0}, performer=registered
        )
        assert [f["code"] for f in out] == ["total"] and out[0]["expected"] == 46_000

    def test_gst_charged_by_an_unregistered_performer(self):
        out = self._run({**GOOD, "gst": 60.0, "subtotal": 400.0, "total": 460.0})
        assert sorted(f["code"] for f in out) == ["gst", "total"]
        only_gst = self._run({**GOOD, "gst": 0.01, "total": 400.0})
        assert [f["code"] for f in only_gst] == ["gst"]

    def test_a_differing_gst_number(self):
        registered = _performer(gst_registered=True, gst_number="049091850")
        out = self._run(
            {**GOOD, "gst_number": "35901981", "gst": 60.0, "total": 460.0},
            performer=registered,
        )
        assert [f["code"] for f in out] == ["gst"]

    def test_a_reused_invoice_number(self):
        out = self._run(GOOD, previous={"inv-1"})
        assert [f["code"] for f in out] == ["duplicate_number"]

    def test_a_payee_matching_none_of_the_names(self):
        out = self._run({**GOOD, "payee_name": "Totally Different Ltd"})
        assert [f["code"] for f in out] == ["payee"]
        assert self._run({**GOOD, "payee_name": "SAMUEL SMITH"}) == []
        assert self._run({**GOOD, "payee_name": "Sam J Smith"}) == []
        assert (
            self._run(
                {**GOOD, "payee_name": "Smith Sounds Limited"},
                performer=_performer(company_name="Smith Sounds Ltd"),
            )
            == []
        )

    def test_dates_that_are_not_the_selected_performances(self):
        out = self._run(
            {
                **GOOD,
                "lines": [{"description": "x", "date": "2026-10-03", "amount": 400.0}],
            }
        )
        assert [f["code"] for f in out] == ["dates"] and out[0]["found"] == [
            "2026-10-03"
        ]
        assert (
            self._run(
                {
                    **GOOD,
                    "lines": [
                        {"description": "x", "date": "10/10/2026", "amount": 400.0}
                    ],
                }
            )
            == []
        )

    def test_unreadable(self):
        assert [f["code"] for f in self._run({"error": "blurred"})] == ["unreadable"]
        assert [
            f["code"]
            for f in self._run(
                {"payee_name": None, "total": None, "lines": [], "invoice_number": None}
            )
        ] == ["unreadable"]

    def test_several_performances_sum(self):
        out = self._run(
            {
                **GOOD,
                "total": 900.0,
                "lines": [
                    {"description": "a", "date": "2026-10-10", "amount": 400.0},
                    {"description": "b", "date": "2026-10-17", "amount": 500.0},
                ],
            },
            perfs=[_perf(), _perf(dt.date(2026, 10, 17), 50_000)],
        )
        assert out == []

    def test_a_performance_with_no_approved_amount_is_named_not_read_as_zero(self):
        """An Orbit shift approved with no actual amount imports as
        actual_cents None. The 23 Oct trial's invoices for such shifts were
        right, yet the check said "differs from the approved $0.00"."""
        out = self._run(
            {
                **GOOD,
                "total": 900.0,
                "lines": [
                    {"description": "a", "date": "2026-10-10", "amount": 400.0},
                    {"description": "b", "date": "2026-10-17", "amount": 500.0},
                ],
            },
            perfs=[_perf(), _perf(dt.date(2026, 10, 17), None)],
        )
        assert [f["code"] for f in out] == ["total"]
        assert out[0]["message"] == (
            "No approved amount is recorded for 2026-10-17, so the invoice "
            "total can't be checked."
        )
        assert out[0]["expected"] is None and out[0]["found"] == 90_000


class TestLines:
    def test_lines_go_on_the_bill_only_when_they_add_up_never_rescaled(self):
        assert lines_add_up(GOOD, 40_000) == [
            {"description": "DJ set Brew Bar", "amount_cents": 40_000}
        ]
        assert (
            lines_add_up(
                {
                    **GOOD,
                    "lines": [
                        {"description": "a", "amount": 300.0},
                        {"description": "b", "amount": 99.0},
                    ],
                },
                40_000,
            )
            is None
        )
        assert (
            lines_add_up(
                {**GOOD, "lines": [{"description": "a", "amount": None}]}, 40_000
            )
            is None
        )


@pytest.fixture
def w(db_session):
    return make_world(db_session)


def _stored(w, performer, perf, *, extracted=None, number=None) -> EntFile:
    data = b"%PDF-1.4\n" + (number or "x").encode()
    from app.services.entertainers.performers import store_file

    file = (
        store_file.__wrapped__(
            w.db,
            w.org.id,
            kind="invoice",
            data=data,
            filename="i.pdf",
            content_type="application/pdf",
        )
        if hasattr(store_file, "__wrapped__")
        else None
    )
    if file is None:
        import hashlib

        file = EntFile(
            organization_id=w.org.id,
            sha256=hashlib.sha256(data).hexdigest(),
            kind="invoice",
            filename="i.pdf",
            content_type="application/pdf",
            size_bytes=len(data),
            data=data,
            check_status="pending",
            invoice_number=number,
            extracted=extracted,
        )
        w.db.add(file)
        w.db.flush()
    perf.invoice_file_id = file.id
    w.db.flush()
    return file


class TestRunCheck:
    def test_matched_then_the_bill_carries_the_invoices_lines(self, w):
        performer = add_performer(
            w.db,
            w.org,
            lane="invoice",
            legal_name="Samuel Smith",
            trading_name="Sam Sounds",
        )
        perf = approved(w.db, w.org, w.venue, performer)
        file = _stored(w, performer, perf)
        calls = []

        def reader(db, f):
            calls.append(f.id)
            return GOOD

        invoice_check.run_check(w.db, file, reader=reader)
        assert (
            file.check_status == "matched"
            and file.check_findings == []
            and file.invoice_number == "INV-1"
        )
        assert calls == [file.id]
        params = bills.bill_params(
            w.db,
            perf=perf,
            performer=performer,
            payee=performer,
            venue=w.venue,
            file=file,
            tenant="t1",
        )
        assert params["lines"] == [
            {
                "description": "DJ set Brew Bar",
                "quantity": 1,
                "unit_amount": 400.0,
                "category": "entertainment_expense",
                "gst": "no",
            }
        ]
        assert (
            params["attachment_file_id"] == file.id
            and params["idempotency_key"] == f"norm:ent-bill:{perf.id}"
        )
        assert (
            params["line_amount_types"] == "NoTax"
            and params["invoice_number"] == "INV-1"
        )

    def test_a_second_check_of_the_same_file_does_not_call_the_reader(self, w):
        performer = add_performer(
            w.db, w.org, lane="invoice", trading_name="Sam Sounds"
        )
        perf = approved(w.db, w.org, w.venue, performer)
        file = _stored(w, performer, perf)
        calls = []

        def model(db, f):
            calls.append(1)
            return GOOD

        import app.services.entertainers.invoice_reader as reader_mod

        original = reader_mod._call
        reader_mod._call = lambda db, block: model(db, None)
        try:
            invoice_check.run_check(w.db, file)
            file.check_status = "pending"
            invoice_check.run_check(w.db, file)
        finally:
            reader_mod._call = original
        assert calls == [1]
        assert file.extracted == GOOD

    def test_a_query_is_never_billed_until_waived_and_the_query_email_carries_the_figures(
        self, w
    ):
        performer = add_performer(
            w.db, w.org, lane="invoice", trading_name="Sam Sounds"
        )
        perf = approved(w.db, w.org, w.venue, performer)
        file = _stored(w, performer, perf)
        invoice_check.run_check(
            w.db, file, reader=lambda db, f: {**GOOD, "total": 450.0}
        )
        assert file.check_status == "query"
        ctx = w.ctx()
        with pytest.raises(Conflict, match="query"):
            bills._target(w.db, ctx, perf.id, None)
        invoice_check.send_query(w.db, ctx, file.id)
        from app.db.entertainers_models import EntEmailLink
        from app.db.models import EmailLog

        link = (
            w.db.query(EntEmailLink)
            .filter(EntEmailLink.kind == "ent_invoice_query")
            .one()
        )
        log = w.db.query(EmailLog).filter(EmailLog.id == link.email_log_id).one()
        assert "$400.00" in log.html_body and "$450.00" in log.html_body
        invoice_check.waive(w.db, ctx, file.id, note="agreed with Sam")
        assert file.check_status == "waived" and file.waived_by == w.payroll_person.id

    def test_a_transient_reader_failure_leaves_the_file_pending(self, w):
        performer = add_performer(w.db, w.org, lane="invoice")
        perf = approved(w.db, w.org, w.venue, performer)
        file = _stored(w, performer, perf)
        invoice_check.run_check(
            w.db, file, reader=lambda db, f: {"error": "overloaded", "transient": True}
        )
        assert file.check_status == "pending" and file.checked_at is None

    def test_the_reader_runs_under_no_organisation(self, w, monkeypatch):
        performer = add_performer(w.db, w.org, lane="invoice")
        perf = approved(w.db, w.org, w.venue, performer)
        file = _stored(w, performer, perf)
        seen = {}

        def fake_call_llm(**kw):
            from app.services import caller_scope

            scope = caller_scope.current()
            seen["org"] = scope.org_id if scope else "none"
            seen["thread"] = kw.get("thread_id")
            seen["call_type"] = kw.get("call_type")
            seen["documents"] = len(kw.get("documents") or [])
            return GOOD, "call-id"

        import app.interpreter.llm_interpreter as llm

        monkeypatch.setattr(llm, "call_llm", fake_call_llm)
        from app.services import caller_scope

        with caller_scope.use(
            caller_scope.Scope(org_id=w.org.id, user_id=w.payroll_person.id)
        ):
            out = invoice_reader.read_file(w.db, file)
        assert out == GOOD and seen == {
            "org": None,
            "thread": None,
            "call_type": "extraction",
            "documents": 1,
        }

    def test_the_job_checks_pending_files_and_caps_at_100_a_day(self, w, monkeypatch):
        performer = add_performer(
            w.db, w.org, lane="invoice", trading_name="Sam Sounds"
        )
        perf = approved(w.db, w.org, w.venue, performer)
        file = _stored(w, performer, perf)
        now = dt.datetime(2026, 10, 12, 1, 0, tzinfo=dt.timezone.utc)
        out = jobs._invoice_checks(w.db, w.org, now, reader=lambda db, f: GOOD)
        assert out == {"checked": 1, "failed": 0} and file.check_status == "matched"
        assert (
            w.db.query(FinJobRun).filter(FinJobRun.job == "ent.invoice_checks").count()
            == 1
        )
        monkeypatch.setattr(invoice_check, "CHECKS_PER_ORG_PER_DAY", 1)
        file.check_status = "pending"
        w.db.flush()
        assert (
            jobs._invoice_checks(w.db, w.org, now, reader=lambda db, f: GOOD)["skipped"]
            == "daily cap"
        )
