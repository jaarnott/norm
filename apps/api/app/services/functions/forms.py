"""Web forms — the embeddable enquiry form (``/f/<slug>`` and ``forms.js``),
its builder, and what happens to a submission.

**A submission** is checked before it is believed: a per-form signed
timestamp from the schema fetch (``form_token``) gives a server-measured fill
time; a honeypot field catches form-filling bots; the router rate-limits per
hashed IP and form; and every answer is validated against the form's fields
here, never trusted from the page. Spam is kept (with ``spam_reason``, for
tuning) but answered with the same "thanks" as a real lead and goes nowhere.

* **Shadow** (Orbit still live): the submission is stored and emailed to the
  venue mailbox with ``Reply-To`` = the customer — Orbit's behaviour, so the
  forms can be re-embedded on the websites before cut-over. (Norm's Gmail
  writes are off in shadow; the email goes out through Norm's system mail.)
* **Live**: the enquiry is created too — contact matched by email, source and
  attribution fields set — the submission linked to it, and the venue emailed.

Attribution (utm_*, gclid, gbraid, wbraid, referrer, landing page) comes from
the page: first-party ``track.js`` keeps first-touch values that ``forms.js``
posts with the answers. The client IP is stored only as a salted hash.

Staff build forms here too (a fixed field set and theme tokens). Builder
writes follow the module rule: live only — in shadow the forms come from the
Orbit import.
"""

from __future__ import annotations

import datetime as dt
import hmac
import html as html_lib
import logging
import math
import re
import secrets
from dataclasses import dataclass, field

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.functions_models import (
    BOOKING_TYPES,
    FORM_FIELD_TYPES,
    FnFormSubmission,
    FnMailboxAccount,
    FnVenueFunctionSettings,
    FnWebForm,
    FnWebFormField,
)
from app.db.models import Organization, Venue, _now
from app.services.functions import enquiries as enquiries_svc
from app.services.functions import notify
from app.services.functions._rows import apply_fields, check_version, load, require_text
from app.services.functions.contacts import normalise_email
from app.services.functions.ctx import (
    FunctionsCtx,
    for_org,
    require_live,
    require_staff,
    require_venue,
)
from app.services.functions.errors import (
    Conflict,
    FunctionsError,
    Invalid,
    NotFound,
    flush,
)
from app.services.functions.sanitize import sanitize_html
from app.services.functions.timeutil import infer_ends_next_day, local_now

logger = logging.getLogger(__name__)

#: Faster than this from schema fetch to submit is a script, not a person.
MIN_FILL = dt.timedelta(seconds=2)
#: A filled honeypot only counts as spam when it was also quick: password
#: managers autofill hidden inputs for real people (Orbit learnt this — it
#: lost genuine leads to a honeypot-only rule).
HONEYPOT_FAST = dt.timedelta(seconds=10)
#: A form left open longer than this must be reloaded.
FORM_TOKEN_TTL = dt.timedelta(hours=48)
#: Whole-form ceilings, whatever IP a request claims (X-Forwarded-For can be
#: forged on a direct run.app hit, so the per-IP limit alone can be dodged).
#: Real traffic is far below them (Orbit: ~7 leads a day across ALL forms).
#: Past them a submission is REFUSED (429), not stored as spam: a flood must
#: fill neither the database disk nor the venue's inbox. The minute limit is
#: the router's (mcp_rate_limits); the day limit counts stored submissions.
FORM_MINUTE_LIMIT = 30
FORM_DAY_LIMIT = 500
MAX_FIELDS = 60
MAX_OPTIONS = 50
ATTRIBUTION_KEYS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "gbraid",
    "wbraid",
    "referrer",
    "landing_page",
)
#: What a field's answer may fill on the enquiry it creates (live mode).
MAPS_TO = (
    "contact.name",
    "contact.email",
    "contact.phone",
    "contact.company",
    "guests",
    "preferred_date",
    "preferred_dates",
    "preferred_time",
    "booking_type",
    "subject",
    "client_note",
)
FORM_SETTINGS = (
    "slug",
    "name",
    "active",
    "theme",
    "intro_html",
    "submit_label",
    "success_message",
    "recipient_email",
    "subject_template",
    "reply_to_field_key",
    "honeypot_field",
    "booking_type",
)
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
#: The theme keys forms.js and the /f page understand — THEME_ALIASES +
#: THEME_KEYS in apps/web/public/embed/forms.js. Keep the two in step: a key
#: listed here and not there would be saved and silently ignored.
THEME_KEYS = frozenset(
    {
        # shorthands
        "primary",
        "brand_color",
        "accent",
        "text",
        "font",
        "radius",
        # specific
        "background",
        "foreground",
        "muted_foreground",
        "heading_font",
        "heading_color",
        "heading_size",
        "heading_weight",
        "heading_transform",
        "body_font",
        "body_size",
        "label_color",
        "label_size",
        "label_weight",
        "label_transform",
        "input_background",
        "input_border_color",
        "input_border_width",
        "input_radius",
        "textarea_radius",
        "input_text_color",
        "input_placeholder_color",
        "input_focus_color",
        "input_padding_x",
        "input_padding_y",
        "checkbox_size",
        "button_background",
        "button_text_color",
        "button_border_color",
        "button_border_width",
        "button_radius",
        "button_transform",
        "button_weight",
        "button_font",
        "button_padding_x",
        "button_padding_y",
        "section_title_font",
        "section_title_color",
        "section_title_size",
        "section_title_weight",
        "section_title_spacing",
        "section_title_margin_top",
        "section_title_margin_bottom",
        "max_width",
        "field_gap",
    }
)
_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_THEME_BAD = re.compile(r"[<>{};\\]")
_TIME = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")
_NONCE = re.compile(r"[A-Za-z0-9_-]{16}")
#: The longest answer accepted per field type — checked BEFORE any parsing or
#: pattern matching, so a 60 KB "email" costs nothing to refuse.
_MAX_CHARS = {
    "name": 200,
    "email": 254,
    "phone": 40,
    "text": 500,
    "long_text": 5000,
    "number": 40,
    "date": 10,
    "time": 8,
    "choice": 200,
}
_NAME_PART = 100
MAX_DATES = 20
DEFAULT_SUCCESS = "Thanks — we'll be in touch."


