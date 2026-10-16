"""Norm Functions — the schema for enquiries, events and everything around them.

Functions is a native module (docs: the Norm Functions plan), so its data lives
in real tables rather than the app platform's JSON records. Three rules shape
every table here; they are what the metadata test in
``tests/test_functions_foundation.py`` asserts:

**Tenancy is in the schema, not in each query.** Every table carries
``organization_id NOT NULL`` and ``UNIQUE (organization_id, id)``, and every
reference to another Functions table — or to ``venues`` — is a composite
foreign key ``(organization_id, x_id) -> x (organization_id, id)``. A row can
therefore never point at another organisation's row, whatever a query forgets
to filter. References to ``users`` stay plain: users are global.

**Store facts, derive state.** Nothing here has a ``status`` column that a job
keeps up to date. An enquiry is "quoted" because a ``fn_proposal_events`` row
says a quote was sent; an event is "confirmed" because three
``fn_event_section_confirmations`` say so. The derivations live in exactly one
place, ``app/services/functions/status.py``.

**Plans are local, facts are instants.** When something happened is a
``timestamptz``. When something is planned to happen (an event's date and start
time, a minimum-spend window, a run-sheet line) is a venue-local ``Date`` +
``Time`` with an ``ends_next_day`` flag — converting a plan to an instant bakes
in whichever zone was assumed at the time, which is how Orbit ended up with New
York times on Auckland events. ``timeutil.to_instant`` converts when needed.

Money is integer cents (``*_cents``); currency and tax wording are company
settings on ``organizations``.

ON DELETE, deliberately:

* ``organization_id`` cascades — an organisation's Functions data goes with it.
* A child that only means something inside its parent cascades (an email
  address of a contact, a run-sheet line of an event, a fact about an option).
* A reference between business records is ``NO ACTION``: a contact with
  enquiries, or a venue with events, cannot be hard-deleted (archive instead).
  ``NO ACTION`` rather than ``RESTRICT`` because ``RESTRICT`` is checked before
  sibling cascades run, which would make an organisation itself undeletable.
* A composite reference can never be plain ``SET NULL``: that would null
  ``organization_id`` too. Where a reference should just drop (an event's
  table-plan image), Postgres's column-list form ``SET NULL (column)`` nulls
  only the reference. Things that get retired (areas, templates) are
  deactivated, not deleted.

Like ``mcp_models``, this module is not re-exported from ``app.db.models``
(it imports ``Base`` from there). It is imported explicitly wherever
``Base.metadata`` must be complete: ``alembic/env.py``, ``tests/conftest.py``,
``app.main``, and the Functions services package.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import declared_attr, mapped_column

from app.db.models import Base, _now, _uuid

# ---------------------------------------------------------------------------
# Vocabularies. Enforced by CHECK constraints below and imported by services,
# so a value the database would refuse never gets as far as a flush.
# ---------------------------------------------------------------------------

#: organizations.functions_mode (CHECK ck_organizations_functions_mode).
FUNCTIONS_MODES = ("off", "shadow", "live")

BOOKING_TYPES = (
    "seated_dining",
    "group_booking",
    "large_event_partial",
    "large_event_exclusive",
)
#: Booking types sold on a minimum spend. Their events come into being only by
#: a customer confirming an approved option (min_spends.confirm_option); the
#: others are confirmed on the booking itself.
MIN_SPEND_BOOKING_TYPES = ("large_event_partial", "large_event_exclusive")

ENQUIRY_SOURCES = (
    "email",
    "web_form",
    "phone",
    "walk_in",
    "manual",
    "agent",
    "import",
    "other",
)
ENQUIRY_CLOSURE_KINDS = ("lost", "reopened")

APPROVAL_KINDS = ("approve", "override", "revoke")
RESPONSE_KINDS = ("confirm", "decline")
RESPONSE_CHANNELS = ("quote_page", "email", "phone", "in_person", "import")

EVENT_SECTIONS = ("details", "beverages", "food")
SECTION_CONFIRMATION_KINDS = ("confirmed", "skipped", "reopened")
EVENT_CHANGE_KINDS = ("created", "edited", "link_sent", "cancelled", "reinstated")
ACTOR_KINDS = ("staff", "client", "system", "import")
EVENT_CREATED_VIA = ("min_spend", "booking", "direct", "import")
FOOD_TIMING_TYPES = (
    "all_at_once_arrival",
    "staggered_arrival",
    "all_at_once_time",
    "staggered_time",
)
ENTERTAINMENT_KINDS = ("dj", "playlist")
AV_KINDS = ("in_house_mic", "in_house_tv", "external_av")
MENU_ITEM_KINDS = ("header", "item")

PROPOSAL_EVENT_KINDS = ("sent", "viewed")
PROPOSAL_EVENT_SOURCES = ("outbox", "gmail", "quote_page", "import")

MAILBOX_STATUSES = ("active", "needs_reconnect", "resyncing", "disabled")
FOLDERS = ("enquiry", "reservation", "other")
OUTBOX_STATUSES = ("sending", "sent", "failed")
OUTBOX_PURPOSES = ("reply", "compose", "forward", "proposal", "portal_link")
DRAFT_KINDS = ("email", "proposal")
#: How a draft goes out. NULL = as before: a reply when it has a thread, else new.
DRAFT_MODES = ("reply", "reply_all", "forward", "new")
DRAFT_OWNERS = ("norm", "gmail")
DRAFT_STATUSES = ("open", "sent", "discarded")
REPLY_TEMPLATE_STAGES = ("first_reply", "follow_up")

PUBLIC_TOKEN_KINDS = ("quote", "portal")
FORM_FIELD_TYPES = (
    "name",
    "email",
    "phone",
    "text",
    "long_text",
    "number",
    "date",
    "dates",
    "time_range",
    "choice",
    "checkbox",
    "section",
)
FORM_DELIVERY_STATUSES = ("pending", "sent", "failed", "skipped")


# ---------------------------------------------------------------------------
# Table-argument helpers — the tenancy rules, written once.
# ---------------------------------------------------------------------------


class _Ref:
    """A composite, org-scoped reference from one table to another."""

    def __init__(
        self,
        cols: str | tuple[str, ...],
        target: str,
        ondelete: str,
        *,
        target_cols: tuple[str, ...] | None = None,
        index: bool = True,
    ):
        self.cols = (cols,) if isinstance(cols, str) else tuple(cols)
        self.target = target
        self.target_cols = target_cols or ("id",)
        self.ondelete = ondelete
        self.index = index


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def _check(table: str, column: str, values: tuple[str, ...]) -> CheckConstraint:
    """A vocabulary CHECK. NULL passes, as in every SQL CHECK — nullability is
    the column's business."""
    return CheckConstraint(_in(column, values), name=f"ck_{table}_{column}")


def _tenancy(table: str, *refs: _Ref, extra: tuple = ()) -> tuple:
    """``UNIQUE (organization_id, id)`` plus the composite FKs (each indexed:
    Postgres does not index the referencing side, and every one of these is a
    join or a cascade check)."""
    out: list = [UniqueConstraint("organization_id", "id", name=f"uq_{table}_org_id")]
    for r in refs:
        name = r.cols[-1]
        out.append(
            ForeignKeyConstraint(
                ["organization_id", *r.cols],
                [f"{r.target}.organization_id"]
                + [f"{r.target}.{c}" for c in r.target_cols],
                name=f"fk_{table}_{name}",
                ondelete=r.ondelete,
            )
        )
        if r.index:
            out.append(Index(f"ix_{table}_{name}", "organization_id", *r.cols))
    out.extend(extra)
    return tuple(out)


def _user_fk(table: str, column: str) -> Column:
    """A plain reference to a user. Users are global, and an actor leaving must
    not take the record of what they did with them."""
    return Column(
        String,
        ForeignKey("users.id", ondelete="SET NULL", name=f"fk_{table}_{column}"),
        nullable=True,
    )


