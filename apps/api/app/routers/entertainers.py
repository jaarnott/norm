"""Entertainers — the staff API (``/api/entertainers/...``, plan §11, D15).

Every endpoint does the same things, written once:

1. **Who and where** — ``staff_ctx``: the signed-in person's organisation
   (membership, ``X-Organization`` honoured), 404 unless the module is on
   for it (``entertainers_mode`` shadow or live), then ``build_ctx`` — the
   venues they may use (fail closed) and the scopes they hold.
2. **The rule** — a service in ``app/services/entertainers``; the router
   shapes what comes back. Register, Roster and Emails are venue-access
   routes; Payments, accept, shares, the sensitive read and the money
   email bodies also carry ``require_permission("payroll:write")`` — and
   the services check ``ctx.require`` again, so a tool or a job can't skip it.
3. **Errors** — ``{"detail": {"code", "message"}}`` with the service's
   status: 404 / 403 / 400 / 409 (stale, ruled out, or ``not_live``).
4. **One request, one transaction** — writes run in a savepoint and commit
   once; the emails a write queues go out after that commit.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_permission
from app.db.entertainers_models import LANES, PERFORMER_TYPES
from app.db.models import User, Venue
from app.services.entertainers import band as band_svc
from app.services.entertainers import bills as bills_svc
from app.services.entertainers import closing_manager as closing_svc
from app.services.entertainers import email_links, emails
from app.services.entertainers import invoice_check as invoice_svc
from app.services.entertainers import payments as payments_svc
from app.services.entertainers import performances as performances_svc
from app.services.entertainers import performers as performers_svc
from app.services.entertainers import series as series_svc
from app.services.entertainers._shared import check_version, settings_for
from app.services.entertainers.performers import performer_dict
from app.services.finance_core import modes as modes_svc
from app.services.finance_core.ctx import FinanceCtx, build_ctx
from app.services.finance_core.errors import FinanceError, http_exception
from app.db.engine import get_config_db, get_db
from app.services.entitlements import org_id_for_user

router = APIRouter(prefix="/entertainers", tags=["entertainers"])


# ===========================================================================
# Plumbing
# ===========================================================================


def staff_ctx(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> FinanceCtx:
    """The caller's ctx — or 404 when their organisation has the module off."""
    org_id = org_id_for_user(user.id, db)
    if not org_id or modes_svc.mode(db, org_id, "entertainers") == "off":
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_found",
                "message": "Entertainers is not switched on for your organisation.",
            },
        )
    try:
        return build_ctx(db, user, org_id=org_id)
    except FinanceError as exc:
        raise http_exception(exc) from exc


def _fail(err: FinanceError) -> HTTPException:
    exc = http_exception(err)
    errors = getattr(err, "errors", None)
    if errors:
        exc.detail = {**exc.detail, "errors": errors}
    return exc


@contextmanager
def _reading():
    try:
        yield
    except FinanceError as exc:
        raise _fail(exc) from exc


@contextmanager
def _writing(db: Session):
    """A savepoint around the write, one commit, then the queued emails."""
    with emails.collecting() as pending:
        try:
            with db.begin_nested():
                yield
        except FinanceError as exc:
            raise _fail(exc) from exc
        db.commit()
    emails.run_pending(db, pending)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _file_response(row, file_id: str) -> Response:
    """A stored file, shown inline under its checked type only (``nosniff``),
    its name cleaned for the header — an imported name came from a URL."""
    name = performers_svc.clean_filename(row.filename) or file_id
    return Response(
        content=bytes(row.data),
        media_type=row.content_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{name}"',
        },
    )


def _date(value: str | None, what: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            400, detail={"code": "invalid", "message": f"{what} must be YYYY-MM-DD."}
        ) from exc


# ===========================================================================
# Meta and settings
# ===========================================================================


