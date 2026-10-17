"""Norm Functions core services: approvals, the customer's confirmation,
events and their sections, menus, contacts and enquiries.

The confirmation tests carry the owner's rule: a customer confirming a
minimum-spend option creates the event — exactly once, only for an approved
option (or one in an area that needs no approval), linking the option and the
food rather than copying them. Approval itself never confirms anything.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.functions_models import (
    FnEnquiry,
    FnEvent,
    FnMinSpend,
    FnMinSpendResponse,
    FnPreorderMenu,
    FnPreorderTemplate,
    FnPreorderTemplateItem,
)
from app.services.functions import (
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    contacts,
    enquiries,
    events,
    menus,
    min_spends,
    status,
)
from app.services.functions.ctx import for_client, for_org, for_quote
from tests.functions_world import add_contact, make_world

DATE = dt.date(2026, 12, 4)


@pytest.fixture
def w(db_session):
    return make_world(db_session)


def _enquiry(w, *, venue=None, booking_type="large_event_exclusive", **kw):
    return enquiries.create_enquiry(
        w.db,
        w.ctx(),
        contact={"name": "Casey Client", "email": "casey@example.com"},
        venue_id=(venue or w.venue).id,
        booking_type=booking_type,
        guests=60,
        subject="Casey's 40th",
        **kw,
    )


def _option(w, enquiry, *, area=None, amount=500_000, date=DATE, **kw):
    return min_spends.add_min_spend(
        w.db,
        w.ctx(),
        enquiry_id=enquiry.id,
        venue_id=w.venue.id,
        area_id=(area or w.deck).id,
        amount_cents=amount,
        event_date=date,
        start_time=dt.time(19, 0),
        end_time=dt.time(1, 0),
        ends_next_day=True,
        **kw,
    )


def _template(w, *, set_menu=True):
    t = FnPreorderTemplate(
        organization_id=w.org.id,
        venue_id=w.venue.id,
        name="Canapés" if set_menu else "Platters",
        is_set_menu=set_menu,
        price_per_person_cents=4500 if set_menu else None,
    )
    w.db.add(t)
    w.db.flush()
    rows = [
        ("header", "Hot", None, None),
        ("item", "Arancini", 50, None if set_menu else 1500),
        ("item", "Sliders", 50, None if set_menu else 2200),
        ("header", "Cold", None, None),
        ("item", "Oysters", 34, None if set_menu else 3000),
        ("item", "Tartlets", 33, None if set_menu else 1200),
        ("item", "Crudo", 33, None if set_menu else 1800),
    ]
    for i, (kind, name, pct, price) in enumerate(rows):
        w.db.add(
            FnPreorderTemplateItem(
                organization_id=w.org.id,
                template_id=t.id,
                kind=kind,
                name=name,
                default_percent=pct,
                unit_price_cents=price,
                sort_order=i,
            )
        )
    w.db.flush()
    return t


# ===========================================================================
# Approval rules
# ===========================================================================


class TestApprovals:
    def test_only_the_venue_approver_approves(self, w):
        opt = _option(w, _enquiry(w))
        with pytest.raises(Forbidden):
            min_spends.approve_min_spend(w.db, w.ctx(), opt.id)
        with pytest.raises(Forbidden):  # owners get no approval powers either
            min_spends.approve_min_spend(w.db, w.ctx(w.owner), opt.id)
        fact = min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)
        assert fact.kind == "approve" and fact.amount_cents == 500_000
        assert min_spends.min_spend_state(w.db, w.ctx(), opt.id) == "approved"
        again = min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)
        assert again.id == fact.id

    def test_no_approval_area_lets_anyone_approve(self, w):
        opt = _option(w, _enquiry(w), area=w.snug)
        assert min_spends.approve_min_spend(w.db, w.ctx(), opt.id).kind == "approve"

    def test_override_needs_the_override_approver_and_a_reason(self, w):
        opt = _option(w, _enquiry(w))
        with pytest.raises(Forbidden):
            min_spends.override_min_spend(w.db, w.ctx(w.approver), opt.id, reason="VIP")
        with pytest.raises(Invalid):
            min_spends.override_min_spend(w.db, w.ctx(w.overrider), opt.id, reason="  ")
        fact = min_spends.override_min_spend(
            w.db, w.ctx(w.overrider), opt.id, reason="VIP"
        )
        assert (fact.kind, fact.reason) == ("override", "VIP")

    def test_no_approver_configured(self, w):
        from app.db.functions_models import FnVenueFunctionSettings

        s = w.db.query(FnVenueFunctionSettings).filter_by(venue_id=w.venue.id).one()
        s.approver_user_id = None
        s.override_approver_user_id = None
        w.db.flush()
        opt = _option(w, _enquiry(w))
        with pytest.raises(Forbidden, match="no minimum-spend approver"):
            min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)

    def test_revoke_before_confirmation_only(self, w):
        enquiry = _enquiry(w)
        opt = _option(w, enquiry)
        min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)
        with pytest.raises(Forbidden):
            min_spends.revoke_approval(w.db, w.ctx(), opt.id)
        min_spends.revoke_approval(w.db, w.ctx(w.approver), opt.id, reason="Wrong date")
        assert min_spends.min_spend_state(w.db, w.ctx(), opt.id) == "proposed"
        min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)
        min_spends.confirm_option(w.db, w.ctx(), opt.id, channel="email")
        with pytest.raises(Conflict):
            min_spends.revoke_approval(w.db, w.ctx(w.approver), opt.id)

    def test_editing_an_approved_option_unapproves_it(self, w):
        opt = _option(w, _enquiry(w))
        min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)
        min_spends.update_min_spend(w.db, w.ctx(), opt.id, label="Deck hire")
        assert min_spends.min_spend_state(w.db, w.ctx(), opt.id) == "approved"
        min_spends.update_min_spend(w.db, w.ctx(), opt.id, amount_cents=400_000)
        assert min_spends.min_spend_state(w.db, w.ctx(), opt.id) == "proposed"
        last = min_spends.approvals(w.db, w.ctx(), opt.id)[-1]
        assert last.kind == "revoke" and last.automatic

    def test_approval_rights_explain_themselves(self, w):
        opt = _option(w, _enquiry(w))
        staff = min_spends.approval_rights(w.db, w.ctx(), opt)
        assert not staff["approve"] and "approver" in staff["why"]
        boss = min_spends.approval_rights(w.db, w.ctx(w.approver), opt)
        assert boss["approve"] and not boss["override"]


class TestApprovalCoversExactTerms:
    """Security review, Oct 2026: approver approves $9,000 -> staff records a
    customer DECLINE -> staff edits the option to $50 (no revoke, because the
    option was no longer "approved") -> staff records the customer's yes ->
    an event booked at $50 with no valid approval. An approval now covers
    the exact terms it was given on, in every state."""

    def test_decline_edit_confirm_bypass_is_refused(self, w):
        opt = _option(w, _enquiry(w), amount=900_000)
        min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)
        min_spends.decline_option(w.db, w.ctx(), opt.id, channel="phone")
        assert min_spends.min_spend_state(w.db, w.ctx(), opt.id) == "declined"
        min_spends.update_min_spend(w.db, w.ctx(), opt.id, amount_cents=5_000)
        last = min_spends.approvals(w.db, w.ctx(), opt.id)[-1]
        assert (last.kind, last.automatic) == ("revoke", True)
        assert last.reason == "Terms changed after approval: amount $9,000 -> $50"
        with pytest.raises(Forbidden):
            min_spends.confirm_option(w.db, w.ctx(), opt.id, channel="phone")
        assert opt.event_id is None
        # Re-approved on the new terms, the customer's yes books it.
        min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)
        event, created = min_spends.confirm_option(
            w.db, w.ctx(), opt.id, channel="phone"
        )
        assert created and opt.event_id == event.id

    @pytest.mark.parametrize(
        "change",
        [
            {"event_date": DATE + dt.timedelta(days=1)},
            {"start_time": dt.time(20, 0)},
            {"end_time": dt.time(2, 0)},
            {"ends_next_day": False, "end_time": dt.time(23, 0)},
        ],
    )
    def test_any_term_change_revokes_in_any_state(self, w, change):
        opt = _option(w, _enquiry(w))
        min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)
        min_spends.update_min_spend(w.db, w.ctx(), opt.id, **change)
        assert min_spends.min_spend_state(w.db, w.ctx(), opt.id) == "proposed"
        assert min_spends.approvals(w.db, w.ctx(), opt.id)[-1].kind == "revoke"
        with pytest.raises(Forbidden):
            min_spends.confirm_option(w.db, w.ctx(), opt.id, channel="email")

    def test_label_and_notes_keep_the_approval(self, w):
        opt = _option(w, _enquiry(w))
        min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)
        min_spends.update_min_spend(w.db, w.ctx(), opt.id, label="Deck", notes="x")
        assert min_spends.min_spend_state(w.db, w.ctx(), opt.id) == "approved"

    def test_edit_after_withdraw_is_refused(self, w):
        opt = _option(w, _enquiry(w))
        min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)
        min_spends.withdraw_min_spend(w.db, w.ctx(), opt.id)
        with pytest.raises(Conflict):
            min_spends.update_min_spend(w.db, w.ctx(), opt.id, amount_cents=5_000)
        with pytest.raises(Conflict):
            min_spends.confirm_option(w.db, w.ctx(), opt.id, channel="email")

    def test_area_edits(self, w):
        # Approved in the Deck, moved to another approval area: re-approve.
        from app.db.functions_models import FnVenueArea

        terrace = FnVenueArea(
            organization_id=w.org.id,
            venue_id=w.venue.id,
            name="Terrace",
            requires_approval=True,
        )
        w.db.add(terrace)
        w.db.flush()
        opt = _option(w, _enquiry(w))
        min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)
        min_spends.update_min_spend(w.db, w.ctx(), opt.id, area_id=terrace.id)
        assert min_spends.approvals(w.db, w.ctx(), opt.id)[-1].reason == (
            "Terms changed after approval: area"
        )
        with pytest.raises(Forbidden):
            min_spends.confirm_option(w.db, w.ctx(), opt.id, channel="email")
        # From a no-approval area into one that needs it: no free pass either.
        free = _option(w, _enquiry(w), area=w.snug)
        min_spends.update_min_spend(w.db, w.ctx(), free.id, area_id=w.deck.id)
        with pytest.raises(Forbidden):
            min_spends.confirm_option(w.db, w.ctx(), free.id, channel="email")
        # Into an area that needs no approval: confirmable without one.
        min_spends.update_min_spend(w.db, w.ctx(), opt.id, area_id=w.snug.id)
        _, created = min_spends.confirm_option(w.db, w.ctx(), opt.id, channel="email")
        assert created

    def test_terms_changed_outside_the_services_still_count(self, w):
        """Defence in depth: a write that bypasses update_min_spend (no revoke
        fact) still leaves the approval not covering the new terms."""
        opt = _option(w, _enquiry(w))
        min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)
        w.db.query(FnMinSpend).filter_by(id=opt.id).update({"amount_cents": 1})
        w.db.expire_all()
        assert min_spends.min_spend_state(w.db, w.ctx(), opt.id) == "proposed"
        with pytest.raises(Forbidden, match="changed after it was approved"):
            min_spends.confirm_option(w.db, w.ctx(), opt.id, channel="email")
        rights = min_spends.approval_rights(
            w.db, w.ctx(w.approver), w.db.get(FnMinSpend, opt.id)
        )
        assert rights["approve"]

    def test_imported_approvals_check_the_amount(self, w):
        from app.db.functions_models import FnMinSpendApproval

        opt = _option(w, _enquiry(w), amount=400_000)
        w.db.add(
            FnMinSpendApproval(
                organization_id=w.org.id,
                min_spend_id=opt.id,
                kind="approve",
                amount_cents=400_000,
                terms_key=None,
            )
        )
        w.db.flush()
        assert min_spends.min_spend_state(w.db, w.ctx(), opt.id) == "approved"
        w.db.query(FnMinSpend).filter_by(id=opt.id).update({"amount_cents": 300_000})
        w.db.expire_all()
        assert min_spends.min_spend_state(w.db, w.ctx(), opt.id) == "proposed"


class TestSecondReview:
    """Second security review, Oct 2026: nothing reachable through one venue
    may be used to touch, or learn about, another venue's records."""

    def test_an_option_on_a_hidden_enquiry_is_not_found(self, w):
        hidden = enquiries.create_enquiry(
            w.db,
            w.ctx(w.owner),
            contact={"name": "Hidden", "email": "h@example.com"},
            venue_id=w.venue2.id,
            booking_type="large_event_exclusive",
        )
        # The option itself is at the caller's venue — the enquiry is not.
        opt = min_spends.add_min_spend(
            w.db,
            w.ctx(w.owner),
            enquiry_id=hidden.id,
            venue_id=w.venue.id,
            area_id=w.snug.id,
            amount_cents=100_000,
            event_date=DATE,
        )
        staff, approver = w.ctx(), w.ctx(w.approver)
        for call in (
            lambda: min_spends.get_min_spend(w.db, staff, opt.id),
            lambda: min_spends.update_min_spend(w.db, staff, opt.id, amount_cents=1),
            lambda: min_spends.approve_min_spend(w.db, approver, opt.id),
            lambda: min_spends.decline_option(w.db, staff, opt.id, channel="phone"),
            lambda: min_spends.confirm_option(w.db, staff, opt.id, channel="phone"),
            lambda: min_spends.withdraw_min_spend(w.db, staff, opt.id),
        ):
            with pytest.raises(NotFound, match="Minimum spend not found"):
                call()
        # A group-wide enquiry is everyone's: its option at your venue is yours.
        shared = enquiries.create_enquiry(
            w.db,
            w.ctx(w.owner),
            contact={"name": "Shared", "email": "s@example.com"},
            booking_type="large_event_exclusive",
        )
        mine = min_spends.add_min_spend(
            w.db,
            w.ctx(w.owner),
            enquiry_id=shared.id,
            venue_id=w.venue.id,
            area_id=w.snug.id,
            amount_cents=100_000,
            event_date=DATE,
        )
        assert min_spends.get_min_spend(w.db, staff, mine.id).id == mine.id

    def test_unlinking_needs_the_mailbox(self, w):
        from app.db.functions_models import FnMailboxAccount

        shared = enquiries.create_enquiry(
            w.db, w.ctx(w.owner), contact={"name": "Shared", "email": "s@example.com"}
        )
        garden = FnMailboxAccount(
            organization_id=w.org.id,
            venue_id=w.venue2.id,
            google_email="g@garden.example",
        )
        w.db.add(garden)
        w.db.flush()
        link = enquiries.link_thread(
            w.db,
            w.ctx(w.owner),
            account_id=garden.id,
            gmail_thread_id="T9",
            enquiry_id=shared.id,
        )
        with pytest.raises(NotFound, match="Thread link not found"):
            enquiries.unlink_thread(w.db, w.ctx(), link.id)
        assert link.unlinked_at is None
        enquiries.unlink_thread(w.db, w.ctx(w.owner), link.id)
        assert link.unlinked_at is not None

    def test_other_venues_customers_are_not_disclosed(self, w):
        owner, staff = w.ctx(w.owner), w.ctx()
        theirs = enquiries.create_enquiry(
            w.db,
            owner,
            contact={"name": "Garden Regular", "email": "regular@example.com"},
            venue_id=w.venue2.id,
        )
        assert (
            contacts.find_contact_by_email(w.db, staff, "regular@example.com") is None
        )
        assert contacts.find_contact_by_email(
            w.db, owner, "regular@example.com"
        ).id == (theirs.contact_id)
        # Creating a contact on a hidden customer's address: a generic refusal.
        with pytest.raises(Conflict) as refused:
            contacts.create_contact(
                w.db, staff, name="Copy", email="Regular@example.com"
            )
        assert "Another contact" not in refused.value.message
        assert "ask a manager" in refused.value.message
        mine = contacts.create_contact(
            w.db, staff, name="Mine", email="mine@example.com"
        )
        with pytest.raises(Conflict) as refused:
            contacts.add_contact_email(w.db, staff, mine.id, "regular@example.com")
        assert "ask a manager" in refused.value.message
        # Recording their enquiry here just works — attached to the org's one
        # contact for that address, the same answer as for a new customer.
        here = enquiries.create_enquiry(
            w.db,
            staff,
            contact={"name": "R.", "email": "regular@example.com"},
            venue_id=w.venue.id,
        )
        assert here.contact_id == theirs.contact_id
        # Now staff deal with this customer, the clash may be named.
        with pytest.raises(Conflict, match="Another contact already uses"):
            contacts.create_contact(
                w.db, staff, name="Copy", email="regular@example.com"
            )
        # Direct bookings match by email the same way.
        other = enquiries.create_enquiry(
            w.db,
            owner,
            contact={"name": "Other", "email": "other@example.com"},
            venue_id=w.venue2.id,
        )
        event = events.create_event(
            w.db,
            staff,
            venue_id=w.venue.id,
            event_date=DATE,
            booking_type="group_booking",
            contact={"name": "O.", "email": "other@example.com"},
        )
        assert event.contact_id == other.contact_id


