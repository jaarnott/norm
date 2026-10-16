"""Norm Functions foundation: schema tenancy, the ctx, shadow mode, the
entitlement fallback, money and time rules, and the derived-status twins.

The status tests are the important ones to keep honest: every list in
Functions filters by the SQL derivation and every detail page shows the Python
one. If they ever disagree, a "needs reply" count and the enquiry it links to
tell staff two different things.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, UniqueConstraint
from sqlalchemy.exc import IntegrityError

from app.db import functions_models as fm
from app.db.functions_models import (
    FnEnquiry,
    FnEnquiryClosure,
    FnEvent,
    FnEventChange,
    FnEventSectionConfirmation,
    FnMailboxAccount,
    FnMailboxMessage,
    FnMailboxThread,
    FnMinSpend,
    FnMinSpendApproval,
    FnMinSpendResponse,
    FnPreorderMenu,
    FnPreorderMenuItem,
    FnProposalEvent,
    FnThreadLink,
)
from app.db.models import Organization, Venue
from app.services.functions import (
    Forbidden,
    Invalid,
    NotFound,
    NotLive,
    company,
    contacts,
    enquiries,
    entitlement,
    events,
    money,
    status,
    timeutil,
)
from app.services.functions.ctx import for_client, for_org, for_quote, require_live
from tests.functions_world import UTC, add_contact, add_enquiry, make_world

NZ = ZoneInfo("Pacific/Auckland")


@pytest.fixture
def w(db_session):
    return make_world(db_session)


# ===========================================================================
# Schema
# ===========================================================================

_PLAIN_FK_TARGETS = {"organizations", "users", "connector_configs"}


class TestTenancyMetadata:
    """Every fn_* table carries the tenancy rules in the schema itself."""

    def test_every_table_is_org_scoped_with_composite_references(self):
        problems = []
        for model in fm.FUNCTIONS_MODELS:
            t = model.__table__
            org = t.c.get("organization_id")
            if org is None or org.nullable:
                problems.append(f"{t.name}: organization_id missing or nullable")
            if not any(
                isinstance(c, UniqueConstraint)
                and [col.name for col in c.columns] == ["organization_id", "id"]
                for c in t.constraints
            ):
                problems.append(f"{t.name}: no UNIQUE (organization_id, id)")
            for fk in (c for c in t.constraints if isinstance(c, ForeignKeyConstraint)):
                target = fk.referred_table.name
                local = [c.name for c in fk.columns]
                remote = [e.column.name for e in fk.elements]
                if target in fm.FUNCTIONS_TABLES or target == "venues":
                    if local[0] != "organization_id" or remote[0] != "organization_id":
                        problems.append(
                            f"{t.name}.{local}: reference to {target} not org-composite"
                        )
                    # Plain SET NULL would null organization_id too; only the
                    # column-list form naming the reference itself is allowed.
                    own_set_null = f"SET NULL ({local[-1]})"
                    if fk.ondelete not in ("CASCADE", "NO ACTION", own_set_null):
                        problems.append(
                            f"{t.name}.{local}: composite FK ondelete {fk.ondelete}"
                        )
                elif target not in _PLAIN_FK_TARGETS:
                    problems.append(f"{t.name}.{local}: unexpected FK to {target}")
        assert not problems, "\n".join(problems)

    def test_timestamps_money_and_identifier_lengths(self):
        problems = []
        for model in fm.FUNCTIONS_MODELS:
            t = model.__table__
            for name in ("created_at", "updated_at"):
                col = t.c.get(name)
                if col is None or col.nullable or not isinstance(col.type, DateTime):
                    problems.append(f"{t.name}.{name}")
                elif not col.type.timezone:
                    problems.append(f"{t.name}.{name} not timestamptz")
            for col in t.c:
                if col.name.endswith("_cents") and not isinstance(col.type, Integer):
                    problems.append(f"{t.name}.{col.name} is not integer cents")
            names = [c.name for c in t.constraints if c.name] + [
                i.name for i in t.indexes
            ]
            problems += [f"{t.name}: {n} > 63 chars" for n in names if len(n) > 63]
        assert not problems, "\n".join(problems)

    def test_editable_rows_are_versioned(self):
        for model in (FnEnquiry, FnEvent, FnMinSpend, FnPreorderMenu, fm.FnContact):
            assert model.__mapper__.version_id_col is not None, model.__name__

    def test_core_tables_gained_the_functions_columns(self):
        assert any(
            isinstance(c, UniqueConstraint)
            and [col.name for col in c.columns] == ["organization_id", "id"]
            for c in Venue.__table__.constraints
        )
        cols = Organization.__table__.c
        for name in ("currency", "default_timezone", "tax_label", "functions_mode"):
            assert not cols[name].nullable and cols[name].server_default is not None

    def test_migration_creates_exactly_the_model_tables(self):
        path = next(
            pathlib.Path(__file__).parent.parent.glob(
                "alembic/versions/*_norm_functions.py"
            )
        )
        spec = importlib.util.spec_from_file_location("fn_migration", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert set(mod.FN_TABLES) == fm.FUNCTIONS_TABLES
        assert len(mod.FN_TABLES) == len(set(mod.FN_TABLES))

    def test_database_refuses_a_cross_org_reference(self, w):
        theirs = add_contact(w.db, w.other_org.id)
        with pytest.raises(IntegrityError), w.db.begin_nested():
            w.db.add(
                FnEnquiry(
                    organization_id=w.org.id, contact_id=theirs.id, source="email"
                )
            )
            w.db.flush()

    def test_database_refuses_another_venues_area(self, w):
        c = add_contact(w.db, w.org.id)
        e = add_enquiry(w.db, w.org.id, c.id, w.venue.id)
        with pytest.raises(IntegrityError), w.db.begin_nested():
            w.db.add(
                FnMinSpend(
                    organization_id=w.org.id,
                    enquiry_id=e.id,
                    venue_id=w.venue2.id,
                    area_id=w.deck.id,
                    amount_cents=1,
                )
            )
            w.db.flush()

    def test_database_allows_one_live_event_per_enquiry(self, w):
        c = add_contact(w.db, w.org.id)
        e = add_enquiry(w.db, w.org.id, c.id, w.venue.id)
        with pytest.raises(IntegrityError), w.db.begin_nested():
            for _ in range(2):
                w.db.add(
                    FnEvent(
                        organization_id=w.org.id,
                        enquiry_id=e.id,
                        contact_id=c.id,
                        venue_id=w.venue.id,
                        name="x",
                        event_date=dt.date(2026, 12, 1),
                    )
                )
            w.db.flush()


# ===========================================================================
# Ctx, cross-org and venue scoping, mode
# ===========================================================================


class TestCtx:
    def test_staff_see_their_venues_owners_see_all(self, w):
        assert w.ctx().venue_ids == {w.venue.id}
        assert w.ctx(w.owner).venue_ids == {w.venue.id, w.venue2.id}
        assert w.ctx(w.owner).is_owner and not w.ctx().is_owner

    def test_venue_access_in_another_org_never_counts(self, w):
        from tests.conftest import _make_venue_access

        _make_venue_access(w.db, w.staff, w.other_venue)
        assert w.other_venue.id not in w.ctx().venue_ids

    def test_naming_another_org_is_refused(self, w):
        with pytest.raises(Forbidden):
            w.ctx(org_id=w.other_org.id)

    def test_system_and_client_ctx(self, w):
        sys_ctx = for_org(w.db, w.org.id, actor_label="importer")
        assert sys_ctx.actor_kind == "system" and sys_ctx.venue_ids == {
            w.venue.id,
            w.venue2.id,
        }
        c = add_contact(w.db, w.org.id)
        ev = FnEvent(
            organization_id=w.org.id,
            contact_id=c.id,
            venue_id=w.venue.id,
            name="x",
            event_date=dt.date(2026, 12, 1),
        )
        w.db.add(ev)
        w.db.flush()
        client = for_client(w.db, w.org.id, event_id=ev.id, email=" Casey@X.com ")
        assert client.actor_kind == "client" and client.client_email == "casey@x.com"
        assert client.venue_ids == {w.venue.id} and client.client_event_id == ev.id
        with pytest.raises(NotFound):  # another org's id never resolves
            for_client(w.db, w.other_org.id, event_id=ev.id, email="a@b.co")
        e = add_enquiry(w.db, w.org.id, c.id, None)
        quote = for_quote(w.db, w.org.id, enquiry_id=e.id)
        assert quote.actor_kind == "client" and quote.client_enquiry_id == e.id
        with pytest.raises(NotFound):
            for_quote(w.db, w.other_org.id, enquiry_id=e.id)


class TestCrossOrg:
    """A ctx from one organisation can never read or write another's rows."""

    def test_reads_are_not_found(self, w):
        theirs_contact = add_contact(w.db, w.other_org.id, email="rival@x.com")
        theirs = add_enquiry(w.db, w.other_org.id, theirs_contact.id, w.other_venue.id)
        mine = w.ctx(w.owner)
        with pytest.raises(NotFound):
            enquiries.get_enquiry(w.db, mine, theirs.id)
        with pytest.raises(NotFound):
            contacts.get_contact(w.db, mine, theirs_contact.id)
        assert contacts.find_contact_by_email(w.db, mine, "rival@x.com") is None
        assert enquiries.list_enquiries(w.db, mine) == []

    def test_writes_are_refused(self, w):
        theirs_contact = add_contact(w.db, w.other_org.id)
        theirs = add_enquiry(w.db, w.other_org.id, theirs_contact.id, w.other_venue.id)
        mine = w.ctx(w.owner)
        with pytest.raises(NotFound):
            enquiries.update_enquiry(w.db, mine, theirs.id, notes="hijack")
        with pytest.raises(NotFound):
            enquiries.close_enquiry(w.db, mine, theirs.id, reason="x")
        with pytest.raises(NotFound):
            enquiries.create_enquiry(w.db, mine, contact_id=theirs_contact.id)
        my_contact = add_contact(w.db, w.org.id)
        with pytest.raises(Forbidden):
            enquiries.create_enquiry(
                w.db, mine, contact_id=my_contact.id, venue_id=w.other_venue.id
            )
        with pytest.raises(Forbidden):
            events.create_event(
                w.db,
                mine,
                venue_id=w.other_venue.id,
                contact_id=my_contact.id,
                event_date=dt.date(2026, 12, 1),
                booking_type="seated_dining",
            )
        assert theirs.notes is None

    def test_staff_cannot_reach_another_venue_in_their_own_org(self, w):
        c = add_contact(w.db, w.org.id)
        there = add_enquiry(w.db, w.org.id, c.id, w.venue2.id)
        group_wide = add_enquiry(w.db, w.org.id, c.id, None)
        with pytest.raises(NotFound):
            enquiries.get_enquiry(w.db, w.ctx(), there.id)
        assert enquiries.get_enquiry(w.db, w.ctx(), group_wide.id).id == group_wide.id
        listed = {r.enquiry.id for r in enquiries.list_enquiries(w.db, w.ctx())}
        assert listed == {group_wide.id}

    def test_contact_visible_through_an_enquiry_at_your_venue(self, w):
        c_here = add_contact(w.db, w.org.id, "Here")
        c_there = add_contact(w.db, w.org.id, "There")
        add_enquiry(w.db, w.org.id, c_here.id, w.venue.id)
        add_enquiry(w.db, w.org.id, c_there.id, w.venue2.id)
        loose = add_contact(w.db, w.org.id, "Loose")
        names = {c.name for c in contacts.search_contacts(w.db, w.ctx())}
        assert names == {"Here", "Loose"}
        with pytest.raises(NotFound):
            contacts.get_contact(w.db, w.ctx(), c_there.id)
        assert contacts.get_contact(w.db, w.ctx(), loose.id)


