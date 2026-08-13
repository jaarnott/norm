"""The cannot-receive endpoint and the autopilot-confidence report.

The report exists to answer one question honestly — "would autopilot have been
right?" — so the tests here mostly pin what must be EXCLUDED from the rates:
Norm's own self-fulfilling receives, and invoices nobody ever reviewed.
"""

from datetime import datetime, timezone

import pytest

from app.db.models import InvoiceAutopilotOutcome

from .conftest import _make_organization, _make_venue


def _venue(db, name="Bessie"):
    org = _make_organization(db, name=f"{name} Co")
    return _make_venue(db, name=name, organization_id=org.id)


@pytest.fixture()
def bind_recorder(db_session, monkeypatch):
    """Let the recorder write into the test's transaction.

    In production it deliberately opens its OWN session — a poisoned session
    must never turn a completed receive into an error — but that would be a
    different transaction here, unable to see db_session's uncommitted venue.
    """

    class _Shim:
        def __init__(self, s):
            self._s = s

        def __getattr__(self, k):
            return getattr(self._s, k)

        def commit(self):
            self._s.flush()

        def close(self):
            pass

    monkeypatch.setattr("app.db.engine.SessionLocal", lambda: _Shim(db_session))
    return db_session


def _row(db, venue, outcome, **over):
    row = InvoiceAutopilotOutcome(
        venue_id=venue.id,
        organization_id=venue.organization_id,
        invoice_id=over.pop("invoice_id", f"inv-{outcome}-{id(over)}"),
        supplier_name=over.pop("supplier_name", "Bidfood"),
        outcome=outcome,
        received=outcome != "dojo",
        mode=over.pop("mode", "interactive"),
        actor=over.pop("actor", "user"),
        created_at=datetime.now(timezone.utc),
        **over,
    )
    db.add(row)
    db.flush()
    return row


class TestReport:
    def _get(self, client, headers, **params):
        # Always venue-scoped: the endpoint reads the whole table, so an
        # unscoped assertion would depend on what other tests happened to
        # record (receives elsewhere in the suite now write rows too).
        q = "&".join(f"{k}={v}" for k, v in params.items())
        return client.get(
            f"/api/supplier-invoice-specs/autopilot-confidence{'?' + q if q else ''}",
            headers=headers,
        )

    def test_requires_admin(self, client, manager_headers):
        assert self._get(client, manager_headers).status_code == 403

    def test_norm_rows_never_touch_the_headline_rate(
        self, client, admin_headers, db_session
    ):
        # Autopilot accepted everything a line before receiving, so its own
        # rows are clean by construction — counting them would make the
        # readiness number meaningless.
        v = _venue(db_session)
        _row(db_session, v, "clean", suggestion_count=2, accepted_count=2)
        _row(db_session, v, "edited", invoice_id="i-2", suggestion_count=2)
        for i in range(8):
            _row(
                db_session,
                v,
                "clean",
                invoice_id=f"norm-{i}",
                actor="norm",
                mode="autopilot",
                suggestion_count=3,
            )
        body = self._get(client, admin_headers, venue_id=v.id).json()
        assert body["totals"]["attempts"] == 2  # humans only
        assert body["rates"]["autopilot_ready"] == 0.5
        assert body["autopilot"]["attempts"] == 8  # reported, separately

    def test_zero_suggestion_invoices_leave_the_quality_rate_alone(
        self, client, admin_headers, db_session
    ):
        # They belong in "would autopilot have been right?" but NOT in "when
        # Norm spoke, was it right?" — otherwise the quality rate inflates.
        v = _venue(db_session)
        _row(db_session, v, "clean", invoice_id="a", suggestion_count=2)
        _row(db_session, v, "edited", invoice_id="b", suggestion_count=2)
        for i in range(6):
            _row(db_session, v, "no_suggestions", invoice_id=f"n-{i}")
        body = self._get(client, admin_headers, venue_id=v.id).json()
        assert body["rates"]["suggestion_quality"] == 0.5  # 1 clean of 2 spoken
        assert body["rates"]["autopilot_ready"] == 0.875  # 7 of 8

    def test_not_reviewed_is_in_no_denominator(self, client, admin_headers, db_session):
        v = _venue(db_session)
        _row(db_session, v, "clean", invoice_id="a", suggestion_count=1)
        _row(db_session, v, "not_reviewed", invoice_id="b")
        body = self._get(client, admin_headers, venue_id=v.id).json()
        assert body["totals"]["not_reviewed"] == 1
        assert body["rates"]["autopilot_ready"] == 1.0

    def test_per_supplier_and_missed_fields(self, client, admin_headers, db_session):
        v = _venue(db_session)
        _row(db_session, v, "clean", invoice_id="a", suggestion_count=1)
        _row(
            db_session,
            v,
            "edited",
            invoice_id="b",
            supplier_name="Hancocks",
            suggestion_count=1,
            manual_edit_count=1,
            detail={"manual_fields": ["line:ld-9.unit_cost"]},
        )
        body = self._get(client, admin_headers, venue_id=v.id).json()
        names = {s["supplier_name"]: s for s in body["suppliers"]}
        assert names["Bidfood"]["autopilot_ready"] == 1.0
        assert names["Hancocks"]["autopilot_ready"] == 0.0
        # Normalised so the same field on different lines aggregates.
        assert body["top_missed_fields"][0] == {"field": "line.unit_cost", "count": 1}


