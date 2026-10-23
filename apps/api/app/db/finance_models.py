"""Norm Finance — the ``fin_*`` tables (plan: docs/plans/finance-migration.md §6.3),
plus the two things every finance module shares: the job claim table and
the parity engine's record.

The real asset is the locked daily sales budget with its event notes
(``fin_budget_versions`` + ``fin_budget_days``): the current budget is the
latest locked version, and Loaded's own daily budget is read live
(``get_budgets``) and logged on change (``fin_loaded_budget_log``) so "the
budget as it stood 28 days before" is recoverable. Monthly line items
(``fin_budget_lines``) hold one value each — cents or a ratio, never both.
Forecasting stores a vintage per night (``fin_forecast_vintages`` +
``_days``), the drivers and overrides a person adds, and the scores of each
vintage against actuals and the Loaded benchmark. ``fin_actuals_daily`` is
a cache for scoring, never the record.

``fin_job_runs`` is the ONE claim table for every tick job in the three
modules (DESIGN-CONTRACT §6): a row per (org, job, scope, slot), inserted
before the work, keyed as ``app_schedule_runs`` is; a failed run keeps its
error on the row for the weekly parity email. ``fin_parity_runs`` /
``fin_parity_items`` are the parity record only — never a claim row.

Conventions and ON DELETE are ``app/db/_native_rows.py``'s.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)

from app.db._native_rows import (
    NativeRow,
    _check,
    _Ref,
    _tenancy,
    _user_fk,
    _Versioned,
)
from app.db.models import Base, _now

# ---------------------------------------------------------------------------
# Vocabularies. Enforced by CHECK constraints below.
# ---------------------------------------------------------------------------

#: fin_budget_versions.source.
BUDGET_SOURCES = ("import", "manual", "forecast")
#: fin_budget_lines.line_item — the monthly line items kept (D33 drops the rest).
BUDGET_LINE_ITEMS = (
    "wage_pct",
    "wage_cents",
    "cogs_pct",
    "food_cogs_pct",
    "bev_cogs_pct",
    "leave_cents",
    "opex_cents",
    "depreciation_cents",
    "rebates_pct",
)
#: fin_forecast_drivers.kind.
DRIVER_KINDS = ("holiday", "event", "function", "manual")
#: fin_budget_pushes.source — what asked for the push.
BUDGET_PUSH_SOURCES = ("manual", "chat", "forecast", "restore")
#: fin_budget_pushes.method — Loaded's verb (PUT updates a day, POST creates one).
BUDGET_PUSH_METHODS = ("PUT", "POST")
#: fin_parity_runs.module.
PARITY_MODULES = ("payroll", "journal", "entertainers", "summary", "inputs")
#: fin_parity_runs.trigger.
PARITY_TRIGGERS = ("weekly", "manual")
#: fin_parity_items.class.
PARITY_CLASSES = ("expected", "unexplained")


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


class FinBudgetVersion(NativeRow, Base):
    """One version of a venue's daily budget for a fiscal year. "Locked at
    the start of the year" is a mechanism here (``locked_at``): the current
    budget is the latest locked version; an unlocked one is a draft."""

    __tablename__ = "fin_budget_versions"
    __table_args__ = _tenancy(
        "fin_budget_versions",
        _Ref("venue_id", "venues", "NO ACTION"),
        extra=(_check("fin_budget_versions", "source", BUDGET_SOURCES),),
    )

    venue_id = Column(String, nullable=False)
    fiscal_year = Column(Integer, nullable=False)
    label = Column(String, nullable=True)
    source = Column(String, nullable=False)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    locked_by = _user_fk("fin_budget_versions", "locked_by")
    created_by = _user_fk("fin_budget_versions", "created_by")
    notes = Column(Text, nullable=True)


class FinBudgetDay(NativeRow, Base):
    """A day's budget in a version — GST-inclusive sales, and the event note
    that explains it."""

    __tablename__ = "fin_budget_days"
    __table_args__ = _tenancy(
        "fin_budget_days",
        _Ref("version_id", "fin_budget_versions", "CASCADE", index=False),
        extra=(UniqueConstraint("version_id", "date", name="uq_fin_budget_days_date"),),
    )

    version_id = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    amount_cents = Column(Integer, nullable=False)
    note = Column(Text, nullable=True)


class FinBudgetLine(NativeRow, _Versioned, Base):
    """A monthly line item: exactly one of ``value_cents`` (a dollar line) or
    ``value_ratio`` (a percentage as a decimal, 0.32 = 32%) is set."""

    __tablename__ = "fin_budget_lines"
    __table_args__ = _tenancy(
        "fin_budget_lines",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        extra=(
            UniqueConstraint(
                "venue_id",
                "fiscal_year",
                "month",
                "line_item",
                name="uq_fin_budget_lines_item",
            ),
            _check("fin_budget_lines", "line_item", BUDGET_LINE_ITEMS),
            CheckConstraint("month BETWEEN 1 AND 12", name="ck_fin_budget_lines_month"),
            CheckConstraint(
                "(value_cents IS NULL) <> (value_ratio IS NULL)",
                name="ck_fin_budget_lines_one_value",
            ),
        ),
    )

    venue_id = Column(String, nullable=False)
    fiscal_year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    line_item = Column(String, nullable=False)
    value_cents = Column(Integer, nullable=True)
    value_ratio = Column(Numeric(10, 6), nullable=True)


class FinVenueSettings(NativeRow, _Versioned, Base):
    """Per-venue forecasting settings: the holiday region, the weekdays the
    venue is closed, and whether the level correction runs."""

    __tablename__ = "fin_venue_settings"
    __table_args__ = _tenancy(
        "fin_venue_settings",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        extra=(UniqueConstraint("venue_id", name="uq_fin_venue_settings_venue"),),
    )

    venue_id = Column(String, nullable=False)
    holiday_region = Column(String, nullable=True)
    #: ISO weekdays (1 = Monday … 7 = Sunday) the venue does not trade.
    closed_weekdays = Column(JSON, nullable=False, default=list)
    #: Plan §8: the damped level correction is a per-venue switch, OFF by
    #: default (it did not help overall on Orbit's data — 05 §6.3), scored
    #: on its own when a venue turns it on.
    level_correction_on = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class FinHoliday(NativeRow, Base):
    """A public holiday in a region, seeded by the holidays service."""

    __tablename__ = "fin_holidays"
    __table_args__ = _tenancy(
        "fin_holidays",
        extra=(
            UniqueConstraint(
                "organization_id",
                "region",
                "date",
                "name",
                name="uq_fin_holidays_region_date",
            ),
        ),
    )

    region = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    name = Column(String, nullable=False)
    source = Column(String, nullable=True)


# ---------------------------------------------------------------------------
# Actuals and forecasting
# ---------------------------------------------------------------------------


class FinActualsDaily(NativeRow, Base):
    """A day's sales as read from Loaded — a cache for scoring, refreshed by
    the nightly job; never the record (``get_sales`` is)."""

    __tablename__ = "fin_actuals_daily"
    __table_args__ = _tenancy(
        "fin_actuals_daily",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        extra=(UniqueConstraint("venue_id", "date", name="uq_fin_actuals_daily_date"),),
    )

    venue_id = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    sales_incl_cents = Column(Integer, nullable=False)
    sales_ex_cents = Column(Integer, nullable=False)
    orders = Column(Integer, nullable=True)
    fetched_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )


class FinForecastVintage(NativeRow, Base):
    """One run of the forecast for a venue, as of a date, over a horizon —
    kept so every vintage can be scored against what then happened."""

    __tablename__ = "fin_forecast_vintages"
    __table_args__ = _tenancy(
        "fin_forecast_vintages",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        extra=(
            Index(
                "ix_fin_forecast_vintages_venue_as_of",
                "organization_id",
                "venue_id",
                "as_of",
            ),
        ),
    )

    venue_id = Column(String, nullable=False)
    as_of = Column(Date, nullable=False)
    horizon_days = Column(Integer, nullable=False)
    method = Column(String, nullable=False)
    params = Column(JSON, nullable=True)


class FinForecastDay(NativeRow, Base):
    """A day in a vintage: the baseline, the uplift from drivers, the
    forecast, and the components the engine added up."""

    __tablename__ = "fin_forecast_days"
    __table_args__ = _tenancy(
        "fin_forecast_days",
        _Ref("vintage_id", "fin_forecast_vintages", "CASCADE", index=False),
        extra=(
            UniqueConstraint("vintage_id", "date", name="uq_fin_forecast_days_date"),
        ),
    )

    vintage_id = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    baseline_cents = Column(Integer, nullable=False)
    uplift_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    forecast_cents = Column(Integer, nullable=False)
    components = Column(JSON, nullable=True)


class FinForecastDriver(NativeRow, Base):
    """Something that moves a day's sales: a holiday, an event, a function,
    or a person's manual factor — as an amount or a percentage (basis
    points), exactly one of the two. Retired by ``archived_at``."""

    __tablename__ = "fin_forecast_drivers"
    __table_args__ = _tenancy(
        "fin_forecast_drivers",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        extra=(
            _check("fin_forecast_drivers", "kind", DRIVER_KINDS),
            CheckConstraint(
                "(uplift_cents IS NULL) <> (uplift_bp IS NULL)",
                name="ck_fin_forecast_drivers_one_uplift",
            ),
            Index(
                "ix_fin_forecast_drivers_venue_date",
                "organization_id",
                "venue_id",
                "date",
            ),
        ),
    )

    venue_id = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    kind = Column(String, nullable=False)
    label = Column(String, nullable=False)
    uplift_cents = Column(Integer, nullable=True)
    uplift_bp = Column(Integer, nullable=True)
    created_by = _user_fk("fin_forecast_drivers", "created_by")
    archived_at = Column(DateTime(timezone=True), nullable=True)


class FinForecastOverride(NativeRow, Base):
    """A person's figure for a day, replacing the forecast until superseded."""

    __tablename__ = "fin_forecast_overrides"
    __table_args__ = _tenancy(
        "fin_forecast_overrides",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        extra=(
            Index(
                "ix_fin_forecast_overrides_venue_date",
                "organization_id",
                "venue_id",
                "date",
            ),
        ),
    )

    venue_id = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    amount_cents = Column(Integer, nullable=False)
    reason = Column(Text, nullable=True)
    created_by = _user_fk("fin_forecast_overrides", "created_by")
    superseded_at = Column(DateTime(timezone=True), nullable=True)


