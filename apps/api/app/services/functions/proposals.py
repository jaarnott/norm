"""Proposals — the free HTML + CSS document behind a quote.

One proposal per enquiry. It starts from the venue's brand (its template, or
Norm's default layout filled from the brand's identity, the enquiry, its
options and its menus) and is then edited freely — by a person in the editor
or by the agent. Two rules keep free HTML safe and honest:

* **Sanitised on every save** with F5's ``sanitize.sanitize_proposal`` (no
  script, handlers, forms or remote requests; images only from Norm's own
  files), and rendered in a sandboxed iframe (``sanitize.proposal_document``).
* **The numbers stay structured.** What a customer can confirm is rendered by
  Norm from the option records below the HTML, never from the HTML; and
  ``check_consistency`` warns when a dollar figure in the HTML matches nothing
  on record — Orbit's ``priceConsistencyWarnings``, applied to the document
  itself rather than only an email's plain text.

A send freezes the HTML, CSS and the options offered as a version
(``freeze_version``); the outbox marks it sent (``mark_version_sent``) — the
send itself is the mailbox's (F4).
"""

from __future__ import annotations

import bisect
import datetime as dt
import html as html_lib
import re

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.functions_models import (
    FnContact,
    FnEnquiry,
    FnMinSpend,
    FnProposal,
    FnProposalEvent,
    FnProposalVersion,
    FnVenueArea,
    FnVenueBrand,
)
from app.db.models import Venue, _now
from app.services.functions import enquiries as enquiries_svc
from app.services.functions import menus as menus_svc
from app.services.functions import min_spends as min_spends_svc
from app.services.functions import money as money_svc
from app.services.functions._rows import check_version, load
from app.services.functions.company import format_money, get_company_settings
from app.services.functions.ctx import (
    FunctionsCtx,
    require_live,
    require_staff,
    require_venue,
)
from app.services.functions.errors import Invalid, NotFound, flush
from app.services.functions.events import BOOKING_LABELS
from app.services.functions.sanitize import MAX_DEPTH as SANITIZER_MAX_DEPTH
from app.services.functions.timeutil import local_today

#: Figures under this are per-person prices, corkage, deposits per head —
#: never what a check against the recorded options should judge (Orbit's
#: threshold, kept).
CHECK_MIN_CENTS = 50_000
#: A figure "matches" a recorded one to the dollar (a quote rounds cents
#: away). Orbit allowed 5%, which let a $5,750 typo pass for $5,500.
CHECK_TOLERANCE_CENTS = 100
#: A dollar figure. It starts at the "$" (so "NZ$5,500" matches too) — a
#: literal first character lets the engine skip ahead, keeping the scan linear.
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(?!\d)(?!,\d)")
#: Size caps checked before anything parses the HTML or CSS. The sanitiser
#: and the quote page re-render stored HTML; these keep that work bounded
#: (a long proposal is a few tens of KB).
MAX_HTML_CHARS = 500_000
MAX_CSS_CHARS = 150_000
#: Nesting deeper than this is refused before sanitising: html5ever/nh3 cost
#: grows with the square of the depth (50k nested <div>s: 34 s), and real
#: proposals nest well under 30. The sanitiser's own limit, so the two checks
#: never disagree — this one just says which field it was.
MAX_DEPTH = SANITIZER_MAX_DEPTH
#: Stored rich text (a reply template's body, a venue's policies, a
#: description) and one-line fields (names, labels, subjects).
MAX_TEXT_CHARS = 100_000
MAX_LINE_CHARS = 1_000

# The scanner below. Comments and <script>/<style> are found with one
# forward search; ordinary tags with possessive quantifiers, which cannot
# backtrack and stop at the next "<", so every pattern is linear.
_SPECIAL = re.compile(r"<(?:!--|(script|style)\b)", re.IGNORECASE)
_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9:-]*+)[^<>]*+>")
_DECLARATION = re.compile(r"<[!?][^<>]*+>")
_RAW_END = {
    "script": re.compile(r"</script", re.IGNORECASE),
    "style": re.compile(r"</style", re.IGNORECASE),
}
#: Elements that never contain anything, and elements the parser closes on
#: its own (a new <li> ends the last one) — neither deepens the tree.
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_AUTO_CLOSED = frozenset(
    {
        "p",
        "li",
        "dt",
        "dd",
        "tr",
        "td",
        "th",
        "option",
        "optgroup",
        "thead",
        "tbody",
        "tfoot",
        "colgroup",
        "rb",
        "rt",
        "rtc",
        "rp",
    }
)
#: Placeholders a brand's own template may use (values are escaped; the
#: block tokens are rendered by Norm as HTML).
TEXT_TOKENS = (
    "venue_name",
    "client_name",
    "client_company",
    "event_name",
    "event_date",
    "guests",
    "booking_type",
    "valid_days",
    "valid_until",
    "tax_label",
)
BLOCK_TOKENS = ("logo", "options", "menus", "spaces", "inclusions", "terms", "signoff")


