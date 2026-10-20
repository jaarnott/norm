"""What a person changed on an invoice card, kept so it outlives a rebuild.

The owner's rule (19 Oct 2026): the Norm tab shows what Loaded holds; what
gets received is what Loaded holds, plus the suggestions that were accepted,
plus anything a person typed — on the web card, the Claude card and in
Autopilot's batch alike. A card is rebuilt from Loaded whenever it is
reviewed again, saved again by a chat or batch run, or reopened — and each
rebuild used to bring back only some of that: accepted suggestions were
carried (``invoice_review.carry_forward_decisions``), but a quantity typed,
a unit picked by hand or a line struck lived only in the card's lines, and
``carry_local_state`` copied the old card's links across whoever had set them.

Two records now explain every value on a card:

- ``suggestion_actions`` — decisions on Norm's suggestions (accepted,
  dismissed, undone). Carried by ``carry_forward_decisions`` while the same
  question is asked.
- ``edits`` (this module) — a person's own changes, recorded by the PATCH
  routes (web and Claude card alike) as they arrive, each field with the value
  it replaced. Replayed, in order, on every rebuild.

How the card says which is which:

- ``suggestion_id`` on an op: the card is applying one of Norm's suggestions
  (an Accept, or a create on Norm's suggestion) — a decision, not an edit.
- ``undo_of``: an Undo, writing back what a decision replaced — not an edit
  either. It cancels the person's edits made after that decision on the same
  fields (the whole line, for an added line) and brings back an edit the
  decision had replaced, so the record shows what the card shows.
- ``answer_of``: a value the person picked by hand that happens to be Norm's
  answer too — the person's own edit, kept even if Norm's answer changes.
- untagged: a person's edit — unless the same PATCH records an accept (or an
  undo) that wrote exactly these values: then it is a card still running the
  code from before these tags.

Accepting a suggestion retires the person's earlier edits on its fields
(``superseded_by``; an Undo of that accept brings them back). A line's blocker
stands down while a person's own value for it is on the line
(``hand_settles``): the person decided it.
"""

from __future__ import annotations

import copy
import datetime
import logging

logger = logging.getLogger(__name__)

#: Header keys that are bookkeeping, never a person's value: the decision
#: record, the review itself, Loaded's mirror and the log.
_NOT_EDITS = frozenset(
    {
        "suggestion_actions",
        "actioned_suggestions",
        "edits",
        "suggestions",
        "issues",
        "replica",
        "loaded_snapshot",
        "extracted_snapshot",
        "doc_schema",
        "reviewed_at",
        "reviewed_invoice_fingerprint",
        "confidence",
        "status",
        "is_received",
        "is_deleted",
    }
)

#: Fields that are one value to a person: setting, deciding or undoing one of
#: them is the whole group. The line total is worked out from the quantity
#: and the price, so it goes with either but pulls in neither.
_FIELD_GROUPS = (
    ("unit", "linked_unit_id", "unit_ratio"),
    ("linked_item_id", "item_name"),
    ("brand", "linked_brand_id", "brand_set"),
    ("quantity_received", "total_cost"),
    ("unit_cost", "total_cost"),
    ("linked_supplier_id", "supplier_name"),
    ("linked_purchase_order_id", "purchase_order_number"),
)
_DERIVED = frozenset({"total_cost", "tax_amount"})

#: What makes a group of fields one value of the person's: the record they
#: picked (a unit, a stock item, a supplier, an order), the brand and its
#: record together, the quantity, the price. A name or a ratio that only
#: happens to match is not the person's value, and neither is a name sent
#: without its record.
_OWNED_BY = (
    (("unit", "linked_unit_id", "unit_ratio"), ("linked_unit_id",)),
    (("linked_item_id", "item_name"), ("linked_item_id",)),
    (("brand", "linked_brand_id", "brand_set"), ("brand", "linked_brand_id")),
    (("quantity_received", "total_cost"), ("quantity_received",)),
    (("unit_cost", "total_cost"), ("unit_cost",)),
    (("linked_supplier_id", "supplier_name"), ("linked_supplier_id",)),
    (
        ("linked_purchase_order_id", "purchase_order_number"),
        ("linked_purchase_order_id",),
    ),
)
_GROUPED = frozenset(f for fields, _ in _OWNED_BY for f in fields)

#: Blockers a person's own value answers, by the field that value is and what
#: it must be: a record ("record": a unit, an item, a supplier, an order — no
#: unit is no answer), a brand record or no brand at all ("brand"), or any
#: value (a line kept or struck). The supplier and the order are the
#: invoice's own; the rest are a line's. An order held by another delivery
#: (split, doubled up) is not here: taking its link can break the other
#: delivery's receive, so its own fix decides.
_HAND_SETTLES = {
    "unit_missing": ("linked_unit_id", "record"),
    "unit_unconfirmed": ("linked_unit_id", "record"),
    "unit_unknown": ("linked_unit_id", "record"),
    "unit_would_be_created": ("linked_unit_id", "record"),
    "unit_answer_missing": ("linked_unit_id", "record"),
    "unit_kind_mismatch": ("linked_unit_id", "record"),
    "item_unmatched": ("linked_item_id", "record"),
    "item_answer_missing": ("linked_item_id", "record"),
    "brand_unknown": ("linked_brand_id", "brand"),
    "loaded_line_not_on_copy": ("struck", None),
    "supplier_unresolved": ("linked_supplier_id", "record"),
    "po_unresolved": ("linked_purchase_order_id", "record"),
}

