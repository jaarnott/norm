"""One way to decide an invoice, whoever decides it.

The owner's intent (20 Oct 2026): there is one way to decide an invoice. A
person decides it on the card — accept or decline Norm's suggestions, pick a
unit, type a quantity, strike a line. Autopilot does exactly the same things
on the person's behalf, and the venue's switches only say which suggestions
Autopilot may accept. Reopening a card you changed earlier and Autopilot
picking up that card overnight must behave identically.

Until this module, the web card worked a suggestion out in the browser and
sent the server the changed fields plus a note, while Autopilot applied
suggestions in code of its own; "is this ready to receive?" was written once
on the server and once in the browser. Every decision now comes through
``decide`` — the card sends "accept suggestion X" (a ``decide`` op on the
working-document PATCH), Autopilot calls it for each suggestion the switches
allow — and readiness is worked out once, here (``annotate``), and sent with
the document for the card to show.

A decision is one of:

- ``{"accept": <suggestion id>}`` — apply one of Norm's suggestions and
  record it. A create (a unit, item, brand or supplier) writes to Loaded
  first, through ``invoice_catalog`` as always.
- ``{"decline": <suggestion id>}`` — record that it was declined.
- ``{"undo": <suggestion id>}`` — put back what the decision replaced and
  record the undo (``invoice_edits`` brings back the person's edits the
  decision had retired).
- ``{"edit": {"line_id": ..., "fields": {...}}}`` or ``{"edit": {"fields":
  {...}}}`` (the header) — the person's own value, recorded as their edit;
  a value that is also Norm's pending answer for that field marks the
  suggestion accepted by hand, any other value declines it.
- ``{"create": {"kind": "unit"|"item"|"brand"|"supplier", ...}}`` — a record
  the person makes from the card's own form, then taken as their edit.

Readiness (``annotate``) is the one rule for a stop standing down: its
predicate holds, a person's own value answers it (``invoice_edits``), or
every suggestion that is its fix is accepted and still holds. There is no
"I've checked this": a stop is answered by its fix or by a person's value, or
it stands (owner, 20 Oct 2026).
"""

from __future__ import annotations

import copy
import datetime
import logging

logger = logging.getLogger(__name__)

#: Suggestions that change only the draft — applied in one step.
LOCAL_KINDS = frozenset(
    {
        "line_value",
        "header_value",
        "strike",
        "confirm",
        "supplier",
        "link_po",
        "unlink_po",
        "split_reference",
    }
)
#: What a create writes to Loaded, in the order they depend on each other
#: (invoice_review.LOADED_WRITE_ORDER): the supplier before anything that
#: hangs off it, the unit and the brand before the item made with them.
_WRITE_ORDER = ("create_supplier", "create_unit", "create_brand", "create_item")

#: The field a person's pick is, by the suggestion field it answers.
_PICK_KEYS = {
    "unit": "linked_unit_id",
    "linked_item_id": "linked_item_id",
    "linked_brand_id": "linked_brand_id",
}

#: Blockers only asking again stands down (invoice_review.RETRY_NEXT_REVIEW).
_RETRY = frozenset({"copy_not_checked", "unit_answer_missing", "item_answer_missing"})


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _suggestions(data: dict) -> dict[str, dict]:
    return {
        str(s.get("id")): s
        for s in data.get("suggestions") or []
        if isinstance(s, dict) and s.get("id")
    }


def _line(data: dict, line_id) -> dict | None:
    return next(
        (
            ln
            for ln in data.get("lines") or []
            if isinstance(ln, dict) and str(ln.get("id")) == str(line_id)
        ),
        None,
    )


def _line_of(sug: dict, sid: str):
    """The line a suggestion is about — from the suggestion, or from its id
    (``kind:field:line`` or ``strike:line``) when the review has since
    cleared it."""
    if sug.get("line_id"):
        return sug.get("line_id")
    parts = str(sid).split(":")
    if len(parts) == 3 or (len(parts) == 2 and parts[0] == "strike"):
        return parts[-1]
    return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _desc(ln: dict | None) -> str:
    ln = ln or {}
    return str(ln.get("item_name") or ln.get("description") or ln.get("code") or "line")