def cap_text(value, what: str, limit: int = MAX_TEXT_CHARS):
    """``value`` back, or ``Invalid`` when it is longer than ``limit`` — for
    every free-text field these services store."""
    if value is not None and len(str(value)) > limit:
        raise Invalid(
            f"{what} is too long ({len(str(value)):,} characters) — keep it under "
            f"{limit:,}."
        )
    return value


def _guard(html: str, what: str) -> None:
    cap_text(html, f"{what} HTML", MAX_HTML_CHARS)
    if nesting_depth(html) > MAX_DEPTH:
        raise Invalid(
            f"{what} HTML is nested more than {MAX_DEPTH} levels deep — simplify "
            "its structure."
        )


def sanitise_fragment(html: str | None, what: str, limit: int = MAX_TEXT_CHARS) -> str:
    """Stored rich text that Norm later renders or sends (a reply template's
    body): capped, depth-guarded, then through F5's allowlist — no script,
    event handlers, forms or iframes; links http(s)/mailto/tel only; images
    only Norm's own files. <style> blocks are dropped (an email body keeps
    inline styles only)."""
    from app.services.functions.sanitize import sanitize_html

    text = cap_text(html or "", what, limit)
    _guard(text, what)
    clean, _style_blocks = sanitize_html(text)
    return clean


def sanitise(
    html: str | None, css: str | None, *, what: str = "The proposal"
) -> tuple[str, str]:
    """F5's sanitiser — the one place proposal (and brand template) HTML is
    cleaned before it is stored — behind the size caps and the nesting
    guard, so no input can make the save (or a later render) slow."""
    from app.services.functions.sanitize import sanitize_proposal

    html, css = html or "", css or ""
    cap_text(css, f"{what} CSS", MAX_CSS_CHARS)
    _guard(html, what)
    return sanitize_proposal(html, css)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def get_proposal(db: Session, ctx: FunctionsCtx, enquiry_id: str) -> FnProposal | None:
    enquiries_svc.get_enquiry(db, ctx, enquiry_id)
    return (
        db.query(FnProposal)
        .filter(
            FnProposal.organization_id == ctx.org_id,
            FnProposal.enquiry_id == enquiry_id,
        )
        .first()
    )


def versions(
    db: Session, ctx: FunctionsCtx, enquiry_id: str
) -> list[FnProposalVersion]:
    enquiries_svc.get_enquiry(db, ctx, enquiry_id)
    return (
        db.query(FnProposalVersion)
        .filter(
            FnProposalVersion.organization_id == ctx.org_id,
            FnProposalVersion.enquiry_id == enquiry_id,
        )
        .order_by(FnProposalVersion.version_number.desc())
        .all()
    )


def get_version(db: Session, ctx: FunctionsCtx, version_id: str) -> FnProposalVersion:
    row = load(
        db,
        ctx,
        FnProposalVersion,
        version_id,
        label="Proposal version",
        venue_attr=None,
    )
    enquiries_svc.get_enquiry(db, ctx, row.enquiry_id)  # venue visibility
    return row


def summary(db: Session, ctx: FunctionsCtx, enquiry_id: str) -> dict:
    """The proposal at a glance for the enquiry page: whether one exists, its
    version, the frozen versions, the last send and view, and whether the
    brand has changed since it was started."""
    proposal = get_proposal(db, ctx, enquiry_id)
    events = (
        db.query(FnProposalEvent.kind, func.max(FnProposalEvent.occurred_at))
        .filter(
            FnProposalEvent.organization_id == ctx.org_id,
            FnProposalEvent.enquiry_id == enquiry_id,
        )
        .group_by(FnProposalEvent.kind)
        .all()
    )
    last = dict(events)
    count = (
        db.query(func.count(FnProposalVersion.id))
        .filter(
            FnProposalVersion.organization_id == ctx.org_id,
            FnProposalVersion.enquiry_id == enquiry_id,
        )
        .scalar()
    )
    out = {
        "exists": proposal is not None,
        "id": proposal.id if proposal else None,
        "version": proposal.version if proposal else None,
        "updated_at": proposal.updated_at.isoformat() if proposal else None,
        "versions": int(count or 0),
        "last_sent_at": last["sent"].isoformat() if last.get("sent") else None,
        "last_viewed_at": last["viewed"].isoformat() if last.get("viewed") else None,
        "based_on_brand_version": proposal.based_on_brand_version if proposal else None,
        "brand_changed": False,
    }
    if proposal is not None and proposal.based_on_brand_version is not None:
        brand = _brand_for(db, ctx, _enquiry(db, ctx, enquiry_id))
        out["brand_changed"] = (
            brand is not None and brand.version != proposal.based_on_brand_version
        )
    return out


def _enquiry(db: Session, ctx: FunctionsCtx, enquiry_id: str) -> FnEnquiry:
    return enquiries_svc.get_enquiry(db, ctx, enquiry_id)