class _FnRow:
    """The columns every Functions table carries (``sort_order`` keeps ``id``
    and ``organization_id`` first and the timestamps last in the DDL)."""

    id = mapped_column(String, primary_key=True, default=_uuid, sort_order=-2)

    @declared_attr
    def organization_id(cls):
        return mapped_column(
            String,
            ForeignKey(
                "organizations.id",
                ondelete="CASCADE",
                name=f"fk_{cls.__tablename__}_organization_id",
            ),
            nullable=False,
            sort_order=-1,
        )

    created_at = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
        sort_order=10,
    )
    updated_at = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
        sort_order=11,
    )


class _Versioned:
    """Optimistic concurrency for rows people edit.

    The mapper's ``version_id_col`` puts ``WHERE version = :seen`` on every ORM
    UPDATE and bumps it, so two people saving the same enquiry can never
    silently overwrite each other — the loser gets ``StaleDataError``, which
    the services turn into ``Conflict``. Services also compare the version a
    client says it edited against the row before writing, for the common case
    where the conflict is between page loads rather than transactions.
    """

    version = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"), sort_order=9
    )

    @declared_attr.directive
    def __mapper_args__(cls):
        return {"version_id_col": cls.version}


def _local_window() -> tuple[Column, Column, Column, Column]:
    """A venue-local plan: date, start, end, and whether the end is tomorrow."""
    return (
        Column(Date, nullable=True),
        Column(Time, nullable=True),
        Column(Time, nullable=True),
        Column(Boolean, nullable=False, default=False, server_default=text("false")),
    )


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


class FnFile(_FnRow, Base):
    """Bytes Functions owns — proposal images, area photos, brand logos.

    Content-addressed (``sha256`` unique per org): an image uploaded twice is
    stored once, a public URL can carry the hash and be cached forever, and the
    importer can copy Orbit's files without ever wiping them. Bytes in the
    column, following ``AppFile``/``UploadedDocument`` — the volumes are a few
    hundred images, and one access-control surface beats two.
    """

    __tablename__ = "fn_files"
    __table_args__ = _tenancy(
        "fn_files",
        extra=(UniqueConstraint("organization_id", "sha256", name="uq_fn_files_sha"),),
    )

    sha256 = Column(String(64), nullable=False)
    filename = Column(String, nullable=True)
    content_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    data = Column(LargeBinary, nullable=False)
    #: Where the bytes came from when not uploaded here (an Orbit storage URL),
    #: so a re-import recognises a file it already has.
    source_ref = Column(String, nullable=True)
    created_by_user_id = _user_fk("fn_files", "created_by_user_id")


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


class FnContact(_FnRow, _Versioned, Base):
    """A customer. Never hard-deleted once it has enquiries or events: archive
    hides it, anonymise (privacy requests) blanks the personal data and keeps
    the financial facts."""

    __tablename__ = "fn_contacts"
    __table_args__ = _tenancy("fn_contacts")

    name = Column(String, nullable=False)
    company = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    anonymised_at = Column(DateTime(timezone=True), nullable=True)
    created_by_user_id = _user_fk("fn_contacts", "created_by_user_id")


class FnContactEmail(_FnRow, Base):
    """An address a contact writes from. Unique per organisation regardless of
    case, so an inbound email resolves to at most one contact."""

    __tablename__ = "fn_contact_emails"
    __table_args__ = _tenancy(
        "fn_contact_emails",
        _Ref("contact_id", "fn_contacts", "CASCADE"),
        extra=(
            Index(
                "uq_fn_contact_emails_org_email",
                "organization_id",
                func.lower(text("email")),
                unique=True,
            ),
        ),
    )

    contact_id = Column(String, nullable=False)
    email = Column(String, nullable=False)
    is_primary = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: How we learnt it: manual | email | web_form | import.
    source = Column(String, nullable=True)


# ---------------------------------------------------------------------------
# Venue functions profile
# ---------------------------------------------------------------------------


class FnVenueArea(_FnRow, _Versioned, Base):
    """A bookable space. ``requires_approval`` false means any staff member may
    approve (and so a customer may confirm) a minimum spend here."""

    __tablename__ = "fn_venue_areas"
    __table_args__ = _tenancy(
        "fn_venue_areas",
        _Ref("venue_id", "venues", "CASCADE"),
        extra=(
            # Lets minimum spends and events reference (venue, area) together,
            # so an area can never be attached to another venue's booking.
            UniqueConstraint(
                "organization_id", "venue_id", "id", name="uq_fn_venue_areas_venue_id"
            ),
        ),
    )

    venue_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    capacity_seated = Column(Integer, nullable=True)
    capacity_standing = Column(Integer, nullable=True)
    #: Booking types this area is offered for (BOOKING_TYPES values).
    booking_types = Column(JSON, nullable=False, default=list)
    requires_approval = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    min_spend_notes = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))


class FnVenueAreaPhoto(_FnRow, Base):
    __tablename__ = "fn_venue_area_photos"
    __table_args__ = _tenancy(
        "fn_venue_area_photos",
        _Ref("area_id", "fn_venue_areas", "CASCADE"),
        _Ref("file_id", "fn_files", "NO ACTION"),
    )

    area_id = Column(String, nullable=False)
    file_id = Column(String, nullable=False)
    caption = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))


class FnVenueFunctionSettings(_FnRow, _Versioned, Base):
    """One row per venue: who approves minimum spends, and what the venue shows.

    ``enabled_fields`` is the one visibility flag per venue x booking type x
    field (Orbit kept a staff and a client flag; they agreed in all 378 rows):
    ``{"seated_dining": {"beverages.bar_tab": true, ...}, ...}``.
    ``menu_links`` is ``[{label, url, kind, description, active, sort}]``.
    """

    __tablename__ = "fn_venue_function_settings"
    __table_args__ = _tenancy(
        "fn_venue_function_settings",
        _Ref("venue_id", "venues", "CASCADE", index=False),
        extra=(
            UniqueConstraint(
                "organization_id",
                "venue_id",
                name="uq_fn_venue_function_settings_venue",
            ),
        ),
    )

    venue_id = Column(String, nullable=False)
    approver_user_id = _user_fk("fn_venue_function_settings", "approver_user_id")
    override_approver_user_id = _user_fk(
        "fn_venue_function_settings", "override_approver_user_id"
    )
    policies = Column(Text, nullable=True)
    menu_links = Column(JSON, nullable=False, default=list)
    enabled_fields = Column(JSON, nullable=False, default=dict)
    #: Who hears about confirmations, client edits and new web leads.
    notify_emails = Column(JSON, nullable=False, default=list)


class FnVenueBrand(_FnRow, _Versioned, Base):
    """The venue's proposal identity and its starting template.

    A proposal records ``based_on_brand_version`` (this row's ``version``) when
    it is started, so "the brand changed since" is answerable.
    ``colours`` ``{primary, accent, panel, text, background}``; ``fonts``
    ``{heading, body}``; ``terms`` and ``inclusions`` are lists of strings;
    ``signoff`` ``{title, body, contact}``; ``style`` holds the remaining
    free-form tokens a template may use (image radius, cover/closing
    backgrounds, ...).
    """

    __tablename__ = "fn_venue_brands"
    __table_args__ = _tenancy(
        "fn_venue_brands",
        _Ref("venue_id", "venues", "CASCADE", index=False),
        _Ref("logo_file_id", "fn_files", "NO ACTION"),
        _Ref("cover_file_id", "fn_files", "NO ACTION"),
        _Ref("closing_file_id", "fn_files", "NO ACTION"),
        extra=(
            UniqueConstraint(
                "organization_id", "venue_id", name="uq_fn_venue_brands_venue"
            ),
        ),
    )

    venue_id = Column(String, nullable=False)
    logo_file_id = Column(String, nullable=True)
    cover_file_id = Column(String, nullable=True)
    closing_file_id = Column(String, nullable=True)
    wordmark = Column(String, nullable=True)
    tagline = Column(String, nullable=True)
    colours = Column(JSON, nullable=False, default=dict)
    fonts = Column(JSON, nullable=False, default=dict)
    terms = Column(JSON, nullable=False, default=list)
    inclusions = Column(JSON, nullable=False, default=list)
    signoff = Column(JSON, nullable=False, default=dict)
    style = Column(JSON, nullable=False, default=dict)
    footer_contact = Column(Text, nullable=True)
    valid_days = Column(Integer, nullable=False, default=14, server_default=text("14"))
    template_html = Column(Text, nullable=True)
    template_css = Column(Text, nullable=True)