def _gate_words(gate) -> str:
    return str(gate or "").replace("_", " ")


def order_key(decision: dict, data: dict) -> tuple:
    """The one order of work, for Autopilot's run and the card's "Accept
    all" alike: a delete first (nothing else matters once the draft is
    gone), then lines to add (their unit and item land on them), then the
    draft's other values, then what is made in Loaded — supplier, unit,
    brand, item — then declines, undos and edits as they came."""
    by_id = _suggestions(data)
    if "accept" in decision:
        kind = (by_id.get(str(decision["accept"])) or {}).get("kind")
        if kind == "delete_invoice":
            return (0,)
        if kind == "add_line":
            return (1,)
        if kind in _WRITE_ORDER:
            return (3, _WRITE_ORDER.index(kind))
        return (2,)
    return (4,)


def order_decisions(decisions: list[dict], data: dict) -> list[dict]:
    return sorted(
        (d for d in decisions if isinstance(d, dict)),
        key=lambda d: order_key(d, data),
    )


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------


def decide(
    data: dict,
    decision: dict,
    *,
    by: object = None,
    actor: str = "user",
    lh=None,
    db=None,
    config_db=None,
    venue_id: str | None = None,
    invoice_id: str | None = None,
) -> dict:
    """Make one decision on the invoice's working values, in place, and
    record it. Returns ``{"ok": bool, "detail": str | None, "did": str |
    None, ...}``: ``did`` says what happened in words (the batch's verdict
    lists them); a create adds ``created`` (new in Loaded, or found) and
    ``name``. Never raises for a refusal — the reason is in ``detail``.

    ``actor`` is ``"user"`` or ``"norm"`` (Autopilot); ``by`` the person's
    id, kept on their edits. ``lh``, ``db``, ``config_db``, ``venue_id`` and
    ``invoice_id`` are needed only for a create or a delete (Loaded writes).
    """
    if not isinstance(data, dict) or not isinstance(decision, dict):
        return {"ok": False, "detail": "not a decision"}
    try:
        if "accept" in decision:
            return _accept(
                data,
                str(decision["accept"]),
                actor,
                by,
                lh,
                db,
                config_db,
                venue_id,
                invoice_id,
            )
        if "decline" in decision:
            return _decline(data, str(decision["decline"]), actor, by)
        if "undo" in decision:
            return _undo(data, str(decision["undo"]), actor, by)
        if "edit" in decision:
            return _edit(data, decision["edit"], by)
        if "create" in decision:
            return _create(
                data, decision["create"], by, lh, db, config_db, venue_id, invoice_id
            )
    except _Refused as e:
        return {"ok": False, "detail": str(e)}
    except Exception as e:  # noqa: BLE001 — a decision that can't be made is refused, never half-made
        logger.warning("decision failed: %s", e, exc_info=True)
        return {"ok": False, "detail": str(e) or "could not be done"}
    return {"ok": False, "detail": "not a decision"}


class _Refused(Exception):
    pass


def _record(
    data: dict, sid: str, action: str, actor: str, by, before=None, after=None
) -> dict:
    now = _now()
    entry: dict = {
        "suggestion_id": sid,
        "action": action,
        "by": actor,
        "at": now,
        "srv_at": now,
    }
    if by and actor == "user":
        entry["user_id"] = str(by)
    if before is not None:
        entry["before"] = before
    if after is not None:
        entry["after"] = after
    data.setdefault("suggestion_actions", []).append(entry)
    return entry


def _last(data: dict, sid: str) -> dict | None:
    from app.services.invoice_review import _last_actions

    return _last_actions(data).get(sid)