# ===========================================================================
# The customer's confirmation
# ===========================================================================


class TestConfirmOption:
    def test_confirming_creates_the_event_once_and_links_everything(self, w):
        enquiry = _enquiry(w)
        opt = _option(w, enquiry)
        other = _option(w, enquiry, area=w.snug, amount=300_000)
        tmpl = _template(w)
        menu = menus.add_menu_from_template(
            w.db, w.ctx(), template_id=tmpl.id, enquiry_id=enquiry.id, guest_count=60
        )
        min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)

        event, created = min_spends.confirm_option(
            w.db, w.ctx(), opt.id, channel="phone", note="Called to say yes"
        )
        assert created and event.enquiry_id == enquiry.id
        assert (event.venue_id, event.area_id, event.event_date) == (
            w.venue.id,
            w.deck.id,
            DATE,
        )
        assert (event.start_time, event.end_time, event.ends_next_day) == (
            dt.time(19),
            dt.time(1),
            True,
        )
        assert (
            event.created_via == "min_spend" and event.confirmation_channel == "phone"
        )
        assert event.name == "Casey's 40th" and event.guests == 60
        assert opt.event_id == event.id
        assert w.db.get(FnPreorderMenu, menu.id).event_id == event.id
        assert other.withdrawn_at is not None
        resp = w.db.query(FnMinSpendResponse).filter_by(min_spend_id=opt.id).one()
        assert (resp.kind, resp.channel, resp.recorded_by_user_id) == (
            "confirm",
            "phone",
            w.staff.id,
        )
        stage, action = enquiries.enquiry_state(w.db, w.ctx(), enquiry.id)
        assert stage == "confirmed" and action.kind == "none"
        assert min_spends.min_spend_state(w.db, w.ctx(), opt.id) == "confirmed"
        assert min_spends.min_spend_state(w.db, w.ctx(), other.id) == "withdrawn"

        again, created_again = min_spends.confirm_option(
            w.db, w.ctx(), opt.id, channel="email"
        )
        assert again.id == event.id and not created_again
        assert w.db.query(FnEvent).filter_by(enquiry_id=enquiry.id).count() == 1

    def test_a_second_option_on_a_booked_enquiry_is_refused(self, w):
        """Also: an option in an area that needs no approval confirms without
        one, in person, booking that area."""
        enquiry = _enquiry(w)
        a = _option(w, enquiry, area=w.snug)
        b = _option(w, enquiry, area=w.snug, amount=200_000)
        event, created = min_spends.confirm_option(
            w.db, w.ctx(), a.id, channel="in_person"
        )
        assert created and event.area_id == w.snug.id
        with pytest.raises(Conflict):
            min_spends.confirm_option(w.db, w.ctx(), b.id, channel="email")

    def test_unapproved_or_revoked_option_in_an_approval_area_is_refused(self, w):
        opt = _option(w, _enquiry(w))
        with pytest.raises(Forbidden):  # never approved
            min_spends.confirm_option(w.db, w.ctx(), opt.id, channel="email")
        min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)
        min_spends.revoke_approval(w.db, w.ctx(w.approver), opt.id)
        with pytest.raises(Forbidden):  # approved, then revoked
            min_spends.confirm_option(w.db, w.ctx(), opt.id, channel="email")

    def test_quote_ctx_reaches_only_its_own_enquiry(self, w):
        mine = _enquiry(w)
        theirs = enquiries.create_enquiry(
            w.db,
            w.ctx(),
            contact={"name": "Other", "email": "other@example.com"},
            venue_id=w.venue.id,
            booking_type="large_event_exclusive",
        )
        their_opt = _option(w, theirs, area=w.snug)
        quote = for_quote(w.db, w.org.id, enquiry_id=mine.id)
        with pytest.raises(NotFound):
            min_spends.confirm_option(w.db, quote, their_opt.id, channel="quote_page")
        with pytest.raises(NotFound):
            min_spends.decline_option(w.db, quote, their_opt.id, channel="quote_page")
        with pytest.raises(NotFound):
            enquiries.get_enquiry(w.db, quote, theirs.id)
        assert [r.enquiry.id for r in enquiries.list_enquiries(w.db, quote)] == [
            mine.id
        ]
        assert contacts.search_contacts(w.db, quote) == []
        my_opt = _option(w, mine, area=w.snug)
        event, created = min_spends.confirm_option(
            w.db, quote, my_opt.id, channel="quote_page"
        )
        assert created
        assert events.event_changes(w.db, w.ctx(), event.id)[0].actor_kind == "client"

    def test_quote_page_confirmation_from_a_customer_ctx(self, w):
        opt = _option(w, _enquiry(w), area=w.snug)
        customer = for_org(w.db, w.org.id, actor_label="customer")
        event, created = min_spends.confirm_option(
            w.db,
            customer,
            opt.id,
            channel="quote_page",
            customer_email="Casey@Example.com",
        )
        resp = w.db.query(FnMinSpendResponse).filter_by(min_spend_id=opt.id).one()
        assert created and resp.recorded_by_user_id is None
        assert resp.customer_email == "casey@example.com"
        with pytest.raises(Forbidden):  # a phone yes is recorded by a person
            min_spends.confirm_option(w.db, customer, opt.id, channel="phone")

    def test_withdrawn_dateless_and_lost(self, w):
        enquiry = _enquiry(w)
        dateless = _option(w, enquiry, area=w.snug, date=None)
        with pytest.raises(Invalid):
            min_spends.confirm_option(w.db, w.ctx(), dateless.id, channel="email")
        gone = _option(w, enquiry, area=w.snug)
        min_spends.withdraw_min_spend(w.db, w.ctx(), gone.id)
        with pytest.raises(Conflict):
            min_spends.confirm_option(w.db, w.ctx(), gone.id, channel="email")
        live = _option(w, enquiry, area=w.snug)
        enquiries.close_enquiry(w.db, w.ctx(), enquiry.id, reason="Went elsewhere")
        with pytest.raises(Conflict):
            min_spends.confirm_option(w.db, w.ctx(), live.id, channel="email")

    def test_decline_then_change_of_heart(self, w):
        opt = _option(w, _enquiry(w), area=w.snug)
        first = min_spends.decline_option(w.db, w.ctx(), opt.id, channel="email")
        assert (
            min_spends.decline_option(w.db, w.ctx(), opt.id, channel="email").id
            == first.id
        )
        assert min_spends.min_spend_state(w.db, w.ctx(), opt.id) == "declined"
        _, created = min_spends.confirm_option(w.db, w.ctx(), opt.id, channel="email")
        assert created

    def test_booked_option_edits_need_a_note_and_are_logged(self, w):
        enquiry = _enquiry(w)
        opt = _option(w, enquiry, area=w.snug)
        event, _ = min_spends.confirm_option(w.db, w.ctx(), opt.id, channel="email")
        with pytest.raises(Invalid):
            min_spends.update_min_spend(w.db, w.ctx(), opt.id, amount_cents=600_000)
        with pytest.raises(Conflict):
            min_spends.update_min_spend(
                w.db, w.ctx(), opt.id, area_id=w.deck.id, note="x"
            )
        min_spends.update_min_spend(
            w.db, w.ctx(), opt.id, amount_cents=600_000, note="Added a second bar"
        )
        log = events.event_changes(w.db, w.ctx(), event.id)
        assert log[-1].changes == {"min_spend.amount_cents": [500_000, 600_000]}
        assert log[-1].note == "Added a second bar"

    def test_lowering_a_booked_minimum_needs_an_approver(self, w):
        """Owner, 16 Oct 2026: a booked minimum only comes down with the
        sign-off of someone who could have approved the option."""
        opt = _option(w, _enquiry(w))  # the Deck needs approval
        min_spends.approve_min_spend(w.db, w.ctx(w.approver), opt.id)
        min_spends.confirm_option(w.db, w.ctx(), opt.id, channel="email")
        rights = min_spends.approval_rights(w.db, w.ctx(), opt)
        assert not rights["lower"]
        assert rights["why"] == (
            "Only the venue's minimum-spend approver can lower a booked minimum."
        )
        with pytest.raises(Forbidden):
            min_spends.update_min_spend(
                w.db, w.ctx(), opt.id, amount_cents=400_000, note="Smaller group"
            )
        # Raising it still needs only the note.
        min_spends.update_min_spend(
            w.db, w.ctx(), opt.id, amount_cents=550_000, note="Added a band"
        )
        min_spends.update_min_spend(
            w.db, w.ctx(w.approver), opt.id, amount_cents=400_000, note="Smaller group"
        )
        min_spends.update_min_spend(
            w.db, w.ctx(w.overrider), opt.id, amount_cents=350_000, note="Weekday"
        )
        log = [
            (a.kind, a.amount_cents, a.reason)
            for a in min_spends.approvals(w.db, w.ctx(), opt.id)
        ]
        assert log[-2:] == [
            ("approve", 400_000, "Booked minimum lowered: Smaller group"),
            ("override", 350_000, "Booked minimum lowered: Weekday"),
        ]
        assert min_spends.min_spend_state(w.db, w.ctx(), opt.id) == "confirmed"

    def test_lowering_where_no_approval_is_needed(self, w):
        opt = _option(w, _enquiry(w), area=w.snug)
        min_spends.confirm_option(w.db, w.ctx(), opt.id, channel="email")
        min_spends.update_min_spend(
            w.db, w.ctx(), opt.id, amount_cents=400_000, note="Smaller group"
        )
        assert min_spends.approvals(w.db, w.ctx(), opt.id)[-1].kind == "approve"