class FnProposalImage(_FnRow, _Versioned, Base):
    """The venue's image library for proposals."""

    __tablename__ = "fn_proposal_images"
    __table_args__ = _tenancy(
        "fn_proposal_images",
        _Ref("venue_id", "venues", "CASCADE"),
        _Ref("file_id", "fn_files", "NO ACTION"),
    )

    venue_id = Column(String, nullable=False)
    file_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    caption = Column(String, nullable=True)
    tags = Column(JSON, nullable=False, default=list)
    created_by_user_id = _user_fk("fn_proposal_images", "created_by_user_id")


class FnReplyTemplate(_FnRow, _Versioned, Base):
    """A canned reply. ``venue_id``/``booking_type`` NULL = applies to all."""

    __tablename__ = "fn_reply_templates"
    __table_args__ = _tenancy(
        "fn_reply_templates",
        _Ref("venue_id", "venues", "CASCADE"),
        extra=(
            _check("fn_reply_templates", "booking_type", BOOKING_TYPES),
            _check("fn_reply_templates", "stage", REPLY_TEMPLATE_STAGES),
        ),
    )

    venue_id = Column(String, nullable=True)
    booking_type = Column(String, nullable=True)
    stage = Column(String, nullable=False)
    name = Column(String, nullable=False)
    subject = Column(String, nullable=True)
    body_html = Column(Text, nullable=False, default="")
    active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))


class FnPreorderTemplate(_FnRow, _Versioned, Base):
    """A pre-order menu a venue offers. Retired with ``active = false`` —
    menus already built from it keep pointing at it."""

    __tablename__ = "fn_preorder_templates"
    __table_args__ = _tenancy(
        "fn_preorder_templates",
        _Ref("venue_id", "venues", "CASCADE"),
    )

    venue_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    is_set_menu = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    price_per_person_cents = Column(Integer, nullable=True)
    #: The LoadedHub menu this was cloned from, if any.
    loaded_menu_id = Column(String, nullable=True)
    active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))


class FnPreorderTemplateItem(_FnRow, Base):
    """A dish (or a section header) on a template.

    Set-menu dishes carry no price (``unit_price_cents`` NULL) — the menu's
    value is guests x price per person — and a ``default_percent`` of the
    guests expected to choose it within its section. A la carte dishes are
    priced. LoadedHub ids are text references, not FKs: Loaded owns them.
    """

    __tablename__ = "fn_preorder_template_items"
    __table_args__ = _tenancy(
        "fn_preorder_template_items",
        _Ref("template_id", "fn_preorder_templates", "CASCADE"),
        extra=(_check("fn_preorder_template_items", "kind", MENU_ITEM_KINDS),),
    )

    template_id = Column(String, nullable=False)
    kind = Column(String, nullable=False, default="item")
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    unit_price_cents = Column(Integer, nullable=True)
    default_percent = Column(Integer, nullable=True)
    dietaries = Column(JSON, nullable=False, default=list)
    loaded_recipe_id = Column(String, nullable=True)
    loaded_stock_item_id = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))


# ---------------------------------------------------------------------------
# Web forms
# ---------------------------------------------------------------------------


class FnWebForm(_FnRow, _Versioned, Base):
    """An embeddable enquiry form. ``slug`` is globally unique: ``/f/<slug>``
    is a public route with no organisation in it."""

    __tablename__ = "fn_web_forms"
    __table_args__ = _tenancy(
        "fn_web_forms",
        _Ref("venue_id", "venues", "NO ACTION"),
        extra=(
            UniqueConstraint("slug", name="uq_fn_web_forms_slug"),
            _check("fn_web_forms", "booking_type", BOOKING_TYPES),
        ),
    )

    venue_id = Column(String, nullable=False)
    slug = Column(String, nullable=False)
    name = Column(String, nullable=False)
    active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    #: Theme tokens (colours, radius, font) the embed applies.
    theme = Column(JSON, nullable=False, default=dict)
    intro_html = Column(Text, nullable=True)
    submit_label = Column(String, nullable=False, default="Send enquiry")
    success_message = Column(Text, nullable=True)
    #: Shadow mode emails each submission here (Orbit's behaviour), with this
    #: subject ("{name} — {date}" style placeholders) and Reply-To taken from
    #: the answer to the named field.
    recipient_email = Column(String, nullable=True)
    subject_template = Column(String, nullable=True)
    reply_to_field_key = Column(String, nullable=True)
    honeypot_field = Column(String, nullable=False, default="website")
    #: Booking type given to enquiries this form creates, when it asks none.
    booking_type = Column(String, nullable=True)


class FnWebFormField(_FnRow, Base):
    __tablename__ = "fn_web_form_fields"
    __table_args__ = _tenancy(
        "fn_web_form_fields",
        _Ref("form_id", "fn_web_forms", "CASCADE"),
        extra=(
            UniqueConstraint("form_id", "key", name="uq_fn_web_form_fields_key"),
            _check("fn_web_form_fields", "type", FORM_FIELD_TYPES),
        ),
    )

    form_id = Column(String, nullable=False)
    key = Column(String, nullable=False)
    label = Column(String, nullable=False)
    type = Column(String, nullable=False)
    required = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    options = Column(JSON, nullable=True)
    placeholder = Column(String, nullable=True)
    help_text = Column(String, nullable=True)
    #: Which enquiry/contact field this answer fills when the form creates an
    #: enquiry (e.g. "guests", "preferred_date", "booking_type", "client_note").
    maps_to = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))


