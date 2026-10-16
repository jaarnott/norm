"""Norm Functions — the staff API (``/api/functions/...``).

Everything the Functions pages need: the meta call they load first, the
dashboard and reports, contacts, enquiries (with their minimum-spend options,
pre-order menus and proposal), events (with sections, run sheet, prints), and
the venue's profile and templates. The inbox (F4), the web-form builder and
the customer pages (F5) have their own routers.

Every endpoint does the same four things, so they're written once:

1. **Who and where** — ``staff_ctx``: the signed-in user's organisation (from
   membership), refused as 404 unless Functions is on for it
   (``entitlement.functions_enabled``), then ``build_ctx`` (the venues they
   may use, the org's mode).
2. **The rule** — a service in ``app/services/functions``. Routers never
   query a rule of their own; they shape what the services return.
3. **Errors** — domain errors become ``{"detail": {"code", "message"}}`` with
   the service's status (404 not found / other org, 403 not allowed, 409
   stale version or ruled out by the facts, 409 ``not_live`` in shadow mode,
   422 bad input). Body validation errors are FastAPI's usual 422.
4. **One request, one transaction** — writes run in a savepoint and commit
   once: a refused write leaves nothing behind, and the response carries the
   row's new ``version`` for the next edit.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.engine import get_config_db, get_db
from app.db.functions_models import (
    AV_KINDS,
    BOOKING_TYPES,
    ENQUIRY_SOURCES,
    ENTERTAINMENT_KINDS,
    EVENT_SECTIONS,
    FOOD_TIMING_TYPES,
    MIN_SPEND_BOOKING_TYPES,
    REPLY_TEMPLATE_STAGES,
    FnContact,
    FnEnquiry,
    FnEvent,
    FnMailboxAccount,
    FnMailboxThread,
    FnMinSpend,
    FnMinSpendApproval,
    FnMinSpendResponse,
    FnPreorderMenu,
    FnPreorderTemplate,
    FnProposalImage,
    FnReplyTemplate,
    FnVenueArea,
    FnVenueBrand,
)
from app.db.models import User, Venue
from app.services.entitlements import org_id_for_user
from app.services.functions import contacts as contacts_svc
from app.services.functions import enquiries as enquiries_svc
from app.services.functions import entitlement
from app.services.functions import events as events_svc
from app.services.functions import menus as menus_svc
from app.services.functions import min_spends as min_spends_svc
from app.services.functions import money as money_svc
from app.services.functions import privacy as privacy_svc
from app.services.functions import proposals as proposals_svc
from app.services.functions import reports
from app.services.functions import status as status_svc
from app.services.functions import templates as templates_svc
from app.services.functions import uploads as uploads_svc
from app.services.functions import venue_profile
from app.services.functions.company import get_company_settings
from app.services.functions.ctx import FunctionsCtx, build_ctx
from app.services.functions.errors import FunctionsError, Invalid, http_exception
from app.services.functions.timeutil import validate_timezone

router = APIRouter(prefix="/functions", tags=["functions"])


# ===========================================================================
# Plumbing
# ===========================================================================


def staff_ctx(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
) -> FunctionsCtx:
    """The caller's Functions context — or 404 when their organisation
    doesn't have Functions (off, not entitled, or no organisation at all):
    the pages don't exist for them, so neither do the endpoints."""
    org_id = org_id_for_user(user.id, db)
    if not entitlement.functions_enabled(db, config_db, org_id):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_found",
                "message": "Functions is not switched on for your organisation.",
            },
        )
    try:
        return build_ctx(db, user, org_id=org_id)
    except FunctionsError as exc:
        raise http_exception(exc) from exc


@contextmanager
def _reading():
    try:
        yield
    except FunctionsError as exc:
        raise http_exception(exc) from exc


@contextmanager
def _writing(db: Session):
    """A savepoint around the write, then one commit. A domain refusal rolls
    the savepoint back (nothing half-written survives, and the session stays
    usable) and becomes the HTTP error."""
    try:
        with db.begin_nested():
            yield
    except FunctionsError as exc:
        raise http_exception(exc) from exc
    db.commit()


class _Body(BaseModel):
    """Request bodies refuse unknown fields: a typo must not look like a
    successful save."""

    model_config = ConfigDict(extra="forbid")


def _given(body: BaseModel, *drop: str) -> dict:
    """The fields the client actually sent (PATCH semantics)."""
    return {
        k: v for k, v in body.model_dump(exclude_unset=True).items() if k not in drop
    }


def _no_nulls(fields: dict, model) -> dict:
    """A JSON ``null`` for a column that can't be empty is a 422 the user can
    read — not a database refusal surfacing as a confusing 409."""
    cols = model.__table__.c
    bad = sorted(
        k for k, v in fields.items() if v is None and k in cols and not cols[k].nullable
    )
    if bad:
        raise HTTPException(
            422, {"code": "invalid", "message": f"{', '.join(bad)} can't be empty."}
        )
    return fields


def _multi(values: list[str] | None) -> list[str] | None:
    """A list query param, repeated (``?stage=new&stage=quoted``) or
    comma-separated (``?stage=new,quoted``)."""
    if not values:
        return None
    out = [v.strip() for raw in values for v in raw.split(",") if v.strip()]
    return out or None