#: Suggestion kinds — an id's first part (``kind:field:line``, built in
#: invoice_review._sugg). Any other id in the decision record is a blocker's,
#: and a blocker's accept recorded the PERSON's pick, not Norm's.
_SUGGESTION_KINDS = frozenset(
    {
        "line_value",
        "header_value",
        "strike",
        "add_line",
        "create_unit",
        "create_item",
        "create_brand",
        "create_supplier",
        "supplier",
        "link_po",
        "unlink_po",
        "split_reference",
        "confirm",
        "delete_invoice",
    }
)

#: Lines Norm adds take positional ids (rep-N, totals-N): a later review can
#: give the same id to a different invoice line.
_ADDED_PREFIXES = ("rep-", "totals-")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


_NEVER = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def _ts(value: object) -> datetime.datetime:
    """A recorded time, whichever way it was written (the card writes
    '…Z', the server '…+00:00'); unknown is the distant past."""
    try:
        at = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return _NEVER
    return at if at.tzinfo else at.replace(tzinfo=datetime.timezone.utc)


def _is_decision(op: dict) -> bool:
    """An op applying or undoing one of Norm's suggestions."""
    return bool(op.get("suggestion_id") or op.get("undo_of"))


def _key(line: object) -> str | None:
    return str(line) if line else None


def _group(fields) -> set:
    """The fields with every group they belong to (a derived figure alone
    pulls in nothing)."""
    out = set(fields)
    for f in list(out):
        if f in _DERIVED:
            continue
        for g in _FIELD_GROUPS:
            if f in g:
                out |= set(g)
    return out


def _live(e: dict) -> bool:
    """An edit still in force: not undone, not replaced by a later accept."""
    return not e.get("undone_at") and not e.get("superseded_by")


def _suggestions(data: dict) -> dict[str, dict]:
    return {
        str(x.get("id")): x
        for x in data.get("suggestions") or []
        if isinstance(x, dict) and x.get("id")
    }


def _new_actions(data: dict, ops: list) -> list[dict]:
    """The decisions a PATCH adds: entries of its suggestion_actions the card
    doesn't already hold (the card sends the whole list each time)."""
    known = [e for e in data.get("suggestion_actions") or [] if isinstance(e, dict)]
    out: list[dict] = []
    for op in ops:
        if not isinstance(op, dict) or op.get("op") != "update_header":
            continue
        log = (op.get("fields") or {}).get("suggestion_actions")
        if not isinstance(log, list):
            continue
        for i, e in enumerate(log):
            if not isinstance(e, dict):
                continue
            prev = known[i] if i < len(known) else None
            if prev is None or any(
                prev.get(k) != e.get(k) for k in ("suggestion_id", "action", "at")
            ):
                out.append(e)
    return out


def _untagged_decision(data: dict, op: dict, added: list[dict]) -> str | None:
    """A card still running the code from before the tags sends its accept
    and undo ops untagged. Read the same PATCH's new decisions: an op that
    writes exactly what an accept recorded is that accept; one that writes
    back what it replaced — or removes the line an accepted add put there —
    undoes it. The line total the card works out beside a quantity or a price
    isn't part of what a suggestion records, so it isn't compared.
    Returns ``"accept"``, ``"undo:<suggestion id>"`` or None (a person's edit).
    """
    if op.get("answer_of") or _is_decision(op):
        return None
    by_id = _suggestions(data)
    prior: dict[str, dict] = {}
    for e in data.get("suggestion_actions") or []:
        if isinstance(e, dict) and e.get("action") == "accepted":
            prior[str(e.get("suggestion_id"))] = e
    kind = op.get("op")
    fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
    for e in added:
        sid = str(e.get("suggestion_id") or "")
        sug = by_id.get(sid)
        if sug is None:
            continue
        line = sug.get("line_id")
        if kind == "add_line":
            if (
                e.get("action") == "accepted"
                and sug.get("kind") == "add_line"
                and fields.get("id") == line
            ):
                return "accept"
            continue
        if kind == "remove_line":
            if (
                e.get("action") == "undone"
                and sug.get("kind") == "add_line"
                and str(op.get("line_id") or "") == str(line or "")
            ):
                return f"undo:{sid}"
            continue
        if kind == "update_line" and str(op.get("line_id") or "") != str(line or ""):
            continue
        if kind == "update_header" and line:
            continue
        if e.get("action") == "accepted":
            wrote = {**(sug.get("apply") or {}), **(e.get("after") or {})}
        elif e.get("action") == "undone":
            wrote = (prior.get(sid) or {}).get("before") or {}
        else:
            continue
        compared = {k: v for k, v in fields.items() if k in wrote or k not in _DERIVED}
        if compared and all(k in wrote and wrote[k] == v for k, v in compared.items()):
            return "accept" if e.get("action") == "accepted" else f"undo:{sid}"
    return None