class FnFormSubmission(_FnRow, Base):
    """One submission, kept verbatim with where the lead came from."""

    __tablename__ = "fn_form_submissions"
    __table_args__ = _tenancy(
        "fn_form_submissions",
        _Ref("form_id", "fn_web_forms", "NO ACTION"),
        _Ref("venue_id", "venues", "NO ACTION"),
        _Ref("enquiry_id", "fn_enquiries", "NO ACTION"),
        extra=(
            _check("fn_form_submissions", "delivery_status", FORM_DELIVERY_STATUSES),
            Index(
                "ix_fn_form_submissions_submitted", "organization_id", "submitted_at"
            ),
        ),
    )

    form_id = Column(String, nullable=False)
    venue_id = Column(String, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    submitted_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    utm_source = Column(String, nullable=True)
    utm_medium = Column(String, nullable=True)
    utm_campaign = Column(String, nullable=True)
    utm_term = Column(String, nullable=True)
    utm_content = Column(String, nullable=True)
    gclid = Column(String, nullable=True)
    gbraid = Column(String, nullable=True)
    wbraid = Column(String, nullable=True)
    referrer = Column(Text, nullable=True)
    landing_page = Column(Text, nullable=True)
    ref_code = Column(String, nullable=True)
    #: Salted hash, never the address; user agent is cleared by retention.
    ip_hash = Column(String, nullable=True)
    user_agent = Column(Text, nullable=True)
    #: Set (and the submission kept out of the pipeline) when a spam check hit.
    spam_reason = Column(String, nullable=True)
    delivery_status = Column(String, nullable=False, default="pending")
    delivery_error = Column(Text, nullable=True)
    delivery_message_id = Column(String, nullable=True)
    #: The enquiry this submission created (live mode only).
    enquiry_id = Column(String, nullable=True)


# ---------------------------------------------------------------------------
# Enquiries
# ---------------------------------------------------------------------------


class FnEnquiry(_FnRow, _Versioned, Base):
    """A request for a function. Its stage and next action are derived
    (status.py) from closures, events, proposal sends and linked mail."""

    __tablename__ = "fn_enquiries"
    __table_args__ = _tenancy(
        "fn_enquiries",
        _Ref("contact_id", "fn_contacts", "NO ACTION"),
        _Ref("venue_id", "venues", "NO ACTION"),
        extra=(
            _check("fn_enquiries", "booking_type", BOOKING_TYPES),
            _check("fn_enquiries", "source", ENQUIRY_SOURCES),
            Index("ix_fn_enquiries_created", "organization_id", "created_at"),
        ),
    )

    contact_id = Column(String, nullable=False)
    #: NULL = a group-wide enquiry not yet placed at a venue.
    venue_id = Column(String, nullable=True)
    booking_type = Column(String, nullable=True)
    subject = Column(String, nullable=True)
    guests = Column(Integer, nullable=True)
    preferred_date_start = Column(Date, nullable=True)
    preferred_date_end = Column(Date, nullable=True)
    preferred_start_time = Column(Time, nullable=True)
    preferred_end_time = Column(Time, nullable=True)
    preferred_ends_next_day = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    source = Column(String, nullable=False, default="manual")
    utm_source = Column(String, nullable=True)
    utm_medium = Column(String, nullable=True)
    utm_campaign = Column(String, nullable=True)
    utm_term = Column(String, nullable=True)
    utm_content = Column(String, nullable=True)
    gclid = Column(String, nullable=True)
    referrer = Column(Text, nullable=True)
    landing_page = Column(Text, nullable=True)
    owner_user_id = _user_fk("fn_enquiries", "owner_user_id")
    #: The customer asked us to come back on this (venue-local) date. Parks
    #: every other next action until then.
    follow_up_date = Column(Date, nullable=True)
    follow_up_reason = Column(Text, nullable=True)
    client_note = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_by_user_id = _user_fk("fn_enquiries", "created_by_user_id")
    #: Who created it when no user did ("Norm", "web form", "importer").
    created_by_label = Column(String, nullable=True)


class FnEnquiryClosure(_FnRow, Base):
    """Closed as lost, or reopened — each with its reason. The latest row wins;
    the rows together are the enquiry's open/close history."""

    __tablename__ = "fn_enquiry_closures"
    __table_args__ = _tenancy(
        "fn_enquiry_closures",
        _Ref("enquiry_id", "fn_enquiries", "CASCADE"),
        extra=(_check("fn_enquiry_closures", "kind", ENQUIRY_CLOSURE_KINDS),),
    )

    enquiry_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    reason = Column(Text, nullable=False)
    actor_user_id = _user_fk("fn_enquiry_closures", "actor_user_id")
    actor_label = Column(String, nullable=True)
    occurred_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Minimum spends — one model for enquiry and event
# ---------------------------------------------------------------------------


class FnMinSpend(_FnRow, _Versioned, Base):
    """A minimum-spend option offered to a customer.

    Lives on an enquiry while it is being offered and gains ``event_id`` when
    the customer confirms it (min_spends.confirm_option) — the same row, never
    a copy. An event booked without an enquiry (imported history) may carry
    one with ``enquiry_id`` NULL. State (proposed/approved/confirmed/declined/
    withdrawn) is derived from this row plus its approval and response facts.
    """

    __tablename__ = "fn_min_spends"
    __table_args__ = _tenancy(
        "fn_min_spends",
        _Ref("enquiry_id", "fn_enquiries", "NO ACTION"),
        _Ref("event_id", "fn_events", "NO ACTION", index=False),
        _Ref("venue_id", "venues", "NO ACTION"),
        _Ref(
            ("venue_id", "area_id"),
            "fn_venue_areas",
            "NO ACTION",
            target_cols=("venue_id", "id"),
        ),
        extra=(
            CheckConstraint(
                "enquiry_id IS NOT NULL OR event_id IS NOT NULL",
                name="ck_fn_min_spends_parent",
            ),
            CheckConstraint("amount_cents >= 0", name="ck_fn_min_spends_amount"),
            # One booked option per event: the confirmation is a single fact.
            Index(
                "uq_fn_min_spends_event",
                "organization_id",
                "event_id",
                unique=True,
                postgresql_where=text("event_id IS NOT NULL"),
            ),
        ),
    )

    enquiry_id = Column(String, nullable=True)
    event_id = Column(String, nullable=True)
    venue_id = Column(String, nullable=False)
    area_id = Column(String, nullable=True)
    label = Column(String, nullable=True)
    amount_cents = Column(Integer, nullable=False)
    event_date, start_time, end_time, ends_next_day = _local_window()
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))
    withdrawn_at = Column(DateTime(timezone=True), nullable=True)
    withdrawn_by_user_id = _user_fk("fn_min_spends", "withdrawn_by_user_id")
    withdraw_reason = Column(Text, nullable=True)
    created_by_user_id = _user_fk("fn_min_spends", "created_by_user_id")
    created_by_label = Column(String, nullable=True)


class FnMinSpendApproval(_FnRow, Base):
    """A manager's approve / override / revoke. Approval means "OK to offer"
    THESE terms, nothing more. The latest row wins — and only while
    ``terms_key`` still matches the option (status.terms_key): an option
    whose amount, date, times or area moved is no longer approved, whatever
    path moved it. ``amount_cents`` keeps the log readable; for imported
    history with no ``terms_key`` it is the term checked."""

    __tablename__ = "fn_min_spend_approvals"
    __table_args__ = _tenancy(
        "fn_min_spend_approvals",
        _Ref("min_spend_id", "fn_min_spends", "CASCADE"),
        extra=(_check("fn_min_spend_approvals", "kind", APPROVAL_KINDS),),
    )

    min_spend_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    actor_user_id = _user_fk("fn_min_spend_approvals", "actor_user_id")
    actor_label = Column(String, nullable=True)
    #: Required for override; the reason for a revoke.
    reason = Column(Text, nullable=True)
    amount_cents = Column(Integer, nullable=True)
    #: The option's terms this fact was made on (status.terms_key).
    terms_key = Column(String, nullable=True)
    #: True when the system revoked it because the option changed after
    #: approval — no manager acted.
    automatic = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    occurred_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )


class FnMinSpendResponse(_FnRow, Base):
    """The customer's confirm or decline — on the quote page, or recorded by
    staff from an email / call (``recorded_by_user_id`` set)."""

    __tablename__ = "fn_min_spend_responses"
    __table_args__ = _tenancy(
        "fn_min_spend_responses",
        _Ref("min_spend_id", "fn_min_spends", "CASCADE"),
        _Ref("public_token_id", "fn_public_tokens", "NO ACTION"),
        extra=(
            _check("fn_min_spend_responses", "kind", RESPONSE_KINDS),
            _check("fn_min_spend_responses", "channel", RESPONSE_CHANNELS),
        ),
    )

    min_spend_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    channel = Column(String, nullable=False)
    recorded_by_user_id = _user_fk("fn_min_spend_responses", "recorded_by_user_id")
    customer_email = Column(String, nullable=True)
    public_token_id = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    occurred_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class FnEvent(_FnRow, _Versioned, Base):
    """A booked function. Status is derived from section confirmations and the
    change log (status.py); ``cancelled_at`` is the cancellation fact.

    One live (uncancelled) event per enquiry, enforced by a partial unique
    index — the database's half of confirm_option's exactly-once guarantee.
    """

    __tablename__ = "fn_events"
    __table_args__ = _tenancy(
        "fn_events",
        _Ref("enquiry_id", "fn_enquiries", "NO ACTION"),
        _Ref("contact_id", "fn_contacts", "NO ACTION"),
        _Ref("invoice_contact_id", "fn_contacts", "NO ACTION"),
        _Ref("venue_id", "venues", "NO ACTION", index=False),
        # The plan image is decoration, not a record: if its file goes, the
        # event keeps going — only this column is nulled.
        _Ref("table_plan_file_id", "fn_files", "SET NULL (table_plan_file_id)"),
        _Ref(
            ("venue_id", "area_id"),
            "fn_venue_areas",
            "NO ACTION",
            target_cols=("venue_id", "id"),
        ),
        extra=(
            _check("fn_events", "booking_type", BOOKING_TYPES),
            _check("fn_events", "created_via", EVENT_CREATED_VIA),
            _check("fn_events", "confirmation_channel", RESPONSE_CHANNELS),
            _check("fn_events", "food_timing_type", FOOD_TIMING_TYPES),
            CheckConstraint("bar_tab_cents >= 0", name="ck_fn_events_bar_tab"),
            Index(
                "ix_fn_events_venue_date", "organization_id", "venue_id", "event_date"
            ),
            Index(
                "uq_fn_events_live_enquiry",
                "organization_id",
                "enquiry_id",
                unique=True,
                postgresql_where=text(
                    "enquiry_id IS NOT NULL AND cancelled_at IS NULL"
                ),
            ),
        ),
    )

    enquiry_id = Column(String, nullable=True)
    contact_id = Column(String, nullable=False)
    invoice_contact_id = Column(String, nullable=True)
    venue_id = Column(String, nullable=False)
    area_id = Column(String, nullable=True)
    booking_type = Column(String, nullable=True)
    name = Column(String, nullable=False)
    event_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    ends_next_day = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    guests = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    special_instructions = Column(Text, nullable=True)
    #: Sensitive: kept out of logs and agent summaries unless asked.
    allergies = Column(Text, nullable=True)
    # Beverages
    bar_tab_cents = Column(Integer, nullable=False, default=0, server_default=text("0"))
    bar_tab_authorised_people = Column(Text, nullable=True)
    bar_tab_restrictions = Column(Text, nullable=True)
    bar_tab_notes = Column(Text, nullable=True)
    arrival_drinks = Column(Text, nullable=True)
    table_ordering = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    table_ordering_limit_cents = Column(Integer, nullable=True)
    # Payment
    payment_method = Column(String, nullable=True)
    deposit_cents = Column(Integer, nullable=True)
    deposit_status = Column(String, nullable=True)
    # Food and timings
    food_timing_type = Column(String, nullable=True)
    food_time = Column(Time, nullable=True)
    food_end_time = Column(Time, nullable=True)
    food_notes = Column(Text, nullable=True)
    arrival_time = Column(Time, nullable=True)
    seated_time = Column(Time, nullable=True)
    timing_preference = Column(Text, nullable=True)
    timing_notes = Column(Text, nullable=True)
    table_plan = Column(Text, nullable=True)
    #: The table-plan image (fn_files), beside the text plan.
    table_plan_file_id = Column(String, nullable=True)
    # How it came to be
    created_via = Column(String, nullable=False, default="direct")
    confirmation_channel = Column(String, nullable=True)
    created_by_user_id = _user_fk("fn_events", "created_by_user_id")
    created_by_label = Column(String, nullable=True)
    # Cancellation (a fact; reinstating clears it and is logged)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancel_reason = Column(Text, nullable=True)
    cancelled_by_user_id = _user_fk("fn_events", "cancelled_by_user_id")


def _event_child(table: str, extra: tuple = ()) -> tuple:
    return _tenancy(table, _Ref("event_id", "fn_events", "CASCADE"), extra=extra)


class FnEventTiming(_FnRow, Base):
    """A run-sheet line. ``at_time`` when it has a clock time; ``label`` for
    the rest ("after speeches")."""

    __tablename__ = "fn_event_timings"
    __table_args__ = _event_child("fn_event_timings")

    event_id = Column(String, nullable=False)
    at_time = Column(Time, nullable=True)
    ends_next_day = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    label = Column(String, nullable=True)
    activity = Column(Text, nullable=False, default="")
    is_heading = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))


class FnEventSecurity(_FnRow, Base):
    __tablename__ = "fn_event_security"
    __table_args__ = _event_child("fn_event_security")

    event_id = Column(String, nullable=False)
    name = Column(String, nullable=True)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    ends_next_day = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))


class FnEventEntertainment(_FnRow, Base):
    __tablename__ = "fn_event_entertainment"
    __table_args__ = _event_child(
        "fn_event_entertainment",
        extra=(_check("fn_event_entertainment", "kind", ENTERTAINMENT_KINDS),),
    )

    event_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    name = Column(String, nullable=True)
    link = Column(Text, nullable=True)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    ends_next_day = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))


class FnEventAv(_FnRow, Base):
    __tablename__ = "fn_event_av"
    __table_args__ = _event_child(
        "fn_event_av", extra=(_check("fn_event_av", "kind", AV_KINDS),)
    )

    event_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))


class FnEventSectionConfirmation(_FnRow, Base):
    """A section (details / beverages / food) confirmed by the client, skipped
    by staff with a note, or reopened. The latest row per section wins; all
    three confirmed-or-skipped makes the event confirmed."""

    __tablename__ = "fn_event_section_confirmations"
    __table_args__ = _event_child(
        "fn_event_section_confirmations",
        extra=(
            _check("fn_event_section_confirmations", "section", EVENT_SECTIONS),
            _check(
                "fn_event_section_confirmations", "kind", SECTION_CONFIRMATION_KINDS
            ),
            _check("fn_event_section_confirmations", "actor_kind", ACTOR_KINDS),
        ),
    )

    event_id = Column(String, nullable=False)
    section = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    actor_kind = Column(String, nullable=False)
    #: The client's verified portal email, when the client acted.
    client_email = Column(String, nullable=True)
    actor_user_id = _user_fk("fn_event_section_confirmations", "actor_user_id")
    actor_label = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    occurred_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )


class FnEventChange(_FnRow, Base):
    """The event's log: created, edited (with a field diff), portal link sent,
    cancelled, reinstated. Status reads two facts from it — a ``link_sent`` row
    (sent to client) and an ``edited`` row by the client (in progress / edited
    since confirmed).

    ``changes`` is ``{field: [old, new]}``; ``after_confirmation`` marks edits
    made once all three sections were confirmed, with the editor's ``note``.
    """

    __tablename__ = "fn_event_changes"
    __table_args__ = _event_child(
        "fn_event_changes",
        extra=(
            _check("fn_event_changes", "kind", EVENT_CHANGE_KINDS),
            _check("fn_event_changes", "actor_kind", ACTOR_KINDS),
            _check("fn_event_changes", "section", EVENT_SECTIONS),
            Index(
                "ix_fn_event_changes_kind",
                "organization_id",
                "event_id",
                "kind",
                "occurred_at",
            ),
        ),
    )

    event_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    actor_kind = Column(String, nullable=False)
    actor_user_id = _user_fk("fn_event_changes", "actor_user_id")
    client_email = Column(String, nullable=True)
    actor_label = Column(String, nullable=True)
    section = Column(String, nullable=True)
    changes = Column(JSON, nullable=True)
    after_confirmation = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    note = Column(Text, nullable=True)
    occurred_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Pre-order menus
# ---------------------------------------------------------------------------


class FnPreorderMenu(_FnRow, _Versioned, Base):
    """A food pre-order. Built on the enquiry; gains ``event_id`` when the
    booking is confirmed — linked, not copied. An event booked without an
    enquiry carries its menus directly.

    Value (money.menu_food_cents): a set menu is ``price_per_person_cents x
    guest_count`` plus any priced extras; a la carte is the sum of its priced
    items.
    """

    __tablename__ = "fn_preorder_menus"
    __table_args__ = _tenancy(
        "fn_preorder_menus",
        _Ref("enquiry_id", "fn_enquiries", "NO ACTION"),
        _Ref("event_id", "fn_events", "NO ACTION"),
        _Ref("venue_id", "venues", "NO ACTION"),
        _Ref("template_id", "fn_preorder_templates", "NO ACTION"),
        extra=(
            CheckConstraint(
                "enquiry_id IS NOT NULL OR event_id IS NOT NULL",
                name="ck_fn_preorder_menus_parent",
            ),
        ),
    )

    enquiry_id = Column(String, nullable=True)
    event_id = Column(String, nullable=True)
    venue_id = Column(String, nullable=False)
    template_id = Column(String, nullable=True)
    name = Column(String, nullable=False)
    is_set_menu = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    price_per_person_cents = Column(Integer, nullable=True)
    guest_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))


