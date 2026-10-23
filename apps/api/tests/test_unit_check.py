"""The daily unit check (owner, 18 Oct 2026): every line Norm received, against
the invoice Norm read and Norm's catalogue — corrections for a person to apply or
dismiss, and lines Norm booked on an answer it wasn't sure of, to confirm or
change once (Norm's catalogue keeps the answer). The check never writes to
Loaded; applying or changing touches only the line's unit."""

import copy
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import (
    InvoiceAutopilotOutcome,
    UnitCheckProposal,
    UnitCheckRun,
    WorkingDocument,
)
from app.services import unit_check as UC
from app.services.received_invoice import (
    ReceivedLineUnitError,
    change_received_line_unit,
)

UNITS = [
    {"id": "u-kilo", "name": "Kilo", "ratio": 1, "stockUnitType": "Weight"},
    {"id": "u-100g", "name": "100 Grams", "ratio": 0.1, "stockUnitType": "Weight"},
    {"id": "u-each", "name": "Each", "ratio": 1, "stockUnitType": "Count"},
    {"id": "u-6x700", "name": "6x700ml", "ratio": 4.2, "stockUnitType": "Volume"},
    {"id": "u-42kg", "name": "4.2 KG", "ratio": 4.2, "stockUnitType": "Weight"},
]

NOW = datetime(2026, 10, 19, 19, 30, tzinfo=timezone.utc)


def _detail():
    return {
        "id": "inv-1",
        "referenceNumber": "INZ100",
        "supplierName": "Prime Fresh & Co",
        "issuedAt": "2026-10-18",
        "isReceived": True,
        "subtotal": 29.1,
        "total": 29.1,
        "lines": [
            {
                "id": "l1",
                "code": "HB2",
                "description": "BASIL bunch",
                "unit": "Kilo",
                "linkedUnitId": "u-kilo",
                "linkedUnitRatio": 1,
                "quantityReceived": 2.0,
                "unitCostExclTax": 3.8,
                "totalCostExclTax": 7.6,
                "linkedItemId": "it-basil",
            },
            {
                "id": "l2",
                "code": "P1",
                "description": "Passata",
                "unit": "4.2 KG",
                "linkedUnitId": "u-42kg",
                "linkedUnitRatio": 4.2,
                "quantityReceived": 1.0,
                "unitCostExclTax": 16.5,
                "totalCostExclTax": 16.5,
                "linkedItemId": "it-pass",
            },
            {
                "id": "l3",
                "code": "E1",
                "description": "FENNEL bulb",
                "unit": "Each",
                "linkedUnitId": "u-each",
                "linkedUnitRatio": 1,
                "quantityReceived": 2.0,
                "unitCostExclTax": 2.5,
                "totalCostExclTax": 5.0,
                "linkedItemId": "it-fen",
            },
        ],
    }


READING = [
    {
        "code": "HB2",
        "description": "BASIL bunch",
        "quantity": 2,
        "unit": "Bunch",
        "unit_of_measure": None,
        "unit_price_ex_tax": 3.8,
    },
    {
        "code": "P1",
        "description": "Passata 700ml x 6",
        "quantity": 1,
        "unit": "CTN",
        "unit_of_measure": "700ml x 6",
        "unit_price_ex_tax": 16.5,
    },
    {
        "code": "E1",
        "description": "FENNEL bulb",
        "quantity": 2,
        "unit": "Each",
        "unit_of_measure": "each",
        "unit_price_ex_tax": 2.5,
    },
]


class FakeLoaded:
    def __init__(self, details):
        self.details = details
        self.puts = []

    units = UNITS

    def get(self, path, *a, **k):
        assert path == "/1.0/stock/internal/units"
        return copy.deepcopy(self.units)

    def invoice(self, invoice_id):
        return copy.deepcopy(self.details[invoice_id])

    def request(self, method, path, body=None):
        self.puts.append((method, path, copy.deepcopy(body)))
        self.details[path.rsplit("/", 1)[-1]] = copy.deepcopy(body)


