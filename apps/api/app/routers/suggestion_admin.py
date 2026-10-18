"""Settings → AI team → Suggestions: tune Home's suggestions site-wide.

Platform admins only (``admin:system``): the config DB is shared by every
organisation and every environment. Reads the effective config with its
defaults so the page can show what changed; saves through
``services/suggestions/config.py``, which checks first and keeps the value
before each save for undo. docs/plans/suggestions-config.md.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permission
from app.db.engine import get_config_db, get_config_db_rw, get_db
from app.db.models import User

router = APIRouter(prefix="/admin/suggestions", tags=["suggestions-admin"])

ADMIN = require_permission("admin:system")


def _config():
    from app.services.suggestions import config

    return config


def _invalid(exc) -> HTTPException:
    return HTTPException(422, {"problems": getattr(exc, "problems", [str(exc)])})


def _rows(config_db) -> dict:
    from app.db.config_models import SuggestionConfig

    return {r.key: r for r in config_db.query(SuggestionConfig).all()}


@router.get("")
def describe(
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(ADMIN),
):
    """Everything the page shows: each value with its default, what changed,
    whether it can be undone, and the live insights per detector."""
    from app.auth.permissions import ALL_ORG_PERMISSIONS
    from app.db.config_models import AgentConfig, MarketplaceApp
    from app.db.models import Insight
    from app.services.insights.rules import AREA_MEMBERS, DETECTORS

    cfg = _config()
    cfg.clear_cache()
    eff = cfg.current(config_db)
    rows = _rows(config_db)

    def meta(key):
        r = rows.get(key)
        return {
            "can_undo": bool(r is not None and r.previous is not None),
            "updated_by": r.updated_by if r else None,
            "updated_at": r.updated_at.isoformat() if r and r.updated_at else None,
        }

    settings = []
    for name, spec in cfg.SETTINGS.items():
        default = cfg.setting_default(name)
        value = eff.settings.get(name, default)
        settings.append(
            {
                "name": name,
                "label": spec.label,
                "help": spec.help,
                "kind": spec.kind,
                "group": spec.group,
                "min": spec.minimum,
                "max": spec.maximum,
                "default": default,
                "value": value,
                "changed": value != default,
            }
        )

    defaults = cfg.default_prompts()
    prompts = []
    for pid, p in eff.prompt_rows.items():
        d = defaults.get(pid)
        changed = [k for k in p if d is not None and k in d and p[k] != d[k]]
        prompts.append({**p, "changed": changed, **meta(f"prompt:{pid}")})

    now = datetime.now(timezone.utc)
    live = {
        det: (n, last)
        for det, n, last in db.query(
            Insight.detector, func.count(Insight.id), func.max(Insight.updated_at)
        )
        .filter(Insight.expires_at > now)
        .group_by(Insight.detector)
        .all()
    }
    detectors = []
    for name, spec in DETECTORS.items():
        e = eff.detectors[name]
        default_members = (
            list(spec.members)
            if spec.members is not None
            else list(AREA_MEMBERS.get(spec.area, ()))
        )
        members = (
            list(e["members"]) if e.get("members") is not None else default_members
        )
        n, last = live.get(name, (0, None))
        changed = [
            k
            for k, dv in (
                ("enabled", True),
                ("weight", spec.weight),
                ("lift", spec.lift),
            )
            if e.get(k) != dv
        ]
        if members != default_members:
            changed.append("members")
        detectors.append(
            {
                "name": name,
                "label": spec.label,
                "description": spec.description,
                "area": spec.area,
                "cadence": spec.cadence,
                "audience": spec.audience,
                "valued": spec.valued,
                "enabled": e["enabled"],
                "weight": e["weight"],
                "default_weight": spec.weight,
                "lift": e["lift"],
                "default_lift": spec.lift,
                "members": members,
                "default_members": default_members,
                "thresholds": [
                    {
                        "name": k,
                        "label": t.label,
                        "kind": t.kind,
                        "min": t.minimum,
                        "max": t.maximum,
                        "default": t.default,
                        "value": e["thresholds"][k],
                        "changed": e["thresholds"][k] != t.default,
                    }
                    for k, t in spec.thresholds.items()
                ],
                "templates": [
                    {
                        "name": k,
                        "label": t.label,
                        "default": t.default,
                        "value": e["templates"][k],
                        "changed": e["templates"][k] != t.default,
                        "placeholders": t.placeholders,
                        "example": t.example,
                    }
                    for k, t in spec.templates.items()
                ],
                "changed": changed
                + [
                    f"threshold:{k}"
                    for k, t in spec.thresholds.items()
                    if e["thresholds"][k] != t.default
                ]
                + [
                    f"template:{k}"
                    for k, t in spec.templates.items()
                    if e["templates"][k] != t.default
                ],
                "live": {"count": n, "last": last.isoformat() if last else None},
                **meta(f"detector:{name}"),
            }
        )

    members = [
        {"slug": a.agent_slug, "name": a.display_name}
        for a in config_db.query(AgentConfig).order_by(AgentConfig.display_name).all()
        if a.agent_slug != "base"
    ]
    apps = [
        {"slug": a.slug, "name": a.name or a.slug}
        for a in config_db.query(MarketplaceApp).all()
    ]
    if not any(a["slug"] == "norm-core" for a in apps):
        apps.append({"slug": "norm-core", "name": "Norm Core"})
    return {
        "settings": settings,
        "settings_meta": meta("settings"),
        "wording_version": str(eff.wording_version),
        "guidance_placeholders": cfg.GUIDANCE_PLACEHOLDERS,
        "prompts": prompts,
        "new_prompt": cfg.defaults_template(),
        "detectors": detectors,
        "members": members,
        "apps": sorted(apps, key=lambda a: a["slug"]),
        "permissions": ALL_ORG_PERMISSIONS,
    }


class Changes(BaseModel):
    changes: dict


@router.put("/settings")
def save_settings(
    body: Changes,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(ADMIN),
):
    cfg = _config()
    try:
        cfg.save_settings(config_db, body.changes, user.email)
    except cfg.Invalid as exc:
        raise _invalid(exc)
    return {"ok": True}


@router.put("/prompts/{prompt_id}")
def save_prompt(
    prompt_id: str,
    body: Changes,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(ADMIN),
):
    cfg = _config()
    try:
        cfg.save_prompt(config_db, prompt_id, body.changes, user.email)
    except cfg.Invalid as exc:
        raise _invalid(exc)
    return {"ok": True}


@router.delete("/prompts/{prompt_id}")
def delete_prompt(
    prompt_id: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(ADMIN),
):
    cfg = _config()
    try:
        cfg.delete_prompt(config_db, prompt_id, user.email)
    except cfg.Invalid as exc:
        raise _invalid(exc)
    return {"ok": True}


@router.put("/detectors/{name}")
def save_detector(
    name: str,
    body: Changes,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(ADMIN),
):
    cfg = _config()
    try:
        cfg.save_detector(config_db, name, body.changes, user.email)
    except cfg.Invalid as exc:
        raise _invalid(exc)
    return {"ok": True}


class Key(BaseModel):
    key: str


def _check_key(key: str) -> None:
    from app.services.insights.rules import DETECTORS

    if key == "settings" or key.startswith("prompt:"):
        return
    if key.startswith("detector:") and key.split(":", 1)[1] in DETECTORS:
        return
    raise HTTPException(404, "No such setting")


@router.post("/reset")
def reset(
    body: Key,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(ADMIN),
):
    cfg = _config()
    _check_key(body.key)
    try:
        cfg.reset(config_db, body.key, user.email)
    except cfg.Invalid as exc:
        raise _invalid(exc)
    return {"ok": True}


@router.post("/undo")
def undo(
    body: Key,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(ADMIN),
):
    cfg = _config()
    _check_key(body.key)
    try:
        cfg.undo(config_db, body.key, user.email)
    except cfg.Invalid as exc:
        raise _invalid(exc)
    return {"ok": True}


# ------------------------------------------------------------------ preview
class PreviewBody(BaseModel):
    user_id: str
    venue_id: str | None = None
    member: str | None = None
    limit: int = 4


@router.post("/preview")
def preview(
    body: PreviewBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(ADMIN),
):
    """What this person would see now, and why every other candidate wasn't
    picked. Reads only: nothing is recorded as shown."""
    from app.services.suggestions.engine import Context, explain

    person = db.query(User).filter(User.id == body.user_id).first()
    if person is None:
        raise HTTPException(404, "No such person")
    ctx = Context(db, config_db, person, body.venue_id, body.member)
    return {
        "venue": ctx.venue.name if ctx.venue else None,
        "local_time": ctx.local.strftime("%a %d %b, %H:%M"),
        **explain(ctx, max(1, min(body.limit, 4))),
    }


@router.get("/people")
def people(
    q: str = Query("", max_length=100),
    db: Session = Depends(get_db),
    user: User = Depends(ADMIN),
):
    """People to preview as: name or email matches, with their venues."""
    from app.services.venue_service import get_user_venues

    query = db.query(User)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter((User.email.ilike(like)) | (User.full_name.ilike(like)))
    out = []
    for u in query.order_by(User.email).limit(20).all():
        venues = list(get_user_venues(db, u.id) or [])
        out.append(
            {
                "id": u.id,
                "email": u.email,
                "name": u.full_name,
                "venues": [{"id": v.id, "name": v.name} for v in venues],
            }
        )
    return {"people": out}


# ------------------------------------------------------------------ templates and wording
class RenderBody(BaseModel):
    detector: str
    template: str
    text: str


@router.post("/render")
def render(
    body: RenderBody,
    db: Session = Depends(get_db),
    user: User = Depends(ADMIN),
):
    """A template checked, filled in with its example, and with the facts of
    up to five live insights of that detector (any organisation)."""
    from app.db.models import Insight, Venue
    from app.services.insights.rules import DETECTORS, check_template, render_text

    spec = DETECTORS.get(body.detector)
    if spec is None or body.template not in spec.templates:
        raise HTTPException(404, "No such template")
    t = spec.templates[body.template]
    problems = check_template(body.text, t.placeholders)
    if problems:
        return {"problems": problems, "example": None, "live": []}
    now = datetime.now(timezone.utc)
    rows = (
        db.query(Insight)
        .filter(Insight.detector == body.detector, Insight.expires_at > now)
        .order_by(Insight.value.desc())
        .limit(20)
        .all()
    )
    live = []
    for r in rows:
        facts = (r.data or {}).get("facts")
        if not isinstance(facts, dict):
            continue
        if (r.data or {}).get("template", "prompt") not in (
            body.template,
            None,
        ) and len(spec.templates) > 1:
            continue
        try:
            text = render_text(body.text, facts)
        except (KeyError, ValueError, IndexError):
            continue
        venue = db.query(Venue.name).filter(Venue.id == r.venue_id).scalar()
        live.append({"venue": venue, "now": r.prompt, "rendered": text})
        if len(live) >= 5:
            break
    return {
        "problems": [],
        "example": render_text(body.text, t.example),
        "live": live,
    }


class WordingBody(BaseModel):
    instructions: str


@router.post("/try-wording")
def try_wording(
    body: WordingBody,
    db: Session = Depends(get_db),
    user: User = Depends(ADMIN),
):
    """One Haiku call (Norm's cost, about $0.004) rewording up to six live
    insights of one venue under these instructions. Changes nothing."""
    from app.db.models import Insight, Venue
    from app.services.insights import wording

    problems = _config().check_setting("wording_instructions", body.instructions)
    if problems:
        raise HTTPException(422, {"problems": problems})
    now = datetime.now(timezone.utc)
    top = (
        db.query(Insight.venue_id, func.count(Insight.id))
        .filter(Insight.expires_at > now)
        .group_by(Insight.venue_id)
        .order_by(func.count(Insight.id).desc())
        .first()
    )
    if top is None:
        raise HTTPException(409, "There are no live insights to try it on.")
    venue = db.get(Venue, top[0])
    rows = (
        db.query(Insight)
        .filter(Insight.venue_id == venue.id, Insight.expires_at > now)
        .order_by(Insight.value.desc())
        .limit(6)
        .all()
    )
    try:
        results = wording.trial(db, venue, rows, body.instructions)
        db.commit()  # the llm_calls row (no organisation: Norm's cost)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(502, f"The wording call failed: {exc}"[:300])
    return {"venue": venue.name, "results": results}
