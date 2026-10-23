"""An organisation's receiving rules (services/receiving_rules,
docs/plans/receiving-rules.md).

The Lott asked for it (thread 3dd88935, 22 Oct 2026): "if there's no due date
on an invoice, I always select Sunday", so the Xero export needs nothing
entered. The owner: a manager sets rules, they sit with the organisation with
venue overrides, and a rule wins over what the invoice says.
"""

import datetime as dt
import importlib.util
import pathlib

import pytest

from app.services import receiving_rules as RR
from app.services import venue_autopilot as VA
from tests.conftest import _make_membership, _make_thread
from tests.org_helpers import (
    auth_headers,
    make_org,
    make_user,
    make_venue,
    seed_system_roles,
)

D = dt.date
SUNDAY_ON_OR_AFTER = {"basis": "weekday_on_or_after", "weekday": 6, "when": "always"}
#: Steve's own (The Lott, 22 Oct 2026): "if there's no due date … I always select Sunday".
STEVES_RULE = {**SUNDAY_ON_OR_AFTER, "when": "no_due_date"}


class TestTheDueDate:
    def test_each_way_of_working_it_out(self):
        wed, sun = D(2026, 10, 21), D(2026, 10, 25)
        assert RR.due_date_for(wed, SUNDAY_ON_OR_AFTER) == sun
        assert RR.due_date_for(sun, SUNDAY_ON_OR_AFTER) == sun
        after = {"basis": "weekday_after", "weekday": 6}
        assert RR.due_date_for(sun, after) == D(2026, 11, 1)
        assert RR.due_date_for(wed, {"basis": "days_after", "days": 14}) == D(
            2026, 11, 4
        )
        nxt = {"basis": "day_of_next_month", "day": 31}
        assert RR.due_date_for(D(2026, 1, 15), nxt) == D(
            2026, 2, 28
        )  # the month's last day
        assert RR.due_date_for(
            D(2026, 12, 3), {"basis": "day_of_next_month", "day": 20}
        ) == D(2027, 1, 20)
        assert RR.due_date_for(wed, {"basis": "invoice_date"}) == wed

    def test_settings_are_checked_and_said_in_words(self):
        assert (
            RR.clean(
                "due_date",
                {"basis": "weekday_on_or_after", "weekday": "sunday", "days": 3},
            )
            == SUNDAY_ON_OR_AFTER
        )
        assert (
            RR.describe("due_date", SUNDAY_ON_OR_AFTER)
            == "the Sunday on or after the invoice date, on every invoice"
        )
        assert (
            RR.describe("due_date", {"basis": "day_of_next_month", "day": 20})
            == "the 20th of the month after the invoice, on every invoice"
        )
        for bad in (
            ("due_date", {"basis": "weekday_after"}),
            ("due_date", {"basis": "days_after", "days": 400}),
            ("due_date", {"basis": "fortnightly"}),
            ("supplier_terms", {"basis": "invoice_date"}),
        ):
            with pytest.raises(RR.RuleError):
                RR.clean(*bad)

    def test_examples_settle_which_sunday(self):
        shown = RR.examples("due_date", SUNDAY_ON_OR_AFTER, today=D(2026, 10, 22))
        assert [(e["invoice"], e["due"]) for e in shown] == [
            ("2026-10-19", "2026-10-25"),
            ("2026-10-21", "2026-10-25"),
            ("2026-10-24", "2026-10-25"),
            ("2026-10-25", "2026-10-25"),
        ]


def _world(db):
    roles = seed_system_roles(db)
    org = make_org(db, "Lott Group")
    lott, other = (
        make_venue(db, "The Lott Cafe", org),
        make_venue(db, "Lott Kiosk", org),
    )
    manager, staff = make_user(db), make_user(db)
    _make_membership(db, manager, org).role_id = roles["manager"].id
    _make_membership(db, staff, org).role_id = roles["team_member"].id
    db.flush()
    return org, lott, other, manager, staff