def _d(value: dt.date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _t(value: dt.time | None) -> str | None:
    return value.isoformat(timespec="minutes") if value is not None else None


def _ts(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _page(rows: list, limit: int) -> tuple[list, bool]:
    return rows[:limit], len(rows) > limit


# ===========================================================================
# Serialisers
# ===========================================================================


def _contact_json(c: FnContact, emails=None) -> dict:
    out = {
        "id": c.id,
        "version": c.version,
        "name": c.name,
        "company": c.company,
        "phone": c.phone,
        "notes": c.notes,
        "archived_at": _ts(c.archived_at),
        "anonymised_at": _ts(c.anonymised_at),
        "created_at": _ts(c.created_at),
    }
    if emails is not None:
        out["emails"] = [
            {"email": e.email, "is_primary": bool(e.is_primary), "source": e.source}
            for e in emails
        ]
        primary = next((e.email for e in emails if e.is_primary), None)
        out["email"] = primary or (emails[0].email if emails else None)
    return out


def _enquiry_json(e: FnEnquiry) -> dict:
    return {
        "id": e.id,
        "version": e.version,
        "contact_id": e.contact_id,
        "venue_id": e.venue_id,
        "booking_type": e.booking_type,
        "booking_label": events_svc.BOOKING_LABELS.get(e.booking_type or ""),
        "subject": e.subject,
        "guests": e.guests,
        "preferred_date_start": _d(e.preferred_date_start),
        "preferred_date_end": _d(e.preferred_date_end),
        "preferred_start_time": _t(e.preferred_start_time),
        "preferred_end_time": _t(e.preferred_end_time),
        "preferred_ends_next_day": bool(e.preferred_ends_next_day),
        "source": e.source,
        "attribution": {
            k: getattr(e, k)
            for k in enquiries_svc.ATTRIBUTION_FIELDS
            if getattr(e, k) is not None
        },
        "owner_user_id": e.owner_user_id,
        "follow_up_date": _d(e.follow_up_date),
        "follow_up_reason": e.follow_up_reason,
        "client_note": e.client_note,
        "notes": e.notes,
        "archived_at": _ts(e.archived_at),
        "created_by_user_id": e.created_by_user_id,
        "created_by_label": e.created_by_label,
        "created_at": _ts(e.created_at),
        "updated_at": _ts(e.updated_at),
    }


def _enquiry_row_json(r, value_cents: int | None = None) -> dict:
    e = r.enquiry
    return {
        "id": e.id,
        "version": e.version,
        "subject": e.subject,
        "contact_id": e.contact_id,
        "contact_name": r.contact_name,
        "contact_company": r.contact_company,
        "venue_id": e.venue_id,
        "venue_name": r.venue_name,
        "booking_type": e.booking_type,
        "guests": e.guests,
        "preferred_date_start": _d(e.preferred_date_start),
        "preferred_date_end": _d(e.preferred_date_end),
        "source": e.source,
        "owner_user_id": e.owner_user_id,
        "stage": r.stage,
        "next_action": {
            "kind": r.next_action,
            "label": status_svc.NEXT_ACTION_LABELS.get(r.next_action),
            "due_date": _d(r.next_action_due),
            "overdue": bool(r.next_action_overdue),
            "due_today": bool(r.next_action_due_today),
        },
        "last_inbound_at": _ts(r.last_inbound_at),
        "last_outbound_at": _ts(r.last_outbound_at),
        "quote_sent_at": _ts(r.quote_sent_at),
        "follow_up_date": _d(e.follow_up_date),
        "archived_at": _ts(e.archived_at),
        "created_at": _ts(e.created_at),
        "value_cents": value_cents,
    }


def _event_json(e: FnEvent) -> dict:
    out = {
        "id": e.id,
        "version": e.version,
        "enquiry_id": e.enquiry_id,
        "contact_id": e.contact_id,
        "invoice_contact_id": e.invoice_contact_id,
        "venue_id": e.venue_id,
        "area_id": e.area_id,
        "booking_type": e.booking_type,
        "booking_label": events_svc.BOOKING_LABELS.get(e.booking_type or ""),
        "name": e.name,
        "event_date": _d(e.event_date),
        "start_time": _t(e.start_time),
        "end_time": _t(e.end_time),
        "ends_next_day": bool(e.ends_next_day),
        "created_via": e.created_via,
        "confirmation_channel": e.confirmation_channel,
        "created_by_user_id": e.created_by_user_id,
        "created_by_label": e.created_by_label,
        "cancelled_at": _ts(e.cancelled_at),
        "cancel_reason": e.cancel_reason,
        "created_at": _ts(e.created_at),
        "updated_at": _ts(e.updated_at),
    }
    for name in events_svc.EVENT_FIELD_SECTIONS:
        if name in out:
            continue
        value = getattr(e, name)
        if isinstance(value, dt.time):
            value = _t(value)
        elif isinstance(value, dt.date):
            value = _d(value)
        out[name] = value
    # The table-plan image, fetched like any staff file.
    out["table_plan_file_url"] = (
        f"/api/functions/files/{e.table_plan_file_id}" if e.table_plan_file_id else None
    )
    return out


def _event_rows_json(db: Session, ctx: FunctionsCtx, rows) -> list[dict]:
    """``list_events`` rows for the UI, with each contact's company (one
    query for the lot)."""
    rows = list(rows)
    ids = {r.event.contact_id for r in rows}
    companies = (
        dict(
            db.query(FnContact.id, FnContact.company).filter(
                FnContact.organization_id == ctx.org_id, FnContact.id.in_(ids)
            )
        )
        if ids
        else {}
    )
    return [_event_row_json(r, companies.get(r.event.contact_id)) for r in rows]


def _event_row_json(r, contact_company: str | None = None) -> dict:
    e = r.event
    return {
        "id": e.id,
        "version": e.version,
        "name": e.name,
        "enquiry_id": e.enquiry_id,
        "contact_id": e.contact_id,
        "contact_name": r.contact_name,
        "contact_company": contact_company,
        "venue_id": e.venue_id,
        "venue_name": r.venue_name,
        "area_id": e.area_id,
        "area_name": r.area_name,
        "booking_type": e.booking_type,
        "event_date": _d(e.event_date),
        "start_time": _t(e.start_time),
        "end_time": _t(e.end_time),
        "ends_next_day": bool(e.ends_next_day),
        "guests": e.guests,
        "bar_tab_cents": e.bar_tab_cents,
        "status": r.status,
        "edited_since_confirmed": bool(r.edited_since_confirmed),
        "min_spend_cents": r.min_spend_cents,
        "food_cents": int(r.food_cents or 0),
        "expected_spend_cents": int(r.expected_spend_cents or 0),
        "cancelled_at": _ts(e.cancelled_at),
    }


def _menu_json(menu, items) -> dict:
    plan = (
        menus_svc.set_menu_quantities(items, menu.guest_count)
        if menu.is_set_menu
        else {}
    )
    return {
        "id": menu.id,
        "version": menu.version,
        "enquiry_id": menu.enquiry_id,
        "event_id": menu.event_id,
        "venue_id": menu.venue_id,
        "template_id": menu.template_id,
        "name": menu.name,
        "is_set_menu": bool(menu.is_set_menu),
        "price_per_person_cents": menu.price_per_person_cents,
        "guest_count": menu.guest_count,
        "notes": menu.notes,
        "sort_order": menu.sort_order,
        "total_cents": money_svc.menu_food_cents(menu, items),
        "items": [
            {
                "id": i.id,
                "template_item_id": i.template_item_id,
                "kind": i.kind,
                "name": i.name,
                "description": i.description,
                "quantity": i.quantity,
                "unit_price_cents": i.unit_price_cents,
                "line_total_cents": i.quantity * i.unit_price_cents
                if i.unit_price_cents is not None and i.kind == "item"
                else None,
                "default_percent": i.default_percent,
                "suggested_quantity": plan.get(idx),
                "dietaries": list(i.dietaries or []),
                "loaded_recipe_id": i.loaded_recipe_id,
                "loaded_stock_item_id": i.loaded_stock_item_id,
            }
            for idx, i in enumerate(items)
        ],
    }


def _area_json(a: FnVenueArea, photos=None) -> dict:
    out = {
        "id": a.id,
        "version": a.version,
        "venue_id": a.venue_id,
        "name": a.name,
        "description": a.description,
        "capacity_seated": a.capacity_seated,
        "capacity_standing": a.capacity_standing,
        "booking_types": list(a.booking_types or []),
        "requires_approval": bool(a.requires_approval),
        "min_spend_notes": a.min_spend_notes,
        "active": bool(a.active),
        "sort_order": a.sort_order,
    }
    if photos is not None:
        out["photos"] = [_photo_json(p) for p in photos]
    return out


def _photo_json(p) -> dict:
    return {
        "id": p.id,
        "area_id": p.area_id,
        "file_id": p.file_id,
        "caption": p.caption,
        "sort_order": p.sort_order,
    }


def _settings_json(s, names: dict | None = None) -> dict | None:
    if s is None:
        return None
    names = names or {}
    return {
        "id": s.id,
        "version": s.version,
        "venue_id": s.venue_id,
        "approver_user_id": s.approver_user_id,
        "approver_name": names.get(s.approver_user_id),
        "override_approver_user_id": s.override_approver_user_id,
        "override_approver_name": names.get(s.override_approver_user_id),
        "policies": s.policies,
        "menu_links": list(s.menu_links or []),
        "enabled_fields": dict(s.enabled_fields or {}),
        "notify_emails": list(s.notify_emails or []),
    }


def _brand_json(b) -> dict | None:
    if b is None:
        return None
    return {
        "id": b.id,
        "version": b.version,
        "venue_id": b.venue_id,
        **{
            f: (
                list(getattr(b, f) or [])
                if f in ("terms", "inclusions")
                else getattr(b, f)
            )
            for f in venue_profile.BRAND_FIELDS
        },
    }


def _image_json(i) -> dict:
    return {
        "id": i.id,
        "version": i.version,
        "venue_id": i.venue_id,
        "file_id": i.file_id,
        "name": i.name,
        "caption": i.caption,
        "tags": list(i.tags or []),
        "created_at": _ts(i.created_at),
    }


def _template_json(t, items) -> dict:
    return {
        "id": t.id,
        "version": t.version,
        "venue_id": t.venue_id,
        "name": t.name,
        "description": t.description,
        "is_set_menu": bool(t.is_set_menu),
        "price_per_person_cents": t.price_per_person_cents,
        "loaded_menu_id": t.loaded_menu_id,
        "active": bool(t.active),
        "sort_order": t.sort_order,
        "items": [
            {
                "id": i.id,
                "kind": i.kind,
                "name": i.name,
                "description": i.description,
                "unit_price_cents": i.unit_price_cents,
                "default_percent": i.default_percent,
                "dietaries": list(i.dietaries or []),
                "loaded_recipe_id": i.loaded_recipe_id,
                "loaded_stock_item_id": i.loaded_stock_item_id,
            }
            for i in items
        ],
        "warnings": templates_svc.template_warnings(t, items),
    }


def _reply_json(r) -> dict:
    return {
        "id": r.id,
        "version": r.version,
        "venue_id": r.venue_id,
        "booking_type": r.booking_type,
        "stage": r.stage,
        "name": r.name,
        "subject": r.subject,
        "body_html": r.body_html,
        "active": bool(r.active),
        "sort_order": r.sort_order,
    }


def _proposal_json(p) -> dict | None:
    if p is None:
        return None
    return {
        "id": p.id,
        "version": p.version,
        "enquiry_id": p.enquiry_id,
        "html": p.html,
        "css": p.css,
        "based_on_brand_version": p.based_on_brand_version,
        "updated_by_user_id": p.updated_by_user_id,
        "updated_by_label": p.updated_by_label,
        "updated_at": _ts(p.updated_at),
    }


def _version_json(v, *, full: bool = False) -> dict:
    out = {
        "id": v.id,
        "proposal_id": v.proposal_id,
        "enquiry_id": v.enquiry_id,
        "version_number": v.version_number,
        "options": list(v.options or []),
        "sent_at": _ts(v.sent_at),
        "sent_by_user_id": v.sent_by_user_id,
        "sent_by_label": v.sent_by_label,
        "created_at": _ts(v.created_at),
    }
    if full:
        out["html"] = v.html
        out["css"] = v.css
    return out


def _change_json(ch, names: dict) -> dict:
    return {
        "id": ch.id,
        "kind": ch.kind,
        "section": ch.section,
        "changes": ch.changes,
        "after_confirmation": bool(ch.after_confirmation),
        "note": ch.note,
        "actor_kind": ch.actor_kind,
        "actor": reports._actor(
            names, ch.actor_user_id, ch.client_email or ch.actor_label
        ),
        "occurred_at": _ts(ch.occurred_at),
    }


# ===========================================================================
# Meta
# ===========================================================================


@router.get("/meta")
def meta(
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Everything the Functions pages load first: company settings, the
    caller, their venues (with each one's settings summary, enabled fields
    per booking type, and whether the caller approves there), the people of
    the organisation, and the vocabularies."""
    with _reading():
        company = get_company_settings(db, ctx.org_id)
        venues = (
            db.query(Venue)
            .filter(Venue.organization_id == ctx.org_id, Venue.id.in_(ctx.venue_ids))
            .order_by(Venue.name)
            .all()
            if ctx.venue_ids
            else []
        )
        settings = venue_profile.settings_for_venues(db, ctx, [v.id for v in venues])
        people = venue_profile.members(db, ctx)
        names = {m["id"]: m["name"] for m in people}
        brands = {
            b.venue_id
            for b in db.query(FnVenueBrand.venue_id).filter(
                FnVenueBrand.organization_id == ctx.org_id
            )
        }
        out_venues = []
        for v in venues:
            s = settings.get(v.id)
            try:
                validate_timezone(v.timezone or "")
                tz_ok = True
            except FunctionsError:
                tz_ok = False
            out_venues.append(
                {
                    "id": v.id,
                    "name": v.name,
                    "timezone": v.timezone,
                    "timezone_valid": tz_ok,
                    "day_start_time": v.day_start_time,
                    "is_approver": bool(s and s.approver_user_id == ctx.user_id),
                    "is_override_approver": bool(
                        s and s.override_approver_user_id == ctx.user_id
                    ),
                    "has_brand": v.id in brands,
                    "settings": _settings_json(s, names),
                    "enabled_fields": {
                        bt: venue_profile.enabled_fields_for(s, bt)
                        for bt in BOOKING_TYPES
                    },
                }
            )
        user = ctx.user
        return {
            "company": {**company.as_dict(), "name": _org_name(db, ctx.org_id)},
            "mode": ctx.mode,
            "can_write": ctx.is_live,
            "user": {
                "id": ctx.user_id,
                "name": getattr(user, "full_name", None),
                "email": getattr(user, "email", None),
                "is_owner": ctx.is_owner,
            },
            "venues": out_venues,
            "members": people,
            "booking_types": [
                {
                    "value": bt,
                    "label": events_svc.BOOKING_LABELS[bt],
                    "min_spend": bt in MIN_SPEND_BOOKING_TYPES,
                }
                for bt in BOOKING_TYPES
            ],
            "field_catalogue": [
                {"section": section, "key": key, "label": label}
                for section, key, label in venue_profile.FIELD_CATALOGUE
            ],
            "stages": list(status_svc.STAGES),
            "event_statuses": list(status_svc.EVENT_STATUSES),
            "min_spend_states": list(status_svc.MIN_SPEND_STATES),
            "next_actions": [
                {"kind": k, "label": status_svc.NEXT_ACTION_LABELS[k]}
                for k in status_svc.NEXT_ACTION_KINDS
            ],
            "enquiry_sources": list(ENQUIRY_SOURCES),
            "staff_channels": ["email", "phone", "in_person"],
            "food_timing_types": [
                {"value": v, "label": reports.FOOD_TIMING_LABELS[v]}
                for v in FOOD_TIMING_TYPES
            ],
            "entertainment_kinds": list(ENTERTAINMENT_KINDS),
            "av_kinds": list(AV_KINDS),
            "event_sections": list(EVENT_SECTIONS),
            "reply_template_stages": list(REPLY_TEMPLATE_STAGES),
            "dietary_codes": list(templates_svc.DIETARY_CODES),
            "client_fields": sorted(events_svc.CLIENT_FIELDS),
            "reopen_rule": enquiries_svc.REOPEN_RULE,
        }


def _org_name(db: Session, org_id: str) -> str | None:
    from app.db.models import Organization

    row = db.query(Organization.name).filter(Organization.id == org_id).first()
    return row[0] if row else None


# ===========================================================================
# Dashboard and reports
# ===========================================================================


@router.get("/dashboard")
def dashboard(
    venue: str | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """KPIs, pipeline, next 4 weeks, conversion and the needs-attention list.
    ``venue``: a venue id, ``unassigned`` (group-wide enquiries), or empty
    for every venue the caller has."""
    with _reading():
        return reports.dashboard(db, ctx, venue=venue)


@router.get("/reports/enquiries")
def report_enquiries(
    date_from: dt.date | None = Query(None, alias="from"),
    date_to: dt.date | None = Query(None, alias="to"),
    venue: str | None = None,
    stage: str | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _reading():
        return reports.enquiries_over_time(
            db, ctx, date_from=date_from, date_to=date_to, venue=venue, stage=stage
        )


@router.get("/reports/conversion")
def report_conversion(
    weeks: int = 4,
    lag: int = 4,
    venue: str | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _reading():
        venue_ids, unassigned = reports.venue_filter(ctx, venue)
        return reports.conversion(
            db,
            ctx,
            weeks=weeks,
            lag_weeks=lag,
            venue_ids=venue_ids,
            unassigned=unassigned,
            with_rows=True,
        )


@router.get("/reports/upcoming-events")
def report_upcoming(
    days: int | None = None,
    venue: str | None = None,
    status: list[str] | None = Query(None),
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _reading():
        out = reports.upcoming_events(
            db, ctx, days=days, venue=venue, statuses=_multi(status)
        )
        out["rows"] = _event_rows_json(db, ctx, out["rows"])
        return out


@router.get("/reports/sales")
def report_sales(
    date_from: dt.date | None = Query(None, alias="from"),
    days: int = 28,
    venue: str | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Expected spend of live events from ``from`` (default today) for
    ``days`` days, in total and by week."""
    with _reading():
        if not 1 <= days <= 366:
            raise HTTPException(
                422, {"code": "invalid", "message": "days must be 1–366."}
            )
        venue_ids, _ = reports.venue_filter(
            ctx, venue if venue != "unassigned" else None
        )
        start = date_from or dt.datetime.now(reports.org_tz(db, ctx, venue)).date()
        return reports.expected_sales(
            db, ctx, venue_ids=venue_ids, start=start, days=days
        )


@router.get("/reports/lead-sources")
def report_lead_sources(
    date_from: dt.date | None = Query(None, alias="from"),
    date_to: dt.date | None = Query(None, alias="to"),
    venue: str | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _reading():
        return reports.lead_sources(
            db, ctx, date_from=date_from, date_to=date_to, venue=venue
        )


@router.get("/reports/approvals")
def report_approvals(
    date_from: dt.date | None = Query(None, alias="from"),
    date_to: dt.date | None = Query(None, alias="to"),
    venue: str | None = None,
    kind: str | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _reading():
        return reports.approvals_log(
            db, ctx, date_from=date_from, date_to=date_to, venue=venue, kind=kind
        )


# ===========================================================================
# Contacts
# ===========================================================================


class ContactIn(_Body):
    name: str
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    notes: str | None = None


class ContactPatch(_Body):
    version: int | None = None
    name: str | None = None
    company: str | None = None
    phone: str | None = None
    notes: str | None = None


class EmailIn(_Body):
    email: str
    primary: bool = False


class PrimaryEmailIn(_Body):
    email: str


class AnonymiseIn(_Body):
    confirm: bool


def _contact_out(db: Session, ctx: FunctionsCtx, contact_id: str) -> dict:
    contact = contacts_svc.get_contact(db, ctx, contact_id)
    return _contact_json(contact, contacts_svc.contact_emails(db, ctx, contact.id))


def _contact_page(
    db: Session, ctx: FunctionsCtx, q, include_archived: bool, limit: int, offset: int
) -> list[FnContact]:
    """A page of ``contacts.search_contacts`` (visibility, match fields and
    order are the service's)."""
    return contacts_svc.search_contacts(
        db, ctx, q, include_archived=include_archived, limit=limit, offset=offset
    )


@router.get("/contacts")
def list_contacts(
    q: str | None = None,
    include_archived: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Contacts the caller can see (search: name, company, phone, email),
    each with enquiry / booking counts and conversion."""
    with _reading():
        rows, more = _page(
            _contact_page(db, ctx, q, include_archived, limit + 1, offset), limit
        )
        counts = reports.contact_counts(db, ctx, [c.id for c in rows])
        emails: dict[str, list] = {}
        for e in (
            db.query(contacts_svc.FnContactEmail)
            .filter(
                contacts_svc.FnContactEmail.organization_id == ctx.org_id,
                contacts_svc.FnContactEmail.contact_id.in_(
                    [c.id for c in rows] or [""]
                ),
            )
            .order_by(
                contacts_svc.FnContactEmail.is_primary.desc(),
                contacts_svc.FnContactEmail.created_at,
            )
        ):
            emails.setdefault(e.contact_id, []).append(e)
        return {
            "rows": [
                {**_contact_json(c, emails.get(c.id, [])), "counts": counts[c.id]}
                for c in rows
            ],
            "limit": limit,
            "offset": offset,
            "has_more": more,
        }


@router.post("/contacts", status_code=201)
def create_contact(
    body: ContactIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        contact = contacts_svc.create_contact(db, ctx, **body.model_dump())
    with _reading():
        return _contact_out(db, ctx, contact.id)


@router.get("/contacts/{contact_id}")
def get_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """The contact, its addresses, and its enquiries and events (at the
    caller's venues)."""
    with _reading():
        out = _contact_out(db, ctx, contact_id)
        enquiries = enquiries_svc.list_enquiries(
            db, ctx, contact_id=contact_id, include_archived=True, limit=200
        )
        values = reports.enquiry_values(db, ctx, [r.enquiry.id for r in enquiries])
        out["enquiries"] = [
            _enquiry_row_json(r, values.get(r.enquiry.id)) for r in enquiries
        ]
        out["events"] = _event_rows_json(
            db,
            ctx,
            events_svc.list_events(
                db, ctx, contact_id=contact_id, include_cancelled=True, sort="date_desc"
            ),
        )
        out["counts"] = reports.contact_counts(db, ctx, [contact_id])[contact_id]
        return out


@router.patch("/contacts/{contact_id}")
def update_contact(
    contact_id: str,
    body: ContactPatch,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    fields = _no_nulls(_given(body, "version"), FnContact)
    with _writing(db):
        changes = contacts_svc.update_contact(
            db, ctx, contact_id, expected_version=body.version, **fields
        )
    with _reading():
        return {**_contact_out(db, ctx, contact_id), "changes": changes}


@router.post("/contacts/{contact_id}/emails")
def add_contact_email(
    contact_id: str,
    body: EmailIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        contacts_svc.add_contact_email(
            db, ctx, contact_id, body.email, primary=body.primary
        )
    with _reading():
        return _contact_out(db, ctx, contact_id)


@router.put("/contacts/{contact_id}/emails/primary")
def set_primary_email(
    contact_id: str,
    body: PrimaryEmailIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        contacts_svc.set_primary_email(db, ctx, contact_id, body.email)
    with _reading():
        return _contact_out(db, ctx, contact_id)


@router.delete("/contacts/{contact_id}/emails")
def remove_contact_email(
    contact_id: str,
    email: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        contacts_svc.remove_contact_email(db, ctx, contact_id, email)
    with _reading():
        return _contact_out(db, ctx, contact_id)


@router.post("/contacts/{contact_id}/archive")
def archive_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        contacts_svc.archive_contact(db, ctx, contact_id)
    with _reading():
        return _contact_out(db, ctx, contact_id)


@router.post("/contacts/{contact_id}/unarchive")
def unarchive_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        contacts_svc.unarchive_contact(db, ctx, contact_id)
    with _reading():
        return _contact_out(db, ctx, contact_id)


@router.post("/contacts/{contact_id}/anonymise")
def anonymise_contact(
    contact_id: str,
    body: AnonymiseIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Privacy request: take the person out of their records (owner only,
    irreversible — the body must say ``{"confirm": true}``)."""
    if not body.confirm:
        raise HTTPException(
            422,
            {
                "code": "invalid",
                "message": "Confirm the anonymisation — it can't be undone.",
            },
        )
    with _writing(db):
        result = privacy_svc.anonymise_contact(db, ctx, contact_id)
    return result


@router.delete("/contacts/{contact_id}", status_code=204)
def delete_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        contacts_svc.delete_contact(db, ctx, contact_id)
    return Response(status_code=204)


# ===========================================================================
# Enquiries
# ===========================================================================


class ContactRef(_Body):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    company: str | None = None


class AttributionIn(_Body):
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_term: str | None = None
    utm_content: str | None = None
    gclid: str | None = None
    referrer: str | None = None
    landing_page: str | None = None


class EnquiryIn(_Body):
    contact_id: str | None = None
    contact: ContactRef | None = None
    venue_id: str | None = None
    booking_type: str | None = None
    subject: str | None = None
    guests: int | None = None
    preferred_date_start: dt.date | None = None
    preferred_date_end: dt.date | None = None
    preferred_start_time: dt.time | None = None
    preferred_end_time: dt.time | None = None
    preferred_ends_next_day: bool = False
    source: str = "manual"
    attribution: AttributionIn | None = None
    owner_user_id: str | None = None
    follow_up_date: dt.date | None = None
    follow_up_reason: str | None = None
    client_note: str | None = None
    notes: str | None = None


class EnquiryPatch(_Body):
    version: int | None = None
    contact_id: str | None = None
    venue_id: str | None = None
    booking_type: str | None = None
    subject: str | None = None
    guests: int | None = None
    preferred_date_start: dt.date | None = None
    preferred_date_end: dt.date | None = None
    preferred_start_time: dt.time | None = None
    preferred_end_time: dt.time | None = None
    preferred_ends_next_day: bool | None = None
    source: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_term: str | None = None
    utm_content: str | None = None
    gclid: str | None = None
    referrer: str | None = None
    landing_page: str | None = None
    owner_user_id: str | None = None
    follow_up_date: dt.date | None = None
    follow_up_reason: str | None = None
    client_note: str | None = None
    notes: str | None = None


class ReasonIn(_Body):
    reason: str


class MaybeReasonIn(_Body):
    reason: str | None = None


class FollowUpIn(_Body):
    date: dt.date
    reason: str | None = None


class ThreadLinkIn(_Body):
    account_id: str
    gmail_thread_id: str


class ConfirmBookingIn(_Body):
    channel: Literal["email", "phone", "in_person"]
    event_date: dt.date | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    ends_next_day: bool | None = None
    venue_id: str | None = None
    area_id: str | None = None
    name: str | None = None
    guests: int | None = None
    note: str | None = None


@router.get("/enquiries")
def list_enquiries(
    venue: list[str] | None = Query(None),
    stage: list[str] | None = Query(None),
    next_action: list[str] | None = Query(None),
    due_only: bool = False,
    source: list[str] | None = Query(None),
    booking_type: list[str] | None = Query(None),
    owner_user_id: str | None = None,
    contact_id: str | None = None,
    created_from: dt.date | None = None,
    created_to: dt.date | None = None,
    preferred_from: dt.date | None = None,
    preferred_to: dt.date | None = None,
    q: str | None = None,
    include_archived: bool = False,
    sort: str = "created_desc",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """The enquiries list, filtered and sorted by derived stage and next
    action, with the page's KPIs (replies owed, follow-ups due, open value)
    over the same venues. ``created_from``/``created_to`` are civil dates in
    the company timezone (``created_to`` inclusive). ``venue``: venue ids
    and/or ``unassigned`` (group-wide enquiries, which every venue filter
    keeps). ``sort``: F1's ``created_desc|created_asc|due|preferred_date|
    last_activity``, or ``contact|subject|guests|stage|value`` + ``_asc|_desc``
    (applied over every matching row, blanks last)."""
    with _reading():
        tz = reports.org_tz(db, ctx)
        venues = _multi(venue)
        filters = dict(
            venue_ids=venues,
            stages=_multi(stage),
            next_actions=_multi(next_action),
            due_only=due_only,
            sources=_multi(source),
            booking_types=_multi(booking_type),
            owner_user_id=owner_user_id,
            contact_id=contact_id,
            created_from=reports.to_instant(tz, created_from) if created_from else None,
            created_to=reports.to_instant(tz, created_to + dt.timedelta(days=1))
            if created_to
            else None,
            preferred_from=preferred_from,
            preferred_to=preferred_to,
            q=q,
            include_archived=include_archived,
        )
        # Sorts F1 knows run in SQL (enquiries.SORTS); only ``value`` still
        # reads every row.
        by_row = None if sort in enquiries_svc.SORTS else reports.row_sort(sort)
        if by_row is None:
            rows = enquiries_svc.list_enquiries(
                db, ctx, **filters, sort=sort, limit=limit + 1, offset=offset
            )
        else:
            field, descending = by_row
            every = reports.all_enquiry_rows(
                db, ctx, now=dt.datetime.now(dt.UTC), **filters
            )
            values = (
                reports.enquiry_values(db, ctx, [r.enquiry.id for r in every])
                if field == "value"
                else None
            )
            rows = reports.sort_rows(every, field, descending, values)[
                offset : offset + limit + 1
            ]
        rows, more = _page(rows, limit)
        facts = reports.list_page_facts(db, ctx, [r.enquiry.id for r in rows])
        # The tiles count what the rows include: a venue filter keeps the
        # group-wide enquiries, and ``unassigned`` alone means only those.
        at_venues = [v for v in venues or [] if v != "unassigned"]
        return {
            "rows": [
                _enquiry_list_row_json(r, facts.get(r.enquiry.id, {})) for r in rows
            ],
            "kpis": reports.enquiry_kpis(
                db,
                ctx,
                venue_ids=at_venues or None,
                unassigned=bool(venues) and not at_venues,
                group_wide=True,
            ),
            "limit": limit,
            "offset": offset,
            "has_more": more,
        }


def _enquiry_list_row_json(r, facts: dict) -> dict:
    """A list row: the shared enquiry row plus the list's extras
    (``reports.list_page_facts``)."""
    return {
        **_enquiry_row_json(r, facts.get("value_cents")),
        "contact_email": facts.get("contact_email"),
        "min_spend_summary": facts.get(
            "min_spend_summary",
            {"count": 0, "largest_amount_cents": None, "needs_approval": False},
        ),
        "last_email_from": facts.get("last_email_from"),
    }


@router.post("/enquiries", status_code=201)
def create_enquiry(
    body: EnquiryIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """A new enquiry, for an existing contact (``contact_id``) or a new /
    matched-by-email one (``contact``)."""
    fields = body.model_dump(exclude={"contact", "attribution"})
    with _writing(db):
        enquiry = enquiries_svc.create_enquiry(
            db,
            ctx,
            contact=body.contact.model_dump() if body.contact else None,
            attribution=body.attribution.model_dump() if body.attribution else None,
            **fields,
        )
    with _reading():
        return _enquiry_detail(db, ctx, enquiry.id)


def _threads_json(db: Session, ctx: FunctionsCtx, links) -> list[dict]:
    """Linked threads with their mirror rollups (read-only — the mailbox
    owns them); mailboxes at venues the caller can't see are left out."""
    if not links:
        return []
    accounts = {
        a.id: a
        for a in db.query(FnMailboxAccount).filter(
            FnMailboxAccount.organization_id == ctx.org_id,
            FnMailboxAccount.id.in_({lk.account_id for lk in links}),
            ctx.venue_clause(FnMailboxAccount.venue_id),
        )
    }
    threads = {
        (t.account_id, t.gmail_thread_id): t
        for t in db.query(FnMailboxThread).filter(
            FnMailboxThread.organization_id == ctx.org_id,
            FnMailboxThread.account_id.in_(list(accounts) or [""]),
            FnMailboxThread.gmail_thread_id.in_({lk.gmail_thread_id for lk in links}),
        )
    }
    out = []
    for lk in links:
        account = accounts.get(lk.account_id)
        if account is None:
            continue
        t = threads.get((lk.account_id, lk.gmail_thread_id))
        out.append(
            {
                "link_id": lk.id,
                "account_id": lk.account_id,
                "mailbox": account.google_email,
                "venue_id": account.venue_id,
                "gmail_thread_id": lk.gmail_thread_id,
                "linked_at": _ts(lk.linked_at),
                "mirrored": t is not None,
                "subject": t.subject if t else None,
                "snippet": t.snippet if t else None,
                "last_message_at": _ts(t.last_message_at) if t else None,
                "last_from_email": t.last_from_email if t else None,
                "last_from_name": t.last_from_name if t else None,
                "message_count": t.message_count if t else None,
                "unread_count": t.unread_count if t else None,
            }
        )
    return out


def _options_json(db: Session, ctx: FunctionsCtx, rows, food_by_venue: dict) -> list:
    """Options with derived state, what the caller may do to them, their
    totals with food, and their approval and response facts."""
    if not rows:
        return []
    ids = [o.id for o, _, _ in rows]
    approvals: dict[str, list] = {}
    for a in (
        db.query(FnMinSpendApproval)
        .filter(
            FnMinSpendApproval.organization_id == ctx.org_id,
            FnMinSpendApproval.min_spend_id.in_(ids),
        )
        .order_by(FnMinSpendApproval.occurred_at, FnMinSpendApproval.created_at)
    ):
        approvals.setdefault(a.min_spend_id, []).append(a)
    responses: dict[str, list] = {}
    for r in (
        db.query(FnMinSpendResponse)
        .filter(
            FnMinSpendResponse.organization_id == ctx.org_id,
            FnMinSpendResponse.min_spend_id.in_(ids),
        )
        .order_by(FnMinSpendResponse.occurred_at, FnMinSpendResponse.created_at)
    ):
        responses.setdefault(r.min_spend_id, []).append(r)
    areas = {
        a.id: a
        for a in db.query(FnVenueArea).filter(
            FnVenueArea.organization_id == ctx.org_id,
            FnVenueArea.id.in_({o.area_id for o, _, _ in rows if o.area_id} or {""}),
        )
    }
    names = reports.user_names(
        db,
        [a.actor_user_id for v in approvals.values() for a in v]
        + [r.recorded_by_user_id for v in responses.values() for r in v]
        + [o.created_by_user_id for o, _, _ in rows],
    )
    out = []
    for option, state, confirmable in rows:
        food = food_by_venue.get(option.venue_id, 0)
        area = areas.get(option.area_id)
        out.append(
            {
                "id": option.id,
                "version": option.version,
                "enquiry_id": option.enquiry_id,
                "event_id": option.event_id,
                "venue_id": option.venue_id,
                "area_id": option.area_id,
                "area_name": area.name if area else None,
                "area_requires_approval": area.requires_approval if area else True,
                "label": option.label,
                "amount_cents": option.amount_cents,
                "food_cents": food,
                "total_cents": money_svc.option_total_cents(option.amount_cents, food),
                "event_date": _d(option.event_date),
                "start_time": _t(option.start_time),
                "end_time": _t(option.end_time),
                "ends_next_day": bool(option.ends_next_day),
                "notes": option.notes,
                "sort_order": option.sort_order,
                "state": state,
                "confirmable": confirmable,
                "rights": min_spends_svc.approval_rights(db, ctx, option),
                "withdrawn_at": _ts(option.withdrawn_at),
                "withdraw_reason": option.withdraw_reason,
                "created_by": reports._actor(
                    names, option.created_by_user_id, option.created_by_label
                ),
                "created_at": _ts(option.created_at),
                "approvals": [
                    {
                        "id": a.id,
                        "kind": a.kind,
                        "automatic": bool(a.automatic),
                        "reason": a.reason,
                        "amount_cents": a.amount_cents,
                        "actor": reports._actor(names, a.actor_user_id, a.actor_label),
                        "occurred_at": _ts(a.occurred_at),
                    }
                    for a in approvals.get(option.id, [])
                ],
                "responses": [
                    {
                        "id": r.id,
                        "kind": r.kind,
                        "channel": r.channel,
                        "customer_email": r.customer_email,
                        "note": r.note,
                        "recorded_by": reports._actor(
                            names, r.recorded_by_user_id, None
                        ),
                        "occurred_at": _ts(r.occurred_at),
                    }
                    for r in responses.get(option.id, [])
                ],
            }
        )
    return out


def _food_by_venue(menus) -> dict[str, int]:
    out: dict[str, int] = {}
    for menu, items in menus:
        out[menu.venue_id] = out.get(menu.venue_id, 0) + money_svc.menu_food_cents(
            menu, items
        )
    return out


def _enquiry_detail(db: Session, ctx: FunctionsCtx, enquiry_id: str) -> dict:
    enquiry = enquiries_svc.get_enquiry(db, ctx, enquiry_id)
    stage, action = enquiries_svc.enquiry_state(db, ctx, enquiry.id)
    contact = (
        db.query(FnContact)
        .filter(
            FnContact.organization_id == ctx.org_id, FnContact.id == enquiry.contact_id
        )
        .first()
    )
    emails = (
        db.query(contacts_svc.FnContactEmail)
        .filter(
            contacts_svc.FnContactEmail.organization_id == ctx.org_id,
            contacts_svc.FnContactEmail.contact_id == enquiry.contact_id,
        )
        .order_by(
            contacts_svc.FnContactEmail.is_primary.desc(),
            contacts_svc.FnContactEmail.created_at,
        )
        .all()
    )
    menus = menus_svc.list_menus(db, ctx, enquiry_id=enquiry.id)
    food_by_venue = _food_by_venue(menus)
    options = min_spends_svc.list_min_spends(db, ctx, enquiry_id=enquiry.id)
    history = [
        r
        for r in enquiries_svc.list_enquiries(
            db, ctx, contact_id=enquiry.contact_id, include_archived=True, limit=50
        )
        if r.enquiry.id != enquiry.id
    ]
    venue = (
        db.query(Venue).filter(Venue.id == enquiry.venue_id).first()
        if enquiry.venue_id
        else None
    )
    links = enquiries_svc.thread_links(db, ctx, enquiry_id=enquiry.id)
    names = reports.user_names(db, [enquiry.owner_user_id])
    from app.services.functions import public_tokens

    return {
        "enquiry": _enquiry_json(enquiry),
        "stage": stage,
        "next_action": action.as_dict() if action else None,
        "venue": {"id": venue.id, "name": venue.name, "timezone": venue.timezone}
        if venue
        else None,
        "owner_name": names.get(enquiry.owner_user_id),
        "contact": _contact_json(contact, emails) if contact else None,
        "contact_history": {
            "enquiries": [_enquiry_row_json(r) for r in history],
            "events": _event_rows_json(
                db,
                ctx,
                (
                    r
                    for r in events_svc.list_events(
                        db,
                        ctx,
                        contact_id=enquiry.contact_id,
                        include_cancelled=True,
                        sort="date_desc",
                        limit=50,
                    )
                    if r.event.enquiry_id != enquiry.id
                ),
            ),
        },
        "options": _options_json(db, ctx, options, food_by_venue),
        "menus": [_menu_json(m, items) for m, items in menus],
        "food_cents": sum(food_by_venue.values()),
        "value_cents": reports.enquiry_values(db, ctx, [enquiry.id])[enquiry.id],
        "events": _event_rows_json(
            db,
            ctx,
            events_svc.list_events(
                db, ctx, enquiry_id=enquiry.id, include_cancelled=True
            ),
        ),
        "closures": [
            {
                "id": c.id,
                "kind": c.kind,
                "reason": c.reason,
                "actor_label": c.actor_label,
                "actor_user_id": c.actor_user_id,
                "occurred_at": _ts(c.occurred_at),
            }
            for c in enquiries_svc.closures(db, ctx, enquiry.id)
        ],
        "proposal": proposals_svc.summary(db, ctx, enquiry.id),
        "quote_links": public_tokens.links(db, ctx, enquiry_id=enquiry.id),
        "threads": _threads_json(db, ctx, links),
        "timeline": reports.enquiry_timeline(db, ctx, enquiry.id),
    }


@router.get("/enquiries/{enquiry_id}")
def get_enquiry(
    enquiry_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """The enquiry page in one call: the enquiry, its stage and next action,
    the contact and their other enquiries and events, options (state,
    rights, totals, approvals, answers), pre-order menus with totals, its
    bookings, closures, the proposal summary, linked threads and the
    activity timeline."""
    with _reading():
        return _enquiry_detail(db, ctx, enquiry_id)


@router.get("/enquiries/{enquiry_id}/timeline")
def enquiry_timeline(
    enquiry_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _reading():
        return {"items": reports.enquiry_timeline(db, ctx, enquiry_id)}


@router.patch("/enquiries/{enquiry_id}")
def update_enquiry(
    enquiry_id: str,
    body: EnquiryPatch,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    fields = _no_nulls(_given(body, "version"), FnEnquiry)
    with _writing(db):
        changes = enquiries_svc.update_enquiry(
            db, ctx, enquiry_id, expected_version=body.version, **fields
        )
    with _reading():
        return {**_enquiry_detail(db, ctx, enquiry_id), "changes": changes}


@router.post("/enquiries/{enquiry_id}/close")
def close_enquiry(
    enquiry_id: str,
    body: ReasonIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        enquiries_svc.close_enquiry(db, ctx, enquiry_id, reason=body.reason)
    with _reading():
        return _enquiry_detail(db, ctx, enquiry_id)


@router.post("/enquiries/{enquiry_id}/reopen")
def reopen_enquiry(
    enquiry_id: str,
    body: ReasonIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        enquiries_svc.reopen_enquiry(db, ctx, enquiry_id, reason=body.reason)
    with _reading():
        return _enquiry_detail(db, ctx, enquiry_id)


@router.post("/enquiries/{enquiry_id}/archive")
def archive_enquiry(
    enquiry_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        enquiries_svc.archive_enquiry(db, ctx, enquiry_id)
    with _reading():
        return _enquiry_detail(db, ctx, enquiry_id)


@router.post("/enquiries/{enquiry_id}/unarchive")
def unarchive_enquiry(
    enquiry_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        enquiries_svc.unarchive_enquiry(db, ctx, enquiry_id)
    with _reading():
        return _enquiry_detail(db, ctx, enquiry_id)


@router.put("/enquiries/{enquiry_id}/follow-up")
def set_follow_up(
    enquiry_id: str,
    body: FollowUpIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        enquiries_svc.set_follow_up(
            db, ctx, enquiry_id, date=body.date, reason=body.reason
        )
    with _reading():
        return _enquiry_detail(db, ctx, enquiry_id)


@router.delete("/enquiries/{enquiry_id}/follow-up")
def clear_follow_up(
    enquiry_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        enquiries_svc.clear_follow_up(db, ctx, enquiry_id)
    with _reading():
        return _enquiry_detail(db, ctx, enquiry_id)


@router.post("/enquiries/{enquiry_id}/threads", status_code=201)
def link_enquiry_thread(
    enquiry_id: str,
    body: ThreadLinkIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        link = enquiries_svc.link_thread(
            db,
            ctx,
            account_id=body.account_id,
            gmail_thread_id=body.gmail_thread_id,
            enquiry_id=enquiry_id,
        )
    with _reading():
        return {"link_id": link.id, **_enquiry_detail(db, ctx, enquiry_id)}


@router.delete("/thread-links/{link_id}")
def unlink_thread(
    link_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        link = enquiries_svc.unlink_thread(db, ctx, link_id)
    return {
        "link_id": link.id,
        "enquiry_id": link.enquiry_id,
        "event_id": link.event_id,
        "unlinked_at": _ts(link.unlinked_at),
    }


@router.post("/enquiries/{enquiry_id}/confirm-booking")
def confirm_booking(
    enquiry_id: str,
    body: ConfirmBookingIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """The customer confirmed a booking with no minimum spend (seated
    dining, group bookings) by email, phone or in person: create its event,
    once. Returns ``{event, created}``."""
    with _writing(db):
        event, created = events_svc.confirm_booking(
            db, ctx, enquiry_id, **body.model_dump()
        )
    with _reading():
        return {"event": _event_detail(db, ctx, event.id), "created": created}


# ===========================================================================
# Minimum spends
# ===========================================================================


class MinSpendIn(_Body):
    venue_id: str
    amount_cents: int
    area_id: str | None = None
    label: str | None = None
    event_date: dt.date | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    ends_next_day: bool = False
    notes: str | None = None
    sort_order: int = 0


class MinSpendPatch(_Body):
    version: int | None = None
    note: str | None = None
    venue_id: str | None = None
    area_id: str | None = None
    label: str | None = None
    amount_cents: int | None = None
    event_date: dt.date | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    ends_next_day: bool | None = None
    notes: str | None = None
    sort_order: int | None = None


class CustomerConfirmIn(_Body):
    channel: Literal["email", "phone", "in_person"]
    note: str | None = None
    customer_email: str | None = None
    name: str | None = None


class CustomerDeclineIn(_Body):
    channel: Literal["email", "phone", "in_person"]
    note: str | None = None
    customer_email: str | None = None


def _option_out(db: Session, ctx: FunctionsCtx, min_spend_id: str) -> dict:
    option = min_spends_svc.get_min_spend(db, ctx, min_spend_id)
    rows = [
        r
        for r in min_spends_svc.list_min_spends(
            db,
            ctx,
            enquiry_id=option.enquiry_id if option.enquiry_id else None,
            event_id=None if option.enquiry_id else option.event_id,
        )
        if r[0].id == option.id
    ]
    food = (
        money_svc.enquiry_food_cents(
            db, ctx.org_id, option.enquiry_id, venue_id=option.venue_id
        )
        if option.enquiry_id
        else money_svc.event_food_cents(db, ctx.org_id, option.event_id)
    )
    return _options_json(db, ctx, rows, {option.venue_id: food})[0]


@router.post("/enquiries/{enquiry_id}/min-spends", status_code=201)
def add_min_spend(
    enquiry_id: str,
    body: MinSpendIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        option = min_spends_svc.add_min_spend(
            db, ctx, enquiry_id=enquiry_id, **body.model_dump()
        )
    with _reading():
        return _option_out(db, ctx, option.id)


@router.get("/min-spends/{min_spend_id}")
def get_min_spend(
    min_spend_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _reading():
        return _option_out(db, ctx, min_spend_id)


@router.patch("/min-spends/{min_spend_id}")
def update_min_spend(
    min_spend_id: str,
    body: MinSpendPatch,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Edit an option. An approved option whose amount, area, venue or window
    changes is un-approved automatically; a booked one takes only amount /
    label / notes, with a ``note``."""
    fields = _no_nulls(_given(body, "version", "note"), FnMinSpend)
    with _writing(db):
        changes = min_spends_svc.update_min_spend(
            db,
            ctx,
            min_spend_id,
            expected_version=body.version,
            note=body.note,
            **fields,
        )
    with _reading():
        return {**_option_out(db, ctx, min_spend_id), "changes": changes}


@router.post("/min-spends/{min_spend_id}/withdraw")
def withdraw_min_spend(
    min_spend_id: str,
    body: MaybeReasonIn | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        min_spends_svc.withdraw_min_spend(
            db, ctx, min_spend_id, reason=body.reason if body else None
        )
    with _reading():
        return _option_out(db, ctx, min_spend_id)


@router.post("/min-spends/{min_spend_id}/approve")
def approve_min_spend(
    min_spend_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """The venue's approver says "OK to offer" (anyone, in an area needing
    no approval). Approval never confirms a booking."""
    with _writing(db):
        min_spends_svc.approve_min_spend(db, ctx, min_spend_id)
    with _reading():
        return _option_out(db, ctx, min_spend_id)


@router.post("/min-spends/{min_spend_id}/override")
def override_min_spend(
    min_spend_id: str,
    body: ReasonIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        min_spends_svc.override_min_spend(db, ctx, min_spend_id, reason=body.reason)
    with _reading():
        return _option_out(db, ctx, min_spend_id)


@router.post("/min-spends/{min_spend_id}/revoke")
def revoke_min_spend(
    min_spend_id: str,
    body: MaybeReasonIn | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        min_spends_svc.revoke_approval(
            db, ctx, min_spend_id, reason=body.reason if body else None
        )
    with _reading():
        return _option_out(db, ctx, min_spend_id)


@router.post("/min-spends/{min_spend_id}/confirm")
def confirm_min_spend(
    min_spend_id: str,
    body: CustomerConfirmIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Record the CUSTOMER's yes (by email, phone or in person). This
    creates the event — exactly once: a repeat returns the same event with
    ``created: false``; a sibling of a booked option is refused."""
    with _writing(db):
        event, created = min_spends_svc.confirm_option(
            db, ctx, min_spend_id, **body.model_dump()
        )
    with _reading():
        return {
            "event": _event_detail(db, ctx, event.id),
            "created": created,
            "option": _option_out(db, ctx, min_spend_id),
        }


@router.post("/min-spends/{min_spend_id}/decline")
def decline_min_spend(
    min_spend_id: str,
    body: CustomerDeclineIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        min_spends_svc.decline_option(db, ctx, min_spend_id, **body.model_dump())
    with _reading():
        return _option_out(db, ctx, min_spend_id)


@router.get("/min-spends/{min_spend_id}/reference")
def min_spend_reference(
    min_spend_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """The option's venue budget and last year's sales for its date."""
    with _reading():
        option = min_spends_svc.get_min_spend(db, ctx, min_spend_id)
        if option.event_date is None:
            return {"available": False, "reason": "The option has no date yet."}
        return reports.sales_reference(
            db, config_db, ctx, venue_id=option.venue_id, date=option.event_date
        )


@router.get("/venues/{venue_id}/sales-reference")
def venue_sales_reference(
    venue_id: str,
    date: dt.date,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Budget and last-year sales for a venue and date (for an option being
    drafted)."""
    with _reading():
        return reports.sales_reference(db, config_db, ctx, venue_id=venue_id, date=date)


# ===========================================================================
# Events
# ===========================================================================


class EventFields(_Body):
    """Every editable event field (``events.EVENT_FIELD_SECTIONS``)."""

    name: str | None = None
    contact_id: str | None = None
    invoice_contact_id: str | None = None
    booking_type: str | None = None
    area_id: str | None = None
    event_date: dt.date | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    ends_next_day: bool | None = None
    guests: int | None = None
    description: str | None = None
    notes: str | None = None
    special_instructions: str | None = None
    payment_method: str | None = None
    deposit_cents: int | None = None
    deposit_status: str | None = None
    table_plan: str | None = None
    table_plan_file_id: str | None = None
    arrival_time: dt.time | None = None
    seated_time: dt.time | None = None
    timing_preference: str | None = None
    timing_notes: str | None = None
    bar_tab_cents: int | None = None
    bar_tab_authorised_people: str | None = None
    bar_tab_restrictions: str | None = None
    bar_tab_notes: str | None = None
    arrival_drinks: str | None = None
    table_ordering: bool | None = None
    table_ordering_limit_cents: int | None = None
    allergies: str | None = None
    food_timing_type: str | None = None
    food_time: dt.time | None = None
    food_end_time: dt.time | None = None
    food_notes: str | None = None


class EventIn(EventFields):
    venue_id: str
    event_date: dt.date
    booking_type: str
    contact: ContactRef | None = None
    note: str | None = None


class EventPatch(EventFields):
    version: int | None = None
    note: str | None = None


class EventContactPatch(_Body):
    name: str | None = None
    phone: str | None = None
    company: str | None = None
    note: str | None = None


class ChildrenIn(_Body):
    version: int | None = None
    rows: list[dict[str, Any]]
    note: str | None = None


class NoteIn(_Body):
    note: str | None = None


class CancelIn(_Body):
    reason: str
    version: int | None = None


_CHILD_TIMES = ("at_time", "start_time", "end_time")


def _child_rows(rows: list[dict]) -> list[dict]:
    """Run-sheet rows from JSON: clock strings to times (the services take
    real ``time`` objects)."""
    out = []
    for raw in rows:
        row = dict(raw)
        for key in _CHILD_TIMES:
            value = row.get(key)
            if isinstance(value, str):
                try:
                    row[key] = dt.time.fromisoformat(value) if value else None
                except ValueError as exc:
                    raise HTTPException(
                        422,
                        {
                            "code": "invalid",
                            "message": f"'{value}' is not a time (HH:MM).",
                        },
                    ) from exc
        out.append(row)
    return out


def _sections_json(db: Session, ctx: FunctionsCtx, event_id: str) -> dict:
    latest: dict[str, Any] = {}
    for sc in events_svc.section_confirmations(db, ctx, event_id):
        latest[sc.section] = sc
    names = reports.user_names(db, [sc.actor_user_id for sc in latest.values()])
    return {
        section: (
            {
                "kind": sc.kind,
                "done": sc.kind in ("confirmed", "skipped"),
                "actor_kind": sc.actor_kind,
                "actor": reports._actor(
                    names, sc.actor_user_id, sc.client_email or sc.actor_label
                ),
                "note": sc.note,
                "occurred_at": _ts(sc.occurred_at),
            }
            if (sc := latest.get(section))
            else {"kind": None, "done": False}
        )
        for section in EVENT_SECTIONS
    }


def _event_detail(db: Session, ctx: FunctionsCtx, event_id: str) -> dict:
    from app.services.functions import public_tokens

    event = events_svc.get_event(db, ctx, event_id)
    state = events_svc.event_state(db, ctx, event.id)
    contacts = {
        c.id: c
        for c in db.query(FnContact).filter(
            FnContact.organization_id == ctx.org_id,
            FnContact.id.in_({event.contact_id, event.invoice_contact_id} - {None}),
        )
    }
    emails: dict[str, list] = {}
    for e in (
        db.query(contacts_svc.FnContactEmail)
        .filter(
            contacts_svc.FnContactEmail.organization_id == ctx.org_id,
            contacts_svc.FnContactEmail.contact_id.in_(list(contacts) or [""]),
        )
        .order_by(
            contacts_svc.FnContactEmail.is_primary.desc(),
            contacts_svc.FnContactEmail.created_at,
        )
    ):
        emails.setdefault(e.contact_id, []).append(e)
    venue = db.query(Venue).filter(Venue.id == event.venue_id).first()
    area = (
        db.query(FnVenueArea)
        .filter(
            FnVenueArea.organization_id == ctx.org_id, FnVenueArea.id == event.area_id
        )
        .first()
        if event.area_id
        else None
    )
    menus = menus_svc.list_menus(db, ctx, event_id=event.id)
    options = min_spends_svc.list_min_spends(db, ctx, event_id=event.id)
    changes = events_svc.event_changes(db, ctx, event.id)
    names = reports.user_names(db, [c.actor_user_id for c in changes])
    children = events_svc.event_children(db, ctx, event.id)
    settings = venue_profile.get_settings(db, ctx, event.venue_id)
    enquiry = None
    if event.enquiry_id:
        found = enquiries_svc.get_enquiry_or_none(db, ctx, event.enquiry_id)
        if found is not None:
            stage, _ = enquiries_svc.enquiry_state(db, ctx, found.id)
            enquiry = {"id": found.id, "subject": found.subject, "stage": stage}
    links = enquiries_svc.thread_links(db, ctx, event_id=event.id)
    return {
        "event": _event_json(event),
        "status": state.as_dict(),
        "money": money_svc.event_expected_spend(db, event),
        "contact": _contact_json(
            contacts[event.contact_id], emails.get(event.contact_id, [])
        )
        if event.contact_id in contacts
        else None,
        "invoice_contact": _contact_json(
            contacts[event.invoice_contact_id], emails.get(event.invoice_contact_id, [])
        )
        if event.invoice_contact_id in contacts
        else None,
        "venue": {
            "id": venue.id,
            "name": venue.name,
            "timezone": venue.timezone,
        }
        if venue
        else None,
        "area": _area_json(area) if area else None,
        "enquiry": enquiry,
        "children": {
            "timings": [
                {
                    "id": t.id,
                    "at_time": _t(t.at_time),
                    "ends_next_day": bool(t.ends_next_day),
                    "label": t.label,
                    "activity": t.activity,
                    "is_heading": bool(t.is_heading),
                }
                for t in children["timings"]
            ],
            "security": [
                {
                    "id": s.id,
                    "name": s.name,
                    "start_time": _t(s.start_time),
                    "end_time": _t(s.end_time),
                    "ends_next_day": bool(s.ends_next_day),
                    "notes": s.notes,
                }
                for s in children["security"]
            ],
            "entertainment": [
                {
                    "id": x.id,
                    "kind": x.kind,
                    "name": x.name,
                    "link": x.link,
                    "start_time": _t(x.start_time),
                    "end_time": _t(x.end_time),
                    "ends_next_day": bool(x.ends_next_day),
                    "notes": x.notes,
                }
                for x in children["entertainment"]
            ],
            "av": [
                {"id": a.id, "kind": a.kind, "details": a.details}
                for a in children["av"]
            ],
        },
        "menus": [_menu_json(m, items) for m, items in menus],
        "options": _options_json(db, ctx, options, _food_by_venue(menus)),
        "sections": _sections_json(db, ctx, event.id),
        "changes": [_change_json(c, names) for c in changes],
        "enabled_fields": venue_profile.enabled_fields_for(
            settings, event.booking_type
        ),
        "threads": _threads_json(db, ctx, links),
        "portal_links": public_tokens.links(db, ctx, event_id=event.id),
    }


@router.get("/events")
def list_events(
    date_from: dt.date | None = Query(None, alias="from"),
    date_to: dt.date | None = Query(None, alias="to"),
    venue: list[str] | None = Query(None),
    status: list[str] | None = Query(None),
    include_cancelled: bool = False,
    booking_type: list[str] | None = Query(None),
    enquiry_id: str | None = None,
    contact_id: str | None = None,
    q: str | None = None,
    sort: str = "date_asc",
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Events with derived status and expected spend. ``to`` is inclusive;
    cancelled events only with ``include_cancelled`` or
    ``status=cancelled``."""
    with _reading():
        rows = events_svc.list_events(
            db,
            ctx,
            venue_ids=_multi(venue),
            date_from=date_from,
            date_to=date_to,
            statuses=_multi(status),
            include_cancelled=include_cancelled,
            booking_types=_multi(booking_type),
            enquiry_id=enquiry_id,
            contact_id=contact_id,
            q=q,
            sort=sort,
            limit=limit + 1,
            offset=offset,
        )
        rows, more = _page(rows, limit)
        return {
            "rows": _event_rows_json(db, ctx, rows),
            "limit": limit,
            "offset": offset,
            "has_more": more,
        }


@router.get("/events/calendar")
def events_calendar(
    month: str,
    venue: list[str] | None = Query(None),
    status: list[str] | None = Query(None),
    include_cancelled: bool = False,
    q: str | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """A Monday-first month grid (``month=2026-12``): each day's events and
    their summed expected spend."""
    with _reading():
        out = reports.calendar_month(
            db,
            ctx,
            month=month,
            venue_ids=_multi(venue),
            statuses=_multi(status),
            include_cancelled=include_cancelled,
            q=q,
        )
        rows = iter(
            _event_rows_json(db, ctx, [r for day in out["days"] for r in day["events"]])
        )
        for day in out["days"]:
            day["events"] = [next(rows) for _ in day["events"]]
        return out


@router.post("/events", status_code=201)
def create_event(
    body: EventIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """A booking entered directly — only for booking types without a minimum
    spend (large events come from a customer confirming an option). Give
    ``contact_id`` or ``contact`` (matched by email, else created)."""
    fields = _given(body, "contact", "note")
    # ``contact_id: null`` alongside ``contact`` is fine; other nulls aren't.
    _no_nulls({k: v for k, v in fields.items() if k != "contact_id"}, FnEvent)
    core = {
        k: fields.pop(k)
        for k in (
            "venue_id",
            "contact_id",
            "event_date",
            "booking_type",
            "name",
            "area_id",
            "start_time",
            "end_time",
            "ends_next_day",
            "guests",
        )
        if k in fields
    }
    with _writing(db):
        contact = None
        if not core.get("contact_id"):
            if body.contact is None:
                raise Invalid("Choose a contact for this booking.")
            # The service matches it to the org's contact by email — a repeat
            # customer known at another venue is attached, never refused, so
            # the answer can't tell whether another venue knows them.
            core.pop("contact_id", None)
            contact = body.contact.model_dump()
        if "ends_next_day" in core and core["ends_next_day"] is None:
            core.pop("ends_next_day")
        event = events_svc.create_event(
            db, ctx, note=body.note, contact=contact, **core, **fields
        )
    with _reading():
        return _event_detail(db, ctx, event.id)


@router.get("/events/{event_id}")
def get_event(
    event_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """The event page in one call: fields, derived status and money,
    contacts, venue and area, run sheet / security / entertainment / A-V,
    menus with totals, the booked option, section states, the changes log,
    the fields enabled for its venue and booking type, linked threads and
    portal links."""
    with _reading():
        return _event_detail(db, ctx, event_id)


@router.patch("/events/{event_id}")
def update_event(
    event_id: str,
    body: EventPatch,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Edit fields; ``note`` is recorded with the change (and expected when
    the event is confirmed — the change is flagged after-confirmation)."""
    fields = _no_nulls(_given(body, "version", "note"), FnEvent)
    with _writing(db):
        rows = events_svc.update_event(
            db, ctx, event_id, expected_version=body.version, note=body.note, **fields
        )
    with _reading():
        out = _event_detail(db, ctx, event_id)
        out["logged"] = [c.id for c in rows]
        return out


@router.patch("/events/{event_id}/contact")
def update_event_contact(
    event_id: str,
    body: EventContactPatch,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    fields = _no_nulls(_given(body, "note"), FnContact)
    with _writing(db):
        events_svc.update_event_contact(db, ctx, event_id, note=body.note, **fields)
    with _reading():
        return _event_detail(db, ctx, event_id)


@router.put("/events/{event_id}/children/{kind}")
def replace_event_children(
    event_id: str,
    kind: Literal["timings", "security", "entertainment", "av"],
    body: ChildrenIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Replace the run sheet / security / entertainment / A-V list whole."""
    rows = _child_rows(body.rows)
    with _writing(db):
        events_svc.replace_event_children(
            db, ctx, event_id, kind, rows, expected_version=body.version, note=body.note
        )
    with _reading():
        return _event_detail(db, ctx, event_id)


@router.post("/events/{event_id}/sections/{section}/{action}")
def event_section(
    event_id: str,
    section: Literal["details", "beverages", "food"],
    action: Literal["confirm", "skip", "reopen"],
    body: NoteIn | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Record a section as confirmed (on the client's behalf), skip it (a
    note is required) or reopen it for the client."""
    note = body.note if body else None
    with _writing(db):
        if action == "confirm":
            events_svc.confirm_section(db, ctx, event_id, section, note=note)
        elif action == "skip":
            events_svc.skip_section(db, ctx, event_id, section, note=note or "")
        else:
            events_svc.reopen_section(db, ctx, event_id, section, note=note)
    with _reading():
        return _event_detail(db, ctx, event_id)


@router.post("/events/{event_id}/cancel")
def cancel_event(
    event_id: str,
    body: CancelIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        events_svc.cancel_event(
            db, ctx, event_id, reason=body.reason, expected_version=body.version
        )
    with _reading():
        return _event_detail(db, ctx, event_id)


@router.post("/events/{event_id}/reinstate")
def reinstate_event(
    event_id: str,
    body: MaybeReasonIn | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        events_svc.reinstate_event(
            db, ctx, event_id, reason=body.reason if body else None
        )
    with _reading():
        return _event_detail(db, ctx, event_id)


@router.get("/events/{event_id}/timeline")
def event_timeline(
    event_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _reading():
        return {"items": reports.event_timeline(db, ctx, event_id)}


class PortalLinkIn(_Body):
    emails: list[str] = []


@router.post("/events/{event_id}/portal-link")
def event_portal_link(
    event_id: str,
    body: PortalLinkIn | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """The client-portal link for staff to pass on themselves (copied into
    their own email while the venue mailbox isn't connected): allows
    ``emails`` in — the contact's primary email when none are given — and
    records the link as sent, as Orbit did when staff copied it. Nothing is
    emailed. ``{url, emails, link_sent_at}``."""
    from app.services.functions import public_tokens

    body = body or PortalLinkIn()
    with _writing(db):
        event = events_svc.get_event(db, ctx, event_id)
        emails = list(
            dict.fromkeys(contacts_svc.normalise_email(e) for e in body.emails)
        )
        if not emails:
            primary = (
                db.query(contacts_svc.FnContactEmail.email)
                .filter(
                    contacts_svc.FnContactEmail.organization_id == ctx.org_id,
                    contacts_svc.FnContactEmail.contact_id == event.contact_id,
                )
                .order_by(
                    contacts_svc.FnContactEmail.is_primary.desc(),
                    contacts_svc.FnContactEmail.created_at,
                )
                .first()
            )
            if primary is None:
                raise Invalid("Give the email the client will sign in with.")
            emails = [primary[0]]
        url = public_tokens.mint_portal_link(db, ctx, event_id, emails)
        change = events_svc.record_link_sent(
            db, ctx, event_id, to_emails=emails, note="Portal link copied by staff"
        )
    return {"url": url, "emails": emails, "link_sent_at": _ts(change.occurred_at)}


@router.get("/events/{event_id}/print/{kind}")
def event_print(
    event_id: str,
    kind: Literal["full", "run_sheet", "food"],
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """A print sheet as data: ``full`` (event sheet), ``run_sheet`` (no
    food), ``food`` (kitchen pre-order with dish counts)."""
    with _reading():
        return reports.print_sheet(db, ctx, event_id, kind)


@router.post("/events/{event_id}/threads", status_code=201)
def link_event_thread(
    event_id: str,
    body: ThreadLinkIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        link = enquiries_svc.link_thread(
            db,
            ctx,
            account_id=body.account_id,
            gmail_thread_id=body.gmail_thread_id,
            event_id=event_id,
        )
    with _reading():
        return {"link_id": link.id, **_event_detail(db, ctx, event_id)}


# ===========================================================================
# Pre-order menus
# ===========================================================================


class MenuIn(_Body):
    template_id: str | None = None
    venue_id: str | None = None
    name: str | None = None
    is_set_menu: bool = False
    price_per_person_cents: int | None = None
    guest_count: int = 0
    notes: str | None = None
    items: list[dict[str, Any]] | None = None
    note: str | None = None


class MenuPatch(_Body):
    version: int | None = None
    note: str | None = None
    name: str | None = None
    is_set_menu: bool | None = None
    price_per_person_cents: int | None = None
    guest_count: int | None = None
    notes: str | None = None
    sort_order: int | None = None


class MenuItemsIn(_Body):
    version: int | None = None
    items: list[dict[str, Any]]
    note: str | None = None


class QuantitiesIn(_Body):
    version: int | None = None
    quantities: dict[str, int]
    note: str | None = None


class DistributeIn(_Body):
    version: int | None = None
    guest_count: int | None = None
    note: str | None = None


def _add_menu(db: Session, ctx: FunctionsCtx, body: MenuIn, **parent) -> str:
    if body.template_id:
        menu = menus_svc.add_menu_from_template(
            db,
            ctx,
            template_id=body.template_id,
            guest_count=body.guest_count,
            note=body.note,
            **parent,
        )
    else:
        venue_id = body.venue_id
        if venue_id is None and parent.get("event_id"):
            venue_id = events_svc.get_event(db, ctx, parent["event_id"]).venue_id
        if venue_id is None and parent.get("enquiry_id"):
            venue_id = enquiries_svc.get_enquiry(db, ctx, parent["enquiry_id"]).venue_id
        if venue_id is None:
            raise Invalid("Choose the venue this menu is for.")
        menu = menus_svc.create_menu(
            db,
            ctx,
            venue_id=venue_id,
            name=body.name or "",
            is_set_menu=body.is_set_menu,
            price_per_person_cents=body.price_per_person_cents,
            guest_count=body.guest_count,
            notes=body.notes,
            items=body.items,
            note=body.note,
            **parent,
        )
    return menu.id


def _menu_out(db: Session, ctx: FunctionsCtx, menu_id: str) -> dict:
    menu, items = menus_svc.get_menu(db, ctx, menu_id)
    return _menu_json(menu, items)


@router.post("/enquiries/{enquiry_id}/menus", status_code=201)
def add_enquiry_menu(
    enquiry_id: str,
    body: MenuIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Add a pre-order menu: from a template (``template_id``, quantities
    zero) or built by hand (``venue_id``, ``name``, ``items``)."""
    with _writing(db):
        menu_id = _add_menu(db, ctx, body, enquiry_id=enquiry_id)
    with _reading():
        return _menu_out(db, ctx, menu_id)


@router.post("/events/{event_id}/menus", status_code=201)
def add_event_menu(
    event_id: str,
    body: MenuIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        menu_id = _add_menu(db, ctx, body, event_id=event_id)
    with _reading():
        return _menu_out(db, ctx, menu_id)


@router.patch("/menus/{menu_id}")
def update_menu(
    menu_id: str,
    body: MenuPatch,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    fields = _no_nulls(_given(body, "version", "note"), FnPreorderMenu)
    with _writing(db):
        menus_svc.update_menu(
            db, ctx, menu_id, expected_version=body.version, note=body.note, **fields
        )
    with _reading():
        return _menu_out(db, ctx, menu_id)


@router.put("/menus/{menu_id}/items")
def replace_menu_items(
    menu_id: str,
    body: MenuItemsIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Replace every line (add / remove dishes, change prices)."""
    with _writing(db):
        menus_svc.replace_menu_items(
            db, ctx, menu_id, body.items, expected_version=body.version, note=body.note
        )
    with _reading():
        return _menu_out(db, ctx, menu_id)


@router.put("/menus/{menu_id}/quantities")
def set_menu_quantities(
    menu_id: str,
    body: QuantitiesIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        menus_svc.set_item_quantities(
            db,
            ctx,
            menu_id,
            body.quantities,
            expected_version=body.version,
            note=body.note,
        )
    with _reading():
        return _menu_out(db, ctx, menu_id)


@router.post("/menus/{menu_id}/distribute")
def distribute_menu(
    menu_id: str,
    body: DistributeIn | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Fill a set menu's quantities from the dishes' default % (largest
    remainder per section), setting the guest count first when given."""
    body = body or DistributeIn()
    with _writing(db):
        menus_svc.distribute_set_menu(
            db,
            ctx,
            menu_id,
            guest_count=body.guest_count,
            expected_version=body.version,
            note=body.note,
        )
    with _reading():
        return _menu_out(db, ctx, menu_id)


@router.delete("/menus/{menu_id}", status_code=204)
def delete_menu(
    menu_id: str,
    note: str | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        menus_svc.delete_menu(db, ctx, menu_id, note=note)
    return Response(status_code=204)


# ===========================================================================
# Proposals
# ===========================================================================


class ProposalStartIn(_Body):
    venue_id: str | None = None
    restart: bool = False
    version: int | None = None


class ProposalPatch(_Body):
    version: int | None = None
    html: str | None = None
    css: str | None = None


class ProposalCheckIn(_Body):
    html: str | None = None


def _proposal_out(db: Session, ctx: FunctionsCtx, enquiry_id: str) -> dict:
    proposal = proposals_svc.get_proposal(db, ctx, enquiry_id)
    return {
        "proposal": _proposal_json(proposal),
        "summary": proposals_svc.summary(db, ctx, enquiry_id),
        "check": proposals_svc.check_consistency(db, ctx, enquiry_id)
        if proposal is not None
        else None,
        "options": proposals_svc.option_snapshot(db, ctx, enquiry_id),
    }


@router.get("/enquiries/{enquiry_id}/proposal")
def get_proposal(
    enquiry_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """The proposal (``null`` before one is started), its summary, the
    money check, and the options it would offer."""
    with _reading():
        return _proposal_out(db, ctx, enquiry_id)


@router.post("/enquiries/{enquiry_id}/proposal")
def start_proposal(
    enquiry_id: str,
    body: ProposalStartIn | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Start the proposal from the venue's brand (an existing one is
    returned as it is), or ``restart`` it from the brand (send ``version``)."""
    body = body or ProposalStartIn()
    with _writing(db):
        proposals_svc.create_proposal(
            db,
            ctx,
            enquiry_id,
            venue_id=body.venue_id,
            restart=body.restart,
            expected_version=body.version,
        )
    with _reading():
        return _proposal_out(db, ctx, enquiry_id)


@router.put("/enquiries/{enquiry_id}/proposal")
def save_proposal(
    enquiry_id: str,
    body: ProposalPatch,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Save HTML / CSS. Stored sanitised — the response is what customers
    will see, with the money check."""
    with _writing(db):
        proposals_svc.update_proposal(
            db,
            ctx,
            enquiry_id,
            html=body.html,
            css=body.css,
            expected_version=body.version,
        )
    with _reading():
        return _proposal_out(db, ctx, enquiry_id)


@router.post("/enquiries/{enquiry_id}/proposal/check")
def check_proposal(
    enquiry_id: str,
    body: ProposalCheckIn | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """The money check on unsaved ``html`` (or the saved proposal)."""
    with _reading():
        return proposals_svc.check_consistency(
            db, ctx, enquiry_id, html=body.html if body else None
        )


@router.get("/enquiries/{enquiry_id}/proposal/preview")
def preview_proposal(
    enquiry_id: str,
    version_id: str | None = None,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """``{document}`` — a complete, CSP-carrying HTML document for an
    ``<iframe sandbox srcdoc>`` (no ``allow-scripts``)."""
    with _reading():
        return {
            "document": proposals_svc.preview_document(
                db, ctx, enquiry_id, version_id=version_id
            )
        }


@router.get("/enquiries/{enquiry_id}/proposal/versions")
def proposal_versions(
    enquiry_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _reading():
        return {
            "rows": [
                _version_json(v) for v in proposals_svc.versions(db, ctx, enquiry_id)
            ]
        }


@router.get("/proposal-versions/{version_id}")
def proposal_version(
    version_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _reading():
        return _version_json(proposals_svc.get_version(db, ctx, version_id), full=True)


# ===========================================================================
# Templates
# ===========================================================================


class TemplateIn(_Body):
    venue_id: str
    name: str
    description: str | None = None
    is_set_menu: bool = False
    price_per_person_cents: int | None = None
    sort_order: int = 0
    items: list[dict[str, Any]] = []


class TemplatePatch(_Body):
    version: int | None = None
    name: str | None = None
    description: str | None = None
    is_set_menu: bool | None = None
    price_per_person_cents: int | None = None
    active: bool | None = None
    sort_order: int | None = None
    items: list[dict[str, Any]] | None = None


class CloneLoadedIn(_Body):
    venue_id: str
    loaded_menu_id: str
    name: str | None = None
    is_set_menu: bool = False
    price_per_person_cents: int | None = None


class ReplyTemplateIn(_Body):
    venue_id: str | None = None
    booking_type: str | None = None
    stage: str
    name: str
    subject: str | None = None
    body_html: str = ""
    active: bool = True
    sort_order: int = 0


class ReplyTemplatePatch(_Body):
    version: int | None = None
    venue_id: str | None = None
    booking_type: str | None = None
    stage: str | None = None
    name: str | None = None
    subject: str | None = None
    body_html: str | None = None
    active: bool | None = None
    sort_order: int | None = None


def _template_out(db: Session, ctx: FunctionsCtx, template_id: str) -> dict:
    template, items = templates_svc.get_template(db, ctx, template_id)
    return _template_json(template, items)


@router.get("/preorder-templates")
def list_preorder_templates(
    venue_id: str | None = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _reading():
        rows = templates_svc.list_templates(
            db, ctx, venue_id=venue_id, include_inactive=include_inactive
        )
        items = templates_svc.template_items(db, ctx, [t.id for t in rows])
        return {"rows": [_template_json(t, items[t.id]) for t in rows]}


@router.post("/preorder-templates", status_code=201)
def create_preorder_template(
    body: TemplateIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        template = templates_svc.create_template(db, ctx, **body.model_dump())
    with _reading():
        return _template_out(db, ctx, template.id)


@router.post("/preorder-templates/clone-loaded", status_code=201)
def clone_loaded_template(
    body: CloneLoadedIn,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """A new template from one of the venue's Loaded menus (sections, dishes,
    prices, recipe / stock links), read through Norm's ``get_menus``."""
    with _writing(db):
        template = templates_svc.clone_loaded_menu(
            db, config_db, ctx, **body.model_dump()
        )
    with _reading():
        return _template_out(db, ctx, template.id)


@router.get("/preorder-templates/{template_id}")
def get_preorder_template(
    template_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _reading():
        return _template_out(db, ctx, template_id)


@router.patch("/preorder-templates/{template_id}")
def update_preorder_template(
    template_id: str,
    body: TemplatePatch,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Edit a template; ``items`` (when sent) replaces its dishes."""
    fields = _no_nulls(_given(body, "version"), FnPreorderTemplate)
    with _writing(db):
        templates_svc.update_template(
            db, ctx, template_id, expected_version=body.version, **fields
        )
    with _reading():
        return _template_out(db, ctx, template_id)


@router.delete("/preorder-templates/{template_id}")
def delete_preorder_template(
    template_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Deleted when unused, retired (``active: false``) when menus were
    built from it. Returns ``{result: "deleted"|"retired"}``."""
    with _writing(db):
        result = templates_svc.delete_template(db, ctx, template_id)
    return {"result": result}


@router.get("/venues/{venue_id}/loaded-menus")
def loaded_menus(
    venue_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """The venue's Loaded menus to clone — ``{available, menus, reason}``."""
    with _reading():
        menus, reason = templates_svc.loaded_menus(db, config_db, ctx, venue_id)
        return {"available": menus is not None, "menus": menus or [], "reason": reason}


@router.get("/venues/{venue_id}/loaded-recipes")
def loaded_recipes(
    venue_id: str,
    q: str = "",
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Loaded recipes matching ``q`` (2+ characters), to link a dish."""
    with _reading():
        rows, reason = templates_svc.loaded_recipes(db, config_db, ctx, venue_id, q)
        return {"available": rows is not None, "recipes": rows or [], "reason": reason}


@router.get("/reply-templates")
def list_reply_templates(
    venue_id: str | None = None,
    booking_type: str | None = None,
    stage: str | None = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _reading():
        return {
            "rows": [
                _reply_json(r)
                for r in templates_svc.list_reply_templates(
                    db,
                    ctx,
                    venue_id=venue_id,
                    booking_type=booking_type,
                    stage=stage,
                    include_inactive=include_inactive,
                )
            ]
        }


@router.post("/reply-templates", status_code=201)
def create_reply_template(
    body: ReplyTemplateIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        row = templates_svc.create_reply_template(db, ctx, **body.model_dump())
    with _reading():
        return _reply_json(templates_svc.get_reply_template(db, ctx, row.id))


@router.patch("/reply-templates/{template_id}")
def update_reply_template(
    template_id: str,
    body: ReplyTemplatePatch,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    fields = _no_nulls(_given(body, "version"), FnReplyTemplate)
    with _writing(db):
        templates_svc.update_reply_template(
            db, ctx, template_id, expected_version=body.version, **fields
        )
    with _reading():
        return _reply_json(templates_svc.get_reply_template(db, ctx, template_id))


@router.delete("/reply-templates/{template_id}", status_code=204)
def delete_reply_template(
    template_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        templates_svc.delete_reply_template(db, ctx, template_id)
    return Response(status_code=204)


# ===========================================================================
# Venue profile
# ===========================================================================


class AreaIn(_Body):
    name: str
    description: str | None = None
    capacity_seated: int | None = None
    capacity_standing: int | None = None
    booking_types: list[str] = []
    requires_approval: bool = True
    min_spend_notes: str | None = None
    active: bool = True
    sort_order: int = 0


class AreaPatch(_Body):
    version: int | None = None
    name: str | None = None
    description: str | None = None
    capacity_seated: int | None = None
    capacity_standing: int | None = None
    booking_types: list[str] | None = None
    requires_approval: bool | None = None
    min_spend_notes: str | None = None
    active: bool | None = None
    sort_order: int | None = None


class PhotoIn(_Body):
    file_id: str
    caption: str | None = None


class PhotoPatch(_Body):
    caption: str | None = None


class PhotoOrderIn(_Body):
    photo_ids: list[str]


class SettingsPatch(_Body):
    version: int | None = None
    approver_user_id: str | None = None
    override_approver_user_id: str | None = None
    policies: str | None = None
    menu_links: list[dict[str, Any]] | None = None
    enabled_fields: dict[str, dict[str, bool]] | None = None
    notify_emails: list[str] | None = None


class BrandPatch(_Body):
    version: int | None = None
    logo_file_id: str | None = None
    cover_file_id: str | None = None
    closing_file_id: str | None = None
    wordmark: str | None = None
    tagline: str | None = None
    colours: dict[str, str | None] | None = None
    fonts: dict[str, str | None] | None = None
    terms: list[Any] | None = None
    inclusions: list[str] | None = None
    signoff: dict[str, str | None] | None = None
    style: dict[str, Any] | None = None
    footer_contact: str | None = None
    valid_days: int | None = None
    template_html: str | None = None
    template_css: str | None = None


class ImageIn(_Body):
    file_id: str
    name: str | None = None
    caption: str | None = None
    tags: list[str] | None = None


class ImagePatch(_Body):
    version: int | None = None
    name: str | None = None
    caption: str | None = None
    tags: list[str] | None = None


def _profile(db: Session, ctx: FunctionsCtx, venue_id: str) -> dict:
    venue = venue_profile.get_venue(db, ctx, venue_id)
    settings = venue_profile.get_settings(db, ctx, venue_id)
    areas = venue_profile.list_areas(db, ctx, venue_id)
    photos = venue_profile.area_photos(db, ctx, [a.id for a in areas])
    names = reports.user_names(
        db,
        [settings.approver_user_id, settings.override_approver_user_id]
        if settings
        else [],
    )
    return {
        "venue": {
            "id": venue.id,
            "name": venue.name,
            "timezone": venue.timezone,
            "day_start_time": venue.day_start_time,
        },
        "settings": _settings_json(settings, names),
        "enabled_fields": {
            bt: venue_profile.enabled_fields_for(settings, bt) for bt in BOOKING_TYPES
        },
        "can_govern_approvals": venue_profile._may_govern_approvals(db, ctx, settings),
        "areas": [_area_json(a, photos[a.id]) for a in areas],
        "brand": _brand_json(venue_profile.get_brand(db, ctx, venue_id)),
        "images": [
            _image_json(i) for i in venue_profile.list_images(db, ctx, venue_id)
        ],
    }


@router.get("/venues/{venue_id}/profile")
def venue_profile_get(
    venue_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """The venue's Functions settings page in one call: settings (approvers,
    policies, menu links, enabled fields, notify emails), areas with photos,
    brand and image library."""
    with _reading():
        return _profile(db, ctx, venue_id)


@router.put("/venues/{venue_id}/settings")
def update_venue_settings(
    venue_id: str,
    body: SettingsPatch,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Create or edit the venue's settings. Changing the approver or the
    override approver needs an owner (or a current approver)."""
    fields = _given(body, "version")
    with _writing(db):
        venue_profile.update_settings(
            db, ctx, venue_id, expected_version=body.version, **fields
        )
    with _reading():
        return _profile(db, ctx, venue_id)


@router.post("/venues/{venue_id}/areas", status_code=201)
def create_area(
    venue_id: str,
    body: AreaIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        area = venue_profile.create_area(db, ctx, venue_id, **body.model_dump())
    with _reading():
        area = venue_profile.get_area(db, ctx, area.id)
        return _area_json(area, venue_profile.area_photos(db, ctx, [area.id])[area.id])


@router.patch("/areas/{area_id}")
def update_area(
    area_id: str,
    body: AreaPatch,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    fields = _no_nulls(_given(body, "version"), FnVenueArea)
    with _writing(db):
        venue_profile.update_area(
            db, ctx, area_id, expected_version=body.version, **fields
        )
    with _reading():
        area = venue_profile.get_area(db, ctx, area_id)
        return _area_json(area, venue_profile.area_photos(db, ctx, [area.id])[area.id])


@router.delete("/areas/{area_id}")
def delete_area(
    area_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Deleted when unused, retired when options or events use it."""
    with _writing(db):
        result = venue_profile.delete_area(db, ctx, area_id)
    return {"result": result}


@router.post("/areas/{area_id}/photos", status_code=201)
def add_area_photo(
    area_id: str,
    body: PhotoIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        photo = venue_profile.add_area_photo(
            db, ctx, area_id, file_id=body.file_id, caption=body.caption
        )
    return _photo_json(photo)


@router.put("/areas/{area_id}/photos/order")
def reorder_area_photos(
    area_id: str,
    body: PhotoOrderIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        photos = venue_profile.reorder_area_photos(db, ctx, area_id, body.photo_ids)
    return {"rows": [_photo_json(p) for p in photos]}


@router.patch("/area-photos/{photo_id}")
def update_area_photo(
    photo_id: str,
    body: PhotoPatch,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        photo = venue_profile.update_area_photo(db, ctx, photo_id, caption=body.caption)
    return _photo_json(photo)


@router.delete("/area-photos/{photo_id}", status_code=204)
def delete_area_photo(
    photo_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        venue_profile.remove_area_photo(db, ctx, photo_id)
    return Response(status_code=204)


@router.put("/venues/{venue_id}/brand")
def update_brand(
    venue_id: str,
    body: BrandPatch,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Create or edit the venue's proposal brand (template HTML/CSS are
    sanitised like a proposal)."""
    fields = _given(body, "version")
    with _writing(db):
        venue_profile.update_brand(
            db, ctx, venue_id, expected_version=body.version, **fields
        )
    with _reading():
        return _brand_json(venue_profile.get_brand(db, ctx, venue_id))


@router.delete("/venues/{venue_id}/brand", status_code=204)
def reset_brand(
    venue_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Reset the brand to Norm's defaults (started proposals keep their HTML)."""
    with _writing(db):
        venue_profile.delete_brand(db, ctx, venue_id)
    return Response(status_code=204)


@router.get("/venues/{venue_id}/images")
def list_images(
    venue_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _reading():
        return {
            "rows": [
                _image_json(i) for i in venue_profile.list_images(db, ctx, venue_id)
            ]
        }


@router.post("/venues/{venue_id}/images", status_code=201)
def add_image(
    venue_id: str,
    body: ImageIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Add an already-uploaded file to the venue's library."""
    with _writing(db):
        image = venue_profile.add_image(db, ctx, venue_id, **body.model_dump())
    with _reading():
        return _image_json(venue_profile.get_image(db, ctx, image.id))


@router.post("/venues/{venue_id}/images/upload", status_code=201)
async def upload_image(
    venue_id: str,
    file: UploadFile = File(...),
    name: str = Form(""),
    caption: str = Form(""),
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Upload an image and add it to the venue's library in one step."""
    data = await file.read(uploads_svc.MAX_BYTES + 1)
    with _writing(db):
        stored = uploads_svc.store_image(db, ctx, data, file.filename)
        image = venue_profile.add_image(
            db,
            ctx,
            venue_id,
            file_id=stored.id,
            name=name or None,
            caption=caption or None,
        )
    with _reading():
        return {
            **_image_json(venue_profile.get_image(db, ctx, image.id)),
            "file": uploads_svc.file_dict(stored),
        }


@router.patch("/images/{image_id}")
def update_image(
    image_id: str,
    body: ImagePatch,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    fields = _no_nulls(_given(body, "version"), FnProposalImage)
    with _writing(db):
        venue_profile.update_image(
            db, ctx, image_id, expected_version=body.version, **fields
        )
    with _reading():
        return _image_json(venue_profile.get_image(db, ctx, image_id))


@router.delete("/images/{image_id}", status_code=204)
def delete_image(
    image_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    with _writing(db):
        venue_profile.delete_image(db, ctx, image_id)
    return Response(status_code=204)


# ===========================================================================
# Uploads
# ===========================================================================


@router.post("/files", status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Upload an image (JPEG, PNG, GIF or WebP, up to 10 MB). The same bytes
    uploaded twice return the same file. Use the ``id`` for logos, area
    photos and the image library."""
    data = await file.read(uploads_svc.MAX_BYTES + 1)
    with _writing(db):
        stored = uploads_svc.store_image(db, ctx, data, file.filename)
    return uploads_svc.file_dict(stored)


@router.get("/files/{file_id}")
def get_file(
    file_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """The image's bytes, for staff pages (public pages use F5's
    ``/api/public/files/<id>``)."""
    with _reading():
        row = uploads_svc.get_file(db, ctx, file_id)
    return Response(
        content=row.data,
        media_type=row.content_type,
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
            "ETag": f'"{row.sha256}"',
        },
    )


# ===========================================================================
# Customer links and portal access — revoking (added by F5; the rules are in
# services/functions/public_tokens.py and portal.py). Writes need org mode
# live; every record is checked for org and venue visibility.
# ===========================================================================


class PortalAccessIn(_Body):
    email: str


@router.post("/enquiries/{enquiry_id}/quote-link/revoke")
def revoke_quote_link(
    enquiry_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """End the enquiry's quote-page link for good (a link sent to the wrong
    person, or a quote withdrawn). The next "copy link" or send mints a new
    one. ``{revoked, quote_links}``."""
    from app.services.functions import public_tokens

    with _writing(db):
        enquiries_svc.get_enquiry(db, ctx, enquiry_id)
        revoked = public_tokens.revoke(db, ctx, enquiry_id=enquiry_id)
    with _reading():
        return {
            "revoked": revoked,
            "quote_links": public_tokens.links(db, ctx, enquiry_id=enquiry_id),
        }


@router.post("/events/{event_id}/portal-link/revoke")
def revoke_portal_link(
    event_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """End the event's portal link for everyone and sign out everyone who came
    in through it. Allowed emails stay allowed for the next link.
    ``{revoked, portal_links}``."""
    from app.services.functions import public_tokens

    with _writing(db):
        events_svc.get_event(db, ctx, event_id)
        revoked = public_tokens.revoke(db, ctx, event_id=event_id)
    with _reading():
        return {
            "revoked": revoked,
            "portal_links": public_tokens.links(db, ctx, event_id=event_id),
        }


@router.get("/events/{event_id}/portal-access")
def event_portal_access(
    event_id: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Who may open the event's portal: ``{people: [{email, allowed,
    verified_at, invited_by_email, removed_at, locked_for_wrong_codes}]}``."""
    from app.services.functions import portal

    with _reading():
        return {"people": portal.access_list(db, ctx, event_id)}


@router.post("/events/{event_id}/portal-access")
def allow_portal_access(
    event_id: str,
    body: PortalAccessIn,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Allow an email in — or back in after it was removed or locked out by
    too many wrong codes. Nothing is emailed. ``{people}``."""
    from app.services.functions import portal

    with _writing(db):
        portal.allow_access(db, ctx, event_id, body.email)
    with _reading():
        return {"people": portal.access_list(db, ctx, event_id)}


@router.delete("/events/{event_id}/portal-access/{email}")
def remove_portal_access(
    event_id: str,
    email: str,
    db: Session = Depends(get_db),
    ctx: FunctionsCtx = Depends(staff_ctx),
):
    """Take one email out of the portal: no more codes for it, and its
    sessions end now. ``{people}``."""
    from app.services.functions import portal

    with _writing(db):
        portal.remove_access(db, ctx, event_id, email)
    with _reading():
        return {"people": portal.access_list(db, ctx, event_id)}
