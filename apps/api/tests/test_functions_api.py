"""The Norm Functions staff API — plumbing, meta, contacts, enquiries and
minimum spends, through HTTP.

What these tests hold the router to, beyond "the endpoint works":

* **The gate.** An organisation without Functions (mode off) gets 404 on
  every endpoint; shadow mode reads but every write is a 409 ``not_live``.
* **Tenancy.** A user of another organisation gets 404 — never 403, never the
  row — on every id of this one, and their lists never show it. A staff
  member without access to a venue can't see or act on its records.
* **The owner's approval rule, server-side.** Only the venue's approver
  approves, only the override approver overrides (with a reason); an owner
  has no special power. Orbit enforced this in the browser.
* **The customer's confirmation creates the event, exactly once** — also when
  staff record it from a phone call or email.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.auth.security import create_access_token
from app.db.functions_models import (
    FnContact,
    FnContactEmail,
    FnEnquiry,
    FnEvent,
    FnMailboxAccount,
    FnMailboxMessage,
    FnMailboxThread,
    FnMinSpendResponse,
    FnThreadLink,
)
from tests.functions_world import add_contact, add_enquiry, make_world

UTC = dt.timezone.utc
DATE = "2026-12-04"


def H(user) -> dict:
    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


def call(client, method: str, path: str, user, **kw):
    return client.request(method, f"/api/functions{path}", headers=H(user), **kw)


@pytest.fixture
def w(db_session):
    return make_world(db_session)


def new_enquiry(
    client, w, *, user=None, venue=None, booking_type="large_event_exclusive", **kw
):
    body = {
        "contact": {
            "name": "Casey Client",
            "email": kw.pop("email", "casey@example.com"),
        },
        "venue_id": (venue or w.venue).id,
        "booking_type": booking_type,
        "guests": 60,
        "subject": "Casey's 40th",
        **kw,
    }
    r = call(client, "POST", "/enquiries", user or w.staff, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def new_option(client, w, enquiry_id, *, area=None, amount=500_000, user=None, **kw):
    body = {
        "venue_id": w.venue.id,
        "area_id": (area or w.deck).id,
        "amount_cents": amount,
        "event_date": DATE,
        "start_time": "19:00",
        "end_time": "01:00",
        "ends_next_day": True,
        **kw,
    }
    r = call(
        client,
        "POST",
        f"/enquiries/{enquiry_id}/min-spends",
        user or w.staff,
        json=body,
    )
    assert r.status_code == 201, r.text
    return r.json()


def detail_of(created: dict) -> str:
    return created["enquiry"]["id"]


# ===========================================================================
# The gate: entitlement, shadow mode, error shape
# ===========================================================================


class TestGate:
    def test_off_org_gets_404_everywhere(self, client, w):
        w.org.functions_mode = "off"
        w.db.flush()
        for method, path in (
            ("GET", "/meta"),
            ("GET", "/dashboard"),
            ("GET", "/enquiries"),
            ("POST", "/contacts"),
            ("GET", "/events"),
        ):
            r = call(client, method, path, w.staff, json={"name": "x"})
            assert r.status_code == 404, (method, path, r.text)
            assert r.json()["detail"]["code"] == "not_found"

    def test_switching_off_bites_at_once_despite_the_entitlement_cache(self, client, w):
        # The catalog half of the gate is remembered for a short while; the
        # org's own mode is read fresh, so "off" is immediate.
        assert call(client, "GET", "/meta", w.staff).status_code == 200
        w.org.functions_mode = "off"
        w.db.flush()
        assert call(client, "GET", "/meta", w.staff).status_code == 404
        w.org.functions_mode = "shadow"
        w.db.flush()
        assert call(client, "GET", "/meta", w.staff).json()["mode"] == "shadow"

    def test_user_with_no_organisation_gets_404(self, client, w, db_session):
        from tests.conftest import _make_user

        loner = _make_user(db_session, full_name="No Org")
        assert call(client, "GET", "/meta", loner).status_code == 404

    def test_unauthenticated_is_refused(self, client, w):
        assert client.get("/api/functions/meta").status_code in (401, 403)

    def test_shadow_mode_reads_but_refuses_every_write(self, client, w):
        created = new_enquiry(client, w)
        w.org.functions_mode = "shadow"
        w.db.flush()
        enquiry_id = detail_of(created)
        assert (
            call(client, "GET", f"/enquiries/{enquiry_id}", w.staff).status_code == 200
        )
        assert call(client, "GET", "/meta", w.staff).json()["can_write"] is False
        for method, path, body in (
            ("POST", "/enquiries", {"contact": {"name": "A", "email": "a@x.com"}}),
            ("PATCH", f"/enquiries/{enquiry_id}", {"guests": 10}),
            ("POST", f"/enquiries/{enquiry_id}/close", {"reason": "No"}),
            ("POST", "/contacts", {"name": "B"}),
            (
                "POST",
                f"/enquiries/{enquiry_id}/min-spends",
                {"venue_id": w.venue.id, "amount_cents": 1},
            ),
        ):
            r = call(client, method, path, w.staff, json=body)
            assert r.status_code == 409, (path, r.text)
            assert r.json()["detail"]["code"] == "not_live"
            assert "shadow" in r.json()["detail"]["message"]

    def test_unknown_body_fields_are_refused(self, client, w):
        created = new_enquiry(client, w)
        r = call(
            client,
            "PATCH",
            f"/enquiries/{detail_of(created)}",
            w.staff,
            json={"guestz": 10},
        )
        assert r.status_code == 422


# ===========================================================================
# Meta
# ===========================================================================


class TestMeta:
    def test_staff_sees_their_venue_and_company_settings(self, client, w):
        r = call(client, "GET", "/meta", w.staff)
        assert r.status_code == 200, r.text
        m = r.json()
        assert m["company"]["currency"] == "NZD"
        assert m["company"]["functions_mode"] == "live"
        assert m["can_write"] is True
        assert [v["name"] for v in m["venues"]] == ["Brew Bar"]
        venue = m["venues"][0]
        assert venue["timezone_valid"] is True
        assert venue["is_approver"] is False
        assert venue["settings"]["approver_name"] == "Andy Approver"
        assert set(venue["enabled_fields"]) == {
            "seated_dining",
            "group_booking",
            "large_event_partial",
            "large_event_exclusive",
        }
        assert venue["enabled_fields"]["seated_dining"]["beverages.bar_tab"] is True
        assert {b["value"] for b in m["booking_types"] if b["min_spend"]} == {
            "large_event_partial",
            "large_event_exclusive",
        }
        assert m["user"]["id"] == w.staff.id and m["user"]["is_owner"] is False
        names = {p["name"] for p in m["members"]}
        assert {"Sam Staff", "Olive Owner", "Andy Approver"} <= names
        assert "Rita Rival" not in names

    def test_owner_sees_every_venue_approver_flag(self, client, w):
        owner = call(client, "GET", "/meta", w.owner).json()
        assert {v["name"] for v in owner["venues"]} == {"Brew Bar", "Garden Bar"}
        approver = call(client, "GET", "/meta", w.approver).json()
        assert approver["venues"][0]["is_approver"] is True

    def test_enabled_fields_follow_the_venue_settings(self, client, w):
        from app.db.functions_models import FnVenueFunctionSettings

        s = w.db.query(FnVenueFunctionSettings).filter_by(venue_id=w.venue.id).one()
        s.enabled_fields = {"seated_dining": {"details.min_spend": False}}
        w.db.flush()
        venue = call(client, "GET", "/meta", w.staff).json()["venues"][0]
        assert venue["enabled_fields"]["seated_dining"]["details.min_spend"] is False
        assert (
            venue["enabled_fields"]["large_event_exclusive"]["details.min_spend"]
            is True
        )


# ===========================================================================
# Contacts
# ===========================================================================


class TestContacts:
    def test_create_list_get_update_with_versions(self, client, w):
        r = call(
            client,
            "POST",
            "/contacts",
            w.staff,
            json={"name": "Dana Doe", "email": "Dana@Example.com", "phone": "021"},
        )
        assert r.status_code == 201, r.text
        c = r.json()
        assert c["email"] == "dana@example.com" and c["version"] == 1

        listed = call(client, "GET", "/contacts?q=dana", w.staff).json()
        assert [row["id"] for row in listed["rows"]] == [c["id"]]
        assert listed["rows"][0]["counts"]["enquiries"] == 0

        r = call(
            client,
            "PATCH",
            f"/contacts/{c['id']}",
            w.staff,
            json={"version": 1, "company": "Doe Ltd"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["company"] == "Doe Ltd" and r.json()["version"] == 2
        assert r.json()["changes"] == {"company": [None, "Doe Ltd"]}

        stale = call(
            client,
            "PATCH",
            f"/contacts/{c['id']}",
            w.staff,
            json={"version": 1, "company": "Other"},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "conflict"

    def test_duplicate_email_is_a_conflict(self, client, w):
        call(
            client, "POST", "/contacts", w.staff, json={"name": "A", "email": "a@x.com"}
        )
        r = call(
            client, "POST", "/contacts", w.staff, json={"name": "B", "email": "A@x.com"}
        )
        assert r.status_code == 409

    def test_emails_archive_and_delete(self, client, w):
        c = call(
            client,
            "POST",
            "/contacts",
            w.staff,
            json={"name": "E", "email": "e1@x.com"},
        ).json()
        r = call(
            client,
            "POST",
            f"/contacts/{c['id']}/emails",
            w.staff,
            json={"email": "e2@x.com"},
        )
        assert {e["email"] for e in r.json()["emails"]} == {"e1@x.com", "e2@x.com"}
        r = call(
            client,
            "PUT",
            f"/contacts/{c['id']}/emails/primary",
            w.staff,
            json={"email": "e2@x.com"},
        )
        assert r.json()["email"] == "e2@x.com"
        r = call(
            client, "DELETE", f"/contacts/{c['id']}/emails?email=e1@x.com", w.staff
        )
        assert [e["email"] for e in r.json()["emails"]] == ["e2@x.com"]
        assert call(client, "POST", f"/contacts/{c['id']}/archive", w.staff).json()[
            "archived_at"
        ]
        assert call(client, "GET", "/contacts", w.staff).json()["rows"] == []
        assert (
            call(client, "POST", f"/contacts/{c['id']}/unarchive", w.staff).json()[
                "archived_at"
            ]
            is None
        )
        assert (
            call(client, "DELETE", f"/contacts/{c['id']}", w.staff).status_code == 204
        )
        assert call(client, "GET", f"/contacts/{c['id']}", w.staff).status_code == 404

    def test_contact_with_enquiries_cant_be_deleted_and_shows_counts(self, client, w):
        created = new_enquiry(client, w)
        contact_id = created["contact"]["id"]
        assert (
            call(client, "DELETE", f"/contacts/{contact_id}", w.staff).status_code
            == 409
        )
        got = call(client, "GET", f"/contacts/{contact_id}", w.staff).json()
        assert [e["id"] for e in got["enquiries"]] == [detail_of(created)]
        assert got["counts"] == {
            "enquiries": 1,
            "open": 1,
            "confirmed": 0,
            "lost": 0,
            "events": 0,
            "conversion_pct": None,
        }

    def test_anonymise_is_owner_only_and_scrubs_personal_data(self, client, w):
        created = new_enquiry(client, w, client_note="My mum's 60th, she is coeliac")
        contact_id = created["contact"]["id"]
        enquiry_id = detail_of(created)
        account = FnMailboxAccount(
            organization_id=w.org.id,
            venue_id=w.venue.id,
            google_email="events@brew.bar",
        )
        w.db.add(account)
        w.db.flush()
        thread = FnMailboxThread(
            organization_id=w.org.id, account_id=account.id, gmail_thread_id="t1"
        )
        other_thread = FnMailboxThread(
            organization_id=w.org.id, account_id=account.id, gmail_thread_id="t2"
        )
        w.db.add_all([thread, other_thread])
        w.db.flush()
        now = dt.datetime.now(UTC)
        w.db.add_all(
            [
                FnMailboxMessage(
                    organization_id=w.org.id,
                    account_id=account.id,
                    thread_id=thread.id,
                    gmail_message_id="m1",
                    gmail_thread_id="t1",
                    internal_date=now,
                    from_email="casey@example.com",
                    is_inbound=True,
                ),
                FnMailboxMessage(
                    organization_id=w.org.id,
                    account_id=account.id,
                    thread_id=thread.id,
                    gmail_message_id="m2",
                    gmail_thread_id="t1",
                    internal_date=now,
                    from_email="events@brew.bar",
                    to_emails=["Casey <casey@example.com>"],
                    is_inbound=False,
                ),
                FnMailboxMessage(
                    organization_id=w.org.id,
                    account_id=account.id,
                    thread_id=other_thread.id,
                    gmail_message_id="m3",
                    gmail_thread_id="t2",
                    internal_date=now,
                    from_email="joann@example.com",
                    to_emails=["events@brew.bar"],
                    is_inbound=True,
                ),
            ]
        )
        w.db.flush()

        refused = call(
            client,
            "POST",
            f"/contacts/{contact_id}/anonymise",
            w.staff,
            json={"confirm": True},
        )
        assert refused.status_code == 403
        unconfirmed = call(
            client,
            "POST",
            f"/contacts/{contact_id}/anonymise",
            w.owner,
            json={"confirm": False},
        )
        assert unconfirmed.status_code == 422

        r = call(
            client,
            "POST",
            f"/contacts/{contact_id}/anonymise",
            w.owner,
            json={"confirm": True},
        )
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["emails_removed"] == 1
        assert result["mail_threads_purged"] == 1
        assert result["mail_messages_purged"] == 2

        contact = w.db.query(FnContact).filter_by(id=contact_id).one()
        w.db.refresh(contact)
        assert contact.name == "Anonymised contact" and contact.anonymised_at
        assert w.db.query(FnContactEmail).filter_by(contact_id=contact_id).count() == 0
        enquiry = w.db.query(FnEnquiry).filter_by(id=enquiry_id).one()
        w.db.refresh(enquiry)
        assert enquiry.client_note is None and enquiry.subject is None
        assert enquiry.guests == 60  # the facts stay
        remaining = {
            m.gmail_message_id
            for m in w.db.query(FnMailboxMessage).filter_by(account_id=account.id)
        }
        assert remaining == {"m3"}  # joann@ is not casey@


# ===========================================================================
# Enquiries
# ===========================================================================


class TestEnquiries:
    def test_create_with_new_contact_and_read_the_detail(self, client, w):
        created = new_enquiry(client, w)
        assert created["stage"] == "new"
        assert created["contact"]["email"] == "casey@example.com"
        assert created["enquiry"]["version"] == 1
        assert created["venue"]["name"] == "Brew Bar"
        for key in (
            "options",
            "menus",
            "events",
            "closures",
            "proposal",
            "threads",
            "timeline",
            "contact_history",
        ):
            assert key in created
        assert created["timeline"][0]["kind"] == "enquiry_created"

        again = new_enquiry(client, w, subject="Second do")
        assert again["contact"]["id"] == created["contact"]["id"]  # matched by email
        assert [e["id"] for e in again["contact_history"]["enquiries"]] == [
            detail_of(created)
        ]

    def test_create_for_an_existing_contact(self, client, w):
        contact = add_contact(w.db, w.org.id, "Pat", "pat@x.com")
        r = call(
            client,
            "POST",
            "/enquiries",
            w.staff,
            json={
                "contact_id": contact.id,
                "venue_id": w.venue.id,
                "booking_type": "seated_dining",
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["contact"]["id"] == contact.id

    def test_list_filters_paging_and_kpis(self, client, w):
        a = new_enquiry(client, w, subject="Alpha")
        new_enquiry(
            client, w, subject="Beta", booking_type="seated_dining", email="b@x.com"
        )
        new_enquiry(client, w, subject="Gamma", email="g@x.com")
        listed = call(client, "GET", "/enquiries?limit=2", w.staff).json()
        assert len(listed["rows"]) == 2 and listed["has_more"] is True
        assert listed["kpis"]["open"] == 3
        page2 = call(client, "GET", "/enquiries?limit=2&offset=2", w.staff).json()
        assert len(page2["rows"]) == 1 and page2["has_more"] is False
        seated = call(
            client, "GET", "/enquiries?booking_type=seated_dining", w.staff
        ).json()
        assert [r["subject"] for r in seated["rows"]] == ["Beta"]
        assert [
            r["subject"]
            for r in call(client, "GET", "/enquiries?q=alph", w.staff).json()["rows"]
        ] == ["Alpha"]
        row = next(r for r in listed["rows"] + page2["rows"] if r["id"] == detail_of(a))
        assert row["stage"] == "new" and row["next_action"]["kind"] == "none"
        assert (
            call(client, "GET", "/enquiries?stage=new,lost", w.staff).status_code == 200
        )
        assert call(client, "GET", "/enquiries?sort=bogus", w.staff).status_code == 422

    def test_list_kpis_count_what_the_rows_show_under_a_venue_filter(self, client, w):
        new_enquiry(client, w, subject="At the bar")
        group_wide = call(
            client,
            "POST",
            "/enquiries",
            w.staff,
            json={"contact": {"name": "G", "email": "g@x.com"}, "subject": "Anywhere"},
        )
        assert group_wide.status_code == 201
        listed = call(client, "GET", f"/enquiries?venue={w.venue.id}", w.staff).json()
        # The list keeps group-wide enquiries under a venue filter; so do the
        # KPIs, or the numbers above the table wouldn't match its rows.
        assert {r["subject"] for r in listed["rows"]} == {"At the bar", "Anywhere"}
        assert listed["kpis"]["open"] == 2

    def test_update_close_reopen_follow_up_archive(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        r = call(
            client,
            "PATCH",
            f"/enquiries/{enquiry_id}",
            w.staff,
            json={"version": 1, "guests": 80, "preferred_date_start": DATE},
        )
        assert r.status_code == 200, r.text
        assert r.json()["enquiry"]["guests"] == 80
        assert r.json()["enquiry"]["preferred_date_start"] == DATE
        assert r.json()["changes"]["guests"] == [60, 80]
        version = r.json()["enquiry"]["version"]
        assert (
            call(
                client,
                "PATCH",
                f"/enquiries/{enquiry_id}",
                w.staff,
                json={"version": version - 1, "guests": 1},
            ).status_code
            == 409
        )

        assert (
            call(
                client, "POST", f"/enquiries/{enquiry_id}/close", w.staff, json={}
            ).status_code
            == 422
        )
        assert (
            call(
                client,
                "POST",
                f"/enquiries/{enquiry_id}/close",
                w.staff,
                json={"reason": "  "},
            ).status_code
            == 422
        )
        closed = call(
            client,
            "POST",
            f"/enquiries/{enquiry_id}/close",
            w.staff,
            json={"reason": "Went elsewhere"},
        )
        assert closed.status_code == 200 and closed.json()["stage"] == "lost"
        assert (
            call(
                client,
                "POST",
                f"/enquiries/{enquiry_id}/close",
                w.staff,
                json={"reason": "x"},
            ).status_code
            == 409
        )
        reopened = call(
            client,
            "POST",
            f"/enquiries/{enquiry_id}/reopen",
            w.staff,
            json={"reason": "They came back"},
        )
        assert reopened.json()["stage"] == "new"
        assert [c["kind"] for c in reopened.json()["closures"]] == ["lost", "reopened"]

        fu = call(
            client,
            "PUT",
            f"/enquiries/{enquiry_id}/follow-up",
            w.staff,
            json={"date": "2026-01-01", "reason": "Call back"},
        )
        assert fu.json()["next_action"]["kind"] == "requested"
        assert fu.json()["next_action"]["overdue"] is True
        cleared = call(client, "DELETE", f"/enquiries/{enquiry_id}/follow-up", w.staff)
        assert cleared.json()["enquiry"]["follow_up_date"] is None

        assert call(client, "POST", f"/enquiries/{enquiry_id}/archive", w.staff).json()[
            "enquiry"
        ]["archived_at"]
        assert call(client, "GET", "/enquiries", w.staff).json()["rows"] == []
        assert (
            len(
                call(client, "GET", "/enquiries?include_archived=true", w.staff).json()[
                    "rows"
                ]
            )
            == 1
        )
        call(client, "POST", f"/enquiries/{enquiry_id}/unarchive", w.staff)

    def test_thread_links_feed_the_detail(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        account = FnMailboxAccount(
            organization_id=w.org.id,
            venue_id=w.venue.id,
            google_email="events@brew.bar",
        )
        w.db.add(account)
        w.db.flush()
        thread = FnMailboxThread(
            organization_id=w.org.id,
            account_id=account.id,
            gmail_thread_id="g-1",
            subject="Booking",
            message_count=1,
        )
        w.db.add(thread)
        w.db.flush()
        w.db.add(
            FnMailboxMessage(
                organization_id=w.org.id,
                account_id=account.id,
                thread_id=thread.id,
                gmail_message_id="msg-1",
                gmail_thread_id="g-1",
                internal_date=dt.datetime.now(UTC) - dt.timedelta(hours=30),
                from_email="casey@example.com",
                subject="Booking",
                is_inbound=True,
            )
        )
        w.db.flush()
        r = call(
            client,
            "POST",
            f"/enquiries/{enquiry_id}/threads",
            w.staff,
            json={"account_id": account.id, "gmail_thread_id": "g-1"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert (
            body["threads"][0]["subject"] == "Booking"
            and body["threads"][0]["mirrored"]
        )
        assert body["next_action"]["kind"] == "reply_overdue"
        assert any(i["kind"] == "email_in" for i in body["timeline"])
        unlinked = call(client, "DELETE", f"/thread-links/{body['link_id']}", w.staff)
        assert unlinked.status_code == 200 and unlinked.json()["unlinked_at"]
        assert (
            call(client, "GET", f"/enquiries/{enquiry_id}", w.staff).json()["threads"]
            == []
        )

    def test_confirm_booking_for_a_no_min_spend_enquiry_is_idempotent(self, client, w):
        created = new_enquiry(
            client, w, booking_type="seated_dining", preferred_date_start=DATE
        )
        enquiry_id = detail_of(created)
        r = call(
            client,
            "POST",
            f"/enquiries/{enquiry_id}/confirm-booking",
            w.staff,
            json={"channel": "phone"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["created"] is True
        event_id = r.json()["event"]["event"]["id"]
        again = call(
            client,
            "POST",
            f"/enquiries/{enquiry_id}/confirm-booking",
            w.staff,
            json={"channel": "email"},
        )
        assert (
            again.json()["created"] is False
            and again.json()["event"]["event"]["id"] == event_id
        )
        assert w.db.query(FnEvent).filter_by(enquiry_id=enquiry_id).count() == 1
        large = detail_of(
            new_enquiry(client, w, email="l@x.com", preferred_date_start=DATE)
        )
        assert (
            call(
                client,
                "POST",
                f"/enquiries/{large}/confirm-booking",
                w.staff,
                json={"channel": "phone"},
            ).status_code
            == 422
        )


# ===========================================================================
# Minimum spends: approval rules and the customer's confirmation
# ===========================================================================


class TestListAdditions:
    """The enquiries list's extras: row facts beside each row, row sorts that
    page cleanly both ways, and KPI tiles that count what the rows include —
    also under ``venue=unassigned``."""

    def _mailbox(self, w, venue, email):
        account = FnMailboxAccount(
            organization_id=w.org.id, venue_id=venue.id, google_email=email
        )
        w.db.add(account)
        w.db.flush()
        return account

    def _thread(self, w, account, gid, enquiry_id, *, name, email, hours_ago):
        w.db.add(
            FnMailboxThread(
                organization_id=w.org.id,
                account_id=account.id,
                gmail_thread_id=gid,
                last_from_name=name,
                last_from_email=email,
                last_message_at=dt.datetime.now(UTC) - dt.timedelta(hours=hours_ago),
                message_count=1,
            )
        )
        w.db.add(
            FnThreadLink(
                organization_id=w.org.id,
                account_id=account.id,
                gmail_thread_id=gid,
                enquiry_id=enquiry_id,
            )
        )
        w.db.flush()

    def _row(self, client, w, user, enquiry_id):
        rows = call(client, "GET", "/enquiries", user).json()["rows"]
        return next(r for r in rows if r["id"] == enquiry_id)

    def test_rows_carry_email_options_and_last_sender(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        # An older, non-primary address must not win.
        w.db.add(
            FnContactEmail(
                organization_id=w.org.id,
                contact_id=w.db.get(FnEnquiry, enquiry_id).contact_id,
                email="older@example.com",
                is_primary=False,
                created_at=dt.datetime.now(UTC) - dt.timedelta(days=9),
            )
        )
        deck = new_option(client, w, enquiry_id, amount=500_000)  # needs approval
        new_option(client, w, enquiry_id, area=w.snug, amount=300_000)
        gone = new_option(client, w, enquiry_id, area=w.snug, amount=900_000)
        call(
            client,
            "POST",
            f"/min-spends/{gone['id']}/withdraw",
            w.staff,
            json={"reason": "No"},
        )
        here = self._mailbox(w, w.venue, "events@brew.bar")
        self._thread(
            w, here, "g-old", enquiry_id, name="Old", email="o@x.com", hours_ago=50
        )
        self._thread(
            w,
            here,
            "g-new",
            enquiry_id,
            name="Casey Client",
            email="casey@example.com",
            hours_ago=2,
        )
        # Newer still, but in a mailbox the staff member can't see.
        garden = self._mailbox(w, w.venue2, "hello@garden.bar")
        self._thread(
            w, garden, "g-hid", enquiry_id, name="Garden", email="g@x.com", hours_ago=1
        )
        bare = detail_of(new_enquiry(client, w, email="bare@example.com"))

        row = self._row(client, w, w.staff, enquiry_id)
        assert row["contact_email"] == "casey@example.com"
        assert row["min_spend_summary"] == {
            "count": 2,  # the withdrawn one is off the table
            "largest_amount_cents": 500_000,
            "needs_approval": True,
        }
        assert row["value_cents"] == 500_000
        last = row["last_email_from"]
        assert (last["name"], last["email"]) == ("Casey Client", "casey@example.com")
        assert last["at"]
        empty = self._row(client, w, w.staff, bare)
        assert empty["min_spend_summary"] == {
            "count": 0,
            "largest_amount_cents": None,
            "needs_approval": False,
        }
        assert empty["last_email_from"] is None
        assert empty["contact_email"] == "bare@example.com"
        # The owner sees every mailbox, so the newest sender is theirs.
        owner_row = self._row(client, w, w.owner, enquiry_id)
        assert owner_row["last_email_from"]["name"] == "Garden"
        # Approving the deck option clears the flag.
        approved = call(client, "POST", f"/min-spends/{deck['id']}/approve", w.approver)
        assert approved.status_code == 200
        row = self._row(client, w, w.staff, enquiry_id)
        assert row["min_spend_summary"]["needs_approval"] is False

    def test_row_sorts_page_cleanly_both_ways(self, client, w):
        now = dt.datetime.now(UTC)
        specs = [  # (contact, subject, guests) — created 1, 2, 3, 4, 5 min ago
            ("bea", "Wedding", 40),
            ("Al", None, 120),
            ("Cy", "anniversary", None),
            ("al", "Board dinner", 40),
            ("Dee", "Zoo night", 10),
        ]
        ids = []
        for n, (name, subject, guests) in enumerate(specs):
            contact = add_contact(w.db, w.org.id, name, f"s{n}@x.com")
            ids.append(
                add_enquiry(
                    w.db,
                    w.org.id,
                    contact.id,
                    w.venue.id,
                    subject=subject,
                    guests=guests,
                    created_at=now - dt.timedelta(minutes=n + 1),
                ).id
            )
        assert (
            call(
                client,
                "POST",
                f"/enquiries/{ids[4]}/close",
                w.staff,
                json={"reason": "x"},
            ).status_code
            == 200
        )
        new_option(client, w, ids[0], area=w.snug, amount=200_000)
        new_option(client, w, ids[2], area=w.snug, amount=700_000)
        a, b, c, d, e = ids
        # Blanks last either way; equal keys stay newest first.
        expected = {
            "contact_asc": [b, d, a, c, e],
            "contact_desc": [e, c, a, b, d],
            "subject_asc": [c, d, a, e, b],
            "subject_desc": [e, a, d, c, b],
            "guests_asc": [e, a, d, b, c],
            "guests_desc": [b, a, d, e, c],
            "value_asc": [b, d, e, a, c],
            "value_desc": [c, a, b, d, e],
        }
        for sort, order in expected.items():
            pages, more = [], []
            for offset in (0, 2, 4):
                r = call(
                    client,
                    "GET",
                    f"/enquiries?sort={sort}&limit=2&offset={offset}",
                    w.staff,
                )
                assert r.status_code == 200, r.text
                pages += [row["id"] for row in r.json()["rows"]]
                more.append(r.json()["has_more"])
            assert pages == order, sort
            assert more == [True, True, False], sort
        status_order = ("new", "replied", "quoted", "confirmed", "lost")
        for sort in ("stage_asc", "stage_desc"):
            rows = call(client, "GET", f"/enquiries?sort={sort}", w.staff).json()[
                "rows"
            ]
            got = [status_order.index(row["stage"]) for row in rows]
            assert got == sorted(got, reverse=sort.endswith("desc")), sort
        first = call(client, "GET", "/enquiries?sort=stage_desc&limit=1", w.staff)
        assert first.json()["rows"][0]["id"] == e  # the lost one
        # Filters still apply.
        lost = call(client, "GET", "/enquiries?sort=contact_asc&stage=lost", w.staff)
        assert [row["id"] for row in lost.json()["rows"]] == [e]
        for bad in ("contact_up", "bogus_desc", "value"):
            assert (
                call(client, "GET", f"/enquiries?sort={bad}", w.staff).status_code
                == 422
            ), bad

    def test_unassigned_tiles_count_the_group_wide_enquiries(self, client, w):
        at_bar = detail_of(new_enquiry(client, w, subject="At the bar"))
        anywhere = call(
            client,
            "POST",
            "/enquiries",
            w.staff,
            json={"contact": {"name": "G", "email": "g@x.com"}, "subject": "Anywhere"},
        ).json()["enquiry"]["id"]
        past = (dt.date.today() - dt.timedelta(days=2)).isoformat()
        for enquiry_id in (at_bar, anywhere):
            assert (
                call(
                    client,
                    "PUT",
                    f"/enquiries/{enquiry_id}/follow-up",
                    w.staff,
                    json={"date": past},
                ).status_code
                == 200
            )
        new_option(client, w, anywhere, area=w.snug, amount=250_000)
        new_option(client, w, at_bar, area=w.snug, amount=100_000)

        def listed(query, user=w.staff):
            body = call(client, "GET", f"/enquiries?{query}", user).json()
            return {r["subject"] for r in body["rows"]}, body["kpis"]

        subjects, kpis = listed("venue=unassigned")
        assert subjects == {"Anywhere"}
        assert kpis == {
            "open": 1,
            "needs_reply": 0,
            "follow_ups_due": 1,
            "open_value_cents": 250_000,
        }
        # One venue: its own enquiries plus the group-wide ones, as the rows.
        subjects, kpis = listed(f"venue={w.venue.id}")
        assert subjects == {"At the bar", "Anywhere"}
        assert kpis == {
            "open": 2,
            "needs_reply": 0,
            "follow_ups_due": 2,
            "open_value_cents": 350_000,
        }
        subjects, kpis = listed(f"venue={w.venue2.id}", w.owner)
        assert subjects == {"Anywhere"} and kpis["open"] == 1
        assert kpis["open_value_cents"] == 250_000
        # The dashboard's group-wide view agrees.
        dash = call(client, "GET", "/dashboard?venue=unassigned", w.staff).json()
        assert dash["open_enquiries"] == listed("venue=unassigned")[1]


class TestMinSpends:
    def test_add_edit_withdraw(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        option = new_option(client, w, enquiry_id, label="Deck hire")
        assert option["state"] == "proposed" and option["confirmable"] is False
        assert option["rights"]["approve"] is False
        assert option["area_name"] == "Deck" and option["total_cents"] == 500_000
        r = call(
            client,
            "PATCH",
            f"/min-spends/{option['id']}",
            w.staff,
            json={"version": option["version"], "amount_cents": 450_000},
        )
        assert r.status_code == 200 and r.json()["amount_cents"] == 450_000
        stale = call(
            client,
            "PATCH",
            f"/min-spends/{option['id']}",
            w.staff,
            json={"version": option["version"], "label": "x"},
        )
        assert stale.status_code == 409
        bad = call(
            client,
            "POST",
            f"/enquiries/{enquiry_id}/min-spends",
            w.staff,
            json={"venue_id": w.venue.id, "amount_cents": -1},
        )
        assert bad.status_code == 422
        w_ = call(
            client,
            "POST",
            f"/min-spends/{option['id']}/withdraw",
            w.staff,
            json={"reason": "Too dear"},
        )
        assert w_.json()["state"] == "withdrawn"

    def test_only_the_venue_approver_approves(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        option = new_option(client, w, enquiry_id)
        staff = call(client, "POST", f"/min-spends/{option['id']}/approve", w.staff)
        assert staff.status_code == 403
        assert "approver" in staff.json()["detail"]["message"]
        assert (
            call(
                client, "POST", f"/min-spends/{option['id']}/approve", w.owner
            ).status_code
            == 403
        )
        ok = call(client, "POST", f"/min-spends/{option['id']}/approve", w.approver)
        assert ok.status_code == 200 and ok.json()["state"] == "approved"
        assert ok.json()["approvals"][0]["actor"] == "Andy Approver"
        assert ok.json()["rights"]["revoke"] is True

    def test_override_needs_the_override_approver_and_a_reason(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        option = new_option(client, w, enquiry_id)
        assert (
            call(
                client,
                "POST",
                f"/min-spends/{option['id']}/override",
                w.approver,
                json={"reason": "VIP"},
            ).status_code
            == 403
        )
        assert (
            call(
                client,
                "POST",
                f"/min-spends/{option['id']}/override",
                w.overrider,
                json={"reason": " "},
            ).status_code
            == 422
        )
        assert (
            call(
                client,
                "POST",
                f"/min-spends/{option['id']}/override",
                w.overrider,
                json={},
            ).status_code
            == 422
        )
        r = call(
            client,
            "POST",
            f"/min-spends/{option['id']}/override",
            w.overrider,
            json={"reason": "VIP"},
        )
        assert r.status_code == 200 and r.json()["approvals"][-1]["kind"] == "override"

    def test_revoke_rules(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        option = new_option(client, w, enquiry_id)
        call(client, "POST", f"/min-spends/{option['id']}/approve", w.approver)
        assert (
            call(
                client,
                "POST",
                f"/min-spends/{option['id']}/revoke",
                w.staff,
                json={"reason": "x"},
            ).status_code
            == 403
        )
        r = call(
            client,
            "POST",
            f"/min-spends/{option['id']}/revoke",
            w.approver,
            json={"reason": "Wrong date"},
        )
        assert r.status_code == 200 and r.json()["state"] == "proposed"
        assert (
            call(
                client, "POST", f"/min-spends/{option['id']}/revoke", w.approver
            ).status_code
            == 409
        )

    def test_no_approval_area_lets_staff_approve(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        option = new_option(client, w, enquiry_id, area=w.snug)
        assert option["confirmable"] is True  # an area needing no approval
        assert (
            call(client, "POST", f"/min-spends/{option['id']}/approve", w.staff).json()[
                "state"
            ]
            == "approved"
        )

    def test_staff_recorded_confirmation_creates_exactly_one_event(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        option = new_option(client, w, enquiry_id)
        sibling = new_option(client, w, enquiry_id, area=w.snug, amount=300_000)

        early = call(
            client,
            "POST",
            f"/min-spends/{option['id']}/confirm",
            w.staff,
            json={"channel": "phone"},
        )
        assert early.status_code == 403  # not approved yet
        call(client, "POST", f"/min-spends/{option['id']}/approve", w.approver)
        assert (
            call(
                client,
                "POST",
                f"/min-spends/{option['id']}/confirm",
                w.staff,
                json={"channel": "quote_page"},
            ).status_code
            == 422
        )

        r = call(
            client,
            "POST",
            f"/min-spends/{option['id']}/confirm",
            w.staff,
            json={"channel": "phone", "note": "Called to say yes"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created"] is True
        event = body["event"]
        assert event["event"]["event_date"] == DATE
        assert event["event"]["area_id"] == w.deck.id
        assert event["event"]["created_via"] == "min_spend"
        assert event["status"]["status"] == "booked"
        assert body["option"]["state"] == "confirmed"

        again = call(
            client,
            "POST",
            f"/min-spends/{option['id']}/confirm",
            w.staff,
            json={"channel": "email"},
        )
        assert again.json()["created"] is False
        assert again.json()["event"]["event"]["id"] == event["event"]["id"]
        assert call(
            client,
            "POST",
            f"/min-spends/{sibling['id']}/confirm",
            w.staff,
            json={"channel": "phone"},
        ).status_code in (409,)
        assert w.db.query(FnEvent).filter_by(enquiry_id=enquiry_id).count() == 1
        response = (
            w.db.query(FnMinSpendResponse)
            .filter_by(min_spend_id=option["id"], kind="confirm")
            .first()
        )
        assert (
            response.channel == "phone" and response.recorded_by_user_id == w.staff.id
        )

        detail = call(client, "GET", f"/enquiries/{enquiry_id}", w.staff).json()
        assert detail["stage"] == "confirmed"
        states = {o["id"]: o["state"] for o in detail["options"]}
        assert states == {option["id"]: "confirmed", sibling["id"]: "withdrawn"}

    def test_customer_decline_recorded_by_staff(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        option = new_option(client, w, enquiry_id)
        r = call(
            client,
            "POST",
            f"/min-spends/{option['id']}/decline",
            w.staff,
            json={"channel": "email", "note": "Too dear"},
        )
        assert r.status_code == 200 and r.json()["state"] == "declined"
        assert r.json()["responses"][0]["recorded_by"] == "Sam Staff"

    def test_sales_reference_degrades_gracefully(self, client, w, monkeypatch):
        from app.services.functions import reports

        enquiry_id = detail_of(new_enquiry(client, w))
        option = new_option(client, w, enquiry_id)
        monkeypatch.setattr(
            reports,
            "loaded_call",
            lambda *a, **k: (None, "No LoadedHub for this venue."),
        )
        r = call(client, "GET", f"/min-spends/{option['id']}/reference", w.staff)
        assert r.status_code == 200
        assert r.json()["available"] is False and "LoadedHub" in r.json()["reason"]

        seen = {}

        def fake(db, config_db, venue, action, params):
            seen.update(action=action, params=params, venue=venue.name)
            return {
                "rows": [
                    {
                        "venue": venue.name,
                        "actual": 0,
                        "budget": 8250.5,
                        "last_year": 7400,
                    }
                ],
                "totals": {"actual": 0, "budget": 8250.5, "last_year": 7400},
            }, None

        monkeypatch.setattr(reports, "loaded_call", fake)
        ref = call(
            client, "GET", f"/min-spends/{option['id']}/reference", w.staff
        ).json()
        assert ref["available"] is True
        assert ref["budget_cents"] == 825050 and ref["last_year_cents"] == 740000
        assert ref["last_year_date"] == "2025-12-05"  # 364 days back: Friday to Friday
        assert seen["action"] == "get_sales" and seen["params"]["compare"] == [
            "budget",
            "last_year",
        ]
        assert seen["params"]["start"].startswith(
            "2026-12-04T07:00:00+13:00"
        )  # the venue's trading day
        assert (
            call(
                client,
                "GET",
                f"/venues/{w.venue.id}/sales-reference?date=2026-12-05",
                w.staff,
            ).json()["date"]
            == "2026-12-05"
        )
        assert (
            call(
                client,
                "GET",
                f"/venues/{w.venue2.id}/sales-reference?date=2026-12-05",
                w.staff,
            ).status_code
            == 404
        )

        # One failed feed (a future day's sales) keeps the venue out of
        # get_sales' totals; the reference still reads its own row.
        monkeypatch.setattr(
            reports,
            "loaded_call",
            lambda *a, **k: (
                {
                    "rows": [
                        {"errors": ["actual: timeout"], "budget": 100, "last_year": 90}
                    ],
                    "totals": {"actual": None, "budget": None, "last_year": None},
                },
                None,
            ),
        )
        partial = call(
            client, "GET", f"/min-spends/{option['id']}/reference", w.staff
        ).json()
        assert partial["available"] is True
        assert (partial["budget_cents"], partial["last_year_cents"]) == (10_000, 9_000)
        assert partial["reason"] == "actual: timeout"


# ===========================================================================
# Tenancy and venue scoping
# ===========================================================================


class TestIsolation:
    def test_another_organisation_gets_404_on_every_id(self, client, w):
        created = new_enquiry(client, w)
        enquiry_id = detail_of(created)
        contact_id = created["contact"]["id"]
        option = new_option(client, w, enquiry_id)
        rival = w.other_user
        for method, path, body in (
            ("GET", f"/enquiries/{enquiry_id}", None),
            ("GET", f"/enquiries/{enquiry_id}/timeline", None),
            ("PATCH", f"/enquiries/{enquiry_id}", {"guests": 1}),
            ("POST", f"/enquiries/{enquiry_id}/close", {"reason": "x"}),
            ("POST", f"/enquiries/{enquiry_id}/archive", None),
            ("PUT", f"/enquiries/{enquiry_id}/follow-up", {"date": DATE}),
            (
                "POST",
                f"/enquiries/{enquiry_id}/min-spends",
                {"venue_id": w.venue.id, "amount_cents": 1},
            ),
            ("POST", f"/enquiries/{enquiry_id}/confirm-booking", {"channel": "phone"}),
            ("GET", f"/contacts/{contact_id}", None),
            ("PATCH", f"/contacts/{contact_id}", {"name": "x"}),
            ("POST", f"/contacts/{contact_id}/anonymise", {"confirm": True}),
            ("DELETE", f"/contacts/{contact_id}", None),
            ("GET", f"/min-spends/{option['id']}", None),
            ("PATCH", f"/min-spends/{option['id']}", {"amount_cents": 1}),
            ("POST", f"/min-spends/{option['id']}/approve", None),
            ("POST", f"/min-spends/{option['id']}/confirm", {"channel": "phone"}),
            ("POST", f"/min-spends/{option['id']}/decline", {"channel": "phone"}),
            ("GET", f"/min-spends/{option['id']}/reference", None),
        ):
            r = call(client, method, path, rival, json=body)
            assert r.status_code == 404, (method, path, r.status_code, r.text)
        account = FnMailboxAccount(
            organization_id=w.org.id, venue_id=w.venue.id, google_email="ev@brew.bar"
        )
        w.db.add(account)
        w.db.flush()
        link = call(
            client,
            "POST",
            f"/enquiries/{enquiry_id}/threads",
            w.staff,
            json={"account_id": account.id, "gmail_thread_id": "t-9"},
        ).json()["link_id"]
        assert call(client, "DELETE", f"/thread-links/{link}", rival).status_code == 404
        assert call(client, "GET", "/enquiries", rival).json()["rows"] == []
        assert call(client, "GET", "/contacts", rival).json()["rows"] == []
        assert (
            call(client, "GET", "/enquiries?venue=" + w.venue.id, rival).json()["rows"]
            == []
        )
        # Nor can they attach our mailbox's thread to a record of their own.
        theirs = call(
            client,
            "POST",
            "/enquiries",
            rival,
            json={"contact": {"name": "R", "email": "r@x.com"}},
        ).json()
        r = call(
            client,
            "POST",
            f"/enquiries/{theirs['enquiry']['id']}/threads",
            rival,
            json={"account_id": account.id, "gmail_thread_id": "t-9"},
        )
        assert r.status_code == 404

    def test_rival_cannot_attach_to_our_contact_or_venue(self, client, w):
        created = new_enquiry(client, w)
        r = call(
            client,
            "POST",
            "/enquiries",
            w.other_user,
            json={"contact_id": created["contact"]["id"], "venue_id": w.other_venue.id},
        )
        assert r.status_code == 404
        r = call(
            client,
            "POST",
            "/enquiries",
            w.other_user,
            json={"contact": {"name": "x", "email": "x@x.com"}, "venue_id": w.venue.id},
        )
        assert r.status_code == 403

    def test_staff_without_venue_access_cannot_see_or_act(self, client, w):
        contact = add_contact(w.db, w.org.id, "Gail", "gail@x.com")
        hidden = add_enquiry(
            w.db, w.org.id, contact.id, w.venue2.id, subject="Garden party"
        )
        assert (
            call(client, "GET", f"/enquiries/{hidden.id}", w.staff).status_code == 404
        )
        assert (
            call(
                client,
                "POST",
                f"/enquiries/{hidden.id}/close",
                w.staff,
                json={"reason": "x"},
            ).status_code
            == 404
        )
        assert (
            call(client, "GET", f"/enquiries/{hidden.id}", w.owner).status_code == 200
        )
        assert all(
            r["id"] != hidden.id
            for r in call(client, "GET", "/enquiries", w.staff).json()["rows"]
        )
        r = call(
            client,
            "POST",
            "/enquiries",
            w.staff,
            json={
                "contact": {"name": "x", "email": "x@x.com"},
                "venue_id": w.venue2.id,
            },
        )
        assert r.status_code == 403
        assert (
            call(client, "GET", f"/contacts/{contact.id}", w.staff).status_code == 404
        )