class TestWhereARuleApplies:
    def test_a_venues_own_rule_overrides_the_organisations(self, db_session):
        org, lott, other, manager, _ = _world(db_session)
        RR.set_rule(
            db_session,
            org_id=org.id,
            venue_id=None,
            kind="due_date",
            params={"basis": "days_after", "days": 7},
            note=None,
            user=manager,
        )
        RR.set_rule(
            db_session,
            org_id=org.id,
            venue_id=lott.id,
            kind="due_date",
            params=SUNDAY_ON_OR_AFTER,
            note="always Sunday",
            user=manager,
        )
        assert (
            RR.effective(db_session, lott.id)["due_date"].params == SUNDAY_ON_OR_AFTER
        )
        assert RR.effective(db_session, other.id)["due_date"].params == {
            "basis": "days_after",
            "days": 7,
            "when": "always",
        }
        # Saving again replaces, never duplicates.
        RR.set_rule(
            db_session,
            org_id=org.id,
            venue_id=lott.id,
            kind="due_date",
            params={"basis": "weekday_after", "weekday": 6},
            note=None,
            user=manager,
        )
        assert RR.effective(db_session, lott.id)["due_date"].note == "always Sunday"
        RR.remove_rule(db_session, org_id=org.id, venue_id=lott.id, kind="due_date")
        assert (
            RR.effective(db_session, lott.id)["due_date"].params["basis"]
            == "days_after"
        )

    def test_a_venue_in_another_organisation_is_refused(self, db_session):
        org, _, _, manager, _ = _world(db_session)
        stranger = make_venue(db_session, "Elsewhere", make_org(db_session, "Other"))
        with pytest.raises(RR.RuleError):
            RR.set_rule(
                db_session,
                org_id=org.id,
                venue_id=stranger.id,
                kind="due_date",
                params=SUNDAY_ON_OR_AFTER,
                note=None,
                user=manager,
            )


class TestInTheReview:
    def _invoice(self, **over):
        return {
            "issued_at": "2026-10-21T00:00:00",
            "due_at": "2026-10-21",
            "lines": [],
            "suggestions": [],
            "issues": [],
            "suggestion_actions": [],
            **over,
        }

    def test_the_rule_is_a_suggestion_marked_as_the_venues(self, db_session):
        org, lott, _, manager, _ = _world(db_session)
        RR.set_rule(
            db_session,
            org_id=org.id,
            venue_id=lott.id,
            kind="due_date",
            params=SUNDAY_ON_OR_AFTER,
            note=None,
            user=manager,
        )
        data, suggestions = self._invoice(), []
        RR.apply(db_session, lott.id, data, {"issued_at": "2026-10-21"}, suggestions)
        [s] = suggestions
        assert (s["id"], s["kind"], s["field"], s["sure"]) == (
            "rule:due_date",
            "header_value",
            "due_at",
            True,
        )
        assert s["apply"] == {"due_at": "2026-10-25"}
        assert s["explanation"].startswith(
            "The Lott Cafe's rule: the due date is the Sunday on or after"
        )
        lott.name = "Bessie & Royals"  # a name ending in s takes an apostrophe only
        suggestions = []
        RR.apply(db_session, lott.id, self._invoice(), None, suggestions)
        assert suggestions[0]["explanation"].startswith("Bessie & Royals' rule:")
        # A rule wins over the invoice: a printed due date is replaced too.
        data, suggestions = self._invoice(due_at="2026-11-30"), []
        RR.apply(db_session, lott.id, data, None, suggestions)
        assert suggestions[0]["apply"] == {"due_at": "2026-10-25"}
        # Already right: nothing to suggest. Norm's reading of the date wins
        # over Loaded's when they differ.
        data, suggestions = self._invoice(due_at="2026-10-25"), []
        RR.apply(db_session, lott.id, data, None, suggestions)
        assert suggestions == []
        data, suggestions = self._invoice(), []
        RR.apply(db_session, lott.id, data, {"issued_at": "2026-10-26"}, suggestions)
        assert suggestions[0]["apply"] == {"due_at": "2026-11-01"}

    def test_no_rule_no_suggestion(self, db_session):
        _, lott, _, _, _ = _world(db_session)
        suggestions = []
        RR.apply(db_session, lott.id, self._invoice(), None, suggestions)
        assert suggestions == []

    def test_autopilot_accepts_it_like_any_answer_it_is_sure_of(self, db_session):
        from app.services.invoice_review import autopilot_accept

        org, lott, _, manager, _ = _world(db_session)
        RR.set_rule(
            db_session,
            org_id=org.id,
            venue_id=None,
            kind="due_date",
            params=SUNDAY_ON_OR_AFTER,
            note=None,
            user=manager,
        )
        data = self._invoice()
        RR.apply(db_session, lott.id, data, None, data["suggestions"])
        autopilot_accept(
            None, None, lott.id, "inv-1", data, VA.settings_from({"mode": "autopilot"})
        )
        assert data["due_at"] == "2026-10-25"
        assert data["suggestion_actions"][-1]["suggestion_id"] == "rule:due_date"