def record_edits(
    data: dict, ops: list[dict], *, by: object = None, history: list | None = None
) -> int:
    """Log the person edits among ``ops`` onto ``data['edits']``. Call it
    BEFORE the ops are applied: an op addressed by index is resolved to its
    line's id against the lines as they stand, and each field keeps the value
    it replaces. ``history`` is the card's earlier PATCH ops (``pending_ops``):
    a card from before the edit record is read for a person's work, once,
    before its first new edit is added. Returns how many fields were recorded.
    """
    if not isinstance(data, dict) or not isinstance(ops, list):
        return 0
    if "edits" not in data and data.get("lines"):
        # A card from before the record: its earlier hand work goes in first,
        # or this first edit would make the card look as if it had none.
        data["edits"] = legacy_edits(data, data, history)
    lines = [ln for ln in data.get("lines") or [] if isinstance(ln, dict)]
    shadow = [dict(ln) for ln in lines]  # the lines as each op will find them
    entries: list[dict] = []
    undo: list[tuple[str, dict]] = []  # (suggestion id, op) to apply to the log
    at = _now()
    added_actions = _new_actions(data, ops)

    def _line(op: dict) -> dict | None:
        lid = op.get("line_id")
        if lid is not None:
            hit = next((ln for ln in shadow if ln.get("id") == lid), None)
            if hit is not None:
                return hit
        idx = op.get("index")
        if isinstance(idx, int) and 0 <= idx < len(shadow):
            return shadow[idx]
        return None

    for op in ops:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        if kind == "update_header" and isinstance(
            (op.get("fields") or {}).get("suggestion_actions"), list
        ):
            _stamp_arrivals(data, op["fields"]["suggestion_actions"], at)
        if kind in ("update_line", "remove_line") and op.get("line_id") is None:
            # Resolve the index now: the checks below read the line's id.
            target = _line(op)
            if target is not None:
                op = {**op, "line_id": target.get("id")}
        detected = _untagged_decision(data, op, added_actions)
        if op.get("undo_of"):
            undo.append((str(op["undo_of"]), op))
        elif detected and detected.startswith("undo:"):
            undo.append((detected[5:], op))
        person = not _is_decision(op) and detected is None
        if kind == "update_line":
            target = _line(op)
            fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
            if target is None or not fields:
                continue
            if person:
                # Which invoice line this is: an added line's id is a place
                # in Norm's reading, which a later review can give another.
                identity = {
                    "code": target.get("code"),
                    "description": target.get("description"),
                }
                for field, value in fields.items():
                    entries.append(
                        {
                            "line_id": target.get("id"),
                            "field": field,
                            "before": copy.deepcopy(target.get(field)),
                            "after": copy.deepcopy(value),
                            "line_key": identity,
                        }
                    )
            target.update(fields)
        elif kind == "add_line":
            fields = op.get("fields", op)
            new = {k: v for k, v in (fields or {}).items() if k != "op"}
            if person and new.get("id"):
                entries.append({"line_id": new["id"], "added": copy.deepcopy(new)})
            shadow.append(new)
        elif kind == "remove_line":
            target = _line(op)
            if target is None:
                continue
            if person:
                entries.append({"line_id": target.get("id"), "removed": True})
            shadow[:] = [x for x in shadow if x is not target]
        elif kind == "update_header" and person:
            fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
            for field, value in fields.items():
                if field in _NOT_EDITS:
                    continue
                entries.append(
                    {
                        "field": field,
                        "before": copy.deepcopy(data.get(field)),
                        "after": copy.deepcopy(value),
                    }
                )
        elif kind == "update_notes" and person:
            entries.append(
                {
                    "field": "notes",
                    "before": data.get("notes"),
                    "after": op.get("value", ""),
                }
            )
    # An accept retires the person's earlier edits on the fields it writes:
    # the last change to a field stands, and if Norm's answer changes by the
    # next review, a value the person had already given up doesn't return.
    # Adding a line writes the whole line: what the person had typed on that
    # invoice line before (when an earlier review added it) gives way to it.
    by_id = _suggestions(data)
    for e in added_actions:
        if e.get("action") != "accepted":
            continue
        sid = str(e.get("suggestion_id") or "")
        sug = by_id.get(sid)
        if sug is None:
            continue  # a blocker's: the person's own pick, recorded as edits
        if sug.get("kind") == "add_line":
            _supersede(data, sid, _key(sug.get("line_id")), None)
            continue
        wrote = set((sug.get("apply") or {}).keys())
        if isinstance(e.get("after"), dict):
            wrote |= set(e["after"].keys())
        if wrote:
            _supersede(data, sid, _key(sug.get("line_id")), _group(wrote))
    for sid, op in undo:
        _cancel_later_edits(data, sid, op, at)
    if not entries:
        return 0
    log = data.setdefault("edits", [])
    seq = max((int(e.get("seq") or 0) for e in log if isinstance(e, dict)), default=0)
    for e in entries:
        seq += 1
        log.append({"seq": seq, "at": at, "by": str(by) if by else None, **e})
    return len(entries)


def _supersede(data: dict, sid: str, line: str | None, fields: set | None) -> None:
    """Mark the person's live edits on these fields of the line (every field,
    when ``fields`` is None) as replaced by decision ``sid``."""
    for e in data.get("edits") or []:
        if (
            isinstance(e, dict)
            and _live(e)
            and e.get("field")
            and (fields is None or e["field"] in fields)
            and _key(e.get("line_id")) == line
        ):
            e["superseded_by"] = sid


