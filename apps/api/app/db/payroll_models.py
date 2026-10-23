"""Loaded Payroll — the ``pay_*`` tables (plan: docs/plans/finance-migration.md §6.1).

What Norm stores for payroll is only what Loaded does not: the per-entity
settings a client token cannot read, the statutory rules as dated rows, the
week's facts frozen at snapshot time, and every decision a person makes on
top of them (leave payouts, gratuities, ACE inputs, adjustments, accruals,
journal drafts, pushes). Loaded stays the source of rostered and clocked
time; a snapshot copies it so the close, the exports and the parity engine
all work from one frozen set of shifts.

Conventions are ``app/db/_native_rows.py``'s: tenancy in the schema, integer
cents, facts as ``timestamptz``, plans as venue-local dates, Loaded and Xero
ids as text. Rows a person edits carry ``version``.

Two kinds of ``source``: where a row's figures came from (``norm`` keyed here,
``orbit`` carried by the importer, ``orbit_legacy`` for the hundred payouts
Orbit keyed by request id alone), and ``rehearsal`` — a row head office keyed
during the shadow weeks that never feeds an export or a draft.

ON DELETE follows Functions' rule: ``organization_id`` cascades; a child that
only means something inside its parent cascades (facts of a snapshot, lines
of a week); a reference between business records — a venue, a counterparty —
is ``NO ACTION``, so pay records can never vanish with a venue; a decorative
reference drops with the column-list form ``SET NULL (column)``.

Like ``functions_models``, this module is not re-exported from
``app.db.models``. It is imported wherever ``Base.metadata`` must be complete
(``app.services.finance_core``, ``alembic/env.py``, ``tests/conftest.py``,
``app.main``).
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
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
from app.db.models import Base, _now, _uuid

# ---------------------------------------------------------------------------
# Vocabularies. Enforced by CHECK constraints below and imported by services,
# so a value the database would refuse never gets as far as a flush.
# ---------------------------------------------------------------------------

#: pay_snapshots.source — why the snapshot was taken.
SNAPSHOT_SOURCES = ("monday", "close", "manual", "orbit")
#: pay_shift_facts.cost_group — Loaded's role cost group, named.
COST_GROUPS = ("bar_floor", "kitchen", "other")
#: pay_leave_payouts.workflow_kind — which payout workflow decided the row.
LEAVE_WORKFLOW_KINDS = ("annual", "sick", "generic")
#: Where a keyed row's figures came from.
ROW_SOURCES = ("norm", "orbit")
#: pay_leave_payouts.source adds the legacy rows Orbit keyed by request id only.
PAYOUT_SOURCES = ("norm", "orbit", "orbit_legacy")
#: pay_exports.kind — the four Ace files.
EXPORT_KINDS = ("import", "leave", "gratuities", "entertainers")
#: pay_adjustments.report_target — which Weekly Summary line an adjustment
#: lands on (NULL: none).
REPORT_TARGETS = ("wages", "leave")
#: pay_journal_drafts.kind.
JOURNAL_DRAFT_KINDS = ("journal", "ico_invoice", "ico_bill")
#: pay_xero_pushes.kind — drafts' kinds plus entertainer bills.
PUSH_KINDS = ("journal", "ico_invoice", "ico_bill", "entertainer_bill")
#: pay_files.kind.
PAY_FILE_KINDS = ("bond_consent",)
#: The tax rule on the leave & entitlements line of an intercompany invoice
#: or bill: the leave account's own default rate in Xero (Orbit's rule), no
#: GST, or GST at the standard rate (GST on Income on an invoice, GST on
#: Expenses on a bill). The accountant's call, per entity.
ICO_LEAVE_TAX_RULES = ("account_default", "no_gst", "standard_gst")


# ---------------------------------------------------------------------------
# Settings and rules
# ---------------------------------------------------------------------------


class PayEntity(NativeRow, _Versioned, Base):
    """A payroll entity: one venue that runs its own pay run.

    Holds the settings a client token cannot read from Loaded and every
    literal Orbit's payroll report hard-coded (the Ace pay-type code, the
    SECURITY pay code, the $150 uniform bond, the 12% leave uplift on
    intercompany recharges). Statutory literals are not here — they are dated
    rows in ``pay_rules``. ``payroll_on`` is the per-entity switch (AU venues
    stay off until asked, D12); ``payroll_contact_email`` overrides the
    organisation's (D25).
    """

    __tablename__ = "pay_entities"
    __table_args__ = _tenancy(
        "pay_entities",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        extra=(
            UniqueConstraint("venue_id", name="uq_pay_entities_venue"),
            _check("pay_entities", "ico_leave_tax", ICO_LEAVE_TAX_RULES),
        ),
    )

    venue_id = Column(String, nullable=False)
    loaded_company_id = Column(String, nullable=True)
    #: ISO weekday the pay week starts on (1 = Monday).
    pay_week_start_weekday = Column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    payroll_system = Column(String, nullable=False, default="ace", server_default="ace")
    ace_pay_type_code = Column(
        String, nullable=False, default="50001", server_default="50001"
    )
    ace_gratuity_allowance_code = Column(String, nullable=True)
    security_pay_code = Column(
        String, nullable=False, default="SECURITY", server_default="SECURITY"
    )
    uniform_bond_cents = Column(
        Integer, nullable=False, default=15000, server_default=text("15000")
    )
    #: Basis points added to leave recharged between entities (1200 = 12%).
    ico_leave_uplift_bp = Column(
        Integer, nullable=False, default=1200, server_default=text("1200")
    )
    #: The tax rule on that uplift's line (``ICO_LEAVE_TAX_RULES``).
    ico_leave_tax = Column(
        String,
        nullable=False,
        default="account_default",
        server_default="account_default",
    )
    sick_approver_name = Column(String, nullable=True)
    payroll_contact_email = Column(String, nullable=True)
    payroll_on = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class PayRule(Base):
    """A statutory rule with an effective date — sick-day hours, the sick
    leave eligibility thresholds, the Holidays Act literals.

    ``organization_id`` NULL is a platform default; an organisation's own row
    for the same ``rule`` overrides it. A change in the law is a new row with
    a new ``effective_from``, not a code change. Deliberately NOT a tenancy
    table (the org column is nullable), so it carries its own columns.
    """

    __tablename__ = "pay_rules"
    __table_args__ = (
        Index(
            "uq_pay_rules_scope",
            "organization_id",
            "rule",
            "effective_from",
            unique=True,
            # Two platform defaults (NULL org) for one rule and date would
            # otherwise both be allowed: NULLs are distinct in a plain unique.
            postgresql_nulls_not_distinct=True,
        ),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(
        String,
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
            name="fk_pay_rules_organization_id",
        ),
        nullable=True,
    )
    rule = Column(String, nullable=False)
    effective_from = Column(Date, nullable=False)
    params = Column(JSON, nullable=False, default=dict)
    #: Where the rule comes from — a citation ("Holidays Act 2003 s 63") or
    #: who set it.
    source = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
    )


# ---------------------------------------------------------------------------
# Weeks, snapshots, facts
# ---------------------------------------------------------------------------


class PayWeek(NativeRow, _Versioned, Base):
    """One pay week at one entity. Status is derived (a current snapshot, a
    close snapshot, exports, a push); ``closed_at`` is the sign-off fact."""

    __tablename__ = "pay_weeks"
    __table_args__ = _tenancy(
        "pay_weeks",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        extra=(
            UniqueConstraint("venue_id", "week_start", name="uq_pay_weeks_venue_week"),
        ),
    )

    venue_id = Column(String, nullable=False)
    week_start = Column(Date, nullable=False)
    notes = Column(Text, nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_by = _user_fk("pay_weeks", "closed_by")


class PaySnapshot(NativeRow, Base):
    """Loaded's clock-ins for a week, frozen. One current snapshot per week
    (``superseded_at IS NULL``); older ones are kept so a close can be diffed
    against the Monday run. ``content_sha256`` is over the facts, so an
    unchanged re-snapshot is recognisable."""

    __tablename__ = "pay_snapshots"
    __table_args__ = _tenancy(
        "pay_snapshots",
        _Ref("week_id", "pay_weeks", "CASCADE"),
        extra=(_check("pay_snapshots", "source", SNAPSHOT_SOURCES),),
    )

    week_id = Column(String, nullable=False)
    taken_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )
    taken_by = _user_fk("pay_snapshots", "taken_by")
    source = Column(String, nullable=False)
    shift_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    #: Clock-ins Loaded marked deleted, dropped at snapshot and counted.
    dropped_deleted = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: Clock-ins with no clock-out, dropped at snapshot and counted.
    dropped_unfinished = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    content_sha256 = Column(String(64), nullable=False)
    superseded_at = Column(DateTime(timezone=True), nullable=True)


class PayShiftFact(NativeRow, Base):
    """One shift as ``loadedhub.get_payrun`` shaped it, stored under its
    snapshot. ``cost_cents`` is the sum of Loaded's pay-rate breakdown,
    ``ph_cost_cents`` the public-holiday part of it; ``breakdown`` keeps the
    raw breakdown so a parity run can show its working."""

    __tablename__ = "pay_shift_facts"
    __table_args__ = _tenancy(
        "pay_shift_facts",
        _Ref("snapshot_id", "pay_snapshots", "CASCADE", index=False),
        _Ref("worked_venue_id", "venues", "SET NULL (worked_venue_id)", index=False),
        extra=(
            UniqueConstraint(
                "snapshot_id", "source_shift_id", name="uq_pay_shift_facts_shift"
            ),
            _check("pay_shift_facts", "cost_group", COST_GROUPS),
            Index(
                "ix_pay_shift_facts_staff",
                "organization_id",
                "snapshot_id",
                "staff_member_id",
            ),
        ),
    )

    snapshot_id = Column(String, nullable=False)
    source_shift_id = Column(String, nullable=False)
    staff_member_id = Column(String, nullable=True)
    staff_name = Column(String, nullable=True)
    pay_code = Column(String, nullable=True)
    shift_type = Column(String, nullable=True)
    remuneration_type = Column(String, nullable=True)
    role_id = Column(String, nullable=True)
    role_name = Column(String, nullable=True)
    cost_group = Column(String, nullable=True)
    worked_venue_loaded_id = Column(String, nullable=True)
    worked_venue_id = Column(String, nullable=True)
    is_from_other_venue = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    clockin_at = Column(DateTime(timezone=True), nullable=True)
    clockout_at = Column(DateTime(timezone=True), nullable=True)
    trading_date = Column(Date, nullable=True)
    hours = Column(Numeric(8, 2), nullable=False, default=0, server_default=text("0"))
    cost_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    ph_hours = Column(
        Numeric(8, 2), nullable=False, default=0, server_default=text("0")
    )
    ph_cost_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    leave_request_id = Column(String, nullable=True)
    leave_type_name = Column(String, nullable=True)
    is_approved = Column(Boolean, nullable=True)
    breakdown = Column(JSON, nullable=True)
    payload_sha256 = Column(String(64), nullable=True)


# ---------------------------------------------------------------------------
# Leave
# ---------------------------------------------------------------------------


class PayLeavePayout(NativeRow, _Versioned, Base):
    """What payroll decided to pay for one Loaded leave request in one pay
    week — the "paid" side beside Loaded's "requested". A multi-week request
    has one row per week. ``bond_consent_file_id`` NULL with a bond deducted
    means the consent is held in Ace, stated on the row."""

    __tablename__ = "pay_leave_payouts"
    __table_args__ = _tenancy(
        "pay_leave_payouts",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        _Ref(
            "bond_consent_file_id",
            "pay_files",
            "SET NULL (bond_consent_file_id)",
            index=False,
        ),
        extra=(
            UniqueConstraint(
                "organization_id",
                "loaded_request_id",
                "week_start",
                name="uq_pay_leave_payouts_request_week",
            ),
            _check("pay_leave_payouts", "workflow_kind", LEAVE_WORKFLOW_KINDS),
            _check("pay_leave_payouts", "source", PAYOUT_SOURCES),
            Index(
                "ix_pay_leave_payouts_venue_week",
                "organization_id",
                "venue_id",
                "week_start",
            ),
        ),
    )

    venue_id = Column(String, nullable=False)
    week_start = Column(Date, nullable=False)
    loaded_request_id = Column(String, nullable=False)
    staff_member_id = Column(String, nullable=True)
    staff_name = Column(String, nullable=True)
    leave_type = Column(String, nullable=True)
    requested_cents = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    paid_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    bond_deducted_cents = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    bond_consent_file_id = Column(String, nullable=True)
    workflow_kind = Column(String, nullable=False, default="generic")
    #: The workflow's inputs (annual: balance, bond, max payable; sick: the
    #: four gates, overrides, per-day hours). Health information for sick
    #: leave — ``payroll:write`` to read, access logged (plan §6.5).
    workflow_inputs = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)
    decided_by = _user_fk("pay_leave_payouts", "decided_by")
    decided_at = Column(DateTime(timezone=True), nullable=True)
    source = Column(String, nullable=False, default="norm", server_default="norm")
    rehearsal = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class PayLeaveStyle(NativeRow, _Versioned, Base):
    """The Ace style for a leave type Loaded holds no payroll meaning for — a
    company's own (custom) type such as "Paid Public Holiday", or a managed
    type Norm does not know. An override only (owner, 23 Oct 2026: "leave
    types should come from Loaded"): the type, whether it is paid, and the
    style of Loaded's own NZ types come from Loaded at export time
    (``services/payroll/exports.py``). The line shapes follow the style; the
    leave export refuses a type with neither."""

    __tablename__ = "pay_leave_styles"
    __table_args__ = _tenancy(
        "pay_leave_styles",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        extra=(
            UniqueConstraint(
                "venue_id",
                "loaded_leave_type_id",
                name="uq_pay_leave_styles_venue_type",
            ),
        ),
    )

    venue_id = Column(String, nullable=False)
    loaded_leave_type_id = Column(String, nullable=False)
    #: Loaded's name for the type when the override was set — a leave row
    #: in a snapshot carries the name, not the id.
    name = Column(String, nullable=False)
    ace_style = Column(String, nullable=False)
    set_by = _user_fk("pay_leave_styles", "set_by")


# ---------------------------------------------------------------------------
# Gratuities
# ---------------------------------------------------------------------------


class PayGratuityBatch(NativeRow, Base):
    """A gratuity pool earned at a venue over a period, paid in a pay week
    and split pro rata on hours worked there (plan §9)."""

    __tablename__ = "pay_gratuity_batches"
    __table_args__ = _tenancy(
        "pay_gratuity_batches",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        extra=(
            _check("pay_gratuity_batches", "source", ROW_SOURCES),
            Index(
                "ix_pay_gratuity_batches_venue_week",
                "organization_id",
                "venue_id",
                "week_start",
            ),
        ),
    )

    venue_id = Column(String, nullable=False)
    week_start = Column(Date, nullable=False)
    #: The pool's period as ``YYYY-MM``.
    period = Column(String(7), nullable=False)
    total_cents = Column(Integer, nullable=False)
    gl_code = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_by = _user_fk("pay_gratuity_batches", "created_by")
    source = Column(String, nullable=False, default="norm", server_default="norm")
    rehearsal = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class PayGratuityAllocation(NativeRow, Base):
    """One staff member's share of a batch. ``payroll_venue_id`` is the entity
    whose payroll pays it (a share paid elsewhere is an intercompany
    recharge); an excluded share is redistributed, an unpayable one (inactive
    staff) is kept but not paid."""

    __tablename__ = "pay_gratuity_allocations"
    __table_args__ = _tenancy(
        "pay_gratuity_allocations",
        _Ref("batch_id", "pay_gratuity_batches", "CASCADE"),
        _Ref(
            "payroll_venue_id",
            "venues",
            "SET NULL (payroll_venue_id)",
            index=False,
        ),
    )

    batch_id = Column(String, nullable=False)
    staff_member_id = Column(String, nullable=False)
    staff_name = Column(String, nullable=True)
    pay_code = Column(String, nullable=True)
    hours = Column(Numeric(8, 2), nullable=False, default=0, server_default=text("0"))
    amount_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    is_payable = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    payroll_venue_id = Column(String, nullable=True)
    excluded_at = Column(DateTime(timezone=True), nullable=True)
    excluded_by = _user_fk("pay_gratuity_allocations", "excluded_by")


# ---------------------------------------------------------------------------
# Exports and the close
# ---------------------------------------------------------------------------


class PayExport(NativeRow, Base):
    """An Ace file as generated, content included, so a re-export diffs and
    the parity engine compares bytes with the owner's files."""

    __tablename__ = "pay_exports"
    __table_args__ = _tenancy(
        "pay_exports",
        _Ref("week_id", "pay_weeks", "CASCADE"),
        _Ref("snapshot_id", "pay_snapshots", "NO ACTION", index=False),
        extra=(_check("pay_exports", "kind", EXPORT_KINDS),),
    )

    week_id = Column(String, nullable=False)
    snapshot_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    line_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    totals = Column(JSON, nullable=True)
    generated_by = _user_fk("pay_exports", "generated_by")
    generated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )


class PayAceInput(NativeRow, _Versioned, Base):
    """The totals of the entity's Ace pay run for the week, typed in by hand.
    Payroll taxes payable = gross − net + ESCT + KiwiSaver (plan §10)."""

    __tablename__ = "pay_ace_inputs"
    __table_args__ = _tenancy(
        "pay_ace_inputs",
        _Ref("week_id", "pay_weeks", "CASCADE", index=False),
        extra=(
            UniqueConstraint("week_id", name="uq_pay_ace_inputs_week"),
            _check("pay_ace_inputs", "source", ROW_SOURCES),
        ),
    )

    week_id = Column(String, nullable=False)
    gross_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    net_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    esct_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    kiwisaver_cents = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    entered_by = _user_fk("pay_ace_inputs", "entered_by")
    entered_at = Column(DateTime(timezone=True), nullable=True)
    source = Column(String, nullable=False, default="norm", server_default="norm")
    rehearsal = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class PayAdjustment(NativeRow, _Versioned, Base):
    """A signed manual line on the week's reconciliation: public-holiday pay
    Ace paid but Loaded never timed, terminations, head-office salaries,
    rounding. ``report_target`` says which Weekly Summary line it adjusts."""

    __tablename__ = "pay_adjustments"
    __table_args__ = _tenancy(
        "pay_adjustments",
        _Ref("week_id", "pay_weeks", "CASCADE"),
        extra=(
            _check("pay_adjustments", "report_target", REPORT_TARGETS),
            _check("pay_adjustments", "source", ROW_SOURCES),
        ),
    )

    week_id = Column(String, nullable=False)
    label = Column(String, nullable=False)
    amount_cents = Column(Integer, nullable=False)
    xero_account_code = Column(String, nullable=True)
    xero_account_name = Column(String, nullable=True)
    report_target = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))
    created_by = _user_fk("pay_adjustments", "created_by")
    source = Column(String, nullable=False, default="norm", server_default="norm")
    rehearsal = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class PayAccrual(NativeRow, Base):
    """Wages earned in the week but paid in a later run. ``opening_cents``
    NULL means "the prior week's closing"; closing is derived."""

    __tablename__ = "pay_accruals"
    __table_args__ = _tenancy(
        "pay_accruals",
        _Ref("week_id", "pay_weeks", "CASCADE", index=False),
        extra=(
            UniqueConstraint("week_id", name="uq_pay_accruals_week"),
            _check("pay_accruals", "source", ROW_SOURCES),
        ),
    )

    week_id = Column(String, nullable=False)
    opening_cents = Column(Integer, nullable=True)
    accrued_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    paid_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    source = Column(String, nullable=False, default="norm", server_default="norm")