def _key(label: bytes) -> bytes:
    return hmac.new(settings.JWT_SECRET.encode(), label, "sha256").digest()


def ip_hash(form_id: str, ip: str) -> str:
    """The submitter's IP as a salted, per-form hash (the IP is never
    stored, and the same visitor can't be followed across forms)."""
    return hmac.new(
        _key(b"norm-functions/form-ip/v1"), f"{form_id}:{ip}".encode(), "sha256"
    ).hexdigest()


def _form_sig(form_id: str, ts: int, nonce: str) -> str:
    return hmac.new(
        _key(b"norm-functions/form-token/v2"),
        f"{form_id}:{ts}:{nonce}".encode(),
        "sha256",
    ).hexdigest()[:32]


@dataclass(frozen=True)
class FormToken:
    nonce: str
    age: dt.timedelta
    expires_at: dt.datetime


def issue_form_token(form: FnWebForm, now: dt.datetime | None = None) -> str:
    """``<unix seconds>.<nonce>.<signature>``: when the schema was fetched,
    signed so the page can't pretend it was fetched earlier, plus a random
    nonce that makes the token good for one stored submission."""
    ts = int((now or _now()).timestamp())
    nonce = secrets.token_urlsafe(12)
    return f"{ts}.{nonce}.{_form_sig(form.id, ts, nonce)}"


def read_form_token(
    form: FnWebForm, token, now: dt.datetime | None = None
) -> FormToken | None:
    """The token's age, nonce and expiry — or None when it is missing,
    malformed, forged or older than ``FORM_TOKEN_TTL``."""
    if not isinstance(token, str) or len(token) > 100:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    ts_text, nonce, sig = parts
    if not (ts_text.isascii() and ts_text.isdigit() and len(ts_text) <= 12):
        return None
    if not _NONCE.fullmatch(nonce):
        return None
    ts = int(ts_text)
    if not hmac.compare_digest(_form_sig(form.id, ts, nonce), sig):
        return None
    issued = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc)
    age = (now or _now()) - issued
    if age < dt.timedelta(0) or age > FORM_TOKEN_TTL:
        return None
    return FormToken(nonce=nonce, age=age, expires_at=issued + FORM_TOKEN_TTL)


def _first_use(db: Session, form: FnWebForm, token: FormToken) -> bool:
    """Record the token's nonce and say whether this is its first use.

    Called only when a row is about to be stored, in the same transaction:
    the marker commits with the submission, and two concurrent uses queue on
    the unique key — the second sees count 2. It lives in ``mcp_rate_limits``
    with ``window_start`` set to the token's expiry: the hourly GC deletes
    windows that started over an hour ago, so the marker outlives the token
    it guards, then goes."""
    row = db.execute(
        text(
            "INSERT INTO mcp_rate_limits (id, bucket_key, window_start, count) "
            "VALUES (gen_random_uuid()::text, :key, :until, 1) "
            "ON CONFLICT (bucket_key, window_start) "
            "DO UPDATE SET count = mcp_rate_limits.count + 1 "
            "RETURNING count"
        ),
        {"key": f"fnpub:form-nonce:{form.id}:{token.nonce}", "until": token.expires_at},
    ).first()
    return (row[0] if row else 1) == 1


class TooMany(FunctionsError):
    """The form has taken all the submissions it will take for now."""

    status_code = 429
    code = "rate_limited"


# ---------------------------------------------------------------------------
# Public: the schema
# ---------------------------------------------------------------------------


def public_form(db: Session, slug: str) -> FnWebForm:
    """An active form whose organisation runs Functions (shadow or live);
    otherwise NotFound."""
    if not _SLUG.match(slug or ""):
        raise NotFound("Form not found.")
    form = db.query(FnWebForm).filter(FnWebForm.slug == slug).first()
    if form is None or not form.active:
        raise NotFound("Form not found.")
    mode = (
        db.query(Organization.functions_mode)
        .filter(Organization.id == form.organization_id)
        .scalar()
    )
    if mode not in ("shadow", "live"):
        raise NotFound("Form not found.")
    return form


def _fields(db: Session, form: FnWebForm) -> list[FnWebFormField]:
    return (
        db.query(FnWebFormField)
        .filter(
            FnWebFormField.organization_id == form.organization_id,
            FnWebFormField.form_id == form.id,
        )
        .order_by(FnWebFormField.sort_order, FnWebFormField.created_at)
        .all()
    )


def field_dict(f: FnWebFormField) -> dict:
    return {
        "id": f.id,
        "key": f.key,
        "label": f.label,
        "type": f.type,
        "required": f.required,
        "options": f.options,
        "placeholder": f.placeholder,
        "help_text": f.help_text,
        "maps_to": f.maps_to,
        "sort_order": f.sort_order,
    }


def _intro(html: str | None) -> str | None:
    """The intro, re-sanitised; one the sanitiser refuses (e.g. imported and
    absurdly nested) is left out rather than failing the form."""
    if not html:
        return None
    try:
        return sanitize_html(html)[0]
    except Invalid:
        return None


def schema(db: Session, form: FnWebForm, *, now: dt.datetime | None = None) -> dict:
    """What the form page and ``forms.js`` render — no ids, no recipients."""
    fields = [
        {
            k: v
            for k, v in field_dict(f).items()
            if k not in ("id", "maps_to", "sort_order")
        }
        for f in _fields(db, form)
    ]
    venue = (
        db.query(Venue.name)
        .filter(
            Venue.id == form.venue_id, Venue.organization_id == form.organization_id
        )
        .scalar()
    )
    return {
        "form": {
            "slug": form.slug,
            "name": form.name,
            "venue_name": venue,
            "intro_html": _intro(form.intro_html),
            "submit_label": form.submit_label or "Send enquiry",
            "success_message": form.success_message or DEFAULT_SUCCESS,
            "theme": form.theme or {},
            "honeypot_field": form.honeypot_field or "website",
        },
        "fields": fields,
        "form_token": issue_form_token(form, now),
    }


