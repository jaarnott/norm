"""Organization, membership, and venue management endpoints."""

import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import (
    Memory,
    Organization,
    OrganizationMembership,
    UserVenueAccess,
    Venue,
    User,
    Connection,
)
from app.auth.dependencies import get_current_user, require_permission
from app.services.oauth_service import needs_reconnect

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Request bodies ---


class CreateOrgBody(BaseModel):
    name: str
    slug: str
    billing_email: str | None = None
    plan: str = "starter"


class UpdateOrgBody(BaseModel):
    name: str | None = None
    billing_email: str | None = None
    plan: str | None = None


class AddMemberBody(BaseModel):
    user_id: str
    role: str = "member"  # owner|admin|member


class CreateVenueBody(BaseModel):
    name: str
    location: str | None = None
    timezone: str | None = None
    day_start_time: str | None = None  # HH:MM e.g. "07:00"


class UpdateVenueBody(BaseModel):
    name: str | None = None
    location: str | None = None
    timezone: str | None = None
    day_start_time: str | None = None


# --- Helpers ---


def _org_to_dict(org: Organization, db: Session, include_details: bool = False) -> dict:
    d = {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "billing_email": org.billing_email,
        "plan": org.plan,
        "is_active": org.is_active,
        "venue_count": len(org.venues),
        "member_count": len(org.memberships),
        "created_at": org.created_at.isoformat() if org.created_at else None,
    }
    if include_details:
        d["venues"] = [
            {
                "id": v.id,
                "name": v.name,
                "location": v.location,
                "timezone": v.timezone,
                "day_start_time": v.day_start_time,
                "connector_count": db.query(Connection)
                .filter(Connection.venue_id == v.id, Connection.enabled == "true")
                .count(),
            }
            for v in org.venues
        ]
        d["members"] = [
            {
                "id": m.id,
                "user_id": m.user_id,
                "email": m.user.email if m.user else "",
                "full_name": m.user.full_name if m.user else "",
                "role": m.role,  # legacy
                "role_id": m.role_id,
                "role_name": m.role_obj.name if m.role_obj else m.role,
                "role_display_name": m.role_obj.display_name
                if m.role_obj
                else m.role.title(),
                "is_active": m.user.is_active if m.user else True,
            }
            for m in org.memberships
        ]
    return d


def _get_user_org(user: User, db: Session) -> Organization | None:
    """Get the user's primary organization."""
    membership = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .first()
    )
    if not membership:
        return None
    return (
        db.query(Organization)
        .filter(Organization.id == membership.organization_id)
        .first()
    )


def _require_org_permission(
    db: Session, user: User, org_id: str | None, scope: str | None = None
) -> None:
    """403 unless the caller belongs to ``org_id`` — and, when ``scope`` is
    given, holds it in THAT organisation. Platform admins pass.

    ``require_permission`` alone is not enough for anything org-scoped: it
    checks the scope in the caller's FIRST membership, never in the
    organisation being acted on. So a manager of one organisation could edit,
    delete or join another's, and PUT /venues/{id} had no check at all — any
    signed-in user, a Team Member included, could rename any venue or change
    its timezone (which moves its business-day boundary). Oct 2026.
    """
    if user.role == "admin":
        return
    membership = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user.id,
        )
        .first()
        if org_id
        else None
    )
    if not membership:
        raise HTTPException(403, "Not a member of this organization")
    if scope is None:
        return
    role = membership.role_obj
    if not role or scope not in (role.permissions or []):
        raise HTTPException(403, f"Missing permissions: {scope}")


def _orgs_where(db: Session, user_id: str, scope: str | None = None) -> set[str]:
    """The organisations a user belongs to (holding ``scope`` there, when
    given)."""
    return {
        m.organization_id
        for m in db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user_id)
        .all()
        if scope is None
        or (m.role_obj is not None and scope in (m.role_obj.permissions or []))
    }


# --- Organization CRUD ---