class TestMode:
    @pytest.mark.parametrize("mode", ["shadow", "off"])
    def test_writes_refused_reads_allowed(self, db_session, mode):
        w = make_world(db_session, mode=mode)
        c = add_contact(w.db, w.org.id)
        e = add_enquiry(w.db, w.org.id, c.id, w.venue.id)
        ctx = w.ctx()
        with pytest.raises(NotLive):
            require_live(ctx)
        with pytest.raises(NotLive):
            enquiries.create_enquiry(w.db, ctx, contact_id=c.id)
        with pytest.raises(NotLive):
            enquiries.close_enquiry(w.db, ctx, e.id, reason="x")
        with pytest.raises(NotLive):
            contacts.create_contact(w.db, ctx, name="New")
        assert enquiries.get_enquiry(w.db, ctx, e.id).id == e.id
        assert len(enquiries.list_enquiries(w.db, ctx)) == 1

    def test_company_settings_change_in_any_mode(self, db_session):
        w = make_world(db_session, mode="shadow")
        settings = company.update_company_settings(
            w.db, w.ctx(w.owner), functions_mode="live"
        )
        assert settings.functions_mode == "live"
        with pytest.raises(Forbidden):
            company.update_company_settings(w.db, w.ctx(), tax_label="excl. GST")