# ===========================================================================
# Events
# ===========================================================================


def _booked(w):
    enquiry = _enquiry(w, booking_type="seated_dining")
    event, _ = events.confirm_booking(
        w.db,
        w.ctx(),
        enquiry.id,
        channel="email",
        event_date=DATE,
        start_time=dt.time(18),
    )
    return enquiry, event


class TestEvents:
    def test_direct_booking_rules(self, w):
        c = add_contact(w.db, w.org.id)
        with pytest.raises(Invalid):
            events.create_event(
                w.db,
                w.ctx(),
                venue_id=w.venue.id,
                contact_id=c.id,
                event_date=DATE,
                booking_type="large_event_partial",
            )
        ev = events.create_event(
            w.db,
            w.ctx(),
            venue_id=w.venue.id,
            contact_id=c.id,
            event_date=DATE,
            booking_type="group_booking",
            guests=12,
            bar_tab_cents=50_000,
        )
        assert ev.name == "Casey Client — Group booking" and ev.created_via == "direct"
        assert events.event_state(w.db, w.ctx(), ev.id).status == "booked"

    def test_status_walks_through_the_portal(self, w):
        _, event = _booked(w)
        ctx = w.ctx()
        state = lambda: events.event_state(w.db, ctx, event.id)  # noqa: E731
        events.record_link_sent(w.db, ctx, event.id, to_emails=["casey@example.com"])
        assert state().status == "sent_to_client"
        client = for_client(
            w.db, w.org.id, event_id=event.id, email="casey@example.com"
        )
        rows = events.update_event(
            w.db, client, event.id, guests=64, arrival_drinks="Bubbles"
        )
        assert {r.section for r in rows} == {"details", "beverages"}
        assert all(r.actor_kind == "client" and not r.after_confirmation for r in rows)
        assert state().status == "in_progress"
        with pytest.raises(Forbidden):
            events.update_event(w.db, client, event.id, event_date=DATE)
        with pytest.raises(Forbidden):
            events.skip_section(w.db, client, event.id, "food", note="x")
        events.confirm_section(w.db, client, event.id, "details")
        events.confirm_section(w.db, client, event.id, "beverages")
        with pytest.raises(Invalid):
            events.skip_section(w.db, ctx, event.id, "food", note=" ")
        events.skip_section(
            w.db, ctx, event.id, "food", note="Canapés ordered by phone"
        )
        assert state().status == "confirmed" and not state().edited_since_confirmed

        rows = events.update_event(w.db, client, event.id, allergies="One coeliac")
        assert rows[0].after_confirmation
        assert state().status == "confirmed" and state().edited_since_confirmed
        events.confirm_section(w.db, client, event.id, "food")
        assert not state().edited_since_confirmed

    def test_portal_ctx_reaches_only_its_own_event(self, w):
        _, mine = _booked(w)
        c = add_contact(w.db, w.org.id, "Neighbour")
        theirs = events.create_event(
            w.db,
            w.ctx(),
            venue_id=w.venue.id,
            contact_id=c.id,
            event_date=DATE,
            booking_type="group_booking",
        )
        client = for_client(w.db, w.org.id, event_id=mine.id, email="casey@example.com")
        for call in (
            lambda: events.get_event(w.db, client, theirs.id),
            lambda: events.update_event(w.db, client, theirs.id, guests=1),
            lambda: events.confirm_section(w.db, client, theirs.id, "details"),
            lambda: menus.list_menus(w.db, client, event_id=theirs.id),
            lambda: contacts.get_contact(w.db, client, c.id),
        ):
            with pytest.raises(NotFound):
                call()
        assert [r.event.id for r in events.list_events(w.db, client)] == [mine.id]
        assert (
            events.update_event(w.db, client, mine.id, guests=61)[0].actor_kind
            == "client"
        )

    def test_client_surface_matches_the_portal(self, w):
        _, event = _booked(w)
        client = for_client(
            w.db, w.org.id, event_id=event.id, email="casey@example.com"
        )
        with pytest.raises(Forbidden):  # the bar tab amount is staff's
            events.update_event(w.db, client, event.id, bar_tab_cents=1)
        row = events.replace_event_children(
            w.db,
            client,
            event.id,
            "security",
            [
                {
                    "name": "Door",
                    "start_time": dt.time(21),
                    "end_time": dt.time(1),
                    "ends_next_day": True,
                }
            ],
        )
        # A list is replaced whole and the log keeps the list it replaced.
        assert row.actor_kind == "client" and row.changes["security"][0] == []
        row = events.update_event_contact(
            w.db, client, event.id, phone="021 555 0101", name="Casey C"
        )
        assert row.changes == {
            "contact.phone": [None, "021 555 0101"],
            "contact.name": ["Casey Client", "Casey C"],
        }
        assert contacts.get_contact(w.db, w.ctx(), event.contact_id).name == "Casey C"
        with pytest.raises(Invalid):  # email is how they get in — not editable here
            events.update_event_contact(w.db, client, event.id, email="new@example.com")
        assert events.event_state(w.db, w.ctx(), event.id).status == "in_progress"
        tmpl = _template(w)
        with pytest.raises(Forbidden):
            menus.add_menu_from_template(
                w.db, client, template_id=tmpl.id, event_id=event.id
            )

    def test_staff_edits_log_diffs_and_check_versions(self, w):
        _, event = _booked(w)
        seen = event.version
        rows = events.update_event(
            w.db,
            w.ctx(),
            event.id,
            expected_version=seen,
            guests=70,
            note="More coming",
        )
        assert rows[0].changes == {"guests": [60, 70]} and rows[0].note == "More coming"
        with pytest.raises(Conflict):
            events.update_event(
                w.db, w.ctx(), event.id, expected_version=seen, guests=71
            )
        assert events.update_event(w.db, w.ctx(), event.id, guests=70) == []
        with pytest.raises(Invalid):
            events.update_event(w.db, w.ctx(), event.id, colour="red")

    def test_table_plan_image(self, w):
        from app.db.functions_models import FnFile

        _, event = _booked(w)
        f = FnFile(
            organization_id=w.org.id,
            sha256="ab" * 32,
            content_type="image/png",
            size_bytes=1,
            data=b"\x00",
        )
        theirs = FnFile(
            organization_id=w.other_org.id,
            sha256="cd" * 32,
            content_type="image/png",
            size_bytes=1,
            data=b"\x00",
        )
        w.db.add_all([f, theirs])
        w.db.flush()
        with pytest.raises(Invalid):  # another org's file is not a file here
            events.update_event(w.db, w.ctx(), event.id, table_plan_file_id=theirs.id)
        row = events.update_event(w.db, w.ctx(), event.id, table_plan_file_id=f.id)[0]
        assert row.section == "details" and event.table_plan_file_id == f.id
        client = for_client(
            w.db, w.org.id, event_id=event.id, email="casey@example.com"
        )
        with pytest.raises(Forbidden):
            events.update_event(w.db, client, event.id, table_plan_file_id=None)

    def test_list_event_shapes_agree(self, w):
        """Page-first (default) and status-filtered lists return the same
        rows with the same derived status and money. (Folds in the old
        date-window/status/money check on the one booked event.)"""
        _, booked = _booked(w)
        events.update_event(w.db, w.ctx(), booked.id, bar_tab_cents=90_000)
        rows = events.list_events(w.db, w.ctx(), date_from=DATE, date_to=DATE)
        assert [(r.event.id, r.status, r.expected_spend_cents) for r in rows] == [
            (booked.id, "booked", 90_000)
        ]
        assert events.list_events(w.db, w.ctx(), statuses=["confirmed"]) == []
        events.record_link_sent(w.db, w.ctx(), booked.id, to_emails=["c@example.com"])
        c = add_contact(w.db, w.org.id, "Other")
        for i, bt in enumerate(("group_booking", "seated_dining", "group_booking")):
            ev = events.create_event(
                w.db,
                w.ctx(),
                venue_id=w.venue.id,
                contact_id=c.id,
                event_date=DATE + dt.timedelta(days=i),
                booking_type=bt,
            )
            if i == 1:
                events.cancel_event(w.db, w.ctx(), ev.id, reason="Rained off")
        for offset in (0, 2):
            paged = events.list_events(
                w.db, w.ctx(), include_cancelled=True, limit=3, offset=offset
            )
            filtered = events.list_events(
                w.db,
                w.ctx(),
                statuses=list(status.EVENT_STATUSES),
                limit=3,
                offset=offset,
            )
            assert [vars(r) for r in paged] == [vars(r) for r in filtered]
            assert paged
        assert {
            r.status for r in events.list_events(w.db, w.ctx(), include_cancelled=True)
        } == {"sent_to_client", "booked", "cancelled"}


