"""A venue's Functions profile — areas, settings, brand and image library.

What staff configure once per venue and every other screen reads: the
bookable areas (and whether a minimum spend there needs a manager), who
approves, the policies and menu links shown to customers, which event fields
each booking type uses, who is emailed, and the proposal identity.

**Who may change what.** Anyone with access to the venue edits its profile —
except the two things that decide whether an approval means anything: who the
approver and override approver are, and whether an area needs approval at all.
Letting any staff member name themselves approver, or switch an area to "no
approval needed", would turn the owner's approval rule into a formality. Those
two changes need an organisation owner or one of the venue's current approvers
(who can hand the role on).
"""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.db.functions_models import (
    BOOKING_TYPES,
    FnEvent,
    FnMinSpend,
    FnProposalImage,
    FnVenueArea,
    FnVenueAreaPhoto,
    FnVenueBrand,
    FnVenueFunctionSettings,
)
from app.db.models import User, Venue
from app.services.functions._rows import apply_fields, check_version, load, require_text
from app.services.functions.contacts import normalise_email
from app.services.functions.ctx import (
    FunctionsCtx,
    build_ctx,
    require_live,
    require_org_member,
    require_staff,
)
from app.services.functions.errors import Forbidden, Invalid, NotFound, flush
from app.services.functions.proposals import (
    MAX_LINE_CHARS,
    cap_text,
    sanitise,
)
from app.services.functions.uploads import require_file

#: The event fields a venue can switch on or off per booking type — Orbit's
#: FUNCTION_OPTION_CATALOGUE, key for key, so imported settings line up.
#: One flag per venue x booking type x field (Orbit kept a staff and a client
#: flag; they agreed in all 378 rows). A key not set is ON.
FIELD_CATALOGUE = (
    ("details", "details.min_spend", "Min spend"),
    ("details", "details.area", "Area"),
    ("details", "details.payment", "Payment method"),
    ("details", "details.deposit", "Deposit"),
    ("details", "details.security", "Security"),
    ("details", "details.entertainment", "Entertainment"),
    ("details", "details.entertainment.dj", "DJ"),
    ("details", "details.entertainment.playlist", "Playlist"),
    ("details", "details.av", "A/V"),
    ("details", "details.av.in_house_mic", "In-house mic"),
    ("details", "details.av.in_house_tv", "In-house TVs"),
    ("details", "details.av.external_av", "External A/V"),
    ("details", "details.notes", "Notes"),
    ("beverages", "beverages.bar_tab", "Bar tab"),
    ("beverages", "beverages.authorised_people", "Authorised people"),
    ("beverages", "beverages.restrictions", "Restrictions"),
    ("beverages", "beverages.arrival_drinks", "Arrival drinks"),
    ("beverages", "beverages.table_ordering", "Table ordering"),
    ("beverages", "beverages.notes", "Notes"),
    ("food", "food.timing_preference", "Food timing preference"),
    ("food", "food.allergies", "Allergies"),
    ("food", "food.notes", "Food notes"),
    ("food", "food.preorder_menus", "Pre-order menus"),
    ("timings", "timings.preference", "Timing preference"),
    ("timings", "timings.notes", "Timing notes"),
    ("timings", "timings.arrival_time", "Arrival time"),
    ("timings", "timings.seated_time", "Seated time"),
    ("timings", "timings.schedule", "Run sheet"),
)
FIELD_KEYS = tuple(key for _, key, _ in FIELD_CATALOGUE)

#: Bounds on what the profile stores: lists (menu links, terms, inclusions)
#: and shorter free text (a term, a link description, the footer).
MAX_LIST = 100
MAX_NOTE_CHARS = 10_000