class FnPreorderMenuItem(_FnRow, Base):
    """A line on a pre-order: a snapshot of the dish when it was added.

    ``template_item_id`` is a reference, deliberately not a foreign key:
    template items are rewritten whenever a venue edits its template, and a
    menu already sent to a customer must not block that or change with it.
    Priced lines (``unit_price_cents`` set) add ``quantity x price``; unpriced
    set-menu lines carry quantities only and are what the default % split
    distributes.
    """

    __tablename__ = "fn_preorder_menu_items"
    __table_args__ = _tenancy(
        "fn_preorder_menu_items",
        _Ref("menu_id", "fn_preorder_menus", "CASCADE"),
        extra=(
            _check("fn_preorder_menu_items", "kind", MENU_ITEM_KINDS),
            CheckConstraint("quantity >= 0", name="ck_fn_preorder_menu_items_quantity"),
        ),
    )

    menu_id = Column(String, nullable=False)
    template_item_id = Column(String, nullable=True)
    kind = Column(String, nullable=False, default="item")
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    quantity = Column(Integer, nullable=False, default=0, server_default=text("0"))
    unit_price_cents = Column(Integer, nullable=True)
    default_percent = Column(Integer, nullable=True)
    dietaries = Column(JSON, nullable=False, default=list)
    loaded_recipe_id = Column(String, nullable=True)
    loaded_stock_item_id = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


class FnProposal(_FnRow, _Versioned, Base):
    """The enquiry's proposal: free HTML + CSS (sanitised by proposals.py,
    rendered in a sandboxed iframe). The numbers stay structured — Confirm /
    Decline come from fn_min_spends, never from this HTML."""

    __tablename__ = "fn_proposals"
    __table_args__ = _tenancy(
        "fn_proposals",
        _Ref("enquiry_id", "fn_enquiries", "CASCADE", index=False),
        extra=(
            UniqueConstraint(
                "organization_id", "enquiry_id", name="uq_fn_proposals_enquiry"
            ),
        ),
    )

    enquiry_id = Column(String, nullable=False)
    html = Column(Text, nullable=False, default="")
    css = Column(Text, nullable=False, default="")
    based_on_brand_version = Column(Integer, nullable=True)
    updated_by_user_id = _user_fk("fn_proposals", "updated_by_user_id")
    updated_by_label = Column(String, nullable=True)


class FnProposalVersion(_FnRow, Base):
    """What a send froze: the HTML, the CSS and the options offered.

    ``options`` is ``[{min_spend_id, label, venue_id, area_id, area_name,
    event_date, start_time, end_time, ends_next_day, amount_cents, food_cents,
    total_cents}]``. ``sent_at`` is set when the outbox confirms delivery; the
    outbox row that carried it points here (``fn_outbox.proposal_version_id``).
    """

    __tablename__ = "fn_proposal_versions"
    __table_args__ = _tenancy(
        "fn_proposal_versions",
        _Ref("proposal_id", "fn_proposals", "CASCADE"),
        _Ref("enquiry_id", "fn_enquiries", "CASCADE"),
        extra=(
            UniqueConstraint(
                "proposal_id", "version_number", name="uq_fn_proposal_versions_number"
            ),
        ),
    )

    proposal_id = Column(String, nullable=False)
    enquiry_id = Column(String, nullable=False)
    version_number = Column(Integer, nullable=False)
    html = Column(Text, nullable=False)
    css = Column(Text, nullable=False, default="")
    options = Column(JSON, nullable=False, default=list)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    sent_by_user_id = _user_fk("fn_proposal_versions", "sent_by_user_id")
    sent_by_label = Column(String, nullable=True)


class FnProposalEvent(_FnRow, Base):
    """A quote went to the customer (``sent``) or they opened it (``viewed``).

    A ``sent`` row is THE fact that makes an enquiry "quoted" — whether it went
    through the outbox, was spotted in Gmail-sent mail carrying our quote link,
    or came from the import."""

    __tablename__ = "fn_proposal_events"
    __table_args__ = _tenancy(
        "fn_proposal_events",
        _Ref("enquiry_id", "fn_enquiries", "CASCADE", index=False),
        _Ref("proposal_version_id", "fn_proposal_versions", "CASCADE"),
        _Ref("outbox_id", "fn_outbox", "NO ACTION"),
        extra=(
            _check("fn_proposal_events", "kind", PROPOSAL_EVENT_KINDS),
            _check("fn_proposal_events", "source", PROPOSAL_EVENT_SOURCES),
            Index(
                "ix_fn_proposal_events_enquiry_kind",
                "organization_id",
                "enquiry_id",
                "kind",
            ),
        ),
    )

    enquiry_id = Column(String, nullable=False)
    proposal_version_id = Column(String, nullable=True)
    kind = Column(String, nullable=False)
    source = Column(String, nullable=False)
    outbox_id = Column(String, nullable=True)
    account_id = Column(String, nullable=True)
    gmail_message_id = Column(String, nullable=True)
    actor_user_id = _user_fk("fn_proposal_events", "actor_user_id")
    actor_label = Column(String, nullable=True)
    occurred_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Mailboxes
# ---------------------------------------------------------------------------


