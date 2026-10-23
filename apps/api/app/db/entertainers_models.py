"""Entertainers — the ``ent_*`` tables (plan: docs/plans/finance-migration.md §6.2, §11).

A performer is an identity with a payment **lane** (how they are paid:
through payroll as an employee or a withholding-tax contractor, on an
invoice, or under an exemption certificate) and the compliance record that
lane needs — the IR330C declaration signed on the onboarding form, the
contractor confirmations, the certificate. A performance is one booking at
one venue: the agreed rate, what the manager approved, what the performer
confirmed, who closed the venue that night, and when it was paid.

The bank account, IRD number and date of birth are ``EncryptedText`` (D9) —
plaintext in Python, ``enc:v1:`` ciphertext at rest once
``TOKEN_ENCRYPTION_KEYS`` is set — and are returned only to ``payroll:write``
holders, never on a public page (D15, plan §17). Nothing here is a status a
job keeps up to date: a performance's status (paid › did not perform ›
mismatch | ready for payment › awaiting performer › awaiting manager ›
scheduled) is derived in one place, ``services/entertainers/status.py``,
from the stored facts.

Files are content-addressed bytes (the ``fn_files`` pattern). A performer's
documents hang off the performer row (``exemption_file_id``,
``declaration_file_id``, ``company_document_file_id``); an invoice hangs off
every performance it covers (``invoice_file_id`` — one invoice may cover
several). So ``ent_files`` points at nothing, and nothing points in a cycle.

Public links store only a token's SHA-256, globally unique, with the one-time
code's state beside it (``functions/portal.py``'s numbers, copied not
imported). Conventions and ON DELETE are ``app/db/_native_rows.py``'s.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
    Time,
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
from app.db.encrypted import EncryptedText
from app.db.models import Base, _now

# ---------------------------------------------------------------------------
# Vocabularies (DESIGN-CONTRACT §3). Enforced by CHECK constraints below.
# ---------------------------------------------------------------------------

#: ent_performers.lane — how the performer is paid.
LANES = ("payroll_employee", "payroll_contractor", "invoice", "exemption")
#: ent_performers.performer_type — a label, not a lane.
PERFORMER_TYPES = ("solo", "band", "agent")
#: ent_performers.band_mode — one payee, or each member paid separately.
BAND_MODES = ("leader", "members_split")
#: ent_performers.onboarding_status / ent_band_members.invite_status.
ONBOARDING_STATUSES = ("invited", "submitted", "accepted")
#: ent_performers.wt_rate_type — the IR330C rate choice.
WT_RATE_TYPES = ("standard", "self_selected", "tailored")
#: ent_performances.performance_status — the STORED manager decision; the
#: derived status (services/entertainers/status.py) layers the rest on it.
PERFORMANCE_STATUSES = ("scheduled", "approved", "did_not_perform")
#: ent_performances.payment_status.
PAYMENT_STATUSES = ("pending", "paid")
#: ent_performances.closing_manager_source — how the closing manager was found.
CLOSING_MANAGER_SOURCES = ("clockin", "both", "rostered_only", "none", "orbit")
#: ent_performance_changes.changed_by_source.
CHANGE_SOURCES = ("manager", "performer", "system", "import")
#: ent_files.kind.
ENT_FILE_KINDS = ("invoice", "exemption_certificate", "ir330c", "company_document")
#: ent_files.check_status — the invoice check (plan §11.5).
CHECK_STATUSES = ("pending", "matched", "query", "unreadable", "waived")
#: ent_public_tokens.purpose.
PUBLIC_TOKEN_PURPOSES = ("portal", "onboarding", "band_member", "declaration")
#: ent_email_links.recipient_kind.
EMAIL_RECIPIENT_KINDS = (
    "performer",
    "band_member",
    "closing_manager",
    "venue_manager",
    "finance",
)
#: ent_email_links.source — sent by Norm, or a send date carried from Orbit.
EMAIL_LINK_SOURCES = ("norm", "orbit")

#: ent_settings defaults (D22; plan §11.1, §11.2).
DEFAULT_CLOSING_MANAGER_ROLES = ["Sales Manager"]
DEFAULT_MISMATCH_TOLERANCE_MINUTES = 15
DEFAULT_REMINDER_INTERVAL_DAYS = 3
DEFAULT_REMINDER_MAX = 4


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


class EntFile(NativeRow, Base):
    """Bytes the module keeps: invoices, exemption certificates, a signed
    IR330C, company documents. Content-addressed per organisation; PDFs
    through ``files/pdf_check.py`` and images through ``functions/uploads.py``
    before a row exists. An invoice row carries the number and amount the
    performer typed, and the check's findings (``check_status``,
    ``extracted``, ``check_findings``) — the ``extracted`` JSON lives and dies
    with the file (plan §6.5)."""

    __tablename__ = "ent_files"
    __table_args__ = _tenancy(
        "ent_files",
        extra=(
            UniqueConstraint("organization_id", "sha256", name="uq_ent_files_sha"),
            _check("ent_files", "kind", ENT_FILE_KINDS),
            _check("ent_files", "check_status", CHECK_STATUSES),
        ),
    )

    sha256 = Column(String(64), nullable=False)
    kind = Column(String, nullable=False)
    filename = Column(String, nullable=True)
    content_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    data = Column(LargeBinary, nullable=False)
    #: Where the bytes came from when not uploaded here (an Orbit storage
    #: URL), so a re-import recognises a file it already has.
    source_ref = Column(String, nullable=True)
    uploaded_by_user_id = _user_fk("ent_files", "uploaded_by_user_id")
    invoice_number = Column(String, nullable=True)
    invoice_cents = Column(Integer, nullable=True)
    check_status = Column(
        String, nullable=False, default="pending", server_default="pending"
    )
    extracted = Column(JSON, nullable=True)
    check_findings = Column(JSON, nullable=True)
    checked_at = Column(DateTime(timezone=True), nullable=True)
    waived_by = _user_fk("ent_files", "waived_by")
    waived_at = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Performers
# ---------------------------------------------------------------------------


class EntPerformer(NativeRow, _Versioned, Base):
    """A performer: identity, lane, the tax and bank details the lane needs,
    and the compliance record.

    ``onboarding_status`` is the one stored state here and it is a fact
    ladder, not a workflow: ``invited`` (a link was sent), ``submitted`` (the
    form came back — ``submitted_at``), ``accepted`` (finance checked it —
    ``accepted_at/by``). Submitted is not payable (plan §11.2);
    ``bank_changed_at`` after ``accepted_at`` is not payable either until
    accepted again. ``needs_review`` flags a lane the performer could not
    pick ("not sure") or a band whose members have not all submitted.
    ``declaration_snapshot`` is the IR330C record (wording version, typed
    signature, signed at, IP, user agent) and is kept with the pay records.
    """

    __tablename__ = "ent_performers"
    __table_args__ = _tenancy(
        "ent_performers",
        _Ref(
            "exemption_file_id",
            "ent_files",
            "SET NULL (exemption_file_id)",
            index=False,
        ),
        _Ref(
            "declaration_file_id",
            "ent_files",
            "SET NULL (declaration_file_id)",
            index=False,
        ),
        _Ref(
            "company_document_file_id",
            "ent_files",
            "SET NULL (company_document_file_id)",
            index=False,
        ),
        extra=(
            _check("ent_performers", "lane", LANES),
            _check("ent_performers", "performer_type", PERFORMER_TYPES),
            _check("ent_performers", "band_mode", BAND_MODES),
            _check("ent_performers", "onboarding_status", ONBOARDING_STATUSES),
            _check("ent_performers", "wt_rate_type", WT_RATE_TYPES),
            Index(
                "ix_ent_performers_email",
                "organization_id",
                func.lower(text("email")),
            ),
        ),
    )

    name = Column(String, nullable=False)
    legal_name = Column(String, nullable=True)
    trading_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: NULL until the form (or finance) picks one.
    lane = Column(String, nullable=True)
    performer_type = Column(String, nullable=True)
    band_mode = Column(String, nullable=True)
    onboarding_status = Column(
        String, nullable=False, default="invited", server_default="invited"
    )
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    accepted_by = _user_fk("ent_performers", "accepted_by")
    bank_changed_at = Column(DateTime(timezone=True), nullable=True)
    needs_review = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    review_reason = Column(Text, nullable=True)
    # --- tax and bank (encrypted at rest; payroll:write to read) -----------
    ird_number = Column(EncryptedText, nullable=True)
    bank_account = Column(EncryptedText, nullable=True)
    date_of_birth = Column(EncryptedText, nullable=True)
    #: Normalised IR tax code (WT, M, M SL, …).
    tax_code = Column(String, nullable=True)
    #: KiwiSaver contribution rate as a percentage (3.00, 4.00, …).
    kiwisaver_rate = Column(Numeric(5, 2), nullable=True)
    #: The employee code in Ace.
    payroll_code = Column(String, nullable=True)
    #: From the registration answer, never from the payment method.
    gst_registered = Column(Boolean, nullable=True)
    gst_number = Column(String, nullable=True)
    company_name = Column(String, nullable=True)
    company_number = Column(String, nullable=True)
    company_document_file_id = Column(String, nullable=True)
    exemption_certificate_number = Column(String, nullable=True)
    exemption_expiry_date = Column(Date, nullable=True)
    exemption_file_id = Column(String, nullable=True)
    # --- the IR330C declaration (withholding-tax contractors) ---------------
    nz_tax_resident = Column(Boolean, nullable=True)
    wt_rate_type = Column(String, nullable=True)
    #: The withholding rate in basis points (2000 = 20%).
    wt_rate_bp = Column(Integer, nullable=True)
    declaration_snapshot = Column(JSON, nullable=True)
    #: A paper/PDF IR330C uploaded as the fallback for pre-Norm performers.
    declaration_file_id = Column(String, nullable=True)
    band_leader_declaration_at = Column(DateTime(timezone=True), nullable=True)
    #: The six contractor confirmations, keyed by name, each True/False.
    contractor_confirmations = Column(JSON, nullable=True)
    # --- invitations ---------------------------------------------------------
    invite_sent_at = Column(DateTime(timezone=True), nullable=True)
    invite_reminders_sent = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_reminder_at = Column(DateTime(timezone=True), nullable=True)
    anonymised_at = Column(DateTime(timezone=True), nullable=True)
    created_by = _user_fk("ent_performers", "created_by")


class EntBandMember(NativeRow, Base):
    """A member of a ``members_split`` band. ``member_performer_id`` is NULL
    until the member submits their own form and becomes a performer;
    ``share_bp`` is their share of the band's fee in basis points (the shares
    of a band sum to 10,000; equal by default, finance edits)."""

    __tablename__ = "ent_band_members"
    __table_args__ = _tenancy(
        "ent_band_members",
        _Ref("band_performer_id", "ent_performers", "CASCADE"),
        _Ref("member_performer_id", "ent_performers", "NO ACTION", index=False),
        extra=(
            _check("ent_band_members", "invite_status", ONBOARDING_STATUSES),
            CheckConstraint(
                "share_bp >= 0 AND share_bp <= 10000",
                name="ck_ent_band_members_share_bp",
            ),
        ),
    )

    band_performer_id = Column(String, nullable=False)
    member_performer_id = Column(String, nullable=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    share_bp = Column(Integer, nullable=False, default=0, server_default=text("0"))
    invite_status = Column(
        String, nullable=False, default="invited", server_default="invited"
    )
    invite_sent_at = Column(DateTime(timezone=True), nullable=True)
    reminders_sent = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_reminder_at = Column(DateTime(timezone=True), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))
    removed_at = Column(DateTime(timezone=True), nullable=True)


class EntPerformerXeroContact(NativeRow, Base):
    """The Xero contact a performer's bills go to, per tenant (each venue is
    its own Xero organisation)."""

    __tablename__ = "ent_performer_xero_contacts"
    __table_args__ = _tenancy(
        "ent_performer_xero_contacts",
        _Ref("performer_id", "ent_performers", "CASCADE"),
        extra=(
            UniqueConstraint(
                "performer_id",
                "xero_tenant_id",
                name="uq_ent_performer_xero_contacts_tenant",
            ),
        ),
    )

    performer_id = Column(String, nullable=False)
    xero_tenant_id = Column(String, nullable=False)
    xero_contact_id = Column(String, nullable=False)
    xero_contact_name = Column(String, nullable=True)


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------


class EntSeries(NativeRow, _Versioned, Base):
    """A stored weekly recurrence (Orbit only kept the materialised rows):
    every ``weekday`` from ``starts_on`` until ``ends_on``, materialised
    twelve months ahead by the roster service. Retired by ``archived_at``;
    the performances already made stay."""

    __tablename__ = "ent_series"
    __table_args__ = _tenancy(
        "ent_series",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        _Ref("performer_id", "ent_performers", "NO ACTION"),
        extra=(
            CheckConstraint("weekday BETWEEN 1 AND 7", name="ck_ent_series_weekday"),
        ),
    )

    venue_id = Column(String, nullable=False)
    performer_id = Column(String, nullable=False)
    #: ISO weekday (1 = Monday … 7 = Sunday).
    weekday = Column(Integer, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    ends_next_day = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    rate_cents = Column(Integer, nullable=False)
    starts_on = Column(Date, nullable=False)
    ends_on = Column(Date, nullable=True)
    #: The last date the roster service materialised performances to.
    materialised_through = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_by = _user_fk("ent_series", "created_by")


class EntPerformance(NativeRow, _Versioned, Base):
    """One booking. Three sets of times, all venue-local: what was booked,
    what the manager approved (``actual_*``, DST-aware pay = rate × real
    local hours), what the performer confirmed (``performer_actual_*``). The
    closing manager is who closed the venue that night (plan §11.7) — a
    Loaded staff id, their name and email, the Norm user when one matches,
    and how they were found. Payment is the ``paid_*`` facts; a payroll-lane
    performance records the pay week it went into."""

    __tablename__ = "ent_performances"
    __table_args__ = _tenancy(
        "ent_performances",
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        _Ref("performer_id", "ent_performers", "NO ACTION"),
        _Ref("series_id", "ent_series", "NO ACTION"),
        _Ref("invoice_file_id", "ent_files", "SET NULL (invoice_file_id)"),
        extra=(
            _check("ent_performances", "performance_status", PERFORMANCE_STATUSES),
            _check("ent_performances", "payment_status", PAYMENT_STATUSES),
            _check(
                "ent_performances", "closing_manager_source", CLOSING_MANAGER_SOURCES
            ),
            Index(
                "ix_ent_performances_venue_date",
                "organization_id",
                "venue_id",
                "performance_date",
            ),
        ),
    )

    venue_id = Column(String, nullable=False)
    performer_id = Column(String, nullable=False)
    series_id = Column(String, nullable=True)
    performance_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    ends_next_day = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    rate_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    #: rate × booked hours, fixed when booked.
    agreed_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    # --- the manager's approval ----------------------------------------------
    actual_start_time = Column(Time, nullable=True)
    actual_end_time = Column(Time, nullable=True)
    actual_ends_next_day = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    actual_cents = Column(Integer, nullable=True)
    performance_status = Column(
        String, nullable=False, default="scheduled", server_default="scheduled"
    )
    dnp_reason = Column(Text, nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by = _user_fk("ent_performances", "approved_by")
    # --- the performer's confirmation (magic link) ---------------------------
    performer_actual_start_time = Column(Time, nullable=True)
    performer_actual_end_time = Column(Time, nullable=True)
    performer_actual_ends_next_day = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    performer_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    performer_dnp = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    performer_dnp_reason = Column(Text, nullable=True)
    # --- the closing manager ---------------------------------------------------
    closing_manager_staff_id = Column(String, nullable=True)
    closing_manager_name = Column(String, nullable=True)
    closing_manager_email = Column(String, nullable=True)
    closing_manager_user_id = _user_fk("ent_performances", "closing_manager_user_id")
    closing_manager_source = Column(String, nullable=True)
    closing_manager_resolved_at = Column(DateTime(timezone=True), nullable=True)
    # --- payment -----------------------------------------------------------------
    payment_status = Column(
        String, nullable=False, default="pending", server_default="pending"
    )
    paid_at = Column(DateTime(timezone=True), nullable=True)
    paid_by = _user_fk("ent_performances", "paid_by")
    paid_reference = Column(String, nullable=True)
    #: The pay week a payroll-lane performance was paid in.
    payroll_week_start = Column(Date, nullable=True)
    invoice_file_id = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_by = _user_fk("ent_performances", "created_by")


class EntPerformanceShare(NativeRow, Base):
    """A ``members_split`` band's approved amount, split by ``share_bp`` on
    approval — the leftover cent to the first member. Each share is paid
    through its member's own lane."""

    __tablename__ = "ent_performance_shares"
    __table_args__ = _tenancy(
        "ent_performance_shares",
        _Ref("performance_id", "ent_performances", "CASCADE"),
        _Ref("band_member_id", "ent_band_members", "NO ACTION", index=False),
        _Ref("member_performer_id", "ent_performers", "NO ACTION", index=False),
        extra=(
            UniqueConstraint(
                "performance_id",
                "band_member_id",
                name="uq_ent_performance_shares_member",
            ),
        ),
    )

    performance_id = Column(String, nullable=False)
    band_member_id = Column(String, nullable=False)
    member_performer_id = Column(String, nullable=True)
    amount_cents = Column(Integer, nullable=False)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    paid_by = _user_fk("ent_performance_shares", "paid_by")
    paid_reference = Column(String, nullable=True)