AREA_FIELDS = (
    "name",
    "description",
    "capacity_seated",
    "capacity_standing",
    "booking_types",
    "requires_approval",
    "min_spend_notes",
    "active",
    "sort_order",
)
SETTINGS_FIELDS = (
    "approver_user_id",
    "override_approver_user_id",
    "policies",
    "menu_links",
    "enabled_fields",
    "notify_emails",
)
_APPROVER_FIELDS = ("approver_user_id", "override_approver_user_id")
BRAND_FIELDS = (
    "logo_file_id",
    "cover_file_id",
    "closing_file_id",
    "wordmark",
    "tagline",
    "colours",
    "fonts",
    "terms",
    "inclusions",
    "signoff",
    "style",
    "footer_contact",
    "valid_days",
    "template_html",
    "template_css",
)
_BRAND_FILES = ("logo_file_id", "cover_file_id", "closing_file_id")
_COLOUR_KEYS = ("primary", "accent", "panel", "text", "background")
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
IMAGE_FIELDS = ("name", "caption", "tags")


# ---------------------------------------------------------------------------
# Venues
# ---------------------------------------------------------------------------


def get_venue(db: Session, ctx: FunctionsCtx, venue_id: str) -> Venue:
    """A venue the caller may use. Another org's venue, or one the caller has
    no access to, is NotFound (the same answer as a venue that never
    existed)."""
    if not venue_id or venue_id not in ctx.venue_ids:
        raise NotFound("Venue not found.")
    venue = (
        db.query(Venue)
        .filter(Venue.id == venue_id, Venue.organization_id == ctx.org_id)
        .first()
    )
    if venue is None:
        raise NotFound("Venue not found.")
    return venue