class TestOneLine:
    def test_the_invoices_size_decides(self):
        v = UC.judge_line({"id": "u-42kg", "name": "4.2 KG"}, READING[1], None, UNITS)
        assert v["kind"] == "correct" and v["proposed"]["id"] == "u-6x700"
        assert "700ml x 6" in v["reason"] and "4.2 KG" in v["reason"]

    def test_agreeing_with_the_invoice_is_not_news(self):
        assert (
            UC.judge_line({"id": "u-each", "name": "Each"}, READING[2], None, UNITS)
            is None
        )

    def test_with_no_size_norms_catalogue_decides(self):
        v = UC.judge_line(
            {"id": "u-kilo", "name": "Kilo"}, READING[0], "100 Grams", UNITS
        )
        assert v["proposed"]["id"] == "u-100g"
        assert v["invoice_says"] == "no size (charged per 'Bunch')"
        assert "Norm's catalogue says" in v["reason"]

    def test_with_nothing_settled_the_line_is_to_confirm(self):
        # No size and no catalogue answer: the unit went in on Norm's unsure
        # estimate. A person confirms it once (owner: never rely on Loaded).
        est = {"unit": "100 Grams", "confidence": "medium", "why": "a bunch is weighed"}
        v = UC.judge_line(
            {"id": "u-100g", "name": "100 Grams"}, READING[0], None, UNITS, estimate=est
        )
        assert v["kind"] == "confirm" and v["proposed"]["id"] == "u-100g"
        assert "wasn't sure" in v["reason"] and "medium confidence" in v["reason"]
        # Norm had no estimate: its pick from the venue's units, to confirm.
        v = UC.judge_line({"id": "u-kilo", "name": "Kilo"}, READING[0], None, UNITS)
        assert v["kind"] == "confirm" and "no estimate for this product" in v["reason"]

    def test_the_invoices_charge_word_settles_it(self):
        line = dict(READING[0], unit="Kilogram")
        assert (
            UC.judge_line({"id": "u-kilo", "name": "Kilo"}, line, None, UNITS) is None
        )
        # A pack count with no size never does ('6 PK' wine was 6x750ml).
        line = dict(READING[0], unit="6 PK")
        assert (
            UC.judge_line({"id": "u-each", "name": "Each"}, line, None, UNITS)["kind"]
            == "confirm"
        )

    def test_an_ordermentum_unit_is_loadeds_not_the_invoices(self):
        line = dict(READING[0], unit="Kilo", unit_from_loaded=True)
        v = UC.judge_line(
            {"id": "u-kilo", "name": "Kilo"}, line, None, UNITS, has_copy=False
        )
        assert v["kind"] == "confirm" and "Ordermentum" in v["invoice_says"]

    def test_a_line_not_found_on_the_invoice_is_left(self):
        assert (
            UC.judge_line({"id": "u-kilo", "name": "Kilo"}, None, None, UNITS) is None
        )

    def test_a_unit_the_venue_lacks_cant_be_proposed(self):
        printed = dict(READING[1], unit_of_measure="12x300g")
        assert (
            UC.judge_line({"id": "u-42kg", "name": "4.2 KG"}, printed, None, UNITS)
            is None
        )

    def test_a_loaded_line_finds_its_printed_line(self):
        used: set[int] = set()
        assert UC.match_printed({"code": "P1"}, READING, used)["code"] == "P1"
        # Loaded's description is the stock item's name once linked: wording
        # plus price finds a code-less line.
        line = {"description": "BASIL bunch", "unitCostExclTax": 3.8}
        assert (
            UC.match_printed(line, [dict(READING[0], code=None)], set())["unit"]
            == "Bunch"
        )


@pytest.fixture(autouse=True)
def _unit_check_on(monkeypatch):
    """conftest switches the check off for the suite (a background thread on
    the real clock); these tests run it on their own fixed clock."""
    from app.config import settings

    monkeypatch.setattr(settings, "UNIT_CHECK_ENABLED", True)