# ---------------------------------------------------------------------------
# Xero
# ---------------------------------------------------------------------------


class PayJournalDraft(NativeRow, Base):
    """The journal (or intercompany document) as Norm built it from a
    snapshot — what the person approves and the consolidator posts.
    ``basis_sha256`` fingerprints the inputs, so a push can refuse a draft
    whose basis moved."""

    __tablename__ = "pay_journal_drafts"
    __table_args__ = _tenancy(
        "pay_journal_drafts",
        _Ref("week_id", "pay_weeks", "CASCADE"),
        _Ref("snapshot_id", "pay_snapshots", "NO ACTION", index=False),
        _Ref("counterparty_venue_id", "venues", "NO ACTION", index=False),
        extra=(_check("pay_journal_drafts", "kind", JOURNAL_DRAFT_KINDS),),
    )

    week_id = Column(String, nullable=False)
    snapshot_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    counterparty_venue_id = Column(String, nullable=True)
    lines = Column(JSON, nullable=False, default=list)
    debit_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    credit_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    residual_cents = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    basis_sha256 = Column(String(64), nullable=False)
    built_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )
    built_by = _user_fk("pay_journal_drafts", "built_by")


class PayXeroPush(NativeRow, Base):
    """Every document Norm put in Xero for payroll: wage journals,
    intercompany invoices and bills, entertainer bills. The "already pushed"
    guard is the partial unique below — one live document per (venue, week,
    kind, counterparty, performance); a re-push voids the old one first.
    ``venue_id`` and ``week_start`` are stored on the row for that index
    (entertainer bills have no week).
    """

    __tablename__ = "pay_xero_pushes"
    __table_args__ = _tenancy(
        "pay_xero_pushes",
        _Ref("week_id", "pay_weeks", "NO ACTION", index=False),
        _Ref("draft_id", "pay_journal_drafts", "SET NULL (draft_id)", index=False),
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        _Ref("counterparty_venue_id", "venues", "NO ACTION", index=False),
        extra=(
            UniqueConstraint("idempotency_key", name="uq_pay_xero_pushes_key"),
            _check("pay_xero_pushes", "kind", PUSH_KINDS),
            _check("pay_xero_pushes", "source", ROW_SOURCES),
            Index(
                "uq_pay_xero_pushes_live",
                "organization_id",
                "venue_id",
                "week_start",
                "kind",
                "counterparty_venue_id",
                "entertainer_performance_id",
                unique=True,
                postgresql_where=text("voided_at IS NULL"),
                # A journal has no counterparty or performance, an entertainer
                # bill no week: NULLs must collide or the guard is no guard.
                postgresql_nulls_not_distinct=True,
            ),
            Index(
                "ix_pay_xero_pushes_venue_week",
                "organization_id",
                "venue_id",
                "week_start",
            ),
        ),
    )

    week_id = Column(String, nullable=True)
    draft_id = Column(String, nullable=True)
    kind = Column(String, nullable=False)
    venue_id = Column(String, nullable=False)
    week_start = Column(Date, nullable=True)
    counterparty_venue_id = Column(String, nullable=True)
    #: An ``ent_performances`` id (text, not a key: the entertainers tables are
    #: created after these).
    entertainer_performance_id = Column(String, nullable=True)
    xero_tenant_id = Column(String, nullable=False)
    xero_id = Column(String, nullable=True)
    xero_number = Column(String, nullable=True)
    xero_status = Column(String, nullable=True)
    request_payload = Column(JSON, nullable=True)
    response = Column(JSON, nullable=True)
    idempotency_key = Column(String, nullable=False)
    pushed_by = _user_fk("pay_xero_pushes", "pushed_by")
    pushed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )
    voided_at = Column(DateTime(timezone=True), nullable=True)
    voided_by = _user_fk("pay_xero_pushes", "voided_by")
    source = Column(String, nullable=False, default="norm", server_default="norm")


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


class PayFile(NativeRow, Base):
    """Bytes payroll keeps — today only a uniform-bond consent. The
    ``fn_files`` pattern: content-addressed per organisation, bytes in the
    column (a handful of PDFs a year), PDFs through ``files/pdf_check.py``."""

    __tablename__ = "pay_files"
    __table_args__ = _tenancy(
        "pay_files",
        extra=(
            UniqueConstraint("organization_id", "sha256", name="uq_pay_files_sha"),
            _check("pay_files", "kind", PAY_FILE_KINDS),
        ),
    )

    sha256 = Column(String(64), nullable=False)
    kind = Column(String, nullable=False)
    filename = Column(String, nullable=True)
    content_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    data = Column(LargeBinary, nullable=False)
    created_by = _user_fk("pay_files", "created_by")


PAYROLL_MODELS = tuple(
    sorted(
        (
            m
            for m in list(globals().values())
            if isinstance(m, type)
            and issubclass(m, Base)
            and getattr(m, "__tablename__", "").startswith("pay_")
        ),
        key=lambda m: m.__tablename__,
    )
)
PAYROLL_TABLES = frozenset(m.__tablename__ for m in PAYROLL_MODELS)