@router.get("/meta")
def meta(ctx: FinanceCtx = Depends(staff_ctx), db: Session = Depends(get_db)):
    settings_row = settings_for(db, ctx.org_id)
    venues = (
        db.query(Venue)
        .filter(Venue.id.in_(sorted(ctx.venue_ids)))
        .order_by(Venue.name)
        .all()
        if ctx.venue_ids
        else []
    )
    with _reading():
        roster = (
            performances_svc.list_roster(
                db,
                ctx,
                since=dt.date.today() - dt.timedelta(days=60),
                until=dt.date.today() + dt.timedelta(days=1),
            )["performances"]
            if ctx.venue_ids
            else []
        )
        review = [p for p in performers_svc.list_performers(db, ctx, needs_review=True)]
        missing = performers_svc.missing_declarations(db, ctx)
    counts = {
        key: 0
        for key in (
            "awaiting_approvals",
            "awaiting_performer",
            "awaiting_manager",
            "mismatch",
            "ready_for_payment",
        )
    }
    for p in roster:
        if p["status"] in counts:
            counts[p["status"]] += 1
    db.commit()
    return {
        "mode": ctx.mode("entertainers"),
        "can_pay": ctx.holds("payroll:write"),
        "is_owner": ctx.is_owner,
        "venues": [
            {"id": v.id, "name": v.name, "timezone": v.timezone} for v in venues
        ],
        "lanes": list(LANES),
        "performer_types": list(PERFORMER_TYPES),
        "settings": _settings_dict(settings_row),
        "counts": {
            **counts,
            "needs_review": len(review),
            "missing_declarations": len(missing),
        },
    }


def _settings_dict(row) -> dict:
    return {
        "closing_manager_roles": row.closing_manager_roles,
        "mismatch_tolerance_minutes": row.mismatch_tolerance_minutes,
        "reminder_interval_days": row.reminder_interval_days,
        "reminder_max": row.reminder_max,
        "version": row.version,
    }


class SettingsBody(_Body):
    closing_manager_roles: list[str] | None = None
    mismatch_tolerance_minutes: int | None = Field(default=None, ge=0, le=120)
    reminder_interval_days: int | None = Field(default=None, ge=1, le=30)
    reminder_max: int | None = Field(default=None, ge=0, le=20)
    version: int