@pytest.fixture()
def world(db_session, venue):
    from app.db.config_models import SupplierInvoiceSpec
    from app.db.models import User
    from app.services import supplier_catalog as sc

    db_session.add(
        SupplierInvoiceSpec(name="Prime Fresh & Co", aliases=[], instructions="x")
    )
    db_session.flush()
    row = sc.SupplierProduct(
        supplier_key="Prime Fresh & Co", code="HB2", description="BASIL bunch"
    )
    db_session.add(row)
    sc.apply_enrichment(
        db_session,
        row,
        unit_name="100 Grams",
        pack_type="fixed",
        unit_type="weight",
        category=None,
        why="a bunch is weighed",
    )
    db_session.add(
        User(
            id="u-owner",
            email="owner@example.com",
            hashed_password="x",
            full_name="Owner",
            role="admin",
        )
    )
    db_session.add(
        InvoiceAutopilotOutcome(
            venue_id=venue.id,
            invoice_id="inv-1",
            reference_number="INZ100",
            outcome="clean",
            received=True,
            mode="autopilot",
            actor="norm",
            created_at=NOW - timedelta(hours=3),
        )
    )
    db_session.add(
        WorkingDocument(
            venue_id=venue.id,
            doc_type="received_invoice",
            connector_name="loadedhub",
            external_ref={"invoice_id": "inv-1", "venue_id": venue.id},
            data={"file_id": "f-1", "extracted_snapshot": {"lines": READING}},
        )
    )
    db_session.flush()
    return {"venue": venue, "lh": FakeLoaded({"inv-1": _detail()})}


def _run(db, world, now=NOW, **kw):
    return UC.run_unit_check(
        now, db=db, config_db=db, clients={world["venue"].id: world["lh"]}, **kw
    )


class TestTheRun:
    def test_a_run_records_proposals_and_emails_the_admins(
        self, db_session, world, monkeypatch
    ):
        sent = []
        monkeypatch.setattr(
            "app.services.email_service.send_system_email",
            lambda t, to, ctx, db, **k: sent.append((t, to, ctx)),
        )
        out = _run(db_session, world)
        assert out["ran"] and out["summary"]["proposals"] == 2
        got = {p.line_id: p for p in db_session.query(UnitCheckProposal).all()}
        assert set(got) == {"l1", "l2"}  # fennel agrees with its invoice
        assert (
            got["l1"].proposed_unit_name == "100 Grams" and got["l1"].status == "open"
        )
        assert got["l2"].proposed_unit_name == "6x700ml"
        assert world["lh"].puts == []  # the check never writes to Loaded
        [(template, to, ctx)] = sent
        assert template == "unit_check" and to == ["owner@example.com"]
        assert ctx["proposals"] == 2 and ctx["link"].endswith("/app?open=unit-check")

    def test_once_a_day_and_not_before_its_hour(self, db_session, world, monkeypatch):
        monkeypatch.setattr(
            "app.services.email_service.send_system_email", lambda *a, **k: None
        )
        assert _run(db_session, world, now=NOW.replace(hour=3)) == {"ran": False}
        assert _run(db_session, world)["ran"]
        assert _run(db_session, world, now=NOW + timedelta(minutes=5))["ran"] is False
        assert db_session.query(UnitCheckRun).count() == 1

    def test_the_next_run_starts_where_the_last_ended(
        self, db_session, world, monkeypatch
    ):
        monkeypatch.setattr(
            "app.services.email_service.send_system_email", lambda *a, **k: None
        )
        _run(db_session, world)
        out = _run(db_session, world, now=NOW + timedelta(days=1))
        # inv-1 was received before the first run: the second checks nothing.
        assert out["ran"] and out["summary"]["lines"] == 0


class TestAPersonDecides:
    def _proposal(self, db, world, monkeypatch, line_id="l1"):
        monkeypatch.setattr(
            "app.services.email_service.send_system_email", lambda *a, **k: None
        )
        _run(db, world)
        return db.query(UnitCheckProposal).filter_by(line_id=line_id).one()

    def test_apply_changes_only_the_lines_unit(self, db_session, world, monkeypatch):
        p = self._proposal(db_session, world, monkeypatch)
        UC.apply_proposal(
            db_session, db_session, p, "owner@example.com", lh=world["lh"]
        )
        assert p.status == "applied" and p.decided_by == "owner@example.com"
        [(method, path, body)] = world["lh"].puts
        assert (method, path) == ("PUT", "/1.0/stock/internal/invoices/inv-1")
        before, after = _detail(), body
        for old, new in zip(before["lines"], after["lines"]):
            changed = {k for k in old if old[k] != new[k]}
            if old["id"] == "l1":
                assert changed == {"unit", "linkedUnitId", "linkedUnitRatio"}
                assert (new["unit"], new["linkedUnitId"]) == ("100 Grams", "u-100g")
            else:
                assert not changed

    def test_a_line_changed_since_is_left_alone(self, db_session, world, monkeypatch):
        p = self._proposal(db_session, world, monkeypatch)
        world["lh"].details["inv-1"]["lines"][0].update(
            linkedUnitId="u-each", unit="Each"
        )
        UC.apply_proposal(
            db_session, db_session, p, "owner@example.com", lh=world["lh"]
        )
        assert p.status == "failed" and "changed it since" in p.error
        assert world["lh"].puts == []

    def test_dismiss_keeps_the_note(self, db_session, world, monkeypatch):
        p = self._proposal(db_session, world, monkeypatch)
        UC.dismiss_proposal(
            db_session, p, "owner@example.com", "  priced per kilo here  "
        )
        assert p.status == "dismissed" and p.note == "priced per kilo here"