class TestEntitlementFallback:
    @staticmethod
    def _catalog(monkeypatch, rows):
        monkeypatch.setattr("app.services.entitlements._catalog", lambda cdb: rows)

    def test_no_catalog_row_follows_the_mode(self, w, monkeypatch):
        self._catalog(monkeypatch, [])
        assert entitlement.functions_enabled(w.db, w.db, w.org.id) is True
        w.org.functions_mode = "shadow"
        assert entitlement.functions_enabled(w.db, w.db, w.org.id) is True
        w.org.functions_mode = "off"
        w.db.flush()
        assert entitlement.functions_enabled(w.db, w.db, w.org.id) is False

    def test_apps_on_hook(self, w):
        row = SimpleNamespace(slug="functions")
        assert "functions" in entitlement.adjust_apps_on(set(), w.org.id, w.db, [])
        # With a catalog row, the catalog decides (not added here) ...
        assert entitlement.adjust_apps_on(set(), w.org.id, w.db, [row]) == set()
        assert entitlement.adjust_apps_on({"functions"}, w.org.id, w.db, [row]) == {
            "functions"
        }
        # ... but off always wins.
        w.org.functions_mode = "off"
        w.db.flush()
        assert entitlement.adjust_apps_on(
            {"functions", "x"}, w.org.id, w.db, [row]
        ) == {"x"}

    def test_catalog_row_needs_entitlement_and_mode(self, w, monkeypatch):
        row = SimpleNamespace(
            slug="functions", tier="app", bundled=False, composition={}
        )
        self._catalog(monkeypatch, [row])
        monkeypatch.setattr("app.services.entitlements.apps_on", lambda *a: set())
        assert entitlement.functions_enabled(w.db, w.db, w.org.id) is False
        monkeypatch.setattr(
            "app.services.entitlements.apps_on", lambda *a: {"functions"}
        )
        assert entitlement.functions_enabled(w.db, w.db, w.org.id) is True


# ===========================================================================
# Money
# ===========================================================================


