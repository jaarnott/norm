"""The Norm Functions staff API — dashboard and reports.

Every number here comes from the services' derivations (status.py's selects,
money.py's rules); these tests set up the FACTS — mail on linked threads,
closures, section confirmations, a client's edit, a quote-page decline — and
check the dashboard and reports read them the same way, for the caller's
venues only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import contextmanager
from zoneinfo import ZoneInfo

import pytest

from app.db.functions_models import (
    FnEnquiryClosure,
    FnFormSubmission,
    FnMailboxAccount,
    FnMailboxMessage,
    FnMailboxThread,
    FnMinSpend,
    FnMinSpendApproval,
    FnPreorderMenu,
    FnWebForm,
)
from app.services.functions import events as events_svc
from app.services.functions import min_spends as min_spends_svc
from app.services.functions.ctx import for_client, for_quote
from tests.functions_world import add_contact, add_enquiry, make_world
from tests.test_functions_api import call, detail_of, new_enquiry, new_option

UTC = dt.timezone.utc
NZ = ZoneInfo("Pacific/Auckland")


@pytest.fixture
def w(db_session):
    return make_world(db_session)


def nz_today() -> dt.date:
    return dt.datetime.now(NZ).date()


def book(client, w, *, days_ahead: int, user=None, venue=None, **kw):
    body = {
        "venue_id": (venue or w.venue).id,
        "event_date": (nz_today() + dt.timedelta(days=days_ahead)).isoformat(),
        "booking_type": "seated_dining",
        "contact": {
            "name": kw.pop("who", "Robin"),
            "email": kw.pop("email", f"{uuid.uuid4().hex[:6]}@x.com"),
        },
        **kw,
    }
    r = call(client, "POST", "/events", user or w.staff, json=body)
    assert r.status_code == 201, r.text
    return r.json()["event"]


def overdue_reply(client, w, enquiry_id):
    """An inbound customer email 30 hours ago on a thread linked to the
    enquiry — the fact behind "reply overdue"."""
    account = FnMailboxAccount(
        organization_id=w.org.id,
        venue_id=w.venue.id,
        google_email=f"{uuid.uuid4().hex[:6]}@brew.bar",
    )
    w.db.add(account)
    w.db.flush()
    thread = FnMailboxThread(
        organization_id=w.org.id, account_id=account.id, gmail_thread_id="thr"
    )
    w.db.add(thread)
    w.db.flush()
    w.db.add(
        FnMailboxMessage(
            organization_id=w.org.id,
            account_id=account.id,
            thread_id=thread.id,
            gmail_message_id=uuid.uuid4().hex,
            gmail_thread_id="thr",
            internal_date=dt.datetime.now(UTC) - dt.timedelta(hours=30),
            from_email="casey@example.com",
            is_inbound=True,
        )
    )
    w.db.flush()
    r = call(
        client,
        "POST",
        f"/enquiries/{enquiry_id}/threads",
        w.staff,
        json={"account_id": account.id, "gmail_thread_id": "thr"},
    )
    assert r.status_code == 201, r.text


# ===========================================================================
# Dashboard
# ===========================================================================


class TestDashboard:
    def test_needs_attention_reads_every_kind_of_fact(self, client, w):
        # reply overdue
        late = detail_of(new_enquiry(client, w, subject="Late reply"))
        overdue_reply(client, w, late)
        # follow-up requested for yesterday
        asked = detail_of(new_enquiry(client, w, subject="Call back", email="cb@x.com"))
        call(
            client,
            "PUT",
            f"/enquiries/{asked}/follow-up",
            w.staff,
            json={"date": (nz_today() - dt.timedelta(days=1)).isoformat()},
        )
        # an event in 3 days, not confirmed
        soon = book(client, w, days_ahead=3, name="Soon")
        # a confirmed event the client then edited
        edited = book(client, w, days_ahead=30, name="Edited")
        for section in ("details", "beverages", "food"):
            call(
                client,
                "POST",
                f"/events/{edited['id']}/sections/{section}/confirm",
                w.staff,
                json={},
            )
        events_svc.update_event(
            w.db,
            for_client(w.db, w.org.id, event_id=edited["id"], email="c@x.com"),
            edited["id"],
            guests=99,
        )
        # a quote-page decline nobody has acted on
        quoted = detail_of(new_enquiry(client, w, subject="Declined", email="d@x.com"))
        option = new_option(client, w, quoted, area=w.snug)
        min_spends_svc.decline_option(
            w.db,
            for_quote(w.db, w.org.id, enquiry_id=quoted),
            option["id"],
            channel="quote_page",
        )
        # a quote-page confirmation whose portal link hasn't gone out
        booked = detail_of(
            new_enquiry(client, w, subject="Booked online", email="e@x.com")
        )
        option2 = new_option(client, w, booked, area=w.snug)
        min_spends_svc.confirm_option(
            w.db,
            for_quote(w.db, w.org.id, enquiry_id=booked),
            option2["id"],
            channel="quote_page",
        )
        w.db.flush()

        r = call(client, "GET", "/dashboard", w.staff)
        assert r.status_code == 200, r.text
        d = r.json()
        kinds = {(i["kind"], i["title"]) for i in d["needs_attention"]}
        assert ("reply_overdue", "Late reply") in kinds
        assert ("requested", "Call back") in kinds
        assert ("unconfirmed_event", "Soon") in kinds
        assert ("client_edit", "Edited") in kinds
        assert ("quote_response", "Declined") in kinds
        assert any(
            i["kind"] == "quote_response" and i["label"].startswith("New booking")
            for i in d["needs_attention"]
        )
        first = d["needs_attention"][0]
        assert first["overdue"] is True
        assert d["events_unconfirmed_14_days"] >= 1
        assert d["booked_events"]["count"] >= 3
        assert d["open_enquiries"]["needs_reply"] == 1
        assert d["open_enquiries"]["follow_ups_due"] == 1
        assert d["pipeline"]["open"]["new"] >= 1
        assert d["enquiries_last_28_days"]["count"] == 4
        assert set(d["next_4_weeks"]) >= {"events", "expected_spend_cents", "weeks"}
        assert soon["id"] in {i["event_id"] for i in d["needs_attention"]}

        # Acting clears it: a reply-less decline stays until a new option.
        new_option(client, w, quoted, area=w.snug, amount=250_000)
        again = call(client, "GET", "/dashboard", w.staff).json()
        assert ("quote_response", "Declined") not in {
            (i["kind"], i["title"]) for i in again["needs_attention"]
        }

    def test_attention_items_carry_the_follow_up_reason(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w, subject="Imported"))
        reason = "Orbit marked this confirmed without a booking — check it."
        assert (
            call(
                client,
                "PUT",
                f"/enquiries/{enquiry_id}/follow-up",
                w.staff,
                json={
                    "date": (nz_today() - dt.timedelta(days=1)).isoformat(),
                    "reason": reason,
                },
            ).status_code
            == 200
        )
        book(client, w, days_ahead=3, name="Soon")
        items = call(client, "GET", "/dashboard", w.staff).json()["needs_attention"]
        assert items and all("follow_up_reason" in i for i in items)
        mine = next(i for i in items if i["enquiry_id"] == enquiry_id)
        assert mine["kind"] == "requested" and mine["follow_up_reason"] == reason
        soon = next(i for i in items if i["title"] == "Soon")
        assert soon["follow_up_reason"] is None

    def test_dashboard_is_scoped_to_the_callers_venues_and_org(self, client, w):
        hidden = book(
            client, w, user=w.owner, venue=w.venue2, days_ahead=2, name="Garden do"
        )
        mine = call(client, "GET", "/dashboard", w.staff).json()
        assert hidden["id"] not in {i["event_id"] for i in mine["needs_attention"]}
        owners = call(client, "GET", "/dashboard", w.owner).json()
        assert hidden["id"] in {i["event_id"] for i in owners["needs_attention"]}
        rival = call(client, "GET", "/dashboard", w.other_user).json()
        assert rival["needs_attention"] == [] and rival["booked_events"]["count"] == 0
        assert (
            call(
                client, "GET", f"/dashboard?venue={w.venue.id}", w.other_user
            ).status_code
            == 404
        )
        assert (
            call(client, "GET", f"/dashboard?venue={w.venue2.id}", w.staff).status_code
            == 404
        )
        group_wide = call(
            client,
            "POST",
            "/enquiries",
            w.owner,
            json={"contact": {"name": "G", "email": "g@x.com"}, "subject": "Anywhere"},
        ).json()["enquiry"]["id"]
        yesterday = (nz_today() - dt.timedelta(days=1)).isoformat()
        call(
            client,
            "PUT",
            f"/enquiries/{group_wide}/follow-up",
            w.owner,
            json={"date": yesterday},
        )
        unassigned = call(client, "GET", "/dashboard?venue=unassigned", w.owner).json()
        assert unassigned["booked_events"]["count"] == 0
        assert [
            (i["kind"], i["enquiry_id"]) for i in unassigned["needs_attention"]
        ] == [("requested", group_wide)]
        assert unassigned["open_enquiries"]["follow_ups_due"] == 1

    def test_shadow_mode_still_reads(self, client, w):
        book(client, w, days_ahead=5)
        w.org.functions_mode = "shadow"
        w.db.flush()
        assert call(client, "GET", "/dashboard", w.staff).status_code == 200
        assert call(client, "GET", "/reports/approvals", w.staff).status_code == 200


# ===========================================================================
# Reports
# ===========================================================================


class TestReports:
    def test_conversion_cohort_with_lag(self, client, w):
        contact = add_contact(w.db, w.org.id, "Old", "old@x.com")
        six_weeks = dt.datetime.now(UTC) - dt.timedelta(weeks=6)
        won = add_enquiry(
            w.db,
            w.org.id,
            contact.id,
            w.venue.id,
            booking_type="seated_dining",
            preferred_date_start=nz_today(),
            created_at=six_weeks,
        )
        lost = add_enquiry(w.db, w.org.id, contact.id, w.venue.id, created_at=six_weeks)
        add_enquiry(
            w.db, w.org.id, contact.id, w.venue.id, created_at=six_weeks
        )  # still open
        add_enquiry(w.db, w.org.id, contact.id, w.venue.id)  # too recent for the cohort
        w.db.add(
            FnEnquiryClosure(
                organization_id=w.org.id, enquiry_id=lost.id, kind="lost", reason="No"
            )
        )
        w.db.flush()
        assert (
            call(
                client,
                "POST",
                f"/enquiries/{won.id}/confirm-booking",
                w.staff,
                json={"channel": "phone"},
            ).status_code
            == 200
        )

        r = call(client, "GET", "/reports/conversion", w.staff).json()
        assert (r["enquiries"], r["confirmed"], r["lost"], r["open"]) == (3, 1, 1, 1)
        assert r["rate_pct"] == 50
        assert r["by_venue"][0]["venue_name"] == "Brew Bar"
        # The enquiries it counted, so the UI needn't rebuild them.
        rows = {x["id"]: x for x in r["rows"]}
        assert len(r["rows"]) == r["enquiries"]
        assert (rows[won.id]["outcome"], rows[won.id]["counted"]) == ("confirmed", True)
        assert (rows[lost.id]["outcome"], rows[lost.id]["counted"]) == ("lost", True)
        assert [x["outcome"] for x in r["rows"]].count("open") == 1
        assert sum(x["counted"] for x in r["rows"]) == r["confirmed"] + r["lost"]
        assert all(
            x["contact"] == "Old"
            and x["venue"] == "Brew Bar"
            and x["venue_id"] == w.venue.id
            and x["received"]
            for x in r["rows"]
        )
        assert (
            "rows"
            not in call(client, "GET", "/dashboard", w.staff).json()["conversion"]
        )
        none = call(client, "GET", "/reports/conversion?lag=0&weeks=1", w.staff).json()
        assert none["enquiries"] == 1 and none["rate_pct"] is None
        assert [(x["outcome"], x["counted"]) for x in none["rows"]] == [("open", False)]
        assert (
            call(client, "GET", "/reports/conversion?weeks=0", w.staff).status_code
            == 422
        )
        assert (
            call(client, "GET", "/reports/conversion", w.other_user).json()["enquiries"]
            == 0
        )

    def test_enquiries_over_time_with_prior_period(self, client, w):
        contact = add_contact(w.db, w.org.id, "Tim", "tim@x.com")
        noon = dt.time(12, 0)

        def at(day: dt.date):
            return dt.datetime.combine(day, noon, tzinfo=NZ).astimezone(UTC)

        d1 = dt.date(2026, 9, 10)
        for day, n in (
            (d1, 2),
            (d1 + dt.timedelta(days=2), 1),
            (d1 - dt.timedelta(days=5), 3),
        ):
            for _ in range(n):
                add_enquiry(w.db, w.org.id, contact.id, w.venue.id, created_at=at(day))
        r = call(
            client, "GET", "/reports/enquiries?from=2026-09-10&to=2026-09-16", w.staff
        ).json()
        assert r["total"] == 3 and r["prior_total"] == 3
        assert (r["prior_from"], r["prior_to"]) == ("2026-09-03", "2026-09-09")
        by_day = {d["date"]: d for d in r["days"]}
        assert by_day["2026-09-10"]["count"] == 2 and by_day["2026-09-12"]["count"] == 1
        assert by_day["2026-09-10"]["prior_date"] == "2026-09-03"
        assert by_day["2026-09-12"]["prior_count"] == 3 and r["change_pct"] == 0
        assert len(r["days"]) == 7
        new_only = call(
            client,
            "GET",
            "/reports/enquiries?from=2026-09-10&to=2026-09-16&stage=lost",
            w.staff,
        ).json()
        assert new_only["total"] == 0
        assert (
            call(
                client,
                "GET",
                "/reports/enquiries?from=2026-09-16&to=2026-09-10",
                w.staff,
            ).status_code
            == 422
        )
        assert (
            call(
                client,
                "GET",
                "/reports/enquiries?from=2026-09-10&to=2026-09-16",
                w.other_user,
            ).json()["total"]
            == 0
        )

    def test_upcoming_events_and_sales_by_week(self, client, w):
        book(client, w, days_ahead=1, bar_tab_cents=100_000, guests=10)
        book(client, w, days_ahead=20, bar_tab_cents=50_000, guests=30)
        book(client, w, days_ahead=200, bar_tab_cents=1, guests=1)
        r = call(client, "GET", "/reports/upcoming-events", w.staff).json()
        assert r["totals"] == {
            "events": 3,
            "guests": 41,
            "expected_spend_cents": 150_001,
        }
        assert [row["guests"] for row in r["rows"]] == [10, 30, 1]
        fortnight = call(
            client, "GET", "/reports/upcoming-events?days=14", w.staff
        ).json()
        assert fortnight["totals"]["events"] == 1
        sales = call(client, "GET", "/reports/sales", w.staff).json()
        assert sales["events"] == 2 and sales["expected_spend_cents"] == 150_000
        assert sum(wk["cents"] for wk in sales["weeks"]) == 150_000
        assert all(
            dt.date.fromisoformat(wk["week_start"]).weekday() == 0
            for wk in sales["weeks"]
        )
        assert call(client, "GET", "/reports/sales", w.other_user).json()["events"] == 0
        assert (
            call(client, "GET", "/reports/upcoming-events", w.other_user).json()[
                "totals"
            ]["events"]
            == 0
        )

    def test_lead_sources_from_enquiries_and_forms(self, client, w):
        contact = add_contact(w.db, w.org.id, "Leah", "leah@x.com")
        add_enquiry(
            w.db,
            w.org.id,
            contact.id,
            w.venue.id,
            source="web_form",
            utm_source="facebook",
            utm_medium="paid",
            utm_campaign="spring",
        )
        add_enquiry(
            w.db, w.org.id, contact.id, w.venue.id, source="web_form", gclid="abc"
        )
        add_enquiry(w.db, w.org.id, contact.id, w.venue.id, source="email")
        form = FnWebForm(
            organization_id=w.org.id,
            venue_id=w.venue.id,
            slug=f"f-{uuid.uuid4().hex[:8]}",
            name="Enquire",
        )
        w.db.add(form)
        w.db.flush()
        w.db.add_all(
            [
                FnFormSubmission(
                    organization_id=w.org.id,
                    form_id=form.id,
                    venue_id=w.venue.id,
                    payload={},
                    gclid="x1",
                ),
                FnFormSubmission(
                    organization_id=w.org.id,
                    form_id=form.id,
                    venue_id=w.venue.id,
                    payload={},
                    utm_source="instagram",
                ),
                FnFormSubmission(
                    organization_id=w.org.id,
                    form_id=form.id,
                    venue_id=w.venue.id,
                    payload={},
                    spam_reason="honeypot",
                ),
            ]
        )
        w.db.flush()
        r = call(client, "GET", "/reports/lead-sources", w.staff).json()
        assert r["enquiries"] == 3
        channels = {c["source"]: c["enquiries"] for c in r["by_channel"]}
        assert channels == {"web_form": 2, "email": 1}
        campaigns = {
            (c["source"], c["medium"], c["campaign"]) for c in r["by_campaign"]
        }
        assert ("facebook", "paid", "spring") in campaigns
        assert ("google", "cpc", "(not set)") in campaigns
        assert ("email", "(none)", "(not set)") in campaigns
        leads = {c["source"]: c["leads"] for c in r["form_leads"]}
        assert leads == {"google": 1, "instagram": 1}  # spam left out
        assert (
            call(client, "GET", "/reports/lead-sources", w.other_user).json()[
                "enquiries"
            ]
            == 0
        )

    def test_approvals_log(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        a = new_option(client, w, enquiry_id, label="A")
        b = new_option(client, w, enquiry_id, label="B", amount=300_000)
        call(client, "POST", f"/min-spends/{a['id']}/approve", w.approver)
        call(
            client,
            "POST",
            f"/min-spends/{b['id']}/override",
            w.overrider,
            json={"reason": "Regular"},
        )
        call(
            client,
            "PATCH",
            f"/min-spends/{a['id']}",
            w.staff,
            json={"amount_cents": 400_000},
        )  # automatic revoke
        r = call(client, "GET", "/reports/approvals", w.staff).json()
        assert [row["kind"] for row in r["rows"]] == ["revoke", "override", "approve"]
        assert r["rows"][0]["automatic"] is True and r["rows"][0]["actor"].startswith(
            "Norm"
        )
        assert (
            r["rows"][1]["reason"] == "Regular"
            and r["rows"][1]["actor"] == "Fran Functions"
        )
        assert (
            r["rows"][2]["contact"] == "Casey Client"
            and r["rows"][2]["area_name"] == "Deck"
        )
        assert r["totals"] == {
            "approve": {"count": 1, "cents": 500_000},
            "override": {"count": 1, "cents": 300_000},
            "revoke": {"count": 1, "automatic": 1},
        }
        assert [
            row["kind"]
            for row in call(
                client, "GET", "/reports/approvals?kind=override", w.staff
            ).json()["rows"]
        ] == ["override"]
        assert (
            call(client, "GET", "/reports/approvals?kind=nope", w.staff).status_code
            == 422
        )
        assert (
            call(client, "GET", "/reports/approvals", w.other_user).json()["rows"] == []
        )
        assert (
            call(
                client, "GET", f"/reports/approvals?venue={w.venue.id}", w.other_user
            ).status_code
            == 404
        )


# ===========================================================================
# The LoadedHub gateway (no network: the shared executor is faked)
# ===========================================================================


class TestHiddenVenueMoney:
    """Second security review: a group-wide enquiry can carry options, menus
    and a booking at a venue the caller can't see. Their amounts must not
    reach the caller's figures (list values and KPIs, the detail's value, the
    dashboard, lead sources, the agent's tools), and the approvals log must
    not name an enquiry the caller can't open."""

    def _group_wide(self, client, w):
        contact = add_contact(w.db, w.org.id, "Gee", f"{uuid.uuid4().hex[:6]}@x.com")
        enquiry = add_enquiry(
            w.db,
            w.org.id,
            contact.id,
            None,
            booking_type="large_event_exclusive",
            subject="Group-wide party",
        )
        r = call(
            client,
            "POST",
            f"/enquiries/{enquiry.id}/min-spends",
            w.owner,
            json={
                "venue_id": w.venue2.id,  # Garden Bar: the staff member can't see it
                "amount_cents": 987_600,
                "event_date": (nz_today() + dt.timedelta(days=40)).isoformat(),
            },
        )
        assert r.status_code == 201, r.text
        w.db.add(
            FnPreorderMenu(
                organization_id=w.org.id,
                enquiry_id=enquiry.id,
                venue_id=w.venue2.id,
                name="Garden set",
                is_set_menu=True,
                price_per_person_cents=5_000,
                guest_count=40,
            )
        )
        w.db.flush()
        return enquiry

    def test_values_leave_out_hidden_venues(self, client, w):
        enquiry = self._group_wide(client, w)
        detail = call(client, "GET", f"/enquiries/{enquiry.id}", w.staff).json()
        assert detail["options"] == [] and detail["value_cents"] == 0
        listed = call(client, "GET", "/enquiries", w.staff).json()
        row = next(r for r in listed["rows"] if r["id"] == enquiry.id)
        assert row["value_cents"] == 0
        assert row["min_spend_summary"] == {
            "count": 0,
            "largest_amount_cents": None,
            "needs_approval": False,
        }
        assert listed["kpis"]["open_value_cents"] == 0
        dash = call(client, "GET", "/dashboard", w.staff).json()
        assert dash["open_enquiries"]["open_value_cents"] == 0
        contact = call(client, "GET", f"/contacts/{enquiry.contact_id}", w.staff)
        assert contact.json()["enquiries"][0]["value_cents"] == 0
        by_value = call(client, "GET", "/enquiries?sort=value_desc", w.staff).json()
        assert by_value["rows"][0]["value_cents"] == 0
        # The owner sees Garden Bar, so its figures are theirs.
        owner = call(client, "GET", "/enquiries", w.owner).json()
        row = next(r for r in owner["rows"] if r["id"] == enquiry.id)
        assert row["value_cents"] == 987_600
        assert row["min_spend_summary"]["largest_amount_cents"] == 987_600
        assert owner["kpis"]["open_value_cents"] == 987_600

    def test_lead_sources_leave_out_a_booking_at_a_hidden_venue(self, client, w):
        contact = add_contact(w.db, w.org.id, "Lee", "lee@x.com")
        enquiry = add_enquiry(
            w.db,
            w.org.id,
            contact.id,
            None,
            booking_type="seated_dining",
            subject="Lead",
            preferred_date_start=nz_today() + dt.timedelta(days=30),
        )
        r = call(
            client,
            "POST",
            f"/enquiries/{enquiry.id}/confirm-booking",
            w.owner,
            json={"channel": "phone", "venue_id": w.venue2.id},
        )
        assert r.status_code == 200, r.text
        event_id = r.json()["event"]["event"]["id"]
        assert (
            call(
                client,
                "PATCH",
                f"/events/{event_id}",
                w.owner,
                json={"bar_tab_cents": 432_100},
            ).status_code
            == 200
        )

        def booked(user):
            body = call(client, "GET", "/reports/lead-sources", user).json()
            return (
                sum(ch["confirmed"] for ch in body["by_channel"]),
                sum(ch["confirmed_value_cents"] for ch in body["by_channel"]),
            )

        assert booked(w.staff) == (1, 0)  # the enquiry is theirs; the booking isn't
        confirmed, value = booked(w.owner)
        assert confirmed == 1 and value >= 432_100

    def test_approvals_log_names_no_enquiry_the_caller_cant_open(self, client, w):
        contact = add_contact(w.db, w.org.id, "Gala Guest", "gala@x.com")
        enquiry = add_enquiry(
            w.db, w.org.id, contact.id, w.venue2.id, subject="Secret Gala"
        )
        option = FnMinSpend(
            organization_id=w.org.id,
            enquiry_id=enquiry.id,
            venue_id=w.venue.id,  # an option at the staff member's venue
            area_id=w.deck.id,
            amount_cents=500_000,
        )
        w.db.add(option)
        w.db.flush()
        w.db.add(
            FnMinSpendApproval(
                organization_id=w.org.id,
                min_spend_id=option.id,
                kind="approve",
                actor_user_id=w.approver.id,
                amount_cents=500_000,
            )
        )
        w.db.flush()

        def logged(user):
            rows = call(client, "GET", "/reports/approvals", user).json()["rows"]
            return next(r for r in rows if r["min_spend_id"] == option.id)

        staff = logged(w.staff)
        assert (staff["enquiry_id"], staff["enquiry_subject"], staff["contact"]) == (
            None,
            None,
            None,
        )
        assert staff["amount_cents"] == 500_000  # the approval at their venue shows
        owner = logged(w.owner)
        assert (owner["enquiry_id"], owner["enquiry_subject"], owner["contact"]) == (
            enquiry.id,
            "Secret Gala",
            "Gala Guest",
        )

    def test_agent_tools_leave_out_hidden_venues(self, client, w, monkeypatch):
        from app.agents import functions_tools
        from app.agents import internal_tools
        from tests.conftest import _make_thread

        @contextmanager
        def same_session():
            yield w.db

        monkeypatch.setattr(functions_tools, "_config_db", same_session)
        enquiry = self._group_wide(client, w)

        def tool(user, name, **params):
            thread = _make_thread(
                w.db,
                user,
                domain="functions",
                intent="functions.tool_use",
                status="in_progress",
            )
            return internal_tools.get_handler("norm_functions", name)(
                params, w.db, thread.id
            )

        got = tool(w.staff, "get_enquiry", enquiry_id=enquiry.id)
        assert got["success"], got
        # The tools leave a zero value out altogether.
        assert not got["data"].get("options") and not got["data"].get("value_cents")
        found = tool(w.staff, "find_enquiries")["data"]["enquiries"]
        assert not next(r for r in found if r["id"] == enquiry.id).get("value_cents")
        assert not got["data"].get("food_cents")  # the Garden Bar menu's food
        assert not got["data"].get("menus")
        found = tool(w.owner, "find_enquiries")["data"]["enquiries"]
        assert next(r for r in found if r["id"] == enquiry.id)["value_cents"] == 987_600
        theirs = tool(w.owner, "get_enquiry", enquiry_id=enquiry.id)["data"]
        assert (theirs["value_cents"], theirs["food_cents"]) == (987_600, 200_000)