def test_a_change_loaded_doesnt_keep_is_an_error():
    class Drops(FakeLoaded):
        def request(self, method, path, body=None):
            self.puts.append((method, path, body))  # Loaded ignores it

    lh = Drops({"inv-1": _detail()})
    with pytest.raises(ReceivedLineUnitError, match="didn't keep"):
        change_received_line_unit(lh, "inv-1", "l1", UNITS[1], from_unit_id="u-kilo")


@pytest.fixture()
def unsure(db_session, world):
    """Mint charged per 'Market' on two invoices, booked 100 Grams on Norm's
    medium estimate — no settled answer in the catalogue."""
    from app.services import supplier_catalog as sc

    venue, lh = world["venue"], world["lh"]
    row = sc.SupplierProduct(
        supplier_key="Prime Fresh & Co", code="MT1", description="MINT market"
    )
    row.evidence = {
        "guess": {
            "unit": "100 Grams",
            "confidence": "medium",
            "why": "a market bunch",
            "settled": False,
            "v": "estimate-v2",
        }
    }
    db_session.add(row)
    printed = {
        "code": "MT1",
        "description": "MINT market",
        "quantity": 1,
        "unit": "Market",
        "unit_of_measure": None,
        "unit_price_ex_tax": 2.5,
    }
    for inv in ("inv-2", "inv-3"):
        lh.details[inv] = {
            "id": inv,
            "referenceNumber": inv.upper(),
            "supplierName": "Prime Fresh & Co",
            "issuedAt": "2026-10-18",
            "isReceived": True,
            "subtotal": 2.5,
            "total": 2.5,
            "lines": [
                {
                    "id": f"{inv}-l1",
                    "code": "MT1",
                    "description": "MINT market",
                    "unit": "100 Grams",
                    "linkedUnitId": "u-100g",
                    "linkedUnitRatio": 0.1,
                    "quantityReceived": 1.0,
                    "unitCostExclTax": 2.5,
                    "totalCostExclTax": 2.5,
                    "linkedItemId": "it-mint",
                }
            ],
        }
        db_session.add(
            InvoiceAutopilotOutcome(
                venue_id=venue.id,
                invoice_id=inv,
                reference_number=inv.upper(),
                outcome="clean",
                received=True,
                mode="autopilot",
                actor="norm",
                created_at=NOW - timedelta(hours=2),
            )
        )
        db_session.add(
            WorkingDocument(
                venue_id=venue.id,
                doc_type="received_invoice",
                connector_name="loadedhub",
                external_ref={"invoice_id": inv, "venue_id": venue.id},
                data={
                    "file_id": f"f-{inv}",
                    "extracted_snapshot": {"lines": [printed]},
                },
            )
        )
    db_session.flush()
    monkey = pytest.MonkeyPatch()
    monkey.setattr("app.services.email_service.send_system_email", lambda *a, **k: None)
    out = _run(db_session, world)
    monkey.undo()
    assert out["summary"]["to_confirm"] == 2 and out["summary"]["corrections"] == 2
    mints = (
        db_session.query(UnitCheckProposal)
        .filter_by(kind="confirm")
        .order_by(UnitCheckProposal.invoice_id)
        .all()
    )
    assert mints[0].norm_estimate["confidence"] == "medium"
    return mints