# ---------------------------------------------------------------------------
# Options as offered (what a version freezes, what the check compares)
# ---------------------------------------------------------------------------


def option_snapshot(db: Session, ctx: FunctionsCtx, enquiry_id: str) -> list[dict]:
    """The enquiry's live options as a proposal offers them —
    ``fn_proposal_versions.options`` shape: ``[{min_spend_id, label, venue_id,
    area_id, area_name, event_date, start_time, end_time, ends_next_day,
    amount_cents, food_cents, total_cents, state, confirmable}]``. Withdrawn
    and declined options are left out (they are not on offer)."""
    rows = min_spends_svc.list_min_spends(db, ctx, enquiry_id=enquiry_id)
    areas = {
        a.id: a.name
        for a in db.query(FnVenueArea).filter(
            FnVenueArea.organization_id == ctx.org_id,
            FnVenueArea.id.in_({r.area_id for r, _, _ in rows if r.area_id} or {""}),
        )
    }
    out = []
    for option, state, confirmable in rows:
        if state in ("withdrawn", "declined"):
            continue
        food = money_svc.enquiry_food_cents(
            db, ctx.org_id, enquiry_id, venue_id=option.venue_id
        )
        out.append(
            {
                "min_spend_id": option.id,
                "label": option.label,
                "venue_id": option.venue_id,
                "area_id": option.area_id,
                "area_name": areas.get(option.area_id),
                "event_date": option.event_date.isoformat()
                if option.event_date
                else None,
                "start_time": option.start_time.strftime("%H:%M")
                if option.start_time
                else None,
                "end_time": option.end_time.strftime("%H:%M")
                if option.end_time
                else None,
                "ends_next_day": bool(option.ends_next_day),
                "amount_cents": option.amount_cents,
                "food_cents": food,
                "total_cents": money_svc.option_total_cents(option.amount_cents, food),
                "state": state,
                "confirmable": confirmable,
            }
        )
    return out


# ---------------------------------------------------------------------------
# The money consistency check
# ---------------------------------------------------------------------------


def _outside_raw(html: str | None):
    """The fragment's pieces outside comments and <script>/<style> content,
    in order. A construct that never ends (an unclosed comment or script)
    swallows the rest, as a browser would.

    Linear in the length whatever the input holds: the search only moves
    forward, and the tag patterns can't backtrack. (Patterns like
    ``<(script|style).*?</\\1>`` or ``<[^>]+>`` go quadratic on many unclosed
    tags, and nh3/html5ever on deep or wide trees — 30,000 nested <div>s take
    nh3 over 10 s. This runs on HTML the agent writes, before any sanitiser.)"""
    s = html or ""
    n, i = len(s), 0
    while i < n:
        m = _SPECIAL.search(s, i)
        if m is None:
            yield s[i:]
            return
        yield s[i : m.start()]
        if m.group(1) is None:  # a comment
            k = s.find("-->", m.end())
            if k < 0:
                return
            i = k + 3
            continue
        k = s.find(">", m.end())
        end = _RAW_END[m.group(1).lower()].search(s, k + 1) if k >= 0 else None
        k = s.find(">", end.end()) if end is not None else -1
        if k < 0:
            return
        i = k + 1


def nesting_depth(html: str | None) -> int:
    """How deep the elements nest, as the parser will build them — closely
    enough to refuse pathological input (void elements, and those the parser
    closes by itself, don't count)."""
    depth = deepest = 0
    skip = _VOID | _AUTO_CLOSED
    for piece in _outside_raw(html):
        for m in _TAG.finditer(piece):
            name = m.group(2).lower()
            if name in skip:
                continue
            if m.group(1):
                if depth:
                    depth -= 1
            else:
                depth += 1
                if depth > deepest:
                    deepest = depth
    return deepest


def html_text(html: str | None) -> str:
    """The visible text of a fragment for the money check: tags dropped
    (without spaces, so ``$5,<b>500</b>`` reads as ``$5,500``), script and
    style content and comments dropped, entities decoded, whitespace
    collapsed. Linear time (see ``_outside_raw``)."""
    text = "".join(
        _TAG.sub("", _DECLARATION.sub("", piece)) for piece in _outside_raw(html)
    )
    if "&" in text:
        text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def money_figures(text: str) -> list[int]:
    """Dollar figures in ``text``, in cents, at or above ``CHECK_MIN_CENTS``,
    in order of first appearance."""
    seen: dict[int, None] = {}  # ordered and O(1) — a list's "in" is quadratic
    for whole, part in _MONEY.findall(text or ""):
        cents = int(whole.replace(",", "")) * 100 + int((part or "0").ljust(2, "0"))
        if cents >= CHECK_MIN_CENTS:
            seen.setdefault(cents)
    return list(seen)