# ---------------------------------------------------------------------------
# Public: a submission
# ---------------------------------------------------------------------------


class FormInvalid(Invalid):
    """The answers don't fit the form; ``errors`` maps field key -> message."""

    def __init__(self, errors: dict[str, str]):
        first = next(iter(errors.values()))
        super().__init__(first)
        self.errors = errors


def _option_values(f: FnWebFormField) -> list[str]:
    out = []
    for o in f.options or []:
        if isinstance(o, dict):
            out.append(str(o.get("value", o.get("label", ""))))
        else:
            out.append(str(o))
    return out


def _option_label(f: FnWebFormField, value) -> str:
    for o in f.options or []:
        if isinstance(o, dict) and str(o.get("value")) == str(value):
            return str(o.get("label") or value)
    return str(value)


class _Bad(ValueError):
    """An answer that doesn't fit its field; the message completes
    "<label> ..." for the person filling the form in."""


def _short(value, kind: str) -> str:
    """``value`` as text when it is a plain value within the type's cap —
    the first thing every answer goes through, before any parsing or regex."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise _Bad("must be text")
    text = value if isinstance(value, str) else str(value)
    if len(text) > _MAX_CHARS[kind]:
        raise _Bad("is too long")
    return text


def _date(value) -> str:
    text = _short(value, "date")
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise _Bad("needs a date like 2026-12-04") from exc


def _time(value) -> str:
    text = _short(value or "", "time").strip()
    if text and not _TIME.match(text):
        raise _Bad("needs times like 19:30")
    if text:
        try:
            dt.time.fromisoformat(text)
        except ValueError as exc:
            raise _Bad("needs times like 19:30") from exc
    return text


def _clean_answer(f: FnWebFormField, raw):
    """The answer in its stored shape, or ``_Bad(message)``. Sizes are
    checked first (``_short``): nothing unbounded reaches a parser."""
    kind = f.type
    if kind == "name" and isinstance(raw, dict):
        parts = []
        for part in ("first", "last"):
            value = raw.get(part) or ""
            if not isinstance(value, str) or len(value) > _NAME_PART:
                raise _Bad("is too long" if isinstance(value, str) else "must be text")
            parts.append(value.strip())
        raw = " ".join(parts).strip()
    if kind in ("name", "text", "long_text", "phone"):
        text = _short(raw, kind).strip()
        if kind == "phone" and text and not re.search(r"\d", text):
            raise _Bad("is not a phone number")
        return text
    if kind == "email":
        try:
            return normalise_email(_short(raw, "email"))
        except Invalid as exc:
            raise _Bad("is not an email address") from exc
    if kind == "number":
        try:
            number = float(_short(raw, "number"))
        except ValueError as exc:
            raise _Bad("must be a number") from exc
        if not math.isfinite(number) or abs(number) > 1e9:
            raise _Bad("must be a number")
        limits = f.options if isinstance(f.options, dict) else {}
        if limits.get("min") is not None and number < float(limits["min"]):
            raise _Bad("is too small")
        if limits.get("max") is not None and number > float(limits["max"]):
            raise _Bad("is too large")
        return int(number) if number.is_integer() else number
    if kind == "date":
        return _date(raw)
    if kind == "dates":
        values = raw if isinstance(raw, list) else [raw]
        if len(values) > MAX_DATES:
            raise _Bad("has too many dates")
        return list(dict.fromkeys(_date(v) for v in values))
    if kind == "time_range":
        if not isinstance(raw, dict):
            raise _Bad("needs a start and an end time")
        start = _time(raw.get("start"))
        end = _time(raw.get("end") or raw.get("finish"))
        if f.required and not (start and end):
            raise _Bad("needs a start and an end time")
        return {"start": start or None, "end": end or None}
    if kind == "choice":
        value = _short(raw, "choice")
        allowed = _option_values(f)
        if allowed and value not in allowed:
            raise _Bad("is not one of the choices")
        return value
    if kind == "checkbox":
        allowed = _option_values(f)
        if not allowed:
            if not isinstance(raw, bool):
                raise _Bad("must be ticked or not")
            if f.required and not raw:
                raise _Bad("must be ticked")
            return raw
        values = raw if isinstance(raw, list) else [raw]
        if len(values) > len(allowed):
            raise _Bad("has more choices than there are")
        values = [_short(v, "choice") for v in values]
        if any(v not in allowed for v in values):
            raise _Bad("is not one of the choices")
        return list(dict.fromkeys(values))
    raise _Bad("is not a field this form has")


def validate_answers(fields: list[FnWebFormField], payload: dict) -> dict:
    """``{key: cleaned answer}`` for every non-section field (None when
    blank), or ``FormInvalid`` with a message per failing field."""
    errors: dict[str, str] = {}
    out: dict = {}
    for f in fields:
        if f.type == "section":
            continue
        raw = payload.get(f.key)
        if raw is None or raw == "" or raw == [] or raw == {}:
            if f.required:
                errors[f.key] = f"{f.label} is required."
            out[f.key] = None
            continue
        try:
            value = _clean_answer(f, raw)
        except _Bad as exc:
            errors[f.key] = f"{f.label} {exc}."
            continue
        except (ValueError, TypeError):
            errors[f.key] = f"{f.label} is not valid."
            continue
        if f.required and value in ("", [], None):
            errors[f.key] = f"{f.label} is required."
        out[f.key] = value
    if errors:
        raise FormInvalid(errors)
    return out


def _display(f: FnWebFormField, value) -> str:
    if value is None or value == "":
        return ""
    if f.type == "date":
        return dt.date.fromisoformat(value).strftime("%a %d %b %Y")
    if f.type == "dates":
        return ", ".join(
            dt.date.fromisoformat(v).strftime("%a %d %b %Y") for v in value
        )
    if f.type == "time_range":
        return f"{value.get('start') or '?'} – {value.get('end') or '?'}"
    if f.type == "choice":
        return _option_label(f, value)
    if f.type == "checkbox":
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return ", ".join(_option_label(f, v) for v in value)
    return str(value)


def _attribution(raw) -> dict:
    values = raw if isinstance(raw, dict) else {}
    out = {}
    for key in ATTRIBUTION_KEYS:
        v = values.get(key)
        if isinstance(v, str) and v.strip():
            limit = 1000 if key in ("referrer", "landing_page") else 500
            out[key] = v.strip()[:limit]
    return out


def _ref_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "NRM-" + "".join(secrets.choice(alphabet) for _ in range(6))


@dataclass
class Submitted:
    submission: FnFormSubmission
    success_message: str
    notices: list = field(default_factory=list)


def submit(
    db: Session,
    form: FnWebForm,
    body: dict,
    *,
    ip_hash_value: str,
    user_agent: str | None = None,
    now: dt.datetime | None = None,
) -> Submitted:
    """Store one submission and, by mode, deliver it (shadow) or turn it into
    an enquiry (live). The emails go in ``notices``, to send after commit.

    Refused (nothing stored): a missing/forged/stale ``form_token``, answers
    that don't fit the form, and anything past ``FORM_DAY_LIMIT``. Stored as
    spam (answered like a real lead, delivered nowhere): a reused token (a
    double-click or a bot replaying one fetch), a too-quick submit, a quick
    filled honeypot."""
    now = now or _now()
    if not isinstance(body, dict):
        raise Invalid("Send the form as an object.")
    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise Invalid("Send the answers in 'payload'.")
    token = read_form_token(form, body.get("form_token"), now)
    if token is None:
        raise Invalid("This form has expired — reload the page and try again.")
    today = (
        db.query(func.count(FnFormSubmission.id))
        .filter(
            FnFormSubmission.organization_id == form.organization_id,
            FnFormSubmission.form_id == form.id,
            FnFormSubmission.submitted_at > now - dt.timedelta(days=1),
        )
        .scalar()
    )
    if today >= FORM_DAY_LIMIT:
        raise TooMany(
            "This form can't take more enquiries right now — please try again later "
            "or contact the venue directly."
        )
    honeypot = form.honeypot_field or "website"
    trap = payload.get(honeypot)
    trapped = isinstance(trap, str) and trap.strip() != ""
    spam = None
    if token.age < MIN_FILL:
        spam = "too_fast"
    elif trapped and token.age < HONEYPOT_FAST:
        spam = "honeypot"
    fields = _fields(db, form)
    # Validate before the token is spent: a refused submission (a typo in
    # the email) must leave it good for the corrected one.
    answers = None if spam else validate_answers(fields, payload)
    # Spent only now, when a row is about to be stored in this transaction.
    if not _first_use(db, form, token):
        spam = "token_reused"
    success = form.success_message or DEFAULT_SUCCESS
    row = FnFormSubmission(
        organization_id=form.organization_id,
        form_id=form.id,
        venue_id=form.venue_id,
        submitted_at=now,
        ip_hash=ip_hash_value,
        user_agent=(user_agent or "")[:500] or None,
        ref_code=_ref_code(),
        **_attribution(body.get("tracking")),
    )
    if spam:
        # Kept for tuning, answered like a real lead, delivered nowhere.
        row.spam_reason = spam
        row.payload = {
            str(k)[:100]: str(v)[:200] for k, v in list(payload.items())[:MAX_FIELDS]
        }
        row.delivery_status = "skipped"
        db.add(row)
        flush(db)
        return Submitted(row, success)

    row.payload = answers
    row.delivery_status = "pending"
    db.add(row)
    flush(db)
    mode = (
        db.query(Organization.functions_mode)
        .filter(Organization.id == form.organization_id)
        .scalar()
    )
    notice = None
    if mode == "live":
        try:
            # A savepoint: whatever goes wrong making the enquiry — a rule, a
            # bad mapping on an imported form, a bug — the submission stays,
            # goes to the venue mailbox like a shadow-mode one, and the
            # customer is thanked. A lead is never lost to our failure.
            with db.begin_nested():
                enquiry = _create_enquiry(db, form, fields, answers, row)
                row.enquiry_id = enquiry.id
                flush(db)
            notice = _lead_notice(db, form, fields, answers, row, enquiry.id)
        except Exception as exc:  # noqa: BLE001 — see above
            reason = exc.message if isinstance(exc, FunctionsError) else repr(exc)
            logger.exception(
                "web form %s: enquiry not created; emailing the venue instead", form.id
            )
            if row.enquiry_id is None:
                row.delivery_error = f"Enquiry not created: {reason}"[:500]
                flush(db)
    if notice is None:
        notice = _shadow_notice(db, form, fields, answers, row, now)
    return Submitted(row, success, [notice])


def _first(fields, answers, *, maps_to: str, of_type: str | None = None):
    for f in fields:
        if f.maps_to == maps_to and answers.get(f.key) not in (None, "", []):
            return answers[f.key], f
    if of_type:
        for f in fields:
            if f.type == of_type and answers.get(f.key) not in (None, "", []):
                return answers[f.key], f
    return None, None


def _dates_in(value) -> list[dt.date]:
    """Dates in an answer of any shape (a date string, or a list of them),
    sorted; anything else is ignored."""
    values = value if isinstance(value, list) else [value]
    out = []
    for v in values:
        if isinstance(v, str):
            try:
                out.append(dt.date.fromisoformat(v))
            except ValueError:
                continue
    return sorted(out)


def _time_in(value) -> dt.time | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.time.fromisoformat(value)
    except ValueError:
        return None


def lead_from(form: FnWebForm, fields, answers) -> dict:
    """What the answers say about the lead — contact, party size, dates —
    by each field's ``maps_to`` (and, for name / email / phone, the first
    field of that type)."""
    name, _ = _first(fields, answers, maps_to="contact.name", of_type="name")
    email, _ = _first(fields, answers, maps_to="contact.email", of_type="email")
    phone, _ = _first(fields, answers, maps_to="contact.phone", of_type="phone")
    company, _ = _first(fields, answers, maps_to="contact.company")
    guests, _ = _first(fields, answers, maps_to="guests")
    date, _ = _first(fields, answers, maps_to="preferred_date")
    dates, _ = _first(fields, answers, maps_to="preferred_dates")
    times, _ = _first(fields, answers, maps_to="preferred_time")
    booking, _ = _first(fields, answers, maps_to="booking_type")
    subject, _ = _first(fields, answers, maps_to="subject")
    note, _ = _first(fields, answers, maps_to="client_note", of_type="long_text")
    # A field's maps_to need not match its type (an imported form maps a
    # "dates" list to preferred_date): read what's there, drop what isn't.
    parsed = _dates_in(date) or _dates_in(dates)
    start, end = (parsed[0], parsed[-1]) if parsed else (None, None)
    t_start = t_end = None
    if isinstance(times, dict):
        t_start, t_end = _time_in(times.get("start")), _time_in(times.get("end"))
    try:
        party = int(float(guests)) if guests not in (None, "") else None
    except (TypeError, ValueError, OverflowError):
        party = None
    try:
        email = normalise_email(email) if isinstance(email, str) else None
    except Invalid:
        email = None  # a text field mapped to the email: not an address
    return {
        "name": str(name) if name else None,
        "email": email,
        "phone": str(phone) if phone else None,
        "company": str(company) if company else None,
        "guests": party if party is None or party >= 0 else None,
        "preferred_date_start": start,
        "preferred_date_end": end,
        "preferred_start_time": t_start,
        "preferred_end_time": t_end,
        "booking_type": booking if booking in BOOKING_TYPES else form.booking_type,
        "subject": str(subject) if subject else None,
        "client_note": str(note) if note else None,
    }


def _rows(fields, answers) -> list[tuple[str, str]]:
    rows = []
    for f in fields:
        if f.type == "section":
            rows.append((f"— {f.label} —", ""))
            continue
        rows.append((f.label, _display(f, answers.get(f.key))))
    return rows


def _create_enquiry(db, form, fields, answers, submission):
    lead = lead_from(form, fields, answers)
    ctx = for_org(db, form.organization_id, actor_label="web form")
    who = lead["name"] or lead["email"] or "Web enquiry"
    summary = "\n".join(
        f"{label}: {value}" for label, value in _rows(fields, answers) if value
    )
    return enquiries_svc.create_enquiry(
        db,
        ctx,
        contact={
            "name": lead["name"]
            or (lead["email"] or "").split("@")[0]
            or "Web enquiry",
            "email": lead["email"],
            "phone": lead["phone"],
            "company": lead["company"],
        },
        venue_id=form.venue_id,
        booking_type=lead["booking_type"],
        subject=lead["subject"] or f"{form.name} — {who}",
        guests=lead["guests"],
        preferred_date_start=lead["preferred_date_start"],
        preferred_date_end=lead["preferred_date_end"],
        preferred_start_time=lead["preferred_start_time"],
        preferred_end_time=lead["preferred_end_time"],
        preferred_ends_next_day=infer_ends_next_day(
            lead["preferred_start_time"], lead["preferred_end_time"]
        ),
        source="web_form",
        attribution={
            k: getattr(submission, k)
            for k in enquiries_svc.ATTRIBUTION_FIELDS
            if getattr(submission, k, None)
        },
        client_note=lead["client_note"],
        notes=f"Web form '{form.name}' (ref {submission.ref_code}):\n{summary}",
    )


def _reply_to(form, fields, answers) -> str | None:
    if form.reply_to_field_key:
        value = answers.get(form.reply_to_field_key)
        if isinstance(value, str) and "@" in value:
            return value
    value, _ = _first(fields, answers, maps_to="contact.email", of_type="email")
    return value


def _mailbox_address(db: Session, form: FnWebForm) -> str | None:
    """Where shadow mode delivers: the form's recipient, else the venue's
    connected mailbox, else its first notify address."""
    if form.recipient_email:
        return form.recipient_email
    mailbox = (
        db.query(FnMailboxAccount.google_email)
        .filter(
            FnMailboxAccount.organization_id == form.organization_id,
            FnMailboxAccount.venue_id == form.venue_id,
        )
        .order_by(FnMailboxAccount.created_at)
        .first()
    )
    if mailbox and mailbox[0]:
        return mailbox[0]
    notify_to = (
        db.query(FnVenueFunctionSettings.notify_emails)
        .filter(
            FnVenueFunctionSettings.organization_id == form.organization_id,
            FnVenueFunctionSettings.venue_id == form.venue_id,
        )
        .scalar()
    )
    return next((a for a in notify_to or [] if isinstance(a, str) and "@" in a), None)


def render_subject(form, fields, answers, *, now, tz=None) -> str:
    """The shadow email's subject: the form's template (``{key}`` or
    ``{{key}}``, plus ``form_name`` / ``date`` / ``name``), then a short
    timestamp so Gmail never threads two leads together."""
    lead = lead_from(form, fields, answers)
    local = local_now(tz, now) if tz else now

    def brief(value) -> str:
        # Answers are the customer's: one line, short, before any pattern
        # below runs over them.
        return " ".join(str(value or "").split())[:100]

    values = {
        f.key: brief(_display(f, answers.get(f.key)))
        for f in fields
        if f.type != "section"
    }
    values.update(
        form_name=brief(form.name),
        date=local.date().isoformat(),
        name=brief(lead["name"]),
    )

    def fill(match):
        return values.get(match.group(1) or match.group(2), "")

    subject = ""
    if form.subject_template:
        # One pass over the TEMPLATE: an answer containing "{email}" is text.
        subject = re.sub(
            r"\{\{\s*([\w.-]+)\s*\}\}|\{\s*([\w.-]+)\s*\}",
            fill,
            form.subject_template[:200],
        )
        # Tidy separators an empty placeholder left behind.
        subject = re.sub(r"\s*[—–-]\s*(?=[—–-]|$)", "", subject)
        subject = re.sub(r"^\s*[—–-]\s*", "", subject)
        subject = re.sub(r"\s{2,}", " ", subject).strip()
    if not subject:
        subject = f"{form.name} — {lead['name'] or lead['email'] or 'Web enquiry'}"
    stamp = local.strftime("%d %b %H:%M")
    return re.sub(r"[\r\n]+", " ", f"{subject} · {stamp}")[:250]


def _record_delivery(submission: FnFormSubmission, result: notify.Delivery) -> None:
    submission.delivery_status = {"sent": "sent", "failed": "failed"}.get(
        result.status, "skipped"
    )
    # Keep the note that the enquiry couldn't be made beside the email result.
    prior = submission.delivery_error or ""
    kept = prior if prior.startswith("Enquiry not created") else None
    submission.delivery_error = "; ".join(e for e in (kept, result.error) if e) or None
    submission.delivery_message_id = result.provider_id


def _venue_tz(db, form):
    tz = db.query(Venue.timezone).filter(Venue.id == form.venue_id).scalar()
    return tz or None


def _shadow_notice(db, form, fields, answers, submission, now):
    to = _mailbox_address(db, form)
    try:
        subject = render_subject(form, fields, answers, now=now, tz=_venue_tz(db, form))
    except Exception:  # noqa: BLE001 — a bad zone or template must not lose the lead
        logger.exception("web form %s: subject template failed", form.id)
        subject = f"{form.name} — Web enquiry · {now.strftime('%d %b %H:%M')}"
    args = dict(
        org_id=form.organization_id,
        venue_id=form.venue_id,
        to=to,
        subject=subject,
        form_name=form.name,
        ref_code=submission.ref_code,
        rows=_rows(fields, answers) + _source_rows(submission),
        reply_to=_reply_to(form, fields, answers),
    )
    submission_id = submission.id

    def send(session: Session):
        result = notify.form_submission(session, **args)
        row = session.get(FnFormSubmission, submission_id)
        if row is not None:
            _record_delivery(row, result)
            session.flush()
        return result

    return send


def _lead_notice(db, form, fields, answers, submission, enquiry_id):
    args = dict(
        org_id=form.organization_id,
        venue_id=form.venue_id,
        form_name=form.name,
        enquiry_id=enquiry_id,
        rows=_rows(fields, answers) + _source_rows(submission),
        reply_to=_reply_to(form, fields, answers),
        fallback_to=_mailbox_address(db, form),
    )
    submission_id = submission.id

    def send(session: Session):
        result = notify.form_lead(session, **args)
        row = session.get(FnFormSubmission, submission_id)
        if row is not None:
            _record_delivery(row, result)
            session.flush()
        return result

    return send


def _source_rows(submission: FnFormSubmission) -> list[tuple[str, str]]:
    rows = []
    for key, label in (
        ("utm_source", "Source"),
        ("utm_medium", "Medium"),
        ("utm_campaign", "Campaign"),
        ("gclid", "Google click"),
        ("referrer", "Referrer"),
        ("landing_page", "Landing page"),
    ):
        value = getattr(submission, key)
        if value:
            rows.append((label, value))
    return rows


# ---------------------------------------------------------------------------
# Staff: the builder
# ---------------------------------------------------------------------------


def list_forms(db: Session, ctx: FunctionsCtx, *, venue_id: str | None = None):
    q = db.query(FnWebForm).filter(
        FnWebForm.organization_id == ctx.org_id, ctx.venue_clause(FnWebForm.venue_id)
    )
    if venue_id:
        q = q.filter(FnWebForm.venue_id == venue_id)
    return q.order_by(FnWebForm.name).all()


def get_form(
    db: Session, ctx: FunctionsCtx, form_id: str, *, for_update: bool = False
) -> FnWebForm:
    return load(db, ctx, FnWebForm, form_id, label="Form", for_update=for_update)


def form_fields(db: Session, ctx: FunctionsCtx, form_id: str) -> list[FnWebFormField]:
    return _fields(db, get_form(db, ctx, form_id))


def submission_counts(db: Session, ctx: FunctionsCtx, form_ids: list[str]) -> dict:
    """``{form_id: (submissions, last_submitted_at)}``, spam excluded."""
    if not form_ids:
        return {}
    rows = (
        db.query(
            FnFormSubmission.form_id,
            func.count(FnFormSubmission.id),
            func.max(FnFormSubmission.submitted_at),
        )
        .filter(
            FnFormSubmission.organization_id == ctx.org_id,
            FnFormSubmission.form_id.in_(form_ids),
            FnFormSubmission.spam_reason.is_(None),
        )
        .group_by(FnFormSubmission.form_id)
        .all()
    )
    return {r[0]: (r[1], r[2]) for r in rows}


#: Settings and field attributes that are text (or nothing).
_TEXT_SETTINGS = frozenset(FORM_SETTINGS) - {"active", "theme"}
_TEXT_FIELD_KEYS = ("key", "label", "type", "placeholder", "help_text", "maps_to")


def _require_texts(values: dict, names) -> None:
    for name in names:
        value = values.get(name)
        if value is not None and not isinstance(value, str):
            raise Invalid(f"{name} must be text.")


def _clean_settings(db, ctx, form: FnWebForm | None, values: dict) -> dict:
    unknown = set(values) - set(FORM_SETTINGS)
    if unknown:
        raise Invalid(f"Cannot set: {', '.join(sorted(unknown))}.")
    _require_texts(values, _TEXT_SETTINGS)
    out = dict(values)
    if "slug" in out:
        slug = (out["slug"] or "").strip().lower()
        if not _SLUG.match(slug):
            raise Invalid(
                "The address must be lowercase letters, numbers and dashes (up to 64)."
            )
        taken = (
            db.query(FnWebForm.id)
            .filter(FnWebForm.slug == slug, FnWebForm.id != (form.id if form else ""))
            .first()
        )
        if taken is not None:
            raise Conflict("That form address is taken — choose another.")
        out["slug"] = slug
    if "name" in out:
        out["name"] = require_text(out["name"], "A form name")[:200]
    if "active" in out:
        out["active"] = bool(out["active"])
    if "theme" in out:
        out["theme"] = _clean_theme(out["theme"])
    if "intro_html" in out:
        intro = (out["intro_html"] or "").strip()
        out["intro_html"] = sanitize_html(intro)[0] if intro else None
    if "submit_label" in out:
        out["submit_label"] = (out["submit_label"] or "").strip()[:60] or "Send enquiry"
    if "success_message" in out:
        out["success_message"] = (out["success_message"] or "").strip()[:2000] or None
    if "recipient_email" in out:
        out["recipient_email"] = (
            normalise_email(out["recipient_email"]) if out["recipient_email"] else None
        )
    if "subject_template" in out:
        out["subject_template"] = (out["subject_template"] or "").strip()[:200] or None
    if "reply_to_field_key" in out:
        out["reply_to_field_key"] = (out["reply_to_field_key"] or "").strip() or None
    if "honeypot_field" in out:
        hp = (out["honeypot_field"] or "").strip() or "website"
        if not _KEY.match(hp):
            raise Invalid("The honeypot field name must be a simple lowercase word.")
        out["honeypot_field"] = hp
    if out.get("booking_type") is not None and out["booking_type"] not in BOOKING_TYPES:
        raise Invalid(f"Booking type must be one of {', '.join(BOOKING_TYPES)}.")
    return out


def _clean_theme(value) -> dict:
    """The theme as forms.js reads it: keys from ``THEME_KEYS`` (Orbit's
    camelCase names normalised to snake_case, exactly as forms.js does), each
    value a short plain CSS value — no markup, braces, semicolons or url() to
    break out of the custom property it lands in. Unknown keys are refused
    by name rather than saved to be ignored."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise Invalid("The theme must be a set of named values.")
    out, unknown = {}, []
    for raw_key, v in value.items():
        key = _CAMEL.sub(r"\1_\2", str(raw_key)).lower()
        if key not in THEME_KEYS:
            unknown.append(str(raw_key)[:40])
            continue
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            raise Invalid(f"Theme setting '{raw_key}' must be a plain value.")
        text = str(v).strip()
        if len(text) > 200 or _THEME_BAD.search(text) or "url(" in text.lower():
            raise Invalid(f"Theme setting '{raw_key}' isn't a value Norm can use.")
        out[key] = text
    if unknown:
        shown = ", ".join(sorted(unknown)[:10]) + (" …" if len(unknown) > 10 else "")
        raise Invalid(f"Unknown theme settings: {shown}.")
    return out