class FinLoadedBudgetLog(NativeRow, Base):
    """Loaded's daily budget as it stood on ``captured_on`` — appended only
    when it changed, so the benchmark for a day is the row captured 28 days
    before it."""

    __tablename__ = "fin_loaded_budget_log"
    __table_args__ = _tenancy(
        "fin_loaded_budget_log",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        extra=(
            UniqueConstraint(
                "venue_id",
                "date",
                "captured_on",
                name="uq_fin_loaded_budget_log_capture",
            ),
        ),
    )

    venue_id = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    amount_cents = Column(Integer, nullable=False)
    loaded_id = Column(String, nullable=True)
    captured_on = Column(Date, nullable=False)


class FinForecastScore(NativeRow, Base):
    """How a vintage (or the Loaded benchmark) did on one day once the
    actual was known: the absolute percentage error and the signed error.
    One of ``vintage_id`` / ``benchmark`` names what was scored."""

    __tablename__ = "fin_forecast_scores"
    __table_args__ = _tenancy(
        "fin_forecast_scores",
        _Ref("vintage_id", "fin_forecast_vintages", "CASCADE", index=False),
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        extra=(
            CheckConstraint(
                "vintage_id IS NOT NULL OR benchmark IS NOT NULL",
                name="ck_fin_forecast_scores_subject",
            ),
            Index(
                "uq_fin_forecast_scores_subject_date",
                "venue_id",
                "vintage_id",
                "benchmark",
                "date",
                unique=True,
                postgresql_nulls_not_distinct=True,
            ),
        ),
    )

    vintage_id = Column(String, nullable=True)
    #: A named benchmark scored beside the vintages (``loaded_budget``).
    benchmark = Column(String, nullable=True)
    venue_id = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    actual_cents = Column(Integer, nullable=False)
    forecast_cents = Column(Integer, nullable=False)
    ape = Column(Numeric(10, 4), nullable=True)
    signed_error_cents = Column(Integer, nullable=False)