def recorded_figures(db: Session, ctx: FunctionsCtx, enquiry_id: str) -> list[dict]:
    """``[{label, cents}]`` — every figure a proposal may legitimately state:
    each option's minimum and its total with food, each menu's value, the
    food at each venue and in all, and what the enquiry is worth."""
    figures: list[dict] = []

    def add(label: str, cents: int | None):
        if cents and not any(f["cents"] == cents for f in figures):
            figures.append({"label": label, "cents": int(cents)})

    options = [
        (o, s)
        for o, s, _ in min_spends_svc.list_min_spends(db, ctx, enquiry_id=enquiry_id)
        if s != "withdrawn"
    ]
    menus = menus_svc.list_menus(db, ctx, enquiry_id=enquiry_id)
    food_at: dict[str, int] = {}
    for menu, items in menus:
        value = money_svc.menu_food_cents(menu, items)
        add(f"menu {menu.name}", value)
        food_at[menu.venue_id] = food_at.get(menu.venue_id, 0) + value
        if menu.is_set_menu and menu.price_per_person_cents:
            add(f"{menu.name} per person", menu.price_per_person_cents)
    for venue_id, cents in food_at.items():
        add("food at the venue", cents)
    add("food in all", sum(food_at.values()))
    totals = []
    for option, _state in options:
        name = option.label or "option"
        add(f"{name} minimum spend", option.amount_cents)
        total = money_svc.option_total_cents(
            option.amount_cents, food_at.get(option.venue_id, 0)
        )
        totals.append(total)
        add(f"{name} total", total)
    add(
        "enquiry value",
        money_svc.enquiry_value_cents(totals, sum(food_at.values())),
    )
    return figures


#: A real proposal states a handful of figures; past these, a document is
#: noise, and the check's work and response stay bounded.
CHECK_MAX_FIGURES = 1_000
CHECK_MAX_WARNINGS = 20


def _matcher(known: list[int]):
    """``figure -> bool``: within the tolerance of a recorded figure, by
    binary search over the sorted recorded ones."""
    ordered = sorted(known)

    def matches(figure: int) -> bool:
        i = bisect.bisect_left(ordered, figure - CHECK_TOLERANCE_CENTS)
        return i < len(ordered) and ordered[i] <= figure + CHECK_TOLERANCE_CENTS

    return matches