def _accept(data, sid, actor, by, lh, db, config_db, venue_id, invoice_id) -> dict:
    from app.services import invoice_edits as IE
    from app.services.invoice_review import apply_suggestion

    sug = _suggestions(data).get(sid)
    if sug is None:
        raise _Refused("that suggestion is no longer on the invoice")
    if (_last(data, sid) or {}).get("action") == "accepted":
        return {"ok": True, "did": None}  # already done: nothing to do twice
    kind = sug.get("kind")
    if kind == "delete_invoice":
        if lh is None:
            raise _Refused("deleting the draft needs Loaded")
        from app.routers import invoice_fixes as IF

        payload = sug.get("payload") or {}
        IF._apply_delete_invoice(lh, payload, db)
        data["is_deleted"] = True
        data["status"] = "deleted"
        data["deleted_reason"] = payload.get("summary") or sug.get("explanation")
        data["deleted_kind"] = payload.get("reason") or "other"
        _record(data, sid, "accepted", actor, by, {}, {"deleted": True})
        return {
            "ok": True,
            "did": f"deleted the draft ({data['deleted_reason']})",
            "deleted": True,
        }
    if kind in _WRITE_ORDER:
        return _accept_create(
            data, sug, actor, by, lh, db, config_db, venue_id, invoice_id
        )
    if kind == "add_line":
        before = apply_suggestion(data, sug)
        if before is None:
            raise _Refused("nothing to add")
        _record(data, sid, "accepted", actor, by, before, {"added": True})
        IE.on_accept(data, sug, actor)
        return {"ok": True, "did": f"added '{_desc(sug.get('payload'))}'"}
    before = apply_suggestion(data, sug)
    if before is None:
        raise _Refused("that line is no longer on the invoice")
    _record(data, sid, "accepted", actor, by, before, sug.get("apply") or {})
    IE.on_accept(data, sug)
    did = None
    if kind == "strike":
        did = f"struck '{_desc(_line(data, sug.get('line_id')))}' (not billed on the invoice)"
    elif kind == "confirm":
        did = (
            f"accepted ({_gate_words(sug.get('gate'))})"
            if sug.get("gate")
            else "kept it as it is"
        )
    elif sug.get("field") == "unit":
        did = f"set unit '{sug.get('proposed')}'" + (
            f" ({sug.get('gate')})" if sug.get("gate") else ""
        )
    return {"ok": True, "did": did}


