"""The Norm Functions staff API — events, pre-order menus, prints and the
calendar, through HTTP.

The rules underneath are the services' (and their own tests'); these hold
the HTTP layer to them: versions round-trip, run-sheet times parse, a large
event can't be entered directly, shadow mode refuses, prints leave out what
the venue switched off and price set menus properly, and another
organisation (or a venue the caller can't see) is a 404 on every route.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.config import settings
from app.db.functions_models import (
    FnEventChange,
    FnPublicToken,
    FnPreorderTemplate,
    FnPreorderTemplateItem,
    FnVenueFunctionSettings,
)
from app.services.functions import events as events_svc
from app.services.functions import public_tokens
from app.services.functions.ctx import for_client
from tests.functions_world import add_contact, add_enquiry, make_world
from tests.test_functions_api import H, DATE, call, detail_of, new_enquiry, new_option


@pytest.fixture
def w(db_session):
    return make_world(db_session)


def direct_booking(client, w, *, user=None, venue=None, **kw):
    body = {
        "venue_id": (venue or w.venue).id,
        "event_date": DATE,
        "booking_type": "seated_dining",
        "contact": {"name": "Robin Diner", "email": "robin@example.com"},
        "start_time": "18:30",
        "end_time": "22:00",
        "guests": 12,
        **kw,
    }
    r = call(client, "POST", "/events", user or w.staff, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def template(w, *, set_menu=True, venue=None):
    t = FnPreorderTemplate(
        organization_id=w.org.id,
        venue_id=(venue or w.venue).id,
        name="Canapés" if set_menu else "Platters",
        is_set_menu=set_menu,
        price_per_person_cents=4500 if set_menu else None,
    )
    w.db.add(t)
    w.db.flush()
    rows = [
        ("header", "Hot", None, None, []),
        ("item", "Arancini", 50, 1500, ["V"]),
        ("item", "Sliders", 50, 2200, []),
        ("header", "Cold", None, None, []),
        ("item", "Oysters", 34, 3000, ["GF", "DF"]),
        ("item", "Tartlets", 33, 1200, ["V"]),
        ("item", "Crudo", 33, 1800, ["GF"]),
    ]
    for i, (kind, name, pct, price, diet) in enumerate(rows):
        w.db.add(
            FnPreorderTemplateItem(
                organization_id=w.org.id,
                template_id=t.id,
                kind=kind,
                name=name,
                default_percent=pct,
                unit_price_cents=price,
                dietaries=diet,
                sort_order=i,
            )
        )
    w.db.flush()
    return t


def booked_large_event(client, w):
    enquiry_id = detail_of(new_enquiry(client, w))
    option = new_option(client, w, enquiry_id)
    call(client, "POST", f"/min-spends/{option['id']}/approve", w.approver)
    r = call(
        client,
        "POST",
        f"/min-spends/{option['id']}/confirm",
        w.staff,
        json={"channel": "phone"},
    )
    assert r.status_code == 200, r.text
    return enquiry_id, r.json()["event"]


# ===========================================================================
# Events
# ===========================================================================


class TestEvents:
    def test_direct_booking_and_detail(self, client, w):
        detail = direct_booking(client, w, allergies="Coeliac x2", bar_tab_cents=50_000)
        event = detail["event"]
        assert event["created_via"] == "direct"
        assert event["start_time"] == "18:30" and event["guests"] == 12
        assert event["allergies"] == "Coeliac x2"
        assert detail["status"]["status"] == "booked"
        assert detail["money"]["expected_spend_cents"] == 50_000
        assert detail["contact"]["email"] == "robin@example.com"
        assert detail["sections"]["details"] == {"kind": None, "done": False}
        assert detail["enabled_fields"]["beverages.bar_tab"] is True
        assert detail["changes"][0]["kind"] == "created"
        got = call(client, "GET", f"/events/{event['id']}", w.staff).json()
        assert got["event"]["version"] == event["version"]

    def test_update_with_versions_and_post_confirmation_note(self, client, w):
        """Also: a contact correction is logged."""
        detail = direct_booking(client, w)
        event_id, version = detail["event"]["id"], detail["event"]["version"]
        r = call(
            client,
            "PATCH",
            f"/events/{event_id}",
            w.staff,
            json={
                "version": version,
                "guests": 14,
                "bar_tab_cents": 20_000,
                "food_time": "19:30",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["event"]["guests"] == 14 and body["event"]["food_time"] == "19:30"
        assert len(body["logged"]) == 3  # one change row per section touched
        stale = call(
            client,
            "PATCH",
            f"/events/{event_id}",
            w.staff,
            json={"version": version, "guests": 1},
        )
        assert stale.status_code == 409
        # A contact correction is logged on the event.
        r = call(
            client,
            "PATCH",
            f"/events/{event_id}/contact",
            w.staff,
            json={"phone": "021 555"},
        )
        assert r.status_code == 200
        assert r.json()["contact"]["phone"] == "021 555"
        assert "contact.phone" in r.json()["changes"][-1]["changes"]

        for section in ("details", "beverages", "food"):
            r = call(
                client,
                "POST",
                f"/events/{event_id}/sections/{section}/confirm",
                w.staff,
                json={},
            )
            assert r.status_code == 200, r.text
        assert r.json()["status"]["status"] == "confirmed"
        r = call(
            client,
            "PATCH",
            f"/events/{event_id}",
            w.staff,
            json={"notes": "Cake at 9", "note": "Client rang"},
        )
        last = r.json()["changes"][-1]
        assert last["after_confirmation"] is True and last["note"] == "Client rang"

    def test_bad_bookings_and_values_are_refused(self, client, w):
        """A large event can't be entered directly (it comes from a
        minimum-spend confirmation), a booking needs a contact, and an edit
        with unknown fields or bad values is a readable 422."""
        r = call(
            client,
            "POST",
            "/events",
            w.staff,
            json={
                "venue_id": w.venue.id,
                "event_date": DATE,
                "booking_type": "large_event_exclusive",
                "contact": {"name": "X", "email": "x@x.com"},
            },
        )
        assert r.status_code == 422
        assert "minimum-spend" in r.json()["detail"]["message"]
        r = call(
            client,
            "POST",
            "/events",
            w.staff,
            json={
                "venue_id": w.venue.id,
                "event_date": DATE,
                "booking_type": "seated_dining",
            },
        )
        assert r.status_code == 422

        event_id = direct_booking(client, w)["event"]["id"]
        assert (
            call(
                client, "PATCH", f"/events/{event_id}", w.staff, json={"colour": "red"}
            ).status_code
            == 422
        )
        assert (
            call(
                client,
                "PATCH",
                f"/events/{event_id}",
                w.staff,
                json={"food_timing_type": "whenever"},
            ).status_code
            == 422
        )
        assert (
            call(
                client,
                "PATCH",
                f"/events/{event_id}",
                w.staff,
                json={
                    "start_time": "23:00",
                    "end_time": "22:00",
                    "ends_next_day": False,
                },
            ).status_code
            == 422
        )
        assert (
            call(
                client,
                "PATCH",
                f"/events/{event_id}",
                w.staff,
                json={"area_id": w.deck.id},
            ).status_code
            == 200
        )
        # A null for a field that can't be empty is a readable 422, not a
        # database error; a null for a nullable one clears it.
        r = call(
            client,
            "PATCH",
            f"/events/{event_id}",
            w.staff,
            json={"ends_next_day": None, "bar_tab_cents": None},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["message"] == (
            "bar_tab_cents, ends_next_day can't be empty."
        )
        cleared = call(
            client, "PATCH", f"/events/{event_id}", w.staff, json={"area_id": None}
        )
        assert cleared.status_code == 200 and cleared.json()["event"]["area_id"] is None

    def test_children_replace_whole_lists(self, client, w):
        detail = direct_booking(client, w)
        event_id = detail["event"]["id"]
        rows = [
            {"at_time": "18:30", "activity": "Guests arrive"},
            {"label": "After speeches", "activity": "Cake"},
            {"at_time": "00:30", "ends_next_day": True, "activity": "Last drinks"},
        ]
        r = call(
            client,
            "PUT",
            f"/events/{event_id}/children/timings",
            w.staff,
            json={"version": detail["event"]["version"], "rows": rows},
        )
        assert r.status_code == 200, r.text
        timings = r.json()["children"]["timings"]
        assert [t["at_time"] for t in timings] == ["18:30", None, "00:30"]
        assert timings[2]["ends_next_day"] is True
        assert (
            call(
                client,
                "PUT",
                f"/events/{event_id}/children/av",
                w.staff,
                json={"rows": [{"kind": "laser"}]},
            ).status_code
            == 422
        )
        assert (
            call(
                client,
                "PUT",
                f"/events/{event_id}/children/timings",
                w.staff,
                json={"rows": [{"at_time": "7pm"}]},
            ).status_code
            == 422
        )
        assert (
            call(
                client,
                "PUT",
                f"/events/{event_id}/children/parking",
                w.staff,
                json={"rows": []},
            ).status_code
            == 422
        )
        r = call(
            client,
            "PUT",
            f"/events/{event_id}/children/entertainment",
            w.staff,
            json={
                "rows": [
                    {
                        "kind": "dj",
                        "name": "DJ Sam",
                        "start_time": "21:00",
                        "end_time": "01:00",
                        "ends_next_day": True,
                    }
                ]
            },
        )
        assert r.json()["children"]["entertainment"][0]["name"] == "DJ Sam"

    def test_sections_skip_needs_a_note_and_reopen(self, client, w):
        event_id = direct_booking(client, w)["event"]["id"]
        assert (
            call(
                client,
                "POST",
                f"/events/{event_id}/sections/food/skip",
                w.staff,
                json={},
            ).status_code
            == 422
        )
        r = call(
            client,
            "POST",
            f"/events/{event_id}/sections/food/skip",
            w.staff,
            json={"note": "No food"},
        )
        assert r.json()["sections"]["food"]["kind"] == "skipped"
        assert r.json()["sections"]["food"]["actor"] == "Sam Staff"
        r = call(client, "POST", f"/events/{event_id}/sections/food/reopen", w.staff)
        assert r.json()["sections"]["food"]["done"] is False
        assert (
            call(
                client,
                "POST",
                f"/events/{event_id}/sections/drinks/skip",
                w.staff,
                json={"note": "x"},
            ).status_code
            == 422
        )

    def test_cancel_needs_a_reason_and_reinstates(self, client, w):
        enquiry_id, event = booked_large_event(client, w)
        event_id = event["event"]["id"]
        assert (
            call(
                client, "POST", f"/events/{event_id}/cancel", w.staff, json={}
            ).status_code
            == 422
        )
        r = call(
            client,
            "POST",
            f"/events/{event_id}/cancel",
            w.staff,
            json={"reason": "Venue flooded", "version": event["event"]["version"]},
        )
        assert r.status_code == 200 and r.json()["status"]["status"] == "cancelled"
        assert (
            call(client, "GET", f"/enquiries/{enquiry_id}", w.staff).json()["stage"]
            == "lost"
        )
        assert (
            call(
                client, "PATCH", f"/events/{event_id}", w.staff, json={"guests": 3}
            ).status_code
            == 409
        )
        assert (
            call(
                client,
                "POST",
                f"/events/{event_id}/cancel",
                w.staff,
                json={"reason": "again"},
            ).status_code
            == 409
        )
        r = call(
            client,
            "POST",
            f"/events/{event_id}/reinstate",
            w.staff,
            json={"reason": "Dried out"},
        )
        assert r.json()["status"]["status"] == "booked"
        assert (
            call(client, "GET", f"/enquiries/{enquiry_id}", w.staff).json()["stage"]
            == "confirmed"
        )

    def test_list_filters_and_calendar(self, client, w):
        """Also: rows and calendar days carry the contact's company."""
        a = direct_booking(
            client, w, name="Early", event_date="2026-12-01", bar_tab_cents=10_000
        )
        direct_booking(
            client,
            w,
            name="Late",
            event_date="2026-12-20",
            bar_tab_cents=30_000,
            contact={"name": "Lee", "email": "lee@x.com", "company": "Acme"},
        )
        cancelled = direct_booking(
            client,
            w,
            name="Off",
            event_date="2026-12-20",
            contact={"name": "Max", "email": "max@x.com"},
        )
        call(
            client,
            "POST",
            f"/events/{cancelled['event']['id']}/cancel",
            w.staff,
            json={"reason": "No"},
        )

        listed = call(
            client, "GET", "/events?from=2026-12-01&to=2026-12-31", w.staff
        ).json()
        assert [r["name"] for r in listed["rows"]] == ["Early", "Late"]
        assert listed["rows"][0]["expected_spend_cents"] == 10_000
        assert listed["rows"][1]["contact_company"] == "Acme"
        assert (
            len(
                call(
                    client,
                    "GET",
                    "/events?from=2026-12-01&include_cancelled=true",
                    w.staff,
                ).json()["rows"]
            )
            == 3
        )
        assert [
            r["name"]
            for r in call(client, "GET", "/events?status=cancelled", w.staff).json()[
                "rows"
            ]
        ] == ["Off"]
        assert [
            r["name"]
            for r in call(client, "GET", "/events?q=earl", w.staff).json()["rows"]
        ] == ["Early"]
        paged = call(client, "GET", "/events?limit=1", w.staff).json()
        assert paged["has_more"] is True

        cal = call(client, "GET", "/events/calendar?month=2026-12", w.staff).json()
        assert (
            cal["from"] == "2026-11-30" and cal["to"] == "2027-01-03"
        )  # Monday to Sunday
        days = {d["date"]: d for d in cal["days"]}
        assert days["2026-12-01"]["expected_spend_cents"] == 10_000
        assert [e["name"] for e in days["2026-12-20"]["events"]] == ["Late"]
        assert days["2026-12-20"]["events"][0]["contact_company"] == "Acme"
        assert cal["expected_spend_cents"] == 40_000 and cal["events"] == 2
        with_cancelled = call(
            client,
            "GET",
            "/events/calendar?month=2026-12&include_cancelled=true",
            w.staff,
        ).json()
        day20 = next(d for d in with_cancelled["days"] if d["date"] == "2026-12-20")
        assert len(day20["events"]) == 2 and day20["expected_spend_cents"] == 30_000
        assert (
            call(client, "GET", "/events/calendar?month=December", w.staff).status_code
            == 422
        )
        assert a["event"]["id"] in {e["id"] for d in cal["days"] for e in d["events"]}

    def test_timeline_includes_client_edits(self, client, w):
        event_id = direct_booking(client, w)["event"]["id"]
        client_ctx = for_client(
            w.db, w.org.id, event_id=event_id, email="robin@example.com"
        )
        events_svc.update_event(w.db, client_ctx, event_id, guests=20)
        w.db.flush()
        items = call(client, "GET", f"/events/{event_id}/timeline", w.staff).json()[
            "items"
        ]
        assert items[0]["kind"] == "event_edited" and "client" in items[0]["title"]
        assert items[0]["actor"] == "robin@example.com"
        detail = call(client, "GET", f"/events/{event_id}", w.staff).json()
        assert detail["status"]["status"] == "in_progress"