class FinBudgetPush(NativeRow, Base):
    """Every write of a day's budget into Loaded: before and after, the
    verb, what asked for it, who pressed, and Loaded's answer."""

    __tablename__ = "fin_budget_pushes"
    __table_args__ = _tenancy(
        "fin_budget_pushes",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        extra=(
            _check("fin_budget_pushes", "method", BUDGET_PUSH_METHODS),
            _check("fin_budget_pushes", "source", BUDGET_PUSH_SOURCES),
            Index(
                "ix_fin_budget_pushes_venue_date",
                "organization_id",
                "venue_id",
                "date",
            ),
        ),
    )

    venue_id = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    before_cents = Column(Integer, nullable=True)
    after_cents = Column(Integer, nullable=False)
    loaded_id = Column(String, nullable=True)
    method = Column(String, nullable=False)
    source = Column(String, nullable=False)
    pushed_by = _user_fk("fin_budget_pushes", "pushed_by")
    pushed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )
    ok = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    result = Column(JSON, nullable=True)


# ---------------------------------------------------------------------------
# Shared: jobs and parity
# ---------------------------------------------------------------------------


class FinJobRun(NativeRow, Base):
    """One claimed run of a tick job for one organisation, scope and slot
    (``services/finance_core/jobs.py``). The unique row IS the once-per-slot
    guard; a run ``started_at`` over thirty minutes ago with no
    ``finished_at`` is stale and may be reclaimed. ``error`` keeps a
    failure for the parity email."""

    __tablename__ = "fin_job_runs"
    __table_args__ = _tenancy(
        "fin_job_runs",
        extra=(
            UniqueConstraint(
                "organization_id",
                "job",
                "scope_id",
                "slot",
                name="uq_fin_job_runs_slot",
            ),
            Index("ix_fin_job_runs_started", "organization_id", "started_at"),
        ),
    )

    #: The job's name (DESIGN-CONTRACT §6: ``payroll.snapshot``, ``ent.morning``, …).
    job = Column(String, nullable=False)
    #: What the run is for — a venue or entity id, or ``-`` for the organisation.
    scope_id = Column(String, nullable=False)
    #: The period the run covers — a date, a week start, a month.
    slot = Column(String, nullable=False)
    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(Text, nullable=True)


