"""The Finance foundation: schema tenancy across the pay_*, ent_* and fin_*
tables, the ctx (venues, scopes, modes), the job claim table, the parity
record, the catalog's mode rule, and the migration chain.

Built on NON-admin users (tests/org_helpers.py): a platform admin bypasses
every org rule, so a test that used one would prove nothing about them.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import re
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.exc import IntegrityError

from app.db import entertainers_models as em
from app.db import finance_models as fm
from app.db import payroll_models as pm
from app.db.entertainers_models import EntPerformer, EntPublicToken
from app.db.finance_models import FinJobRun, FinParityItem
from app.db.models import Organization, OrganizationMembership, Role, Venue
from app.db.payroll_models import PayRule, PayWeek, PayXeroPush
from app.services.finance_core import (
    Forbidden,
    Invalid,
    NotFound,
    NotLive,
    catalog,
    jobs,
    modes,
    parity,
    tick,
)
from app.services.finance_core.ctx import (
    FINANCE_SCOPES,
    build_ctx,
    for_org,
    for_performer,
    require_venue,
)
from tests.org_helpers import grant_venue, make_org, make_user, make_venue

UTC = dt.timezone.utc
MONDAY = dt.date(2026, 11, 16)

ALL_MODELS = pm.PAYROLL_MODELS + em.ENTERTAINERS_MODELS + fm.FINANCE_MODELS
ALL_TABLES = pm.PAYROLL_TABLES | em.ENTERTAINERS_TABLES | fm.FINANCE_TABLES
#: Tables a plain (non-composite) FK may point at: global rows.
_PLAIN_FK_TARGETS = {"organizations", "users", "email_logs"}


# ===========================================================================
# World
# ===========================================================================


def _role(db, org, name, permissions):
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name=name,
        display_name=name,
        permissions=list(permissions),
    )
    db.add(role)
    db.flush()
    return role


def _member(db, org, *, role=None, legacy="member", venues=()):
    """A non-admin user in ``org`` with a role row and venue rows."""
    user = make_user(db)
    db.add(
        OrganizationMembership(
            id=str(uuid.uuid4()),
            user_id=user.id,
            organization_id=org.id,
            role=legacy,
            role_id=role.id if role else None,
        )
    )
    db.flush()
    for v in venues:
        grant_venue(db, user, v)
    return user


def make_world(db, *, payroll="live", entertainers="live", finance="live"):
    org = make_org(db, "Cook Bros")
    for module, value in (
        ("payroll", payroll),
        ("entertainers", entertainers),
        ("finance", finance),
    ):
        modes.set_mode(db, org.id, module, value)
    venue = make_venue(db, "Brew Bar", org)
    venue2 = make_venue(db, "Garden Bar", org)
    # CBI: head office runs a pay run but never trades.
    cbi = make_venue(db, "Head Office", org)
    cbi.is_trading = False
    db.flush()

    payroll_role = _role(db, org, "payroll", ["payroll:read", "payroll:write"])
    finance_role = _role(db, org, "finance", ["finance:read", "budgets:write"])
    team_role = _role(db, org, "team", ["tasks:read"])

    payroll_person = _member(db, org, role=payroll_role, venues=[venue])
    finance_person = _member(db, org, role=finance_role, venues=[venue, venue2])
    team_member = _member(db, org, role=team_role, venues=[venue])
    nobody = _member(db, org, role=team_role)  # no venue rows at all
    owner = _member(db, org, legacy="owner")

    other_org = make_org(db, "Rival Co")
    other_venue = make_venue(db, "Rival Bar", other_org)
    other_user = _member(db, other_org, legacy="owner")
    db.flush()
    w = SimpleNamespace(
        db=db,
        org=org,
        venue=venue,
        venue2=venue2,
        cbi=cbi,
        payroll_person=payroll_person,
        finance_person=finance_person,
        team_member=team_member,
        nobody=nobody,
        owner=owner,
        other_org=other_org,
        other_venue=other_venue,
        other_user=other_user,
    )
    w.ctx = lambda user=None, **kw: build_ctx(db, user or payroll_person, **kw)
    return w


@pytest.fixture
def w(db_session):
    return make_world(db_session)


def _week(w, venue=None, start=MONDAY):
    week = PayWeek(
        organization_id=w.org.id, venue_id=(venue or w.venue).id, week_start=start
    )
    w.db.add(week)
    w.db.flush()
    return week


# ===========================================================================
# Schema
# ===========================================================================


class TestTenancyMetadata:
    """Every finance table carries the tenancy rules in the schema itself."""

    def test_every_table_is_org_scoped_with_composite_references(self):
        problems = []
        for model in ALL_MODELS:
            t = model.__table__
            org = t.c.get("organization_id")
            # pay_rules is the one exception: NULL org = a platform default.
            if org is None or (org.nullable and t.name != "pay_rules"):
                problems.append(f"{t.name}: organization_id missing or nullable")
            if t.name != "pay_rules" and not any(
                isinstance(c, UniqueConstraint)
                and [col.name for col in c.columns] == ["organization_id", "id"]
                for c in t.constraints
            ):
                problems.append(f"{t.name}: no UNIQUE (organization_id, id)")
            for fk in (c for c in t.constraints if isinstance(c, ForeignKeyConstraint)):
                target = fk.referred_table.name
                local = [c.name for c in fk.columns]
                remote = [e.column.name for e in fk.elements]
                if target in ALL_TABLES or target == "venues":
                    if local[0] != "organization_id" or remote[0] != "organization_id":
                        problems.append(
                            f"{t.name}.{local}: reference to {target} not org-composite"
                        )
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
        for model in ALL_MODELS:
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

    def test_every_vocabulary_is_a_check(self):
        """Each module's vocabulary tuples are enforced by a CHECK named
        ck_<table>_<column> — the pattern the services rely on."""
        expected = {
            ("pay_snapshots", "source", pm.SNAPSHOT_SOURCES),
            ("pay_shift_facts", "cost_group", pm.COST_GROUPS),
            ("pay_leave_payouts", "workflow_kind", pm.LEAVE_WORKFLOW_KINDS),
            ("pay_leave_payouts", "source", pm.PAYOUT_SOURCES),
            ("pay_exports", "kind", pm.EXPORT_KINDS),
            ("pay_adjustments", "report_target", pm.REPORT_TARGETS),
            ("pay_journal_drafts", "kind", pm.JOURNAL_DRAFT_KINDS),
            ("pay_xero_pushes", "kind", pm.PUSH_KINDS),
            ("pay_files", "kind", pm.PAY_FILE_KINDS),
            ("pay_entities", "ico_leave_tax", pm.ICO_LEAVE_TAX_RULES),
            ("ent_performers", "lane", em.LANES),
            ("ent_performers", "performer_type", em.PERFORMER_TYPES),
            ("ent_performers", "band_mode", em.BAND_MODES),
            ("ent_performers", "onboarding_status", em.ONBOARDING_STATUSES),
            ("ent_performances", "performance_status", em.PERFORMANCE_STATUSES),
            ("ent_performances", "payment_status", em.PAYMENT_STATUSES),
            ("ent_performances", "closing_manager_source", em.CLOSING_MANAGER_SOURCES),
            ("ent_files", "kind", em.ENT_FILE_KINDS),
            ("ent_files", "check_status", em.CHECK_STATUSES),
            ("ent_public_tokens", "purpose", em.PUBLIC_TOKEN_PURPOSES),
            ("ent_email_links", "recipient_kind", em.EMAIL_RECIPIENT_KINDS),
            ("fin_budget_versions", "source", fm.BUDGET_SOURCES),
            ("fin_budget_lines", "line_item", fm.BUDGET_LINE_ITEMS),
            ("fin_forecast_drivers", "kind", fm.DRIVER_KINDS),
            ("fin_budget_pushes", "source", fm.BUDGET_PUSH_SOURCES),
            ("fin_parity_runs", "module", fm.PARITY_MODULES),
            ("fin_parity_runs", "trigger", fm.PARITY_TRIGGERS),
            ("fin_parity_items", "class", fm.PARITY_CLASSES),
        }
        by_table = {m.__tablename__: m.__table__ for m in ALL_MODELS}
        missing = []
        for table, column, values in expected:
            checks = {
                c.name: str(c.sqltext)
                for c in by_table[table].constraints
                if isinstance(c, CheckConstraint) and c.name
            }
            sql = checks.get(f"ck_{table}_{column}")
            if sql is None or any(f"'{v}'" not in sql for v in values):
                missing.append(f"ck_{table}_{column}")
        assert not missing, missing

    def test_named_uniques_and_partial_indexes(self):
        by_table = {m.__tablename__: m.__table__ for m in ALL_MODELS}

        def unique_cols(table, name):
            for c in by_table[table].constraints:
                if isinstance(c, UniqueConstraint) and c.name == name:
                    return [col.name for col in c.columns]
            for i in by_table[table].indexes:
                if i.name == name and i.unique:
                    return [getattr(e, "name", str(e)) for e in i.expressions]
            return None

        assert unique_cols("pay_weeks", "uq_pay_weeks_venue_week") == [
            "venue_id",
            "week_start",
        ]
        assert unique_cols(
            "pay_leave_payouts", "uq_pay_leave_payouts_request_week"
        ) == [
            "organization_id",
            "loaded_request_id",
            "week_start",
        ]
        assert unique_cols("fin_job_runs", "uq_fin_job_runs_slot") == [
            "organization_id",
            "job",
            "scope_id",
            "slot",
        ]
        assert unique_cols("fin_budget_days", "uq_fin_budget_days_date") == [
            "version_id",
            "date",
        ]
        assert unique_cols("ent_public_tokens", "uq_ent_public_tokens_hash") == [
            "token_hash"
        ]
        assert unique_cols("pay_xero_pushes", "uq_pay_xero_pushes_key") == [
            "idempotency_key"
        ]
        live = next(
            i
            for i in by_table["pay_xero_pushes"].indexes
            if i.name == "uq_pay_xero_pushes_live"
        )
        assert live.unique
        assert str(live.dialect_options["postgresql"]["where"]) == "voided_at IS NULL"
        assert live.dialect_options["postgresql"]["nulls_not_distinct"] is True

    def test_editable_rows_are_versioned(self):
        for model in (
            pm.PayEntity,
            pm.PayWeek,
            pm.PayLeavePayout,
            pm.PayAceInput,
            pm.PayAdjustment,
            em.EntPerformer,
            em.EntPerformance,
            em.EntSeries,
            fm.FinBudgetLine,
        ):
            assert model.__mapper__.version_id_col is not None, model.__name__

    def test_sensitive_columns_are_encrypted(self):
        from app.db.encrypted import EncryptedText

        cols = EntPerformer.__table__.c
        for name in ("ird_number", "bank_account", "date_of_birth"):
            assert isinstance(cols[name].type, EncryptedText), name

    def test_core_tables_gained_the_finance_columns(self):
        cols = Organization.__table__.c
        for name in ("payroll_mode", "entertainers_mode", "finance_mode"):
            assert not cols[name].nullable and cols[name].server_default is not None
        assert "is_trading" in Venue.__table__.c

    def test_migrations_create_exactly_the_model_tables(self):
        versions = pathlib.Path(__file__).parent.parent / "alembic" / "versions"
        for pattern, const, tables in (
            ("*_loaded_payroll_tables.py", "PAY_TABLES", pm.PAYROLL_TABLES),
            ("*_entertainers_tables.py", "ENT_TABLES", em.ENTERTAINERS_TABLES),
            ("*_norm_finance_tables.py", "FIN_TABLES", fm.FINANCE_TABLES),
        ):
            path = next(versions.glob(pattern))
            spec = importlib.util.spec_from_file_location(path.stem, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            listed = getattr(mod, const)
            assert set(listed) == tables, path.name
            assert len(listed) == len(set(listed)), path.name

    def test_migration_chain_has_one_head(self):
        """Every version file parsed: exactly one revision nobody revises.
        Concurrent sessions have picked the same id before (memory)."""
        versions = pathlib.Path(__file__).parent.parent / "alembic" / "versions"
        revs, downs = {}, set()
        for path in versions.glob("*.py"):
            text = path.read_text()
            # Older files annotate: revision: str = "...", down_revision:
            # Union[str, ...] = "..."
            rev = re.search(
                r'^revision(?:\s*:[^=]+)?\s*=\s*["\']([^"\']+)["\']', text, re.M
            )
            down = re.search(r"^down_revision(?:\s*:[^=]+)?\s*=\s*(.+)$", text, re.M)
            if not rev:
                continue
            assert rev.group(1) not in revs, f"duplicate revision {rev.group(1)}"
            revs[rev.group(1)] = path.name
            downs |= (
                set(re.findall(r'["\']([^"\']+)["\']', down.group(1)))
                if down
                else set()
            )
        heads = set(revs) - downs
        assert heads == {"ne9h1j3l5n7p"}, heads
        chain = {
            "lc7f9h1j3l5n": "kb6e8g0i2k4m",
            "md8g0i2k4m6o": "lc7f9h1j3l5n",
            "ne9h1j3l5n7p": "md8g0i2k4m6o",
        }
        for rev, down in chain.items():
            text = (versions / revs[rev]).read_text()
            assert f'down_revision = "{down}"' in text, rev

    def test_database_refuses_a_cross_org_reference(self, w):
        with pytest.raises(IntegrityError), w.db.begin_nested():
            w.db.add(
                PayWeek(
                    organization_id=w.org.id,
                    venue_id=w.other_venue.id,
                    week_start=MONDAY,
                )
            )
            w.db.flush()

    def test_one_pay_week_per_venue_and_start(self, w):
        _week(w)
        with pytest.raises(IntegrityError), w.db.begin_nested():
            _week(w)

    def test_one_live_push_per_week_even_with_null_counterparty(self, w):
        week = _week(w)

        def push(key, voided=None):
            return PayXeroPush(
                organization_id=w.org.id,
                week_id=week.id,
                kind="journal",
                venue_id=w.venue.id,
                week_start=MONDAY,
                xero_tenant_id="t",
                idempotency_key=key,
                voided_at=voided,
            )

        w.db.add(push("k1", voided=dt.datetime(2026, 11, 17, tzinfo=UTC)))
        w.db.add(push("k2"))
        w.db.flush()  # a voided one and a live one coexist
        with pytest.raises(IntegrityError), w.db.begin_nested():
            w.db.add(push("k3"))  # a second live journal for the week
            w.db.flush()

    def test_platform_default_rules_cannot_repeat(self, w):
        w.db.add(
            PayRule(rule="sick_day_hours", effective_from=MONDAY, params={"hours": 8})
        )
        w.db.flush()
        with pytest.raises(IntegrityError), w.db.begin_nested():
            w.db.add(PayRule(rule="sick_day_hours", effective_from=MONDAY, params={}))
            w.db.flush()
        # An organisation's own row for the same rule and date is fine.
        w.db.add(
            PayRule(
                organization_id=w.org.id,
                rule="sick_day_hours",
                effective_from=MONDAY,
                params={},
            )
        )
        w.db.flush()

    def test_a_token_names_its_target(self, w):
        with pytest.raises(IntegrityError), w.db.begin_nested():
            w.db.add(
                EntPublicToken(
                    organization_id=w.org.id,
                    token_hash="a" * 64,
                    purpose="onboarding",
                    expires_at=dt.datetime(2026, 12, 1, tzinfo=UTC),
                )
            )
            w.db.flush()


# ===========================================================================
# Ctx
# ===========================================================================


class TestCtx:
    def test_membership_gives_venues_and_the_roles_scopes(self, w):
        ctx = w.ctx()
        assert ctx.venue_ids == {w.venue.id}
        assert ctx.scopes == {"payroll:read", "payroll:write"}
        assert ctx.holds("payroll:write") and not ctx.holds("finance:read")
        assert not ctx.is_owner and ctx.actor_kind == "staff"
        fin = w.ctx(w.finance_person)
        assert fin.venue_ids == {w.venue.id, w.venue2.id}
        assert fin.scopes == {"finance:read", "budgets:write"}
        with pytest.raises(Forbidden):
            fin.require("payroll:read")

    def test_no_venue_rows_means_no_venues(self, w):
        ctx = w.ctx(w.nobody)
        assert ctx.venue_ids == frozenset()
        assert not ctx.can_see_venue(w.venue.id)
        with pytest.raises(Forbidden):
            require_venue(ctx, w.venue.id)

    def test_owner_sees_every_venue_including_non_trading(self, w):
        ctx = w.ctx(w.owner)
        assert ctx.is_owner
        assert ctx.venue_ids == {w.venue.id, w.venue2.id, w.cbi.id}
        assert ctx.can_see_venue(w.cbi.id) and ctx.can_see_venue(None)

    def test_venue_access_in_another_org_never_counts(self, w):
        grant_venue(w.db, w.payroll_person, w.other_venue)
        assert w.other_venue.id not in w.ctx().venue_ids
        with pytest.raises(Forbidden):
            require_venue(w.ctx(), w.other_venue.id)

    def test_naming_another_org_is_refused(self, w):
        with pytest.raises(Forbidden):
            w.ctx(org_id=w.other_org.id)

    def test_a_user_outside_any_org_is_refused(self, w):
        loner = make_user(w.db)
        with pytest.raises(Forbidden):
            build_ctx(w.db, loner)
        with pytest.raises(Forbidden):
            build_ctx(w.db, None)

    def test_system_ctx_has_every_venue_and_scope(self, w):
        ctx = for_org(w.db, w.org.id, actor_label="tick")
        assert ctx.actor_kind == "system" and ctx.user_id is None
        assert ctx.venue_ids == {w.venue.id, w.venue2.id, w.cbi.id}
        assert FINANCE_SCOPES <= ctx.scopes
        with pytest.raises(NotFound):
            for_org(w.db, "no-such-org")

    def test_performer_ctx_reaches_nothing_but_itself(self, w):
        p = EntPerformer(organization_id=w.org.id, name="DJ Casey")
        w.db.add(p)
        w.db.flush()
        ctx = for_performer(w.db, w.org.id, p.id)
        assert ctx.actor_kind == "performer" and ctx.performer_id == p.id
        assert ctx.venue_ids == frozenset() and ctx.scopes == frozenset()
        with pytest.raises(Forbidden):
            ctx.require("payroll:read")
        with pytest.raises(NotFound):  # another org's id never resolves
            for_performer(w.db, w.other_org.id, p.id)
        with pytest.raises(NotFound):
            for_performer(w.db, w.org.id, p.id, band_member_id="no-such-member")

    def test_modes_are_read_per_module(self, db_session):
        w = make_world(db_session, payroll="shadow", entertainers="live", finance="off")
        ctx = w.ctx()
        assert ctx.modes == {
            "payroll": "shadow",
            "entertainers": "live",
            "finance": "off",
        }
        assert ctx.is_live("entertainers") and not ctx.is_live("payroll")
        ctx.require_live("entertainers")
        with pytest.raises(NotLive, match="shadow"):
            ctx.require_live("payroll")
        with pytest.raises(NotLive, match="not switched on"):
            ctx.require_live("finance")
        with pytest.raises(Invalid):
            ctx.require_live("rostering")

    def test_set_mode_validates(self, w):
        with pytest.raises(Invalid):
            modes.set_mode(w.db, w.org.id, "payroll", "on")
        with pytest.raises(Invalid):
            modes.set_mode(w.db, w.org.id, "hr", "live")
        with pytest.raises(NotFound):
            modes.set_mode(w.db, "no-such-org", "payroll", "live")
        assert modes.mode(w.db, None, "payroll") == "off"
        assert modes.mode(w.db, "no-such-org", "finance") == "off"


# ===========================================================================
# Jobs
# ===========================================================================


class TestJobs:
    def test_claim_once_then_finish(self, w):
        now = dt.datetime(2026, 11, 16, 7, 30, tzinfo=UTC)
        run = jobs.claim(
            w.db,
            org_id=w.org.id,
            job="payroll.snapshot",
            scope_id=w.venue.id,
            slot="2026-11-16",
            now=now,
        )
        assert run is not None and run.finished_at is None
        again = jobs.claim(
            w.db,
            org_id=w.org.id,
            job="payroll.snapshot",
            scope_id=w.venue.id,
            slot="2026-11-16",
            now=now,
        )
        assert again is None
        # Another scope or slot is its own run.
        assert jobs.claim(
            w.db,
            org_id=w.org.id,
            job="payroll.snapshot",
            scope_id=w.venue2.id,
            slot="2026-11-16",
            now=now,
        )
        jobs.finish(w.db, run, "Loaded timed out")
        assert run.finished_at is not None and run.error == "Loaded timed out"
        assert jobs.failed_since(w.db, w.org.id, now - dt.timedelta(days=1)) == [run]

    def test_stale_unfinished_run_is_reclaimed(self, w):
        t0 = dt.datetime(2026, 11, 16, 7, 30, tzinfo=UTC)
        key = dict(
            org_id=w.org.id,
            job="ent.morning",
            scope_id=jobs.ORG_SCOPE,
            slot="2026-11-16",
        )
        first = jobs.claim(w.db, now=t0, **key)
        assert first is not None
        # 20 minutes on: still the first instance's run.
        assert jobs.claim(w.db, now=t0 + dt.timedelta(minutes=20), **key) is None
        # 31 minutes on with no finish: dead — the next tick takes it over.
        later = t0 + dt.timedelta(minutes=31)
        taken = jobs.claim(w.db, now=later, **key)
        assert taken is not None and taken.id == first.id and taken.started_at == later
        # A finished run is never reclaimed, however old.
        jobs.finish(w.db, taken, now=later)
        assert jobs.claim(w.db, now=later + dt.timedelta(days=2), **key) is None
        assert (
            w.db.query(FinJobRun).filter(FinJobRun.organization_id == w.org.id).count()
            == 1
        )

    def test_tick_runs_hooks_in_their_own_sessions_and_survives_a_failure(
        self, w, monkeypatch
    ):
        import sys
        import types

        calls = []

        def ok_module(name):
            m = types.ModuleType(name)

            def run_due(db, now):
                calls.append((name, now))
                return {"claimed": 1}

            m.run_due = run_due
            return m

        bad = types.ModuleType("app.services.entertainers.jobs")

        def boom(db, now):
            raise RuntimeError("no closing manager")

        bad.run_due = boom
        for name, mod in (
            ("app.services.payroll.jobs", ok_module("app.services.payroll.jobs")),
            ("app.services.entertainers.jobs", bad),
            ("app.services.finance.jobs", ok_module("app.services.finance.jobs")),
        ):
            monkeypatch.setitem(sys.modules, name, mod)
        # The fourth hook names a module that does not exist: skipped, never
        # reported (the modules land in their own waves).
        monkeypatch.setattr(
            tick,
            "HOOKS",
            (
                "app.services.payroll.jobs",
                "app.services.entertainers.jobs",
                "app.services.finance.jobs",
                "app.services.finance_core.not_built_yet",
            ),
        )
        sessions = []

        def factory():
            s = SimpleNamespace(
                commit=lambda: sessions.append("commit"),
                rollback=lambda: sessions.append("rollback"),
                close=lambda: sessions.append("close"),
            )
            return s

        now = dt.datetime(2026, 11, 16, 7, 30, tzinfo=UTC)
        out = tick.run_due_jobs(now, session_factory=factory)
        assert [c[0] for c in calls] == [
            "app.services.payroll.jobs",
            "app.services.finance.jobs",
        ]
        assert out["ran"]["app.services.payroll.jobs"] == {"claimed": 1}
        assert (
            "no closing manager"
            in out["ran"]["app.services.entertainers.jobs"]["error"]
        )
        assert "app.services.finance_core.not_built_yet" not in out["ran"]
        assert sessions == ["commit", "close", "rollback", "close", "commit", "close"]


# ===========================================================================
# Parity
# ===========================================================================


class TestParity:
    def test_record_run_classes_items_and_sign_off(self, w):
        ctx = w.ctx()
        run = parity.record_run(
            w.db,
            ctx,
            module="payroll",
            period="2026-11-16",
            trigger="manual",
            totals={"norm_cents": 100, "orbit_cents": 98},
            items=[
                {
                    "entity": w.venue.id,
                    "key": "gratuities",
                    "norm_value": 100,
                    "orbit_value": 98,
                    "code": "gratuity_residual",
                },
                {
                    "entity": w.venue.id,
                    "key": "hours",
                    "norm_value": "40.00",
                    "orbit_value": "39.50",
                },
            ],
        )
        items = parity.items_of(w.db, ctx, run.id)
        by_key = {i.key: i for i in items}
        assert by_key["gratuities"].class_ == "expected"
        assert (
            by_key["gratuities"].explanation
            == parity.EXPECTED_CLASSES["gratuity_residual"]
        )
        assert by_key["gratuities"].norm_value == "100"
        assert (
            by_key["hours"].class_ == "unexplained"
            and by_key["hours"].explanation is None
        )
        assert parity.unexplained_count(w.db, ctx, run.id) == 1
        signed = parity.sign_off(
            w.db, ctx, by_key["hours"].id, explanation="a late clock-out edit"
        )
        assert signed.signed_off_by == w.payroll_person.id and signed.signed_off_at
        assert parity.unexplained_count(w.db, ctx, run.id) == 0
        assert [r.id for r in parity.list_runs(w.db, ctx, module="payroll")] == [run.id]

    def test_scopes_and_validation(self, w):
        payroll, finance = w.ctx(), w.ctx(w.finance_person)
        with pytest.raises(Forbidden):  # finance:read cannot record payroll parity
            parity.record_run(
                w.db, finance, module="payroll", period="x", trigger="weekly", items=[]
            )
        run = parity.record_run(
            w.db,
            finance,
            module="summary",
            period="2026-11",
            trigger="weekly",
            items=[
                {"entity": "group", "key": "sales", "norm_value": 1, "orbit_value": 2}
            ],
        )
        item = w.db.query(FinParityItem).filter(FinParityItem.run_id == run.id).one()
        with pytest.raises(Forbidden):  # and payroll:write cannot sign a summary item
            parity.sign_off(w.db, payroll, item.id)
        with pytest.raises(Forbidden):  # the payroll person lacks finance:read
            parity.get_run(w.db, payroll, run.id)
        assert parity.list_runs(w.db, payroll) == []
        parity.sign_off(w.db, finance, item.id)
        with pytest.raises(Invalid):
            parity.record_run(
                w.db,
                payroll,
                module="payroll",
                period="x",
                trigger="weekly",
                items=[{"entity": "v", "key": "k", "code": "not_a_class"}],
            )
        with pytest.raises(Invalid):
            parity.record_run(
                w.db,
                payroll,
                module="payroll",
                period="x",
                trigger="weekly",
                items=[
                    {
                        "entity": "v",
                        "key": "k",
                        "class": "expected",
                    }  # expected needs a why
                ],
            )
        with pytest.raises(Invalid):
            parity.record_run(
                w.db,
                payroll,
                module="rostering",
                period="x",
                trigger="weekly",
                items=[],
            )
        with pytest.raises(Invalid):  # a job (no user) cannot sign off
            parity.sign_off(w.db, for_org(w.db, w.org.id), item.id)
        theirs = parity.record_run(
            w.db,
            for_org(w.db, w.other_org.id),
            module="payroll",
            period="x",
            trigger="weekly",
            items=[],
        )
        with pytest.raises(NotFound):
            parity.get_run(w.db, payroll, theirs.id)

    def test_expected_classes_cover_the_plan(self):
        assert {
            "loaded_attribution",
            "dropped_clockins",
            "other_team_plug",
            "budget_day_shift",
            "summed_percentages",
            "budget_as_actual",
            "gratuity_residual",
            "leave_style_default",
            "loaded_changed",
        } == set(parity.EXPECTED_CLASSES)


# ===========================================================================
# Catalog
# ===========================================================================


class TestCatalog:
    def test_entitled_after_modes_drops_the_right_slugs(self, db_session):
        both = {"loaded-payroll", "norm-finance", "xero", "finance-agent"}
        w = make_world(db_session, payroll="off", entertainers="off", finance="off")
        assert catalog.entitled_after_modes(set(both), w.org.id, w.db) == {
            "xero",
            "finance-agent",
        }
        modes.set_mode(w.db, w.org.id, "entertainers", "shadow")
        assert catalog.entitled_after_modes(set(both), w.org.id, w.db) == {
            "loaded-payroll",
            "xero",
            "finance-agent",
        }
        modes.set_mode(w.db, w.org.id, "entertainers", "off")
        modes.set_mode(w.db, w.org.id, "payroll", "live")
        modes.set_mode(w.db, w.org.id, "finance", "shadow")
        assert catalog.entitled_after_modes(set(both), w.org.id, w.db) == both
        # Nothing of ours entitled: the modes are not even read.
        assert catalog.entitled_after_modes({"xero"}, "no-such-org", w.db) == {"xero"}
        assert catalog.entitled_after_modes({"norm-finance"}, None, w.db) == set()

    def test_stand_in_rows_match_the_declaration(self):
        rows = {r.slug: r for r in catalog.stand_in_rows(catalog.CATALOG_SLUGS)}
        assert set(rows) == {"loaded-payroll", "norm-finance"}
        for slug, row in rows.items():
            app = catalog.APPS[slug]
            assert row.bundled is False and row.price_cents == 0 and row.tier == "app"
            assert row.composition["member"] == "finance"
            assert row.composition["tools"] == list(app["tools"])
            assert [c["key"] for c in row.composition["components"]] == [
                c["key"] for c in app["components"]
            ]
        assert catalog.stand_in_rows({"loaded-payroll"})[0].slug == "loaded-payroll"
        assert catalog.stand_in_rows(set()) == []

    def test_pages_tools_and_permissions_follow_the_contract(self):
        pages = {
            c["page"]["id"]: c["key"]
            for app in catalog.APPS.values()
            for c in app["components"]
            if c["page"] is not None
        }
        assert pages == {
            "payroll-payrun": "payroll_payrun",
            "payroll-leave": "payroll_leave",
            "payroll-intercompany": "payroll_intercompany",
            "payroll-reconciliation": "payroll_reconciliation",
            "payroll-settings": "payroll_settings",
            "payroll-shadow": "payroll_shadow",
            "ent-register": "entertainers_register",
            "ent-roster": "entertainers_roster",
            "ent-payments": "entertainers_payments",
            "ent-emails": "entertainers_emails",
            "finance-summary": "finance_summary",
            "finance-budgets": "finance_budgets",
            "finance-budget-vs-loaded": "finance_budget_vs_loaded",
            "finance-forecasting": "finance_forecasting",
        }
        # Register, Roster and Emails carry no permission: venue access only (D15).
        assert {"ent-register", "ent-roster", "ent-emails"}.isdisjoint(
            catalog.PAGE_PERMISSIONS
        )
        assert catalog.PAGE_PERMISSIONS["ent-payments"] == "payroll:write"
        assert set(catalog.PAGE_PERMISSIONS) <= set(pages)
        # A tool belongs to exactly one App, and the engine-only rows to none.
        claimed = [t for app in catalog.APPS.values() for t in app["tools"]]
        assert len(claimed) == len(set(claimed))
        assert set(claimed).isdisjoint(
            catalog.PAYROLL_ENGINE_ONLY + catalog.FINANCE_ENGINE_ONLY
        )
        # No App note: the catalog's note budget (1,500 chars over every App,
        # config_validator.check_app_notes) is full; the rules live on the
        # tool descriptions and in the skills. A note, if one returns, is
        # capped at 300.
        for app in catalog.APPS.values():
            assert len(app.get("note") or "") <= 300
        # get_weekly_financials' chat rendering: declared here or the validator
        # refuses the tool row (F2's build note).
        keys = [c["key"] for c in catalog.APPS["norm-finance"]["components"]]
        assert "finance_weekly" in keys

    # -- before and after the seed ------------------------------------------

    @staticmethod
    def _finance_member(db, org, *, hired=True):
        """The Finance member's catalog row as seeded (bundled=False — hired
        per organisation) and, when hired, the organisation's entitlement
        row for it. Any member row arms team gating."""
        from app.db.config_models import MarketplaceApp
        from app.db.models import OrgAppEntitlement

        db.add(
            MarketplaceApp(
                slug="finance-agent",
                name="Finance",
                description="",
                tier="agent",
                bundled=False,
                composition={"owns_agents": ["finance"]},
            )
        )
        if hired:
            db.add(
                OrgAppEntitlement(
                    organization_id=org.id, app_slug="finance-agent", enabled=True
                )
            )
        db.flush()

    def test_stand_ins_count_as_on_while_the_rows_are_missing(self, db_session):
        """No Finance App rows in the catalog yet (local testing, and the
        hours between the deploy and the catalog seed): an App whose modes
        are on is stood in and counts as on; one whose modes are off is not
        listed at all — not "available to switch on"."""
        from app.services import agent_catalog
        from app.services.entitlements import apps_on

        w = make_world(db_session, payroll="shadow", entertainers="off", finance="off")
        self._finance_member(db_session, w.org)
        agent_catalog.invalidate_cache()
        on = apps_on(w.org.id, db_session, db_session)
        assert "loaded-payroll" in on and "norm-finance" not in on

        payload = agent_catalog.team_payload(w.org.id, db_session, db_session)
        apps = {a["slug"]: a for a in payload["apps"]}
        assert apps["loaded-payroll"]["enabled"] is True
        assert apps["loaded-payroll"]["switchable"] is False  # nothing to switch yet
        assert apps["loaded-payroll"]["price_cents"] == 0
        assert [p["id"] for p in apps["loaded-payroll"]["pages"]] == [
            c["page"]["id"]
            for c in catalog.APPS["loaded-payroll"]["components"]
            if c["page"]
        ]
        assert "norm-finance" not in apps
        finance = next(m for m in payload["members"] if m["slug"] == "finance")
        assert [a["slug"] for a in finance["apps"]] == ["loaded-payroll"]

        modes.set_mode(db_session, w.org.id, "finance", "shadow")
        agent_catalog.invalidate_cache()
        assert "norm-finance" in apps_on(w.org.id, db_session, db_session)
        # the other organisation (modes off, Finance not hired) sees neither
        assert not catalog.CATALOG_SLUGS & apps_on(
            w.other_org.id, db_session, db_session
        )

    def test_a_stand_in_needs_the_finance_member_hired(self, db_session):
        from app.services import agent_catalog
        from app.services.entitlements import apps_on

        w = make_world(db_session)  # every mode live
        self._finance_member(db_session, w.org, hired=False)
        agent_catalog.invalidate_cache()
        assert not catalog.CATALOG_SLUGS & apps_on(w.org.id, db_session, db_session)

    def test_seeded_rows_follow_the_entitlement_and_the_mode(self, db_session):
        """Once the seed has written the rows, the stand-in adds nothing:
        bundled=False means the organisation switches each App on itself,
        and a module's ``off`` still wins over that switch."""
        from app.db.models import OrgAppEntitlement
        from app.services import agent_catalog
        from app.services.entitlements import apps_on

        w = make_world(db_session)
        self._finance_member(db_session, w.org)
        for row in catalog.stand_in_rows(catalog.CATALOG_SLUGS):
            db_session.add(row)
        db_session.flush()
        agent_catalog.invalidate_cache()
        assert not catalog.CATALOG_SLUGS & apps_on(w.org.id, db_session, db_session)
        payload = agent_catalog.team_payload(w.org.id, db_session, db_session)
        apps = {a["slug"]: a for a in payload["apps"]}
        assert apps["loaded-payroll"]["enabled"] is False
        assert apps["loaded-payroll"]["switchable"] is True
        assert apps["norm-finance"]["enabled"] is False

        db_session.add(
            OrgAppEntitlement(
                organization_id=w.org.id, app_slug="loaded-payroll", enabled=True
            )
        )
        db_session.flush()
        agent_catalog.invalidate_cache()
        on = apps_on(w.org.id, db_session, db_session)
        assert "loaded-payroll" in on and "norm-finance" not in on

        modes.set_mode(db_session, w.org.id, "payroll", "off")
        modes.set_mode(db_session, w.org.id, "entertainers", "off")
        agent_catalog.invalidate_cache()
        assert "loaded-payroll" not in apps_on(w.org.id, db_session, db_session)
        payload = agent_catalog.team_payload(w.org.id, db_session, db_session)
        assert "loaded-payroll" not in {a["slug"] for a in payload["apps"]}
        assert "norm-finance" in {a["slug"] for a in payload["apps"]}