@router.get("/organizations")
async def list_organizations(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """List organizations the current user belongs to (all orgs for platform admins).

    The user's own org (the one they're a member of) comes first. Clients pick
    ``organizations[0]`` as the default, so for a platform admin — who gets every
    org — that default must be *their* org, not an arbitrary first row. Without
    this, an admin who owns "Cook Brothers Bars" opens Settings and sees some
    other org's venues.
    """
    member_org_ids = [
        m.organization_id
        for m in db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .all()
    ]

    if user.role == "admin":
        orgs = db.query(Organization).all()
    else:
        orgs = (
            db.query(Organization).filter(Organization.id.in_(member_org_ids)).all()
            if member_org_ids
            else []
        )

    # Membership orgs first, then the rest (only ever non-empty for admins).
    member_set = set(member_org_ids)
    orgs.sort(key=lambda o: (o.id not in member_set, (o.name or "").lower()))
    return {"organizations": [_org_to_dict(o, db) for o in orgs]}


@router.post("/organizations")
async def create_organization(
    body: CreateOrgBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("admin:system")),
):
    """Create a new organization. The creating user becomes the owner."""
    existing = db.query(Organization).filter(Organization.slug == body.slug).first()
    if existing:
        raise HTTPException(400, f"Organization with slug '{body.slug}' already exists")

    org = Organization(
        name=body.name,
        slug=body.slug,
        billing_email=body.billing_email,
        plan=body.plan,
    )
    db.add(org)
    db.flush()

    # Make the creating user the owner
    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=org.id,
        role="owner",
    )
    db.add(membership)
    db.commit()

    return _org_to_dict(org, db, include_details=True)


@router.get("/organizations/{org_id}")
async def get_organization(
    org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Get organization details with venues and members."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, "Organization not found")

    # Verify user is a member
    membership = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user.id,
        )
        .first()
    )
    if not membership and user.role != "admin":
        raise HTTPException(403, "Not a member of this organization")

    return _org_to_dict(org, db, include_details=True)