def _clean_fields(fields: list) -> list[dict]:
    if not isinstance(fields, list):
        raise Invalid("Fields must be a list.")
    if len(fields) > MAX_FIELDS:
        raise Invalid(f"A form can have up to {MAX_FIELDS} fields.")
    out, keys = [], set()
    for i, raw in enumerate(fields):
        if not isinstance(raw, dict):
            raise Invalid("Each field must be an object.")
        _require_texts(raw, _TEXT_FIELD_KEYS)
        kind = raw.get("type")
        if kind not in FORM_FIELD_TYPES:
            raise Invalid(f"Field type must be one of {', '.join(FORM_FIELD_TYPES)}.")
        key = (raw.get("key") or "").strip() or (
            f"section_{i + 1}" if kind == "section" else ""
        )
        if not _KEY.match(key):
            raise Invalid(
                f"Field key '{key}' must start with a letter and use lowercase "
                "letters, numbers and underscores."
            )
        if key in keys:
            raise Invalid(f"Two fields share the key '{key}'.")
        keys.add(key)
        maps_to = raw.get("maps_to") or None
        if maps_to is not None and maps_to not in MAPS_TO:
            raise Invalid(f"maps_to must be one of {', '.join(MAPS_TO)}.")
        out.append(
            {
                "key": key,
                "label": require_text(raw.get("label"), "A field label")[:200],
                "type": kind,
                "required": bool(raw.get("required")) and kind != "section",
                "options": _clean_options(kind, raw.get("options")),
                "placeholder": (raw.get("placeholder") or "").strip()[:200] or None,
                "help_text": (raw.get("help_text") or "").strip()[:500] or None,
                "maps_to": maps_to,
                "sort_order": i,
            }
        )
    return out