class FnMailboxAccount(_FnRow, _Versioned, Base):
    """A venue's Gmail mailbox. Tokens live on the shared (encrypted)
    ``connector_configs`` row; this holds the sync cursor, the lease that keeps
    two workers off one mailbox, and its health. Whether it may SEND follows
    the organisation's ``functions_mode`` — there is no flag here."""

    __tablename__ = "fn_mailbox_accounts"
    __table_args__ = _tenancy(
        "fn_mailbox_accounts",
        _Ref("venue_id", "venues", "NO ACTION"),
        extra=(
            _check("fn_mailbox_accounts", "status", MAILBOX_STATUSES),
            Index(
                "uq_fn_mailbox_accounts_email",
                "organization_id",
                func.lower(text("google_email")),
                unique=True,
            ),
            Index("ix_fn_mailbox_accounts_lease", "status", "lease_until"),
        ),
    )

    venue_id = Column(String, nullable=False)
    connection_id = Column(
        String,
        ForeignKey(
            "connector_configs.id",
            ondelete="SET NULL",
            name="fk_fn_mailbox_accounts_connection_id",
        ),
        nullable=True,
    )
    google_email = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    #: Gmail history ids exceed 2^31.
    history_id = Column(BigInteger, nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    lease_owner = Column(String, nullable=True)
    status = Column(String, nullable=False, default="needs_reconnect")
    last_ok_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    consecutive_failures = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: When the reconnect email went out, so it goes out once.
    reconnect_notified_at = Column(DateTime(timezone=True), nullable=True)
    signature_html = Column(Text, nullable=True)


class FnMailboxThread(_FnRow, Base):
    """A mirrored thread: rollups recomputed from its messages by the sync."""

    __tablename__ = "fn_mailbox_threads"
    __table_args__ = _tenancy(
        "fn_mailbox_threads",
        _Ref("account_id", "fn_mailbox_accounts", "CASCADE", index=False),
        extra=(
            UniqueConstraint(
                "account_id", "gmail_thread_id", name="uq_fn_mailbox_threads_gmail"
            ),
            Index(
                "ix_fn_mailbox_threads_recent",
                "organization_id",
                "account_id",
                "last_message_at",
            ),
        ),
    )

    account_id = Column(String, nullable=False)
    gmail_thread_id = Column(String, nullable=False)
    subject = Column(String, nullable=True)
    snippet = Column(Text, nullable=True)
    last_message_at = Column(DateTime(timezone=True), nullable=True)
    last_from_email = Column(String, nullable=True)
    last_from_name = Column(String, nullable=True)
    message_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    unread_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    label_ids = Column(JSON, nullable=False, default=list)


class FnMailboxMessage(_FnRow, Base):
    """One mirrored message — metadata and snippet only; bodies are fetched
    live. These rows are the mail facts status.py reads (``is_inbound``,
    ``is_internal``, ``is_auto_reply``, ``internal_date``)."""

    __tablename__ = "fn_mailbox_messages"
    __table_args__ = _tenancy(
        "fn_mailbox_messages",
        _Ref("account_id", "fn_mailbox_accounts", "CASCADE", index=False),
        _Ref("thread_id", "fn_mailbox_threads", "CASCADE"),
        extra=(
            UniqueConstraint(
                "account_id", "gmail_message_id", name="uq_fn_mailbox_messages_gmail"
            ),
            Index(
                "ix_fn_mailbox_messages_thread_date",
                "account_id",
                "gmail_thread_id",
                "internal_date",
            ),
            Index("ix_fn_mailbox_messages_rfc822", "rfc822_message_id"),
            # The reply facts status.py derives (last inbound / outbound per
            # linked thread), readable from the index alone.
            Index(
                "ix_fn_mailbox_messages_reply_facts",
                "organization_id",
                "account_id",
                "gmail_thread_id",
                "is_inbound",
                "internal_date",
                postgresql_where=text(
                    "is_internal IS false AND is_auto_reply IS false"
                ),
            ),
        ),
    )

    account_id = Column(String, nullable=False)
    thread_id = Column(String, nullable=False)
    gmail_message_id = Column(String, nullable=False)
    gmail_thread_id = Column(String, nullable=False)
    rfc822_message_id = Column(String, nullable=True)
    internal_date = Column(DateTime(timezone=True), nullable=False)
    from_email = Column(String, nullable=True)
    from_name = Column(String, nullable=True)
    to_emails = Column(JSON, nullable=False, default=list)
    cc_emails = Column(JSON, nullable=False, default=list)
    reply_to_email = Column(String, nullable=True)
    subject = Column(String, nullable=True)
    snippet = Column(Text, nullable=True)
    label_ids = Column(JSON, nullable=False, default=list)
    is_inbound = Column(Boolean, nullable=False)
    #: Between our own people — neither a customer message nor a reply to one.
    is_internal = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: An autoresponder (either direction) — never counts as a reply.
    is_auto_reply = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    unread = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    has_attachments = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class FnMailboxLabel(_FnRow, Base):
    """A Gmail label, and which folder (if any) it stands for — the
    ``Actioned - …`` labels staff already use."""

    __tablename__ = "fn_mailbox_labels"
    __table_args__ = _tenancy(
        "fn_mailbox_labels",
        _Ref("account_id", "fn_mailbox_accounts", "CASCADE", index=False),
        extra=(
            UniqueConstraint(
                "account_id", "gmail_label_id", name="uq_fn_mailbox_labels_gmail"
            ),
            _check("fn_mailbox_labels", "folder", FOLDERS),
        ),
    )

    account_id = Column(String, nullable=False)
    gmail_label_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    #: system | user
    label_type = Column(String, nullable=True)
    folder = Column(String, nullable=True)


class FnMailboxRetry(_FnRow, Base):
    """A sync item that keeps failing, parked so it neither blocks the cursor
    nor is forgotten."""

    __tablename__ = "fn_mailbox_retry"
    __table_args__ = _tenancy(
        "fn_mailbox_retry",
        _Ref("account_id", "fn_mailbox_accounts", "CASCADE", index=False),
        extra=(
            UniqueConstraint(
                "account_id", "kind", "ref", name="uq_fn_mailbox_retry_ref"
            ),
        ),
    )

    account_id = Column(String, nullable=False)
    #: message | thread | history | label
    kind = Column(String, nullable=False)
    ref = Column(String, nullable=False)
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    last_error = Column(Text, nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    parked_at = Column(DateTime(timezone=True), nullable=True)


class FnThreadLink(_FnRow, Base):
    """A mail thread attached to an enquiry or an event.

    Keyed by ``(account_id, gmail_thread_id)`` with no FK to the mirror, so a
    link can exist before (or after) the thread is mirrored and import order
    never matters. Unlinking is soft (``unlinked_at``), and only live links
    count as facts."""

    __tablename__ = "fn_thread_links"
    __table_args__ = _tenancy(
        "fn_thread_links",
        _Ref("account_id", "fn_mailbox_accounts", "NO ACTION", index=False),
        _Ref("enquiry_id", "fn_enquiries", "NO ACTION"),
        _Ref("event_id", "fn_events", "NO ACTION"),
        extra=(
            CheckConstraint(
                "num_nonnulls(enquiry_id, event_id) = 1",
                name="ck_fn_thread_links_target",
            ),
            Index(
                "ix_fn_thread_links_thread",
                "organization_id",
                "account_id",
                "gmail_thread_id",
            ),
            Index(
                "uq_fn_thread_links_live_enquiry",
                "organization_id",
                "account_id",
                "gmail_thread_id",
                "enquiry_id",
                unique=True,
                postgresql_where=text("unlinked_at IS NULL AND enquiry_id IS NOT NULL"),
            ),
            Index(
                "uq_fn_thread_links_live_event",
                "organization_id",
                "account_id",
                "gmail_thread_id",
                "event_id",
                unique=True,
                postgresql_where=text("unlinked_at IS NULL AND event_id IS NOT NULL"),
            ),
        ),
    )

    account_id = Column(String, nullable=False)
    gmail_thread_id = Column(String, nullable=False)
    enquiry_id = Column(String, nullable=True)
    event_id = Column(String, nullable=True)
    linked_by_user_id = _user_fk("fn_thread_links", "linked_by_user_id")
    linked_by_label = Column(String, nullable=True)
    linked_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    unlinked_at = Column(DateTime(timezone=True), nullable=True)
    unlinked_by_user_id = _user_fk("fn_thread_links", "unlinked_by_user_id")


class FnThreadFolder(_FnRow, Base):
    """The thread's one folder (enquiry / reservation / other), mirrored to the
    venue's ``Actioned - …`` Gmail label."""

    __tablename__ = "fn_thread_folders"
    __table_args__ = _tenancy(
        "fn_thread_folders",
        _Ref("account_id", "fn_mailbox_accounts", "NO ACTION", index=False),
        extra=(
            UniqueConstraint(
                "organization_id",
                "account_id",
                "gmail_thread_id",
                name="uq_fn_thread_folders_thread",
            ),
            _check("fn_thread_folders", "folder", FOLDERS),
        ),
    )

    account_id = Column(String, nullable=False)
    gmail_thread_id = Column(String, nullable=False)
    folder = Column(String, nullable=False)
    set_by_user_id = _user_fk("fn_thread_folders", "set_by_user_id")
    set_by_label = Column(String, nullable=True)
    set_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    gmail_synced_at = Column(DateTime(timezone=True), nullable=True)
    gmail_sync_error = Column(Text, nullable=True)


class FnOutbox(_FnRow, Base):
    """Every send through a venue mailbox, written and committed BEFORE Gmail
    is called. The idempotency key makes a double-click or retry one send; our
    own Message-ID lets the reconciler find a send whose response was lost."""

    __tablename__ = "fn_outbox"
    __table_args__ = _tenancy(
        "fn_outbox",
        _Ref("account_id", "fn_mailbox_accounts", "NO ACTION"),
        _Ref("enquiry_id", "fn_enquiries", "NO ACTION"),
        _Ref("event_id", "fn_events", "NO ACTION"),
        _Ref("proposal_version_id", "fn_proposal_versions", "NO ACTION"),
        _Ref("email_draft_id", "fn_email_drafts", "NO ACTION"),
        extra=(
            UniqueConstraint(
                "organization_id", "idempotency_key", name="uq_fn_outbox_idempotency"
            ),
            UniqueConstraint("rfc822_message_id", name="uq_fn_outbox_rfc822"),
            _check("fn_outbox", "status", OUTBOX_STATUSES),
            _check("fn_outbox", "purpose", OUTBOX_PURPOSES),
            Index("ix_fn_outbox_status", "status", "created_at"),
        ),
    )

    idempotency_key = Column(String, nullable=False)
    rfc822_message_id = Column(String, nullable=False)
    account_id = Column(String, nullable=False)
    #: The thread replied on (NULL for a new conversation until sent).
    gmail_thread_id = Column(String, nullable=True)
    in_reply_to = Column(String, nullable=True)
    references_header = Column(Text, nullable=True)
    to_emails = Column(JSON, nullable=False, default=list)
    cc_emails = Column(JSON, nullable=False, default=list)
    bcc_emails = Column(JSON, nullable=False, default=list)
    subject = Column(String, nullable=False, default="")
    body_html = Column(Text, nullable=False, default="")
    status = Column(String, nullable=False, default="sending")
    purpose = Column(String, nullable=False, default="reply")
    gmail_message_id = Column(String, nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    error = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    #: The audited override: why a reply quoting prices went out on an
    #: enquiry with no sent proposal (the pricing check warns, never blocks).
    price_override_reason = Column(Text, nullable=True)
    enquiry_id = Column(String, nullable=True)
    event_id = Column(String, nullable=True)
    proposal_version_id = Column(String, nullable=True)
    email_draft_id = Column(String, nullable=True)
    created_by_user_id = _user_fk("fn_outbox", "created_by_user_id")
    created_by_label = Column(String, nullable=True)


class FnEmailDraft(_FnRow, _Versioned, Base):
    """A draft written in Norm (by staff or the agent).

    One owner at a time: ``owner = 'norm'`` is editable here; ``'gmail'`` means
    it was handed to the venue mailbox as ``gmail_draft_id`` and the Norm copy
    is locked until it is sent from Gmail (status ``sent``), deleted there
    (back to ``norm``), or brought back."""

    __tablename__ = "fn_email_drafts"
    __table_args__ = _tenancy(
        "fn_email_drafts",
        _Ref("enquiry_id", "fn_enquiries", "NO ACTION"),
        _Ref("event_id", "fn_events", "NO ACTION"),
        _Ref("account_id", "fn_mailbox_accounts", "NO ACTION"),
        extra=(
            _check("fn_email_drafts", "kind", DRAFT_KINDS),
            _check("fn_email_drafts", "mode", DRAFT_MODES),
            _check("fn_email_drafts", "owner", DRAFT_OWNERS),
            _check("fn_email_drafts", "status", DRAFT_STATUSES),
            Index(
                "uq_fn_email_drafts_gmail",
                "account_id",
                "gmail_draft_id",
                unique=True,
                postgresql_where=text("gmail_draft_id IS NOT NULL"),
            ),
        ),
    )

    enquiry_id = Column(String, nullable=True)
    event_id = Column(String, nullable=True)
    account_id = Column(String, nullable=True)
    gmail_thread_id = Column(String, nullable=True)
    in_reply_to_gmail_message_id = Column(String, nullable=True)
    kind = Column(String, nullable=False, default="email")
    #: reply | reply_all | forward | new; NULL reads as reply (with a thread)
    #: or new (without) — mailbox/drafts.py decides.
    mode = Column(String, nullable=True)
    to_emails = Column(JSON, nullable=False, default=list)
    cc_emails = Column(JSON, nullable=False, default=list)
    bcc_emails = Column(JSON, nullable=False, default=list)
    subject = Column(String, nullable=False, default="")
    body_html = Column(Text, nullable=False, default="")
    owner = Column(String, nullable=False, default="norm")
    gmail_draft_id = Column(String, nullable=True)
    handed_to_gmail_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, nullable=False, default="open")
    sent_at = Column(DateTime(timezone=True), nullable=True)
    sent_gmail_message_id = Column(String, nullable=True)
    created_by_user_id = _user_fk("fn_email_drafts", "created_by_user_id")
    created_by_label = Column(String, nullable=True)
    updated_by_user_id = _user_fk("fn_email_drafts", "updated_by_user_id")


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


class FnPublicToken(_FnRow, Base):
    """A customer link. Only the SHA-256 of the token is stored, and it is
    globally unique: a public request carries no organisation, so the hash
    alone must resolve to exactly one record — or nothing."""

    __tablename__ = "fn_public_tokens"
    __table_args__ = _tenancy(
        "fn_public_tokens",
        _Ref("enquiry_id", "fn_enquiries", "CASCADE"),
        _Ref("event_id", "fn_events", "CASCADE"),
        _Ref("proposal_version_id", "fn_proposal_versions", "CASCADE"),
        extra=(
            UniqueConstraint("token_hash", name="uq_fn_public_tokens_hash"),
            _check("fn_public_tokens", "kind", PUBLIC_TOKEN_KINDS),
            CheckConstraint(
                "(kind = 'quote' AND enquiry_id IS NOT NULL) "
                "OR (kind = 'portal' AND event_id IS NOT NULL)",
                name="ck_fn_public_tokens_target",
            ),
        ),
    )

    token_hash = Column(String(64), nullable=False)
    kind = Column(String, nullable=False)
    enquiry_id = Column(String, nullable=True)
    event_id = Column(String, nullable=True)
    proposal_version_id = Column(String, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_by_user_id = _user_fk("fn_public_tokens", "created_by_user_id")


class FnPortalAccess(_FnRow, Base):
    """An email allowed into an event's client portal, and its OTP state.

    The OTP itself is never stored — only its hash, with its expiry, the wrong
    attempts against it, and a send counter over a rolling window."""

    __tablename__ = "fn_portal_access"
    __table_args__ = _tenancy(
        "fn_portal_access",
        _Ref("event_id", "fn_events", "CASCADE", index=False),
        extra=(
            Index(
                "uq_fn_portal_access_email",
                "organization_id",
                "event_id",
                func.lower(text("email")),
                unique=True,
            ),
        ),
    )

    event_id = Column(String, nullable=False)
    email = Column(String, nullable=False)
    otp_hash = Column(String, nullable=True)
    otp_expires_at = Column(DateTime(timezone=True), nullable=True)
    otp_attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    otp_sends_in_window = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    otp_window_started_at = Column(DateTime(timezone=True), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    invited_by_user_id = _user_fk("fn_portal_access", "invited_by_user_id")
    #: A colleague invited from inside the portal.
    invited_by_email = Column(String, nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class FnPortalSession(_FnRow, Base):
    """A verified client's session (the cookie carries the token; only its
    hash is stored)."""

    __tablename__ = "fn_portal_sessions"
    __table_args__ = _tenancy(
        "fn_portal_sessions",
        _Ref("event_id", "fn_events", "CASCADE"),
        extra=(UniqueConstraint("session_hash", name="uq_fn_portal_sessions_hash"),),
    )

    session_hash = Column(String(64), nullable=False)
    event_id = Column(String, nullable=False)
    email = Column(String, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


#: Every Functions model, in dependency-free alphabetical order. The metadata
#: test and conftest's two-phase create_all read this.
FUNCTIONS_MODELS = tuple(
    sorted(
        (
            m
            for m in list(globals().values())
            if isinstance(m, type)
            and issubclass(m, _FnRow)
            and hasattr(m, "__tablename__")
        ),
        key=lambda m: m.__tablename__,
    )
)
FUNCTIONS_TABLES = frozenset(m.__tablename__ for m in FUNCTIONS_MODELS)