def check_consistency(
    db: Session, ctx: FunctionsCtx, enquiry_id: str, html: str | None = None
) -> dict:
    """Warnings for a proposal (the saved one, or ``html`` being edited):
    dollar figures that match nothing on record, and offered options a
    customer couldn't confirm yet (not approved). Never blocks — a person
    decides; the quote page only ever offers the recorded options."""
    if html is None:
        proposal = get_proposal(db, ctx, enquiry_id)
        html = proposal.html if proposal else ""
    else:
        cap_text(html, "The proposal HTML", MAX_HTML_CHARS)
        _enquiry(db, ctx, enquiry_id)
    currency = get_company_settings(db, ctx.org_id).currency
    stated = money_figures(html_text(html))[:CHECK_MAX_FIGURES]
    known = recorded_figures(db, ctx, enquiry_id)
    matches = _matcher([k["cents"] for k in known])
    unmatched = [f for f in stated if not matches(f)]
    warnings = []
    listed = ", ".join(format_money(k["cents"], currency) for k in known) or "none"
    for f in unmatched[:CHECK_MAX_WARNINGS]:
        warnings.append(
            f"The proposal states {format_money(f, currency)}, which matches nothing "
            f"on record ({listed}). Check the figure before sending."
        )
    if len(unmatched) > CHECK_MAX_WARNINGS:
        warnings.append(
            f"…and {len(unmatched) - CHECK_MAX_WARNINGS} more figures that match "
            "nothing on record."
        )
    for option in option_snapshot(db, ctx, enquiry_id):
        if not option["confirmable"] and option["state"] == "proposed":
            warnings.append(
                f"{option['label'] or 'An option'} "
                f"({format_money(option['amount_cents'], currency)}) isn't approved "
                "yet — the customer won't be able to confirm it."
            )
    return {
        "ok": not warnings,
        "stated_cents": stated,
        "unmatched_cents": unmatched,
        "recorded": known,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _brand_for(
    db: Session, ctx: FunctionsCtx, enquiry: FnEnquiry, venue_id: str | None = None
) -> FnVenueBrand | None:
    """The brand a proposal starts from: the venue asked for, else the
    enquiry's venue, else the venue of its first option (a group-wide
    enquiry quoted at one venue)."""
    venue = venue_id or enquiry.venue_id
    if venue is None:
        first = (
            db.query(FnMinSpend.venue_id)
            .filter(
                FnMinSpend.organization_id == ctx.org_id,
                FnMinSpend.enquiry_id == enquiry.id,
                ctx.venue_clause(FnMinSpend.venue_id),
            )
            .order_by(FnMinSpend.sort_order, FnMinSpend.created_at)
            .first()
        )
        venue = first[0] if first else None
    if venue is None:
        return None
    return (
        db.query(FnVenueBrand)
        .filter(
            FnVenueBrand.organization_id == ctx.org_id, FnVenueBrand.venue_id == venue
        )
        .first()
    )


def create_proposal(
    db: Session,
    ctx: FunctionsCtx,
    enquiry_id: str,
    *,
    venue_id: str | None = None,
    restart: bool = False,
    expected_version: int | None = None,
) -> FnProposal:
    """Start the enquiry's proposal from the venue's brand. A second call
    returns the existing proposal — unless ``restart``, which rebuilds it
    from the brand (and so needs the version the editor holds)."""
    require_live(ctx)
    require_staff(ctx)
    enquiry = enquiries_svc.get_enquiry(db, ctx, enquiry_id, for_update=True)
    enquiries_svc.require_open_enquiry(db, ctx, enquiry)
    if venue_id is not None:
        require_venue(ctx, venue_id)
    existing = get_proposal(db, ctx, enquiry.id)
    if existing is not None and not restart:
        return existing
    brand = _brand_for(db, ctx, enquiry, venue_id)
    html, css = sanitise(*starting_document(db, ctx, enquiry, brand))
    if existing is not None:
        check_version(existing, expected_version)
        proposal = existing
        proposal.html, proposal.css = html, css
    else:
        proposal = FnProposal(
            organization_id=ctx.org_id, enquiry_id=enquiry.id, html=html, css=css
        )
        db.add(proposal)
    proposal.based_on_brand_version = brand.version if brand is not None else None
    proposal.updated_by_user_id = ctx.user_id
    proposal.updated_by_label = ctx.actor_label
    flush(db)
    return proposal


def update_proposal(
    db: Session,
    ctx: FunctionsCtx,
    enquiry_id: str,
    *,
    html: str | None = None,
    css: str | None = None,
    expected_version: int | None = None,
) -> FnProposal:
    """Save edited HTML / CSS — sanitised, and stored as sanitised (what the
    editor gets back is exactly what a customer will see)."""
    require_live(ctx)
    require_staff(ctx)
    proposal = get_proposal(db, ctx, enquiry_id)
    if proposal is None:
        raise NotFound("This enquiry has no proposal yet — start one first.")
    check_version(proposal, expected_version)
    clean_html, clean_css = sanitise(
        proposal.html if html is None else html,
        proposal.css if css is None else css,
    )
    proposal.html, proposal.css = clean_html, clean_css
    proposal.updated_by_user_id = ctx.user_id
    proposal.updated_by_label = ctx.actor_label
    proposal.updated_at = _now()
    flush(db)
    return proposal


def freeze_version(
    db: Session, ctx: FunctionsCtx, enquiry_id: str
) -> FnProposalVersion:
    """Freeze the proposal as sent: its HTML, CSS and the options on offer.
    For the send path (F4): call it in the transaction that writes the outbox
    row, point ``fn_outbox.proposal_version_id`` here, and call
    ``mark_version_sent`` when the outbox confirms."""
    require_live(ctx)
    require_staff(ctx)
    proposal = get_proposal(db, ctx, enquiry_id)
    if proposal is None:
        raise NotFound("This enquiry has no proposal yet — start one first.")
    if not (proposal.html or "").strip():
        raise Invalid("The proposal is empty.")
    number = (
        db.query(func.max(FnProposalVersion.version_number))
        .filter(
            FnProposalVersion.organization_id == ctx.org_id,
            FnProposalVersion.proposal_id == proposal.id,
        )
        .scalar()
        or 0
    ) + 1
    row = FnProposalVersion(
        organization_id=ctx.org_id,
        proposal_id=proposal.id,
        enquiry_id=enquiry_id,
        version_number=number,
        html=proposal.html,
        css=proposal.css or "",
        options=option_snapshot(db, ctx, enquiry_id),
        sent_by_user_id=ctx.user_id,
        sent_by_label=ctx.actor_label,
    )
    db.add(row)
    flush(db)
    return row


def mark_version_sent(
    db: Session,
    ctx: FunctionsCtx,
    version_id: str,
    *,
    sent_at: dt.datetime | None = None,
    outbox_id: str | None = None,
    account_id: str | None = None,
    gmail_message_id: str | None = None,
) -> FnProposalEvent:
    """The outbox confirmed the send: stamp the version and record the
    ``sent`` fact that makes the enquiry "quoted". Idempotent per outbox
    row."""
    require_live(ctx)
    row = get_version(db, ctx, version_id)
    if outbox_id is not None:
        existing = (
            db.query(FnProposalEvent)
            .filter(
                FnProposalEvent.organization_id == ctx.org_id,
                FnProposalEvent.outbox_id == outbox_id,
                FnProposalEvent.kind == "sent",
            )
            .first()
        )
        if existing is not None:
            return existing
    when = sent_at or _now()
    row.sent_at = row.sent_at or when
    fact = FnProposalEvent(
        organization_id=ctx.org_id,
        enquiry_id=row.enquiry_id,
        proposal_version_id=row.id,
        kind="sent",
        source="outbox",
        outbox_id=outbox_id,
        account_id=account_id,
        gmail_message_id=gmail_message_id,
        actor_user_id=ctx.user_id,
        actor_label=ctx.actor_label,
        occurred_at=when,
    )
    db.add(fact)
    flush(db)
    return fact


def preview_document(
    db: Session, ctx: FunctionsCtx, enquiry_id: str, *, version_id: str | None = None
) -> str:
    """The saved proposal (or a frozen version) as a complete, CSP-carrying
    document for the editor's sandboxed iframe."""
    from app.services.functions.sanitize import proposal_document

    if version_id:
        row = get_version(db, ctx, version_id)
        if row.enquiry_id != enquiry_id:
            raise NotFound("Proposal version not found.")
        return proposal_document(row.html, row.css)
    proposal = get_proposal(db, ctx, enquiry_id)
    if proposal is None:
        raise NotFound("This enquiry has no proposal yet.")
    return proposal_document(proposal.html, proposal.css)


# ---------------------------------------------------------------------------
# The starting document
# ---------------------------------------------------------------------------


def _e(value) -> str:
    return html_lib.escape(str(value or ""), quote=True)


def _date(value: dt.date | None) -> str:
    return (
        f"{value.strftime('%A')} {value.day} {value.strftime('%B %Y')}" if value else ""
    )


def _time(value: dt.time | None) -> str:
    if value is None:
        return ""
    hour = value.hour % 12 or 12
    minutes = f":{value.minute:02d}" if value.minute else ""
    return f"{hour}{minutes}{'am' if value.hour < 12 else 'pm'}"


def _window(start, end) -> str:
    if start and end:
        return f"{_time(start)} – {_time(end)}"
    return _time(start)


def _context(db: Session, ctx: FunctionsCtx, enquiry: FnEnquiry, brand) -> dict:
    """Everything the template draws on, loaded once."""
    contact = (
        db.query(FnContact)
        .filter(
            FnContact.organization_id == ctx.org_id, FnContact.id == enquiry.contact_id
        )
        .first()
    )
    venue_id = brand.venue_id if brand is not None else enquiry.venue_id
    venue = db.query(Venue).filter(Venue.id == venue_id).first() if venue_id else None
    options = option_snapshot(db, ctx, enquiry.id)
    menus = [
        (m, items)
        for m, items in menus_svc.list_menus(db, ctx, enquiry_id=enquiry.id)
        if venue_id is None or m.venue_id == venue_id
    ]
    area_ids = {o["area_id"] for o in options if o["area_id"]}
    areas = (
        db.query(FnVenueArea)
        .filter(FnVenueArea.organization_id == ctx.org_id, FnVenueArea.id.in_(area_ids))
        .order_by(FnVenueArea.sort_order, FnVenueArea.name)
        .all()
        if area_ids
        else []
    )
    company = get_company_settings(db, ctx.org_id)
    dates = sorted({o["event_date"] for o in options if o["event_date"]})
    if len(dates) == 1:
        when = _date(dt.date.fromisoformat(dates[0]))
    elif dates:
        when = "Several dates (see your options)"
    else:
        when = _date(enquiry.preferred_date_start)
    valid_days = brand.valid_days if brand is not None else 14
    try:
        today = local_today(venue) if venue is not None else dt.date.today()
    except Invalid:
        today = dt.date.today()
    return {
        "enquiry": enquiry,
        "contact": contact,
        "venue": venue,
        "brand": brand,
        "options": options,
        "menus": menus,
        "areas": areas,
        "currency": company.currency,
        "tax_label": company.tax_label,
        "when": when,
        "valid_days": valid_days,
        "valid_until": _date(today + dt.timedelta(days=valid_days)),
    }


def _logo(c: dict) -> str:
    from app.services.functions.sanitize import public_file_url

    brand = c["brand"]
    name = (brand.wordmark if brand else None) or (
        c["venue"].name if c["venue"] else ""
    )
    if brand is not None and brand.logo_file_id:
        return (
            f'<img class="logo" src="{public_file_url(brand.logo_file_id)}" '
            f'alt="{_e(name)}">'
        )
    return f'<div class="wordmark">{_e(name)}</div>'


def _options_block(c: dict) -> str:
    if not c["options"]:
        return ""
    money = c["currency"]
    rows = []
    for o in c["options"]:
        when = ""
        if o["event_date"]:
            when = _date(dt.date.fromisoformat(o["event_date"]))
        if o["start_time"]:
            start = dt.time.fromisoformat(o["start_time"])
            end = dt.time.fromisoformat(o["end_time"]) if o["end_time"] else None
            when += f", {_window(start, end)}"
        rows.append(
            "<tr>"
            f"<td>{_e(o['label'] or o['area_name'] or 'Option')}</td>"
            f"<td>{_e(o['area_name'] or '')}</td>"
            f"<td>{_e(when)}</td>"
            f'<td class="num">{_e(format_money(o["amount_cents"], money))}</td>'
            f'<td class="num">{_e(format_money(o["total_cents"], money))}</td>'
            "</tr>"
        )
    title = "Your option" if len(c["options"]) == 1 else "Your options"
    return (
        f'<section class="options"><h2>{title}</h2><table>'
        "<thead><tr><th>Option</th><th>Space</th><th>When</th>"
        '<th class="num">Minimum spend</th><th class="num">Total</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
        f'<p class="note">All prices {_e(c["tax_label"])}. The total is the minimum '
        "spend or your food, whichever is higher.</p></section>"
    )


def _menus_block(c: dict) -> str:
    if not c["menus"]:
        return ""
    out = []
    for menu, items in c["menus"]:
        head = f"<h3>{_e(menu.name)}</h3>"
        if menu.is_set_menu and menu.price_per_person_cents:
            head += (
                f'<p class="price">'
                f"{_e(format_money(menu.price_per_person_cents, c['currency']))} per "
                "person</p>"
            )
        body, open_list = [], False
        for item in items:
            if item.kind == "header":
                if open_list:
                    body.append("</ul>")
                    open_list = False
                body.append(f"<h4>{_e(item.name)}</h4>")
                continue
            if not open_list:
                body.append("<ul>")
                open_list = True
            parts = [_e(item.name)]
            if item.description:
                parts.append(f'<span class="desc">{_e(item.description)}</span>')
            parts += [f'<span class="tag">{_e(t)}</span>' for t in item.dietaries or []]
            body.append(f"<li>{' '.join(parts)}</li>")
        if open_list:
            body.append("</ul>")
        out.append(f'<div class="menu">{head}{"".join(body)}</div>')
    return f'<section class="menus"><h2>Your menu</h2>{"".join(out)}</section>'


def _spaces_block(c: dict) -> str:
    if not c["areas"]:
        return ""
    parts = []
    for a in c["areas"]:
        caps = []
        if a.capacity_seated:
            caps.append(f"{a.capacity_seated} seated")
        if a.capacity_standing:
            caps.append(f"{a.capacity_standing} standing")
        cap = f'<p class="capacity">{_e(" · ".join(caps))}</p>' if caps else ""
        desc = f"<p>{_e(a.description)}</p>" if a.description else ""
        parts.append(f'<div class="space"><h3>{_e(a.name)}</h3>{cap}{desc}</div>')
    title = "Your space" if len(parts) == 1 else "Your spaces"
    return f'<section class="spaces"><h2>{title}</h2>{"".join(parts)}</section>'


def _inclusions_block(c: dict) -> str:
    brand = c["brand"]
    items = [i for i in (brand.inclusions if brand else []) or [] if i]
    if not items:
        return ""
    lis = "".join(f"<li>{_e(i)}</li>" for i in items)
    return (
        f'<section class="inclusions"><h2>What’s included</h2><ul>{lis}</ul></section>'
    )


def _terms_block(c: dict) -> str:
    brand = c["brand"]
    terms = [t for t in (brand.terms if brand else []) or [] if t]
    if not terms:
        return ""
    parts = []
    for t in terms:
        if isinstance(t, dict):
            title = f"<h3>{_e(t.get('title'))}</h3>" if t.get("title") else ""
            parts.append(f"{title}<p>{_e(t.get('body'))}</p>")
        else:
            parts.append(f"<p>{_e(t)}</p>")
    return f'<section class="terms"><h2>Good to know</h2>{"".join(parts)}</section>'


def _signoff_block(c: dict) -> str:
    brand = c["brand"]
    s = (brand.signoff if brand else None) or {}
    title = s.get("title") or "Next steps"
    body = s.get("body") or (
        "Reply to this email or confirm your option below and we'll lock in your date."
    )
    contact = (
        f'<p class="contact">{_e(s.get("contact"))}</p>' if s.get("contact") else ""
    )
    footer = (
        f'<p class="footer">{_e(brand.footer_contact)}</p>'
        if brand is not None and brand.footer_contact
        else ""
    )
    return (
        f'<section class="signoff"><h2>{_e(title)}</h2><p>{_e(body)}</p>{contact}'
        f'<p class="valid">This proposal is valid for {c["valid_days"]} days.</p>'
        f"{footer}</section>"
    )


def _tokens(c: dict) -> dict[str, str]:
    enquiry, contact = c["enquiry"], c["contact"]
    return {
        "venue_name": _e(c["venue"].name if c["venue"] else ""),
        "client_name": _e(contact.name if contact else ""),
        "client_company": _e(contact.company if contact else ""),
        "event_name": _e(enquiry.subject or "Your event"),
        "event_date": _e(c["when"]),
        "guests": _e(enquiry.guests or ""),
        "booking_type": _e(BOOKING_LABELS.get(enquiry.booking_type or "", "")),
        "valid_days": _e(c["valid_days"]),
        "valid_until": _e(c["valid_until"]),
        "tax_label": _e(c["tax_label"]),
        "logo": _logo(c),
        "options": _options_block(c),
        "menus": _menus_block(c),
        "spaces": _spaces_block(c),
        "inclusions": _inclusions_block(c),
        "terms": _terms_block(c),
        "signoff": _signoff_block(c),
    }


def fill_tokens(template: str, values: dict[str, str]) -> str:
    """Replace ``{{token}}`` placeholders (spaces allowed inside the braces);
    unknown tokens are left as written so a typo is visible."""
    return re.sub(
        r"\{\{\s*([a-z_]+)\s*\}\}",
        lambda m: values.get(m.group(1), m.group(0)),
        template or "",
    )


def default_css(brand: FnVenueBrand | None) -> str:
    colours = (brand.colours if brand else None) or {}
    fonts = (brand.fonts if brand else None) or {}
    primary = colours.get("primary", "#1f2a44")
    accent = colours.get("accent", "#b08d57")
    panel = colours.get("panel", "#f6f3ee")
    text = colours.get("text", "#1a1a1a")
    background = colours.get("background", "#ffffff")
    heading = fonts.get("heading", "Georgia, serif")
    body = fonts.get("body", "system-ui, sans-serif")
    return f""".proposal {{ font-family: {body}; color: {text}; background: {background};
  max-width: 820px; margin: 0 auto; padding: 32px; line-height: 1.5; }}
.proposal h1, .proposal h2, .proposal h3, .proposal h4 {{ font-family: {heading};
  color: {primary}; }}
.proposal .cover {{ border-bottom: 2px solid {accent}; padding-bottom: 24px;
  margin-bottom: 24px; }}
.proposal .logo {{ max-height: 80px; }}
.proposal .wordmark {{ font-family: {heading}; font-size: 28px; color: {primary}; }}
.proposal .eyebrow {{ text-transform: uppercase; letter-spacing: 0.12em;
  color: {accent}; font-size: 13px; }}
.proposal section {{ margin: 28px 0; }}
.proposal .glance dl {{ display: grid; grid-template-columns: 140px 1fr; gap: 6px 16px; }}
.proposal .glance dt {{ color: {accent}; }}
.proposal table {{ width: 100%; border-collapse: collapse; }}
.proposal th, .proposal td {{ text-align: left; padding: 8px; border-bottom: 1px solid {panel}; }}
.proposal .num {{ text-align: right; }}
.proposal .menu, .proposal .space {{ background: {panel}; padding: 16px; margin: 12px 0;
  border-radius: 6px; }}
.proposal .tag {{ font-size: 11px; border: 1px solid {accent}; border-radius: 4px;
  padding: 0 4px; margin-left: 4px; }}
.proposal .note, .proposal .valid, .proposal .footer {{ font-size: 13px; opacity: 0.8; }}
"""


def default_html(c: dict) -> str:
    enquiry, contact = c["enquiry"], c["contact"]
    brand = c["brand"]
    tagline = (
        f'<p class="tagline">{_e(brand.tagline)}</p>' if brand and brand.tagline else ""
    )
    prepared = (contact.company or contact.name) if contact else ""
    glance = [("Date", c["when"])]
    if enquiry.preferred_start_time:
        glance.append(
            ("Time", _window(enquiry.preferred_start_time, enquiry.preferred_end_time))
        )
    if enquiry.guests:
        glance.append(("Guests", str(enquiry.guests)))
    if c["areas"]:
        glance.append(("Space", ", ".join(a.name for a in c["areas"])))
    if enquiry.booking_type:
        glance.append(("Format", BOOKING_LABELS.get(enquiry.booking_type, "")))
    dl = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in glance if v)
    t = _tokens(c)
    return (
        '<article class="proposal">'
        f'<header class="cover">{t["logo"]}{tagline}'
        '<p class="eyebrow">Event proposal</p>'
        f"<h1>{t['event_name']}</h1>"
        f"<p>Prepared for {_e(prepared)}</p></header>"
        f'<section class="glance"><h2>At a glance</h2><dl>{dl}</dl></section>'
        f"{t['spaces']}{t['menus']}{t['options']}{t['inclusions']}{t['terms']}"
        f"{t['signoff']}</article>"
    )


def starting_document(
    db: Session, ctx: FunctionsCtx, enquiry: FnEnquiry, brand: FnVenueBrand | None
) -> tuple[str, str]:
    """``(html, css)`` a new proposal starts from: the brand's own template
    with its ``{{tokens}}`` filled, or Norm's default layout in the brand's
    colours and fonts. Not yet sanitised — the caller sanitises."""
    c = _context(db, ctx, enquiry, brand)
    if brand is not None and (brand.template_html or "").strip():
        return fill_tokens(brand.template_html, _tokens(c)), brand.template_css or ""
    return default_html(c), default_css(brand)
