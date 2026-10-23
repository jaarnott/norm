"""Wages reconciliation and the Xero side of Loaded Payroll —
``/api/payroll/reconciliation/...`` and ``/api/payroll/xero/...`` (F4).

What the Reconciliation page and the Bills settings panel's payroll section
call: the nine-term reconciliation per entity-week and for the group, the
intercompany picture, the stored journal and intercompany drafts, the push
ledger, the mapping blocks, and the Xero status refresh.

Every write that reaches Xero goes through ``page_writes.preview`` /
``confirm`` (plan §5.1): the page shows the card the consolidator built from
live data and the person's click is the approval, checked against the card's
fingerprint just before writing. The routes never call Xero themselves.

Gates, as F3's payroll router: ``require_permission("payroll:read")`` to read,
``payroll:write`` to change; the ctx limits a person to their venues (a venue
they cannot see is a 404); ``off`` is a 404 for every route; a push in
``shadow`` is 409 ``not_live`` before any card is built.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permission
from app.db.engine import get_config_db, get_db
from app.db.models import User
from app.services import page_writes
from app.services.finance_core.ctx import FinanceCtx, build_ctx
from app.services.finance_core.errors import FinanceError, Forbidden, http_exception
from app.services.payroll import intercompany as ico_svc
from app.services.payroll import journal as journal_svc
from app.services.payroll import reconciliation as recon_svc
from app.services.payroll import xero_mapping, xero_pushes, xero_status

router = APIRouter(prefix="/payroll", tags=["payroll-xero"])

JOURNAL_TOOL = "xero.post_wage_journal"
ICO_TOOL = "xero.post_intercompany_documents"
VOID_TOOL = "xero.void_payroll_document"


# ===========================================================================
# Plumbing
# ===========================================================================


def _ctx(db: Session, user: User) -> FinanceCtx:
    try:
        ctx = build_ctx(db, user)
    except FinanceError as exc:
        raise http_exception(exc) from exc
    if ctx.mode("payroll") == "off":
        raise HTTPException(
            404,
            detail={
                "code": "not_found",
                "message": "Loaded Payroll is not switched on for your organisation.",
            },
        )
    return ctx


def reader(
    user: User = Depends(require_permission("payroll:read")),
    db: Session = Depends(get_db),
) -> FinanceCtx:
    return _ctx(db, user)


def writer(
    user: User = Depends(require_permission("payroll:write")),
    db: Session = Depends(get_db),
) -> FinanceCtx:
    return _ctx(db, user)


def _run(fn, *args, **kwargs):
    """Call a service; its refusals become the router's one error shape. A
    venue the person cannot see is a 404, never a 403."""
    try:
        return fn(*args, **kwargs)
    except Forbidden as exc:
        if "venue" in exc.message:
            raise HTTPException(
                404, detail={"code": "not_found", "message": "Venue not found."}
            ) from exc
        raise http_exception(exc) from exc
    except FinanceError as exc:
        raise http_exception(exc) from exc


def _week(week_ending: str | None, week_start: str | None):
    return _run(recon_svc.week_start_of, week_start, week_ending)


def _require_live(ctx: FinanceCtx) -> None:
    _run(ctx.require_live, "payroll")


# ===========================================================================
# Reconciliation
# ===========================================================================


@router.get("/reconciliation")
def group_overview(
    week_ending: str | None = Query(None),
    week_start: str | None = Query(None),
    ctx: FinanceCtx = Depends(reader),
    db: Session = Depends(get_db),
):
    return _run(recon_svc.group_overview, db, ctx, _week(week_ending, week_start))


@router.get("/reconciliation/intercompany")
def intercompany(
    week_ending: str | None = Query(None),
    week_start: str | None = Query(None),
    venue_id: str | None = Query(None),
    ctx: FinanceCtx = Depends(reader),
    db: Session = Depends(get_db),
):
    return _run(ico_svc.picture, db, ctx, _week(week_ending, week_start), venue_id)


@router.get("/reconciliation/{venue_id}")
def reconciliation(
    venue_id: str,
    week_ending: str | None = Query(None),
    week_start: str | None = Query(None),
    ctx: FinanceCtx = Depends(reader),
    db: Session = Depends(get_db),
):
    return _run(recon_svc.compute, db, ctx, venue_id, _week(week_ending, week_start))


# ===========================================================================
# Drafts
# ===========================================================================


class WeekBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venue_id: str
    week_ending: str | None = None
    week_start: str | None = None


@router.get("/xero/journal")
def journal(
    venue_id: str = Query(...),
    week_ending: str | None = Query(None),
    week_start: str | None = Query(None),
    ctx: FinanceCtx = Depends(reader),
    db: Session = Depends(get_db),
):
    return _run(journal_svc.current, db, ctx, venue_id, _week(week_ending, week_start))


@router.post("/xero/journal/build")
def build_journal(
    body: WeekBody,
    ctx: FinanceCtx = Depends(writer),
    db: Session = Depends(get_db),
):
    out = _run(
        journal_svc.build,
        db,
        ctx,
        body.venue_id,
        _week(body.week_ending, body.week_start),
    )
    db.commit()
    return out


# ===========================================================================
# Pushes — through the page-side approval helper
# ===========================================================================


class JournalPushBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venue_id: str
    week_ending: str | None = None
    week_start: str | None = None
    draft_id: str | None = None
    basis_sha256: str | None = None
    replace: bool = False
    fingerprint: str | None = None


class IcoPushBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venue_id: str
    week_ending: str | None = None
    week_start: str | None = None
    replace: bool = False
    fingerprint: str | None = None


class VoidBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    push_id: str
    #: Optional — the router reads the venue off the push; one given must match.
    venue_id: str | None = None
    fingerprint: str | None = None


def _journal_params(body: JournalPushBody) -> dict:
    monday = _week(body.week_ending, body.week_start)
    params = {
        "week_ending": recon_svc.week_bounds(monday)[1].isoformat(),
        "replace": "true" if body.replace else "false",
    }
    if body.draft_id:
        params["draft_id"] = body.draft_id
    if body.basis_sha256:
        params["basis_sha256"] = body.basis_sha256
    return params


def _ico_params(body: IcoPushBody) -> dict:
    monday = _week(body.week_ending, body.week_start)
    return {
        "week_ending": recon_svc.week_bounds(monday)[1].isoformat(),
        "replace": "true" if body.replace else "false",
    }


def _preview(db, config_db, ctx, *, tool, params, venue_id):
    _require_live(ctx)
    return page_writes.preview(
        db,
        tool=tool,
        params=params,
        user=ctx.user,
        venue_id=venue_id,
        config_db=config_db,
    )


def _confirm(db, config_db, ctx, *, tool, params, venue_id, fingerprint):
    _require_live(ctx)
    out = page_writes.confirm(
        db,
        tool=tool,
        params=params,
        user=ctx.user,
        venue_id=venue_id,
        fingerprint=fingerprint,
        config_db=config_db,
    )
    db.commit()
    return out


@router.post("/xero/journal/preview")
def preview_journal_push(
    body: JournalPushBody,
    ctx: FinanceCtx = Depends(writer),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    return _preview(
        db,
        config_db,
        ctx,
        tool=JOURNAL_TOOL,
        params=_journal_params(body),
        venue_id=body.venue_id,
    )


@router.post("/xero/journal/confirm")
def confirm_journal_push(
    body: JournalPushBody,
    ctx: FinanceCtx = Depends(writer),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    return _confirm(
        db,
        config_db,
        ctx,
        tool=JOURNAL_TOOL,
        params=_journal_params(body),
        venue_id=body.venue_id,
        fingerprint=body.fingerprint,
    )


@router.post("/xero/intercompany/preview")
def preview_ico_push(
    body: IcoPushBody,
    ctx: FinanceCtx = Depends(writer),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    return _preview(
        db,
        config_db,
        ctx,
        tool=ICO_TOOL,
        params=_ico_params(body),
        venue_id=body.venue_id,
    )


@router.post("/xero/intercompany/confirm")
def confirm_ico_push(
    body: IcoPushBody,
    ctx: FinanceCtx = Depends(writer),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    return _confirm(
        db,
        config_db,
        ctx,
        tool=ICO_TOOL,
        params=_ico_params(body),
        venue_id=body.venue_id,
        fingerprint=body.fingerprint,
    )


def _push_venue(db: Session, ctx: FinanceCtx, body: VoidBody) -> str:
    """The venue a void acts at is the push's own; a venue the page names
    must be that one (a mismatch is a wrong click, never a different venue)."""
    venue_id = _run(xero_pushes.get_push, db, ctx, body.push_id).venue_id
    if body.venue_id and body.venue_id != venue_id:
        raise HTTPException(
            400,
            detail={
                "code": "invalid",
                "message": "That push belongs to another venue than the one named.",
            },
        )
    return venue_id


@router.post("/xero/void/preview")
def preview_void(
    body: VoidBody,
    ctx: FinanceCtx = Depends(writer),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    venue_id = _push_venue(db, ctx, body)
    return _preview(
        db,
        config_db,
        ctx,
        tool=VOID_TOOL,
        params={"push_id": body.push_id},
        venue_id=venue_id,
    )


@router.post("/xero/void/confirm")
def confirm_void(
    body: VoidBody,
    ctx: FinanceCtx = Depends(writer),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    venue_id = _push_venue(db, ctx, body)
    return _confirm(
        db,
        config_db,
        ctx,
        tool=VOID_TOOL,
        params={"push_id": body.push_id},
        venue_id=venue_id,
        fingerprint=body.fingerprint,
    )


# ===========================================================================
# Ledger and status
# ===========================================================================


@router.get("/xero/pushes")
def pushes(
    venue_id: str | None = Query(None),
    week_ending: str | None = Query(None),
    week_start: str | None = Query(None),
    kind: str | None = Query(None),
    include_voided: bool = Query(False),
    ctx: FinanceCtx = Depends(reader),
    db: Session = Depends(get_db),
):
    monday = _week(week_ending, week_start) if (week_ending or week_start) else None
    rows = _run(
        xero_pushes.list_pushes,
        db,
        ctx,
        venue_id=venue_id,
        week_start=monday,
        kind=kind,
        include_voided=include_voided,
    )
    from app.db.models import Venue

    names = dict(
        db.query(Venue.id, Venue.name).filter(Venue.organization_id == ctx.org_id).all()
    )
    return {"pushes": [xero_pushes.to_dict(r, names) for r in rows]}


@router.get("/xero/status")
def status(ctx: FinanceCtx = Depends(reader), db: Session = Depends(get_db)):
    return {"last_run": _run(xero_status.last_run, db, ctx)}


class RefreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venue_id: str | None = None


@router.post("/xero/status/refresh")
def refresh_status(
    body: RefreshBody | None = None,
    ctx: FinanceCtx = Depends(writer),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    out = _run(
        xero_status.refresh,
        db,
        ctx,
        config_db=config_db,
        venue_id=body.venue_id if body else None,
    )
    db.commit()
    return out


# ===========================================================================
# Mapping
# ===========================================================================


class MappingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venue_id: str
    payroll: dict | None = None
    intercompany: dict | None = None
    validate_only: bool = False


@router.get("/xero/mapping")
def get_mapping(
    venue_id: str = Query(...),
    ctx: FinanceCtx = Depends(reader),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    return _run(xero_mapping.read, db, ctx, venue_id, config_db=config_db)


@router.put("/xero/mapping")
def put_mapping(
    body: MappingBody,
    ctx: FinanceCtx = Depends(writer),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    out = _run(
        xero_mapping.save,
        db,
        ctx,
        body.venue_id,
        body.payroll,
        body.intercompany,
        config_db=config_db,
        validate_only=body.validate_only,
    )
    if any(i["level"] == "error" for i in out["issues"]):
        raise HTTPException(
            400,
            detail={
                "code": "invalid",
                "message": "The mapping has errors.",
                "issues": out["issues"],
            },
        )
    db.commit()
    return out