class TestMoney:
    def test_set_menu_is_guests_times_price_plus_priced_extras(self):
        menu = {"is_set_menu": True, "price_per_person_cents": 6500, "guest_count": 40}
        items = [
            {"kind": "header", "name": "Mains"},
            {"kind": "item", "quantity": 20, "unit_price_cents": None},
            {"kind": "item", "quantity": 20, "unit_price_cents": None},
            {"kind": "item", "quantity": 3, "unit_price_cents": 1200},  # extra platter
        ]
        assert money.menu_food_cents(menu, items) == 40 * 6500 + 3 * 1200

    def test_a_la_carte_sums_priced_lines_and_ignores_headers(self):
        menu = {"is_set_menu": False, "price_per_person_cents": 9999, "guest_count": 99}
        items = [
            {"kind": "header", "quantity": 5, "unit_price_cents": 100},
            {"kind": "item", "quantity": 10, "unit_price_cents": 450},
            {"kind": "item", "quantity": 4, "unit_price_cents": None},
        ]
        assert money.menu_food_cents(menu, items) == 4500

    def test_option_total_is_the_larger_of_min_spend_and_food(self):
        assert money.option_total_cents(200_000, 150_000) == 200_000
        assert money.option_total_cents(200_000, 260_000) == 260_000
        assert money.option_total_cents(None, 5) == 5

    def test_alternatives_are_never_summed(self):
        assert money.enquiry_value_cents([300_000, 500_000, 400_000]) == 500_000
        assert money.enquiry_value_cents([], food=12_000) == 12_000

    def test_event_expected_spend(self):
        assert money.event_expected_spend_cents(500_000, 200_000, 100_000) == 500_000
        assert money.event_expected_spend_cents(500_000, 450_000, 100_000) == 550_000
        assert money.event_expected_spend_cents(None, 1000, None) == 1000

    @pytest.mark.parametrize(
        "weights,total,expected",
        [
            ([50, 50], 41, [21, 20]),  # tie goes to the earlier dish
            ([34, 33, 33], 10, [4, 3, 3]),
            ([25, 25, 25, 25], 3, [1, 1, 1, 0]),
            ([10, 10], 10, [5, 5]),  # weights not adding to 100 (Orbit crashed)
            ([0, 0, 0], 4, [2, 1, 1]),
            ([100], 0, [0]),
            ([], 10, []),
        ],
    )
    def test_largest_remainder(self, weights, total, expected):
        got = money.largest_remainder(weights, total)
        assert got == expected
        if weights and total > 0:
            assert sum(got) == total

    def test_event_money_sql_agrees_with_python(self, w):
        c = add_contact(w.db, w.org.id)
        rows = []
        for i, (bar, set_pp, min_spend) in enumerate(
            [
                (0, None, None),
                (100_000, 6000, 300_000),
                (50_000, 4000, None),
                (0, None, 1000),
            ]
        ):
            ev = FnEvent(
                organization_id=w.org.id,
                contact_id=c.id,
                venue_id=w.venue.id,
                name=f"e{i}",
                event_date=dt.date(2026, 12, 1 + i),
                bar_tab_cents=bar,
            )
            w.db.add(ev)
            w.db.flush()
            if set_pp:
                m = FnPreorderMenu(
                    organization_id=w.org.id,
                    event_id=ev.id,
                    venue_id=w.venue.id,
                    name="Set",
                    is_set_menu=True,
                    price_per_person_cents=set_pp,
                    guest_count=30 + i,
                )
                w.db.add(m)
                w.db.flush()
                w.db.add_all(
                    [
                        FnPreorderMenuItem(
                            organization_id=w.org.id,
                            menu_id=m.id,
                            name="a",
                            quantity=10,
                        ),
                        FnPreorderMenuItem(
                            organization_id=w.org.id,
                            menu_id=m.id,
                            name="b",
                            quantity=2,
                            unit_price_cents=2500,
                        ),
                    ]
                )
            if min_spend:
                e = add_enquiry(w.db, w.org.id, c.id, w.venue.id)
                ev.enquiry_id = e.id
                w.db.add(
                    FnMinSpend(
                        organization_id=w.org.id,
                        enquiry_id=e.id,
                        event_id=ev.id,
                        venue_id=w.venue.id,
                        amount_cents=min_spend,
                    )
                )
            rows.append(ev)
        w.db.flush()
        sql = {
            r.event_id: r.expected_spend_cents
            for r in w.db.execute(money.event_money_select(w.org.id))
        }
        for ev in rows:
            assert (
                sql[ev.id]
                == money.event_expected_spend(w.db, ev)["expected_spend_cents"]
            )


class TestCompany:
    def test_validation_and_formatting(self, w):
        ctx = w.ctx(w.owner)
        with pytest.raises(Invalid):
            company.update_company_settings(w.db, ctx, currency="dollars")
        with pytest.raises(Invalid):
            company.update_company_settings(w.db, ctx, default_timezone="Mars/Olympus")
        s = company.update_company_settings(
            w.db, ctx, currency="aud", tax_label="incl. GST"
        )
        assert s.currency == "AUD"
        assert company.format_money(123456, "NZD") == "$1,234.56"
        assert company.format_money(120000, "NZD") == "$1,200"
        assert company.format_money(-5, "GBP") == "-£0.05"
        assert company.format_money(1000, "XYZ") == "XYZ 10"

    def test_going_live_needs_venue_timezones(self, db_session):
        w = make_world(db_session, mode="shadow")
        c = add_contact(w.db, w.org.id)
        add_enquiry(w.db, w.org.id, c.id, w.venue2.id)
        w.venue2.timezone = None
        w.db.flush()
        with pytest.raises(Invalid, match="Garden Bar"):
            company.update_company_settings(w.db, w.ctx(w.owner), functions_mode="live")


# ===========================================================================
# Time
# ===========================================================================