def _clean_options(kind: str, value):
    if kind == "number":
        if not value:
            return None
        if not isinstance(value, dict) or set(value) - {"min", "max"}:
            raise Invalid("A number field's options are {min, max}.")
        return {k: float(v) for k, v in value.items() if v is not None}
    if kind not in ("choice", "checkbox"):
        return None
    if not value:
        if kind == "choice":
            raise Invalid("A choice field needs its choices.")
        return None
    if not isinstance(value, list) or len(value) > MAX_OPTIONS:
        raise Invalid(f"Choices must be a list of up to {MAX_OPTIONS}.")
    out = []
    for o in value:
        if isinstance(o, dict):
            label = str(o.get("label") or o.get("value") or "").strip()
            v = str(o.get("value") or label).strip()
        else:
            label = v = str(o).strip()
        if not label:
            raise Invalid("A choice can't be blank.")
        out.append({"value": v[:200], "label": label[:200]})
    return out


def create_form(
    db: Session,
    ctx: FunctionsCtx,
    *,
    venue_id: str,
    name: str,
    slug: str | None = None,
    fields: list | None = None,
    **values,
) -> FnWebForm:
    require_live(ctx)
    require_staff(ctx)
    if not venue_id:
        raise Invalid("Choose the venue this form is for.")
    require_venue(ctx, venue_id)
    slug = slug or _slug_for(db, name)
    clean = _clean_settings(db, ctx, None, {"slug": slug, "name": name, **values})
    form = FnWebForm(organization_id=ctx.org_id, venue_id=venue_id, **clean)
    db.add(form)
    flush(db)
    _write_fields(db, ctx, form, _clean_fields(fields or _DEFAULT_FIELDS))
    _check_reply_to(db, form)
    return form


