"""Norm's receiving-rule tools (Oct 2026): an organisation's own rules for the
invoices it receives — the due date first (services/receiving_rules).

* ``get_receiving_rules`` (read): the organisation's rules, each venue's
  overrides and what is in force, the rule types, and whether this person may
  change them.
* ``set_receiving_rule`` (write): save or remove one, for the organisation or
  one venue. Always shown first as a card with what it does to invoices dated
  across a week, so "which Sunday?" is settled by looking; an owner or a
  manager only.

A saved note or memory never changes how invoices are received; a rule does —
the review applies it on the card and under Autopilot alike.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.internal_tools import _scoped_venue_id, _thread_user, previews, register
from app.services import receiving_rules as RR

_REFUSED = (
    "Only an owner or a manager can set receiving rules. Ask one of them, or "
    "they can set it in Settings → Receiving rules."
)


def _org_for(db: Session, thread_id: str | None, user) -> str | None:
    from app.db.models import Thread, Venue
    from app.services import active_org

    thread = (
        db.query(Thread).filter(Thread.id == thread_id).first() if thread_id else None
    )
    org = getattr(thread, "organization_id", None)
    if not org and thread is not None and thread.venue_id:
        org = (
            db.query(Venue.organization_id).filter(Venue.id == thread.venue_id).scalar()
        )
    if not org and user is not None:
        membership = active_org.membership_for(db, user.id)
        org = membership.organization_id if membership else None
    return org


def _params(p: dict) -> dict:
    return {
        k: p.get(k)
        for k in ("basis", "weekday", "days", "day", "when")
        if p.get(k) is not None
    }


def _plan(params: dict, db: Session, thread_id: str | None) -> dict:
    """Check a change and say what it does, writing nothing."""
    user = _thread_user(db, thread_id)
    if user is None:
        return {"error": "no person on this conversation"}
    org = _org_for(db, thread_id, user)
    if not org:
        return {"error": "no organisation for this conversation"}
    if not RR.can_manage(db, user, org):
        return {"error": _REFUSED}
    kind = str(params.get("kind") or "due_date")
    scope = str(params.get("scope") or "organisation")
    venue_id = venue_name = None
    if scope == "venue":
        venue_id, err = _scoped_venue_id(params, db)
        if err or not venue_id:
            return {"error": f"which venue? {err or ''}".strip()}
        venue = RR._venue(db, venue_id)
        if venue is None or venue.organization_id != org:
            return {"error": "that venue isn't in this organisation"}
        venue_name = venue.name
    elif scope != "organisation":
        return {"error": "scope is 'organisation' or 'venue'"}
    remove = bool(params.get("remove"))
    cleaned = None
    if not remove:
        try:
            cleaned = RR.clean(kind, _params(params))
        except RR.RuleError as exc:
            return {"error": str(exc)}
    from app.db.models import ReceivingRule

    q = db.query(ReceivingRule).filter(
        ReceivingRule.organization_id == org, ReceivingRule.kind == kind
    )
    q = (
        q.filter(ReceivingRule.venue_id == venue_id)
        if venue_id
        else q.filter(ReceivingRule.venue_id.is_(None))
    )
    current = q.first()
    before = RR.describe(kind, current.params) if current else "no rule"
    after = "no rule" if remove else RR.describe(kind, cleaned)
    return {
        "user": user,
        "org": org,
        "kind": kind,
        "venue_id": venue_id,
        "where": venue_name or "the whole organisation",
        "remove": remove,
        "params": cleaned,
        "before": before,
        "after": after,
        "unchanged": (current is None and remove)
        or (current is not None and not remove and (current.params or {}) == cleaned),
    }


@register("norm", "get_receiving_rules")
def _get_receiving_rules(params: dict, db: Session, thread_id: str | None) -> dict:
    user = _thread_user(db, thread_id)
    if user is None:
        return {
            "success": False,
            "data": None,
            "error": "no person on this conversation",
        }
    org = _org_for(db, thread_id, user)
    if not org:
        return {
            "success": False,
            "data": None,
            "error": "no organisation for this conversation",
        }
    view = RR.view(db, user, org)
    name = str((params or {}).get("venue") or "").strip()
    if name:
        venue_id, err = _scoped_venue_id({"venue": name}, db)
        view["venues"] = [v for v in view["venues"] if v["id"] == venue_id]
        if not view["venues"]:
            return {
                "success": False,
                "data": None,
                "error": err or f"no venue '{name}'",
            }
    view["how_it_works"] = (
        "Norm applies the rule in force whenever it reviews an invoice: the card "
        "and Autopilot both follow it, shown on the invoice as the venue's rule. "
        "A venue's own rule overrides the organisation's. A rule wins over what "
        "the invoice says. Owners and managers change them, here or in "
        "Settings → Receiving rules."
    )
    view["_no_display"] = True
    return {"success": True, "data": view}


@previews("norm", "set_receiving_rule")
def _preview_set_receiving_rule(
    params: dict, db: Session, thread_id: str | None
) -> dict:
    plan = _plan(params, db, thread_id)
    if "error" in plan:
        return {"error": plan["error"]}
    if plan["unchanged"]:
        return {"done": {"result": "already set", "rule": plan["after"]}}
    label = RR.KINDS[plan["kind"]]["label"]
    note = "Changes take effect the next time Norm reviews an invoice."
    if not plan["remove"]:
        shown = RR.examples(plan["kind"], plan["params"])
        note = (
            "For invoices dated across a week: "
            + "; ".join(e["words"] for e in shown)
            + ". "
            + note
        )
    return {
        "preview": {
            "title": "Change a receiving rule",
            "target": f"{label} — {plan['where']}",
            "changes": [
                {"field": label, "before": plan["before"], "after": plan["after"]}
            ],
            "note": note,
        }
    }


@register("norm", "set_receiving_rule")
def _set_receiving_rule(params: dict, db: Session, thread_id: str | None) -> dict:
    plan = _plan(params, db, thread_id)
    if "error" in plan:
        return {"success": False, "data": {}, "error": plan["error"]}
    if plan["remove"]:
        RR.remove_rule(
            db, org_id=plan["org"], venue_id=plan["venue_id"], kind=plan["kind"]
        )
    else:
        RR.set_rule(
            db,
            org_id=plan["org"],
            venue_id=plan["venue_id"],
            kind=plan["kind"],
            params=plan["params"],
            note=params.get("note"),
            user=plan["user"],
        )
    return {
        "success": True,
        "data": {
            "where": plan["where"],
            "rule": plan["after"],
            "before": plan["before"],
            "takes_effect": "the next time Norm reviews an invoice there",
        },
    }