class TestAPersonConfirms:
    def test_confirm_keeps_the_answer_and_settles_the_product(
        self, db_session, world, unsure
    ):
        from app.services import supplier_catalog as sc

        UC.confirm_proposal(db_session, db_session, unsure[0], "owner@example.com")
        assert [p.status for p in unsure] == ["confirmed", "confirmed"]  # both invoices
        assert world["lh"].puts == []  # nothing changes in Loaded
        answer = sc.catalog_unit_for_line(db_session, "Prime Fresh & Co", "MT1")
        assert answer["unit_name"] == "100 Grams"
        # The next run has nothing left to ask about this product.
        assert (
            UC.judge_line(
                {"id": "u-100g", "name": "100 Grams"},
                unsure[0].printed,
                answer["unit_name"],
                UNITS,
            )
            is None
        )

    def test_change_books_every_line_and_keeps_the_answer(
        self, db_session, world, unsure
    ):
        from app.services import supplier_catalog as sc

        UC.change_proposal(
            db_session,
            db_session,
            unsure[0],
            "u-each",
            "owner@example.com",
            clients={world["venue"].id: world["lh"]},
        )
        assert [p.status for p in unsure] == ["applied", "applied"]
        assert {path for _, path, _ in world["lh"].puts} == {
            "/1.0/stock/internal/invoices/inv-2",
            "/1.0/stock/internal/invoices/inv-3",
        }
        for _, _, body in world["lh"].puts:
            assert body["lines"][0]["linkedUnitId"] == "u-each"
            assert body["lines"][0]["quantityReceived"] == 1.0  # only the unit
        assert (
            sc.catalog_unit_for_line(db_session, "Prime Fresh & Co", "MT1")["unit_name"]
            == "Each"
        )

    def test_a_unit_that_isnt_a_measurement_is_refused(self, db_session, world, unsure):
        world["lh"].units = UNITS + [
            {"id": "u-box", "name": "Box", "ratio": 1, "stockUnitType": "Count"}
        ]
        UC.change_proposal(
            db_session,
            db_session,
            unsure[0],
            "u-box",
            "owner@example.com",
            clients={world["venue"].id: world["lh"]},
        )
        assert unsure[0].status == "failed" and "measurement" in unsure[0].error
        assert world["lh"].puts == []


class TestTheDojoEndpoints:
    def test_admins_only(self, client, manager_headers):
        r = client.get(
            "/api/supplier-invoice-specs/dojo/unit-check", headers=manager_headers
        )
        assert r.status_code == 403

    def test_list_and_dismiss(
        self, client, admin_headers, db_session, world, monkeypatch
    ):
        monkeypatch.setattr(
            "app.services.email_service.send_system_email", lambda *a, **k: None
        )
        _run(db_session, world)
        # The basil line as the receive kept it: the owner changed it on the card.
        doc = db_session.query(WorkingDocument).one()
        doc.data = {
            **doc.data,
            "lines": [{"id": "l1", "unit": "Kilo", "linked_unit_id": "u-kilo"}],
            "edits": [
                {
                    "line_id": "l1",
                    "field": "unit",
                    "before": "100 Grams",
                    "after": "Kilo",
                    "by": "u-owner",
                    "at": "2026-10-19T01:00:00+00:00",
                }
            ],
        }
        db_session.flush()
        r = client.get(
            "/api/supplier-invoice-specs/dojo/unit-check", headers=admin_headers
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["runs"]) == 1 and data["runs"][0]["status_counts"] == {
            "open": 2
        }
        basil = next(p for p in data["proposals"] if p["code"] == "HB2")
        assert (
            basil["booked_unit_name"] == "Kilo"
            and basil["proposed_unit_name"] == "100 Grams"
        )
        # Where 'Kilo' came from, and the copy to show beside the line.
        assert basil["origin"]["text"] == (
            "Owner changed it on the invoice card from '100 Grams'."
        )
        assert basil["file_id"] == "f-1"
        r = client.post(
            f"/api/supplier-invoice-specs/dojo/unit-check/proposals/{basil['id']}/dismiss",
            json={"note": "checked"},
            headers=admin_headers,
        )
        assert r.status_code == 200 and r.json()["status"] == "dismissed"