@router.put("/organizations/{org_id}")
async def update_organization(
    org_id: str,
    body: UpdateOrgBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update organization details."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, "Organization not found")
    _require_org_permission(db, user, org_id, "org:manage")

    if body.name is not None:
        org.name = body.name
    if body.billing_email is not None:
        org.billing_email = body.billing_email
    if body.plan is not None:
        org.plan = body.plan

    db.commit()
    return _org_to_dict(org, db, include_details=True)


# --- Company settings ---
#
# Currency, default timezone and tax wording live on the organisation because
# Norm Functions stores money as bare cents (the currency is the company's,
# not the row's), with the Functions mode beside them. The rules are in
# services/functions/company.py; these endpoints add who may call it.


class CompanySettingsBody(BaseModel):
    currency: str | None = None
    default_timezone: str | None = None
    tax_label: str | None = None
    functions_mode: str | None = None


#: ISO 4217 codes in circulation (list one, as amended to Oct 2026) — the
#: fund, precious-metal and testing codes left out: nobody trades in them.
#: Stricter than company.py's three-letters check, so "NDZ" can't land on a
#: quote by typo.
ISO_4217_CURRENCIES = frozenset(
    """
    AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BHD BIF BMD BND BOB BRL BSD
    BTN BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP
    ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS
    INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR
    LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN
    NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD
    SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY
    TTD TWD TZS UAH UGX USD UYU UZS VED VES VND VUV WST XAF XCD XCG XOF XPF YER
    ZAR ZMW ZWG
    """.split()
)


def _can_edit_company(db: Session, user: User, org_id: str) -> bool:
    """Whether ``user`` may change this organisation's company settings: an
    owner or admin of it, or a platform admin — company.py's own rule
    (``FunctionsCtx.is_owner``), so "owner" means one thing everywhere."""
    from app.services.functions import FunctionsError
    from app.services.functions.ctx import build_ctx

    try:
        return build_ctx(db, user, org_id=org_id).is_owner
    except FunctionsError:
        return False


def _company_payload(db: Session, org_id: str, *, can_edit: bool) -> dict:
    from app.services.functions.company import get_company_settings

    return {
        **get_company_settings(db, org_id).as_dict(),
        "can_edit": can_edit,
        "currencies": sorted(ISO_4217_CURRENCIES),
    }


@router.get("/organizations/{org_id}/company-settings")
async def get_org_company_settings(
    org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Currency, default timezone, tax wording and the Functions mode. Any
    member may read them; ``can_edit`` says whether this user may change them,
    so the page shows no control the API would refuse."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, "Organization not found")
    _require_org_permission(db, user, org_id)
    return _company_payload(db, org_id, can_edit=_can_edit_company(db, user, org_id))


@router.put("/organizations/{org_id}/company-settings")
async def update_org_company_settings(
    org_id: str,
    body: CompanySettingsBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Change any of them — owners and admins of the organisation (and
    platform admins) only.

    The Functions mode most of all: ``shadow`` and ``live`` are what open
    Functions to everyone in the organisation, and ``live`` lets it write and
    send, so every change of mode is logged with who made it. All or nothing:
    one invalid field leaves the others as they were."""
    from app.services.functions import FunctionsError
    from app.services.functions.company import (
        get_company_settings,
        update_company_settings,
    )
    from app.services.functions.ctx import build_ctx

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, "Organization not found")
    _require_org_permission(db, user, org_id)
    ctx = build_ctx(db, user, org_id=org_id)  # a member or platform admin by now
    if not ctx.is_owner:
        raise HTTPException(
            403,
            "Only an owner or admin of the organisation can change company settings.",
        )
    if body.currency is not None:
        code = body.currency.strip().upper()
        if code not in ISO_4217_CURRENCIES:
            raise HTTPException(
                422,
                f"'{body.currency.strip()}' is not an ISO 4217 currency code "
                "(e.g. NZD, AUD, GBP).",
            )
    before = get_company_settings(db, org_id).functions_mode
    try:
        with db.begin_nested():
            saved = update_company_settings(
                db,
                ctx,
                currency=body.currency,
                default_timezone=body.default_timezone,
                tax_label=body.tax_label,
                functions_mode=body.functions_mode,
            )
    except FunctionsError as e:
        raise HTTPException(e.status_code, e.message) from e
    db.commit()
    if saved.functions_mode != before:
        logger.info(
            "functions_mode_changed",
            extra={
                "org_id": org_id,
                "previous_mode": before,
                "mode": saved.functions_mode,
                "by": user.id,
            },
        )
    return _company_payload(db, org_id, can_edit=True)


# --- Member management ---


@router.post("/organizations/{org_id}/members")
async def add_member(
    org_id: str,
    body: AddMemberBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add a user to an organization."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, "Organization not found")
    _require_org_permission(db, user, org_id, "org:members")

    target_user = db.query(User).filter(User.id == body.user_id).first()
    if not target_user:
        raise HTTPException(404, "User not found")

    existing = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == body.user_id,
        )
        .first()
    )
    if existing:
        existing.role = body.role
    else:
        db.add(
            OrganizationMembership(
                user_id=body.user_id,
                organization_id=org_id,
                role=body.role,
            )
        )

    db.commit()
    return {"ok": True}


@router.delete("/organizations/{org_id}/members/{member_user_id}")
async def remove_member(
    org_id: str,
    member_user_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove a user from an organization."""
    _require_org_permission(db, user, org_id, "org:members")
    membership = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == member_user_id,
        )
        .first()
    )
    if not membership:
        raise HTTPException(404, "Membership not found")

    # Leaving an organisation ends everything that reached its venues. Venue
    # access is its own table and is what the venue picker, the chat and the
    # connectors check, so deleting only the membership left a removed person
    # able to open every venue (rich@loadedhub.com, 18 Oct 2026). Access to
    # OTHER organisations' venues is untouched.
    org_venues = db.query(Venue.id).filter(Venue.organization_id == org_id)
    db.query(UserVenueAccess).filter(
        UserVenueAccess.user_id == member_user_id,
        UserVenueAccess.venue_id.in_(org_venues),
    ).delete(synchronize_session=False)
    from datetime import datetime, timezone

    from app.db.mcp_models import McpGrant

    db.query(McpGrant).filter(
        McpGrant.user_id == member_user_id,
        McpGrant.organization_id == org_id,
        McpGrant.revoked_at.is_(None),
    ).update({"revoked_at": datetime.now(timezone.utc)}, synchronize_session=False)

    db.delete(membership)
    db.commit()
    return {"ok": True}


# --- Venue management within an org ---


@router.post("/organizations/{org_id}/venues")
async def create_venue(
    org_id: str,
    body: CreateVenueBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a venue within an organization."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, "Organization not found")
    _require_org_permission(db, user, org_id, "org:venues")

    venue = Venue(
        name=body.name,
        location=body.location,
        timezone=body.timezone,
        day_start_time=body.day_start_time,
        organization_id=org_id,
    )
    db.add(venue)
    db.flush()

    # Grant the creating user access to the venue
    db.add(UserVenueAccess(user_id=user.id, venue_id=venue.id))
    db.commit()

    return {
        "id": venue.id,
        "name": venue.name,
        "location": venue.location,
        "timezone": venue.timezone,
        "day_start_time": venue.day_start_time,
        "organization_id": org_id,
    }


@router.put("/venues/{venue_id}")
async def update_venue(
    venue_id: str,
    body: UpdateVenueBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update venue details — needs ``org:venues`` in the venue's organisation."""
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(404, "Venue not found")
    _require_org_permission(db, user, venue.organization_id, "org:venues")

    if body.name is not None:
        venue.name = body.name
    if body.location is not None:
        venue.location = body.location
    if body.timezone is not None:
        venue.timezone = body.timezone
    if body.day_start_time is not None:
        venue.day_start_time = body.day_start_time

    db.commit()
    return {
        "id": venue.id,
        "name": venue.name,
        "location": venue.location,
        "timezone": venue.timezone,
        "day_start_time": venue.day_start_time,
    }


def _functions_records(db: Session, venue_id: str) -> list[str]:
    """What Norm Functions holds at a venue that deleting it can't take along,
    as ``"enquiries (3)"`` — empty when nothing.

    That is every fn_* table whose reference to venues doesn't cascade:
    enquiries, events, minimum spends, menus, mailboxes, web forms and their
    submissions. (The venue's Functions CONFIG — areas, settings, brand,
    templates — cascades.) Read from the schema rather than listed, so a table
    added later is covered too; tests/test_venue_delete.py's guard only scans
    app.db.models."""
    from sqlalchemy import func, select

    from app.db.functions_models import FUNCTIONS_MODELS

    held = []
    for model in FUNCTIONS_MODELS:
        table = model.__table__
        for fk in table.foreign_key_constraints:
            if fk.referred_table.name != "venues":
                continue
            if (fk.ondelete or "").upper() == "CASCADE":
                continue
            column = next(c for c in fk.columns if c.name != "organization_id")
            count = db.execute(
                select(func.count()).select_from(table).where(column == venue_id)
            ).scalar()
            if count:
                label = table.name.removeprefix("fn_").replace("_", " ")
                held.append(f"{label} ({count})")
    return held


@router.delete("/venues/{venue_id}")
async def delete_venue(
    venue_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a venue, its access grants and its connector configs.

    Many tables carry a venues.id foreign key, and none of them cascade
    (``ON DELETE NO ACTION``). Clearing only some of them means the final
    ``DELETE FROM venues`` raises a FK violation and the venue can't be removed
    at all — which is exactly what happened: a venue with tool_call history was
    undeletable.

    So each referencing table is handled explicitly, and the split is deliberate:

    * **Venue-scoped config is deleted** — access grants and connector configs
      (including OAuth tokens) are meaningless once the venue is gone, and the
      tokens should not outlive it.
    * **History keeps its rows and drops the reference** — threads, tool calls,
      reports, email logs. Deleting a venue is not a reason to erase the
      record of what was ordered or what an agent did; those columns are all
      nullable precisely so the history can outlive the venue.
    * **Norm Functions' records refuse it** (409, ``_functions_records``) —
      enquiries and events can't drop their venue (it is part of the booking,
      and the composite keys forbid it), so the venue stays while they exist.
    """
    from sqlalchemy import func

    from app.db.models import (
        AppCall,
        AppFile,
        AppRecord,
        AutomatedTask,
        BackgroundJob,
        EmailLog,
        InvoiceAutopilotOutcome,
        HiringCriteria,
        HrSetup,
        Insight,
        InsightNote,
        InsightRun,
        InvoicePrewarmAttempt,
        Job,
        OAuthState,
        Report,
        SuggestionEvent,
        Thread,
        ToolCall,
        UnitCheckProposal,
        UploadedDocument,
        WorkingDocument,
    )

    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(404, "Venue not found")
    _require_org_permission(db, user, venue.organization_id, "org:venues")

    # Norm Functions' records are booking history that must not vanish with
    # the venue; their foreign keys refuse, so say why instead of a 500.
    held = _functions_records(db, venue_id)
    if held:
        raise HTTPException(
            409,
            f"{venue.name} has Norm Functions records ({', '.join(held)}). "
            "They are its booking history and keep the venue, so it can't be "
            "deleted while they exist.",
        )

    # Venue-scoped config — remove entirely.
    # (user_venue_access.venue_id is NOT NULL, so it must be deleted, not nulled.)
    db.query(UserVenueAccess).filter(UserVenueAccess.venue_id == venue_id).delete(
        synchronize_session=False
    )
    db.query(Connection).filter(Connection.venue_id == venue_id).delete(
        synchronize_session=False
    )
    db.query(OAuthState).filter(OAuthState.venue_id == venue_id).delete(
        synchronize_session=False
    )
    db.query(HrSetup).filter(HrSetup.venue_id == venue_id).delete(
        synchronize_session=False
    )
    # Pre-warm bookkeeping is cache state about THIS venue's invoices —
    # meaningless without it, and venue_id is NOT NULL. Delete, never null.
    db.query(InvoicePrewarmAttempt).filter(
        InvoicePrewarmAttempt.venue_id == venue_id
    ).delete(synchronize_session=False)
    # An app record scoped to a venue is that venue's operational data — a
    # training assignment at this venue, a job opening for it. NULLing the
    # column would not orphan it, it would PROMOTE it: NULL means "belongs to
    # the whole organization" in app_records, so every remaining venue would
    # suddenly see rows that were never theirs. Delete with the venue.
    # (Group-wide rows have venue_id NULL already and are untouched.)
    db.query(AppRecord).filter(AppRecord.venue_id == venue_id).delete(
        synchronize_session=False
    )
    # Same rule for the bytes: a file scoped to a venue is that venue's
    # evidence, and NULL here means "the whole organization", so nulling the
    # column would publish it rather than orphan it.
    db.query(AppFile).filter(AppFile.venue_id == venue_id).delete(
        synchronize_session=False
    )
    # What Norm noticed in this venue's numbers, what people said about it, and
    # its daily run slots describe THIS venue only (services/insights) — gone
    # with it, never moved onto another venue.
    for model in (Insight, InsightNote, InsightRun):
        db.query(model).filter(model.venue_id == venue_id).delete(
            synchronize_session=False
        )
    # The daily unit check's proposed corrections are about this venue's
    # invoices only and can't be applied once it's gone.
    db.query(UnitCheckProposal).filter(UnitCheckProposal.venue_id == venue_id).delete(
        synchronize_session=False
    )

    # A background job still working for this venue stops (a running one
    # between invoices); its record of what it did is history, below.
    db.query(BackgroundJob).filter(
        BackgroundJob.venue_id == venue_id, BackgroundJob.status == "queued"
    ).update(
        {BackgroundJob.status: "cancelled", BackgroundJob.finished_at: func.now()},
        synchronize_session=False,
    )
    db.query(BackgroundJob).filter(
        BackgroundJob.venue_id == venue_id, BackgroundJob.status == "running"
    ).update({BackgroundJob.cancel_requested: True}, synchronize_session=False)

    # History and user-owned records — keep the row, drop the venue reference.
    for model in (
        Thread,
        ToolCall,
        Report,
        EmailLog,
        Job,
        HiringCriteria,
        AutomatedTask,
        WorkingDocument,
        # An uploaded document is owned by the user who uploaded it; the
        # venue_id is just the venue it was uploaded against. Drop the tag,
        # keep the upload.
        UploadedDocument,
        # How well Norm did on this venue's invoices is evidence about NORM,
        # not about the venue — it stays useful after the venue is gone.
        InvoiceAutopilotOutcome,
        # A memory's venue_id is a tag, not ownership — "Mr Murdochs is
        # closed" is an org fact that happens to be narrow. Deleting the venue
        # should not delete what the org learned, so drop the tag and keep the
        # memory.
        Memory,
        # What an app did while the venue existed is audit, not venue config.
        AppCall,
        # So is what a background job did for it.
        BackgroundJob,
        # What a person was shown, clicked or dismissed on Home is their
        # history (services/suggestions).
        SuggestionEvent,
    ):
        db.query(model).filter(model.venue_id == venue_id).update(
            {model.venue_id: None}, synchronize_session=False
        )

    db.delete(venue)
    db.commit()

    return {"ok": True}


@router.get("/venues/{venue_id}/connectors")
async def list_venue_connectors(
    venue_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """List connector configuration status for a specific venue."""
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(404, "Venue not found")
    _require_org_permission(db, user, venue.organization_id)

    configs = db.query(Connection).filter(Connection.venue_id == venue_id).all()
    return {
        "venue_id": venue_id,
        "venue_name": venue.name,
        "connectors": [
            {
                "connector_name": c.connector_name,
                "enabled": c.enabled == "true",
                "has_oauth": bool(c.access_token),
                "needs_reconnect": needs_reconnect(c),
                "last_auth_error": c.last_auth_error,
                "oauth_metadata": c.oauth_metadata,
            }
            for c in configs
        ],
    }


# --- User venue access ---


@router.get("/users/{user_id}/venues")
async def list_user_venues(
    user_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """List venues a user has access to.

    Yourself, a platform admin, or someone who shares an organisation with
    the user — and then only the venues of the organisations you share.
    """
    shared = None
    if user.role != "admin" and user_id != user.id:
        shared = _orgs_where(db, user.id) & _orgs_where(db, user_id)
        if not shared:
            raise HTTPException(403, "Not a member of this organization")
    access = db.query(UserVenueAccess).filter(UserVenueAccess.user_id == user_id).all()
    venue_ids = [a.venue_id for a in access]
    venues = db.query(Venue).filter(Venue.id.in_(venue_ids)).all() if venue_ids else []
    if shared is not None:
        venues = [v for v in venues if v.organization_id in shared]
    return {
        "venues": [{"id": v.id, "name": v.name, "location": v.location} for v in venues]
    }


@router.put("/users/{user_id}/venues")
async def set_user_venues(
    user_id: str,
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Set venue access for a user. Body: {"venue_ids": ["v1", "v2"]}.

    Needs ``org:members`` in an organisation the user belongs to, and touches
    only that organisation's venues: it used to let a manager anywhere grant
    themselves (or anyone) access to any venue, and wiped the user's access
    in every other organisation on the way.
    """
    venue_ids = body.get("venue_ids", [])
    if not isinstance(venue_ids, list) or not all(
        isinstance(v, str) for v in venue_ids
    ):
        raise HTTPException(422, "venue_ids must be a list of venue ids")
    venue_ids = list(dict.fromkeys(venue_ids))

    scope = None  # every organisation — a platform admin
    if user.role != "admin":
        scope = _orgs_where(db, user.id, "org:members") & _orgs_where(db, user_id)
        if not scope:
            raise HTTPException(403, "Not a member of this organization")

    venues = db.query(Venue).filter(Venue.id.in_(venue_ids)).all() if venue_ids else []
    if len(venues) != len(venue_ids):
        raise HTTPException(404, "Venue not found")
    if scope is not None and any(v.organization_id not in scope for v in venues):
        raise HTTPException(403, "Not a member of this organization")

    # Remove existing access — within the caller's reach only
    existing = db.query(UserVenueAccess).filter(UserVenueAccess.user_id == user_id)
    if scope is not None:
        existing = existing.filter(
            UserVenueAccess.venue_id.in_(
                db.query(Venue.id).filter(Venue.organization_id.in_(scope))
            )
        )
    existing.delete(synchronize_session=False)

    # Add new access
    for vid in venue_ids:
        db.add(UserVenueAccess(user_id=user_id, venue_id=vid))

    db.commit()
    return {"ok": True, "venue_count": len(venue_ids)}


_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "billable_tokens",
    "cost_usd",
    "llm_call_count",
)


def _require_usage_access(db: Session, user: User, org_id: str) -> None:
    """Members of the organisation (and platform admins) only — usage showed
    any organisation's figures to anyone signed in (Oct 2026)."""
    if user.role == "admin":
        return
    member = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user.id,
        )
        .first()
    )
    if not member:
        raise HTTPException(403, "Not a member of this organization")