class TestOnlyWhenTheInvoiceHasNoDueDate:
    """Steve's rule covers only invoices that print no due date; one that
    prints its own keeps it. Norm reads the due date off the copy for this."""

    def _invoice(self, **over):
        return {
            "issued_at": "2026-10-21T00:00:00",
            "due_at": "2026-10-21",
            "lines": [],
            "suggestions": [],
            "issues": [],
            "suggestion_actions": [],
            **over,
        }

    def test_it_fills_only_a_missing_due_date(self, db_session):
        org, lott, _, manager, _ = _world(db_session)
        RR.set_rule(
            db_session,
            org_id=org.id,
            venue_id=lott.id,
            kind="due_date",
            params=STEVES_RULE,
            note=None,
            user=manager,
        )
        none_printed = {
            "issued_at": "2026-10-21",
            "due_at": None,
            "due_date_read": True,
        }
        suggestions = []
        RR.apply(db_session, lott.id, self._invoice(), none_printed, suggestions)
        [s] = suggestions
        assert s["apply"] == {"due_at": "2026-10-25"}
        assert "the invoice prints no due date, so it is the Sunday" in s["explanation"]
        # The invoice prints its own (Red & White Cellars INV568748: 20 Nov).
        printed = {**none_printed, "due_at": "2026-11-20"}
        suggestions = []
        RR.apply(
            db_session,
            lott.id,
            self._invoice(due_at="2026-11-20"),
            printed,
            suggestions,
        )
        assert suggestions == []
        # A read that never looked for one: unsure, so untouched.
        suggestions = []
        RR.apply(
            db_session,
            lott.id,
            self._invoice(),
            {"issued_at": "2026-10-21"},
            suggestions,
        )
        assert suggestions == []

    def test_norm_reads_the_due_date_off_the_copy(self):
        from app.services.invoice_extraction import extraction_from_loaded
        from tests.test_invoice_replica import EXTRACTION, _build

        doc = _build(dict(EXTRACTION, due_date="20/11/2026"))
        assert (doc["due_at"], doc["due_date_read"]) == ("2026-11-20", True)
        doc = _build(dict(EXTRACTION, due_date=None))
        assert (doc["due_at"], doc["due_date_read"]) == (None, True)
        assert _build(dict(EXTRACTION))["due_date_read"] is False
        # Ordermentum's data-only copy: the supplier's system set its due date.
        assert (
            extraction_from_loaded({"dueAt": "2026-10-30", "lines": []})["due_date"]
            == "2026-10-30"
        )

    def test_a_review_leaves_a_printed_due_date_and_fills_a_missing_one(
        self, db_session
    ):
        from app.services.invoice_review import review_invoice
        from tests.test_invoice_review import DETAIL, EXTRACTION, REFERENCE

        org, lott, _, manager, _ = _world(db_session)
        RR.set_rule(
            db_session,
            org_id=org.id,
            venue_id=None,
            kind="due_date",
            params=STEVES_RULE,
            note=None,
            user=manager,
        )

        def review(due_date):
            data = review_invoice(
                db_session,
                None,
                lott.id,
                "inv-1",
                lh=object(),
                detail=DETAIL(dueAt="2026-08-07"),
                extraction=EXTRACTION(due_date=due_date),
                reference=REFERENCE(),
            )
            return [s for s in data["suggestions"] if s["id"] == "rule:due_date"]

        assert review("2026-09-15") == []
        [rule] = review(None)
        assert rule["apply"] == {"due_at": "2026-08-09"}


