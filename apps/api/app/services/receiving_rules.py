"""An organisation's receiving rules: how Norm fills in what an invoice leaves
to the venue.

Asked for by The Lott (22 Oct 2026): "if there's no due date on an invoice, I
always select Sunday — then the Xero export needs nothing entered". The owner's
decisions, the same day:

- a manager sets them (``receiving:rules``: owners and managers);
- they sit with the organisation, and a venue can override one;
- a rule wins over what the invoice says.

A rule is one of a small, growing list of types (``KINDS``), each with its own
settings, checks and plain-words description — never free text an AI reads on
every invoice. The review applies the venue's rules as suggestions marked as
its rule (``apply``), so the card shows them and Autopilot accepts them like
any other answer it is sure of, recorded and undoable: one path for both.

Plan: docs/plans/receiving-rules.md.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import logging
import re

logger = logging.getLogger(__name__)

PERMISSION = "receiving:rules"

WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

#: How a due date is worked out from the invoice's date.
DUE_BASES = {
    "invoice_date": "the invoice date",
    "weekday_on_or_after": "the {weekday} on or after the invoice date",
    "weekday_after": "the first {weekday} after the invoice date",
    "days_after": "{days} days after the invoice date",
    "day_of_next_month": "the {day_th} of the month after the invoice",
}

#: The same choices, as a form offers them.
DUE_BASIS_LABELS = {
    "invoice_date": "The invoice date",
    "weekday_on_or_after": "A day of the week, on or after the invoice date",
    "weekday_after": "A day of the week, always after the invoice date",
    "days_after": "A number of days after the invoice date",
    "day_of_next_month": "A day of the month after the invoice",
}

#: Which invoices a rule applies to. Where it applies it wins over the invoice
#: (owner, 22 Oct 2026); "no_due_date" is The Lott's "if there's no due date
#: on an invoice, I always select Sunday" — an invoice printing its own keeps it.
DUE_WHEN = {
    "always": "on every invoice",
    "no_due_date": "when the invoice has no due date",
}
DUE_WHEN_LABELS = {
    "always": "Every invoice (even one that prints its own due date)",
    "no_due_date": "Only invoices that print no due date",
}

#: The rule types: what each fills in, and the settings it takes.
KINDS = {
    "due_date": {
        "label": "Due date",
        "what": "the invoice's due date",
        "bases": DUE_BASES,
    },
    # The Lott counts herbs, shallots and kale by the bunch, and its recipes
    # use bunches; Norm booking them by weight multiplied their recipe costs
    # (Coriander Prepped 25.88 → 322.35, 20 Oct 2026). Everywhere else a bunch
    # is a weight (owner, 23 Oct 2026); this rule is the venue's exception.
    "bunch_unit": {
        "label": "Bunches",
        "what": "the unit anything sold by the bunch is booked in",
    },
}

#: A line the invoice sells by the bunch: 'CORIANDER bunch', 'HERB BASIL BCH',
#: or 'bunch' in its unit column. A market bunch ('5-6 M/B', 'Market') is
#: several bunches, so it is not one.
_BUNCH = re.compile(r"\b(bunch(?:es)?|bch|bnch)\b", re.IGNORECASE)
_SEVERAL = re.compile(r"\bm/b\b|\bmarket\b", re.IGNORECASE)

#: The blockers a venue's bunch rule answers: the unit is the venue's choice.
_UNIT_ISSUE_CODES = frozenset(
    {
        "unit_missing",
        "unit_unconfirmed",
        "unit_unknown",
        "unit_would_be_created",
        "unit_answer_missing",
    }
)


class RuleError(ValueError):
    """A rule that can't be saved as given; the message says why."""