def _accept_create(
    data, sug, actor, by, lh, db, config_db, venue_id, invoice_id
) -> dict:
    """A create on Norm's suggestion: the Loaded write (through
    invoice_catalog, the same functions as the card's own form), then the
    line or header takes the record, recorded as the accept."""
    from app.services import invoice_catalog as catalog
    from app.services import invoice_edits as IE

    if lh is None:
        raise _Refused("creating in Loaded needs Loaded")
    kind = sug.get("kind")
    payload = sug.get("payload") or {}
    sid = str(sug.get("id"))
    ln = _line(data, sug.get("line_id")) if sug.get("line_id") else None
    if kind != "create_supplier" and ln is None:
        raise _Refused("that line is no longer on the invoice")
    if ln is not None and ln.get("struck"):
        raise _Refused("the line is struck: nothing to make in Loaded for it")
    try:
        if kind == "create_supplier":
            name = payload.get("supplier_name") or sug.get("proposed") or ""
            supplier, new = catalog.get_or_create_supplier(
                lh, name, details=payload.get("details")
            )
            before = {
                "linked_supplier_id": data.get("linked_supplier_id"),
                "supplier_name": data.get("supplier_name"),
            }
            data["linked_supplier_id"] = supplier["id"]
            data["supplier_name"] = supplier.get("name") or name
            after = {
                "linked_supplier_id": supplier["id"],
                "supplier_name": data["supplier_name"],
                "created": new,
            }
            label = data["supplier_name"]
            verb = "created" if new else "linked existing"
            what = "supplier"
            rid = supplier["id"]
        elif kind == "create_unit":
            unit, new = catalog.get_or_create_unit(
                lh, payload.get("unit_name") or sug.get("proposed") or "", db
            )
            before = {k: ln.get(k) for k in ("linked_unit_id", "unit", "unit_ratio")}
            ln["linked_unit_id"] = unit.get("id")
            ln["unit"] = unit.get("name")
            if unit.get("ratio") is not None:
                ln["unit_ratio"] = unit.get("ratio")
            after = {
                "linked_unit_id": unit.get("id"),
                "unit": unit.get("name"),
                "unit_ratio": ln.get("unit_ratio"),
                "created": new,
            }
            label = unit.get("name")
            verb = "created" if new else "used existing"
            what = "unit"
            rid = unit.get("id")
        elif kind == "create_brand":
            brand, new = catalog.get_or_create_brand(
                lh, payload.get("brand_name") or sug.get("proposed") or ""
            )
            before = {k: ln.get(k) for k in ("linked_brand_id", "brand", "brand_set")}
            # The name with the record, and brand_set: do_receive replaces
            # Loaded's text only then.
            ln["linked_brand_id"] = brand["id"]
            ln["brand"] = brand.get("name")
            ln["brand_set"] = True
            after = {
                "linked_brand_id": brand["id"],
                "brand": brand.get("name"),
                "brand_set": True,
                "created": new,
            }
            label = brand.get("name")
            verb = "created" if new else "used existing"
            what = "brand"
            rid = brand["id"]
        else:  # create_item
            out = catalog.create_item(
                lh,
                db,
                config_db,
                venue_id=str(venue_id or ""),
                invoice_id=str(invoice_id or data.get("invoice_id") or ""),
                line_id=str(sug.get("line_id")),
                name=payload.get("name")
                or (
                    sug.get("proposed")
                    if isinstance(sug.get("proposed"), str)
                    else None
                ),
                group_id=payload.get("group_id") or "",
                unit_id=ln.get("linked_unit_id"),
                brand_id=ln.get("linked_brand_id"),
                supplier_id=data.get("linked_supplier_id"),
                doc_data=data,
            )
            new = bool(out["created"])
            before = {k: ln.get(k) for k in ("linked_item_id", "item_name")}
            ln["linked_item_id"] = out["item_id"]
            ln["item_name"] = out["item_name"]
            after = {
                "linked_item_id": out["item_id"],
                "item_name": out["item_name"],
                "created": new,
            }
            label = out["item_name"]
            verb = "created" if new else "linked existing"
            what = "stock item"
            rid = out["item_id"]
    except catalog.CatalogError as e:
        raise _Refused(e.message) from e
    _record(data, sid, "accepted", actor, by, before, after)
    IE.on_accept(data, sug)
    return {
        "ok": True,
        "did": f"{verb} {what} '{label}'",
        "created": new,
        "name": label,
        "kind": what,
        "id": rid,
    }


def _decline(data, sid, actor, by) -> dict:
    sug = _suggestions(data).get(sid)
    if sug is None:
        raise _Refused("that suggestion is no longer on the invoice")
    if (_last(data, sid) or {}).get("action") == "dismissed":
        return {"ok": True, "did": None}
    _record(data, sid, "dismissed", actor, by)
    return {"ok": True, "did": f"declined: {sug.get('explanation') or sid}"}