class EntPerformanceChange(NativeRow, Base):
    """Field-level audit of a performance: every confirmation, approval and
    edit, who made it and from where. ``ip_hash`` and ``user_agent`` are
    blanked after six months by the retention job (plan §6.5)."""

    __tablename__ = "ent_performance_changes"
    __table_args__ = _tenancy(
        "ent_performance_changes",
        _Ref("performance_id", "ent_performances", "CASCADE"),
        extra=(_check("ent_performance_changes", "changed_by_source", CHANGE_SOURCES),),
    )

    performance_id = Column(String, nullable=False)
    changed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )
    changed_by_source = Column(String, nullable=False)
    changed_by_user_id = _user_fk("ent_performance_changes", "changed_by_user_id")
    changed_by_name = Column(String, nullable=True)
    field = Column(String, nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    note = Column(Text, nullable=True)
    ip_hash = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)


# ---------------------------------------------------------------------------
# Public links, email, settings
# ---------------------------------------------------------------------------


class EntPublicToken(NativeRow, Base):
    """A personal link: the portal, the onboarding form, a band member's
    form, the declaration-only request. Only the SHA-256 of the token is
    stored, globally unique (a public request carries no organisation).

    An onboarding link submits once (``submitted_at``); "Resend" revokes it
    and mints a new one. The one-time code that opens the bank and tax steps
    lives beside it: hash, expiry, wrong attempts, and the send window
    (10-minute code, 5 attempts, 3 sends per 15 minutes — plan §11.2).
    """

    __tablename__ = "ent_public_tokens"
    __table_args__ = _tenancy(
        "ent_public_tokens",
        _Ref("performer_id", "ent_performers", "CASCADE"),
        _Ref("band_member_id", "ent_band_members", "CASCADE"),
        extra=(
            UniqueConstraint("token_hash", name="uq_ent_public_tokens_hash"),
            _check("ent_public_tokens", "purpose", PUBLIC_TOKEN_PURPOSES),
            CheckConstraint(
                "(purpose = 'band_member' AND band_member_id IS NOT NULL) "
                "OR (purpose <> 'band_member' AND performer_id IS NOT NULL)",
                name="ck_ent_public_tokens_target",
            ),
        ),
    )

    token_hash = Column(String(64), nullable=False)
    purpose = Column(String, nullable=False)
    performer_id = Column(String, nullable=True)
    band_member_id = Column(String, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    otp_hash = Column(String, nullable=True)
    otp_expires_at = Column(DateTime(timezone=True), nullable=True)
    otp_attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    otp_sends_in_window = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    otp_window_started_at = Column(DateTime(timezone=True), nullable=True)
    otp_verified_at = Column(DateTime(timezone=True), nullable=True)
    created_by = _user_fk("ent_public_tokens", "created_by")


class EntEmailLink(NativeRow, Base):
    """One row per email the module sent (``email_log_id`` → ``email_logs``,
    where delivery status lands), and Orbit's send dates carried as history
    (``source = 'orbit'``, no ``email_log_id``, date only). The Emails page
    reads this table (D32)."""

    __tablename__ = "ent_email_links"
    __table_args__ = _tenancy(
        "ent_email_links",
        _Ref("performer_id", "ent_performers", "CASCADE"),
        _Ref("band_member_id", "ent_band_members", "CASCADE", index=False),
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        extra=(
            _check("ent_email_links", "recipient_kind", EMAIL_RECIPIENT_KINDS),
            _check("ent_email_links", "source", EMAIL_LINK_SOURCES),
            Index("ix_ent_email_links_sent", "organization_id", "sent_at"),
        ),
    )

    email_log_id = Column(
        String,
        ForeignKey(
            "email_logs.id", ondelete="SET NULL", name="fk_ent_email_links_email_log_id"
        ),
        nullable=True,
    )
    performer_id = Column(String, nullable=True)
    band_member_id = Column(String, nullable=True)
    venue_id = Column(String, nullable=True)
    recipient_kind = Column(String, nullable=False)
    #: What the email was: invite, invite_reminder, code, confirm_request,
    #: mismatch, approved_amount, invoice_query, manager_approval, digest, …
    #: (the template names in DESIGN-CONTRACT §5).
    kind = Column(String, nullable=False)
    recipient_email = Column(String, nullable=True)
    subject = Column(String, nullable=True)
    #: The performances the email was about.
    performance_ids = Column(JSON, nullable=True)
    sent_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )
    source = Column(String, nullable=False, default="norm", server_default="norm")


