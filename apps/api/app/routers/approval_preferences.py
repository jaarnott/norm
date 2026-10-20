"""What Norm may do without asking you — read and change your own.

Settings → Preferences → Approvals (Oct 2026). One list of the writes Norm can
make for this person: ordinary writes are "Ask me" or "Always allow"; the two
invoice tools have levels, and receiving has switches at its top level. It
replaced the per-user "workflow modes" endpoints and the per-venue receiving
ladder (``/venues/{id}/invoice-autopilot``).

Anyone may set their own, and only their own. Every change is recorded
(an ``approval_preference_set`` Approval row).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.engine import get_config_db, get_db
from app.db.models import User
from app.services import approvals

router = APIRouter()


def _entries(db: Session, config_db: Session, user: User) -> list[dict]:
    from app.services.entitlements import apps_on, org_id_for_user

    apps = apps_on(org_id_for_user(user.id, db), db, config_db)
    return approvals.catalog(config_db, apps)


def _view(user: User, entry: dict) -> dict:
    key, row = entry["key"], entry["row"]
    policy = row.get("approval") or {}
    label = str(policy.get("label") or key)
    out = {
        "key": key,
        "label": label[:1].upper() + label[1:],
        "summary": approvals.describe(user, key, row),
    }
    if policy.get("levels"):
        chosen = approvals.preference(user, key)
        switches = [
            {"id": o.get("id"), "label": o.get("label")}
            for o in policy.get("options") or []
            if isinstance(o, dict)
        ]
        if row.get("action") == "review_and_receive_invoices":
            # Switches for what Norm may do AFTER a receive (Xero bill): their
            # own group, shown at every level.
            from app.services import venue_autopilot as VA

            switches += [
                {"id": a, "label": text, "group": "after_receive"}
                for a, text in VA.AFTER_RECEIVE.items()
            ]
        known = {sw["id"] for sw in switches}
        out.update(
            kind="levels",
            level=approvals.level(user, key),
            levels=[
                {k: lv.get(k) for k in ("id", "label", "description")}
                for lv in policy["levels"]
                if isinstance(lv, dict)
            ],
            switches=switches,
            # A switch that no longer exists (receive_unreconciled_totals,
            # removed 16 Oct 2026) stays in what people saved; sent back,
            # it would be refused as unknown and nothing could be saved.
            options={
                k: True
                for k, v in (chosen.get("options") or {}).items()
                if v is True and k in known
            },
        )
    elif policy.get("allow_auto"):
        out.update(
            kind="ask",
            value="always" if approvals.always_allowed(user, key, row) else "ask",
        )
    else:
        # Changing what Norm may do without asking is itself one of these:
        # it always asks, so Norm can never raise its own autonomy silently.
        out.update(kind="locked", value="ask")
    return out


@router.get("/approval-preferences")
def get_approval_preferences(
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    # Settings are per organisation: say which one these are for.
    from app.db.models import Organization
    from app.services import active_org

    org_id = active_org.org_id_for(db, user.id)
    org = (
        db.query(Organization).filter(Organization.id == org_id).first()
        if org_id
        else None
    )
    return {
        "organization": {"id": org.id, "name": org.name} if org else None,
        "tools": [_view(user, e) for e in _entries(db, config_db, user)],
    }


class SetPreference(BaseModel):
    key: str
    #: "ask" | "always" for an ordinary write; {"level", "options"} for one
    #: with levels.
    value: str | dict


@router.put("/approval-preferences")
def set_approval_preference(
    body: SetPreference,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    entry = next(
        (e for e in _entries(db, config_db, user) if e["key"] == body.key), None
    )
    if entry is None:
        raise HTTPException(404, f"no write tool {body.key!r} to set")
    try:
        approvals.set_preference(
            db, user, entry["key"], entry["row"], body.value, via="settings"
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return _view(user, entry)
