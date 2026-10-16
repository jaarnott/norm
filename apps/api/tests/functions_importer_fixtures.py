"""A fake Orbit for the Functions importer tests: a PostgREST + public storage
served from memory through ``httpx.MockTransport``, and a small Orbit dataset
that exercises every transform.

The fake implements exactly the PostgREST subset the importer's client sends
— ``select``, ``order``, ``limit``, ``eq``/``gt``/``in``/``ov``/``cs``/``is``
filters and the keyset ``or=(…)`` — so the real ``OrbitPostgrest`` (paging,
quoting, the GET-only rule, the storage host check) is what the tests run.
Anything but GET/HEAD fails the test.
"""

from __future__ import annotations

import copy
import io
import re
import urllib.parse
import uuid

import httpx

ORBIT_URL = "https://fakeorbit.supabase.co"
STORAGE = ORBIT_URL + "/storage/v1/object/public/function-photos/"


def oid(name: str) -> str:
    """A stable Orbit uuid for a readable fixture name."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"orbit.{name}"))


def png_bytes(color=(200, 30, 30), size=(4, 3)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# The fake PostgREST
# ---------------------------------------------------------------------------


def _split_top(text: str) -> list[str]:
    """Split on commas outside parentheses and double quotes."""
    out, depth, quoted, cur = [], 0, False, ""
    for ch in text:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        if ch == "," and depth == 0 and not quoted:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def _cmp_key(value):
    return "" if value is None else str(value)


def _match(row: dict, column: str, expr: str) -> bool:
    op, _, raw = expr.partition(".")
    value = row.get(column)
    if op == "eq":
        return _cmp_key(value) == _unquote(raw)
    if op == "gt":
        return value is not None and _cmp_key(value) > _unquote(raw)
    if op == "in":
        wanted = {_unquote(v) for v in _split_top(raw[1:-1])}
        return _cmp_key(value) in wanted
    if op in ("ov", "cs"):
        wanted = {v.strip() for v in raw.strip("{}").split(",") if v.strip()}
        have = set(value or [])
        return bool(have & wanted) if op == "ov" else wanted <= have
    if op == "is":
        return value is None if raw == "null" else False
    raise AssertionError(f"fake PostgREST: unsupported operator {op!r}")


def _logical(row: dict, expr: str) -> bool:
    """``(a.gt."x",and(a.eq."x",b.gt."y"))`` — the keyset predicate."""
    expr = expr.strip()
    if expr.startswith("and("):
        return all(_logical(row, p) for p in _split_top(expr[4:-1]))
    if expr.startswith("("):
        return any(_logical(row, p) for p in _split_top(expr[1:-1]))
    column, _, rest = expr.partition(".")
    return _match(row, column, rest)


class FakeOrbit:
    """In-memory Orbit. ``tables`` maps table → rows; ``files`` maps storage
    URL → bytes; ``count_override`` lets a test make Orbit's own count lie
    (a broken read)."""

    def __init__(self, tables: dict, files: dict | None = None):
        self.tables = copy.deepcopy(tables)
        self.files = dict(files or {})
        self.count_override: dict[str, int] = {}
        self.requests: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, str(request.url)))
        if request.method not in ("GET", "HEAD"):
            raise AssertionError(
                f"Orbit must only be read: {request.method} {request.url}"
            )
        url = str(request.url)
        if url.startswith(ORBIT_URL + "/storage/v1/object/public/"):
            assert "apikey" not in request.headers, (
                "the key must never go to a storage URL"
            )
            body = self.files.get(url.split("?", 1)[0])
            if body is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(
                200, content=body, headers={"content-type": "image/png"}
            )
        path = request.url.path
        assert path.startswith("/rest/v1/"), path
        assert request.headers.get("apikey") == "service-key"
        table = path.rsplit("/", 1)[-1]
        rows = [dict(r) for r in self.tables.get(table, [])]
        select = "*"
        order: list[str] = []
        limit = None
        for key, value in urllib.parse.parse_qsl(
            request.url.query.decode(), keep_blank_values=True
        ):
            if key == "select":
                select = value
            elif key == "order":
                order = [o.split(".")[0] for o in value.split(",")]
            elif key == "limit":
                limit = int(value)
            elif key == "or":
                rows = [r for r in rows if _logical(r, value)]
            else:
                rows = [r for r in rows if _match(r, key, value)]
        if request.method == "HEAD":
            total = self.count_override.get(table, len(rows))
            return httpx.Response(200, headers={"content-range": f"0-0/{total}"})
        for column in reversed(order):
            rows.sort(key=lambda r: _cmp_key(r.get(column)))
        if limit is not None:
            rows = rows[:limit]
        if select != "*":
            cols = [c.strip() for c in select.split(",")]
            rows = [{c: r.get(c) for c in cols} for r in rows]
        return httpx.Response(200, json=rows)

    def client(self, page: int = 1000):
        from app.services.functions.importer import OrbitPostgrest

        http = httpx.Client(
            transport=httpx.MockTransport(self.handler), follow_redirects=False
        )
        return OrbitPostgrest(ORBIT_URL, "service-key", client=http, page=page)


# ---------------------------------------------------------------------------
# The Norm side
# ---------------------------------------------------------------------------


def make_import_world(db):
    """The organisation an import lands in: shadow mode, the two venues the
    dataset maps to, one member who is also an Orbit team member (by email),
    and Functions rows from before — which the import must replace.

    Lighter than ``functions_world.make_world`` (seven bcrypt-hashed users):
    every importer test runs a whole import, so its setup cost counts."""
    from types import SimpleNamespace

    from app.db.functions_models import FnVenueArea, FnVenueFunctionSettings
    from app.services.functions.importer import import_id
    from tests.conftest import (
        _make_membership,
        _make_organization,
        _make_user,
        _make_venue,
    )

    org = _make_organization(db, name="Cook Bros")
    org.functions_mode = "shadow"
    venue = _make_venue(db, name="Brew Bar", organization_id=org.id)
    venue2 = _make_venue(db, name="Garden Bar", organization_id=org.id)
    venue.timezone = venue2.timezone = "Pacific/Auckland"
    approver = _make_user(db, full_name="Andy Approver")
    _make_membership(db, approver, org, role="member")
    # Rows of an earlier import whose Orbit records are gone since: the
    # reload must delete them. (Import ids — Norm-authored rows would make
    # the importer refuse; see test_records_created_in_norm_stop_the_reload.)
    db.add(
        FnVenueArea(
            id=import_id(org.id, "venue_event_areas", "gone"),
            organization_id=org.id,
            venue_id=venue.id,
            name="Old area",
        )
    )
    db.add(
        FnVenueFunctionSettings(
            id=import_id(org.id, "venues.function_settings", "gone"),
            organization_id=org.id,
            venue_id=venue.id,
        )
    )
    db.flush()
    return SimpleNamespace(
        db=db, org=org, venue=venue, venue2=venue2, approver=approver
    )


# ---------------------------------------------------------------------------
# The dataset
# ---------------------------------------------------------------------------

V1, V2, V3 = oid("venue.brew"), oid("venue.garden"), oid("venue.closed")
TM_ASH, TM_GEORGE = oid("tm.ash"), oid("tm.george")
A_DECK, A_SNUG = oid("area.deck"), oid("area.snug")
C1, C2, C3, C4 = oid("c1"), oid("c2"), oid("c3"), oid("c4")
E1, E2, E3, E4, E5, E6 = (oid(f"e{n}") for n in range(1, 7))
#: Orbit "confirmed" with no booking: recent by date, historic, recent by creation.
E7, E8, E9 = oid("e7"), oid("e8"), oid("e9")
F1, F2, F3, F4 = (oid(f"f{n}") for n in range(1, 5))
P1, P2, P3 = oid("p1"), oid("p2"), oid("p3")
L1, L2, L3 = oid("l1"), oid("l2"), oid("l3")
P4, L4 = oid("p4"), oid("l4")
PT_SET, PT_ALC = oid("pt.set"), oid("pt.alc")
GA1, GA2 = oid("ga1"), oid("ga2")
WF1, SUB1 = oid("wf1"), oid("sub1")
IMG_LOGO = STORAGE + "brand/logo.png"
IMG_PROPOSAL = STORAGE + "quote-images/a.png"
IMG_LIBRARY = STORAGE + "quote-images/library.png"
IMG_TABLE_PLAN = STORAGE + "events/plan.png"
T1, T9 = "thread0001", "thread0009"


def _ts(day: str, time: str = "00:00:00") -> str:
    return f"2026-{day}T{time}+00:00"


def orbit_dataset(*, approver_email: str = "approver@example.com") -> tuple[dict, dict]:
    """``(tables, files)`` — every Orbit table the importer reads."""
    created = _ts("08-01", "01:00:00")
    t: dict[str, list] = {
        name: []
        for name in (
            "venues",
            "team_members",
            "contacts",
            "contact_emails",
            "enquiries",
            "enquiry_status_events",
            "lead_attributions",
            "venue_functions",
            "venue_function_spend_options",
            "contact_proposals",
            "contact_proposal_spend_lines",
            "min_spend_approval_events",
            "enquiry_preorder_menus",
            "venue_function_preorder_menus",
            "preorder_templates",
            "preorder_template_items",
            "venue_function_post_confirm_edits",
            "venue_function_status_history",
            "venue_function_timings",
            "enquiry_quote_documents",
            "enquiry_quote_versions",
            "enquiry_quote_events",
            "venue_event_areas",
            "venue_function_option_visibility",
            "venue_ai_context",
            "venue_menu_links",
            "venue_brand_styles",
            "venue_quote_templates",
            "quote_images",
            "function_reply_templates",
            "web_forms",
            "web_form_fields",
            "web_form_submissions",
            "venue_gmail_accounts",
            "enquiry_gmail_threads",
            "gmail_mailbox_labels",
            "gmail_threads",
            "gmail_messages",
            "venue_function_tokens",
        )
    }
    t["venues"] = [
        {
            "id": V1,
            "name": "Brew Bar",
            "timezone": "Pacific/Auckland",
            "minimum_spend_approver_id": TM_ASH,
            "minimum_spend_override_approver_id": TM_GEORGE,
        },
        {
            "id": V2,
            "name": "The Garden",
            "timezone": "Pacific/Auckland",
            "minimum_spend_approver_id": None,
            "minimum_spend_override_approver_id": None,
        },
        {
            "id": V3,
            "name": "Closed Venue",
            "timezone": None,
            "minimum_spend_approver_id": None,
            "minimum_spend_override_approver_id": None,
        },
    ]
    t["team_members"] = [
        {
            "id": TM_ASH,
            "name": "Ash Approver",
            "email": approver_email.upper(),
            "google_email": None,
        },
        {
            "id": TM_GEORGE,
            "name": "George Eason",
            "email": "george@orbit.example",
            "google_email": None,
        },
    ]
    t["venue_event_areas"] = [
        {
            "id": A_DECK,
            "venue_id": V1,
            "name": "Deck",
            "description": "Sunny",
            "capacity_seated": 40,
            "capacity_standing": 80,
            "event_types": ["large_event_exclusive"],
            "requires_approval": True,
            "min_spend_notes": None,
            "active": True,
            "sort_index": 1,
            "created_at": created,
            "updated_at": created,
        },
        {
            "id": A_SNUG,
            "venue_id": V1,
            "name": "Snug",
            "description": None,
            "capacity_seated": None,
            "capacity_standing": None,
            "event_types": ["group_booking", "bogus"],
            "requires_approval": False,
            "min_spend_notes": "small",
            "active": True,
            "sort_index": 2,
            "created_at": created,
            "updated_at": created,
        },
    ]
    t["venue_function_option_visibility"] = [
        {
            "id": oid("vis1"),
            "venue_id": V1,
            "event_type": "group_booking",
            "option_key": "beverages.bar_tab",
            "visible": True,
            "visible_admin": True,
            "created_at": created,
            "updated_at": created,
        },
        {
            "id": oid("vis2"),
            "venue_id": V1,
            "event_type": "seated_dining",
            "option_key": "details.av",
            "visible": False,
            "visible_admin": False,
            "created_at": created,
            "updated_at": created,
        },
    ]
    t["venue_ai_context"] = [
        {
            "id": oid("ai1"),
            "venue_id": V1,
            "active": True,
            "overview": "Rooftop bar.",
            "policies": "No glitter.",
            "booking_rules": "",
            "extras": "Parking nearby.",
            "created_at": created,
            "updated_at": created,
        },
    ]
    t["venue_menu_links"] = [
        {
            "id": oid("ml1"),
            "venue_id": V1,
            "label": "Food",
            "url": "https://brew.example/food",
            "kind": "food",
            "description": None,
            "active": True,
            "sort_index": 1,
            "created_at": created,
            "updated_at": created,
        },
    ]
    t["venue_brand_styles"] = [
        {
            "id": oid("brand1"),
            "venue_id": V1,
            "style": {
                "headingFont": "Georgia",
                "bodyFont": "Helvetica",
                "foreground": "#111111",
                "background": "#ffffff",
                "headingColor": "#0e5340",
            },
            "logo_url": IMG_LOGO,
            "wordmark": "BREW",
            "tagline": "Bar",
            "footer_contact": "1 Main St",
            "accent_color": "#0e5340",
            "panel_color": "#eeeeee",
            "image_radius": 8,
            "quote_terms": [
                {"title": "Minimum spend", "body": "Food and drink count."}
            ],
            "quote_inclusions": ["Staffing"],
            "signoff_title": "Thanks",
            "signoff_body": "See you",
            "signoff_contact": "Ash",
            "quote_valid_days": 21,
            "cover_background": None,
            "cover_image_id": None,
            "closing_background": None,
            "closing_image_id": None,
            "created_at": created,
            "updated_at": created,
        },
    ]
    t["venue_quote_templates"] = [
        {
            "id": oid("qt1"),
            "venue_id": V1,
            "template_html": "",
            "template_css": "",
            "created_at": created,
            "updated_at": created,
        },
    ]
    t["quote_images"] = [
        {
            "id": oid("qi1"),
            "venue_id": V1,
            "name": "deck crowd",
            "caption": None,
            "public_url": IMG_LIBRARY,
            "storage_path": "quote-images/library.png",
            "tags": ["deck"],
            "width": 4,
            "height": 3,
            "created_by": TM_ASH,
            "created_at": created,
            "updated_at": created,
        },
    ]
    t["function_reply_templates"] = [
        {
            "id": oid("rt1"),
            "venue_id": V1,
            "classification": "new_enquiry",
            "event_type": "group_booking",
            "name": "First reply",
            "subject": "Thanks",
            "body": "Hi",
            "body_html": "<p>Hi</p><script>x()</script>",
            "is_active": True,
            "sort_index": 1,
            "created_at": created,
            "updated_at": created,
        },
        {
            "id": oid("rt2"),
            "venue_id": V1,
            "classification": "follow_up",
            "event_type": "unsure",
            "name": "Nudge",
            "subject": None,
            "body": "Line one\nline two\n\nPara two & more",
            "body_html": None,
            "is_active": True,
            "sort_index": 2,
            "created_at": created,
            "updated_at": created,
        },
        {
            "id": oid("rt3"),
            "venue_id": V3,
            "classification": "follow_up",
            "event_type": "unsure",
            "name": "Closed",
            "subject": None,
            "body": "x",
            "body_html": None,
            "is_active": True,
            "sort_index": 1,
            "created_at": created,
            "updated_at": created,
        },
    ]
    t["preorder_templates"] = [
        {
            "id": PT_SET,
            "venue_id": V1,
            "name": "Set Menu A",
            "description": None,
            "is_set_menu": True,
            "price_per_person": 65,
            "is_active": True,
            "created_at": created,
            "updated_at": created,
        },
        {
            "id": PT_ALC,
            "venue_id": V1,
            "name": "Canapes",
            "description": None,
            "is_set_menu": False,
            "price_per_person": 0,
            "is_active": True,
            "created_at": created,
            "updated_at": created,
        },
    ]
    t["preorder_template_items"] = [
        {
            "id": oid("pti1"),
            "template_id": PT_SET,
            "item_type": "header",
            "name": "Mains",
            "description": None,
            "unit_price": 0,
            "default_percentage": 0,
            "recipe_id": None,
            "stock_item_id": None,
            "sort_order": 0,
            "created_at": created,
        },
        {
            "id": oid("pti2"),
            "template_id": PT_SET,
            "item_type": "item",
            "name": "Steak",
            "description": "Rare",
            "unit_price": 10,
            "default_percentage": 60,
            "recipe_id": "recipe-1",
            "stock_item_id": None,
            "sort_order": 1,
            "created_at": created,
        },
        {
            "id": oid("pti3"),
            "template_id": PT_ALC,
            "item_type": "item",
            "name": "Arancini",
            "description": None,
            "unit_price": 4.5,
            "default_percentage": 0,
            "recipe_id": None,
            "stock_item_id": "stock-9",
            "sort_order": 0,
            "created_at": created,
        },
    ]
    t["contacts"] = [
        {
            "id": C1,
            "contact_name": "Casey Client",
            "contact_email": "Casey@Example.com",
            "company": "Casey Co",
            "contact_phone": "021 1",
            "notes": None,
            "source": "email",
            "assigned_to_member_id": TM_ASH,
            "created_at": _ts("07-01"),
            "updated_at": _ts("07-02"),
        },
        {
            "id": C2,
            "contact_name": "Dup Person",
            "contact_email": "shared@example.com",
            "company": None,
            "contact_phone": None,
            "notes": None,
            "source": "email",
            "assigned_to_member_id": TM_GEORGE,
            "created_at": _ts("07-03"),
            "updated_at": _ts("07-03"),
        },
        {
            "id": C3,
            "contact_name": "Other Name",
            "contact_email": None,
            "company": None,
            "contact_phone": None,
            "notes": "VIP",
            "source": "manual",
            "assigned_to_member_id": None,
            "created_at": _ts("07-04"),
            "updated_at": _ts("07-04"),
        },
        {
            "id": C4,
            "contact_name": None,
            "contact_email": "acme@example.com",
            "company": "Acme Ltd",
            "contact_phone": None,
            "notes": None,
            "source": "mcp",
            "assigned_to_member_id": None,
            "created_at": _ts("07-05"),
            "updated_at": _ts("07-05"),
        },
    ]
    t["contact_emails"] = [
        {
            "id": oid("ce1"),
            "contact_id": C1,
            "email": "casey@example.com",
            "is_primary": True,
            "source": "addon",
            "first_seen_at": created,
            "created_at": created,
        },
        {
            "id": oid("ce2"),
            "contact_id": C1,
            "email": "casey.alt@example.com",
            "is_primary": False,
            "source": "addon",
            "first_seen_at": created,
            "created_at": created,
        },
        {
            "id": oid("ce3"),
            "contact_id": C3,
            "email": "SHARED@example.com",
            "is_primary": True,
            "source": "addon",
            "first_seen_at": created,
            "created_at": created,
        },
    ]
    enquiry = {
        "ai_classification": None,
        "ai_event_type": None,
        "callback_date": None,
        "callback_reason": None,
        "client_note": None,
        "notes": None,
        "preferred_date_end": None,
        "last_outbound_at": None,
        "lost_reason": None,
        "proposal_sent_at": None,
        "quote_amount": None,
        "quote_draft_id": None,
        "quote_drafted_at": None,
        "quote_source": None,
        "quote_token": None,
        "won_function_id": None,
        "guest_count": 60,
        "function_subtype": "large_event_exclusive",
        "source": "email",
        "subject": "A party",
    }
    t["enquiries"] = [
        {
            **enquiry,
            "id": E1,
            "contact_id": C1,
            "venue_id": V1,
            "status": "lost",
            "lost_reason": "Too expensive",
            "preferred_date_start": _ts("11-20", "11:00:00"),
            "created_at": _ts("08-02"),
            "updated_at": _ts("08-20"),
        },
        {
            **enquiry,
            "id": E2,
            "contact_id": C1,
            "venue_id": V1,
            "status": "confirmed",
            "function_subtype": "cocktail",
            "won_function_id": F1,
            "preferred_date_start": _ts("12-03", "11:00:00"),
            "created_at": _ts("08-03"),
            "updated_at": _ts("08-25"),
        },
        {
            **enquiry,
            "id": E3,
            "contact_id": C2,
            "venue_id": V2,
            "status": "quoted",
            "source": "mcp",
            "proposal_sent_at": _ts("08-21", "02:00:00"),
            "preferred_date_start": _ts("12-10", "11:00:00"),
            "preferred_date_end": _ts("12-09", "11:00:00"),
            "created_at": _ts("08-04"),
            "updated_at": _ts("08-21"),
        },
        {
            **enquiry,
            "id": E4,
            "contact_id": C3,
            "venue_id": V1,
            "status": "replied",
            "callback_date": "2026-11-02",
            "callback_reason": "After budget",
            "preferred_date_start": None,
            "created_at": _ts("08-05"),
            "updated_at": _ts("08-06"),
        },
        {
            **enquiry,
            "id": E5,
            "contact_id": C4,
            "venue_id": None,
            "status": "new",
            "function_subtype": None,
            "ai_event_type": "seated_dining",
            "preferred_date_start": None,
            "created_at": _ts("08-06"),
            "updated_at": _ts("08-06"),
        },
        {
            **enquiry,
            "id": E6,
            "contact_id": C1,
            "venue_id": V3,
            "status": "new",
            "preferred_date_start": None,
            "created_at": _ts("08-07"),
            "updated_at": _ts("08-07"),
        },
        {
            **enquiry,
            "id": E7,
            "contact_id": C1,
            "venue_id": V1,
            "status": "confirmed",
            "subject": "Recent wedding",
            "preferred_date_start": _ts("10-19", "11:00:00"),  # 20 Oct in Auckland
            "callback_date": "2026-10-30",
            "callback_reason": "Check numbers",
            "created_at": _ts("03-01"),
            "updated_at": _ts("09-20"),
        },
        {
            **enquiry,
            "id": E8,
            "contact_id": C4,
            "venue_id": V2,
            "status": "confirmed",
            "subject": "Old party",
            "notes": "Loves the deck",
            "preferred_date_start": _ts("02-28", "11:00:00"),  # 1 Mar in Auckland
            "created_at": _ts("02-01"),
            "updated_at": _ts("03-05"),
        },
        {
            **enquiry,
            "id": E9,
            "contact_id": C4,
            "venue_id": V1,
            "status": "confirmed",
            "subject": "New corporate",
            "preferred_date_start": None,
            "created_at": _ts("09-01"),
            "updated_at": _ts("09-02"),
        },
    ]
    t["enquiry_status_events"] = [
        {
            "id": oid("se1"),
            "enquiry_id": E1,
            "from_status": "new",
            "to_status": "replied",
            "actor_label": "System",
            "actor_member_id": None,
            "reason": None,
            "evidence": {},
            "created_at": _ts("08-03"),
        },
        {
            "id": oid("se2"),
            "enquiry_id": E1,
            "from_status": "replied",
            "to_status": "lost",
            "actor_label": None,
            "actor_member_id": TM_GEORGE,
            "reason": None,
            "evidence": {},
            "created_at": _ts("08-10"),
        },
        {
            "id": oid("se3"),
            "enquiry_id": E6,
            "from_status": None,
            "to_status": "new",
            "actor_label": None,
            "actor_member_id": None,
            "reason": None,
            "evidence": {},
            "created_at": _ts("08-07"),
        },
    ]
    t["lead_attributions"] = [
        {
            "id": oid("la1"),
            "enquiry_id": E3,
            "submission_id": SUB1,
            "form_id": WF1,
            "function_id": None,
            "utm_source": "google",
            "utm_medium": "cpc",
            "utm_campaign": "xmas",
            "utm_term": None,
            "utm_content": None,
            "gclid": "gclid-1",
            "gbraid": None,
            "wbraid": None,
            "referrer": "https://google.com",
            "landing_page": "https://brew.example/functions",
            "ref_code": "r1",
            "contact_email": None,
            "contact_name": None,
            "contact_phone": None,
            "conversion_value": None,
            "converted_at": None,
            "linked_manually": False,
            "ads_error": None,
            "ads_upload_status": "none",
            "ads_uploaded_at": None,
            "ads_uploaded_value": None,
            "venue_id": V2,
            "created_at": _ts("08-04"),
            "updated_at": _ts("08-04"),
        },
    ]
    function = {
        "allergies": None,
        "area": None,
        "arrival_drinks": None,
        "arrival_time": None,
        "av": None,
        "bar_tab": None,
        "bar_tab_authorised_people": None,
        "bar_tab_notes": None,
        "bar_tab_restrictions": None,
        "client_link_sent_at": None,
        "client_submitted_at": None,
        "company": None,
        "contact_email": None,
        "contact_id": None,
        "contact_name": None,
        "contact_phone": None,
        "deposit_amount": None,
        "deposit_status": None,
        "description": None,
        "drive_file_id": None,
        "drive_file_url": None,
        "enquiry_id": None,
        "entertainment": None,
        "event_name": None,
        "food_notes": None,
        "food_preorders": [],
        "food_start_at": None,
        "food_timing_end_time": None,
        "food_timing_time": None,
        "food_timing_type": None,
        "function_end_at": None,
        "function_subtype": None,
        "guest_count": 50,
        "image_url": None,
        "invoice_company_name": None,
        "invoice_contact_email": None,
        "invoice_contact_name": None,
        "notes": None,
        "payment_method": "invoice",
        "preorder_template_id": None,
        "prepaid_status": None,
        "runsheet": None,
        "seated_time": None,
        "security": None,
        "special_instructions": None,
        "status": "booked",
        "table_ordering": False,
        "table_ordering_limit": None,
        "table_plan": None,
        "timing_notes": None,
        "timing_preference": None,
        "total_revenue": None,
    }
    for sec in ("details", "beverages", "food"):
        for col in (
            "confirmed_at",
            "edited_at",
            "skipped_at",
            "skipped_by",
            "skipped_by_name",
            "skipped_note",
        ):
            function[f"client_{sec}_{col}"] = None
    t["venue_functions"] = [
        {
            **function,
            "id": F1,
            "venue_id": V1,
            "contact_id": C1,
            "name": "Casey Client",
            "event_name": "Casey's 40th",
            "function_subtype": "group_booking",
            "area": "deck",
            "function_start_at": _ts("12-04", "06:00:00"),
            "function_end_at": _ts("12-04", "11:30:00"),
            "bar_tab": 1500.555,
            "deposit_amount": 200,
            "deposit_status": "paid",
            "status": "confirmed",
            "client_details_confirmed_at": _ts("09-01"),
            "client_food_confirmed_at": _ts("09-02"),
            "client_beverages_skipped_at": _ts("09-03"),
            "client_beverages_skipped_by": TM_ASH,
            "client_beverages_skipped_note": "Paying on the night",
            "client_details_edited_at": _ts("09-05"),
            "client_link_sent_at": _ts("08-26"),
            "av": [{"type": "in_house_mic"}, {"type": "hologram"}],
            "security": [{"name": "Door", "start_time": "18:00", "end_time": "23:00"}],
            "entertainment": [
                {
                    "type": "dj",
                    "name": "DJ Sam",
                    "link": None,
                    "start_time": "21:00",
                    "end_time": "00:00",
                }
            ],
            "food_timing_type": "staggered_arrival",
            "food_timing_time": "19:30",
            "arrival_time": "18:30",
            "seated_time": "",
            "food_preorders": [
                {"item": "copy", "quantity": 1, "unit_price": 1, "total": 1}
            ],
            "created_at": _ts("08-25"),
            "updated_at": _ts("09-05"),
        },
        {
            **function,
            "id": F2,
            "venue_id": V2,
            "contact_email": "New.Person@example.com",
            "contact_name": "New Person",
            "name": "New Person",
            "area": "Mystery Room",
            "drive_file_url": "https://docs.google.com/document/d/abc",
            "status": "sent_to_client",
            "function_start_at": _ts("12-05", "05:00:00"),
            "function_end_at": _ts("12-05", "10:00:00"),
            "image_url": IMG_TABLE_PLAN,
            "food_preorders": [
                {
                    "item": "Fries",
                    "category": "Snacks",
                    "quantity": 10,
                    "unit_price": 12,
                    "total": 120,
                    "dietary": "V, GF",
                },
                {
                    "item": "Olives",
                    "category": "Snacks",
                    "quantity": 0,
                    "unit_price": 9,
                    "total": 0,
                    "dietary": "",
                },
                {
                    "item": "Steak",
                    "category": "Mains",
                    "quantity": 2,
                    "unit_price": 30.005,
                    "total": 60.01,
                    "dietary": "",
                },
            ],
            "created_at": _ts("08-26"),
            "updated_at": _ts("08-28"),
        },
        {
            **function,
            "id": F3,
            "venue_id": V1,
            "name": "Rhearne Herlihy",
            "status": "cancelled",
            "function_start_at": _ts("11-30", "11:00:00"),
            "created_at": _ts("08-27"),
            "updated_at": _ts("08-29"),
        },
        {
            **function,
            "id": F4,
            "venue_id": V1,
            "enquiry_id": E2,
            "contact_id": C1,
            "name": "Second",
            "function_start_at": _ts("12-06", "06:00:00"),
            "created_at": _ts("08-28"),
            "updated_at": _ts("08-28"),
        },
    ]
    t["venue_function_tokens"] = [
        {
            "id": oid("tok-f2"),
            "function_id": F2,
            "created_at": _ts("08-27", "03:00:00"),
        },
    ]
    t["venue_function_status_history"] = [
        {
            "id": oid("sh1"),
            "function_id": F1,
            "from_status": None,
            "to_status": "booked",
            "source": "app",
            "changed_by": TM_ASH,
            "changed_by_name": "Ash Approver",
            "created_at": _ts("08-25"),
        },
        {
            "id": oid("sh2"),
            "function_id": F3,
            "from_status": "booked",
            "to_status": "cancelled",
            "source": "app",
            "changed_by": TM_GEORGE,
            "changed_by_name": "George Eason",
            "created_at": _ts("08-29"),
        },
    ]
    t["venue_function_post_confirm_edits"] = [
        {
            "id": oid("pce1"),
            "function_id": F1,
            "edited_by": TM_GEORGE,
            "edited_by_name": "George Eason",
            "note": None,
            "created_at": _ts("09-06"),
        },
    ]
    t["venue_function_timings"] = [
        {
            "id": oid("tim1"),
            "function_id": F1,
            "template_name": "Run",
            "timing_template_id": None,
            "created_at": _ts("09-01"),
            "items": [
                {
                    "_key": "a",
                    "activity": "Guests arrive",
                    "item_type": "item",
                    "time_label": "6:30pm",
                },
                {
                    "_key": "b",
                    "activity": "Cake",
                    "item_type": "item",
                    "time_label": "after speeches",
                },
                {
                    "_key": "c",
                    "activity": "Last drinks",
                    "item_type": "item",
                    "time_label": "12:15am",
                },
            ],
        },
    ]
    t["contact_proposals"] = [
        {
            "id": P1,
            "contact_id": C1,
            "enquiry_id": E2,
            "venue_id": V1,
            "area_id": A_DECK,
            "proposed_start": _ts("12-04", "06:00:00"),
            "proposed_end": _ts("12-04", "11:30:00"),
            "label": None,
            "amount": 0,
            "status": "accepted",
            "approved_at": None,
            "approved_by_member_id": None,
            "converted_function_id": F1,
            "created_at": _ts("08-10"),
            "updated_at": _ts("08-25"),
        },
        {
            "id": P2,
            "contact_id": C1,
            "enquiry_id": E2,
            "venue_id": V1,
            "area_id": A_SNUG,
            "proposed_start": _ts("12-05", "06:00:00"),
            "proposed_end": None,
            "label": "Snug option",
            "amount": 0,
            "status": "proposed",
            "approved_at": None,
            "approved_by_member_id": None,
            "converted_function_id": None,
            "created_at": _ts("08-11"),
            "updated_at": _ts("08-11"),
        },
        {
            "id": P3,
            "contact_id": C2,
            "enquiry_id": E3,
            "venue_id": V2,
            "area_id": None,
            "proposed_start": _ts("12-10", "05:00:00"),
            "proposed_end": _ts("12-10", "10:00:00"),
            "label": None,
            "amount": 0,
            "status": "accepted",
            "approved_at": None,
            "approved_by_member_id": None,
            "converted_function_id": None,
            "created_at": _ts("08-12"),
            "updated_at": _ts("08-22"),
        },
        {
            "id": P4,
            "contact_id": C4,
            "enquiry_id": E8,
            "venue_id": V2,
            "area_id": None,
            "proposed_start": _ts("02-28", "06:00:00"),
            "proposed_end": None,
            "label": None,
            "amount": 0,
            "status": "proposed",
            "approved_at": None,
            "approved_by_member_id": None,
            "converted_function_id": None,
            "created_at": _ts("02-02"),
            "updated_at": _ts("02-10"),
        },
    ]
    t["contact_proposal_spend_lines"] = [
        {
            "id": L1,
            "proposal_id": P1,
            "area_id": A_DECK,
            "label": None,
            "amount": 5000.5,
            "start_at": _ts("12-04", "06:00:00"),
            "end_at": _ts("12-04", "11:30:00"),
            "status": "approved",
            "approved_by": TM_ASH,
            "approved_at": _ts("08-15"),
            "approval_type": "standard",
            "approval_note": None,
            "created_at": _ts("08-10"),
            "updated_at": _ts("08-15"),
        },
        {
            "id": L2,
            "proposal_id": P2,
            "area_id": A_SNUG,
            "label": None,
            "amount": 3000,
            "start_at": None,
            "end_at": None,
            "status": "approved",
            "approved_by": TM_GEORGE,
            "approved_at": _ts("08-16"),
            "approval_type": "override",
            "approval_note": "Quiet night",
            "created_at": _ts("08-11"),
            "updated_at": _ts("08-16"),
        },
        {
            "id": L3,
            "proposal_id": P3,
            "area_id": None,
            "label": None,
            "amount": 8000,
            "start_at": _ts("12-10", "05:00:00"),
            "end_at": _ts("12-10", "10:00:00"),
            "status": "proposed",
            "approved_by": None,
            "approved_at": None,
            "approval_type": None,
            "approval_note": None,
            "created_at": _ts("08-12"),
            "updated_at": _ts("08-12"),
        },
    ]
    t["contact_proposal_spend_lines"].append(
        {
            "id": L4,
            "proposal_id": P4,
            "area_id": None,
            "label": None,
            "amount": 1200,
            "start_at": None,
            "end_at": None,
            "status": "approved",
            "approved_by": TM_ASH,
            "approved_at": _ts("02-10"),
            "approval_type": "standard",
            "approval_note": None,
            "created_at": _ts("02-02"),
            "updated_at": _ts("02-10"),
        }
    )
    t["min_spend_approval_events"] = [
        {
            "id": oid("ae1"),
            "line_id": L1,
            "proposal_id": P1,
            "enquiry_id": E2,
            "venue_id": V1,
            "action": "approve",
            "approval_type": "standard",
            "amount": 5000.5,
            "member_id": TM_ASH,
            "member_name": "Ash Approver",
            "note": None,
            "created_at": _ts("08-15"),
        },
        {
            "id": oid("ae-gone"),
            "line_id": oid("deleted-line"),
            "proposal_id": None,
            "enquiry_id": None,
            "venue_id": V1,
            "action": "approve",
            "approval_type": "standard",
            "amount": 1,
            "member_id": TM_ASH,
            "member_name": "Ash Approver",
            "note": None,
            "created_at": _ts("08-15"),
        },
    ]
    t["venue_function_spend_options"] = [
        {
            "id": oid("o1"),
            "function_id": F1,
            "label": None,
            "amount": 5000.5,
            "start_at": _ts("12-04", "06:00:00"),
            "end_at": _ts("12-04", "11:30:00"),
            "approved": True,
            "approved_by": TM_ASH,
            "approved_at": _ts("08-15"),
            "spend_status": "confirmed",
            "area_id": A_DECK,
            "created_at": _ts("08-25"),
        },
        {
            "id": oid("o2"),
            "function_id": F2,
            "label": None,
            "amount": 2500,
            "start_at": _ts("12-05", "05:00:00"),
            "end_at": _ts("12-05", "10:00:00"),
            "approved": True,
            "approved_by": TM_GEORGE,
            "approved_at": _ts("08-26"),
            "spend_status": "confirmed",
            "area_id": None,
            "created_at": _ts("08-26"),
        },
        {
            "id": oid("o3"),
            "function_id": F3,
            "label": None,
            "amount": 100,
            "start_at": _ts("11-30", "11:00:00"),
            "end_at": _ts("11-30", "12:00:00"),
            "approved": True,
            "approved_by": None,
            "approved_at": None,
            "spend_status": "approved",
            "area_id": None,
            "created_at": _ts("08-27"),
        },
        {
            "id": oid("o4"),
            "function_id": F3,
            "label": None,
            "amount": 200,
            "start_at": _ts("11-30", "11:00:00"),
            "end_at": _ts("11-30", "12:00:00"),
            "approved": True,
            "approved_by": None,
            "approved_at": None,
            "spend_status": "approved",
            "area_id": None,
            "created_at": _ts("08-27"),
        },
    ]
    set_items = [
        {
            "item": "Mains",
            "item_type": "header",
            "quantity": 0,
            "unit_price": 0,
            "total": 0,
            "dietary": "",
            "category": "",
            "description": "Mains",
        },
        {
            "item": "Steak",
            "item_type": "item",
            "quantity": 30,
            "unit_price": 12,
            "total": 0,
            "dietary": "",
            "dietaries": ["GF"],
            "category": "",
            "description": "Rare",
            "recipe_id": "recipe-1",
            "default_percentage": 60,
        },
    ]
    t["enquiry_preorder_menus"] = [
        {
            "id": oid("em1"),
            "enquiry_id": E2,
            "venue_id": V1,
            "preorder_template_id": PT_SET,
            "template_name": "Set Menu A",
            "is_set_menu": True,
            "price_per_person": 65,
            "guest_count": 50,
            "items": set_items,
            "created_at": _ts("08-12"),
            "updated_at": _ts("08-12"),
        },
        {
            "id": oid("em2"),
            "enquiry_id": E4,
            "venue_id": V1,
            "preorder_template_id": PT_ALC,
            "template_name": "Canapes",
            "is_set_menu": False,
            "price_per_person": 0,
            "guest_count": 0,
            "items": [
                {
                    "item": "Arancini",
                    "item_type": "item",
                    "quantity": 20,
                    "unit_price": 4.5,
                    "total": 90,
                }
            ],
            "created_at": _ts("08-12"),
            "updated_at": _ts("08-12"),
        },
    ]
    t["venue_function_preorder_menus"] = [
        {
            "id": oid("vm1"),
            "function_id": F1,
            "preorder_template_id": PT_SET,
            "template_name": "Set Menu A",
            "is_set_menu": True,
            "price_per_person": 65,
            "guest_count": 52,
            "items": set_items,
            "created_at": _ts("08-25"),
        },
        {
            "id": oid("vm2"),
            "function_id": F1,
            "preorder_template_id": None,
            "template_name": "Extras",
            "is_set_menu": False,
            "price_per_person": 0,
            "guest_count": 0,
            "items": [
                {
                    "item": "Fries",
                    "item_type": "item",
                    "quantity": 5,
                    "unit_price": 14.5,
                    "total": 72.5,
                }
            ],
            "created_at": _ts("08-26"),
        },
    ]
    proposal_html = (
        f'<h1>Hi</h1><img src="{IMG_PROPOSAL}?width=300&amp;q=1">'
        '<link href="https://fonts.googleapis.com/css2?family=Inter" rel="stylesheet">'
        # What the sanitiser must take out of stored HTML:
        '<p onclick="steal()">Dear Casey</p><script>steal()</script>'
        '<img src="https://elsewhere.example/hotlink.png">'
    )
    t["enquiry_quote_documents"] = [
        {
            "id": oid("doc1"),
            "enquiry_id": E3,
            "body_html": proposal_html,
            "custom_css": "@import url(https://fonts.googleapis.com/css2?family=Inter);"
            f".cover{{background:url('{IMG_PROPOSAL}')}}",
            "email_body": "Hi there",
            "email_body_html": None,
            "email_mode": "proposal",
            "email_subject": "Your quote",
            "is_customised": True,
            "template_version": 3,
            "updated_by_member_id": TM_ASH,
            "created_at": _ts("08-20"),
            "updated_at": _ts("08-21"),
        },
    ]
    t["enquiry_quote_versions"] = [
        {
            "id": oid("qv1"),
            "enquiry_id": E3,
            "version_number": 1,
            "body_html": proposal_html,
            "custom_css": "",
            "quote_token": "tok-1",
            "sent_by_member_id": TM_ASH,
            "total": 8000,
            "created_at": _ts("08-21", "01:00:00"),
        },
    ]
    t["enquiry_quote_events"] = [
        {
            "id": oid("qe1"),
            "enquiry_id": E3,
            "kind": "sent",
            "token": "tok-1",
            "member_id": TM_ASH,
            "gmail_message_id": "msg-sent",
            "gmail_thread_id": T1,
            "gmail_draft_id": None,
            "amount": 8000,
            "note": None,
            "proposal_id": None,
            "created_at": _ts("08-21", "01:00:00"),
        },
        {
            "id": oid("qe2"),
            "enquiry_id": E3,
            "kind": "viewed",
            "token": "tok-1",
            "member_id": None,
            "gmail_message_id": None,
            "gmail_thread_id": None,
            "gmail_draft_id": None,
            "amount": None,
            "note": None,
            "proposal_id": None,
            "created_at": _ts("08-22"),
        },
        {
            "id": oid("qe3"),
            "enquiry_id": E3,
            "kind": "drafted",
            "token": None,
            "member_id": TM_ASH,
            "gmail_message_id": None,
            "gmail_thread_id": None,
            "gmail_draft_id": "d1",
            "amount": None,
            "note": None,
            "proposal_id": None,
            "created_at": _ts("08-20"),
        },
    ]
    t["web_forms"] = [
        {
            "id": WF1,
            "venue_id": V2,
            "slug": "garden-enquiry",
            "name": "Garden enquiry",
            "is_active": True,
            "brand_color": "#123456",
            # Orbit's camelCase style profile: two settings forms.js applies,
            # one it doesn't, one value Norm must refuse (url()).
            "style": {
                "bodyFont": "Inter",
                "inputRadius": 8,
                "checkboxShape": "round",
                "headingColor": "url(https://evil.example/x)",
            },
            "intro_html": '<p>Hello</p><iframe src="https://evil.example"></iframe>',
            "submit_button_label": "Send",
            "success_message": "Thanks!",
            "recipient_email": "garden@example.com",
            "subject_template": "{name}",
            "reply_to_field_key": "email",
            "honeypot_field_name": "hp_1",
            "created_at": created,
            "updated_at": created,
        },
    ]
    choices = [
        {"label": "Seated dinner", "value": "seated_dining"},
        {"label": "Party", "value": "party"},
    ]
    t["web_form_fields"] = [
        {
            "id": oid(f"ff{n}"),
            "form_id": WF1,
            "key": key,
            "label": label,
            "type": kind,
            "is_required": n < 2,
            "options": options,
            "placeholder": None,
            "help_text": None,
            "sort_order": n,
            "validation": validation,
            "created_at": created,
            "updated_at": created,
        }
        for n, (key, kind, label, options, validation) in enumerate(
            [
                ("name", "name", "Name", None, None),
                ("email", "email", "Email", None, None),
                ("message", "textarea", "Message", None, None),
                ("type_of_event", "radio", "Type of event", choices, None),
                ("preferred_dates", "date_list", "Preferred dates", None, None),
                ("contact_section", "section", "", None, None),
                ("start_finish_times", "time_range", "Start & finish", None, None),
                (
                    "estimated_group_size",
                    "number",
                    "Guests",
                    None,
                    {"min": 1, "max": 400},
                ),
                ("area_preference", "select", "Area", choices, None),
                ("company_name", "text", "Company", None, None),
                (
                    "extras",
                    "checkbox",
                    "Extras",
                    [{"label": "DJ", "value": "dj"}],
                    None,
                ),
            ]
        )
    ]
    t["web_form_submissions"] = [
        {
            "id": SUB1,
            "form_id": WF1,
            "venue_id": V2,
            "payload": {"name": "Dup Person"},
            "submitted_at": _ts("08-04"),
            "created_at": _ts("08-04"),
            "delivery_status": "sent",
            "delivery_error": None,
            "message_id": "m-1",
            "ip_hash": "abc",
            "user_agent": "UA",
        },
    ]
    t["venue_gmail_accounts"] = [
        {
            "id": GA1,
            "venue_id": V1,
            "google_email": "Events@BrewBar.example",
            "display_name": "Brew Events",
            "signature_html": "<p>Brew</p><script>x()</script>",
            "created_at": created,
            "updated_at": created,
        },
        {
            "id": GA2,
            "venue_id": V2,
            "google_email": "garden@example.com",
            "display_name": None,
            "signature_html": None,
            "created_at": created,
            "updated_at": created,
        },
    ]
    t["gmail_mailbox_labels"] = [
        {
            "account_id": GA1,
            "account_source": "venue_gmail_accounts",
            "label_id": "Label_1",
            "label_name": "Actioned - Enquiry",
            "actioned_kind": "booking",
            "updated_at": created,
        },
        {
            "account_id": GA1,
            "account_source": "venue_gmail_accounts",
            "label_id": "Label_2",
            "label_name": "Actioned - Reservations",
            "actioned_kind": "event",
            "updated_at": created,
        },
        {
            "account_id": GA1,
            "account_source": "venue_gmail_accounts",
            "label_id": "INBOX",
            "label_name": "INBOX",
            "actioned_kind": None,
            "updated_at": created,
        },
        {
            "account_id": GA2,
            "account_source": "venue_gmail_accounts",
            "label_id": "Label_7",
            "label_name": "Actioned - Enquiry",
            "actioned_kind": "booking",
            "updated_at": created,
        },
    ]
    thread = {
        "account_source": "venue_gmail_accounts",
        "subject": "Party",
        "snippet": "hi",
        "message_count": 3,
        "unread_count": 0,
        "last_from_email": "c@example.com",
        "last_from_name": "C",
        "created_at": created,
    }
    t["gmail_threads"] = [
        {
            **thread,
            "account_id": GA1,
            "thread_id": T1,
            "last_message_at": _ts("08-06"),
            "label_ids": ["Label_1"],
            "updated_at": _ts("08-06"),
        },
        {
            **thread,
            "account_id": GA1,
            "thread_id": T9,
            "last_message_at": _ts("08-07"),
            "label_ids": ["Label_1", "Label_2", "INBOX"],
            "updated_at": _ts("08-07"),
        },
        {
            **thread,
            "account_id": GA2,
            "thread_id": "garden-thread",
            "last_message_at": _ts("08-07"),
            "label_ids": ["Label_7"],
            "updated_at": _ts("08-07"),
        },
    ]
    message = {
        "account_source": "venue_gmail_accounts",
        "cc_emails": [],
        "reply_to_email": None,
        "unread": False,
        "is_internal": None,
        "is_auto_reply": None,
        "created_at": created,
        "updated_at": created,
        "subject": "Party",
        "snippet": "…",
    }
    t["gmail_messages"] = [
        {
            **message,
            "message_id": "m-in",
            "thread_id": T1,
            "account_id": GA1,
            "from_email": "c3@example.com",
            "from_name": "Other",
            "to_emails": ["events@brewbar.example"],
            "internal_date": _ts("08-05", "03:00:00"),
            "label_ids": ["INBOX"],
            "is_inbound": True,
            "unread": True,
        },
        {
            **message,
            "message_id": "m-out",
            "thread_id": T1,
            "account_id": GA1,
            "from_email": "events@brewbar.example",
            "from_name": "Brew",
            "to_emails": ["c3@example.com"],
            "internal_date": _ts("08-05", "05:00:00"),
            "label_ids": ["SENT"],
            "is_inbound": False,
        },
        {
            **message,
            "message_id": "m-draft",
            "thread_id": T1,
            "account_id": GA1,
            "from_email": "events@brewbar.example",
            "from_name": "Brew",
            "to_emails": ["c3@example.com"],
            "internal_date": _ts("08-05", "06:00:00"),
            "label_ids": ["DRAFT"],
            "is_inbound": False,
        },
        {
            **message,
            "message_id": "m-garden",
            "thread_id": "garden-thread",
            "account_id": GA2,
            "from_email": "x@example.com",
            "from_name": "X",
            "to_emails": [],
            "internal_date": _ts("08-07"),
            "label_ids": [],
            "is_inbound": True,
        },
    ]
    t["enquiry_gmail_threads"] = [
        {
            "id": oid("egt1"),
            "enquiry_id": E4,
            "thread_id": T1,
            "attached_by_account_id": None,
            "attached_by_member_id": None,
            "linked_by_member_id": TM_ASH,
            "linked_at": _ts("08-05", "04:00:00"),
            "unlinked_at": None,
            "last_message_at": None,
            "snippet": None,
            "subject": None,
        },
        {
            "id": oid("egt2"),
            "enquiry_id": E1,
            "thread_id": T1,
            "attached_by_account_id": None,
            "attached_by_member_id": None,
            "linked_by_member_id": None,
            "linked_at": _ts("08-03"),
            "unlinked_at": _ts("08-04"),
            "last_message_at": None,
            "snippet": None,
            "subject": None,
        },
        {
            "id": oid("egt3"),
            "enquiry_id": E2,
            "thread_id": "garden-thread",
            "attached_by_account_id": GA2,
            "attached_by_member_id": None,
            "linked_by_member_id": None,
            "linked_at": _ts("08-07"),
            "unlinked_at": None,
            "last_message_at": None,
            "snippet": None,
            "subject": None,
        },
        {
            "id": oid("egt4"),
            "enquiry_id": E5,
            "thread_id": "thread-nowhere",
            "attached_by_account_id": None,
            "attached_by_member_id": None,
            "linked_by_member_id": None,
            "linked_at": _ts("08-06"),
            "unlinked_at": None,
            "last_message_at": None,
            "snippet": None,
            "subject": None,
        },
    ]
    files = {
        IMG_LOGO: png_bytes((1, 2, 3)),
        IMG_PROPOSAL: png_bytes((4, 5, 6)),
        IMG_LIBRARY: png_bytes((7, 8, 9)),
        IMG_TABLE_PLAN: png_bytes((10, 11, 12)),
    }
    return t, files


def strip(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