def _cancel_later_edits(data: dict, sid: str, op: dict, at: str) -> None:
    """An undo writes back what a decision replaced (or, for an added line,
    removes it). The edits that decision retired come back — under the id it
    was made with, when a review has since carried it under another (a
    create, then the link to what it made); a person's edits to the same
    fields made after it go with it (marked, not deleted) — every edit on the
    line, for an added line taken away. So the next rebuild shows what the
    card shows."""
    decided = None
    made_as = {sid}
    for e in data.get("suggestion_actions") or []:
        if isinstance(e, dict) and str(e.get("suggestion_id")) == sid:
            if e.get("action") == "accepted":
                decided = _ts(e.get("srv_at") or e.get("at"))
                if e.get("carried_from"):
                    made_as.add(str(e["carried_from"]))
    for e in data.get("edits") or []:
        if isinstance(e, dict) and e.get("superseded_by") in made_as:
            e.pop("superseded_by", None)
    if decided is None:
        return
    kind = op.get("op")
    line = _key(op.get("line_id"))
    fields = None if kind == "remove_line" else _group((op.get("fields") or {}).keys())
    for e in data.get("edits") or []:
        if not isinstance(e, dict) or not _live(e) or not e.get("field"):
            continue
        if _key(e.get("line_id")) != (None if kind == "update_header" else line):
            continue
        if fields is not None and e["field"] not in fields:
            continue
        if _ts(e.get("at")) >= decided:
            e["undone_at"] = at


def _stamp_arrivals(data: dict, log: list, at: str) -> None:
    """Give each decision the card sends the server's time of arrival
    (``srv_at``), keeping it on entries already known. A decision and a
    person's edit are then ordered by one clock: the card stamps its entries
    with the device's own, which can be minutes out."""
    known = [e for e in data.get("suggestion_actions") or [] if isinstance(e, dict)]
    for i, e in enumerate(log):
        if not isinstance(e, dict):
            continue
        prev = known[i] if i < len(known) else None
        same = prev is not None and all(
            prev.get(k) == e.get(k) for k in ("suggestion_id", "action", "at")
        )
        if same and prev.get("srv_at"):
            e.setdefault("srv_at", prev["srv_at"])
        elif not same:
            e.setdefault("srv_at", at)


def _decided_at(data: dict) -> dict[tuple, datetime.datetime]:
    """When each field was last set by an accepted decision now in force:
    {(line_id or None, field): at}. A person edit older than that decision
    gives way to it, a newer one wins — the last change to a field stands."""
    from app.services.invoice_review import _last_actions

    by_id = _suggestions(data)
    out: dict[tuple, datetime.datetime] = {}
    for sid, action in _last_actions(data).items():
        if action.get("action") != "accepted":
            continue
        s = by_id.get(sid) or {}
        keys = set((s.get("apply") or {}).keys()) | set(
            (action.get("after") or {}).keys()
        )
        line = s.get("line_id")
        for k in keys:
            key = (_key(line), k)
            at = _ts(action.get("srv_at") or action.get("at"))
            if at > out.get(key, _NEVER):
                out[key] = at
    return out


def _same_line(e: dict, target: dict) -> bool:
    """An edit on an added line applies only while that id still names the
    invoice line it was made on."""
    identity = e.get("line_key")
    if not str(e.get("line_id") or "").startswith(_ADDED_PREFIXES):
        return True
    if not isinstance(identity, dict):
        return True
    return identity.get("code") == target.get("code") and identity.get(
        "description"
    ) == target.get("description")


def replay_edits(fresh: dict, edits: list | None) -> int:
    """Apply a card's person edits, oldest first, onto a payload rebuilt from
    Loaded (``fresh``, decisions already carried). In place; keeps the log on
    ``fresh``. Returns how many were applied.

    Addressed by line id, so a rebuild that reorders lines can't move an edit.
    An edit to a line Loaded no longer has is skipped, and so is one to an
    added line whose id now names another invoice line; an added line comes
    back once; a field a later decision set keeps the decision."""
    edits = [e for e in edits or [] if isinstance(e, dict)]
    if not isinstance(fresh, dict) or not edits:
        return 0
    edits.sort(key=lambda e: int(e.get("seq") or 0))
    decided = _decided_at(fresh)
    lines = fresh.setdefault("lines", [])
    applied = 0
    for e in edits:
        if not _live(e):
            continue  # undone, or replaced by a later accept
        lid = e.get("line_id")
        at = _ts(e.get("at"))
        if "added" in e:
            if lid and not any(
                isinstance(ln, dict) and ln.get("id") == lid for ln in lines
            ):
                lines.append(copy.deepcopy(e["added"]))
                applied += 1
            continue
        if e.get("removed"):
            before = len(lines)
            lines[:] = [
                ln for ln in lines if not (isinstance(ln, dict) and ln.get("id") == lid)
            ]
            applied += before - len(lines)
            continue
        field = e.get("field")
        if not field:
            continue
        if decided.get((_key(lid), field), _NEVER) > at:
            continue  # a decision made after this edit stands
        if lid is None:
            if field in _NOT_EDITS:
                continue
            fresh[field] = copy.deepcopy(e.get("after"))
            applied += 1
            continue
        target = next(
            (ln for ln in lines if isinstance(ln, dict) and ln.get("id") == lid),
            None,
        )
        if target is None or not _same_line(e, target):
            continue
        target[field] = copy.deepcopy(e.get("after"))
        applied += 1
    fresh["edits"] = edits
    return applied


def _line_of(sid: str) -> str | None:
    """The line a suggestion id names: ``kind:field:line`` or ``kind:line``
    (invoice_review._sugg)."""
    parts = str(sid or "").split(":", 2)
    return parts[-1] if len(parts) >= 2 and parts[-1] else None


def _blocker_id(previous: dict, sid: str) -> bool:
    """A blocker's id in the decision record, not a suggestion's."""
    return sid not in _suggestions(previous) and (
        str(sid).split(":", 1)[0] not in _SUGGESTION_KINDS
    )