def _ordinal(n: int) -> str:
    suffix = (
        "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    )
    return f"{n}{suffix}"


def clean(kind: str, params: dict | None) -> dict:
    """The rule's settings, checked and reduced to what its type uses."""
    if kind not in KINDS:
        raise RuleError(
            f"there's no rule for {kind!r}; the rules are: {', '.join(KINDS)}"
        )
    params = dict(params or {})
    if kind == "bunch_unit":
        unit = " ".join(str(params.get("unit") or "").split())
        if not unit or len(unit) > 40:
            raise RuleError(
                "name the venue's unit a bunch is booked in, as Loaded spells it "
                "(e.g. 'Bunch')"
            )
        return {"unit": unit}
    basis = params.get("basis")
    if basis not in DUE_BASES:
        raise RuleError("the due date is worked out as one of: " + ", ".join(DUE_BASES))
    when = params.get("when") or "always"
    if when not in DUE_WHEN:
        raise RuleError("the rule applies to: " + ", ".join(DUE_WHEN))
    out: dict = {"basis": basis, "when": when}
    if basis in ("weekday_on_or_after", "weekday_after"):
        weekday = params.get("weekday")
        if isinstance(weekday, str):
            names = [w.lower() for w in WEEKDAYS]
            weekday = names.index(weekday.lower()) if weekday.lower() in names else None
        if not isinstance(weekday, int) or not 0 <= weekday <= 6:
            raise RuleError("choose the day of the week (Monday to Sunday)")
        out["weekday"] = weekday
    elif basis == "days_after":
        days = params.get("days")
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = None
        if days is None or not 0 <= days <= 120:
            raise RuleError("the number of days is between 0 and 120")
        out["days"] = days
    elif basis == "day_of_next_month":
        day = params.get("day")
        try:
            day = int(day)
        except (TypeError, ValueError):
            day = None
        if day is None or not 1 <= day <= 31:
            raise RuleError("the day of the month is between 1 and 31")
        out["day"] = day
    return out


def date_words(kind: str, params: dict) -> str:
    """How the date is worked out: 'the Sunday on or after the invoice date'."""
    basis = (params or {}).get("basis")
    template = DUE_BASES.get(basis, "the invoice date")
    return template.format(
        weekday=WEEKDAYS[params.get("weekday", 6) % 7] if "weekday" in params else "",
        days=params.get("days"),
        day_th=_ordinal(int(params.get("day") or 1)),
    )


def describe(kind: str, params: dict) -> str:
    """The rule in plain words, with which invoices it covers: 'the Sunday on
    or after the invoice date, when the invoice has no due date'."""
    if kind == "bunch_unit":
        return f"anything sold by the bunch is booked as one '{(params or {}).get('unit')}'"
    when = (params or {}).get("when") or "always"
    return f"{date_words(kind, params)}, {DUE_WHEN.get(when, DUE_WHEN['always'])}"


def due_date_for(issued: _dt.date, params: dict) -> _dt.date:
    """The due date the rule gives an invoice dated ``issued``."""
    basis = params.get("basis")
    if basis in ("weekday_on_or_after", "weekday_after"):
        ahead = (int(params["weekday"]) - issued.weekday()) % 7
        if basis == "weekday_after" and ahead == 0:
            ahead = 7
        return issued + _dt.timedelta(days=ahead)
    if basis == "days_after":
        return issued + _dt.timedelta(days=int(params["days"]))
    if basis == "day_of_next_month":
        year, month = (
            (issued.year + 1, 1)
            if issued.month == 12
            else (issued.year, issued.month + 1)
        )
        last = calendar.monthrange(year, month)[1]
        return _dt.date(year, month, min(int(params["day"]), last))
    return issued


def examples(kind: str, params: dict, today: _dt.date | None = None) -> list[dict]:
    """What the rule does to invoices dated across one week, from Monday —
    shown before it is saved, so 'which Sunday?' is settled by looking."""
    if kind != "due_date":
        return []
    today = today or _dt.date.today()
    monday = today - _dt.timedelta(days=today.weekday())
    out = []
    for offset in (0, 2, 5, 6):  # Monday, Wednesday, Saturday, Sunday
        issued = monday + _dt.timedelta(days=offset)
        due = due_date_for(issued, params)
        out.append(
            {
                "invoice": issued.isoformat(),
                "due": due.isoformat(),
                "words": f"{issued:%a %-d %b} → due {due:%a %-d %b}",
            }
        )
    if params.get("when") == "no_due_date":
        out.append(
            {
                "invoice": None,
                "due": None,
                "words": "an invoice that prints its own due date keeps it",
            }
        )
    return out


# ---------------------------------------------------------------------------
# Who may set them, and where they are
# ---------------------------------------------------------------------------


def can_manage(db, user, org_id: str | None) -> bool:
    """An owner or a manager of the organisation (anyone holding
    ``receiving:rules``), or a platform admin."""
    if user is None:
        return False
    if getattr(user, "role", None) == "admin":
        return True
    from app.db.models import Role
    from app.services import active_org

    membership = active_org.membership_for(db, user.id, org_id)
    if not membership or not membership.role_id:
        return False
    role = db.query(Role).filter(Role.id == membership.role_id).first()
    return bool(role) and PERMISSION in (role.permissions or [])


def _venue(db, venue_id: str | None):
    from app.db.models import Venue

    return db.query(Venue).filter(Venue.id == venue_id).first() if venue_id else None


def serialize(row, venue_name: str | None = None) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "label": KINDS.get(row.kind, {}).get("label", row.kind),
        "params": row.params or {},
        "description": describe(row.kind, row.params or {}),
        "scope": "venue" if row.venue_id else "organisation",
        "venue_id": row.venue_id,
        "venue_name": venue_name,
        "note": row.note,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def effective(db, venue_id: str) -> dict:
    """``{kind: rule row}`` in force at a venue: its own, else its
    organisation's."""
    from app.db.models import ReceivingRule

    venue = _venue(db, venue_id)
    if venue is None or not venue.organization_id:
        return {}
    rows = (
        db.query(ReceivingRule)
        .filter(ReceivingRule.organization_id == venue.organization_id)
        .filter(
            (ReceivingRule.venue_id.is_(None)) | (ReceivingRule.venue_id == venue_id)
        )
        .all()
    )
    out: dict = {}
    for row in sorted(rows, key=lambda r: r.venue_id is not None):
        out[row.kind] = row  # the venue's own, sorted last, wins
    return out


def set_rule(
    db,
    *,
    org_id: str,
    venue_id: str | None,
    kind: str,
    params: dict,
    note: str | None,
    user,
):
    """Save the organisation's rule of this kind, or a venue's override."""
    from app.db.models import ReceivingRule

    if venue_id:
        venue = _venue(db, venue_id)
        if venue is None or venue.organization_id != org_id:
            raise RuleError("that venue isn't in this organisation")
    cleaned = clean(kind, params)
    q = db.query(ReceivingRule).filter(
        ReceivingRule.organization_id == org_id, ReceivingRule.kind == kind
    )
    q = (
        q.filter(ReceivingRule.venue_id == venue_id)
        if venue_id
        else q.filter(ReceivingRule.venue_id.is_(None))
    )
    row = q.first()
    if row is None:
        row = ReceivingRule(
            organization_id=org_id,
            venue_id=venue_id,
            kind=kind,
            created_by=getattr(user, "id", None),
        )
        db.add(row)
    row.params = cleaned
    row.note = (note or "").strip() or row.note
    row.updated_by = getattr(user, "id", None)
    row.updated_at = _dt.datetime.now(_dt.timezone.utc)
    db.flush()
    return row


def remove_rule(db, *, org_id: str, venue_id: str | None, kind: str) -> bool:
    from app.db.models import ReceivingRule

    q = db.query(ReceivingRule).filter(
        ReceivingRule.organization_id == org_id, ReceivingRule.kind == kind
    )
    q = (
        q.filter(ReceivingRule.venue_id == venue_id)
        if venue_id
        else q.filter(ReceivingRule.venue_id.is_(None))
    )
    n = q.delete(synchronize_session=False)
    db.flush()
    return bool(n)


def view(db, user, org_id: str) -> dict:
    """Everything the Settings section shows: the rule types, the
    organisation's rules, and per venue its overrides and what is in force."""
    from app.db.models import ReceivingRule, Venue

    venues = (
        db.query(Venue)
        .filter(Venue.organization_id == org_id)
        .order_by(Venue.name)
        .all()
    )
    names = {v.id: v.name for v in venues}
    rows = db.query(ReceivingRule).filter(ReceivingRule.organization_id == org_id).all()
    org_rules = [serialize(r) for r in rows if not r.venue_id]
    by_venue: dict[str, list] = {}
    for r in rows:
        if r.venue_id:
            by_venue.setdefault(r.venue_id, []).append(
                serialize(r, names.get(r.venue_id))
            )
    org_by_kind = {r["kind"]: r for r in org_rules}
    venue_views = []
    for v in venues:
        own = {r["kind"]: r for r in by_venue.get(v.id, [])}
        in_force = {**org_by_kind, **own}
        venue_views.append(
            {
                "id": v.id,
                "name": v.name,
                "rules": list(own.values()),
                "in_force": list(in_force.values()),
            }
        )
    return {
        "kinds": [
            {
                "kind": k,
                "label": d["label"],
                "what": d["what"],
                "bases": [
                    {"id": b, "words": w, "label": DUE_BASIS_LABELS.get(b, b)}
                    for b, w in d.get("bases", {}).items()
                ],
                "whens": [
                    {"id": w, "words": words, "label": DUE_WHEN_LABELS[w]}
                    for w, words in DUE_WHEN.items()
                ]
                if k == "due_date"
                else [],
            }
            for k, d in KINDS.items()
        ],
        "weekdays": WEEKDAYS,
        "organisation": org_rules,
        "venues": venue_views,
        "can_edit": can_manage(db, user, org_id),
    }


# ---------------------------------------------------------------------------
# In the review
# ---------------------------------------------------------------------------


def _date_part(value: object) -> str:
    return str(value or "")[:10]


def _as_date(value: object) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(_date_part(value))
    except ValueError:
        return None


def _whose(db, rule) -> str:
    venue = _venue(db, rule.venue_id) if rule.venue_id else None
    if venue is None:
        return "Your organisation's rule"
    name = venue.name or "This venue"
    return f"{name}' rule" if name.endswith("s") else f"{name}'s rule"


def sold_by_the_bunch(*texts: object) -> bool:
    """Does the invoice sell this line by the bunch (its printed name or its
    unit column)? A market bunch is several bunches, never one."""
    words = " ".join(str(t or "") for t in texts)
    return bool(_BUNCH.search(words)) and not _SEVERAL.search(words)


def bunch_unit(units: list | None, name: object) -> dict | None:
    """The venue's own unit the rule names. Norm hides bundling units such as
    'Bunch' from its own choices (invoice_units.hide_non_measurement_units),
    so a unit only Norm hid counts; one Loaded deleted doesn't."""
    want = " ".join(str(name or "").lower().split())
    for u in units or []:
        if not isinstance(u, dict):
            continue
        if u.get("datestampDeleted") and not u.get("hiddenByNorm"):
            continue
        if " ".join(str(u.get("name") or "").lower().split()) == want:
            return u
    return None


def bunch_unit_at(db, venue_id: str | None, units: list | None) -> dict | None:
    """The unit a venue's bunch rule books bunches in, or None (no rule, or the
    venue has no such unit)."""
    if db is None or not venue_id:
        return None
    try:
        rule = effective(db, venue_id).get("bunch_unit")
    except Exception:  # noqa: BLE001
        logger.info("receiving rules unavailable for %s", venue_id, exc_info=True)
        return None
    return bunch_unit(units, (rule.params or {}).get("unit")) if rule else None


def _apply_bunch_unit(
    db, rule, data: dict, replica: dict | None, suggestions: list, issues, units, pairs
) -> None:
    """Every line sold by the bunch takes the venue's bunch unit: Norm's own
    unit answer and the unit blockers for that line stand down."""
    unit = bunch_unit(units, (rule.params or {}).get("unit"))
    if unit is None:
        logger.info(
            "bunch rule: no unit %r at venue %s",
            (rule.params or {}).get("unit"),
            rule.venue_id,
        )
        return
    # What the invoice prints for each working line: Norm's reading of the
    # copy (its name and unit column), through the review's line pairs.
    read = (data.get("extracted_snapshot") or {}).get("lines") or []
    rep_lines = {
        str(ln.get("id")): ln
        for ln in (replica or {}).get("lines") or []
        if isinstance(ln, dict)
    }
    printed: dict[str, tuple[str, list]] = {}
    for rid, wid in (pairs or {}).items():
        texts = [(rep_lines.get(str(rid)) or {}).get("description")]
        m = re.fullmatch(r"rep-(\d+)", str(rid))
        if (
            m
            and int(m.group(1)) < len(read)
            and isinstance(read[int(m.group(1))], dict)
        ):
            el = read[int(m.group(1))]
            texts += [el.get("description"), el.get("unit")]
        printed[str(wid)] = (str(rid), texts)
    whose = _whose(db, rule)
    for line in data.get("lines") or []:
        if not isinstance(line, dict) or line.get("struck"):
            continue
        wid = str(line.get("id"))
        rid, texts = printed.get(wid, (None, [line.get("description")]))
        if not sold_by_the_bunch(*texts):
            continue
        ids = {wid, rid}
        suggestions[:] = [
            s
            for s in suggestions
            if not (str(s.get("line_id")) in ids and s.get("field") == "unit")
        ]
        if issues is not None:
            issues[:] = [
                i
                for i in issues
                if not (
                    str(i.get("line_id")) in ids and i.get("code") in _UNIT_ISSUE_CODES
                )
            ]
        if str(line.get("linked_unit_id")) == str(unit.get("id")):
            continue
        suggestions.append(
            {
                "id": f"rule:bunch_unit:{wid}",
                "kind": "line_value",
                "field": "unit",
                "line_id": line.get("id"),
                "current": line.get("unit"),
                "proposed": unit.get("name"),
                "explanation": (
                    f"{whose}: anything sold by the bunch is booked as one "
                    f"'{unit.get('name')}'"
                ),
                "sure": True,
                "apply": {
                    "linked_unit_id": unit.get("id"),
                    "unit": unit.get("name"),
                    "unit_ratio": unit.get("ratio"),
                },
                "rule": {
                    "kind": "bunch_unit",
                    "id": rule.id,
                    "scope": "venue" if rule.venue_id else "organisation",
                },
            }
        )


def apply(
    db,
    venue_id: str | None,
    data: dict,
    replica: dict | None,
    suggestions: list,
    *,
    issues: list | None = None,
    units: list | None = None,
    pairs: dict | None = None,
) -> None:
    """The venue's rules, as suggestions on the invoice: what the rule gives,
    where the draft differs. A rule wins over what the invoice says (owner,
    22 Oct 2026). Best-effort: a rule that can't be read never fails a review.
    ``units`` (the venue's) and ``pairs`` (Norm's line → working line) let a
    bunch rule find the lines the invoice sells by the bunch."""
    if db is None or not venue_id:
        return
    try:
        rules = effective(db, venue_id)
    except Exception:  # noqa: BLE001 — the review stands on its own
        logger.info("receiving rules unavailable for %s", venue_id, exc_info=True)
        return
    if "bunch_unit" in rules:
        try:
            _apply_bunch_unit(
                db,
                rules["bunch_unit"],
                data,
                replica,
                suggestions,
                issues,
                units,
                pairs,
            )
        except Exception:  # noqa: BLE001
            logger.info("bunch rule failed at %s", venue_id, exc_info=True)
    rule = rules.get("due_date")
    if rule is None:
        return
    # The invoice's own date: Norm's reading of the copy, else Loaded's draft
    # (a corrected date moves the due date with it).
    issued = _as_date((replica or {}).get("issued_at")) or _as_date(
        data.get("issued_at")
    )
    if issued is None:
        return
    params = rule.params or {}
    rep = replica or {}
    if params.get("when") == "no_due_date":
        # Only an invoice that prints no due date. Norm must have read the
        # copy for one (an old read never looked): unsure means untouched.
        if not rep.get("due_date_read") or rep.get("due_at"):
            return
    due = due_date_for(issued, params)
    # Where a rule applies it wins (owner): the printed due date the review
    # would otherwise suggest gives way, even when the draft already matches.
    suggestions[:] = [
        s
        for s in suggestions
        if not (s.get("field") == "due_at" and s.get("line_id") is None)
    ]
    current = _date_part(data.get("due_at")) or None
    if current == due.isoformat():
        return
    whose = _whose(db, rule)
    suggestions.append(
        {
            "id": "rule:due_date",
            "kind": "header_value",
            "field": "due_at",
            "line_id": None,
            "current": current,
            "proposed": due.isoformat(),
            "explanation": (
                f"{whose}: "
                + (
                    "the invoice prints no due date, so it is "
                    if params.get("when") == "no_due_date"
                    else "the due date is "
                )
                + f"{date_words('due_date', params)} — {due:%a %-d %b %Y}"
            ),
            "sure": True,
            "apply": {"due_at": due.isoformat()},
            "rule": {
                "kind": "due_date",
                "id": rule.id,
                "scope": "venue" if rule.venue_id else "organisation",
            },
        }
    )