# ===========================================================================
# Menus
# ===========================================================================


class TestMenus:
    def test_template_snapshot_and_set_menu_split(self, w):
        enquiry = _enquiry(w)
        tmpl = _template(w, set_menu=True)
        menu = menus.add_menu_from_template(
            w.db, w.ctx(), template_id=tmpl.id, enquiry_id=enquiry.id
        )
        items = menus.distribute_set_menu(w.db, w.ctx(), menu.id, guest_count=41)
        qty = {i.name: i.quantity for i in items if i.kind == "item"}
        assert qty == {
            "Arancini": 21,
            "Sliders": 20,
            "Oysters": 14,
            "Tartlets": 14,
            "Crudo": 13,
        }
        m, rows = menus.get_menu(w.db, w.ctx(), menu.id)
        assert menus.menu_value_cents(m, rows) == 41 * 4500
        assert all(r.unit_price_cents is None for r in rows)

    def test_a_la_carte_keeps_prices(self, w):
        enquiry = _enquiry(w)
        menu = menus.add_menu_from_template(
            w.db,
            w.ctx(),
            template_id=_template(w, set_menu=False).id,
            enquiry_id=enquiry.id,
        )
        m, rows = menus.get_menu(w.db, w.ctx(), menu.id)
        menus.set_item_quantities(
            w.db, w.ctx(), m.id, {r.id: 2 for r in rows if r.kind == "item"}
        )
        m, rows = menus.get_menu(w.db, w.ctx(), menu.id)
        assert menus.menu_value_cents(m, rows) == 2 * (1500 + 2200 + 3000 + 1200 + 1800)

    def test_client_quantity_edits_on_a_booked_menu_are_client_edits(self, w):
        enquiry = _enquiry(w, booking_type="seated_dining")
        tmpl = _template(w)
        menu = menus.add_menu_from_template(
            w.db, w.ctx(), template_id=tmpl.id, enquiry_id=enquiry.id, guest_count=20
        )
        event, _ = events.confirm_booking(
            w.db, w.ctx(), enquiry.id, channel="email", event_date=DATE
        )
        assert w.db.get(FnPreorderMenu, menu.id).event_id == event.id
        client = for_client(
            w.db, w.org.id, event_id=event.id, email="casey@example.com"
        )
        _, rows = menus.get_menu(w.db, client, menu.id)
        menus.set_item_quantities(w.db, client, menu.id, {rows[1].id: 7})
        log = events.event_changes(w.db, w.ctx(), event.id)[-1]
        assert (log.section, log.actor_kind) == ("food", "client")
        assert events.event_state(w.db, w.ctx(), event.id).status == "in_progress"
        with pytest.raises(Forbidden):
            menus.update_menu(w.db, client, menu.id, price_per_person_cents=1)
        with pytest.raises(Forbidden):
            menus.replace_menu_items(w.db, client, menu.id, [])

    def test_menu_venue_scoping(self, w):
        enquiry = _enquiry(w)
        menu = menus.create_menu(
            w.db,
            w.ctx(w.owner),
            venue_id=w.venue2.id,
            name="Garden BBQ",
            enquiry_id=enquiry.id,
        )
        with pytest.raises(NotFound):
            menus.get_menu(w.db, w.ctx(), menu.id)
        assert [
            m.id for m, _ in menus.list_menus(w.db, w.ctx(), enquiry_id=enquiry.id)
        ] == []