def _decision_values(previous: dict) -> dict[tuple, list]:
    """{(line_id, field): [(value, at, before, suggestion id)]} that an
    accepted decision on Norm's suggestions put there — read from the
    decision record itself, so a decision whose suggestion has since been
    cleared still counts. On a card reviewed the current way a blocker's
    accept is left out: the card records the person's own pick that way (a
    unit chosen while "no unit" stood). An older review's blocker Accept
    applied Norm's value, so there it is a decision."""
    from app.services.invoice_review import DOC_SCHEMA, _last_actions

    by_id = _suggestions(previous)
    current = previous.get("doc_schema") == DOC_SCHEMA
    out: dict[tuple, list] = {}
    for sid, action in _last_actions(previous).items():
        if action.get("action") != "accepted":
            continue
        sug = by_id.get(sid)
        if current and _blocker_id(previous, sid):
            continue  # the person's own pick
        sug = sug or {}
        line = sug.get("line_id") or _line_of(sid)
        if not line:
            continue
        at = _ts(action.get("srv_at") or action.get("at"))
        before = action.get("before") if isinstance(action.get("before"), dict) else {}
        wrote = {
            **(sug.get("apply") or {}),
            **(action.get("after") if isinstance(action.get("after"), dict) else {}),
        }
        if not wrote and str(sid).startswith("strike:"):
            wrote = {"struck": True}  # an older accept that recorded no values
        for k, v in wrote.items():
            out.setdefault((str(line), k), []).append((v, at, before, sid))
    return out


#: Fields a person sets by hand on a line, in groups that travel together.
_HAND_GROUPS = (
    ("struck",),
    ("brand", "linked_brand_id", "brand_set"),
    ("linked_item_id", "item_name"),
    ("linked_unit_id", "unit", "unit_ratio"),
    ("quantity_received", "total_cost"),
    ("unit_cost", "total_cost"),
)


def _typed_from_history(previous: dict, ops: list | None) -> dict[tuple, list]:
    """{(line_id, field): [values]} the card's PATCH history shows a person
    setting. An op addressed only by its place in the table (the card has
    sent the line's id too since 7 Aug 2026) is read against the lines as
    they stood then: Loaded's lines in order, with the history's own added
    lines appended and removed lines taken out as it goes."""
    ops = [op for op in ops or [] if isinstance(op, dict)]
    added_since = {
        str((op.get("fields") or op).get("id"))
        for op in ops
        if op.get("op") == "add_line"
    }
    order = [
        str(ln.get("id"))
        for ln in previous.get("lines") or []
        if isinstance(ln, dict) and str(ln.get("id")) not in added_since
    ]
    typed: dict[tuple, list] = {}
    for op in ops:
        kind = op.get("op")
        if kind == "add_line":
            new_id = (op.get("fields") or op).get("id")
            if new_id:
                order.append(str(new_id))
            continue
        lid = op.get("line_id")
        idx = op.get("index")
        if lid is None and isinstance(idx, int) and 0 <= idx < len(order):
            lid = order[idx]
        if kind == "remove_line":
            if lid is not None and str(lid) in order:
                order.remove(str(lid))
            continue
        if kind != "update_line" or _is_decision(op) or lid is None:
            continue
        if not isinstance(op.get("fields"), dict):
            continue
        for k, v in op["fields"].items():
            typed.setdefault((str(lid), k), []).append(v)
    # The card reviewed the current way logs a value picked by hand while a
    # blocker stood as that blocker's accept, with the pick: proof too — the
    # old chat re-save emptied the history but kept the record.
    from app.services.invoice_review import DOC_SCHEMA, _last_actions

    if previous.get("doc_schema") == DOC_SCHEMA:
        issue_lines = {
            str(i.get("id")): i.get("line_id")
            for i in previous.get("issues") or []
            if isinstance(i, dict)
        }
        for sid, action in _last_actions(previous).items():
            after = action.get("after")
            if (
                action.get("action") != "accepted"
                or not isinstance(after, dict)
                or not _blocker_id(previous, sid)
            ):
                continue
            line = issue_lines.get(sid) or _line_of(sid)
            for k, v in after.items() if line else ():
                typed.setdefault((str(line), k), []).append(v)
    return typed


def _norms_value(previous: dict, line: str, lead: str, value) -> bool:
    """One of Norm's suggestions on the old card proposed exactly this value
    for the line: a strike, or a brand (linked, removed or created)."""
    for s in previous.get("suggestions") or []:
        if not isinstance(s, dict) or str(s.get("line_id")) != line:
            continue
        apply = s.get("apply") if isinstance(s.get("apply"), dict) else {}
        if lead == "struck" and value is True and s.get("kind") == "strike":
            return True
        if lead == "brand":
            if "linked_brand_id" in apply and (
                apply.get("brand"),
                apply.get("linked_brand_id"),
            ) == tuple(value):
                return True
            if s.get("kind") == "create_brand" and s.get("proposed") == value[0]:
                return True
    return False