class TestTime:
    def test_plain_conversion_and_next_day_end(self, w):
        start, end = timeutil.plan_window(
            w.venue,
            dt.date(2026, 11, 14),
            dt.time(19, 0),
            dt.time(1, 0),
            ends_next_day=True,
        )
        assert start == dt.datetime(2026, 11, 14, 6, 0, tzinfo=UTC)  # NZDT +13
        assert end == dt.datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
        assert timeutil.infer_ends_next_day(dt.time(19), dt.time(1))
        with pytest.raises(Invalid):
            timeutil.validate_window(dt.time(19), dt.time(1), False)

    def test_spring_forward_gap_reads_forward(self, w):
        # 27 Sep 2026: Auckland jumps 02:00 NZST -> 03:00 NZDT; 02:30 does not exist.
        got = timeutil.to_instant(w.venue, dt.date(2026, 9, 27), dt.time(2, 30))
        assert got == dt.datetime(2026, 9, 26, 14, 30, tzinfo=UTC)
        assert got.astimezone(NZ).time() == dt.time(3, 30)

    def test_fall_back_hour_takes_the_first_occurrence(self, w):
        # 5 Apr 2026: 02:00-03:00 happens twice; the first is still NZDT (+13).
        got = timeutil.to_instant(w.venue, dt.date(2026, 4, 5), dt.time(2, 30))
        assert got == dt.datetime(2026, 4, 4, 13, 30, tzinfo=UTC)

    def test_standard_and_daylight_offsets(self, w):
        winter = timeutil.to_instant(w.venue, dt.date(2026, 7, 1), dt.time(12, 0))
        summer = timeutil.to_instant(w.venue, dt.date(2026, 1, 1), dt.time(12, 0))
        assert winter.hour == 0 and summer.hour == 23

    def test_trading_day_uses_day_start(self, w):
        # 01:30 Sunday local, day starts 07:00 -> Saturday's trade.
        at = dt.datetime(2026, 11, 15, 1, 30, tzinfo=NZ)
        assert timeutil.business_date(w.venue, at) == dt.date(2026, 11, 14)
        assert timeutil.business_date(w.venue, at.replace(hour=7)) == dt.date(
            2026, 11, 15
        )
        assert timeutil.local_today(w.venue, at) == dt.date(2026, 11, 15)

    def test_a_venue_without_a_timezone_is_refused(self, w):
        w.venue2.timezone = None
        with pytest.raises(Invalid, match="Garden Bar"):
            timeutil.venue_tz(w.venue2)
        w.venue2.timezone = "Local"
        with pytest.raises(Invalid):
            timeutil.to_instant(w.venue2, dt.date(2026, 1, 1), dt.time(9))


# ===========================================================================
# Next action (Python rules)
# ===========================================================================


def _facts(**kw) -> status.EnquiryFacts:
    base = dict(
        enquiry_id="e",
        created_at=dt.datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        follow_up_date=None,
        follow_up_reason=None,
        closure_kind=None,
        has_live_event=False,
        quote_sent_at=None,
        last_inbound_at=None,
        last_outbound_at=None,
        tz="Pacific/Auckland",
    )
    return status.EnquiryFacts(**(base | kw))


NOW = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)  # 13:00 on 20 Oct in Auckland


class TestNextAction:
    def test_requested_follow_up_parks_everything(self):
        f = _facts(
            follow_up_date=dt.date(2026, 10, 25),
            last_inbound_at=NOW - dt.timedelta(days=3),
        )
        a = status.next_action(f, NOW)
        assert (a.kind, a.overdue, a.due_today, a.source) == (
            "requested",
            False,
            False,
            "customer",
        )
        assert "parked" in a.why

    def test_requested_due_today_is_judged_in_venue_time(self):
        # 20 Oct 23:30 UTC is already 21 Oct in Auckland.
        late = dt.datetime(2026, 10, 20, 23, 30, tzinfo=UTC)
        a = status.next_action(_facts(follow_up_date=dt.date(2026, 10, 21)), late)
        assert a.due_today and not a.overdue
        a = status.next_action(_facts(follow_up_date=dt.date(2026, 10, 20)), late)
        assert a.overdue

    def test_reply_overdue_after_24_hours(self):
        f = _facts(
            last_inbound_at=NOW - dt.timedelta(hours=25),
            last_outbound_at=NOW - dt.timedelta(days=2),
        )
        a = status.next_action(f, NOW)
        assert a.kind == "reply_overdue" and a.overdue
        within = _facts(last_inbound_at=NOW - dt.timedelta(hours=23))
        assert status.next_action(within, NOW).kind == "none"

    def test_call_and_close_after_14_days_of_silence(self):
        f = _facts(
            last_inbound_at=NOW - dt.timedelta(days=15),
            last_outbound_at=NOW - dt.timedelta(days=14, hours=20),
        )
        a = status.next_action(f, NOW)
        assert a.kind == "day_14" and a.overdue

    def test_day_7_follow_up(self):
        f = _facts(
            created_at=NOW - dt.timedelta(days=9),
            last_inbound_at=NOW - dt.timedelta(days=9),
            last_outbound_at=NOW - dt.timedelta(days=7),
        )
        a = status.next_action(f, NOW)
        assert a.kind == "day_7" and a.due_today
        recent = _facts(
            created_at=NOW - dt.timedelta(days=3),
            last_inbound_at=NOW - dt.timedelta(days=3),
            last_outbound_at=NOW - dt.timedelta(days=2),
        )
        assert status.next_action(recent, NOW).kind == "none"

    def test_no_mail_and_closed_enquiries_owe_nothing(self):
        assert status.next_action(_facts(), NOW).kind == "none"
        lost = _facts(closure_kind="lost", follow_up_date=dt.date(2026, 10, 1))
        assert status.next_action(lost, NOW).kind == "none"
        booked = _facts(has_live_event=True, last_inbound_at=NOW - dt.timedelta(days=5))
        assert status.next_action(booked, NOW).kind == "none"

    def test_stage_ladder(self):
        assert status.enquiry_stage(_facts()) == "new"
        assert status.enquiry_stage(_facts(last_outbound_at=NOW)) == "replied"
        assert (
            status.enquiry_stage(_facts(last_outbound_at=NOW, quote_sent_at=NOW))
            == "quoted"
        )
        assert (
            status.enquiry_stage(_facts(quote_sent_at=NOW, has_live_event=True))
            == "confirmed"
        )
        assert (
            status.enquiry_stage(_facts(has_live_event=True, closure_kind="lost"))
            == "lost"
        )
        assert (
            status.enquiry_stage(_facts(closure_kind="reopened", quote_sent_at=NOW))
            == "quoted"
        )


