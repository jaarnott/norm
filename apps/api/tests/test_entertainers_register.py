"""The Register and Roster on venue access (D15), and the finance-only
parts: accept, the sensitive read, the payable rule, revocation on
deactivation, the exemption expiry flag, "request updated details".

The blocking D15 test (plan §17): a ``team_member`` with venue access
books, invites and approves at their venue, is refused at another, gets 403
from the payroll routes, cannot accept a submission and never receives
``bank_account`` or ``ird_number`` from any entertainer route.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.entertainers_models import EntEmailLink, EntPublicToken
from app.db.finance_models import FinAccessLog
from app.db.models import EmailLog
from app.services.entertainers import performers, tokens
from app.services.entertainers.performers import (
    account_ending,
    normalise_bank_account,
    normalise_ird,
    payable,
)
from app.services.finance_core.errors import Invalid
from tests.entertainers_world import (
    UTC,
    add_performance,
    add_performer,
    assert_no_sensitive,
    make_world,
    portal_link,
)


@pytest.fixture
def w(db_session):
    return make_world(db_session)


class TestValidators:
    @pytest.mark.parametrize(
        "value", ["49091850", "35901981", "49098576", "136410132", "136-410-132"]
    )
    def test_valid_ird_numbers(self, value):
        assert len(normalise_ird(value)) == 9

    @pytest.mark.parametrize(
        "value", ["136410133", "9125568", "49091851", "", "abc", "1234567890"]
    )
    def test_invalid_ird_numbers_are_refused(self, value):
        with pytest.raises(Invalid):
            normalise_ird(value)

    def test_bank_account_shape(self):
        assert normalise_bank_account("12-3456-7890123-00") == "12-3456-7890123-00"
        assert normalise_bank_account("12 3456 7890123 000") == "12-3456-7890123-000"
        assert normalise_bank_account("123456789012300") == "12-3456-7890123-00"
        assert account_ending("12-3456-7890134-00") == "34"
        for bad in ("12-34-567-0", "12-3456-0000000-00", "", "hello"):
            with pytest.raises(Invalid):
                normalise_bank_account(bad)


class TestD15Gate:
    """A team member with venue access, through the HTTP routes."""

    def test_team_member_books_invites_and_approves_at_their_venue(self, w, client):
        h = w.headers(w.team_member)
        r = client.post(
            "/api/entertainers/performers",
            json={"name": "New DJ", "email": "dj@example.test", "phone": "021"},
            headers=h,
        )
        assert r.status_code == 201, r.text
        performer = r.json()["performer"]
        assert (
            r.json()["invite"]["sent"] is True and "/join/" in r.json()["invite"]["url"]
        )
        assert_no_sensitive(r.json())
        r = client.post(
            "/api/entertainers/performances",
            json={
                "venue_id": w.venue.id,
                "performer_id": performer["id"],
                "performance_date": "2026-10-10",
                "start_time": "21:00",
                "end_time": "01:00",
                "rate_cents": 10000,
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
        perf = r.json()["performance"]
        assert perf["agreed_cents"] == 40_000 and perf["status"] == "awaiting_approvals"
        r = client.post(
            f"/api/entertainers/performances/{perf['id']}/approve",
            json={
                "actual_start_time": "21:00",
                "actual_end_time": "00:30",
                "version": perf["version"],
            },
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert r.json()["performance"]["actual_cents"] == 35_000
        assert r.json()["performance"]["status"] == "awaiting_performer"
        assert_no_sensitive(r.json())

    def test_team_member_is_refused_at_another_venue(self, w, client):
        h = w.headers(w.team_member)
        performer = add_performer(w.db, w.org)
        r = client.post(
            "/api/entertainers/performances",
            json={
                "venue_id": w.venue2.id,
                "performer_id": performer.id,
                "performance_date": "2026-10-10",
                "start_time": "21:00",
                "end_time": "01:00",
                "rate_cents": 10000,
            },
            headers=h,
        )
        assert r.status_code == 403
        other = add_performance(w.db, w.org, w.venue2, performer)
        assert (
            client.get(
                f"/api/entertainers/performances/{other.id}", headers=h
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/entertainers/performances/{other.id}/approve",
                json={
                    "actual_start_time": "21:00",
                    "actual_end_time": "01:00",
                    "version": 1,
                },
                headers=h,
            ).status_code
            == 404
        )
        roster = client.get(
            "/api/entertainers/roster",
            params={"from": "2026-10-01", "to": "2026-10-31"},
            headers=h,
        ).json()
        assert other.id not in {p["id"] for p in roster["performances"]}
        assert (
            client.get(
                "/api/entertainers/roster", params={"venue_id": w.venue2.id}, headers=h
            ).status_code
            == 403
        )

    def test_team_member_gets_403_from_payroll_routes(self, w, client):
        h = w.headers(w.team_member)
        assert client.get("/api/entertainers/payments", headers=h).status_code == 403
        performer = add_performer(w.db, w.org, status="submitted")
        assert (
            client.post(
                f"/api/entertainers/performers/{performer.id}/accept", headers=h
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/entertainers/performers/{performer.id}/sensitive", headers=h
            ).status_code
            == 403
        )
        r = client.get("/api/payroll/entities", headers=h)
        assert r.status_code in (
            403,
            404,
        )  # F3's router: 403 once mounted; 404 while it isn't

    def test_team_member_never_receives_bank_or_ird(self, w, client):
        h = w.headers(w.team_member)
        performer = add_performer(w.db, w.org)
        perf = add_performance(w.db, w.org, w.venue, performer)
        for path in (
            "/api/entertainers/meta",
            "/api/entertainers/performers",
            f"/api/entertainers/performers/{performer.id}",
            "/api/entertainers/roster?from=2026-10-01&to=2026-10-31",
            f"/api/entertainers/performances/{perf.id}",
            "/api/entertainers/emails",
        ):
            r = client.get(path, headers=h)
            assert r.status_code == 200, (path, r.text)
            assert_no_sensitive(r.json())
            body = r.json()
            if "performer" in body:
                assert "bank_account_ending" not in body["performer"]

    def test_a_member_with_no_venue_rows_sees_nothing_and_writes_nothing(
        self, w, client
    ):
        h = w.headers(w.nobody)
        assert (
            client.get("/api/entertainers/roster", headers=h).json()["performances"]
            == []
        )
        r = client.post(
            "/api/entertainers/performers",
            json={"name": "X", "email": "x@example.test"},
            headers=h,
        )
        assert r.status_code == 403

    def test_another_organisation_is_not_found(self, w, client):
        performer = add_performer(w.db, w.org)
        h = {**w.headers(w.other_user), "X-Organization": w.other_org.id}
        assert (
            client.get(
                f"/api/entertainers/performers/{performer.id}", headers=h
            ).status_code
            == 404
        )


class TestFinance:
    def test_payroll_write_sees_endings_and_the_sensitive_route_logs(
        self, w, client, caplog
    ):
        performer = add_performer(w.db, w.org, bank="12-3456-7890134-00")
        r = client.get(
            f"/api/entertainers/performers/{performer.id}", headers=w.headers()
        )
        p = r.json()["performer"]
        assert p["bank_account_ending"] == "34" and p["ird_ending"] == "850"
        assert "bank_account" not in p and "ird_number" not in p
        with caplog.at_level("INFO"):
            r = client.get(
                f"/api/entertainers/performers/{performer.id}/sensitive",
                headers=w.headers(),
            )
        assert r.json() == {
            "bank_account": "12-3456-7890134-00",
            "ird_number": "049091850",
            "date_of_birth": "1990-01-02",
        }
        assert any("sensitive read" in m and performer.id in m for m in caplog.messages)

    def test_accept_needs_a_submission_and_makes_the_performer_payable(self, w, client):
        performer = add_performer(w.db, w.org, status="submitted")
        assert payable(w.db, performer) == (
            False,
            "submitted — finance has not accepted it yet",
        )
        r = client.post(
            f"/api/entertainers/performers/{performer.id}/accept", headers=w.headers()
        )
        assert (
            r.status_code == 200
            and r.json()["performer"]["onboarding_status"] == "accepted"
        )
        assert r.json()["performer"]["payable"] is True
        assert (
            client.post(
                f"/api/entertainers/performers/{performer.id}/accept",
                headers=w.headers(),
            ).status_code
            == 409
        )

    def test_the_payable_rule(self, w):
        today = dt.date(2026, 10, 20)
        assert payable(
            w.db, add_performer(w.db, w.org, name="A", declaration=False), today=today
        ) == (False, "no IR330C declaration on record")
        assert payable(
            w.db, add_performer(w.db, w.org, name="B", lane="invoice"), today=today
        ) == (True, None)
        expired = add_performer(
            w.db,
            w.org,
            name="C",
            lane="exemption",
            exemption_expiry_date=dt.date(2026, 10, 1),
        )
        assert (
            payable(w.db, expired, today=today)[1]
            == "exemption certificate has expired"
        )
        soon = add_performer(
            w.db,
            w.org,
            name="D",
            lane="exemption",
            exemption_expiry_date=dt.date(2026, 11, 10),
        )
        assert performers.exemption_state(soon, today) == (True, False)
        changed = add_performer(
            w.db, w.org, name="E", bank_changed_at=dt.datetime(2026, 10, 15, tzinfo=UTC)
        )
        assert payable(w.db, changed, today=today) == (
            False,
            "bank account changed — accept again",
        )
        inactive = add_performer(w.db, w.org, name="F", is_active=False)
        assert payable(w.db, inactive, today=today) == (False, "performer is inactive")

    def test_deactivation_revokes_every_live_link(self, w, client):
        performer = add_performer(w.db, w.org)
        token, _url = portal_link(w.db, w.org, performer)
        tokens.mint(
            w.db, org_id=w.org.id, purpose="onboarding", performer_id=performer.id
        )
        assert (
            w.db.query(EntPublicToken)
            .filter(
                EntPublicToken.performer_id == performer.id,
                EntPublicToken.revoked_at.is_(None),
            )
            .count()
            == 2
        )
        r = client.post(
            f"/api/entertainers/performers/{performer.id}/deactivate",
            headers=w.headers(w.team_member),
        )
        assert r.status_code == 200 and r.json()["performer"]["is_active"] is False
        assert (
            w.db.query(EntPublicToken)
            .filter(
                EntPublicToken.performer_id == performer.id,
                EntPublicToken.revoked_at.is_(None),
            )
            .count()
            == 0
        )
        assert tokens.resolve(w.db, token, purposes=("portal",)) is None

    def test_resend_invite_revokes_the_old_link_and_records_the_email(self, w, client):
        performer = add_performer(w.db, w.org, status="invited")
        first = client.post(
            f"/api/entertainers/performers/{performer.id}/invite",
            headers=w.headers(w.team_member),
        ).json()
        second = client.post(
            f"/api/entertainers/performers/{performer.id}/invite",
            headers=w.headers(w.team_member),
        ).json()
        assert first["url"] != second["url"]
        old = first["url"].rsplit("/", 1)[-1]
        assert tokens.resolve(w.db, old, purposes=("onboarding",)) is None
        new = second["url"].rsplit("/", 1)[-1]
        assert tokens.resolve(w.db, new, purposes=("onboarding",)) is not None
        links = (
            w.db.query(EntEmailLink)
            .filter(
                EntEmailLink.performer_id == performer.id,
                EntEmailLink.kind == "ent_invite",
            )
            .all()
        )
        assert len(links) == 2
        log = w.db.query(EmailLog).filter(EmailLog.id == links[0].email_log_id).one()
        # shadow? no — live, but outside production: suppressed, with the body kept
        assert log.status == "suppressed" and first["url"] in log.html_body

    def test_request_updated_details_sends_a_fresh_link_for_an_accepted_performer(
        self, w, client
    ):
        performer = add_performer(w.db, w.org)
        assert (
            client.post(
                f"/api/entertainers/performers/{performer.id}/invite",
                headers=w.headers(),
            ).status_code
            == 409
        )
        r = client.post(
            f"/api/entertainers/performers/{performer.id}/request-details",
            headers=w.headers(w.team_member),
        )
        assert r.status_code == 200 and "/join/" in r.json()["url"]
        link = (
            w.db.query(EntEmailLink)
            .filter(EntEmailLink.performer_id == performer.id)
            .one()
        )
        log = w.db.query(EmailLog).filter(EmailLog.id == link.email_log_id).one()
        assert "update your performer details" in log.subject

    def test_finance_fields_need_payroll_write(self, w, client):
        performer = add_performer(w.db, w.org)
        body = {"lane": "invoice", "version": performer.version}
        assert (
            client.put(
                f"/api/entertainers/performers/{performer.id}",
                json=body,
                headers=w.headers(w.team_member),
            ).status_code
            == 403
        )
        r = client.put(
            f"/api/entertainers/performers/{performer.id}",
            json=body,
            headers=w.headers(),
        )
        assert r.status_code == 200 and r.json()["performer"]["lane"] == "invoice"
        stale = client.put(
            f"/api/entertainers/performers/{performer.id}",
            json={"name": "X", "version": 1},
            headers=w.headers(w.team_member),
        )
        assert stale.status_code == 409

    def test_missing_declarations_lists_wt_performers_without_one(self, w, client):
        a = add_performer(w.db, w.org, name="No Decl", declaration=False)
        add_performer(w.db, w.org, name="Has Decl")
        add_performer(w.db, w.org, name="Invoice", lane="invoice")
        r = client.get(
            "/api/entertainers/performers/missing-declarations",
            headers=w.headers(w.team_member),
        )
        assert [p["id"] for p in r.json()["performers"]] == [a.id]
        meta = client.get("/api/entertainers/meta", headers=w.headers()).json()
        assert meta["counts"]["missing_declarations"] == 1 and meta["can_pay"] is True

    def test_module_off_is_404_everywhere(self, db_session, client):
        w = make_world(db_session, mode="off")
        assert (
            client.get("/api/entertainers/meta", headers=w.headers()).status_code == 404
        )


class TestAccessLog:
    """Plan §6.5 item 3: every read of a bank account or IRD number writes an
    access row; a read that shows neither writes none."""

    def _rows(self, w):
        return (
            w.db.query(FinAccessLog)
            .filter(FinAccessLog.organization_id == w.org.id)
            .order_by(FinAccessLog.at, FinAccessLog.field)
            .all()
        )

    def test_the_sensitive_route_logs_each_field(self, w, client):
        performer = add_performer(w.db, w.org)
        assert self._rows(w) == []
        r = client.get(
            f"/api/entertainers/performers/{performer.id}/sensitive",
            headers=w.headers(),
        )
        assert r.status_code == 200
        rows = self._rows(w)
        assert sorted((x.subject_kind, x.subject_id, x.field) for x in rows) == [
            ("performer", performer.id, "bank_account"),
            ("performer", performer.id, "date_of_birth"),
            ("performer", performer.id, "ird_number"),
        ]
        assert all(
            x.user_id == w.payroll_person.id
            and x.route == "entertainers.performers.sensitive"
            and x.actor_label is None
            and x.at is not None
            for x in rows
        )

    def test_the_register_endings_log_and_a_team_members_read_does_not(self, w, client):
        performer = add_performer(w.db, w.org)
        r = client.get(
            f"/api/entertainers/performers/{performer.id}",
            headers=w.headers(w.team_member),
        )
        assert r.status_code == 200 and self._rows(w) == []
        r = client.get(
            "/api/entertainers/roster?from=2026-10-01&to=2026-10-31",
            headers=w.headers(),
        )
        assert r.status_code == 200 and self._rows(w) == []
        r = client.get(
            f"/api/entertainers/performers/{performer.id}", headers=w.headers()
        )
        assert r.json()["performer"]["bank_account_ending"] == "23"
        assert sorted(x.field for x in self._rows(w)) == [
            "bank_account_ending",
            "ird_number_ending",
        ]
        assert {x.route for x in self._rows(w)} == {"entertainers.register"}

    def test_a_job_names_itself(self, w):
        from app.services.finance_core import access_log
        from app.services.finance_core.ctx import for_org

        row = access_log.record(
            w.db,
            for_org(w.db, w.org.id, actor_label="tick"),
            subject_kind="staff_member",
            subject_id="sm-1",
            field="staff_detail",
            route="payroll.leave.eligibility",
        )
        assert row.user_id is None and row.actor_label == "tick"
        with pytest.raises(Invalid):
            access_log.record(
                w.db,
                w.ctx(),
                subject_kind="venue",
                subject_id="x",
                field="f",
                route="r",
            )