def _undo(data, sid, actor, by) -> dict:
    from app.services import invoice_edits as IE

    last = _last(data, sid)
    if not last or last.get("action") == "undone":
        return {"ok": True, "did": None}
    if (last.get("after") or {}).get("by_hand"):
        # The person's own pick answered it: their edit is the decision, so
        # it changes by picking again, not by undoing Norm's suggestion.
        raise _Refused("that is your own pick; choose another value to change it")
    sug = _suggestions(data).get(sid) or {}
    before = last.get("before") if isinstance(last.get("before"), dict) else None
    op: dict | None = None
    if last.get("action") == "accepted" and before is not None:
        if sug.get("kind") == "add_line" or "added_line_id" in before:
            lid = before.get("added_line_id") or sug.get("line_id")
            data["lines"] = [
                ln
                for ln in data.get("lines") or []
                if not (isinstance(ln, dict) and str(ln.get("id")) == str(lid))
            ]
            op = {"op": "remove_line", "line_id": lid}
        elif _line_of(sug, sid):
            ln = _line(data, _line_of(sug, sid))
            if ln is not None and before:
                ln.update(
                    copy.deepcopy({k: v for k, v in before.items() if k != "created"})
                )
                op = {"op": "update_line", "line_id": ln.get("id"), "fields": before}
        elif before:
            data.update(
                {k: copy.deepcopy(v) for k, v in before.items() if k != "created"}
            )
            op = {"op": "update_header", "fields": before}
    now = _now()
    _record(data, sid, "undone", actor, by)
    if op is not None:
        IE.on_undo(data, sid, op, now)
    else:
        IE.on_undo(data, sid, {"op": "update_header", "fields": {}}, now)
    return {"ok": True, "did": f"undid: {sug.get('explanation') or sid}"}


def _edit(data, edit, by) -> dict:
    """A person's own value: recorded as their edit (invoice_edits), then
    applied; a pending suggestion for that field is answered by it."""
    from app.services import invoice_edits as IE

    if not isinstance(edit, dict):
        raise _Refused("not an edit")
    fields = edit.get("fields") if isinstance(edit.get("fields"), dict) else {}
    if "struck" in edit and "struck" not in fields:
        fields = {**fields, "struck": bool(edit["struck"])}
    if "notes" in edit:
        op = {"op": "update_notes", "value": edit.get("notes") or ""}
        IE.record_edits(data, [op], by=by)
        data["notes"] = op["value"]
        return {"ok": True, "did": None}
    if not fields:
        raise _Refused("nothing to change")
    if edit.get("line_id") is not None:
        ln = _line(data, edit["line_id"])
        if ln is None:
            raise _Refused("that line is no longer on the invoice")
        fields = dict(fields)
        if "quantity_received" in fields or "unit_cost" in fields:
            q = _f(fields.get("quantity_received", ln.get("quantity_received")))
            c = _f(fields.get("unit_cost", ln.get("unit_cost")))
            if q is not None and c is not None:
                fields["total_cost"] = round(q * c, 4)
        op = {"op": "update_line", "line_id": ln.get("id"), "fields": fields}
        IE.record_edits(data, [op], by=by)
        was = {k: copy.deepcopy(ln.get(k)) for k in fields}
        ln.update(copy.deepcopy(fields))
        _answer_with(data, ln.get("id"), fields, by, was)
        return {"ok": True, "did": None}
    op = {"op": "update_header", "fields": fields}
    IE.record_edits(data, [op], by=by)
    was = {k: copy.deepcopy(data.get(k)) for k in fields}
    data.update(copy.deepcopy(fields))
    _answer_with(data, None, fields, by, was)
    return {"ok": True, "did": None}