# ===========================================================================
# Prints
# ===========================================================================


class TestPrints:
    def test_full_run_sheet_and_food_sheets(self, client, w):
        detail = direct_booking(
            client,
            w,
            allergies="Nut allergy",
            bar_tab_cents=40_000,
            food_timing_type="staggered_arrival",
        )
        event_id = detail["event"]["id"]
        t = template(w)
        menu = call(
            client,
            "POST",
            f"/events/{event_id}/menus",
            w.staff,
            json={"template_id": t.id, "guest_count": 12},
        ).json()
        call(client, "POST", f"/menus/{menu['id']}/distribute", w.staff, json={})
        call(
            client,
            "PUT",
            f"/events/{event_id}/children/timings",
            w.staff,
            json={"rows": [{"at_time": "18:30", "activity": "Arrive"}]},
        )

        full = call(client, "GET", f"/events/{event_id}/print/full", w.staff).json()
        assert (
            full["event"]["name"]
            and full["event"]["contact"]["email"] == "robin@example.com"
        )
        assert (
            full["details"] is not None and full["beverages"]["bar_tab_cents"] == 40_000
        )
        assert full["timings"]["schedule"][0]["activity"] == "Arrive"
        food = full["food"]
        assert food["allergies"] == "Nut allergy"
        assert food["timing"]["label"].startswith("Staggered")
        printed = food["menus"][0]
        assert printed["total_cents"] == 12 * 4500  # set menu: guests x per person
        dishes = [ln for ln in printed["lines"] if ln["kind"] == "item"]
        assert sum(d["quantity"] for d in dishes) == 24  # 12 per section, 2 sections
        assert any(d["dietaries"] == ["GF", "DF"] for d in dishes)
        assert full["money"]["expected_spend_cents"] == max(0, 54_000 + 40_000)

        run = call(client, "GET", f"/events/{event_id}/print/run_sheet", w.staff).json()
        assert run["food"] is None and run["timings"] is not None

        kitchen = call(client, "GET", f"/events/{event_id}/print/food", w.staff).json()
        assert kitchen["details"] is None and kitchen["beverages"] is None
        assert {c["name"] for c in kitchen["food"]["kitchen_counts"]} == {
            "Arancini",
            "Sliders",
            "Oysters",
            "Tartlets",
            "Crudo",
        }
        assert (
            call(client, "GET", f"/events/{event_id}/print/menu", w.staff).status_code
            == 422
        )

    def test_prints_leave_out_switched_off_fields_and_show_skips(self, client, w):
        s = w.db.query(FnVenueFunctionSettings).filter_by(venue_id=w.venue.id).one()
        s.enabled_fields = {
            "seated_dining": {"beverages.bar_tab": False, "food.allergies": False}
        }
        w.db.flush()
        event_id = direct_booking(client, w, allergies="Nuts", bar_tab_cents=1000)[
            "event"
        ]["id"]
        call(
            client,
            "POST",
            f"/events/{event_id}/sections/beverages/skip",
            w.staff,
            json={"note": "Cash bar"},
        )
        full = call(client, "GET", f"/events/{event_id}/print/full", w.staff).json()
        assert full["beverages"]["bar_tab_cents"] is None
        assert full["food"]["allergies"] is None
        assert full["skips"]["beverages"]["note"] == "Cash bar"
        assert full["skips"]["beverages"]["by"] == "Sam Staff"