class EntSettings(NativeRow, _Versioned, Base):
    """One row per organisation: which Loaded roles count as the closing
    manager (D22), the mismatch tolerance between the performer's and the
    manager's times, and the reminder throttle."""

    __tablename__ = "ent_settings"
    __table_args__ = _tenancy(
        "ent_settings",
        extra=(UniqueConstraint("organization_id", name="uq_ent_settings_org"),),
    )

    closing_manager_roles = Column(
        JSON, nullable=False, default=lambda: list(DEFAULT_CLOSING_MANAGER_ROLES)
    )
    mismatch_tolerance_minutes = Column(
        Integer,
        nullable=False,
        default=DEFAULT_MISMATCH_TOLERANCE_MINUTES,
        server_default=text(str(DEFAULT_MISMATCH_TOLERANCE_MINUTES)),
    )
    reminder_interval_days = Column(
        Integer,
        nullable=False,
        default=DEFAULT_REMINDER_INTERVAL_DAYS,
        server_default=text(str(DEFAULT_REMINDER_INTERVAL_DAYS)),
    )
    reminder_max = Column(
        Integer,
        nullable=False,
        default=DEFAULT_REMINDER_MAX,
        server_default=text(str(DEFAULT_REMINDER_MAX)),
    )


ENTERTAINERS_MODELS = tuple(
    sorted(
        (
            m
            for m in list(globals().values())
            if isinstance(m, type)
            and issubclass(m, Base)
            and getattr(m, "__tablename__", "").startswith("ent_")
        ),
        key=lambda m: m.__tablename__,
    )
)
ENTERTAINERS_TABLES = frozenset(m.__tablename__ for m in ENTERTAINERS_MODELS)