def _answer_with(data: dict, line_id, fields: dict, by, was: dict) -> None:
    """A value a person set that is exactly what Norm proposed for that
    field (a unit, item or brand picked, a quantity or price typed, a line
    struck, a header value) answers Norm's suggestion: it reads accepted, by
    hand, and stays the person's own edit. A different value leaves Norm's
    suggestion standing beside it: "Accept all" means take every one of
    Norm's suggestions, over what the person changed (owner, 20 Oct 2026),
    and Decline is how the person turns one down. A confirm writes nothing,
    so a value never answers one: its stop says whether the value settles
    it."""
    from app.services.invoice_review import _last_actions

    last = _last_actions(data)
    for s in data.get("suggestions") or []:
        if (
            not isinstance(s, dict)
            or s.get("kind") in ("add_line", "confirm", "delete_invoice")
            or (str(s.get("line_id")) if s.get("line_id") is not None else None)
            != (str(line_id) if line_id is not None else None)
            or (last.get(str(s.get("id"))) or {}).get("action")
            in ("accepted", "dismissed")
        ):
            continue
        apply = s.get("apply") if isinstance(s.get("apply"), dict) else {}
        if s.get("kind") == "strike":
            apply = {"struck": True}
        key = _PICK_KEYS.get(str(s.get("field") or ""))
        if key is None:
            key = s.get("field") if s.get("field") in apply else None
        if key is None and len(apply) == 1:
            key = next(iter(apply))
        if key is None or key not in fields or key not in apply:
            continue
        want, got = apply.get(key), fields.get(key)
        if key == "struck":
            same = bool(want) == bool(got)
        elif want is None or got is None:
            same = want is None and got is None
        else:
            wf, gf = _f(want), _f(got)
            same = (wf is not None and gf is not None and abs(wf - gf) < 1e-9) or str(
                want
            ) == str(got)
        if same:
            _record(
                data,
                str(s["id"]),
                "accepted",
                "user",
                by,
                was,
                {**{k: fields[k] for k in fields}, "by_hand": True},
            )


def _create(data, spec, by, lh, db, config_db, venue_id, invoice_id) -> dict:
    """A record the person makes from the card's form, then taken as their
    own edit (a unit Loaded already has is used instead, never duplicated)."""
    from app.services import invoice_catalog as catalog

    if lh is None:
        raise _Refused("creating in Loaded needs Loaded")
    if not isinstance(spec, dict):
        raise _Refused("not a create")
    kind = spec.get("kind")
    name = str(spec.get("name") or "").strip()
    if not name:
        raise _Refused("a name is required")
    try:
        if kind == "supplier":
            details = catalog.draft_supplier_details(
                db, str(venue_id or ""), str(invoice_id or data.get("invoice_id") or "")
            )
            supplier, new = catalog.get_or_create_supplier(lh, name, details)
            out = _edit(
                data,
                {
                    "fields": {
                        "linked_supplier_id": supplier["id"],
                        "supplier_name": supplier.get("name") or name,
                    }
                },
                by,
            )
            return {
                **out,
                "created": new,
                "name": supplier.get("name") or name,
                "kind": "supplier",
                "id": supplier["id"],
            }
        ln = _line(data, spec.get("line_id"))
        if ln is None:
            raise _Refused("that line is no longer on the invoice")
        if kind == "unit":
            unit, new = catalog.get_or_create_unit(
                lh, name, db, ratio=spec.get("ratio"), unit_type=spec.get("unit_type")
            )
            out = _edit(
                data,
                {
                    "line_id": ln.get("id"),
                    "fields": {
                        "linked_unit_id": unit.get("id"),
                        "unit": unit.get("name"),
                        "unit_ratio": unit.get("ratio"),
                    },
                },
                by,
            )
            return {
                **out,
                "created": new,
                "name": unit.get("name"),
                "kind": "unit",
                "id": unit.get("id"),
                "ratio": unit.get("ratio"),
            }
        if kind == "brand":
            brand, new = catalog.get_or_create_brand(lh, name)
            out = _edit(
                data,
                {
                    "line_id": ln.get("id"),
                    "fields": {
                        "linked_brand_id": brand["id"],
                        "brand": brand.get("name"),
                        "brand_set": True,
                    },
                },
                by,
            )
            return {
                **out,
                "created": new,
                "name": brand.get("name"),
                "kind": "brand",
                "id": brand["id"],
            }
        if kind == "item":
            if not ln.get("linked_unit_id"):
                raise _Refused(
                    "choose the unit it comes in before creating the stock item"
                )
            res = catalog.create_item(
                lh,
                db,
                config_db,
                venue_id=str(venue_id or ""),
                invoice_id=str(invoice_id or data.get("invoice_id") or ""),
                line_id=str(ln.get("id")),
                name=name,
                group_id=str(spec.get("group_id") or ""),
                unit_id=ln.get("linked_unit_id"),
                brand_id=ln.get("linked_brand_id"),
                supplier_id=data.get("linked_supplier_id"),
                doc_data=data,
            )
            out = _edit(
                data,
                {
                    "line_id": ln.get("id"),
                    "fields": {
                        "linked_item_id": res["item_id"],
                        "item_name": res["item_name"],
                    },
                },
                by,
            )
            return {
                **out,
                "created": bool(res["created"]),
                "name": res["item_name"],
                "kind": "stock item",
                "id": res["item_id"],
            }
    except catalog.CatalogError as e:
        raise _Refused(e.message) from e
    raise _Refused(f"can't create a {kind}")