# ===========================================================================
# Menus
# ===========================================================================


class TestMenus:
    def test_template_menu_on_an_enquiry_and_option_totals(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        option = new_option(client, w, enquiry_id, amount=200_000)
        t = template(w)
        r = call(
            client,
            "POST",
            f"/enquiries/{enquiry_id}/menus",
            w.staff,
            json={"template_id": t.id, "guest_count": 60},
        )
        assert r.status_code == 201, r.text
        menu = r.json()
        assert menu["is_set_menu"] and menu["total_cents"] == 60 * 4500
        assert all(
            i["unit_price_cents"] is None for i in menu["items"]
        )  # set-menu dishes unpriced
        assert [
            i["suggested_quantity"] for i in menu["items"] if i["kind"] == "item"
        ] == [30, 30, 20, 20, 20]
        distributed = call(
            client,
            "POST",
            f"/menus/{menu['id']}/distribute",
            w.staff,
            json={"guest_count": 61},
        )
        assert distributed.status_code == 200, distributed.text
        assert distributed.json()["guest_count"] == 61
        assert [
            i["quantity"] for i in distributed.json()["items"] if i["kind"] == "item"
        ] == [31, 30, 21, 20, 20]

        detail = call(client, "GET", f"/enquiries/{enquiry_id}", w.staff).json()
        opt = next(o for o in detail["options"] if o["id"] == option["id"])
        assert opt["food_cents"] == 61 * 4500
        assert opt["total_cents"] == max(200_000, 61 * 4500)
        assert detail["value_cents"] == opt["total_cents"]

    def test_hand_built_menu_items_quantities_and_delete(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        r = call(
            client,
            "POST",
            f"/enquiries/{enquiry_id}/menus",
            w.staff,
            json={
                "name": "Platters",
                "items": [{"name": "Cheese", "unit_price_cents": 9000, "quantity": 2}],
            },
        )
        assert r.status_code == 201, r.text
        menu = r.json()
        assert menu["venue_id"] == w.venue.id and menu["total_cents"] == 18_000
        r = call(
            client,
            "PUT",
            f"/menus/{menu['id']}/items",
            w.staff,
            json={
                "version": menu["version"],
                "items": [
                    {"name": "Cheese", "unit_price_cents": 9000, "quantity": 2},
                    {"name": "Fruit", "unit_price_cents": 5000, "quantity": 1},
                ],
            },
        )
        assert r.status_code == 200 and r.json()["total_cents"] == 23_000
        menu = r.json()
        fruit = next(i for i in menu["items"] if i["name"] == "Fruit")
        r = call(
            client,
            "PUT",
            f"/menus/{menu['id']}/quantities",
            w.staff,
            json={"version": menu["version"], "quantities": {fruit["id"]: 3}},
        )
        assert r.status_code == 200 and r.json()["total_cents"] == 33_000
        stale = call(
            client,
            "PUT",
            f"/menus/{menu['id']}/quantities",
            w.staff,
            json={"version": menu["version"], "quantities": {fruit["id"]: 4}},
        )
        assert stale.status_code == 409
        r = call(
            client, "PATCH", f"/menus/{menu['id']}", w.staff, json={"name": "Grazing"}
        )
        assert r.json()["name"] == "Grazing"
        assert (
            call(
                client, "POST", f"/menus/{menu['id']}/distribute", w.staff, json={}
            ).status_code
            == 422
        )  # not a set menu
        assert (
            call(client, "DELETE", f"/menus/{menu['id']}", w.staff).status_code == 204
        )
        assert (
            call(client, "GET", f"/enquiries/{enquiry_id}", w.staff).json()["menus"]
            == []
        )

    def test_menu_edits_on_an_event_are_logged_as_food(self, client, w):
        event_id = direct_booking(client, w)["event"]["id"]
        t = template(w, set_menu=False)
        menu = call(
            client,
            "POST",
            f"/events/{event_id}/menus",
            w.staff,
            json={"template_id": t.id},
        ).json()
        arancini = next(i for i in menu["items"] if i["name"] == "Arancini")
        call(
            client,
            "PUT",
            f"/menus/{menu['id']}/quantities",
            w.staff,
            json={"quantities": {arancini["id"]: 10}},
        )
        food_rows = (
            w.db.query(FnEventChange)
            .filter_by(event_id=event_id, section="food")
            .count()
        )
        assert food_rows >= 2
        detail = call(client, "GET", f"/events/{event_id}", w.staff).json()
        assert detail["money"]["food_cents"] == 15_000

    def test_template_from_another_venue_is_refused(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        other = template(w, venue=w.venue2)
        r = call(
            client,
            "POST",
            f"/enquiries/{enquiry_id}/menus",
            w.owner,
            json={"template_id": other.id},
        )
        assert r.status_code == 422
        # Staff can't even see a template at a venue they lack.
        assert (
            call(
                client,
                "POST",
                f"/enquiries/{enquiry_id}/menus",
                w.staff,
                json={"template_id": other.id},
            ).status_code
            == 404
        )


# ===========================================================================
# Shadow mode, tenancy, venue scoping
# ===========================================================================


class TestEventIsolation:
    def test_booking_a_customer_another_venue_knows_reveals_nothing(self, client, w):
        # Vic is known only at Garden Bar, which the staff member can't see.
        vic = add_contact(w.db, w.org.id, "Vic Visitor", "vic@example.com")
        add_enquiry(w.db, w.org.id, vic.id, w.venue2.id, subject="Garden drinks")
        assert call(client, "GET", f"/contacts/{vic.id}", w.staff).status_code == 404

        def book(name, email):
            return call(
                client,
                "POST",
                "/events",
                w.staff,
                json={
                    "venue_id": w.venue.id,
                    "event_date": DATE,
                    "booking_type": "seated_dining",
                    "contact": {"name": name, "email": email},
                },
            )

        known = book("Vic Visitor", "Vic@Example.com")
        new = book("Nell New", "nell@example.com")
        # The same answer as for a brand-new customer...
        assert (known.status_code, new.status_code) == (201, 201), known.text
        assert set(known.json()) == set(new.json())
        assert set(known.json()["event"]) == set(new.json()["event"])
        # ...attached to the org's one contact, not a duplicate.
        assert known.json()["event"]["contact_id"] == vic.id
        assert call(client, "GET", f"/contacts/{vic.id}", w.staff).status_code == 200

    def test_shadow_mode_refuses_event_and_menu_writes(self, client, w):
        detail = direct_booking(client, w)
        event_id = detail["event"]["id"]
        menu = call(
            client,
            "POST",
            f"/events/{event_id}/menus",
            w.staff,
            json={"name": "M", "items": []},
        ).json()
        w.org.functions_mode = "shadow"
        w.db.flush()
        assert call(client, "GET", f"/events/{event_id}", w.staff).status_code == 200
        assert (
            call(client, "GET", f"/events/{event_id}/print/full", w.staff).status_code
            == 200
        )
        for method, path, body in (
            (
                "POST",
                "/events",
                {
                    "venue_id": w.venue.id,
                    "event_date": DATE,
                    "booking_type": "seated_dining",
                    "contact": {"name": "x", "email": "x@x.com"},
                },
            ),
            ("PATCH", f"/events/{event_id}", {"guests": 1}),
            ("PUT", f"/events/{event_id}/children/timings", {"rows": []}),
            ("POST", f"/events/{event_id}/sections/food/skip", {"note": "x"}),
            ("POST", f"/events/{event_id}/cancel", {"reason": "x"}),
            ("PATCH", f"/menus/{menu['id']}", {"name": "y"}),
            ("DELETE", f"/menus/{menu['id']}", None),
            ("POST", f"/events/{event_id}/portal-link", None),
        ):
            r = call(client, method, path, w.staff, json=body)
            assert r.status_code == 409, (path, r.text)
            assert r.json()["detail"]["code"] == "not_live"

    def test_another_organisation_gets_404(self, client, w):
        detail = direct_booking(client, w)
        event_id = detail["event"]["id"]
        menu = call(
            client,
            "POST",
            f"/events/{event_id}/menus",
            w.staff,
            json={"name": "M", "items": []},
        ).json()
        rival = w.other_user
        for method, path, body in (
            ("GET", f"/events/{event_id}", None),
            ("PATCH", f"/events/{event_id}", {"guests": 1}),
            ("PATCH", f"/events/{event_id}/contact", {"phone": "1"}),
            ("PUT", f"/events/{event_id}/children/timings", {"rows": []}),
            ("POST", f"/events/{event_id}/sections/food/confirm", None),
            ("POST", f"/events/{event_id}/cancel", {"reason": "x"}),
            ("POST", f"/events/{event_id}/reinstate", None),
            ("GET", f"/events/{event_id}/timeline", None),
            ("GET", f"/events/{event_id}/print/full", None),
            ("POST", f"/events/{event_id}/menus", {"name": "x"}),
            ("PATCH", f"/menus/{menu['id']}", {"name": "x"}),
            ("PUT", f"/menus/{menu['id']}/items", {"items": []}),
            ("PUT", f"/menus/{menu['id']}/quantities", {"quantities": {}}),
            ("POST", f"/menus/{menu['id']}/distribute", None),
            ("DELETE", f"/menus/{menu['id']}", None),
            ("POST", f"/events/{event_id}/portal-link", None),
        ):
            r = call(client, method, path, rival, json=body)
            assert r.status_code == 404, (method, path, r.status_code, r.text)
        assert call(client, "GET", "/events", rival).json()["rows"] == []
        cal = call(client, "GET", "/events/calendar?month=2026-12", rival).json()
        assert cal["events"] == 0
        r = call(
            client,
            "POST",
            "/events",
            rival,
            json={
                "venue_id": w.venue.id,
                "event_date": DATE,
                "booking_type": "seated_dining",
                "contact": {"name": "x", "email": "x@x.com"},
            },
        )
        assert r.status_code == 403

    def test_staff_without_the_venue(self, client, w):
        hidden = direct_booking(client, w, user=w.owner, venue=w.venue2)["event"]["id"]
        assert call(client, "GET", f"/events/{hidden}", w.staff).status_code == 404
        assert (
            call(client, "GET", f"/events/{hidden}/print/food", w.staff).status_code
            == 404
        )
        assert all(
            r["id"] != hidden
            for r in call(client, "GET", "/events", w.staff).json()["rows"]
        )
        contact = add_contact(w.db, w.org.id, "Zed", "zed@x.com")
        r = call(
            client,
            "POST",
            "/events",
            w.staff,
            json={
                "venue_id": w.venue2.id,
                "event_date": DATE,
                "booking_type": "seated_dining",
                "contact_id": contact.id,
            },
        )
        assert r.status_code == 403


# ===========================================================================
# Additions for the events UI: table-plan file, contact company, portal link
# ===========================================================================


class TestEventAdditions:
    def test_table_plan_file(self, client, w):
        from tests.test_functions_api_settings import png, upload

        detail = direct_booking(client, w)
        event_id = detail["event"]["id"]
        assert detail["event"]["table_plan_file_id"] is None
        assert detail["event"]["table_plan_file_url"] is None
        f = upload(client, w.staff, png()).json()
        r = call(
            client,
            "PATCH",
            f"/events/{event_id}",
            w.staff,
            json={"table_plan_file_id": f["id"]},
        )
        assert r.status_code == 200, r.text
        event = r.json()["event"]
        assert event["table_plan_file_id"] == f["id"]
        assert event["table_plan_file_url"] == f"/api/functions/files/{f['id']}"
        got = client.get(event["table_plan_file_url"], headers=H(w.staff))
        assert got.status_code == 200 and got.headers["content-type"] == "image/png"
        theirs = upload(client, w.other_user, png((3, 3, 3))).json()
        assert (
            call(
                client,
                "PATCH",
                f"/events/{event_id}",
                w.staff,
                json={"table_plan_file_id": theirs["id"]},
            ).status_code
            == 422
        )
        cleared = call(
            client,
            "PATCH",
            f"/events/{event_id}",
            w.staff,
            json={"table_plan_file_id": None},
        ).json()["event"]
        assert cleared["table_plan_file_id"] is None

    def test_portal_link_for_staff_to_pass_on(self, client, w):
        detail = direct_booking(client, w)
        event_id = detail["event"]["id"]
        r = call(
            client,
            "POST",
            f"/events/{event_id}/portal-link",
            w.staff,
            json={"emails": ["Robin@Example.com", "boss@example.com"]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "/e/" in body["url"] and body["link_sent_at"]
        assert body["emails"] == ["robin@example.com", "boss@example.com"]
        after = call(client, "GET", f"/events/{event_id}", w.staff).json()
        assert after["status"]["status"] == "sent_to_client"
        assert after["changes"][-1]["kind"] == "link_sent"
        # The same link again (it is stable); no emails given = the contact's.
        again = call(client, "POST", f"/events/{event_id}/portal-link", w.staff)
        assert again.json()["url"] == body["url"]
        assert again.json()["emails"] == ["robin@example.com"]
        bad = call(
            client,
            "POST",
            f"/events/{event_id}/portal-link",
            w.staff,
            json={"emails": ["not-an-email"]},
        )
        assert bad.status_code == 422
        call(
            client,
            "POST",
            f"/events/{event_id}/cancel",
            w.staff,
            json={"reason": "Off"},
        )
        assert (
            call(client, "POST", f"/events/{event_id}/portal-link", w.staff).status_code
            == 409
        )

    def test_link_urls_only_for_live_links(self, client, w, monkeypatch):
        # Quote link on the enquiry detail.
        enquiry_id = detail_of(new_enquiry(client, w))
        url = public_tokens.mint_quote_link(w.db, w.ctx(), enquiry_id)

        def quote_urls():
            body = call(client, "GET", f"/enquiries/{enquiry_id}", w.staff).json()
            return [link["url"] for link in body["quote_links"]]

        assert quote_urls() == [url]
        row = w.db.query(FnPublicToken).filter_by(enquiry_id=enquiry_id).one()
        live_until = row.expires_at
        row.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
        w.db.flush()
        assert quote_urls() == [None]  # expired
        row.expires_at = live_until
        w.db.flush()
        revoked = call(
            client, "POST", f"/enquiries/{enquiry_id}/quote-link/revoke", w.staff
        )
        assert revoked.status_code == 200, revoked.text
        assert [
            (x["url"], bool(x["revoked_at"])) for x in revoked.json()["quote_links"]
        ] == [(None, True)]
        # Portal link on the event detail, the same rule.
        event_id = direct_booking(client, w)["event"]["id"]
        portal = call(client, "POST", f"/events/{event_id}/portal-link", w.staff).json()
        detail = call(client, "GET", f"/events/{event_id}", w.staff).json()
        assert [link["url"] for link in detail["portal_links"]] == [portal["url"]]
        # A live link issued under an earlier key can't be shown again.
        monkeypatch.setattr(settings, "JWT_SECRET", "a-rotated-secret")
        detail = call(client, "GET", f"/events/{event_id}", w.staff).json()
        assert [link["url"] for link in detail["portal_links"]] == [None]
        monkeypatch.undo()
        gone = call(client, "POST", f"/events/{event_id}/portal-link/revoke", w.staff)
        assert gone.status_code == 200, gone.text
        assert [link["url"] for link in gone.json()["portal_links"]] == [None]