def legacy_edits(previous: dict, fresh: dict, ops: list | None = None) -> list[dict]:
    """A card from before the edit record (19 Oct 2026) keeps a person's work
    only in its lines. Read once — on its first rebuild, or before its first
    new edit — as edits, and only what nothing else explains, so no ghost
    comes back as "typed":

    - a value Loaded holds, or Norm filled in from Loaded's screen, or an
      accepted decision on Norm's suggestions put there, is not a person's
      edit (the decision is carried, or not, on its own terms);
    - a struck line and a brand chosen or cleared are a person's;
    - a line added by hand (a ``new-`` line) is a person's;
    - an item or unit link, a quantity or a price only when the card's own
      patch history (``ops``) shows a person set it. A link nothing proves a
      person chose may be the old opening fill-in or a value copied from an
      older card, so it isn't carried: a New field then blocks the receive
      until it is settled.

    A person's value that differs from a decision on the same field came
    after it (an override, a Restore), so it is dated now and stands over the
    decision; otherwise it is dated before every decision. A value the person
    typed and then replaced by accepting Norm's answer is kept as replaced by
    that accept, so an Undo of it brings the value back.

    A line Norm added is measured against what Norm's add put there, not
    Loaded (which has no such line). Its edits keep which invoice line they
    were made on, and are read even when the rebuild hasn't added the line
    (yet): Autopilot may add it again, and they apply only to the same
    invoice line."""
    snap = {
        str(ln.get("id")): ln
        for ln in (previous.get("loaded_snapshot") or {}).get("lines") or []
        if isinstance(ln, dict)
    }
    norm_added = {
        str(s.get("line_id")): s.get("payload")
        for s in previous.get("suggestions") or []
        if isinstance(s, dict)
        and s.get("kind") == "add_line"
        and isinstance(s.get("payload"), dict)
    }
    filled = previous.get("server_filled") or {}
    decided = _decision_values(previous)
    typed = _typed_from_history(previous, ops)
    fresh_lines = {
        str(ln.get("id")): ln for ln in fresh.get("lines") or [] if isinstance(ln, dict)
    }
    fresh_ids = {ln.get("id") for ln in fresh_lines.values()}
    now = _now()
    out: list[dict] = []

    def _add(**entry) -> None:
        out.append({"seq": len(out) + 1, "by": None, **entry})

    def loaded(lid: str, field: str) -> tuple[bool, object]:
        """What the line held before anyone touched it: Loaded's value (the
        card's snapshot, else — on a rebuild — the line just rebuilt from
        Loaded), or for a line Norm added, what the add put there."""
        sl = snap.get(lid)
        if isinstance(sl, dict) and field in sl:
            return True, sl[field]
        added = norm_added.get(lid)
        if isinstance(added, dict) and field in added:
            return True, added[field]
        fl_ = fresh_lines.get(lid) if fresh is not previous else None
        if isinstance(fl_, dict) and field in fl_:
            return True, fl_[field]
        return False, None

    for old in previous.get("lines") or []:
        if not isinstance(old, dict) or not old.get("id"):
            continue
        lid = old["id"]
        key = str(lid)
        if key.startswith("new-"):
            # Always recorded: replay adds it only where it is missing, and a
            # second reading of the same old card (the batch's, then the
            # save's) must not lose it from the record.
            _add(line_id=lid, added=copy.deepcopy(old), at=None)
            continue
        norms = key.startswith(_ADDED_PREFIXES)
        if lid not in fresh_ids and not norms:
            continue
        where = (
            {
                "line_key": {
                    "code": old.get("code"),
                    "description": old.get("description"),
                }
            }
            if norms
            else {}
        )
        fl = filled.get(key) if isinstance(filled.get(key), dict) else {}
        for group in _HAND_GROUPS:
            lead = group[0]
            if lead == "struck":
                value: object = bool(old.get("struck"))
                from_loaded = value is False  # Loaded never strikes a line
                choices = decided.get((key, "struck"), [])
                explained = any(c[0] == value for c in choices)
            elif lead == "brand":
                hb, b_brand = loaded(key, "brand")
                hl, b_link = loaded(key, "linked_brand_id")
                # Chosen, then replaced by accepting Norm's brand: kept as
                # replaced by that accept, so its Undo brings the choice back.
                for _v, _at, before, sid in decided.get((key, "linked_brand_id"), []):
                    if "linked_brand_id" not in before and "brand" not in before:
                        continue
                    was = (before.get("brand"), before.get("linked_brand_id"))
                    if was == (b_brand, b_link) or (
                        was[1] not in typed.get((key, "linked_brand_id"), [])
                        and was[0] not in typed.get((key, "brand"), [])
                    ):
                        continue
                    for f, v in zip(group, (*was, True)):
                        _add(
                            line_id=lid,
                            field=f,
                            before=loaded(key, f)[1],
                            after=v,
                            at=None,
                            superseded_by=sid,
                            **where,
                        )
                if not old.get("brand_set"):
                    continue
                value = (old.get("brand"), old.get("linked_brand_id"))
                # Unknown what Loaded holds: a brand choice is kept.
                from_loaded = (hb or hl) and value == (b_brand, b_link)
                choices = decided.get((key, "linked_brand_id"), []) + decided.get(
                    (key, "brand"), []
                )
                # A brand decision is its record (or, removed, no brand at all).
                links = [c[0] for c in decided.get((key, "linked_brand_id"), [])]
                names = [c[0] for c in decided.get((key, "brand"), [])]
                explained = value[1] in links and (
                    value[1] is not None or value[0] in names
                )
            else:
                if lead not in old:
                    continue
                value = old.get(lead)
                has, base = loaded(key, lead)
                from_loaded = (has and value == base) or (
                    lead in fl and value == fl[lead]
                )
                choices = decided.get((key, lead), [])
                explained = any(c[0] == value for c in choices)
                # Typed, then replaced by accepting Norm's answer: kept as
                # replaced by that accept, so its Undo brings the value back.
                for _v, _at, before, sid in choices:
                    was = before.get(lead)
                    if (
                        lead in before
                        and was != base
                        and was in typed.get((key, lead), [])
                    ):
                        for f in group:
                            if f in before:
                                _add(
                                    line_id=lid,
                                    field=f,
                                    before=loaded(key, f)[1],
                                    after=copy.deepcopy(before[f]),
                                    at=None,
                                    superseded_by=sid,
                                    **where,
                                )
            if explained:
                continue  # what an accepted decision put there
            if from_loaded and not choices:
                continue  # Loaded's own value (or its screen's), untouched
            # A strike or a brand is the person's on its face — unless one of
            # Norm's own suggestions on that card proposed exactly it: the
            # old batch save kept Autopilot's values but threw its decisions
            # away, so then only the card's history proves a person.
            needs_proof = lead not in ("struck", "brand") or _norms_value(
                previous, key, lead, value
            )
            proof = typed.get((key, "linked_brand_id" if lead == "brand" else lead), [])
            shown = value[1] if lead == "brand" else value
            if needs_proof and shown not in proof:
                continue  # nothing shows a person chose it
            # Different from a decision on this field: the person came after
            # it (an override, a Restore), so it stands over the decision.
            at = now if choices else None
            for f in group:
                if f == "struck":
                    after = bool(old.get("struck"))
                elif f in old:
                    after = copy.deepcopy(old.get(f))
                else:
                    continue
                _add(
                    line_id=lid,
                    field=f,
                    before=loaded(key, f)[1],
                    after=after,
                    at=at,
                    **where,
                )
    return out