# ---------------------------------------------------------------------------
# Readiness — worked out once, here, and sent with the document
# ---------------------------------------------------------------------------


def _still_new(data: dict, s: dict) -> bool:
    """The line's field still names something Loaded has no record of: a
    declined answer for it keeps its row open until it is settled."""
    ln = _line(data, s.get("line_id")) if s.get("line_id") else None
    if ln is None or ln.get("struck"):
        return False
    field = s.get("field")
    if field == "unit":
        return not ln.get("linked_unit_id")
    if field == "linked_item_id":
        return not ln.get("linked_item_id")
    if field == "linked_brand_id":
        return bool(ln.get("brand")) and not ln.get("linked_brand_id")
    return False


def annotate(data: dict) -> dict:
    """Work out the invoice's readiness, in place, and return it:

    - ``confidence``: ``ready`` when no stop stands against Autopilot;
    - ``issue_states``: for each stop, ``{"state": "open" | "settled",
      "how": ...}`` — how it stood down (``cleared`` by its predicate,
      ``by_hand``, ``fixed`` by its accepted fix);
    - ``suggestion_states``: for each suggestion, its last decision and
      whether its row is still open;
    - ``receive``: whether Autopilot would receive it and whether a person
      can (a gated stop never greys out a person's Receive — the person is
      the authority the switch defers to; what Loaded would refuse does).
    """
    from app.services.invoice_edits import hand_fields, hand_settles
    from app.services.invoice_review import (
        RETRY_NEXT_REVIEW,
        _clears,
        _last_actions,
        is_settled,
    )

    last = _last_actions(data)
    hand = hand_fields(data)  # once: every stop asks it
    issue_states: dict[str, dict] = {}
    blocked: list[str] = []
    stopped_lines: set[str] = set()
    autopilot_blocked = False
    for i in data.get("issues") or []:
        if not isinstance(i, dict) or not i.get("id"):
            continue
        settled = is_settled(data, i, last, hand)
        how = None
        if settled:
            how = (
                "cleared"
                if _clears(data, i)
                else "by_hand"
                if hand_settles(data, i, hand)
                else "fixed"
            )
        issue_states[str(i["id"])] = {
            "state": "settled" if settled else "open",
            "how": how,
        }
        if not settled and i.get("blocking"):
            autopilot_blocked = True
            # A stop a switch would wave through, or one only asking again
            # can answer, doesn't stop a person at the card.
            if not i.get("gate") and i.get("code") not in RETRY_NEXT_REVIEW:
                blocked.append(str(i.get("message") or i.get("code")))
                if i.get("line_id"):
                    stopped_lines.add(str(i["line_id"]))
    # A confirm ("receive it as it is") is only a way to stand its stop
    # down: once every stop it answers is settled another way, there is
    # nothing left for it to decide.
    answers: dict[str, list[str]] = {}
    for i in data.get("issues") or []:
        if isinstance(i, dict) and i.get("id"):
            for f in i.get("fix") or []:
                answers.setdefault(str(f), []).append(str(i["id"]))
    suggestion_states: dict[str, dict] = {}
    for s in data.get("suggestions") or []:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        sid = str(s["id"])
        action = (last.get(sid) or {}).get("action")
        state = action if action in ("accepted", "dismissed") else "pending"
        moot = (
            state == "pending"
            and s.get("kind") == "confirm"
            and bool(answers.get(sid))
            and all(
                (issue_states.get(x) or {}).get("state") == "settled"
                for x in answers[sid]
            )
        )
        suggestion_states[sid] = {
            "state": state,
            "open": (state == "pending" and not moot)
            or (state == "dismissed" and _still_new(data, s)),
        }
    lines = [ln for ln in data.get("lines") or [] if isinstance(ln, dict)]
    live = [ln for ln in lines if not ln.get("struck")]
    # What Loaded would refuse: a line with no stock item or unit, a brand
    # Loaded has no record for, no supplier, nothing to receive.
    for ln in live:
        if str(ln.get("id")) in stopped_lines:
            continue  # its stop says it already
        needs = []
        if not ln.get("linked_item_id"):
            needs.append("a stock item")
        if not ln.get("linked_unit_id"):
            needs.append("a unit")
        if ln.get("brand") and not ln.get("linked_brand_id"):
            needs.append("its brand in Loaded")
        if needs:
            blocked.append(f"'{_desc(ln)}' needs {' and '.join(needs)}")
    if data.get("invoice_id") and not data.get("linked_supplier_id"):
        blocked.append(
            "pick the supplier first — Loaded can't receive a supplier-less invoice"
        )
    if data.get("invoice_id") and lines and not live:
        blocked.append("this draft has no line items — nothing to receive")
    if data.get("invoice_id") and not lines:
        blocked.append("this draft has no line items — nothing to receive")
    data["confidence"] = "needs_review" if autopilot_blocked else "ready"
    data["issue_states"] = issue_states
    data["suggestion_states"] = suggestion_states
    data["receive"] = {
        "ready": not autopilot_blocked,
        "person_can_receive": not blocked,
        "blocked_by": blocked,
    }
    return data["receive"]