def _may_govern_approvals(
    db: Session, ctx: FunctionsCtx, settings: FnVenueFunctionSettings | None
) -> bool:
    if ctx.is_owner:
        return True
    if settings is None or not ctx.user_id:
        return False
    return ctx.user_id in (
        settings.approver_user_id,
        settings.override_approver_user_id,
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def get_settings(
    db: Session, ctx: FunctionsCtx, venue_id: str
) -> FnVenueFunctionSettings | None:
    get_venue(db, ctx, venue_id)
    return (
        db.query(FnVenueFunctionSettings)
        .filter(
            FnVenueFunctionSettings.organization_id == ctx.org_id,
            FnVenueFunctionSettings.venue_id == venue_id,
        )
        .first()
    )


def settings_for_venues(
    db: Session, ctx: FunctionsCtx, venue_ids
) -> dict[str, FnVenueFunctionSettings]:
    ids = [v for v in venue_ids if v in ctx.venue_ids]
    if not ids:
        return {}
    return {
        s.venue_id: s
        for s in db.query(FnVenueFunctionSettings).filter(
            FnVenueFunctionSettings.organization_id == ctx.org_id,
            FnVenueFunctionSettings.venue_id.in_(ids),
        )
    }


def enabled_fields_for(
    settings: FnVenueFunctionSettings | None, booking_type: str | None
) -> dict[str, bool]:
    """``{field_key: on}`` for one booking type, every catalogue key present.
    Missing settings, a missing booking type or a missing key all mean ON —
    Orbit's rule, so a venue that never configured anything shows
    everything."""
    stored = {}
    if settings is not None and booking_type:
        stored = (settings.enabled_fields or {}).get(booking_type) or {}
    return {key: stored.get(key) is not False for key in FIELD_KEYS}


def _clean_enabled_fields(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise Invalid("Enabled fields must map booking types to {field: on/off}.")
    out = {}
    for booking_type, fields in value.items():
        if booking_type not in BOOKING_TYPES:
            raise Invalid(f"Unknown booking type '{booking_type}'.")
        if not isinstance(fields, dict):
            raise Invalid("Enabled fields must map booking types to {field: on/off}.")
        unknown = set(fields) - set(FIELD_KEYS)
        if unknown:
            raise Invalid(f"Unknown fields: {', '.join(sorted(unknown))}.")
        out[booking_type] = {k: bool(v) for k, v in fields.items()}
    return out


def _clean_menu_links(value) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise Invalid("Menu links must be a list.")
    if len(value) > MAX_LIST:
        raise Invalid(f"A venue has at most {MAX_LIST} menu links.")
    out = []
    for i, link in enumerate(value):
        if not isinstance(link, dict):
            raise Invalid("Each menu link needs a label and a URL.")
        url = cap_text((link.get("url") or "").strip(), "A menu link URL", 2_000)
        if not re.match(r"^https?://", url, re.IGNORECASE):
            raise Invalid("Menu links must be http(s) URLs.")
        out.append(
            {
                "label": cap_text(
                    require_text(link.get("label"), "A menu link label"),
                    "A menu link label",
                    MAX_LINE_CHARS,
                ),
                "url": url,
                "kind": cap_text(
                    (link.get("kind") or "").strip() or None, "A menu link kind", 100
                ),
                "description": cap_text(
                    (link.get("description") or "").strip() or None,
                    "A menu link description",
                    MAX_NOTE_CHARS,
                ),
                "active": link.get("active") is not False,
                "sort": int(link.get("sort") if link.get("sort") is not None else i),
            }
        )
    return out


def _clean_emails(value) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise Invalid("Notify emails must be a list of addresses.")
    if len(value) > 50:
        raise Invalid("Notify at most 50 addresses.")
    seen: list[str] = []
    for raw in value:
        email = normalise_email(raw)
        if email not in seen:
            seen.append(email)
    return seen


def _check_approver(db: Session, ctx: FunctionsCtx, user_id, venue_id: str) -> None:
    """An approver must be in the organisation AND able to see the venue —
    otherwise every approval at the venue would be impossible."""
    if user_id is None:
        return
    require_org_member(db, ctx, user_id)
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise Invalid("That person is not in this organisation.")
    try:
        theirs = build_ctx(db, user, org_id=ctx.org_id)
    except Forbidden as exc:
        raise Invalid("That person is not in this organisation.") from exc
    if venue_id not in theirs.venue_ids:
        raise Invalid(
            "That person has no access to this venue — give them access first."
        )


def update_settings(
    db: Session,
    ctx: FunctionsCtx,
    venue_id: str,
    *,
    expected_version: int | None = None,
    **fields,
) -> tuple[FnVenueFunctionSettings, dict]:
    """Create or edit the venue's settings. Returns ``(row, changes)``.
    Changing the approver or override approver needs an organisation owner
    (or a current approver of this venue)."""
    require_live(ctx)
    require_staff(ctx)
    get_venue(db, ctx, venue_id)  # a path id: another org's venue is 404
    settings = get_settings(db, ctx, venue_id)
    if settings is not None:
        check_version(settings, expected_version)
    unknown = set(fields) - set(SETTINGS_FIELDS)
    if unknown:
        raise Invalid(f"Cannot set: {', '.join(sorted(unknown))}.")
    clean = dict(fields)
    if "menu_links" in clean:
        clean["menu_links"] = _clean_menu_links(clean["menu_links"])
    if "enabled_fields" in clean:
        clean["enabled_fields"] = _clean_enabled_fields(clean["enabled_fields"])
    if "notify_emails" in clean:
        clean["notify_emails"] = _clean_emails(clean["notify_emails"])
    if "policies" in clean:
        clean["policies"] = cap_text(
            (clean["policies"] or "").strip() or None, "The policies"
        )
    touching = [
        f
        for f in _APPROVER_FIELDS
        if f in clean
        and clean[f] != (getattr(settings, f) if settings is not None else None)
    ]
    if touching:
        if not _may_govern_approvals(db, ctx, settings):
            raise Forbidden(
                "Only an owner of the organisation (or the venue's approver) can "
                "change who approves minimum spends."
            )
        for f in touching:
            _check_approver(db, ctx, clean[f], venue_id)
    if settings is None:
        # One insert, so a new row starts at version 1.
        settings = FnVenueFunctionSettings(
            organization_id=ctx.org_id, venue_id=venue_id, **clean
        )
        db.add(settings)
        flush(db)
        return settings, {k: [None, v] for k, v in clean.items()}
    changes = apply_fields(settings, clean, SETTINGS_FIELDS)
    flush(db)
    return settings, changes


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------


def list_areas(
    db: Session, ctx: FunctionsCtx, venue_id: str, *, include_inactive: bool = True
) -> list[FnVenueArea]:
    get_venue(db, ctx, venue_id)
    q = db.query(FnVenueArea).filter(
        FnVenueArea.organization_id == ctx.org_id, FnVenueArea.venue_id == venue_id
    )
    if not include_inactive:
        q = q.filter(FnVenueArea.active.is_(True))
    return q.order_by(FnVenueArea.sort_order, FnVenueArea.name).all()


def get_area(db: Session, ctx: FunctionsCtx, area_id: str) -> FnVenueArea:
    return load(db, ctx, FnVenueArea, area_id, label="Area")


def _clean_area(fields: dict) -> dict:
    clean = dict(fields)
    if "name" in clean:
        clean["name"] = cap_text(
            require_text(clean["name"], "An area name"), "An area name", MAX_LINE_CHARS
        )
    for text_field, what in (
        ("description", "An area description"),
        ("min_spend_notes", "Minimum-spend notes"),
    ):
        if text_field in clean:
            cap_text(clean[text_field], what)
    if "booking_types" in clean:
        types = list(dict.fromkeys(clean["booking_types"] or []))
        bad = [t for t in types if t not in BOOKING_TYPES]
        if bad:
            raise Invalid(f"Unknown booking types: {', '.join(bad)}.")
        clean["booking_types"] = types
    for cap in ("capacity_seated", "capacity_standing"):
        if clean.get(cap) is not None and int(clean[cap]) < 0:
            raise Invalid("Capacities cannot be negative.")
    for flag in ("requires_approval", "active"):
        if flag in clean:
            if clean[flag] is None:  # never read a missing flag as "no approval"
                raise Invalid(f"{flag} must be true or false.")
            clean[flag] = bool(clean[flag])
    return clean


def create_area(db: Session, ctx: FunctionsCtx, venue_id: str, **fields) -> FnVenueArea:
    """A new area. Areas need approval by default; creating one that doesn't
    is an approval-governance change (owner or venue approver)."""
    require_live(ctx)
    require_staff(ctx)
    get_venue(db, ctx, venue_id)  # a path id: another org's venue is 404
    unknown = set(fields) - set(AREA_FIELDS)
    if unknown:
        raise Invalid(f"Cannot set: {', '.join(sorted(unknown))}.")
    clean = _clean_area({"name": fields.get("name"), **fields})
    if clean.get("requires_approval") is False and not _may_govern_approvals(
        db, ctx, get_settings(db, ctx, venue_id)
    ):
        raise Forbidden(
            "Only an owner (or the venue's approver) can create an area that "
            "needs no minimum-spend approval."
        )
    area = FnVenueArea(organization_id=ctx.org_id, venue_id=venue_id, **clean)
    db.add(area)
    flush(db)
    return area


def update_area(
    db: Session,
    ctx: FunctionsCtx,
    area_id: str,
    *,
    expected_version: int | None = None,
    **fields,
) -> tuple[FnVenueArea, dict]:
    require_live(ctx)
    require_staff(ctx)
    area = get_area(db, ctx, area_id)
    check_version(area, expected_version)
    clean = _clean_area(fields)
    if (
        "requires_approval" in clean
        and clean["requires_approval"] != area.requires_approval
        and not _may_govern_approvals(db, ctx, get_settings(db, ctx, area.venue_id))
    ):
        raise Forbidden(
            "Only an owner (or the venue's approver) can change whether an area "
            "needs minimum-spend approval."
        )
    changes = apply_fields(area, clean, AREA_FIELDS)
    flush(db)
    return area, changes


def delete_area(db: Session, ctx: FunctionsCtx, area_id: str) -> str:
    """Delete an unused area; retire (``active=false``) one that bookings or
    options point at. Returns ``"deleted"`` or ``"retired"``."""
    require_live(ctx)
    require_staff(ctx)
    area = get_area(db, ctx, area_id)
    used = (
        db.query(FnMinSpend.id)
        .filter(FnMinSpend.organization_id == ctx.org_id, FnMinSpend.area_id == area.id)
        .first()
        or db.query(FnEvent.id)
        .filter(FnEvent.organization_id == ctx.org_id, FnEvent.area_id == area.id)
        .first()
    )
    if used:
        area.active = False
        flush(db)
        return "retired"
    db.delete(area)
    flush(db)
    return "deleted"


def area_photos(
    db: Session, ctx: FunctionsCtx, area_ids
) -> dict[str, list[FnVenueAreaPhoto]]:
    ids = list(area_ids)
    out: dict[str, list] = {a: [] for a in ids}
    if not ids:
        return out
    for p in (
        db.query(FnVenueAreaPhoto)
        .filter(
            FnVenueAreaPhoto.organization_id == ctx.org_id,
            FnVenueAreaPhoto.area_id.in_(ids),
        )
        .order_by(FnVenueAreaPhoto.sort_order, FnVenueAreaPhoto.created_at)
    ):
        out[p.area_id].append(p)
    return out


def add_area_photo(
    db: Session,
    ctx: FunctionsCtx,
    area_id: str,
    *,
    file_id: str,
    caption: str | None = None,
) -> FnVenueAreaPhoto:
    require_live(ctx)
    require_staff(ctx)
    area = get_area(db, ctx, area_id)
    require_file(db, ctx, file_id)
    count = (
        db.query(FnVenueAreaPhoto)
        .filter(
            FnVenueAreaPhoto.organization_id == ctx.org_id,
            FnVenueAreaPhoto.area_id == area.id,
        )
        .count()
    )
    photo = FnVenueAreaPhoto(
        organization_id=ctx.org_id,
        area_id=area.id,
        file_id=file_id,
        caption=cap_text((caption or "").strip() or None, "A caption", MAX_LINE_CHARS),
        sort_order=count,
    )
    db.add(photo)
    flush(db)
    return photo


def _photo(db: Session, ctx: FunctionsCtx, photo_id: str) -> FnVenueAreaPhoto:
    photo = load(db, ctx, FnVenueAreaPhoto, photo_id, label="Photo", venue_attr=None)
    get_area(db, ctx, photo.area_id)  # venue visibility
    return photo


def remove_area_photo(db: Session, ctx: FunctionsCtx, photo_id: str) -> None:
    require_live(ctx)
    require_staff(ctx)
    db.delete(_photo(db, ctx, photo_id))
    flush(db)


def reorder_area_photos(
    db: Session, ctx: FunctionsCtx, area_id: str, photo_ids: list[str]
) -> list[FnVenueAreaPhoto]:
    """Put the area's photos in this order (every photo, each once)."""
    require_live(ctx)
    require_staff(ctx)
    area = get_area(db, ctx, area_id)
    photos = area_photos(db, ctx, [area.id])[area.id]
    if sorted(photo_ids) != sorted(p.id for p in photos):
        raise Invalid("List every photo of the area exactly once.")
    by_id = {p.id: p for p in photos}
    for i, pid in enumerate(photo_ids):
        by_id[pid].sort_order = i
    flush(db)
    return [by_id[pid] for pid in photo_ids]


def update_area_photo(
    db: Session, ctx: FunctionsCtx, photo_id: str, *, caption: str | None
) -> FnVenueAreaPhoto:
    require_live(ctx)
    require_staff(ctx)
    photo = _photo(db, ctx, photo_id)
    photo.caption = cap_text(
        (caption or "").strip() or None, "A caption", MAX_LINE_CHARS
    )
    flush(db)
    return photo


# ---------------------------------------------------------------------------
# Brand
# ---------------------------------------------------------------------------


def get_brand(db: Session, ctx: FunctionsCtx, venue_id: str) -> FnVenueBrand | None:
    get_venue(db, ctx, venue_id)
    return (
        db.query(FnVenueBrand)
        .filter(
            FnVenueBrand.organization_id == ctx.org_id,
            FnVenueBrand.venue_id == venue_id,
        )
        .first()
    )


def _string_list(value, what: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise Invalid(f"{what} must be a list of lines.")
    if len(value) > MAX_LIST:
        raise Invalid(f"{what} hold at most {MAX_LIST} lines.")
    return [
        cap_text(str(v).strip(), f"A line of {what.lower()}", MAX_LINE_CHARS)
        for v in value
        if str(v or "").strip()
    ]


def _terms(value) -> list:
    """Terms are lines, or ``{title, body}`` sections (Orbit's shape, which
    the import keeps)."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise Invalid("Terms must be a list.")
    if len(value) > MAX_LIST:
        raise Invalid(f"Terms hold at most {MAX_LIST} entries.")
    out = []
    for term in value:
        if isinstance(term, dict):
            if set(term) - {"title", "body"}:
                raise Invalid("A term is a line, or {title, body}.")
            title = cap_text(
                str(term.get("title") or "").strip(), "A term's title", MAX_LINE_CHARS
            )
            body = cap_text(
                str(term.get("body") or "").strip(), "A term", MAX_NOTE_CHARS
            )
            if title or body:
                out.append({"title": title or None, "body": body})
        elif str(term or "").strip():
            out.append(cap_text(str(term).strip(), "A term", MAX_NOTE_CHARS))
    return out


def _clean_brand(db: Session, ctx: FunctionsCtx, fields: dict) -> dict:
    unknown = set(fields) - set(BRAND_FIELDS)
    if unknown:
        raise Invalid(f"Cannot set: {', '.join(sorted(unknown))}.")
    clean = dict(fields)
    for f in _BRAND_FILES:
        if f in clean:
            clean[f] = require_file(db, ctx, clean[f] or None)
    if "colours" in clean:
        colours = clean["colours"] or {}
        if not isinstance(colours, dict):
            raise Invalid("Colours must be {primary, accent, panel, text, background}.")
        out = {}
        for key, value in colours.items():
            if key not in _COLOUR_KEYS:
                raise Invalid(f"Unknown colour '{key}'.")
            if value in (None, ""):
                continue
            if not _HEX.match(str(value)):
                raise Invalid(f"Colour '{key}' must be a hex colour like #1f2a44.")
            out[key] = str(value)
        clean["colours"] = out
    if "fonts" in clean:
        fonts = clean["fonts"] or {}
        if not isinstance(fonts, dict) or set(fonts) - {"heading", "body"}:
            raise Invalid("Fonts must be {heading, body}.")
        clean["fonts"] = {
            k: cap_text(str(v).strip(), "A font name", 200)
            for k, v in fonts.items()
            if v
        }
    if "terms" in clean:
        clean["terms"] = _terms(clean["terms"])
    if "inclusions" in clean:
        clean["inclusions"] = _string_list(clean["inclusions"], "Inclusions")
    if "signoff" in clean:
        signoff = clean["signoff"] or {}
        if not isinstance(signoff, dict) or set(signoff) - {"title", "body", "contact"}:
            raise Invalid("The sign-off must be {title, body, contact}.")
        clean["signoff"] = {
            k: cap_text(
                str(v),
                "The sign-off",
                MAX_NOTE_CHARS if k == "body" else MAX_LINE_CHARS,
            )
            for k, v in signoff.items()
            if v not in (None, "")
        }
    if "style" in clean and not isinstance(clean["style"] or {}, dict):
        raise Invalid("Style must be an object of tokens.")
    if "style" in clean:
        clean["style"] = clean["style"] or {}
        cap_text(json.dumps(clean["style"]), "The style tokens", 20_000)
    for line_field, what in (("wordmark", "The wordmark"), ("tagline", "The tagline")):
        if line_field in clean:
            cap_text(clean[line_field], what, MAX_LINE_CHARS)
    if "footer_contact" in clean:
        cap_text(clean["footer_contact"], "The footer", MAX_NOTE_CHARS)
    if "valid_days" in clean:
        days = int(clean["valid_days"] or 0)
        if days < 1 or days > 365:
            raise Invalid("A quote is valid for 1 to 365 days.")
        clean["valid_days"] = days
    return clean


def update_brand(
    db: Session,
    ctx: FunctionsCtx,
    venue_id: str,
    *,
    expected_version: int | None = None,
    **fields,
) -> tuple[FnVenueBrand, dict]:
    """Create or edit the venue's proposal brand. The template HTML/CSS is
    sanitised exactly like a proposal (it is where every proposal starts)."""
    require_live(ctx)
    require_staff(ctx)
    get_venue(db, ctx, venue_id)  # a path id: another org's venue is 404
    brand = get_brand(db, ctx, venue_id)
    if brand is not None:
        check_version(brand, expected_version)
    clean = _clean_brand(db, ctx, fields)
    if "template_html" in clean or "template_css" in clean:
        html, css = sanitise(
            clean.get("template_html", brand.template_html if brand is not None else "")
            or "",
            clean.get("template_css", brand.template_css if brand is not None else "")
            or "",
            what="The brand template",
        )
        if "template_html" in clean:
            clean["template_html"] = html or None
        if "template_css" in clean:
            clean["template_css"] = css or None
    if brand is None:
        brand = FnVenueBrand(organization_id=ctx.org_id, venue_id=venue_id, **clean)
        db.add(brand)
        flush(db)
        return brand, {k: [None, v] for k, v in clean.items()}
    changes = apply_fields(brand, clean, BRAND_FIELDS)
    flush(db)
    return brand, changes


def delete_brand(db: Session, ctx: FunctionsCtx, venue_id: str) -> bool:
    """Reset the venue's brand to Norm's defaults (Orbit's "Reset"). Proposals
    already started keep their HTML; the uploaded files stay. Returns whether
    there was a brand to remove."""
    require_live(ctx)
    require_staff(ctx)
    brand = get_brand(db, ctx, venue_id)
    if brand is None:
        return False
    db.delete(brand)
    flush(db)
    return True


# ---------------------------------------------------------------------------
# Proposal image library
# ---------------------------------------------------------------------------


def list_images(db: Session, ctx: FunctionsCtx, venue_id: str) -> list[FnProposalImage]:
    get_venue(db, ctx, venue_id)
    return (
        db.query(FnProposalImage)
        .filter(
            FnProposalImage.organization_id == ctx.org_id,
            FnProposalImage.venue_id == venue_id,
        )
        .order_by(FnProposalImage.name, FnProposalImage.created_at)
        .all()
    )


def get_image(db: Session, ctx: FunctionsCtx, image_id: str) -> FnProposalImage:
    return load(db, ctx, FnProposalImage, image_id, label="Image")


def _clean_image(fields: dict) -> dict:
    unknown = set(fields) - set(IMAGE_FIELDS)
    if unknown:
        raise Invalid(f"Cannot set: {', '.join(sorted(unknown))}.")
    clean = dict(fields)
    if "name" in clean:
        clean["name"] = require_text(clean["name"], "An image name")[:200]
    if "caption" in clean:
        clean["caption"] = cap_text(
            (clean["caption"] or "").strip() or None, "A caption", MAX_LINE_CHARS
        )
    if "tags" in clean:
        tags = clean["tags"] or []
        if len(tags) > 50 or any(len(str(t)) > 50 for t in tags):
            raise Invalid("Tags are at most 50, of up to 50 characters each.")
        clean["tags"] = sorted(
            {str(t).strip().lower() for t in clean["tags"] or [] if str(t).strip()}
        )
    return clean


def add_image(
    db: Session,
    ctx: FunctionsCtx,
    venue_id: str,
    *,
    file_id: str,
    name: str | None = None,
    caption: str | None = None,
    tags: list[str] | None = None,
) -> FnProposalImage:
    """Put an uploaded file in the venue's library (named after the file
    when no name is given)."""
    require_live(ctx)
    require_staff(ctx)
    get_venue(db, ctx, venue_id)  # a path id: another org's venue is 404
    from app.services.functions.uploads import get_file

    file = get_file(db, ctx, require_file(db, ctx, file_id))
    stem = (file.filename or "").rsplit(".", 1)[0] or "Image"
    clean = _clean_image({"name": name or stem, "caption": caption, "tags": tags or []})
    image = FnProposalImage(
        organization_id=ctx.org_id,
        venue_id=venue_id,
        file_id=file.id,
        created_by_user_id=ctx.user_id,
        **clean,
    )
    db.add(image)
    flush(db)
    return image


def update_image(
    db: Session,
    ctx: FunctionsCtx,
    image_id: str,
    *,
    expected_version: int | None = None,
    **fields,
) -> tuple[FnProposalImage, dict]:
    require_live(ctx)
    require_staff(ctx)
    image = get_image(db, ctx, image_id)
    check_version(image, expected_version)
    changes = apply_fields(image, _clean_image(fields), IMAGE_FIELDS)
    flush(db)
    return image, changes


def delete_image(db: Session, ctx: FunctionsCtx, image_id: str) -> None:
    """Remove from the library. The file itself stays (other rows — or a
    proposal already sent — may use it); files are never wiped."""
    require_live(ctx)
    require_staff(ctx)
    image = get_image(db, ctx, image_id)
    db.delete(image)
    flush(db)


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


def members(db: Session, ctx: FunctionsCtx) -> list[dict]:
    """The organisation's people and the venues each can act at — for the
    owner and approver pickers. Same rule as ``ctx.build_ctx``: owners and
    admins reach every venue, everyone else their venue-access rows."""
    from app.db.models import OrganizationMembership, Role, UserVenueAccess
    from app.services.functions.ctx import _OWNER_ROLES

    rows = (
        db.query(OrganizationMembership, User)
        .join(User, User.id == OrganizationMembership.user_id)
        .filter(OrganizationMembership.organization_id == ctx.org_id)
        .all()
    )
    role_names = {
        r.id: r.name
        for r in db.query(Role).filter(
            Role.id.in_({m.role_id for m, _ in rows if m.role_id} or {""})
        )
    }
    all_venues = sorted(
        r[0] for r in db.query(Venue.id).filter(Venue.organization_id == ctx.org_id)
    )
    access: dict[str, set[str]] = {}
    for user_id, venue_id in (
        db.query(UserVenueAccess.user_id, UserVenueAccess.venue_id)
        .join(Venue, Venue.id == UserVenueAccess.venue_id)
        .filter(
            Venue.organization_id == ctx.org_id,
            UserVenueAccess.user_id.in_({u.id for _, u in rows} or {""}),
        )
    ):
        access.setdefault(user_id, set()).add(venue_id)
    out = []
    for membership, user in rows:
        is_owner = (
            user.role == "admin"
            or membership.role in _OWNER_ROLES
            or role_names.get(membership.role_id) == "owner"
        )
        out.append(
            {
                "id": user.id,
                "name": user.full_name or user.email,
                "email": user.email,
                "is_owner": is_owner,
                "venue_ids": all_venues
                if is_owner
                else sorted(access.get(user.id, set())),
            }
        )
    out.sort(key=lambda m: (m["name"] or "").lower())
    return out