# ===========================================================================
# SQL twin == Python twin
# ===========================================================================


def _mailbox(w):
    acct = FnMailboxAccount(
        organization_id=w.org.id,
        venue_id=w.venue.id,
        google_email="events@brewbar.example",
        status="active",
    )
    w.db.add(acct)
    w.db.flush()
    return acct


def _thread(w, acct, enquiry, messages, *, unlinked=False):
    gid = f"t-{enquiry.id[:8]}-{len(messages)}"
    t = FnMailboxThread(
        organization_id=w.org.id, account_id=acct.id, gmail_thread_id=gid
    )
    w.db.add(t)
    w.db.flush()
    for i, (when, inbound, extra) in enumerate(messages):
        w.db.add(
            FnMailboxMessage(
                organization_id=w.org.id,
                account_id=acct.id,
                thread_id=t.id,
                gmail_message_id=f"{gid}-{i}",
                gmail_thread_id=gid,
                internal_date=when,
                is_inbound=inbound,
                **extra,
            )
        )
    w.db.add(
        FnThreadLink(
            organization_id=w.org.id,
            account_id=acct.id,
            gmail_thread_id=gid,
            enquiry_id=enquiry.id,
            unlinked_at=NOW - dt.timedelta(days=1) if unlinked else None,
        )
    )


def _build_enquiry_fixtures(w):
    acct = _mailbox(w)
    c = add_contact(w.db, w.org.id)
    d = dt.timedelta
    made = []

    def enq(venue=w.venue.id, **kw):
        e = add_enquiry(w.db, w.org.id, c.id, venue, **kw)
        e.created_at = kw.get("created_at", NOW - d(days=30))
        made.append(e)
        return e

    enq()  # new, no mail
    _thread(w, acct, enq(), [(NOW - d(hours=30), True, {})])  # reply overdue
    _thread(w, acct, enq(), [(NOW - d(hours=2), True, {})])  # inbound, not yet due
    _thread(  # replied, day 7
        w, acct, enq(), [(NOW - d(days=9), True, {}), (NOW - d(days=8), False, {})]
    )
    _thread(  # replied, day 14 (silent since inbound 20 days ago)
        w, acct, enq(), [(NOW - d(days=20), True, {}), (NOW - d(days=10), False, {})]
    )
    _thread(  # outbound only internal / auto replies -> still new
        w,
        acct,
        enq(),
        [
            (NOW - d(days=3), False, {"is_internal": True}),
            (NOW - d(days=3), False, {"is_auto_reply": True}),
        ],
    )
    _thread(  # unlinked thread does not count
        w, acct, enq(), [(NOW - d(days=3), False, {})], unlinked=True
    )
    quoted = enq()
    _thread(w, acct, quoted, [(NOW - d(days=1), False, {})])
    w.db.add(
        FnProposalEvent(
            organization_id=w.org.id,
            enquiry_id=quoted.id,
            kind="sent",
            source="outbox",
            occurred_at=NOW - d(days=1),
        )
    )
    viewed_only = enq()
    w.db.add(
        FnProposalEvent(
            organization_id=w.org.id,
            enquiry_id=viewed_only.id,
            kind="viewed",
            source="quote_page",
            occurred_at=NOW,
        )
    )
    lost = enq(follow_up_date=dt.date(2026, 10, 19))
    w.db.add(
        FnEnquiryClosure(
            organization_id=w.org.id,
            enquiry_id=lost.id,
            kind="lost",
            reason="x",
            occurred_at=NOW - d(days=2),
        )
    )
    reopened = enq()
    w.db.add_all(
        [
            FnEnquiryClosure(
                organization_id=w.org.id,
                enquiry_id=reopened.id,
                kind="lost",
                reason="x",
                occurred_at=NOW - d(days=3),
            ),
            FnEnquiryClosure(
                organization_id=w.org.id,
                enquiry_id=reopened.id,
                kind="reopened",
                reason="y",
                occurred_at=NOW - d(days=1),
            ),
        ]
    )
    booked = enq()
    cancelled_only = enq()
    for e, cancelled in ((booked, None), (cancelled_only, NOW)):
        w.db.add(
            FnEvent(
                organization_id=w.org.id,
                enquiry_id=e.id,
                contact_id=c.id,
                venue_id=w.venue.id,
                name="x",
                event_date=dt.date(2026, 12, 1),
                cancelled_at=cancelled,
            )
        )
    enq(follow_up_date=dt.date(2026, 10, 20))  # due today (Auckland 20 Oct)
    enq(follow_up_date=dt.date(2026, 10, 18))  # overdue
    enq(venue=None, follow_up_date=dt.date(2026, 10, 21))  # group-wide: org default tz
    fresh = enq()
    fresh.created_at = NOW - d(days=2)
    _thread(
        w,
        acct,
        fresh,
        [(NOW - d(days=2), True, {}), (NOW - d(days=2) + d(minutes=5), False, {})],
    )
    w.venue2.timezone = "Not/AZone"  # a bad zone must fall back, not break the list
    enq(venue=w.venue2.id, follow_up_date=dt.date(2026, 10, 20))
    w.db.flush()
    return made