def warn_unit_kinds(
    data: dict, catalogue: list | None, units: list | None
) -> list[dict]:
    """A unit a person picked that is a different kind (weight, volume,
    count) from what stocktakes count its product in, shown as a warning on
    the row the moment they pick it. It never greys out the person's
    Receive: they are the authority the switch defers to. Autopilot's next
    review raises the clash as a stop on the venue's switch (receive in a
    different kind of unit), so the switch decides whether Autopilot
    receives it (owner, 20 Oct 2026). In place, on ``data["warnings"]``;
    returns them."""
    from app.services.invoice_edits import hand_fields
    from app.services.invoice_review import _KIND_WORDS

    by_item = {str(i.get("id")): i for i in catalogue or [] if isinstance(i, dict)}
    by_unit = {str(u.get("id")): u for u in units or [] if isinstance(u, dict)}
    hand = hand_fields(data)
    out: list[dict] = []
    for ln in data.get("lines") or []:
        if not isinstance(ln, dict) or ln.get("struck"):
            continue
        if (str(ln.get("id")), "linked_unit_id") not in hand:
            continue
        item = by_item.get(str(ln.get("linked_item_id")))
        unit = by_unit.get(str(ln.get("linked_unit_id")))
        counting = by_unit.get(str((item or {}).get("countingUnitId")))
        got = (unit or {}).get("stockUnitType")
        want = (counting or {}).get("stockUnitType")
        if not got or not want or got == want:
            continue
        out.append(
            {
                "line_id": ln.get("id"),
                "code": "unit_kind",
                "text": (
                    f"'{unit.get('name')}' is {_KIND_WORDS.get(got, got)}, but stocktakes "
                    f"count '{_desc(ln)}' in '{counting.get('name')}' "
                    f"({_KIND_WORDS.get(want, want)})"
                ),
            }
        )
    data["warnings"] = out
    return out