class TestSettings:
    def test_everyone_sees_them_a_manager_changes_them(self, client, db_session):
        org, lott, _, manager, staff = _world(db_session)
        body = {"kind": "due_date", "venue_id": lott.id, "params": SUNDAY_ON_OR_AFTER}
        assert (
            client.put(
                "/api/receiving-rules", json=body, headers=auth_headers(staff)
            ).status_code
            == 403
        )
        r = client.put("/api/receiving-rules", json=body, headers=auth_headers(manager))
        assert r.status_code == 200
        view = client.get("/api/receiving-rules", headers=auth_headers(staff)).json()
        assert view["can_edit"] is False
        venue = next(v for v in view["venues"] if v["id"] == lott.id)
        assert (
            venue["in_force"][0]["description"]
            == "the Sunday on or after the invoice date, on every invoice"
        )
        assert venue["rules"][0]["scope"] == "venue"
        bad = {**body, "params": {"basis": "weekday_after"}}
        assert (
            client.put(
                "/api/receiving-rules", json=bad, headers=auth_headers(manager)
            ).status_code
            == 400
        )
        r = client.delete(
            f"/api/receiving-rules?kind=due_date&venue_id={lott.id}",
            headers=auth_headers(manager),
        )
        assert (
            r.status_code == 200
            and next(v for v in r.json()["venues"] if v["id"] == lott.id)["rules"] == []
        )

    def test_examples_before_saving(self, client, db_session):
        _, _, _, manager, _ = _world(db_session)
        r = client.get(
            "/api/receiving-rules/examples?kind=due_date&basis=weekday_on_or_after&weekday=6",
            headers=auth_headers(manager),
        )
        assert r.status_code == 200 and len(r.json()["examples"]) == 4

    def test_the_role_migration_gives_what_the_code_defines(self):
        from app.auth.permissions import STANDARD_ROLES

        path = (
            pathlib.Path(__file__).parents[1]
            / "alembic/versions/ah3j5l7n9p1s_receiving_rules.py"
        )
        spec = importlib.util.spec_from_file_location("mig", path)
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        for role, defn in STANDARD_ROLES.items():
            want = {p for p in defn["permissions"] if p == RR.PERMISSION}
            assert set(mig.GRANTS.get(role, ())) == want, role


class TestInChat:
    def _thread(self, db, user, org):
        t = _make_thread(db, user)
        t.organization_id = org.id
        db.flush()
        return t.id

    def test_a_manager_sets_it_on_a_card_a_team_member_is_told_who_can(
        self, db_session
    ):
        from app.agents import receiving_rule_tools as T

        org, lott, _, manager, staff = _world(db_session)
        params = {
            "scope": "venue",
            "venue": "The Lott Cafe",
            "basis": "weekday_on_or_after",
            "weekday": "Sunday",
            "when": "no_due_date",
            "note": "no due date → Sunday",
        }
        card = T._preview_set_receiving_rule(
            params, db_session, self._thread(db_session, manager, org)
        )
        change = card["preview"]["changes"][0]
        assert (change["before"], change["after"]) == (
            "no rule",
            "the Sunday on or after the invoice date, when the invoice has no due date",
        )
        assert "→ due" in card["preview"]["note"]
        assert "prints its own due date keeps it" in card["preview"]["note"]
        out = T._set_receiving_rule(
            params, db_session, self._thread(db_session, manager, org)
        )
        assert out["success"] and out["data"]["where"] == "The Lott Cafe"
        assert (
            RR.effective(db_session, lott.id)["due_date"].note == "no due date → Sunday"
        )
        refused = T._set_receiving_rule(
            params, db_session, self._thread(db_session, staff, org)
        )
        assert refused["success"] is False and "owner or a manager" in refused["error"]
        read = T._get_receiving_rules(
            {}, db_session, self._thread(db_session, staff, org)
        )
        assert read["data"]["can_edit"] is False and read["data"]["venues"]


def test_a_review_carries_the_rule(db_session):
    """The whole review adds it: the card and Autopilot both see it."""
    from app.services.invoice_review import review_invoice
    from tests.test_invoice_review import DETAIL, EXTRACTION, REFERENCE

    org, lott, _, manager, _ = _world(db_session)
    RR.set_rule(
        db_session,
        org_id=org.id,
        venue_id=None,
        kind="due_date",
        params=SUNDAY_ON_OR_AFTER,
        note=None,
        user=manager,
    )
    data = review_invoice(
        db_session,
        None,
        lott.id,
        "inv-1",
        lh=object(),
        detail=DETAIL(dueAt="2026-08-07"),
        extraction=EXTRACTION(),
        reference=REFERENCE(),
    )
    rule = next(s for s in data["suggestions"] if s["id"] == "rule:due_date")
    assert rule["apply"] == {"due_at": "2026-08-09"}  # invoice dated Fri 7 Aug
    assert data["suggestion_states"]["rule:due_date"]["open"] is True