#: A new form's starting fields (staff edit from here).
_DEFAULT_FIELDS = [
    {
        "key": "name",
        "label": "Name",
        "type": "name",
        "required": True,
        "maps_to": "contact.name",
    },
    {
        "key": "email",
        "label": "Email",
        "type": "email",
        "required": True,
        "maps_to": "contact.email",
    },
    {"key": "phone", "label": "Phone", "type": "phone", "maps_to": "contact.phone"},
    {
        "key": "event_date",
        "label": "Event date",
        "type": "date",
        "maps_to": "preferred_date",
    },
    {
        "key": "guests",
        "label": "Number of guests",
        "type": "number",
        "maps_to": "guests",
    },
    {
        "key": "message",
        "label": "Tell us about your event",
        "type": "long_text",
        "maps_to": "client_note",
    },
]


def _slug_for(db: Session, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:48] or "enquiry"
    slug = base
    while db.query(FnWebForm.id).filter(FnWebForm.slug == slug).first() is not None:
        slug = f"{base}-{secrets.token_hex(2)}"
    return slug


def update_form(
    db: Session,
    ctx: FunctionsCtx,
    form_id: str,
    *,
    expected_version: int | None = None,
    **values,
) -> dict:
    require_live(ctx)
    require_staff(ctx)
    form = get_form(db, ctx, form_id, for_update=True)
    check_version(form, expected_version)
    changes = apply_fields(form, _clean_settings(db, ctx, form, values), FORM_SETTINGS)
    flush(db)
    _check_reply_to(db, form)
    return changes