class TestCannotReceive:
    def test_stages_records_and_never_receives(
        self, client, manager_headers, db_session, monkeypatch, bind_recorder
    ):
        from app.routers import invoice_fixes as IF

        v = _venue(db_session, "Goose")
        staged = {}

        def fake_stage(db, venue_id, invoice_id, *, draft):
            staged["draft"] = draft
            return {
                "sample_id": "s-1",
                "spec_id": "sp-1",
                "spec_name": "Bidfood",
                "created_spec": False,
                "already_in_dojo": False,
            }

        monkeypatch.setattr("app.services.spec_dojo.stage_invoice_sample", fake_stage)
        # A Loaded receive here would be a bug — there is no client to call.
        monkeypatch.setattr(
            IF, "_do_receive", lambda *a, **k: pytest.fail("must not receive")
        )
        res = client.post(
            "/api/invoice-fixes/cannot-receive",
            headers=manager_headers,
            json={"venue_id": v.id, "invoice_id": "inv-x", "reason": "unit is wrong"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["staged"] is True
        # The card branches on these — a repeat press must not claim it filed
        # something, and a copy-less invoice must not claim it at all.
        assert body["already_in_dojo"] is False
        # NOT a draft. Drafts are the Dojo page's "somebody expanded this row"
        # state and are hidden from "awaiting review", so filing a human's
        # explicit verdict that way made the button look like it did nothing.
        assert staged["draft"] is False

        row = (
            db_session.query(InvoiceAutopilotOutcome)
            .filter(InvoiceAutopilotOutcome.invoice_id == "inv-x")
            .first()
        )
        assert row is not None
        assert row.outcome == "dojo" and row.received is False
        assert row.detail["dojo"]["reason"] == "unit is wrong"

    def test_an_invoice_with_no_copy_still_records_the_verdict(
        self, client, manager_headers, db_session, monkeypatch, bind_recorder
    ):
        # It cannot be staged, but the human's verdict IS the measurement —
        # losing it because Loaded has no PDF would defeat the feature.
        v = _venue(db_session, "Zeppa")

        def no_copy(*a, **k):
            raise RuntimeError("no invoice copy attached — nothing to add")

        monkeypatch.setattr("app.services.spec_dojo.stage_invoice_sample", no_copy)
        res = client.post(
            "/api/invoice-fixes/cannot-receive",
            headers=manager_headers,
            json={"venue_id": v.id, "invoice_id": "inv-nopdf"},
        )
        assert res.status_code == 200
        assert res.json()["staged"] is False
        row = (
            db_session.query(InvoiceAutopilotOutcome)
            .filter(InvoiceAutopilotOutcome.invoice_id == "inv-nopdf")
            .first()
        )
        assert row.outcome == "dojo" and row.detail["dojo"]["staged"] is False


class TestAFiledInvoiceIsVisible:
    """The bug behind "Can't receive isn't adding invoices to the dojo".

    Filing worked — a sample row was created every time. But it was created as
    a DRAFT, and the Dojo page hides drafts: they are excluded from "awaiting
    review" and never earn the green "in dojo" chip, because a draft means
    "somebody expanded this row", not "a human filed this for training". The
    invoice therefore sat in the outstanding list looking exactly as before.
    """

    def _sample(self, db, spec, *, draft):
        from app.db.config_models import SupplierSpecSample

        s = SupplierSpecSample(
            spec_id=spec.id,
            label=f"draft-{draft}.pdf",
            pdf_bytes=b"%PDF-",
            expected=None,  # no baseline yet — this is what "awaiting review" means
            draft=draft,
        )
        db.add(s)
        db.commit()
        return s

    def test_a_filed_sample_reaches_awaiting_review(
        self, client, admin_headers, db_session
    ):
        from app.db.config_models import SupplierInvoiceSpec

        spec = SupplierInvoiceSpec(name="Filed Co", aliases=[], instructions="")
        db_session.add(spec)
        db_session.commit()
        filed = self._sample(db_session, spec, draft=False)
        hidden = self._sample(db_session, spec, draft=True)

        res = client.get(
            "/api/supplier-invoice-specs/dojo/overview", headers=admin_headers
        )
        assert res.status_code == 200, res.text
        ids = {r["id"] for r in res.json()["pending_review"]}
        assert filed.id in ids, "a filed invoice must be visible in the dojo"
        assert hidden.id not in ids, "an incidental draft must stay hidden"
