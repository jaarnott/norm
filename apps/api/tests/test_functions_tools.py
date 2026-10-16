"""The Functions team member's tools (app/agents/functions_tools.py).

Each tool is called the way the agent loop calls it — the registered handler
with ``(params, db, thread_id)``, params as the model sends them (lists may
arrive as JSON text) — and each write's preview through
``services.previews.preview_call``, which is what puts the approval card in
front of a person.

* Reads: the derived stage, next action and status come back with the record,
  money as text plus cents.
* Writes: the preview is a dry run of the real service — exactly the change,
  nothing kept — and the approved run makes it. Approved minimum spends are
  read-only to Norm; the client's own portal answers are never overwritten.
* Fences: another organisation's records are not found from any tool; staff
  see only their venues; shadow mode reads but never writes; Functions off is
  a refusal; a scheduled run may read but not act (no autonomous runs).
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

import pytest

from app.agents import functions_tools as FT
from app.agents import internal_tools as IT
from app.db.functions_models import (
    FnContact,
    FnEmailDraft,
    FnEnquiry,
    FnEvent,
    FnMinSpend,
    FnPreorderTemplate,
    FnPreorderTemplateItem,
    FnProposal,
    FnReplyTemplate,
    FnThreadFolder,
    FnThreadLink,
)
from app.services import previews
from app.services.functions import enquiries as enquiries_svc
from app.services.functions import events as events_svc
from app.services.functions import min_spends as min_spends_svc
from app.services.functions import reports
from app.services.functions.mailbox import sync
from tests.conftest import _make_thread
from tests.functions_mailbox_fakes import (
    MAILBOX,
    FakeGmail,
    install,
    mailbox_world,
    other_mailbox,
    sessions,
)
from tests.functions_world import add_contact, add_enquiry, make_world

UTC = dt.timezone.utc
CUSTOMER = "casey@clientco.nz"
NEXT_FRIDAY = dt.date.today() + dt.timedelta(days=30)


# ------------------------------------------------------------ helpers ----


@pytest.fixture(autouse=True)
def _config_db(monkeypatch, db_session):
    """The entitlement check reads the catalog from the test's own session
    (never the shared config DB)."""

    @contextmanager
    def same_session():
        yield db_session

    monkeypatch.setattr(FT, "_config_db", same_session)


def thread_for(db, user, intent="functions.tool_use"):
    return _make_thread(
        db, user, domain="functions", intent=intent, status="in_progress"
    )


def call(db, user, tool, intent="functions.tool_use", **params):
    handler = IT.get_handler("norm_functions", tool)
    return handler(params, db, thread_for(db, user, intent).id)


def ok(result) -> dict:
    assert result["success"], result.get("error")
    return result["data"]


def card(db, user, tool, **params):
    """The approval-card outcome for a write (previews.Outcome)."""
    return previews.preview_call(
        "norm_functions", tool, params, None, db, thread_for(db, user).id
    )


def rows(outcome) -> dict:
    return {r["field"]: (r.get("before"), r["after"]) for r in outcome.card["changes"]}


def enquiry(db, w, venue=None, **kw):
    contact = add_contact(db, w.org.id, email=kw.pop("email", CUSTOMER))
    return add_enquiry(
        db,
        w.org.id,
        contact.id,
        (venue or w.venue).id,
        subject=kw.pop("subject", "Casey's 40th"),
        **kw,
    )


# ------------------------------------------------------------- reads ----


class TestFindEnquiries:
    def test_rows_carry_derived_stage_next_action_and_value(self, db_session):
        w = make_world(db_session)
        e = enquiry(db_session, w, guests=60, booking_type="large_event_exclusive")
        min_spends_svc.add_min_spend(
            db_session,
            w.ctx(),
            enquiry_id=e.id,
            venue_id=w.venue.id,
            amount_cents=550000,
            area_id=w.snug.id,
        )
        data = ok(call(db_session, w.staff, "find_enquiries"))
        (row,) = [r for r in data["enquiries"] if r["id"] == e.id]
        assert row["stage"] == "new"
        assert row["next_action"] == "none"
        assert row["venue"] == "Brew Bar" and row["guests"] == 60
        assert (row["value"], row["value_cents"]) == ("$5,500", 550000)
        assert data["money"]["currency"] == "NZD"

    def test_list_filters_work_as_the_model_sends_them(self, db_session):
        w = make_world(db_session)
        mine = enquiry(db_session, w, booking_type="group_booking")
        enquiries_svc.close_enquiry(
            db_session, w.ctx(), enquiry(db_session, w, email="x@y.nz").id, reason="No"
        )
        for stage in ('["new"]', "new,replied", ["new"]):
            data = ok(call(db_session, w.staff, "find_enquiries", stage=stage))
            assert [r["id"] for r in data["enquiries"]] == [mine.id]
        data = ok(
            call(
                db_session, w.staff, "find_enquiries", booking_type='["seated_dining"]'
            )
        )
        assert data["enquiries"] == []

    def test_unknown_values_are_refused_with_the_choices(self, db_session):
        w = make_world(db_session)
        out = call(db_session, w.staff, "find_enquiries", stage="open")
        assert not out["success"] and "new, replied" in out["error"]


class TestGetEnquiry:
    def test_options_say_who_must_approve_and_what_norm_may_edit(self, db_session):
        w = make_world(db_session)
        e = enquiry(db_session, w)
        deck = min_spends_svc.add_min_spend(
            db_session,
            w.ctx(),
            enquiry_id=e.id,
            venue_id=w.venue.id,
            amount_cents=900000,
            area_id=w.deck.id,
            label="The Deck",
        )
        min_spends_svc.add_min_spend(
            db_session,
            w.ctx(),
            enquiry_id=e.id,
            venue_id=w.venue.id,
            amount_cents=300000,
            area_id=w.snug.id,
            label="The Snug",
        )
        data = ok(call(db_session, w.staff, "get_enquiry", enquiry_id=e.id))
        by = {o["label"]: o for o in data["options"]}
        assert "needs approval from Andy Approver" in by["The Deck"]["approval"]
        assert by["The Deck"]["editable_by_norm"] is True
        assert "no approval needed" in by["The Snug"]["approval"]
        assert data["link"].endswith(f"enquiry={e.id}")

        min_spends_svc.approve_min_spend(db_session, w.ctx(w.approver), deck.id)
        data = ok(call(db_session, w.staff, "get_enquiry", enquiry_id=e.id))
        deck_row = next(o for o in data["options"] if o["label"] == "The Deck")
        assert deck_row["state"] == "approved" and deck_row["editable_by_norm"] is False

    def test_found_by_contact_email(self, db_session):
        w = make_world(db_session)
        e = enquiry(db_session, w)
        data = ok(
            call(db_session, w.staff, "get_enquiry", contact_email=CUSTOMER.upper())
        )
        assert data["id"] == e.id
        assert data["contact"]["emails"] == [CUSTOMER]


class TestEvents:
    def _booked(self, db, w):
        e = enquiry(
            db,
            w,
            booking_type="seated_dining",
            guests=20,
            preferred_date_start=NEXT_FRIDAY,
        )
        event, _ = events_svc.confirm_booking(db, w.ctx(), e.id, channel="phone")
        event.allergies = "Two coeliacs"
        db.flush()
        return e, event

    def test_find_and_get_with_status_money_and_allergies_withheld(self, db_session):
        w = make_world(db_session)
        _e, event = self._booked(db_session, w)
        data = ok(call(db_session, w.staff, "find_events", date_from=str(NEXT_FRIDAY)))
        (row,) = [r for r in data["events"] if r["id"] == event.id]
        assert row["status"] == "booked" and row["guests"] == 20
        assert data["totals"]["events"] == 1

        detail = ok(call(db_session, w.staff, "get_event", event_id=event.id))
        assert detail["status"] == "booked"
        assert "Two coeliacs" not in str(detail)
        assert detail["sections"]["food"] == {"state": "waiting"}
        asked = ok(
            call(
                db_session,
                w.staff,
                "get_event",
                event_id=event.id,
                include_allergies=True,
            )
        )
        assert asked["food"]["allergies"] == "Two coeliacs"

    def test_printed_sheets_come_from_the_print_service(self, db_session):
        w = make_world(db_session)
        _e, event = self._booked(db_session, w)
        sheet = ok(
            call(db_session, w.staff, "get_event", event_id=event.id, view="full")
        )
        assert sheet["kind"] == "full" and sheet["event"]["name"] == event.name
        assert "Two coeliacs" not in str(sheet)


class TestVenueProfile:
    def test_areas_templates_approvers_and_sales_reference(
        self, db_session, monkeypatch
    ):
        w = make_world(db_session)
        db_session.add_all(
            [
                FnReplyTemplate(
                    organization_id=w.org.id,
                    venue_id=w.venue.id,
                    booking_type="group_booking",
                    stage="first_reply",
                    name="Group first reply",
                    body_html="<p>Hi [NAME]</p>",
                ),
                FnReplyTemplate(
                    organization_id=w.org.id,
                    venue_id=w.venue.id,
                    booking_type="seated_dining",
                    stage="first_reply",
                    name="Dining first reply",
                    body_html="<p>Hi</p>",
                ),
            ]
        )
        tpl = FnPreorderTemplate(
            organization_id=w.org.id,
            venue_id=w.venue.id,
            name="Canapés",
            is_set_menu=False,
        )
        db_session.add(tpl)
        db_session.flush()
        db_session.add(
            FnPreorderTemplateItem(
                organization_id=w.org.id,
                template_id=tpl.id,
                kind="item",
                name="Arancini",
                unit_price_cents=450,
            )
        )
        db_session.flush()
        monkeypatch.setattr(
            reports,
            "loaded_call",
            lambda *a, **k: ({"rows": [{"budget": 8250.5, "last_year": 7400}]}, None),
        )
        data = ok(
            call(
                db_session,
                w.staff,
                "get_venue_profile",
                venue="brew bar",
                booking_type="group_booking",
                stage="first_reply",
                date=str(NEXT_FRIDAY),
            )
        )
        assert data["venue"]["name"] == "Brew Bar"
        assert [r["name"] for r in data["reply_templates"]] == ["Group first reply"]
        assert data["approvers"]["minimum_spend_approver"] == "Andy Approver"
        assert {a["name"] for a in data["areas"]} == {"Deck", "Snug"}
        assert data["menus"][0]["dishes"][0] == {"dish": "Arancini", "price": "$4.50"}
        assert data["sales_reference"]["budget"] == "$8,250.50"


# ------------------------------------------------------------ writes ----


class TestRecordEnquiry:
    def test_create_previews_without_writing_then_creates(self, db_session):
        w = make_world(db_session)
        before = db_session.query(FnEnquiry).filter_by(organization_id=w.org.id).count()
        out = card(
            db_session,
            w.staff,
            "record_enquiry",
            action="create",
            contact_name="Pat Planner",
            contact_email="pat@example.com",
            venue="Brew Bar",
            booking_type="group_booking",
            guests=30,
            date_from=str(NEXT_FRIDAY),
            subject="Pat's farewell",
        )
        assert out.kind == "card", out.result
        assert rows(out)["Customer"][1].endswith("(new contact)")
        assert rows(out)["Booking type"][1] == "Group booking"
        assert (
            db_session.query(FnEnquiry).filter_by(organization_id=w.org.id).count()
            == before
        )
        assert (
            db_session.query(FnContact)
            .filter_by(organization_id=w.org.id, name="Pat Planner")
            .count()
            == 0
        )

        data = ok(
            call(
                db_session,
                w.staff,
                "record_enquiry",
                action="create",
                contact_name="Pat Planner",
                contact_email="pat@example.com",
                venue="Brew Bar",
                booking_type="group_booking",
                guests="30",
                date_from=str(NEXT_FRIDAY),
                subject="Pat's farewell",
            )
        )
        made = db_session.get(FnEnquiry, data["enquiry_id"])
        assert (made.guests, made.booking_type, made.source) == (
            30,
            "group_booking",
            "agent",
        )
        assert made.created_by_label == "Norm" and made.created_by_user_id == w.staff.id
        assert data["stage"] == "new"

    def test_update_shows_before_and_after_and_clears_with_empty(self, db_session):
        w = make_world(db_session)
        e = enquiry(db_session, w, guests=40, follow_up_date=NEXT_FRIDAY)
        out = card(
            db_session,
            w.staff,
            "record_enquiry",
            action="update",
            enquiry_id=e.id,
            guests=55,
            follow_up_date="",
        )
        assert rows(out) == {
            "Guests": ("40", "55"),
            "Follow-up date": (str(NEXT_FRIDAY), "—"),
        }
        assert db_session.get(FnEnquiry, e.id).guests == 40
        nothing = card(
            db_session,
            w.staff,
            "record_enquiry",
            action="update",
            enquiry_id=e.id,
            guests=40,
        )
        assert nothing.kind == "done"

        ok(
            call(
                db_session,
                w.staff,
                "record_enquiry",
                action="update",
                enquiry_id=e.id,
                guests=55,
                follow_up_date="",
            )
        )
        db_session.refresh(e)
        assert (e.guests, e.follow_up_date) == (55, None)

    def test_close_and_reopen_need_a_reason_and_reopen_carries_the_rule(
        self, db_session
    ):
        w = make_world(db_session)
        e = enquiry(db_session, w)
        out = call(
            db_session, w.staff, "record_enquiry", action="close", enquiry_id=e.id
        )
        assert not out["success"] and "reason" in out["error"].lower()
        assert (
            ok(
                call(
                    db_session,
                    w.staff,
                    "record_enquiry",
                    action="close",
                    enquiry_id=e.id,
                    reason="Went elsewhere",
                )
            )["stage"]
            == "lost"
        )
        out = card(
            db_session,
            w.staff,
            "record_enquiry",
            action="reopen",
            enquiry_id=e.id,
            reason="They want 5 Dec",
        )
        assert rows(out)["Stage"] == ("lost", "new")
        assert any("Reopen only when" in x for x in out.card["warnings"])

    def test_confirming_an_approved_option_books_the_event(self, db_session):
        w = make_world(db_session)
        e = enquiry(db_session, w, booking_type="large_event_exclusive", guests=80)
        deck = min_spends_svc.add_min_spend(
            db_session,
            w.ctx(),
            enquiry_id=e.id,
            venue_id=w.venue.id,
            amount_cents=900000,
            area_id=w.deck.id,
            label="The Deck",
            event_date=NEXT_FRIDAY,
        )
        snug = min_spends_svc.add_min_spend(
            db_session,
            w.ctx(),
            enquiry_id=e.id,
            venue_id=w.venue.id,
            amount_cents=300000,
            area_id=w.snug.id,
            label="The Snug",
            event_date=NEXT_FRIDAY,
        )
        refused = card(
            db_session,
            w.staff,
            "record_enquiry",
            action="confirm",
            enquiry_id=e.id,
            option_id=deck.id,
            channel="email",
        )
        assert refused.kind == "refused"

        min_spends_svc.approve_min_spend(db_session, w.ctx(w.approver), deck.id)
        out = card(
            db_session,
            w.staff,
            "record_enquiry",
            action="confirm",
            enquiry_id=e.id,
            option_id=deck.id,
            channel="email",
        )
        assert out.kind == "card"
        assert rows(out)["Minimum spend"][1] == "$9,000"
        assert any("The Snug" in x for x in out.card["warnings"])
        assert db_session.query(FnEvent).filter_by(enquiry_id=e.id).count() == 0

        data = ok(
            call(
                db_session,
                w.staff,
                "record_enquiry",
                action="confirm",
                enquiry_id=e.id,
                option_id=deck.id,
                channel="email",
            )
        )
        assert data["stage"] == "confirmed" and data["created"] is True
        assert (
            min_spends_svc.min_spend_state(db_session, w.ctx(), snug.id) == "withdrawn"
        )
        again = card(
            db_session,
            w.staff,
            "record_enquiry",
            action="confirm",
            enquiry_id=e.id,
            option_id=deck.id,
            channel="email",
        )
        assert again.kind == "done"

    def test_decline_is_recorded(self, db_session):
        w = make_world(db_session)
        e = enquiry(db_session, w)
        snug = min_spends_svc.add_min_spend(
            db_session,
            w.ctx(),
            enquiry_id=e.id,
            venue_id=w.venue.id,
            amount_cents=300000,
            area_id=w.snug.id,
        )
        ok(
            call(
                db_session,
                w.staff,
                "record_enquiry",
                action="decline",
                enquiry_id=e.id,
                option_id=snug.id,
                channel="phone",
            )
        )
        assert (
            min_spends_svc.min_spend_state(db_session, w.ctx(), snug.id) == "declined"
        )


class TestRecordEvent:
    def test_staff_fields_change_and_the_clients_answers_are_refused(self, db_session):
        w = make_world(db_session)
        e = enquiry(
            db_session,
            w,
            booking_type="seated_dining",
            preferred_date_start=NEXT_FRIDAY,
        )
        event, _ = events_svc.confirm_booking(
            db_session, w.ctx(), e.id, channel="phone"
        )
        out = card(
            db_session,
            w.staff,
            "record_event",
            action="update",
            event_id=event.id,
            deposit="500",
            area="Snug",
            payment_method="Invoice",
        )
        assert rows(out)["Deposit"] == ("—", "$500")
        assert rows(out)["Area"][1] == "Snug"
        ok(
            call(
                db_session,
                w.staff,
                "record_event",
                action="update",
                event_id=event.id,
                deposit=500,
                area="Snug",
            )
        )
        db_session.refresh(event)
        assert (event.deposit_cents, event.area_id) == (50000, w.snug.id)

        refused = call(
            db_session,
            w.staff,
            "record_event",
            action="update",
            event_id=event.id,
            guests=99,
        )
        assert not refused["success"] and "client's own answers" in refused["error"]

    def test_cancel_closes_the_enquiry_and_skip_needs_a_note(self, db_session):
        w = make_world(db_session)
        e = enquiry(
            db_session,
            w,
            booking_type="seated_dining",
            preferred_date_start=NEXT_FRIDAY,
        )
        event, _ = events_svc.confirm_booking(
            db_session, w.ctx(), e.id, channel="phone"
        )
        assert not call(
            db_session,
            w.staff,
            "record_event",
            action="skip_section",
            event_id=event.id,
            section="food",
        )["success"]
        ok(
            call(
                db_session,
                w.staff,
                "record_event",
                action="skip_section",
                event_id=event.id,
                section="food",
                note="Canapés on the night",
            )
        )
        out = card(
            db_session,
            w.staff,
            "record_event",
            action="cancel",
            event_id=event.id,
            reason="Venue flooded",
        )
        assert rows(out)["Status"] == ("booked", "cancelled")
        assert any("closed as lost" in x for x in out.card["warnings"])
        assert (
            ok(
                call(
                    db_session,
                    w.staff,
                    "record_event",
                    action="cancel",
                    event_id=event.id,
                    reason="Venue flooded",
                )
            )["status"]
            == "cancelled"
        )
        assert enquiries_svc.enquiry_state(db_session, w.ctx(), e.id)[0] == "lost"


class TestBuildProposal:
    def test_options_menus_document_and_the_money_check(self, db_session):
        w = make_world(db_session)
        e = enquiry(db_session, w, booking_type="large_event_exclusive", guests=60)
        tpl = FnPreorderTemplate(
            organization_id=w.org.id,
            venue_id=w.venue.id,
            name="Canapés",
            is_set_menu=False,
        )
        db_session.add(tpl)
        db_session.flush()
        db_session.add(
            FnPreorderTemplateItem(
                organization_id=w.org.id,
                template_id=tpl.id,
                kind="item",
                name="Arancini",
                unit_price_cents=450,
            )
        )
        db_session.flush()
        data = ok(
            call(
                db_session,
                w.staff,
                "build_proposal",
                enquiry_id=e.id,
                options='[{"area": "Deck", "amount": 5500, "date": "%s", "start_time": "19:00", "end_time": "23:30", "label": "The Deck"}]'
                % NEXT_FRIDAY,
                menus=[
                    {
                        "template_id": tpl.id,
                        "guest_count": 60,
                        "quantities": {"arancini": 120},
                    }
                ],
                include_text=True,
            )
        )
        (option,) = data["options"]
        assert option["minimum_spend_cents"] == 550000 and option["state"] == "proposed"
        assert data["menus"][0]["value"] == "$540"
        assert data["proposal"]["exists"] is True
        assert any("isn't approved yet" in x for x in data["check"]["warnings"])
        assert "text" in data

        proposal = db_session.query(FnProposal).filter_by(enquiry_id=e.id).one()
        assert proposal.updated_by_label == "Norm"
        data = ok(
            call(
                db_session,
                w.staff,
                "build_proposal",
                enquiry_id=e.id,
                replacements=[{"find": "$5,500", "replace": "$7,777"}],
            )
        )
        assert any("$7,777" in x for x in data["check"]["warnings"])
        missing = call(
            db_session,
            w.staff,
            "build_proposal",
            enquiry_id=e.id,
            replacements=[{"find": "no such text", "replace": "x"}],
        )
        assert not missing["success"] and "Couldn't find" in missing["error"]

    def test_approved_options_are_read_only_to_norm(self, db_session):
        w = make_world(db_session)
        e = enquiry(db_session, w)
        deck = min_spends_svc.add_min_spend(
            db_session,
            w.ctx(),
            enquiry_id=e.id,
            venue_id=w.venue.id,
            amount_cents=600000,
            area_id=w.deck.id,
            label="Deck",
        )
        min_spends_svc.approve_min_spend(db_session, w.ctx(w.approver), deck.id)
        for change in (
            {"option_id": deck.id, "amount": 7500},
            {"option_id": deck.id, "withdraw": True},
        ):
            out = call(
                db_session, w.staff, "build_proposal", enquiry_id=e.id, options=[change]
            )
            assert not out["success"] and "read-only to Norm" in out["error"]
        db_session.refresh(deck)
        assert (deck.amount_cents, deck.withdrawn_at) == (600000, None)
        assert (
            min_spends_svc.min_spend_state(db_session, w.ctx(), deck.id) == "approved"
        )

    def test_a_new_option_must_name_its_area_and_nothing_is_half_built(
        self, db_session
    ):
        w = make_world(db_session)
        e = enquiry(db_session, w)
        out = call(
            db_session,
            w.staff,
            "build_proposal",
            enquiry_id=e.id,
            options=[{"area": "Snug", "amount": 3000}, {"amount": 9000}],
        )
        assert not out["success"] and "names its area" in out["error"]
        assert db_session.query(FnMinSpend).filter_by(enquiry_id=e.id).count() == 0
        assert db_session.query(FnProposal).filter_by(enquiry_id=e.id).count() == 0


class TestDraftEmail:
    def test_a_new_email_about_an_enquiry_goes_to_the_contact(self, db_session):
        w = make_world(db_session)
        e = enquiry(db_session, w)
        data = ok(
            call(
                db_session,
                w.staff,
                "draft_email",
                enquiry_id=e.id,
                body="<p>Hi Casey, the deck is $9,000.</p>",
            )
        )
        draft = db_session.get(FnEmailDraft, data["draft_id"])
        assert (draft.to_emails, draft.subject, draft.owner, draft.status) == (
            [CUSTOMER],
            "Casey's 40th",
            "norm",
            "open",
        )
        assert draft.created_by_label == "Norm"
        assert any("quotes a price" in x for x in data["warnings"])
        assert any("No connected venue mailbox" in x for x in data["warnings"])

        ok(
            call(
                db_session,
                w.staff,
                "draft_email",
                draft_id=draft.id,
                body="<p>Hi Casey, which date suits?</p>",
            )
        )
        db_session.refresh(draft)
        assert "which date" in draft.body_html
        assert (
            ok(
                call(
                    db_session, w.staff, "draft_email", draft_id=draft.id, discard=True
                )
            )["status"]
            == "discarded"
        )

    def test_template_names_are_filled_and_blanks_reported(self, db_session):
        w = make_world(db_session)
        e = enquiry(db_session, w)
        body = "<p>Hi {{client_name}}, booked for [DATE].</p><p>{{ sender_name }} {{quote_link}} [SPEND — check with James]</p>"
        data = ok(call(db_session, w.staff, "draft_email", enquiry_id=e.id, body=body))
        draft = db_session.get(FnEmailDraft, data["draft_id"])
        assert (
            "Hi Casey," in draft.body_html and "Sam {{quote_link}}" in draft.body_html
        )
        (warning,) = [x for x in data["warnings"] if "Unfilled blanks" in x]
        assert "[DATE]" in warning and "[SPEND — check with James]" in warning
        assert "quote_link" not in warning

    def test_a_proposal_draft_needs_an_enquiry(self, db_session):
        w = make_world(db_session)
        out = call(
            db_session, w.staff, "draft_email", kind="proposal", body="Here it is"
        )
        assert not out["success"] and "enquiry" in out["error"]


# ---------------------------------------------------------------- mail ----


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeGmail()
    install(monkeypatch, fake)
    return fake


def seed_mail(db, w, fake):
    fake.add_message(
        frm=f"Casey Client <{CUSTOMER}>",
        to=[MAILBOX],
        subject="Wedding in March",
        body="Can we book the deck for 80 people?",
        thread_id="t-casey",
        message_id="<casey@clientco.nz>",
        when=dt.datetime.now(UTC) - dt.timedelta(hours=30),
    )
    fake.add_message(
        frm="Fresh Produce <orders@produce.co.nz>",
        to=[MAILBOX],
        subject="Invoice 42",
        thread_id="t-supplier",
        when=dt.datetime.now(UTC) - dt.timedelta(hours=3),
    )
    out = sync.sync_account(w.account.id, session_factory=sessions(db))
    assert out.get("ok"), out
    db.expire_all()


class TestThreads:
    def test_find_count_and_read(self, db_session, fake):
        w = mailbox_world(db_session)
        seed_mail(db_session, w, fake)
        data = ok(
            call(db_session, w.staff, "find_threads", folder="unfiled", count=True)
        )
        assert data["total"] == 2
        casey = next(t for t in data["threads"] if t["gmail_thread_id"] == "t-casey")
        assert casey["awaiting_reply"] is True and casey["folder"] == "unfiled"

        thread = ok(
            call(
                db_session,
                w.staff,
                "read_thread",
                account_id=w.account.id,
                gmail_thread_id="t-casey",
                detail="full",
            )
        )
        (message,) = thread["messages"]
        assert message["direction"] == "from customer"
        assert "80 people" in message["text"]
        digest = ok(
            call(
                db_session,
                w.staff,
                "read_thread",
                account_id=w.account.id,
                gmail_thread_id="t-casey",
            )
        )
        assert "text" not in digest["messages"][0] and digest["messages"][0]["snippet"]

    def test_file_previews_without_writing_then_files_links_and_creates(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        seed_mail(db_session, w, fake)
        out = card(
            db_session,
            w.staff,
            "file_thread",
            account_id=w.account.id,
            gmail_thread_id="t-casey",
            folder="enquiry",
            create_enquiry=True,
            booking_type="large_event_exclusive",
        )
        assert out.kind == "card", out.result
        assert rows(out)["Folder"] == ("unfiled", "enquiry")
        assert CUSTOMER in rows(out)["New enquiry for"][1]
        assert (
            db_session.query(FnThreadFolder)
            .filter_by(gmail_thread_id="t-casey")
            .count()
            == 0
        )
        assert fake.calls("POST", "/modify") == []

        data = ok(
            call(
                db_session,
                w.staff,
                "file_thread",
                account_id=w.account.id,
                gmail_thread_id="t-casey",
                folder="enquiry",
                create_enquiry=True,
                booking_type="large_event_exclusive",
            )
        )
        assert data["folder"] == "enquiry" and data["gmail_label_updated"] is True
        made = db_session.get(FnEnquiry, data["enquiry_id"])
        assert made.booking_type == "large_event_exclusive"
        link = db_session.query(FnThreadLink).filter_by(gmail_thread_id="t-casey").one()
        assert link.enquiry_id == made.id and link.linked_by_label == "Norm"

        again = card(
            db_session,
            w.staff,
            "file_thread",
            account_id=w.account.id,
            gmail_thread_id="t-casey",
            folder="enquiry",
        )
        assert again.kind == "done"

    def test_relinking_ends_the_old_link_and_keeps_the_new(self, db_session, fake):
        w = mailbox_world(db_session)
        seed_mail(db_session, w, fake)
        old = enquiry(db_session, w)
        new = enquiry(db_session, w, email="pat@example.com", subject="Pat's farewell")
        enquiries_svc.link_thread(
            db_session,
            w.ctx(),
            account_id=w.account.id,
            gmail_thread_id="t-casey",
            enquiry_id=old.id,
        )
        out = card(
            db_session,
            w.staff,
            "file_thread",
            account_id=w.account.id,
            gmail_thread_id="t-casey",
            unlink=True,
            link_enquiry_id=new.id,
        )
        assert rows(out)["Unlink from"][1] == "enquiry “Casey's 40th”"
        data = ok(
            call(
                db_session,
                w.staff,
                "file_thread",
                account_id=w.account.id,
                gmail_thread_id="t-casey",
                unlink=True,
                link_enquiry_id=new.id,
            )
        )
        assert data["linked_enquiry_id"] == new.id and data["unlinked"] == 1
        live = (
            db_session.query(FnThreadLink)
            .filter_by(gmail_thread_id="t-casey", unlinked_at=None)
            .all()
        )
        assert [lk.enquiry_id for lk in live] == [new.id]

    def test_a_draft_can_be_handed_to_gmail_and_is_then_locked(self, db_session, fake):
        w = mailbox_world(db_session)
        seed_mail(db_session, w, fake)
        e = enquiry(db_session, w)
        enquiries_svc.link_thread(
            db_session,
            w.ctx(),
            account_id=w.account.id,
            gmail_thread_id="t-casey",
            enquiry_id=e.id,
        )
        data = ok(
            call(
                db_session,
                w.staff,
                "draft_email",
                enquiry_id=e.id,
                body="<p>Which date suits?</p>",
                hand_to_gmail=True,
            )
        )
        assert data["in_gmail"] is True and fake.drafts and fake.sent == []
        locked = call(
            db_session,
            w.staff,
            "draft_email",
            draft_id=data["draft_id"],
            body="changed",
        )
        assert not locked["success"] and "Gmail" in locked["error"]

    def test_a_reply_draft_takes_its_recipients_from_the_thread(self, db_session, fake):
        w = mailbox_world(db_session)
        seed_mail(db_session, w, fake)
        e = enquiry(db_session, w)
        enquiries_svc.link_thread(
            db_session,
            w.ctx(),
            account_id=w.account.id,
            gmail_thread_id="t-casey",
            enquiry_id=e.id,
        )
        data = ok(
            call(
                db_session,
                w.staff,
                "draft_email",
                enquiry_id=e.id,
                body="<p>Yes, the deck is free.</p>",
            )
        )
        assert data["reply_on_thread"] == "t-casey"
        assert data["to"] == [CUSTOMER] and data["subject"] == "Re: Wedding in March"
        assert data["mailbox"] == MAILBOX and fake.sent == []


# ------------------------------------------------------------- fences ----


class TestFences:
    def test_another_organisation_finds_nothing(self, db_session, fake):
        w = mailbox_world(db_session)
        seed_mail(db_session, w, fake)
        theirs = other_mailbox(db_session, w)
        e = enquiry(db_session, w)
        draft = ok(
            call(db_session, w.staff, "draft_email", enquiry_id=e.id, body="Hello")
        )["draft_id"]
        rival = w.other_user
        for tool, params in (
            ("get_enquiry", {"enquiry_id": e.id}),
            ("record_enquiry", {"action": "update", "enquiry_id": e.id, "guests": 2}),
            ("build_proposal", {"enquiry_id": e.id}),
            ("read_thread", {"account_id": w.account.id, "gmail_thread_id": "t-casey"}),
            (
                "file_thread",
                {
                    "account_id": w.account.id,
                    "gmail_thread_id": "t-casey",
                    "folder": "other",
                },
            ),
            ("draft_email", {"draft_id": draft, "body": "mine now"}),
            ("send_email", {"draft_id": draft, "idempotency_key": "k"}),
        ):
            out = call(db_session, rival, tool, **params)
            assert not out["success"] and "not found" in out["error"].lower(), (
                tool,
                out,
            )
        assert ok(call(db_session, rival, "find_enquiries"))["enquiries"] == []
        rival_threads = ok(call(db_session, rival, "find_threads"))["threads"]
        assert all(t["account_id"] == theirs.account.id for t in rival_threads)
        out = card(
            db_session,
            rival,
            "record_enquiry",
            action="update",
            enquiry_id=e.id,
            guests=2,
        )
        assert out.kind == "refused"
        assert db_session.get(FnEnquiry, e.id).guests is None

    def test_staff_see_only_their_venues(self, db_session):
        w = make_world(db_session)
        garden = enquiry(db_session, w, venue=w.venue2, email="g@x.nz")
        out = call(db_session, w.staff, "get_enquiry", enquiry_id=garden.id)
        assert not out["success"] and "not found" in out["error"].lower()
        assert garden.id not in [
            r["id"]
            for r in ok(call(db_session, w.staff, "find_enquiries"))["enquiries"]
        ]
        out = call(db_session, w.staff, "find_enquiries", venue="Garden Bar")
        assert not out["success"] and "No venue called 'Garden Bar'" in out["error"]
        assert garden.id in [
            r["id"]
            for r in ok(call(db_session, w.owner, "find_enquiries"))["enquiries"]
        ]

    def test_shadow_mode_reads_and_never_writes(self, db_session):
        w = make_world(db_session, mode="shadow")
        e = enquiry(db_session, w)
        assert (
            ok(call(db_session, w.staff, "get_enquiry", enquiry_id=e.id))["stage"]
            == "new"
        )
        out = card(
            db_session,
            w.staff,
            "record_enquiry",
            action="update",
            enquiry_id=e.id,
            guests=9,
        )
        assert out.kind == "refused" and "shadow" in out.result["error"]
        for tool, params in (
            ("record_enquiry", {"action": "update", "enquiry_id": e.id, "guests": 9}),
            ("draft_email", {"enquiry_id": e.id, "body": "Hi"}),
            ("build_proposal", {"enquiry_id": e.id}),
        ):
            out = call(db_session, w.staff, tool, **params)
            assert not out["success"] and "shadow" in out["error"], tool

    def test_functions_off_is_a_refusal(self, db_session):
        w = make_world(db_session, mode="off")
        out = call(db_session, w.staff, "find_enquiries")
        assert not out["success"] and out["error"] == FT.OFF

    def test_a_scheduled_run_may_read_but_not_act(self, db_session):
        w = make_world(db_session)
        e = enquiry(db_session, w)
        run = "functions.automated_task"
        assert call(db_session, w.staff, "find_enquiries", intent=run)["success"]
        for tool, params in (
            ("record_enquiry", {"action": "update", "enquiry_id": e.id, "guests": 9}),
            ("draft_email", {"enquiry_id": e.id, "body": "Hi"}),
            ("build_proposal", {"enquiry_id": e.id}),
        ):
            out = call(db_session, w.staff, tool, intent=run, **params)
            assert out["error"] == FT.UNATTENDED, tool
        assert IT.get_previewer("norm_functions", "record_enquiry")(
            {"action": "update", "enquiry_id": e.id, "guests": 9},
            db_session,
            thread_for(db_session, w.staff, run).id,
        ) == {"error": FT.UNATTENDED}