def replace_fields(
    db: Session,
    ctx: FunctionsCtx,
    form_id: str,
    fields: list,
    *,
    expected_version: int | None = None,
) -> list[FnWebFormField]:
    """Replace the form's fields (the builder saves the whole list). Bumps the
    form's version so two editors conflict properly."""
    require_live(ctx)
    require_staff(ctx)
    form = get_form(db, ctx, form_id, for_update=True)
    check_version(form, expected_version)
    clean = _clean_fields(fields)
    for existing in _fields(db, form):
        db.delete(existing)
    flush(db)
    _write_fields(db, ctx, form, clean)
    form.updated_at = _now()
    flush(db)
    _check_reply_to(db, form)
    return _fields(db, form)


def _write_fields(db, ctx, form, clean: list[dict]) -> None:
    for f in clean:
        db.add(FnWebFormField(organization_id=ctx.org_id, form_id=form.id, **f))
    flush(db)


def _check_reply_to(db, form) -> None:
    """Settings that name fields must agree with the fields: Reply-To is an
    email field, and the honeypot is NOT a real field (or genuine answers to
    it would be taken for a bot's)."""
    kinds = {f.key: f.type for f in _fields(db, form)}
    if form.reply_to_field_key and kinds.get(form.reply_to_field_key) != "email":
        raise Invalid("Reply-To must name one of the form's email fields.")
    if (form.honeypot_field or "website") in kinds:
        raise Invalid("The honeypot name can't be one of the form's field keys.")