def rebuild_from_decisions(
    previous: dict | None, fresh: dict, *, previous_ops: list | None = None
) -> tuple[int, int]:
    """A card rebuilt from Loaded (``fresh``), with what was decided on the
    old one (``previous``): Norm's suggestions that were accepted (where the
    same question is asked again), then a person's own edits. Nothing else is
    copied across. ``previous_ops`` is the old card's PATCH history, which
    proves a person's choice on a card from before the edit record.
    Returns ``(decisions carried, edits replayed)``."""
    from app.services.invoice_review import (
        _last_actions,
        apply_suggestion,
        carry_forward_decisions,
        compute_confidence,
    )

    previous = previous if isinstance(previous, dict) else {}
    n0 = len(fresh.get("suggestion_actions") or [])
    reviewed = bool(previous.get("doc_schema") and fresh.get("doc_schema"))
    if previous.get("doc_schema") and not fresh.get("doc_schema"):
        # A plain re-read of Loaded, not a review: the old card's review and
        # its decisions still stand, on the new base. The next review asks
        # its questions again and carries what is still the same.
        for k, v in previous.items():
            if k not in fresh and k != "edits":
                fresh[k] = copy.deepcopy(v)
        by_id = _suggestions(fresh)
        accepted = [
            a for a in _last_actions(fresh).values() if a.get("action") == "accepted"
        ]
        accepted.sort(key=lambda a: _ts(a.get("srv_at") or a.get("at")))
        carried = 0
        for action in accepted:
            sug = by_id.get(str(action.get("suggestion_id")))
            if sug is None:
                continue
            if apply_suggestion(fresh, sug) is None and sug.get("line_id"):
                ln = next(
                    (
                        x
                        for x in fresh.get("lines") or []
                        if isinstance(x, dict) and x.get("id") == sug.get("line_id")
                    ),
                    None,
                )
                after = action.get("after")
                if ln is not None and isinstance(after, dict):
                    ln.update(after)
            carried += 1
    else:
        carried = carry_forward_decisions(previous, fresh)
    if "edits" in previous:
        edits = previous.get("edits")
    else:
        # Only a card from before the edit record lacks the key: every card
        # built since starts with one (received_invoice.build_received_invoice_data).
        edits = legacy_edits(previous, fresh, previous_ops)
        if previous.get("actioned_suggestions") and "actioned_suggestions" not in fresh:
            fresh["actioned_suggestions"] = copy.deepcopy(
                previous["actioned_suggestions"]
            )
    replayed = replay_edits(fresh, edits)
    # The record goes on even when empty: the key is what marks a card as
    # having one, so the old-card reading never runs on it again.
    fresh.setdefault("edits", [])
    if reviewed:
        drop_stale_blocker_accepts(fresh, n0, previous)
    try:
        from app.services.invoice_po_reference import project_po_reference

        project_po_reference(fresh)  # the order rows follow the item links
    except Exception:  # noqa: BLE001 — a projection never sinks a rebuild
        logger.info("rebuild: order projection failed", exc_info=True)
    if fresh.get("doc_schema") and fresh.get("issues") is not None:
        fresh["confidence"] = compute_confidence(fresh)
    return carried, replayed


def _value(data: dict, line: str | None, field: str) -> tuple[bool, object]:
    if line is None:
        return True, data.get(field)
    ln = next(
        (
            x
            for x in data.get("lines") or []
            if isinstance(x, dict) and str(x.get("id")) == line
        ),
        None,
    )
    return (ln is not None), (ln or {}).get(field)


def _holds(data: dict, line: str | None, field: str, value: object) -> bool:
    there, have = _value(data, line, field)
    if not there:
        return False
    if field == "struck":
        return bool(have) == bool(value)
    return have == value


def _standing(data: dict) -> dict[tuple, dict]:
    """The person's last live edit of each field: {(line_id or None, field):
    edit}, counting an edit only on the invoice line it was made on."""
    lines = {
        str(ln.get("id")): ln for ln in data.get("lines") or [] if isinstance(ln, dict)
    }
    last: dict[tuple, dict] = {}
    edits = [e for e in data.get("edits") or [] if isinstance(e, dict)]
    for e in sorted(edits, key=lambda e: int(e.get("seq") or 0)):
        if not e.get("field") or not _live(e):
            continue
        line = _key(e.get("line_id"))
        if line is not None:
            target = lines.get(line)
            if target is None or not _same_line(e, target):
                continue
        last[(line, e["field"])] = e
    return last