class TestLoadedGateway:
    def test_loaded_call_goes_through_the_shared_executor_venue_strict(
        self, w, monkeypatch
    ):
        from app.connectors import tool_executor
        from app.connectors.tool_executor import ToolResult
        from app.services.functions import reports

        seen = {}

        def fake(connector, action, params, db, config_db, **kw):
            seen.update(connector=connector, action=action, params=params, **kw)
            return ToolResult(success=True, payload={"menus": []})

        monkeypatch.setattr(tool_executor, "execute_connector_tool", fake)
        assert reports.loaded_call(w.db, w.db, w.venue, "get_menus", {"x": 1}) == (
            {"menus": []},
            None,
        )
        assert seen == {
            "connector": "loadedhub",
            "action": "get_menus",
            "params": {"x": 1},
            "venue_id": w.venue.id,
            "strict_venue": True,  # never another venue's credentials
        }

    def test_loaded_call_turns_every_failure_into_a_reason(self, w, monkeypatch):
        from app.connectors import tool_executor
        from app.connectors.tool_executor import ToolResult
        from app.services.functions import reports

        cases = [
            (
                ToolResult(success=False, payload=None, error="Connector not found"),
                "Connector not found",
            ),
            (
                ToolResult(success=True, payload={"error": "no venue: pass venues"}),
                "no venue: pass venues",
            ),
            (ToolResult(success=False, payload=None), "LoadedHub returned an error."),
        ]
        for result, reason in cases:
            monkeypatch.setattr(
                tool_executor, "execute_connector_tool", lambda *a, _r=result, **k: _r
            )
            assert reports.loaded_call(w.db, w.db, w.venue, "get_sales", {}) == (
                None,
                reason,
            )

        def boom(*a, **k):
            raise RuntimeError("socket closed")

        monkeypatch.setattr(tool_executor, "execute_connector_tool", boom)
        assert reports.loaded_call(w.db, w.db, w.venue, "get_sales", {}) == (
            None,
            "LoadedHub isn't reachable right now.",
        )
