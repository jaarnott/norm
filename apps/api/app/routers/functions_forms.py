"""Norm Functions — the web-form builder for staff (``/api/functions/forms``).

Signed-in staff of an organisation with Functions switched on build their
venues' enquiry forms here (fixed field set, theme tokens, recipient, subject
template, success message), copy the embed snippets, and read submissions.
The rules — validation, the venue scope, live-only writes — are in
``services/functions/forms.py``; the public side of the same forms is in
``routers/public.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.engine import get_config_db, get_db
from app.db.models import User, Venue
from app.services.functions import forms as forms_svc
from app.services.functions.ctx import FunctionsCtx, build_ctx
from app.services.functions.entitlement import functions_enabled
from app.services.functions.errors import FunctionsError, http_exception

router = APIRouter(prefix="/functions/forms", tags=["functions"])


def functions_ctx(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
) -> FunctionsCtx:
    """The caller's Functions ctx, or 403 when Functions is off for their
    organisation."""
    try:
        ctx = build_ctx(db, user)
    except FunctionsError as err:
        raise http_exception(err) from err
    if not functions_enabled(db, config_db, ctx.org_id):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "functions_off",
                "message": "Norm Functions isn't switched on for this organisation.",
            },
        )
    from app.routers.functions import refuse_unless_functions

    refuse_unless_functions(db, config_db, user)
    return ctx


#: Keys of ``form_dict`` that aren't settings (``version`` doubles as
#: ``expected_version`` when that isn't sent).
_READ_ONLY = frozenset(
    {
        "id",
        "version",
        "venue_id",
        "venue_name",
        "embed",
        "fields",
        "created_at",
        "updated_at",
        "submissions",
        "last_submitted_at",
    }
)


def _version(value) -> int | None:
    """``expected_version`` as sent: a whole number or nothing (anything else
    is the caller's mistake, not a server error)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid",
                "message": "expected_version must be a whole number.",
            },
        )
    return value


def _fail(err: FunctionsError) -> HTTPException:
    return http_exception(err)


def _venue_names(db: Session, ctx: FunctionsCtx) -> dict:
    return {
        v.id: v.name
        for v in db.query(Venue.id, Venue.name).filter(
            Venue.organization_id == ctx.org_id
        )
    }


def _full(db: Session, ctx: FunctionsCtx, form) -> dict:
    out = forms_svc.form_dict(form, forms_svc.form_fields(db, ctx, form.id))
    out["venue_name"] = _venue_names(db, ctx).get(form.venue_id)
    return out


@router.get("")
def list_forms(
    venue_id: str | None = Query(default=None),
    ctx: FunctionsCtx = Depends(functions_ctx),
    db: Session = Depends(get_db),
):
    forms = forms_svc.list_forms(db, ctx, venue_id=venue_id)
    counts = forms_svc.submission_counts(db, ctx, [f.id for f in forms])
    names = _venue_names(db, ctx)
    out = []
    for f in forms:
        row = forms_svc.form_dict(f)
        total, last = counts.get(f.id, (0, None))
        row.update(
            venue_name=names.get(f.venue_id),
            submissions=total,
            last_submitted_at=last.isoformat() if last else None,
        )
        out.append(row)
    return {"forms": out}


@router.post("", status_code=201)
def create_form(
    body: dict = Body(...),
    ctx: FunctionsCtx = Depends(functions_ctx),
    db: Session = Depends(get_db),
):
    """``{venue_id, name, slug?, fields?: [...], ...settings}`` — without
    ``fields`` the form starts with name / email / phone / date / guests /
    message."""
    values = dict(body)
    try:
        form = forms_svc.create_form(
            db,
            ctx,
            venue_id=values.pop("venue_id", None),
            name=values.pop("name", None),
            slug=values.pop("slug", None),
            fields=values.pop("fields", None),
            **values,
        )
        out = _full(db, ctx, form)
    except FunctionsError as err:
        raise _fail(err) from err
    db.commit()
    return out


@router.get("/submissions")
def list_submissions(
    form_id: str | None = Query(default=None),
    venue_id: str | None = Query(default=None),
    include_spam: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: FunctionsCtx = Depends(functions_ctx),
    db: Session = Depends(get_db),
):
    try:
        rows = forms_svc.list_submissions(
            db,
            ctx,
            form_id=form_id,
            venue_id=venue_id,
            include_spam=include_spam,
            limit=limit,
            offset=offset,
        )
    except FunctionsError as err:
        raise _fail(err) from err
    return {"submissions": [forms_svc.submission_dict(s) for s in rows]}


@router.get("/{form_id}")
def get_form(
    form_id: str,
    ctx: FunctionsCtx = Depends(functions_ctx),
    db: Session = Depends(get_db),
):
    try:
        return _full(db, ctx, forms_svc.get_form(db, ctx, form_id))
    except FunctionsError as err:
        raise _fail(err) from err


@router.put("/{form_id}")
def update_form(
    form_id: str,
    body: dict = Body(...),
    ctx: FunctionsCtx = Depends(functions_ctx),
    db: Session = Depends(get_db),
):
    """Settings (``FORM_SETTINGS``) with ``expected_version``. Read-only keys
    of a form the page loaded (id, embed, fields, counts…) are ignored, so the
    builder can send back what it got; anything else unknown is refused."""
    values = {k: v for k, v in body.items() if k not in _READ_ONLY}
    expected = _version(values.pop("expected_version", body.get("version")))
    try:
        forms_svc.update_form(db, ctx, form_id, expected_version=expected, **values)
        out = _full(db, ctx, forms_svc.get_form(db, ctx, form_id))
    except FunctionsError as err:
        raise _fail(err) from err
    db.commit()
    return out


@router.put("/{form_id}/fields")
def replace_fields(
    form_id: str,
    body: dict = Body(...),
    ctx: FunctionsCtx = Depends(functions_ctx),
    db: Session = Depends(get_db),
):
    """``{fields: [...], expected_version}`` — the whole list, in order."""
    try:
        forms_svc.replace_fields(
            db,
            ctx,
            form_id,
            body.get("fields"),
            expected_version=_version(body.get("expected_version")),
        )
        out = _full(db, ctx, forms_svc.get_form(db, ctx, form_id))
    except FunctionsError as err:
        raise _fail(err) from err
    db.commit()
    return out


@router.delete("/{form_id}")
def delete_form(
    form_id: str,
    ctx: FunctionsCtx = Depends(functions_ctx),
    db: Session = Depends(get_db),
):
    """Deletes a form that never received a submission; otherwise switches
    it off (its submissions keep pointing at it)."""
    try:
        deleted = forms_svc.delete_form(db, ctx, form_id)
    except FunctionsError as err:
        raise _fail(err) from err
    db.commit()
    return {"deleted": deleted, "active": False}


@router.get("/{form_id}/submissions")
def form_submissions(
    form_id: str,
    include_spam: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: FunctionsCtx = Depends(functions_ctx),
    db: Session = Depends(get_db),
):
    return list_submissions(
        form_id=form_id,
        venue_id=None,
        include_spam=include_spam,
        limit=limit,
        offset=offset,
        ctx=ctx,
        db=db,
    )