def _usage_of(r) -> dict:
    """One token_usage row's figures. ``billable_tokens`` falls back to input +
    output for days recorded before it existed — what limits counted then."""
    out = {k: (getattr(r, k) or 0) for k in _USAGE_FIELDS}
    out["cost_usd"] = float(out["cost_usd"])
    if not getattr(r, "billable_tokens", None):
        out["billable_tokens"] = out["input_tokens"] + out["output_tokens"]
    return out


def _add(into: dict, more: dict) -> None:
    for k, v in more.items():
        into[k] = into.get(k, 0) + v


@router.get("/organizations/{org_id}/usage")
async def get_usage(
    org_id: str,
    month: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Monthly usage for an organization: every kind of token, the billable
    tokens plan limits count, and the cost (see app/services/llm_cost.py)."""
    from app.db.models import TokenUsage
    import datetime as dt

    _require_usage_access(db, user, org_id)
    if not month:
        month = dt.date.today().strftime("%Y-%m")

    rows = (
        db.query(TokenUsage)
        .filter(
            TokenUsage.organization_id == org_id,
            TokenUsage.date.like(f"{month}%"),
        )
        .all()
    )

    totals: dict = {}
    by_user: dict[str, dict] = {}
    for r in rows:
        u = _usage_of(r)
        _add(totals, u)
        _add(by_user.setdefault(r.user_id or "system", {}), u)

    return {
        "month": month,
        "total_input_tokens": totals.get("input_tokens", 0),
        "total_output_tokens": totals.get("output_tokens", 0),
        "total_cache_read_tokens": totals.get("cache_read_tokens", 0),
        "total_cache_write_tokens": totals.get("cache_write_tokens", 0),
        # Every token processed, cache included.
        "total_tokens": sum(
            totals.get(k, 0)
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            )
        ),
        "total_billable_tokens": totals.get("billable_tokens", 0),
        "total_cost_usd": round(totals.get("cost_usd", 0), 4),
        "total_llm_calls": totals.get("llm_call_count", 0),
        "by_user": by_user,
    }


@router.get("/organizations/{org_id}/usage/daily")
async def get_daily_usage(
    org_id: str,
    month: str | None = None,
    user_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Daily usage for an organization, optionally for one user."""
    from app.db.models import TokenUsage
    import datetime as dt

    _require_usage_access(db, user, org_id)
    if not month:
        month = dt.date.today().strftime("%Y-%m")

    query = db.query(TokenUsage).filter(
        TokenUsage.organization_id == org_id,
        TokenUsage.date.like(f"{month}%"),
    )
    if user_id:
        query = query.filter(TokenUsage.user_id == user_id)
    by_day: dict[str, dict] = {}
    for r in query.order_by(TokenUsage.date).all():
        _add(by_day.setdefault(r.date, {}), _usage_of(r))

    return {"month": month, "days": by_day}


#: How the breakdown groups call types.
_KIND = {
    "tool_use": "chat",
    "extraction": "invoice extraction",
}


@router.get("/organizations/{org_id}/usage/breakdown")
async def get_usage_breakdown(
    org_id: str,
    month: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Where a month's cost went: by kind of work, and the costliest chats."""
    import datetime as dt

    from sqlalchemy import func

    from app.db.models import LlmCall, Thread

    _require_usage_access(db, user, org_id)
    if not month:
        month = dt.date.today().strftime("%Y-%m")
    start = dt.datetime.strptime(month + "-01", "%Y-%m-%d").replace(
        tzinfo=dt.timezone.utc
    )
    end = (start + dt.timedelta(days=32)).replace(day=1)
    in_month = (
        LlmCall.organization_id == org_id,
        LlmCall.created_at >= start,
        LlmCall.created_at < end,
    )

    by_kind: dict[str, dict] = {}
    for call_type, n, cost, billable, read, write in (
        db.query(
            LlmCall.call_type,
            func.count(),
            func.coalesce(func.sum(LlmCall.cost_usd), 0),
            func.coalesce(func.sum(LlmCall.billable_tokens), 0),
            func.coalesce(func.sum(LlmCall.cache_read_tokens), 0),
            func.coalesce(func.sum(LlmCall.cache_write_tokens), 0),
        )
        .filter(*in_month)
        .group_by(LlmCall.call_type)
        .all()
    ):
        _add(
            by_kind.setdefault(_KIND.get(call_type, "other"), {}),
            {
                "calls": n,
                "cost_usd": float(cost),
                "billable_tokens": int(billable),
                "cache_read_tokens": int(read),
                "cache_write_tokens": int(write),
            },
        )

    top = (
        db.query(
            LlmCall.thread_id,
            func.count(),
            func.coalesce(func.sum(LlmCall.cost_usd), 0),
            func.coalesce(func.sum(LlmCall.billable_tokens), 0),
            func.coalesce(func.sum(LlmCall.cache_read_tokens), 0),
            func.coalesce(func.sum(LlmCall.cache_write_tokens), 0),
        )
        .filter(*in_month, LlmCall.thread_id.isnot(None))
        .group_by(LlmCall.thread_id)
        .order_by(func.coalesce(func.sum(LlmCall.cost_usd), 0).desc())
        .limit(10)
        .all()
    )
    titles = {
        t.id: (t.title or t.raw_prompt or "")[:120]
        for t in db.query(Thread).filter(Thread.id.in_([r[0] for r in top])).all()
    }
    return {
        "month": month,
        "by_kind": by_kind,
        "top_threads": [
            {
                "thread_id": tid,
                "title": titles.get(tid, ""),
                "calls": n,
                "cost_usd": round(float(cost), 4),
                "billable_tokens": int(billable),
                "cache_ratio": round(int(read) / int(write), 1) if write else None,
            }
            for tid, n, cost, billable, read, write in top
        ],
    }


@router.get("/users/by-email")
async def find_user_by_email(
    email: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Find a user by email address. Used for adding members to an organization."""
    target = db.query(User).filter(User.email == email).first()
    if not target:
        raise HTTPException(404, "User not found")
    return {
        "id": target.id,
        "email": target.email,
        "full_name": target.full_name,
        "role": target.role,
    }