# ===========================================================================
# Contacts and enquiries
# ===========================================================================


class TestContactsAndEnquiries:
    def test_contacts_dedupe_by_email_case_insensitively(self, w):
        a = _enquiry(w)
        b = enquiries.create_enquiry(
            w.db, w.ctx(), contact={"name": "C. Client", "email": "CASEY@example.com "}
        )
        assert a.contact_id == b.contact_id
        with pytest.raises(Conflict):
            contacts.create_contact(
                w.db, w.ctx(), name="Dup", email="Casey@Example.com"
            )

    def test_email_check_is_linear_and_capped(self, db_session):
        """Also the strict shape: these addresses are sent to (Resend to /
        reply_to, Gmail), so the check is a dot-atom local part and
        letter/digit/hyphen labels. (The mailbox's recipient check shares
        it: test_functions_mailbox_security.py TestStrictEmail.)"""
        import time

        from app.services.functions.ctx import FunctionsCtx

        # No world needed: an address that fails the check is refused before
        # any query, so a bare ctx on an empty org answers the same.
        ctx = FunctionsCtx(
            org_id="no-such-org",
            user_id=None,
            venue_ids=frozenset(),
            is_owner=True,
            mode="live",
        )
        hostile = "a@" + "b." * 31_000 + "@"  # ~60 KB; quadratic before the fix
        started = time.perf_counter()
        with pytest.raises(Invalid):
            contacts.normalise_email(hostile)
        assert contacts.find_contact_by_email(db_session, ctx, hostile) is None
        assert time.perf_counter() - started < 0.05
        # Even uncapped the pattern stays linear on the same shape.
        from app.services.functions import addresses

        started = time.perf_counter()
        assert addresses._ADDRESS.fullmatch(hostile[:-1] + "x@") is None
        assert time.perf_counter() - started < 0.05
        labels = ".".join(["b" * 63, "c" * 63, "d" * 50]) + ".com"
        long_ok = "a" * 64 + "@" + labels  # 247 characters, every part at its limit
        assert len(long_ok) == 247 and contacts.normalise_email(long_ok) == long_ok
        with pytest.raises(Invalid):  # 255 characters
            contacts.normalise_email("a" * 64 + "@" + labels + "x" * 8)
        for good in (
            "Casey@Example.com",
            "a.b+c@sub.example.co.nz",
            "o'brien+tag@sub.example.co.nz",
            "a_b-c@x-y.com",
            "first.last@xn--bcher-kva.example",
        ):
            assert contacts.normalise_email(good) == good.lower(), good
        for bad in (
            "a@b",
            "a@b..com",
            "a@.com",
            "a@b.com.",
            "@b.com",
            "a b@c.com",
            "a,b@x.com",  # two recipients in one value
            '"a"@x.com',
            "a<b@x.com",
            "a>b@x.com",
            "a@x_y.com",
            "a@-x.com",
            "a@x-.com",
            "a..b@x.com",
            ".a@x.com",
            "a.@x.com",
            "a@x.123",
            "a@[10.0.0.1]",
            "a(b)@x.com",
            "a@x,y.com",
            "a" * 65 + "@x.com",
            "a@" + "b" * 64 + ".com",
            "ünïcode@x.com",
        ):
            with pytest.raises(Invalid):
                contacts.normalise_email(bad)

    def test_search_contacts_pages(self, w):
        for name in ("Ava", "Ben", "Cat", "Cat", "Dan"):
            contacts.create_contact(w.db, w.ctx(), name=name)
        pages = [
            contacts.search_contacts(w.db, w.ctx(), limit=2, offset=o)
            for o in (0, 2, 4)
        ]
        ids = [c.id for page in pages for c in page]
        assert [len(p) for p in pages] == [2, 2, 1] and len(set(ids)) == 5
        assert [c.name for page in pages for c in page] == [
            "Ava",
            "Ben",
            "Cat",
            "Cat",
            "Dan",
        ]

    def test_close_and_reopen_need_reasons(self, w):
        e = _enquiry(w)
        with pytest.raises(Invalid):
            enquiries.close_enquiry(w.db, w.ctx(), e.id, reason="")
        enquiries.close_enquiry(w.db, w.ctx(), e.id, reason="Booked elsewhere")
        with pytest.raises(Conflict):
            enquiries.close_enquiry(w.db, w.ctx(), e.id, reason="again")
        assert enquiries.enquiry_state(w.db, w.ctx(), e.id)[0] == "lost"
        enquiries.reopen_enquiry(w.db, w.ctx(), e.id, reason="New date offered")
        assert enquiries.enquiry_state(w.db, w.ctx(), e.id)[0] == "new"
        assert [c.kind for c in enquiries.closures(w.db, w.ctx(), e.id)] == [
            "lost",
            "reopened",
        ]

    def test_booked_enquiry_is_not_lost(self, w):
        enquiry, _ = _booked(w)
        with pytest.raises(Conflict):
            enquiries.close_enquiry(w.db, w.ctx(), enquiry.id, reason="x")

    def test_list_filters_by_derived_stage_and_due(self, w):
        e = _enquiry(
            w, follow_up_date=dt.date(2020, 1, 1), follow_up_reason="Call back"
        )
        rows = enquiries.list_enquiries(w.db, w.ctx(), stages=["new"], due_only=True)
        assert [r.enquiry.id for r in rows] == [e.id]
        assert rows[0].next_action == "requested" and rows[0].next_action_overdue
        assert enquiries.list_enquiries(w.db, w.ctx(), stages=["quoted"]) == []
        assert enquiries.list_enquiries(w.db, w.ctx(), q="casey")[0].enquiry.id == e.id

    def test_owner_must_be_in_the_organisation(self, w):
        e = _enquiry(w)
        with pytest.raises(Invalid):
            enquiries.update_enquiry(w.db, w.ctx(), e.id, owner_user_id=w.other_user.id)
        enquiries.update_enquiry(w.db, w.ctx(), e.id, owner_user_id=w.approver.id)
        assert e.owner_user_id == w.approver.id

    def test_sql_sorts_match_the_row_sorts(self, w, subtests):
        """F3 sorted these by reading every row (reports.sort_rows); the SQL
        sorts must give exactly that order — blanks last both ways, ties
        newest first, then id — in one page or many. All eight sorts (four
        fields, both ways) over one set of enquiries; each sort is its own
        subtest, so a failure names it."""
        from app.db.functions_models import FnEnquiryClosure, FnProposalEvent
        from app.services.functions import reports

        base = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
        rows = [  # contact, subject, guests, hours after base, stage fact
            ("bob", None, None, 0, None),
            ("Alice", "  ", 40, 1, "lost"),
            ("   ", "Wedding", 10, 2, "quoted"),
            ("alice", "anniversary", 40, 2, None),  # same instant as the last
            ("Émile", "wedding", None, 3, "quoted"),
            ("zed", "Ball", 200, 4, "lost"),
            ("Bob", "ball", 10, 5, "booked"),
        ]
        for name, subject, guests, hours, fact in rows:
            c = add_contact(w.db, w.org.id, name)
            e = FnEnquiry(
                organization_id=w.org.id,
                contact_id=c.id,
                venue_id=w.venue.id,
                source="email",
                subject=subject,
                guests=guests,
                created_at=base + dt.timedelta(hours=hours),
            )
            w.db.add(e)
            w.db.flush()
            if fact == "lost":
                w.db.add(
                    FnEnquiryClosure(
                        organization_id=w.org.id,
                        enquiry_id=e.id,
                        kind="lost",
                        reason="x",
                    )
                )
            elif fact == "quoted":
                w.db.add(
                    FnProposalEvent(
                        organization_id=w.org.id,
                        enquiry_id=e.id,
                        kind="sent",
                        source="import",
                    )
                )
            elif fact == "booked":
                w.db.add(
                    FnEvent(
                        organization_id=w.org.id,
                        enquiry_id=e.id,
                        contact_id=c.id,
                        venue_id=w.venue.id,
                        name="x",
                        event_date=DATE,
                    )
                )
        w.db.flush()
        ctx = w.ctx(w.owner)
        every = reports.all_enquiry_rows(w.db, ctx)
        for field in ("contact", "subject", "guests", "stage"):
            for descending in (False, True):
                sort = f"{field}_{'desc' if descending else 'asc'}"
                with subtests.test(sort=sort):
                    expected = [
                        r.enquiry.id
                        for r in reports.sort_rows(every, field, descending)
                    ]
                    assert sort in enquiries.SORTS
                    full = enquiries.list_enquiries(w.db, ctx, sort=sort, limit=100)
                    assert [r.enquiry.id for r in full] == expected
                    paged = []
                    for offset in range(0, len(expected), 2):
                        page = enquiries.list_enquiries(
                            w.db, ctx, sort=sort, limit=2, offset=offset
                        )
                        paged += [r.enquiry.id for r in page]
                    assert paged == expected

    def test_update_checks_versions(self, w):
        e = _enquiry(w)
        v = e.version
        assert enquiries.update_enquiry(
            w.db, w.ctx(), e.id, expected_version=v, guests=80
        ) == {"guests": [60, 80]}
        with pytest.raises(Conflict):
            enquiries.update_enquiry(w.db, w.ctx(), e.id, expected_version=v, guests=90)
        with pytest.raises(Invalid):
            enquiries.update_enquiry(w.db, w.ctx(), e.id, booking_type="wedding")

    def test_thread_links_feed_the_stage(self, w):
        from app.db.functions_models import (
            FnMailboxAccount,
            FnMailboxMessage,
            FnMailboxThread,
        )

        e = _enquiry(w)
        acct = FnMailboxAccount(
            organization_id=w.org.id, venue_id=w.venue.id, google_email="x@brew.example"
        )
        w.db.add(acct)
        w.db.flush()
        t = FnMailboxThread(
            organization_id=w.org.id, account_id=acct.id, gmail_thread_id="T1"
        )
        w.db.add(t)
        w.db.flush()
        w.db.add(
            FnMailboxMessage(
                organization_id=w.org.id,
                account_id=acct.id,
                thread_id=t.id,
                gmail_message_id="M1",
                gmail_thread_id="T1",
                is_inbound=False,
                internal_date=dt.datetime.now(dt.timezone.utc),
            )
        )
        w.db.flush()
        link = enquiries.link_thread(
            w.db, w.ctx(), account_id=acct.id, gmail_thread_id="T1", enquiry_id=e.id
        )
        assert (
            enquiries.link_thread(
                w.db, w.ctx(), account_id=acct.id, gmail_thread_id="T1", enquiry_id=e.id
            ).id
            == link.id
        )
        assert enquiries.enquiry_state(w.db, w.ctx(), e.id)[0] == "replied"
        enquiries.unlink_thread(w.db, w.ctx(), link.id)
        assert enquiries.enquiry_state(w.db, w.ctx(), e.id)[0] == "new"
        assert status.STAGES[0] == "new"