class TestStatusTwins:
    @pytest.mark.parametrize(
        "now",
        [
            NOW,
            NOW + dt.timedelta(hours=11),  # crosses Auckland midnight
            NOW + dt.timedelta(days=6, hours=3),
        ],
    )
    @pytest.mark.parametrize("prevalidated", [False, True])
    def test_enquiry_sql_matches_python(self, w, now, prevalidated):
        made = _build_enquiry_fixtures(w)
        valid = status.valid_timezones(w.db, w.org.id) if prevalidated else None
        assert valid is None or valid == ("Pacific/Auckland",)
        sql = {
            r.enquiry_id: r
            for r in w.db.execute(
                status.enquiry_status_select(w.org.id, now, valid_tz=valid)
            )
        }
        facts = status.load_enquiry_facts(w.db, w.org.id, [e.id for e in made])
        assert set(sql) == set(facts)
        for eid, f in facts.items():
            stage = status.enquiry_stage(f)
            action = status.next_action(f, now)
            row = sql[eid]
            assert (row.stage, row.next_action) == (stage, action.kind), eid
            assert row.next_action_due == action.due_date, eid
            assert bool(row.next_action_overdue) == action.overdue, eid
            assert bool(row.next_action_due_today) == action.due_today, eid
            assert row.tz == f.tz

    def test_list_shapes_agree(self, w):
        """The default list (page first, then that page's status) and the
        derived-filter list (every enquiry's status in SQL) return the same
        rows with the same derived values."""
        _build_enquiry_fixtures(w)
        ctx = w.ctx(w.owner)
        for sort in ("created_desc", "preferred_date"):
            for offset in (0, 5):
                paged = enquiries.list_enquiries(
                    w.db, ctx, now=NOW, sort=sort, limit=7, offset=offset
                )
                derived = enquiries.list_enquiries(
                    w.db,
                    ctx,
                    now=NOW,
                    sort=sort,
                    limit=7,
                    offset=offset,
                    stages=list(status.STAGES),
                )
                assert [vars(r) for r in paged] == [vars(r) for r in derived]
                assert paged

    def test_the_fixture_set_covers_every_stage_and_action(self, w):
        made = _build_enquiry_fixtures(w)
        facts = status.load_enquiry_facts(w.db, w.org.id, [e.id for e in made])
        stages = {status.enquiry_stage(f) for f in facts.values()}
        kinds = {status.next_action(f, NOW).kind for f in facts.values()}
        assert stages == set(status.STAGES)
        assert kinds == set(status.NEXT_ACTION_KINDS)

    def test_event_sql_matches_python(self, w):
        c = add_contact(w.db, w.org.id)
        d = dt.timedelta
        made = []

        def ev(**kw):
            e = FnEvent(
                organization_id=w.org.id,
                contact_id=c.id,
                venue_id=w.venue.id,
                name="x",
                event_date=dt.date(2026, 12, 1),
                **kw,
            )
            w.db.add(e)
            w.db.flush()
            made.append(e)
            return e

        def sec(e, section, kind, at):
            w.db.add(
                FnEventSectionConfirmation(
                    organization_id=w.org.id,
                    event_id=e.id,
                    section=section,
                    kind=kind,
                    actor_kind="client",
                    occurred_at=at,
                )
            )

        def change(e, kind, actor, at):
            w.db.add(
                FnEventChange(
                    organization_id=w.org.id,
                    event_id=e.id,
                    kind=kind,
                    actor_kind=actor,
                    occurred_at=at,
                )
            )

        # Each case pins the status Orbit stored (F2's parity run checks the
        # real data): only a CLIENT EDIT makes an event in progress.
        expect = {}

        def case(want, edited=False, **kw):
            e = ev(**kw)
            expect[e.id] = (want, edited)
            return e

        case("booked")
        change(case("sent_to_client"), "link_sent", "staff", NOW)
        change(case("booked"), "edited", "staff", NOW)  # staff edits don't count
        e = case("in_progress")  # the client edited
        change(e, "link_sent", "staff", NOW - d(days=2))
        change(e, "edited", "client", NOW - d(days=1))
        e = case("in_progress")  # a client edit with no link logged (imported)
        change(e, "edited", "client", NOW - d(days=1))
        sec(e, "details", "confirmed", NOW)
        e = case("booked")  # a section confirmed alone is not progress
        sec(e, "food", "confirmed", NOW)
        e = case("sent_to_client")  # link + two sections done, no edit
        change(e, "link_sent", "staff", NOW - d(days=1))
        sec(e, "details", "confirmed", NOW)
        sec(e, "food", "skipped", NOW)
        e = case("confirmed")  # all three, one of them skipped
        for i, s in enumerate(("details", "beverages")):
            sec(e, s, "confirmed", NOW - d(hours=5 - i))
        sec(e, "food", "skipped", NOW - d(hours=1))
        e = case("confirmed", edited=True)  # confirmed, then edited by the client
        for s in ("details", "beverages", "food"):
            sec(e, s, "confirmed", NOW - d(days=2))
        change(e, "edited", "client", NOW - d(days=1))
        e = case("confirmed")  # edited then re-confirmed -> flag clears
        for s in ("details", "beverages", "food"):
            sec(e, s, "confirmed", NOW - d(days=2))
        change(e, "edited", "client", NOW - d(days=1))
        sec(e, "food", "confirmed", NOW)
        e = case("sent_to_client")  # a reopened section, no edit
        change(e, "link_sent", "staff", NOW - d(days=3))
        for s in ("details", "beverages", "food"):
            sec(e, s, "confirmed", NOW - d(days=2))
        sec(e, "beverages", "reopened", NOW - d(days=1))
        e = case("cancelled", cancelled_at=NOW)  # cancelled beats confirmed
        for s in ("details", "beverages", "food"):
            sec(e, s, "confirmed", NOW - d(days=2))
        w.db.flush()

        sql = {
            r.event_id: r for r in w.db.execute(status.event_status_select(w.org.id))
        }
        facts = status.load_event_facts(w.db, w.org.id, [e.id for e in made])
        assert set(facts) == set(expect)
        for eid, (want, edited) in expect.items():
            st = status.event_status(facts[eid])
            assert (st.status, st.edited_since_confirmed) == (want, edited), eid
            assert sql[eid].status == want, eid
            assert bool(sql[eid].edited_since_confirmed) == edited, eid
            assert sql[eid].confirmed_at == st.confirmed_at, eid
        assert {w_ for w_, _ in expect.values()} == set(status.EVENT_STATUSES)

    def test_min_spend_sql_matches_python(self, w):
        c = add_contact(w.db, w.org.id)
        e = add_enquiry(w.db, w.org.id, c.id, w.venue.id)
        d = dt.timedelta
        made = []

        def ms(area=None, **kw):
            m = FnMinSpend(
                organization_id=w.org.id,
                enquiry_id=e.id,
                venue_id=w.venue.id,
                area_id=area,
                amount_cents=100_000,
                **kw,
            )
            w.db.add(m)
            w.db.flush()
            made.append(m)
            return m

        def ap(m, kind, at, **terms):
            w.db.add(
                FnMinSpendApproval(
                    organization_id=w.org.id,
                    min_spend_id=m.id,
                    kind=kind,
                    occurred_at=at,
                    **terms,
                )
            )

        def rs(m, kind, at):
            w.db.add(
                FnMinSpendResponse(
                    organization_id=w.org.id,
                    min_spend_id=m.id,
                    kind=kind,
                    channel="email",
                    occurred_at=at,
                )
            )

        ms(w.deck.id)  # proposed, needs approval
        ms(w.snug.id)  # proposed, confirmable (no approval needed)
        ms()  # no area -> needs approval
        ap(ms(w.deck.id), "approve", NOW)  # approved
        m = ms(w.deck.id)  # override
        ap(m, "override", NOW)
        m = ms(w.deck.id)  # approved then revoked
        ap(m, "approve", NOW - d(hours=2))
        ap(m, "revoke", NOW - d(hours=1))
        m = ms(w.deck.id)  # declined after approval
        ap(m, "approve", NOW - d(hours=2))
        rs(m, "decline", NOW)
        ms(w.deck.id, withdrawn_at=NOW)  # withdrawn
        # Coverage: an approval counts only for the terms it was given on.
        expect = {}
        m = ms(
            w.deck.id,
            event_date=dt.date(2026, 12, 4),
            start_time=dt.time(19, 0),
            end_time=dt.time(1, 0),
            ends_next_day=True,
        )
        ap(m, "approve", NOW, terms_key=status.terms_key_of(m))
        expect[m.id] = ("approved", True)
        m = ms(w.deck.id, event_date=dt.date(2026, 12, 4))  # terms moved since
        ap(m, "approve", NOW, terms_key=status.terms_key_of(m))
        m.event_date = dt.date(2026, 12, 5)
        expect[m.id] = ("proposed", False)
        m = ms(w.deck.id)  # imported: no key, amount matches
        ap(m, "approve", NOW, amount_cents=100_000)
        expect[m.id] = ("approved", True)
        m = ms(w.deck.id)  # imported: amount changed since
        ap(m, "approve", NOW, amount_cents=90_000)
        expect[m.id] = ("proposed", False)
        m = ms(w.snug.id)  # stale approval, but the area needs none
        ap(m, "approve", NOW, amount_cents=1)
        expect[m.id] = ("proposed", True)
        ev = FnEvent(
            organization_id=w.org.id,
            enquiry_id=e.id,
            contact_id=c.id,
            venue_id=w.venue.id,
            name="x",
            event_date=dt.date(2026, 12, 1),
        )
        w.db.add(ev)
        w.db.flush()
        m = ms(w.deck.id, event_id=ev.id)  # confirmed
        ap(m, "approve", NOW - d(hours=3))
        rs(m, "confirm", NOW - d(hours=1))
        w.db.flush()

        sql = {
            r.min_spend_id: r
            for r in w.db.execute(status.min_spend_status_select(w.org.id))
        }
        facts = status.load_min_spend_facts(w.db, w.org.id, [m.id for m in made])
        states = set()
        for mid, f in facts.items():
            assert sql[mid].state == status.min_spend_state(f), mid
            assert bool(sql[mid].confirmable) == status.min_spend_confirmable(f), mid
            assert bool(sql[mid].approval_covers) == f.approval_covers, mid
            states.add(sql[mid].state)
        assert states == set(status.MIN_SPEND_STATES)
        for mid, (state, confirmable) in expect.items():
            assert (sql[mid].state, bool(sql[mid].confirmable)) == (state, confirmable)