class FinParityRun(NativeRow, Base):
    """One comparison of Norm's figures against Orbit's for a module and a
    period (plan §14), weekly from the tick or on demand from the Shadow tab."""

    __tablename__ = "fin_parity_runs"
    __table_args__ = _tenancy(
        "fin_parity_runs",
        extra=(
            _check("fin_parity_runs", "module", PARITY_MODULES),
            _check("fin_parity_runs", "trigger", PARITY_TRIGGERS),
            Index("ix_fin_parity_runs_module", "organization_id", "module", "ran_at"),
        ),
    )

    module = Column(String, nullable=False)
    #: The period compared — a week start, a date, a month — as text.
    period = Column(String, nullable=False)
    ran_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )
    trigger = Column(String, nullable=False)
    totals = Column(JSON, nullable=True)
    ran_by = _user_fk("fin_parity_runs", "ran_by")


class FinParityItem(NativeRow, Base):
    """One difference found by a run. ``class`` is ``expected`` (a named
    class of known difference, ``parity.EXPECTED_CLASSES``) or
    ``unexplained``; the payroll person signs an item off."""

    __tablename__ = "fin_parity_items"
    __table_args__ = _tenancy(
        "fin_parity_items",
        _Ref("run_id", "fin_parity_runs", "CASCADE"),
        extra=(_check("fin_parity_items", "class", PARITY_CLASSES),),
    )

    run_id = Column(String, nullable=False)
    #: A venue id, or a label when the difference is not a venue's.
    entity = Column(String, nullable=False)
    key = Column(String, nullable=False)
    norm_value = Column(Text, nullable=True)
    orbit_value = Column(Text, nullable=True)
    #: Python keeps ``class`` for itself; the column is still named ``class``.
    class_ = Column("class", String, nullable=False)
    explanation = Column(Text, nullable=True)
    signed_off_by = _user_fk("fin_parity_items", "signed_off_by")
    signed_off_at = Column(DateTime(timezone=True), nullable=True)


#: fin_access_log.subject_kind — whose sensitive field was read.
ACCESS_SUBJECT_KINDS = ("performer", "staff_member")


class FinAccessLog(NativeRow, Base):
    """One read of a bank account, IRD number or date of birth (plan §6.5
    item 3; ``services/finance_core/access_log.py``): who, when, which
    performer or Loaded staff member, which field, from which route. Never
    the value. ``user_id`` is NULL for a job, which names itself in
    ``actor_label``."""

    __tablename__ = "fin_access_log"
    __table_args__ = _tenancy(
        "fin_access_log",
        extra=(
            _check("fin_access_log", "subject_kind", ACCESS_SUBJECT_KINDS),
            Index(
                "ix_fin_access_log_subject",
                "organization_id",
                "subject_kind",
                "subject_id",
            ),
        ),
    )

    subject_kind = Column(String, nullable=False)
    #: An ``ent_performers`` id or a Loaded staff member id (text, not a key:
    #: the log outlives the row it names).
    subject_id = Column(String, nullable=False)
    field = Column(String, nullable=False)
    user_id = _user_fk("fin_access_log", "user_id")
    actor_label = Column(String, nullable=True)
    route = Column(String, nullable=False)
    at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )


FINANCE_MODELS = tuple(
    sorted(
        (
            m
            for m in list(globals().values())
            if isinstance(m, type)
            and issubclass(m, Base)
            and getattr(m, "__tablename__", "").startswith("fin_")
        ),
        key=lambda m: m.__tablename__,
    )
)
FINANCE_TABLES = frozenset(m.__tablename__ for m in FINANCE_MODELS)