def test_each_line_says_where_its_unit_came_from():
    """Whose unit the check would undo (owner, 24 Oct 2026): at Mr Murdochs a
    person had changed cases of wine to bottles on the card, and the check
    proposed the case back with nothing to say it was their choice."""
    names = {"u-dianna": "Dianna Fong"}

    def origin(booked="u-750", line=(), read=(), **over):
        received = {
            "id": "l1",
            "code": "W1",
            "unit": "750 mL",
            "linked_unit_id": "u-750",
            "original_unit_id": "u-case",
            **dict(line),
        }
        reading = {
            "code": "W1",
            "unit": "6x750ml",
            "linked_unit_id": "u-case",
            **dict(read),
        }
        data = {"lines": [received], "replica": {"lines": [reading]}, **over}
        p = UnitCheckProposal(line_id="l1", booked_unit_id=booked)
        return UC.unit_origin(p, data, names)

    edit = {
        "line_id": "l1",
        "field": "unit",
        "before": "6x750ml",
        "after": "750 mL",
        "by": "u-dianna",
        "at": "2026-10-21T19:59:38+00:00",
    }
    assert origin(edits=[edit]) == {
        "who": "person",
        "text": "Dianna Fong changed it on the invoice card from '6x750ml'.",
        "at": "2026-10-21T19:59:38+00:00",
    }
    # Changed in Loaded since: Norm's record no longer explains it.
    o = origin(booked="u-litre", edits=[edit])
    assert o["who"] == "loaded" and "received it as '750 mL'" in o["text"]
    suggestion = {
        "id": "line_value:unit:l1",
        "field": "unit",
        "line_id": "l1",
        "proposed": "6x750ml",
        "explanation": "the invoice's delivered unit is 6x750ml",
    }
    turned_down = {
        "suggestion_id": "line_value:unit:l1",
        "action": "dismissed",
        "by": "user",
        "user_id": "u-dianna",
    }
    o = origin(suggestions=[suggestion], suggestion_actions=[turned_down])
    assert o["text"] == (
        "Dianna Fong turned down Norm's suggestion of '6x750ml' and kept '750 mL'."
    )
    norm_did = {
        "suggestion_id": "line_value:unit:l1",
        "action": "accepted",
        "by": "norm",
    }
    o = origin(
        suggestions=[{**suggestion, "proposed": "750 mL", "explanation": "x"}],
        suggestion_actions=[norm_did],
    )
    assert (o["who"], o["text"]) == ("norm", "Norm set it when receiving: x.")
    # Norm's reading decided it.
    o = origin(read={"linked_unit_id": "u-750", "unit_source": "catalogue"})
    assert o["text"] == "Norm's catalogue answer for this product."
    # Loaded's draft, which nothing changed.
    o = origin(line={"original_unit_id": "u-750"})
    assert o["who"] == "loaded" and o["text"] == (
        "Loaded's draft: it was '750 mL' when Norm first saw the invoice, and "
        "nothing changed it (Norm read '6x750ml')."
    )
    p = UnitCheckProposal(line_id="l1", booked_unit_id="u-750")
    assert UC.unit_origin(p, None, names)["who"] == "unknown"


def test_a_venues_bunch_rule_is_its_choice_not_a_correction(db_session, world):
    """The Lott books bunches in its own 'Bunch' unit (a receiving rule, 23 Oct
    2026). Such a line isn't a unit to correct, though Norm's catalogue says
    100 Grams; a venue without the rule still gets the correction."""
    from app.db.models import User
    from app.services import receiving_rules as RR

    bunch = {
        "id": "u-bunch",
        "name": "Bunch",
        "ratio": 1,
        "stockUnitType": "Count",
        "datestampDeleted": "1970-01-01T00:00:00+00:00",
        "hiddenByNorm": "not a measurement",
    }
    lh = world["lh"]
    lh.units = UNITS + [bunch]
    detail = lh.details["inv-1"]
    detail["lines"][0].update(unit="Bunch", linkedUnitId="u-bunch")
    RR.set_rule(
        db_session,
        org_id=world["venue"].organization_id,
        venue_id=world["venue"].id,
        kind="bunch_unit",
        params={"unit": "Bunch"},
        note=None,
        user=db_session.get(User, "u-owner"),
    )
    _run(db_session, world)
    lines = {p.line_id for p in db_session.query(UnitCheckProposal).all()}
    assert "l1" not in lines and "l2" in lines