def delete_form(db: Session, ctx: FunctionsCtx, form_id: str) -> bool:
    """Delete a form that never received anything; one with submissions is
    switched off instead (they reference it). True when deleted."""
    require_live(ctx)
    require_staff(ctx)
    form = get_form(db, ctx, form_id, for_update=True)
    used = (
        db.query(FnFormSubmission.id)
        .filter(
            FnFormSubmission.organization_id == ctx.org_id,
            FnFormSubmission.form_id == form.id,
        )
        .first()
    )
    if used is not None:
        form.active = False
        flush(db)
        return False
    db.delete(form)
    flush(db)
    return True


def embed_snippets(form: FnWebForm) -> dict:
    """The page URL and the two ways to put the form on a website."""
    base = settings.app_url.rstrip("/")
    url = f"{base}/f/{form.slug}"
    name = html_lib.escape(form.name, quote=True)
    return {
        "url": url,
        "iframe": (
            f'<iframe src="{url}" title="{name}" loading="lazy" '
            'style="width:100%;min-height:760px;border:0;"></iframe>'
        ),
        "script": (
            f'<div data-norm-form="{form.slug}"></div>\n'
            f'<script src="{base}/embed/forms.js" async></script>'
        ),
        "tracker": f'<script src="{base}/embed/track.js" async></script>',
    }


def form_dict(form: FnWebForm, fields: list[FnWebFormField] | None = None) -> dict:
    out = {
        "id": form.id,
        "version": form.version,
        "venue_id": form.venue_id,
        **{k: getattr(form, k) for k in FORM_SETTINGS},
        "embed": embed_snippets(form),
        "created_at": form.created_at.isoformat() if form.created_at else None,
        "updated_at": form.updated_at.isoformat() if form.updated_at else None,
    }
    if fields is not None:
        out["fields"] = [field_dict(f) for f in fields]
    return out


def list_submissions(
    db: Session,
    ctx: FunctionsCtx,
    *,
    form_id: str | None = None,
    venue_id: str | None = None,
    include_spam: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[FnFormSubmission]:
    q = db.query(FnFormSubmission).filter(
        FnFormSubmission.organization_id == ctx.org_id,
        ctx.venue_clause(FnFormSubmission.venue_id),
    )
    if form_id:
        get_form(db, ctx, form_id)
        q = q.filter(FnFormSubmission.form_id == form_id)
    if venue_id:
        q = q.filter(FnFormSubmission.venue_id == venue_id)
    if not include_spam:
        q = q.filter(FnFormSubmission.spam_reason.is_(None))
    return (
        q.order_by(FnFormSubmission.submitted_at.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 500)))
        .all()
    )


def submission_dict(s: FnFormSubmission) -> dict:
    """For staff. The IP hash and user agent stay server-side."""
    return {
        "id": s.id,
        "form_id": s.form_id,
        "venue_id": s.venue_id,
        "payload": s.payload,
        "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
        "ref_code": s.ref_code,
        "attribution": {k: getattr(s, k) for k in ATTRIBUTION_KEYS if getattr(s, k)},
        "spam_reason": s.spam_reason,
        "delivery_status": s.delivery_status,
        "delivery_error": s.delivery_error,
        "enquiry_id": s.enquiry_id,
    }