def hand_fields(data: dict) -> set[tuple]:
    """{(line_id or None, field)} holding a person's own value: their last
    edit still stands, on the invoice line they made it on, and the card
    still shows it. A grouped value counts by what makes it the person's
    (``_OWNED_BY``) and then covers its whole group — a unit by its record,
    never by a ratio that happens to match. Autopilot applies nothing over
    these (anything a person typed is received)."""
    held = {
        k
        for k, e in _standing(data).items()
        if _holds(data, k[0], k[1], e.get("after"))
    }
    out: set[tuple] = set()
    for fields, leads in _OWNED_BY:
        for line in {k[0] for k in held if k[1] in leads}:
            if all((line, f) in held for f in leads):
                out |= {(line, f) for f in fields if f not in _DERIVED}
    out |= {k for k in held if k[1] not in _GROUPED}
    return out


def hand_settles(data: dict, issue: dict) -> bool:
    """A blocker answered by a person's own value: a unit, a stock item, a
    supplier or an order they picked, a brand they chose or cleared, a line
    they struck or kept. However the card recorded the decision, the value is
    the person's (owner, 19 Oct 2026), so Autopilot never parks on it — as
    long as it is an answer Loaded can take (a record, or no brand at all)."""
    rule = _HAND_SETTLES.get(str((issue or {}).get("code")))
    if rule is None or not data.get("edits"):
        return False
    field, needs = rule
    line = _key((issue or {}).get("line_id"))
    there, have = _value(data, line, field)
    if not there:
        return False
    if needs == "record" and not have:
        return False
    if needs == "brand" and not have and _value(data, line, "brand")[1] is not None:
        return False  # a brand name with no record Loaded can take
    hand = hand_fields(data)
    if (
        issue.get("code") == "unit_kind_mismatch"
        and (line, "linked_item_id") not in hand
    ):
        # The mismatch is between the unit and the item: a unit picked before
        # Norm's item answer made it one is not a decision about it.
        return False
    return (line, field) in hand


def _identity(data: dict, line_id: str) -> tuple | None:
    """Which invoice line an id names in a payload — its code and
    description: the card's line, else the line the review would add."""
    for ln in data.get("lines") or []:
        if isinstance(ln, dict) and str(ln.get("id")) == line_id:
            return (ln.get("code"), ln.get("description"))
    for s in data.get("suggestions") or []:
        if (
            isinstance(s, dict)
            and s.get("kind") == "add_line"
            and str(s.get("line_id")) == line_id
        ):
            p = s.get("payload") if isinstance(s.get("payload"), dict) else {}
            return (p.get("code"), p.get("description"))
    for ln in (data.get("replica") or {}).get("lines") or []:
        if isinstance(ln, dict) and str(ln.get("id")) == line_id:
            return (ln.get("code"), ln.get("description"))
    return None


def drop_stale_blocker_accepts(
    fresh: dict, since: int = 0, previous: dict | None = None
) -> None:
    """A person's decision on a blocker carried onto a rebuilt card (from
    ``since`` on in its record) stands only while it is about the same thing:

    - on a line Norm adds, the same invoice line — positional ids can name
      another line in a later review (``previous`` is the card it came from);
    - a blocker they settled by picking a value ("accepted", with what they
      picked) only while the line still holds that value, or a later value of
      their own. Otherwise it would read settled over a value nobody chose (an
      Undo, a pick that didn't come back, a line added again without it)."""
    actions = fresh.setdefault("suggestion_actions", [])
    issues = {
        str(i.get("id")): i for i in fresh.get("issues") or [] if isinstance(i, dict)
    }
    lines = {
        str(ln.get("id")): ln for ln in fresh.get("lines") or [] if isinstance(ln, dict)
    }
    hand = hand_fields(fresh)
    keep = list(actions[:since])
    for a in list(actions[since:]):
        issue = issues.get(str(a.get("suggestion_id"))) if isinstance(a, dict) else None
        line_id = _key((issue or {}).get("line_id"))
        if issue is None or line_id is None:
            keep.append(a)
            continue
        if line_id.startswith(_ADDED_PREFIXES) and isinstance(previous, dict):
            was, now = _identity(previous, line_id), _identity(fresh, line_id)
            if was is not None and now is not None and was != now:
                continue  # decided about another invoice line under this id
        after = a.get("after")
        line = lines.get(line_id)
        if (
            a.get("action") == "accepted"
            and isinstance(after, dict)
            and after
            and line is not None
            and any(k in line and line.get(k) != v for k, v in after.items())
            and not any((line_id, f) in hand for f in _group(after.keys()))
        ):
            continue
        keep.append(a)
    # In place: Autopilot is still appending to this list.
    actions[:] = keep
    fresh["suggestion_actions"] = actions


def decided_fields(data: dict) -> set[tuple]:
    """{(line_id or None, field)} holding a decision or a person edit — the
    fields a reopen must not overwrite from Loaded."""
    out = set(_decided_at(data)) | hand_fields(data)
    for ln in data.get("lines") or []:
        if isinstance(ln, dict) and ln.get("brand_set"):
            for f in ("brand", "linked_brand_id"):
                out.add((str(ln.get("id")), f))
    return out