@router.put("/settings", dependencies=[Depends(require_permission("payroll:write"))])
def put_settings(
    body: SettingsBody,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        ctx.require("payroll:write")
        row = settings_for(db, ctx.org_id)
        check_version(row, body.version)
        if body.closing_manager_roles is not None:
            roles = [
                " ".join(r.split())
                for r in body.closing_manager_roles
                if r and r.strip()
            ]
            if not roles:
                raise HTTPException(
                    400,
                    detail={
                        "code": "invalid",
                        "message": "Name at least one closing-manager role.",
                    },
                )
            row.closing_manager_roles = roles
        for key in (
            "mismatch_tolerance_minutes",
            "reminder_interval_days",
            "reminder_max",
        ):
            value = getattr(body, key)
            if value is not None:
                setattr(row, key, value)
        db.flush()
    return _settings_dict(row)


# ===========================================================================
# Register
# ===========================================================================


@router.get("/performers")
def list_performers(
    q: str | None = None,
    include_inactive: bool = False,
    needs_review: bool | None = None,
    lane: str | None = None,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        rows = performers_svc.list_performers(
            db,
            ctx,
            q=q,
            include_inactive=include_inactive,
            needs_review=needs_review,
            lane=lane,
        )
    db.commit()
    return {"performers": rows}


@router.get("/performers/missing-declarations")
def missing_declarations(
    ctx: FinanceCtx = Depends(staff_ctx), db: Session = Depends(get_db)
):
    with _reading():
        return {"performers": performers_svc.missing_declarations(db, ctx)}


class PerformerCreate(_Body):
    name: str = Field(max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=2000)
    performer_type: Literal["solo", "band", "agent"] | None = None
    send_invite: bool = True


@router.post("/performers", status_code=201)
def create_performer(
    body: PerformerCreate,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        row, invite = performers_svc.create_performer(db, ctx, **body.model_dump())
    return {"performer": performer_dict(db, ctx, row), "invite": invite}


@router.get("/performers/{performer_id}")
def get_performer(
    performer_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        row = performers_svc.get_performer(db, ctx, performer_id)
        members = (
            band_svc.list_members(db, ctx, row.id)
            if row.band_mode == "members_split"
            else None
        )
        sent, _total = email_links.list_links(db, ctx, performer_id=row.id, limit=50)
        recent = (
            performances_svc.list_roster(
                db,
                ctx,
                performer_id=row.id,
                since=dt.date.today() - dt.timedelta(days=90),
            )["performances"][-10:]
            if ctx.venue_ids
            else []
        )
    documents = [
        {"kind": kind, "file_id": file_id}
        for kind, file_id in (
            ("exemption_certificate", row.exemption_file_id),
            ("company_document", row.company_document_file_id),
            ("ir330c", row.declaration_file_id),
        )
        if file_id and (kind != "ir330c" or ctx.holds("payroll:write"))
    ]
    db.commit()
    return {
        "performer": performer_dict(db, ctx, row),
        "band_members": members,
        "documents": documents,
        "emails": sent,
        "performances": recent,
    }


class PerformerUpdate(_Body):
    name: str | None = None
    legal_name: str | None = None
    trading_name: str | None = None
    email: str | None = None
    phone: str | None = None
    notes: str | None = None
    performer_type: Literal["solo", "band", "agent"] | None = None
    is_active: bool | None = None
    lane: (
        Literal["payroll_employee", "payroll_contractor", "invoice", "exemption"] | None
    ) = None
    band_mode: Literal["leader", "members_split"] | None = None
    payroll_code: str | None = None
    tax_code: str | None = None
    kiwisaver_rate: float | None = None
    gst_registered: bool | None = None
    gst_number: str | None = None
    company_name: str | None = None
    company_number: str | None = None
    exemption_certificate_number: str | None = None
    exemption_expiry_date: str | None = None
    needs_review: bool | None = None
    review_reason: str | None = None
    wt_rate_type: Literal["standard", "self_selected", "tailored"] | None = None
    wt_rate_bp: int | None = None
    version: int


@router.put("/performers/{performer_id}")
def update_performer(
    performer_id: str,
    body: PerformerUpdate,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    fields = body.model_dump(exclude_unset=True)
    version = fields.pop("version")
    with _writing(db):
        row = performers_svc.update_performer(
            db, ctx, performer_id, fields, version=version
        )
    return {"performer": performer_dict(db, ctx, row)}


@router.post("/performers/{performer_id}/invite")
def invite(
    performer_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        out = performers_svc.send_invite(db, ctx, performer_id)
    return out


@router.post("/performers/{performer_id}/request-details")
def request_details(
    performer_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        out = performers_svc.request_details(db, ctx, performer_id)
    return out


@router.post(
    "/performers/{performer_id}/declaration-link",
    dependencies=[Depends(require_permission("payroll:write"))],
)
def declaration_link(
    performer_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        out = performers_svc.send_declaration_link(db, ctx, performer_id)
    return out


@router.post(
    "/performers/{performer_id}/accept",
    dependencies=[Depends(require_permission("payroll:write"))],
)
def accept(
    performer_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        row = performers_svc.accept(db, ctx, performer_id)
    return {"performer": performer_dict(db, ctx, row)}


@router.post("/performers/{performer_id}/deactivate")
def deactivate(
    performer_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        row = performers_svc.deactivate(db, ctx, performer_id)
    return {"performer": performer_dict(db, ctx, row)}


@router.post("/performers/{performer_id}/reactivate")
def reactivate(
    performer_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        row = performers_svc.reactivate(db, ctx, performer_id)
    return {"performer": performer_dict(db, ctx, row)}


@router.get(
    "/performers/{performer_id}/sensitive",
    dependencies=[Depends(require_permission("payroll:write"))],
)
def sensitive(
    performer_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        return performers_svc.sensitive(db, ctx, performer_id)


@router.post("/performers/{performer_id}/documents", status_code=201)
async def upload_document(
    performer_id: str,
    kind: str = Form(...),
    file: UploadFile = File(...),
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    # One byte past the cap is enough to refuse; never the whole upload.
    data = await file.read(performers_svc.MAX_UPLOAD + 1)
    with _writing(db):
        row = performers_svc.attach_document(
            db,
            ctx,
            performer_id,
            kind=kind,
            data=data,
            filename=file.filename,
            content_type=file.content_type,
        )
    return {"file": performers_svc.file_dict(row)}


@router.get("/performers/{performer_id}/documents/{file_id}")
def get_document(
    performer_id: str,
    file_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        row = performers_svc.get_document(db, ctx, performer_id, file_id)
    return _file_response(row, file_id)


# --- band members ----------------------------------------------------------


@router.get("/performers/{performer_id}/members")
def list_members(
    performer_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        return {"members": band_svc.list_members(db, ctx, performer_id)}


class MemberIn(_Body):
    id: str | None = None
    name: str = Field(max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)


class MembersBody(_Body):
    members: list[MemberIn] = Field(max_length=20)


@router.put("/performers/{performer_id}/members")
def put_members(
    performer_id: str,
    body: MembersBody,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        out = band_svc.update_members(
            db, ctx, performer_id, [m.model_dump() for m in body.members]
        )
    return {"members": out}


class SharesBody(_Body):
    shares: dict[str, int]


@router.put(
    "/performers/{performer_id}/members/shares",
    dependencies=[Depends(require_permission("payroll:write"))],
)
def put_shares(
    performer_id: str,
    body: SharesBody,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        out = band_svc.set_shares(db, ctx, performer_id, body.shares)
    return {"members": out}


@router.post("/performers/{performer_id}/members/{member_id}/invite")
def invite_member(
    performer_id: str,
    member_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        out = band_svc.invite_member(db, ctx, performer_id, member_id)
    return out


# ===========================================================================
# Roster
# ===========================================================================


@router.get("/roster")
def roster(
    venue_id: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    performer_id: str | None = None,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    since = _date(from_, "from") or (dt.date.today() - dt.timedelta(days=30))
    until = _date(to, "to") or (dt.date.today() + dt.timedelta(days=60))
    with _reading():
        out = performances_svc.list_roster(
            db,
            ctx,
            venue_id=venue_id,
            since=since,
            until=until,
            performer_id=performer_id,
        )
    db.commit()
    return out


class RepeatIn(_Body):
    weekdays: list[int] | None = None
    ends_on: str | None = None


class PerformanceCreate(_Body):
    venue_id: str
    performer_id: str
    performance_date: str
    start_time: str
    end_time: str
    ends_next_day: bool = False
    rate_cents: int = Field(ge=0)
    notes: str | None = Field(default=None, max_length=2000)
    repeat: RepeatIn | None = None


@router.post("/performances", status_code=201)
def create_performance(
    body: PerformanceCreate,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        perf, series_rows = performances_svc.book(
            db,
            ctx,
            venue_id=body.venue_id,
            performer_id=body.performer_id,
            performance_date=body.performance_date,
            start_time=body.start_time,
            end_time=body.end_time,
            ends_next_day=body.ends_next_day,
            rate_cents=body.rate_cents,
            notes=body.notes,
            repeat=body.repeat.model_dump() if body.repeat else None,
        )
    return {
        "performance": performances_svc.performance_dict(db, ctx, perf),
        "series": [series_svc.series_dict(s) for s in series_rows] or None,
    }


@router.get("/performances/{performance_id}")
def get_performance(
    performance_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        out = performances_svc.get(db, ctx, performance_id)
    db.commit()
    return out


class PerformanceUpdate(_Body):
    performer_id: str | None = None
    performance_date: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    ends_next_day: bool | None = None
    rate_cents: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)
    version: int
    scope: Literal["this", "following", "all"] = "this"


@router.put("/performances/{performance_id}")
def update_performance(
    performance_id: str,
    body: PerformanceUpdate,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    fields = body.model_dump(exclude_unset=True)
    version, scope = fields.pop("version"), fields.pop("scope", "this")
    with _writing(db):
        changed = performances_svc.update(
            db, ctx, performance_id, fields, version=version, scope=scope
        )
    return {
        "performance": performances_svc.performance_dict(db, ctx, changed[0]),
        "changed": len(changed),
    }


@router.delete("/performances/{performance_id}", status_code=204)
def delete_performance(
    performance_id: str,
    version: int | None = None,
    scope: Literal["this", "following", "all"] = "this",
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        performances_svc.delete(db, ctx, performance_id, version=version, scope=scope)
    return Response(status_code=204)


class ApproveBody(_Body):
    actual_start_time: str
    actual_end_time: str
    actual_ends_next_day: bool = False
    note: str | None = Field(default=None, max_length=1000)
    version: int


@router.post("/performances/{performance_id}/approve")
def approve(
    performance_id: str,
    body: ApproveBody,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        perf = performances_svc.approve(db, ctx, performance_id, **body.model_dump())
    return {
        "performance": performances_svc.performance_dict(
            db, ctx, perf, with_emails=True
        )
    }


class DnpBody(_Body):
    reason: str = Field(max_length=2000)
    version: int


@router.post("/performances/{performance_id}/did-not-perform")
def did_not_perform(
    performance_id: str,
    body: DnpBody,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        perf = performances_svc.did_not_perform(
            db, ctx, performance_id, reason=body.reason, version=body.version
        )
    return {"performance": performances_svc.performance_dict(db, ctx, perf)}


class VersionBody(_Body):
    version: int


@router.post("/performances/{performance_id}/reset")
def reset(
    performance_id: str,
    body: VersionBody,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        perf = performances_svc.reset(db, ctx, performance_id, version=body.version)
    return {"performance": performances_svc.performance_dict(db, ctx, perf)}


@router.post("/performances/{performance_id}/use-performer-times")
def use_performer_times(
    performance_id: str,
    body: VersionBody,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        perf = performances_svc.use_performer_times(
            db, ctx, performance_id, version=body.version
        )
    return {
        "performance": performances_svc.performance_dict(
            db, ctx, perf, with_emails=True
        )
    }


@router.post("/performances/{performance_id}/resend-confirmation")
def resend_confirmation(
    performance_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        out = performances_svc.send_confirm_request(
            db, ctx, performance_id, resend=True
        )
    return out


@router.post("/performances/{performance_id}/portal-link")
def portal_link(
    performance_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        out = performances_svc.staff_portal_link(db, ctx, performance_id)
    return out


@router.post("/performances/{performance_id}/resolve-closing-manager")
def resolve_closing_manager(
    performance_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        perf = closing_svc.resolve_one(db, ctx, performance_id)
    return {"performance": performances_svc.performance_dict(db, ctx, perf)}


# ===========================================================================
# Payments, invoices, bills — payroll:write
# ===========================================================================

_pay = Depends(require_permission("payroll:write"))


@router.get("/payments", dependencies=[_pay])
def payments(
    venue_id: str | None = None,
    status: str = "pending",
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    lane: str | None = None,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        out = payments_svc.list_payments(
            db,
            ctx,
            venue_id=venue_id,
            status=status,
            since=_date(from_, "from"),
            until=_date(to, "to"),
            lane=lane,
        )
    db.commit()
    return out


class MarkPaidBody(_Body):
    reference: str | None = Field(default=None, max_length=200)
    version: int


@router.post("/performances/{performance_id}/mark-paid", dependencies=[_pay])
def mark_paid(
    performance_id: str,
    body: MarkPaidBody,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        perf = payments_svc.mark_paid(
            db, ctx, performance_id, reference=body.reference, version=body.version
        )
    return {"performance": performances_svc.performance_dict(db, ctx, perf)}


class MarkPaidWeekBody(_Body):
    week_start: str
    performance_ids: list[str] = Field(default_factory=list, max_length=500)
    share_ids: list[str] = Field(default_factory=list, max_length=500)
    venue_id: str | None = None


@router.post("/payments/mark-paid-week", dependencies=[_pay])
def mark_paid_week(
    body: MarkPaidWeekBody,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    """Mark performances (and band shares) paid in a payroll week — stamps
    ``payroll_week_start``. Without ``venue_id`` the performances are grouped
    by their own venue (each must be one of the caller's)."""
    from app.db.entertainers_models import EntPerformance

    week = _date(body.week_start, "week_start")
    if week is None or week.isoweekday() != 1:
        raise HTTPException(
            400,
            detail={
                "code": "invalid",
                "message": "week_start must be a Monday (YYYY-MM-DD).",
            },
        )
    totals = {"paid": 0, "shares": 0}
    with _writing(db):
        by_venue: dict[str, list[str]] = {}
        if body.venue_id:
            by_venue[body.venue_id] = list(body.performance_ids)
        else:
            rows = (
                db.query(EntPerformance.id, EntPerformance.venue_id)
                .filter(
                    EntPerformance.organization_id == ctx.org_id,
                    EntPerformance.id.in_(body.performance_ids),
                )
                .all()
                if body.performance_ids
                else []
            )
            for pid, vid in rows:
                by_venue.setdefault(vid, []).append(pid)
            if len(rows) != len(set(body.performance_ids)):
                raise HTTPException(
                    404,
                    detail={"code": "not_found", "message": "Performance not found."},
                )
        first = True
        for vid, ids in by_venue.items():
            out = payments_svc.mark_paid_for_week(
                db, ctx, vid, week, ids, share_ids=body.share_ids if first else ()
            )
            first = False
            totals["paid"] += out["paid"]
            totals["shares"] += out["shares"]
        if not by_venue and body.share_ids:
            raise HTTPException(
                400,
                detail={
                    "code": "invalid",
                    "message": "Name the venue for band shares.",
                },
            )
    return totals


@router.post("/performances/{performance_id}/undo-paid", dependencies=[_pay])
def undo_paid(
    performance_id: str,
    body: VersionBody,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        perf = payments_svc.undo_paid(db, ctx, performance_id, version=body.version)
    return {"performance": performances_svc.performance_dict(db, ctx, perf)}


class ShareRefBody(_Body):
    reference: str | None = Field(default=None, max_length=200)


@router.post(
    "/performances/{performance_id}/shares/{member_id}/mark-paid", dependencies=[_pay]
)
def mark_share_paid(
    performance_id: str,
    member_id: str,
    body: ShareRefBody,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        share = payments_svc.mark_share_paid(
            db, ctx, performance_id, member_id, reference=body.reference
        )
    return {"share": performances_svc.share_dict(db, share)}


@router.post(
    "/performances/{performance_id}/shares/{member_id}/undo-paid", dependencies=[_pay]
)
def undo_share_paid(
    performance_id: str,
    member_id: str,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        share = payments_svc.undo_share_paid(db, ctx, performance_id, member_id)
    return {"share": performances_svc.share_dict(db, share)}


@router.get("/invoices/{file_id}", dependencies=[_pay])
def get_invoice(
    file_id: str, ctx: FinanceCtx = Depends(staff_ctx), db: Session = Depends(get_db)
):
    with _reading():
        row = invoice_svc.get_file(db, ctx, file_id)
    return _file_response(row, file_id)


@router.get("/invoices/{file_id}/check", dependencies=[_pay])
def get_invoice_check(
    file_id: str, ctx: FinanceCtx = Depends(staff_ctx), db: Session = Depends(get_db)
):
    with _reading():
        row = invoice_svc.get_file(db, ctx, file_id)
        return invoice_svc.file_dict(db, ctx, row)


@router.post("/invoices/{file_id}/check-now", dependencies=[_pay])
def check_now(
    file_id: str, ctx: FinanceCtx = Depends(staff_ctx), db: Session = Depends(get_db)
):
    with _writing(db):
        row = invoice_svc.check_now(db, ctx, file_id)
    return invoice_svc.file_dict(db, ctx, row)


class WaiveBody(_Body):
    note: str = Field(max_length=1000)


@router.post("/invoices/{file_id}/waive", dependencies=[_pay])
def waive(
    file_id: str,
    body: WaiveBody,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        row = invoice_svc.waive(db, ctx, file_id, note=body.note)
    return invoice_svc.file_dict(db, ctx, row)


@router.post("/invoices/{file_id}/query", dependencies=[_pay])
def query_invoice(
    file_id: str, ctx: FinanceCtx = Depends(staff_ctx), db: Session = Depends(get_db)
):
    with _writing(db):
        out = invoice_svc.send_query(db, ctx, file_id)
    return out


class BillPreviewBody(_Body):
    member_id: str | None = None
    status: Literal["DRAFT", "SUBMITTED", "AUTHORISED"] = "DRAFT"


class BillConfirmBody(BillPreviewBody):
    fingerprint: str | None = None


@router.post("/performances/{performance_id}/bill/preview", dependencies=[_pay])
def bill_preview(
    performance_id: str,
    body: BillPreviewBody,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    with _reading():
        return bills_svc.preview(
            db,
            ctx,
            performance_id,
            member_id=body.member_id,
            status=body.status,
            config_db=config_db,
        )


@router.post("/performances/{performance_id}/bill/confirm", dependencies=[_pay])
def bill_confirm(
    performance_id: str,
    body: BillConfirmBody,
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    with _writing(db):
        push = bills_svc.confirm(
            db,
            ctx,
            performance_id,
            fingerprint=body.fingerprint,
            member_id=body.member_id,
            status=body.status,
            config_db=config_db,
        )
    return {"push": bills_svc.push_dict(push)}


# ===========================================================================
# Emails page
# ===========================================================================


@router.get("/emails")
def list_emails(
    performer_id: str | None = None,
    venue_id: str | None = None,
    kind: str | None = None,
    recipient: str | None = None,
    performance_id: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: FinanceCtx = Depends(staff_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        rows, total = email_links.list_links(
            db,
            ctx,
            performer_id=performer_id,
            venue_id=venue_id,
            kind=kind,
            recipient=recipient,
            performance_id=performance_id,
            since=_date(from_, "from"),
            until=_date(to, "to"),
            limit=limit,
            offset=offset,
        )
    return {"emails": rows, "total": total, "kinds": sorted(emails.KINDS)}


@router.get("/emails/{link_id}")
def get_email(
    link_id: str, ctx: FinanceCtx = Depends(staff_ctx), db: Session = Depends(get_db)
):
    with _reading():
        return email_links.get_link(db, ctx, link_id, with_body=True)


@router.post("/emails/{link_id}/resend")
def resend_email(
    link_id: str, ctx: FinanceCtx = Depends(staff_ctx), db: Session = Depends(get_db)
):
    with _writing(db):
        out = email_links.resend(db, ctx, link_id)
    return out