class TestSyncScript:
    def test_declared_rows_match_the_catalog(self):
        """Written when no module had registered a handler yet; now every
        module has, so the pin is the other way round: each connector's
        declared rows are exactly the catalog's tools + engine-only rows for
        it (a renamed tool must change both places), and every row carries
        the fields the config validator insists on."""
        from app.services.finance_core import catalog
        from scripts import sync_norm_finance as s

        expected = {
            "norm_payroll": {
                n
                for n in catalog.PAYROLL_TOOLS + catalog.PAYROLL_ENGINE_ONLY
                if n.startswith("norm_payroll.")
            },
            "norm_finance": {
                n
                for n in catalog.FINANCE_TOOLS + catalog.FINANCE_ENGINE_ONLY
                if n.startswith("norm_finance.")
            },
        }
        for connector in s.CONNECTORS:
            rows = s.tool_rows(connector)
            assert rows, connector
            names = {f"{connector}.{r['action']}" for r in rows}
            assert names == expected[connector]
            for row in rows:
                assert row.get("field_schema") is not None, row["action"]
                assert row.get("effect"), row["action"]
                if row["effect"] == "write" and not row.get("engine_only"):
                    assert row["approval"]["label"], row["action"]

    def test_mcp_capabilities_are_the_three_reads(self):
        """Plan §12: get_payrun, get_weekly_financials and get_forecast go out
        over MCP, nothing else — and each behind a scope that requires the
        org scope the tool row itself requires."""
        from app.mcp.scopes import MCP_SCOPES
        from scripts import sync_norm_finance as s

        rows = s.mcp_rows()
        assert [(r["target"], r["action"], r["scopes"]) for r in rows] == [
            ("loadedhub", "get_payrun", ["mcp:payroll:read"]),
            ("loadedhub", "get_weekly_financials", ["mcp:finance:read"]),
            ("norm_finance", "get_forecast", ["mcp:finance:read"]),
        ]
        assert all(r["kind"] == "connector" for r in rows)
        assert MCP_SCOPES["mcp:payroll:read"].requires == {"payroll:read"}
        assert MCP_SCOPES["mcp:finance:read"].requires == {"finance:read"}
        forecast = next(
            r for r in s.tool_rows("norm_finance") if r["action"] == "get_forecast"
        )
        assert forecast["requires"] == ["finance:read"]

    def test_mcp_rows_wait_for_their_tool_rows(self, db_session):
        """The two loadedhub capabilities are created only once
        sync_payroll_config.py has written the rows (a capability pointing
        at no row is a validator error); the norm_finance one, whose row
        this script installs, goes in on the first run."""
        from app.db.config_models import ConnectionSpec, McpCapability
        from scripts import sync_norm_finance as s

        lines = s._mcp(db_session, dry_run=False)
        db_session.flush()
        assert sum("skip MCP capability loadedhub." in line for line in lines) == 2
        assert any(
            "create MCP capability connector norm_finance.get_forecast" in line
            for line in lines
        )
        assert db_session.query(McpCapability).count() == 1

        db_session.add(
            ConnectionSpec(
                connector_name="loadedhub",
                display_name="Loaded",
                auth_type="none",
                execution_mode="template",
                enabled=True,
                endpoints=[],
                tools=[
                    {"action": a, "method": "GET", "read_only": True, "effect": "read"}
                    for a in ("get_payrun", "get_weekly_financials")
                ],
            )
        )
        db_session.flush()
        lines = s._mcp(db_session, dry_run=False)
        db_session.flush()
        assert (
            sum("create MCP capability connector loadedhub." in line for line in lines)
            == 2
        )
        assert "norm_finance" not in "".join(lines)  # already there, unchanged
        assert db_session.query(McpCapability).count() == 3
        assert s._mcp(db_session, dry_run=True) == []

    def test_playbooks_are_the_apps_skills_and_name_only_real_tools(self):
        from app.services.finance_core import catalog
        from scripts import sync_norm_finance as s

        assert set(s.PLAYBOOKS) == set(catalog.PAYROLL_SKILLS + catalog.FINANCE_SKILLS)
        for pb in s.PLAYBOOKS.values():
            assert pb["display_name"] and pb["description"] and pb["instructions"]
        named = set(
            re.findall(
                r"`((?:norm_payroll|norm_finance|loadedhub|xero)\.[a-z_]+)",
                "".join(pb["instructions"] for pb in s.PLAYBOOKS.values()),
            )
        )
        # every tool a skill names is one the model can reach: claimed by the
        # two Apps or the Xero App, or a Loaded read every organisation has
        reachable = set(catalog.PAYROLL_TOOLS + catalog.FINANCE_TOOLS) | {
            "loadedhub.get_budgets",
            "loadedhub.set_budgets_typed",  # named as the page's door, not for use
            "xero.post_wage_journal",
            "xero.post_intercompany_documents",
            "xero.void_payroll_document",
        }
        assert named <= reachable, named - reachable

    def test_skills_switch_on_only_once_their_app_claims_them(self, db_session):
        """The Functions pattern: created off by the first run; the run after
        the catalog seed (which claims them) switches them on."""
        from app.db.config_models import Playbook
        from app.services.finance_core import catalog
        from scripts import sync_norm_finance as s

        lines = s._skills(db_session, dry_run=False)
        db_session.flush()
        assert len(lines) == 4 and all(
            "off until its App claims it" in line for line in lines
        )
        rows = {pb.slug: pb for pb in db_session.query(Playbook).all()}
        assert set(rows) == set(s.PLAYBOOKS)
        assert not any(pb.enabled for pb in rows.values())
        assert all(pb.agent_slug == "finance" for pb in rows.values())

        for row in catalog.stand_in_rows(catalog.CATALOG_SLUGS):
            db_session.add(row)
        db_session.flush()
        lines = s._skills(db_session, dry_run=False)
        db_session.flush()
        assert len(lines) == 4 and all("enabled; on" in line for line in lines)
        assert all(pb.enabled for pb in db_session.query(Playbook).all())
        assert s._skills(db_session, dry_run=True) == []
