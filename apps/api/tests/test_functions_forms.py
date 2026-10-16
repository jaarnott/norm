"""Web forms: the public schema and submit (``/api/public/forms/<slug>``) and
the staff builder (``/api/functions/forms``).

A submission is believed only after a server-measured fill time, a honeypot
that tolerates password managers, a per-IP rate limit and validation against
the form's own fields. Shadow mode delivers it to the venue mailbox the way
Orbit did (Reply-To = the customer); live mode also creates the enquiry.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re
import time
import uuid
from types import SimpleNamespace

import pytest

from app.auth.security import create_access_token
from app.db.functions_models import FnContact, FnEnquiry, FnFormSubmission, FnWebForm
from app.db.models import EmailLog, _now
from app.services.functions import forms as forms_svc
from app.services.functions import notify
from tests.functions_world import add_contact, make_world

FIELDS = [
    {"key": "name", "label": "Name", "type": "name", "required": True},
    {"key": "email", "label": "Email", "type": "email", "required": True},
    {"key": "phone", "label": "Phone", "type": "phone"},
    {"key": "details", "label": "Your event", "type": "section"},
    {
        "key": "event_date",
        "label": "Event date",
        "type": "date",
        "maps_to": "preferred_date",
    },
    {
        "key": "times",
        "label": "Times",
        "type": "time_range",
        "maps_to": "preferred_time",
    },
    {"key": "guests", "label": "Guests", "type": "number", "maps_to": "guests"},
    {
        "key": "kind",
        "label": "Booking type",
        "type": "choice",
        "options": [
            {"value": "group_booking", "label": "Group booking"},
            {"value": "seated_dining", "label": "Seated dining"},
        ],
        "maps_to": "booking_type",
    },
    {
        "key": "extras",
        "label": "Extras",
        "type": "checkbox",
        "options": ["DJ", "Projector"],
    },
    {
        "key": "message",
        "label": "Message",
        "type": "long_text",
        "maps_to": "client_note",
    },
    {
        "key": "terms",
        "label": "I accept the terms",
        "type": "checkbox",
        "required": True,
    },
]
GOOD = {
    "name": {"first": "Casey", "last": "Client"},
    "email": "Casey@Example.com",
    "phone": "021 555 0101",
    "event_date": "2026-12-04",
    "times": {"start": "19:00", "finish": "01:00"},
    "guests": "60",
    "kind": "group_booking",
    "extras": ["DJ"],
    "message": "A 40th for about sixty.",
    "terms": True,
}
TRACKING = {
    "utm_source": "google",
    "utm_medium": "cpc",
    "utm_campaign": "functions-spring",
    "gclid": "Cj0KCQ-test",
    "wbraid": "wb-1",
    "referrer": "https://www.google.com/",
    "landing_page": "https://brewbar.co.nz/functions?gclid=Cj0KCQ-test",
}


@pytest.fixture
def ip(client):
    value = f"192.0.2.{uuid.uuid4().int % 250}-{uuid.uuid4().hex[:8]}"
    client.headers["X-Forwarded-For"] = value
    return value


@pytest.fixture
def f(db_session, client, ip):
    w = make_world(db_session)
    form = forms_svc.create_form(
        db_session,
        w.ctx(w.owner),
        venue_id=w.venue.id,
        name="Brew Bar functions",
        slug=f"brew-{uuid.uuid4().hex[:10]}",
        recipient_email="events@brewbar.example",
        subject_template="{{name}} — {event_date} — {{missing}}",
        reply_to_field_key="email",
        fields=FIELDS,
    )
    db_session.flush()
    return SimpleNamespace(w=w, db=db_session, form=form, ip=ip)


def _post(client, f, payload, *, age=5, tracking=None, token="auto"):
    if token == "auto":
        token = forms_svc.issue_form_token(
            f.form, now=_now() - dt.timedelta(seconds=age)
        )
    body = {"payload": payload, "tracking": tracking or {}}
    if token is not None:
        body["form_token"] = token
    return client.post(
        f"/api/public/forms/{f.form.slug}/submit",
        content=json.dumps(body),
        headers={
            "Content-Type": "text/plain;charset=UTF-8",
            "User-Agent": "TestBrowser/1",
        },
    )


def _subs(f):
    return f.db.query(FnFormSubmission).filter_by(form_id=f.form.id).all()


def _emails(ns, template):
    """This test's own email log (the shared dev database has others')."""
    return (
        ns.db.query(EmailLog)
        .filter(
            EmailLog.template_name == f"functions:{template}",
            EmailLog.organization_id == ns.w.org.id,
        )
        .all()
    )


def _tag() -> str:
    """Form slugs are globally unique, and other runs share this database:
    a random tail keeps two runs from queueing on the same slug."""
    return uuid.uuid4().hex[:8]


def _auth(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


class TestPublicSchema:
    def test_schema_renders_without_private_settings(self, client, f):
        r = client.get(f"/api/public/forms/{f.form.slug}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["form"]["name"] == "Brew Bar functions"
        assert body["form"]["venue_name"] == "Brew Bar"
        assert [x["key"] for x in body["fields"]][:3] == ["name", "email", "phone"]
        assert "recipient_email" not in r.text and "events@brewbar" not in r.text
        assert "maps_to" not in r.text
        assert forms_svc.read_form_token(f.form, body["form_token"]) is not None
        assert r.headers["access-control-allow-origin"] == "*"
        assert r.headers["cache-control"] == "no-store"

    def test_inactive_and_switched_off_forms_are_404_shadow_is_up(self, client, f):
        f.w.org.functions_mode = "shadow"
        f.db.flush()
        assert client.get(f"/api/public/forms/{f.form.slug}").status_code == 200
        f.w.org.functions_mode = "off"
        f.db.flush()
        assert client.get(f"/api/public/forms/{f.form.slug}").status_code == 404
        f.w.org.functions_mode = "live"
        f.form.active = False
        f.db.flush()
        r = client.get(f"/api/public/forms/{f.form.slug}")
        assert r.status_code == 404
        assert r.headers["access-control-allow-origin"] == "*"
        assert client.get("/api/public/forms/no-such-form").status_code == 404


class TestSubmitLive:
    def test_a_live_submission_creates_the_contact_and_enquiry(self, client, f):
        r = _post(client, f, GOOD, tracking=TRACKING)
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "success_message": forms_svc.DEFAULT_SUCCESS}
        assert r.headers["access-control-allow-origin"] == "*"
        (sub,) = _subs(f)
        assert sub.spam_reason is None
        assert sub.payload["name"] == "Casey Client"
        assert sub.payload["email"] == "casey@example.com"
        assert sub.payload["guests"] == 60
        assert sub.payload["times"] == {"start": "19:00", "end": "01:00"}
        assert (
            sub.utm_source == "google"
            and sub.gclid == "Cj0KCQ-test"
            and sub.wbraid == "wb-1"
        )
        assert sub.user_agent == "TestBrowser/1"
        assert sub.ref_code.startswith("NRM-")
        enquiry = f.db.get(FnEnquiry, sub.enquiry_id)
        assert enquiry.source == "web_form" and enquiry.created_by_label == "web form"
        assert enquiry.venue_id == f.w.venue.id
        assert enquiry.booking_type == "group_booking"
        assert enquiry.guests == 60
        assert (
            enquiry.preferred_date_start
            == enquiry.preferred_date_end
            == dt.date(2026, 12, 4)
        )
        assert enquiry.preferred_start_time == dt.time(19, 0)
        assert enquiry.preferred_ends_next_day is True
        assert enquiry.client_note == "A 40th for about sixty."
        assert (
            enquiry.utm_campaign == "functions-spring"
            and enquiry.gclid == "Cj0KCQ-test"
        )
        assert "Casey Client" in enquiry.notes
        contact = f.db.get(FnContact, enquiry.contact_id)
        assert contact.name == "Casey Client" and contact.phone == "021 555 0101"
        sent = _emails(f, "form_lead")
        assert len(sent) == 1 and sent[0].to_addresses == ["events@brewbar.example"]
        assert sub.delivery_status == "skipped"  # suppressed outside production

    def test_an_existing_contact_is_matched_by_email(self, client, f):
        existing = add_contact(
            f.db, f.w.org.id, name="Casey C", email="casey@example.com"
        )
        assert _post(client, f, GOOD).status_code == 200
        (sub,) = _subs(f)
        assert f.db.get(FnEnquiry, sub.enquiry_id).contact_id == existing.id

    def test_if_the_enquiry_cannot_be_made_the_lead_still_reaches_the_venue(
        self, client, f, monkeypatch
    ):
        def refuse(*args, **kwargs):
            raise forms_svc.Invalid("No.")

        monkeypatch.setattr(forms_svc, "_create_enquiry", refuse)
        assert _post(client, f, GOOD).status_code == 200
        (sub,) = _subs(f)
        assert sub.enquiry_id is None and sub.payload["email"] == "casey@example.com"
        assert len(_emails(f, "form_submission")) == 1
        assert _emails(f, "form_lead") == []

    def test_any_failure_making_the_enquiry_keeps_and_delivers_the_lead(
        self, client, f, monkeypatch
    ):
        # Not a domain refusal — a bug-shaped failure (F9 met a TypeError from
        # an imported form's mapping): still stored, emailed, thanked.
        def crash(*args, **kwargs):
            raise TypeError("fromisoformat: argument must be str")

        monkeypatch.setattr(forms_svc, "_create_enquiry", crash)
        r = _post(client, f, GOOD)
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert r.headers["access-control-allow-origin"] == "*"
        (sub,) = _subs(f)
        assert sub.enquiry_id is None and sub.payload["email"] == "casey@example.com"
        assert sub.delivery_error.startswith("Enquiry not created: TypeError")
        assert "Suppressed outside production" in sub.delivery_error
        assert len(_emails(f, "form_submission")) == 1

    def test_a_dates_field_mapped_to_preferred_date_still_makes_the_enquiry(
        self, client, f
    ):
        forms_svc.replace_fields(
            f.db,
            f.w.ctx(f.w.owner),
            f.form.id,
            [
                {"key": "name", "label": "Name", "type": "name"},
                {"key": "email", "label": "Email", "type": "email"},
                {
                    "key": "when",
                    "label": "Dates",
                    "type": "dates",
                    "maps_to": "preferred_date",
                },
            ],
        )
        payload = {
            "name": "Casey",
            "email": "casey@example.com",
            "when": ["2026-12-05", "2026-12-04"],
        }
        assert _post(client, f, payload).status_code == 200
        (sub,) = _subs(f)
        enquiry = f.db.get(FnEnquiry, sub.enquiry_id)
        assert enquiry.preferred_date_start == dt.date(2026, 12, 4)
        assert enquiry.preferred_date_end == dt.date(2026, 12, 5)

    def test_an_unexpected_error_still_answers_with_cors(self, client, f, monkeypatch):
        def explode(*args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(forms_svc, "submit", explode)
        r = _post(client, f, GOOD)
        assert r.status_code == 500
        assert r.json()["detail"]["code"] == "server_error"
        assert r.headers["access-control-allow-origin"] == "*"
        assert r.headers["cache-control"] == "no-store"

    def test_the_ip_is_kept_only_as_a_salted_hash(self, client, f):
        _post(client, f, GOOD)
        (sub,) = _subs(f)
        assert sub.ip_hash and f.ip not in sub.ip_hash
        assert sub.ip_hash != hashlib.sha256(f.ip.encode()).hexdigest()
        assert sub.ip_hash == forms_svc.ip_hash(f.form.id, f.ip)


class TestSubmitShadow:
    def test_shadow_emails_the_venue_mailbox_with_reply_to_and_makes_no_enquiry(
        self, client, f, monkeypatch
    ):
        f.w.org.functions_mode = "shadow"
        f.db.flush()
        sent = []
        monkeypatch.setattr(notify, "suppressed", lambda: False)
        monkeypatch.setattr(notify.settings, "RESEND_API_KEY", "re_test")
        monkeypatch.setattr(
            notify, "_resend", lambda payload: sent.append(payload) or "msg-1"
        )
        assert _post(client, f, GOOD, tracking=TRACKING).status_code == 200
        (sub,) = _subs(f)
        assert sub.enquiry_id is None
        assert f.db.query(FnEnquiry).filter_by(organization_id=f.w.org.id).count() == 0
        assert len(sent) == 1
        email = sent[0]
        assert email["to"] == ["events@brewbar.example"]
        assert email["reply_to"] == "casey@example.com"
        assert email["subject"].startswith("Casey Client — Fri 04 Dec 2026 · ")
        assert "{" not in email["subject"]
        assert (
            "A 40th for about sixty." in email["html"] and sub.ref_code in email["html"]
        )
        assert "functions-spring" in email["html"]
        assert sub.delivery_status == "sent" and sub.delivery_message_id == "msg-1"

    def test_without_a_recipient_the_venue_mailbox_address_is_used(
        self, client, f, monkeypatch
    ):
        from app.db.functions_models import FnMailboxAccount

        f.w.org.functions_mode = "shadow"
        f.form.recipient_email = None
        f.db.add(
            FnMailboxAccount(
                organization_id=f.w.org.id,
                venue_id=f.w.venue.id,
                google_email="brew@gmail.example",
            )
        )
        f.db.flush()
        assert _post(client, f, GOOD).status_code == 200
        (email,) = _emails(f, "form_submission")
        assert email.to_addresses == ["brew@gmail.example"]


class TestSpamAndValidation:
    def test_too_fast_is_kept_as_spam_and_goes_nowhere(self, client, f):
        r = _post(client, f, GOOD, age=0)
        assert r.status_code == 200 and r.json()["ok"] is True
        (sub,) = _subs(f)
        assert sub.spam_reason == "too_fast" and sub.delivery_status == "skipped"
        assert sub.enquiry_id is None and _emails(f, "form_lead") == []

    def test_a_filled_honeypot_is_spam_only_when_quick(self, client, f):
        trapped = {**GOOD, "website": "http://spam.example"}
        assert _post(client, f, trapped, age=4).status_code == 200
        assert _post(client, f, trapped, age=30).status_code == 200
        quick, slow = sorted(_subs(f), key=lambda s: s.submitted_at)
        assert quick.spam_reason == "honeypot" and quick.enquiry_id is None
        assert slow.spam_reason is None and slow.enquiry_id is not None
        assert "website" not in slow.payload

    def test_a_missing_or_forged_form_token_is_refused(self, client, f):
        assert _post(client, f, GOOD, token=None).status_code == 422
        assert _post(client, f, GOOD, token="123.deadbeef").status_code == 422
        assert _post(client, f, GOOD, token=12345).status_code == 422
        stale = forms_svc.issue_form_token(f.form, now=_now() - dt.timedelta(days=3))
        assert _post(client, f, GOOD, token=stale).status_code == 422
        assert _subs(f) == []

    def test_answers_are_validated_against_the_fields(self, client, f):
        bad = {
            **GOOD,
            "email": "not-an-email",
            "kind": "rave",
            "event_date": "next friday",
            "times": {"start": "7pm"},
            "terms": False,
        }
        del bad["name"]
        r = _post(client, f, bad)
        assert r.status_code == 422
        errors = r.json()["detail"]["errors"]
        assert set(errors) == {"name", "email", "kind", "event_date", "times", "terms"}
        assert errors["name"] == "Name is required."
        assert _subs(f) == []

    def test_unknown_payload_keys_are_not_stored(self, client, f):
        assert (
            _post(client, f, {**GOOD, "admin": "true", "__proto__": "x"}).status_code
            == 200
        )
        (sub,) = _subs(f)
        assert "admin" not in sub.payload and "__proto__" not in sub.payload

    def test_submissions_are_rate_limited_per_ip_and_form(self, client, f):
        codes = [_post(client, f, GOOD, age=0).status_code for _ in range(6)]
        assert codes == [200] * 5 + [429]
        client.headers["X-Forwarded-For"] = f"{f.ip}-other"
        assert _post(client, f, GOOD, age=0).status_code == 200

    def test_bodies_must_be_json_objects_of_bounded_size(self, client, f):
        url = f"/api/public/forms/{f.form.slug}/submit"
        assert client.post(url, content="not json").status_code == 400
        assert client.post(url, content="[" * 30000 + "]" * 30000).status_code == 400
        assert client.post(url, content=json.dumps([1, 2])).status_code == 422
        assert client.post(url, content="x" * (70 * 1024)).status_code == 413


class TestAbuse:
    """The independent security review's reproductions, kept as tests: an
    oversized answer must be refused before any pattern runs over it, a
    forged X-Forwarded-For must not buy fresh limits, and one schema fetch
    must not be replayed into many leads."""

    EVIL_EMAIL = "a@" + "b." * 31000 + "@"  # ~62 KB, under MAX_FORM_BODY

    def test_an_oversized_email_is_refused_before_any_regex(self, client, f):
        fields = forms_svc._fields(f.db, f.form)
        started = time.perf_counter()
        with pytest.raises(forms_svc.FormInvalid) as caught:
            forms_svc.validate_answers(fields, {**GOOD, "email": self.EVIL_EMAIL})
        assert time.perf_counter() - started < 0.05
        assert caught.value.errors == {"email": "Email is too long."}
        body = json.dumps(
            {
                "payload": {**GOOD, "email": self.EVIL_EMAIL},
                "form_token": forms_svc.issue_form_token(
                    f.form, now=_now() - dt.timedelta(seconds=10)
                ),
            }
        )
        assert len(body) < 64 * 1024
        started = time.perf_counter()
        r = client.post(
            f"/api/public/forms/{f.form.slug}/submit",
            content=body,
            headers={"Content-Type": "text/plain"},
        )
        assert r.status_code == 422
        assert time.perf_counter() - started < 2
        assert _subs(f) == []

    @pytest.mark.parametrize(
        "key,value",
        [
            ("name", "x" * 201),
            ("name", {"first": "x" * 101, "last": "y"}),
            ("name", {"first": ["x"], "last": "y"}),
            ("phone", "1" * 41),
            ("message", "x" * 5001),
            ("guests", "9" * 41),
            ("event_date", "2026-12-04" + " " * 100),
            ("times", {"start": "19:00" + "0" * 100, "end": "23:00"}),
            ("kind", "g" * 201),
            ("extras", ["DJ", "Projector", "DJ"]),
            ("extras", [{"a": 1}]),
        ],
    )
    def test_every_answer_type_has_a_size_cap(self, client, f, key, value):
        fields = forms_svc._fields(f.db, f.form)
        with pytest.raises(forms_svc.FormInvalid) as caught:
            forms_svc.validate_answers(fields, {**GOOD, key: value})
        assert set(caught.value.errors) == {key}

    def test_dates_lists_are_capped(self, client, f):
        field = forms_svc.FnWebFormField(key="d", label="Dates", type="dates")
        many = [f"2026-12-{day:02d}" for day in range(1, 22)]
        with pytest.raises(forms_svc.FormInvalid):
            forms_svc.validate_answers([field], {"d": many})
        assert forms_svc.validate_answers([field], {"d": many[:20]})["d"] == many[:20]

    def test_a_form_token_is_good_for_one_submission(self, client, f):
        token = forms_svc.issue_form_token(
            f.form, now=_now() - dt.timedelta(seconds=10)
        )
        # A refused submission doesn't use the token up...
        broken = {k: v for k, v in GOOD.items() if k != "name"}
        assert _post(client, f, broken, token=token).status_code == 422
        # ...so the corrected one goes through on it,
        assert _post(client, f, GOOD, token=token).status_code == 200
        # and a second send of the same fetch (double click, replay) is
        # answered the same but kept as spam: no second lead, no email.
        assert _post(client, f, GOOD, token=token).status_code == 200
        first, second = sorted(_subs(f), key=lambda s: s.submitted_at)
        assert first.enquiry_id is not None and first.spam_reason is None
        assert second.spam_reason == "token_reused" and second.enquiry_id is None
        assert len(_emails(f, "form_lead")) == 1

    def test_forged_forwarded_for_with_one_token_makes_one_lead(self, client, f):
        token = forms_svc.issue_form_token(
            f.form, now=_now() - dt.timedelta(seconds=10)
        )
        codes = []
        for i in range(25):
            # What a direct run.app request with a forged header produces.
            client.headers["X-Forwarded-For"] = f"198.51.100.{i}, 203.0.113.9"
            codes.append(
                _post(
                    client, f, {**GOOD, "email": f"spam{i}@example.com"}, token=token
                ).status_code
            )
        assert codes == [200] * 25
        subs = _subs(f)
        assert sum(1 for s in subs if s.spam_reason == "token_reused") == 24
        assert sum(1 for s in subs if s.enquiry_id) == 1

    def test_the_form_has_a_ceiling_no_forged_ip_escapes(self, client, f, monkeypatch):
        monkeypatch.setattr(forms_svc, "FORM_MINUTE_LIMIT", 4)
        codes = []
        for i in range(6):
            client.headers["X-Forwarded-For"] = f"198.51.100.{i}, 203.0.113.9"
            codes.append(
                _post(client, f, {**GOOD, "email": f"lead{i}@example.com"}).status_code
            )
        assert codes == [200] * 4 + [429] * 2
        assert len(_subs(f)) == 4

    def test_the_daily_ceiling_refuses_without_storing(self, client, f, monkeypatch):
        monkeypatch.setattr(forms_svc, "FORM_DAY_LIMIT", 2)
        codes = []
        for i in range(3):
            client.headers["X-Forwarded-For"] = f"{f.ip}-{i}"
            codes.append(
                _post(client, f, {**GOOD, "email": f"day{i}@example.com"}).status_code
            )
        assert codes == [200, 200, 429]
        assert len(_subs(f)) == 2

    def test_answers_cannot_inject_subject_placeholders(self):
        form = FnWebForm(name="Enquiries", subject_template="{name} — {message}")
        fields = [
            forms_svc.FnWebFormField(key="name", label="Name", type="name"),
            forms_svc.FnWebFormField(key="email", label="Email", type="email"),
            forms_svc.FnWebFormField(key="message", label="Msg", type="long_text"),
        ]
        subject = forms_svc.render_subject(
            form,
            fields,
            {
                "name": "Casey {email}",
                "email": "c@example.com",
                "message": " " * 4000 + "hi",
            },
            now=dt.datetime(2026, 10, 12, 1, 0, tzinfo=dt.timezone.utc),
        )
        assert "c@example.com" not in subject and "{email}" in subject
        assert len(subject) < 250 and "  " not in subject


def test_subject_template_tidies_empty_placeholders():
    form = FnWebForm(
        name="Enquiries", subject_template="{{name}} — {{missing}}\r\nBcc: x"
    )
    fields = [
        forms_svc.FnWebFormField(key="name", label="Name", type="name"),
    ]
    subject = forms_svc.render_subject(
        form,
        fields,
        {"name": "Casey"},
        now=dt.datetime(2026, 10, 12, 1, 0, tzinfo=dt.timezone.utc),
    )
    assert "\n" not in subject and "\r" not in subject
    assert subject.startswith("Casey")
    assert subject.endswith("· 12 Oct 01:00")


# ---------------------------------------------------------------------------
# Staff builder
# ---------------------------------------------------------------------------


class TestBuilder:
    def test_create_with_default_fields_and_embed_snippets(self, client, f):
        r = client.post(
            "/api/functions/forms",
            json={"venue_id": f.w.venue.id, "name": f"Garden Bar Parties! {_tag()}"},
            headers=_auth(f.w.owner),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["slug"].startswith("garden-bar-parties")
        assert [x["key"] for x in body["fields"]] == [
            "name",
            "email",
            "phone",
            "event_date",
            "guests",
            "message",
        ]
        assert f"/f/{body['slug']}" in body["embed"]["iframe"]
        assert f'data-norm-form="{body["slug"]}"' in body["embed"]["script"]
        assert "/embed/forms.js" in body["embed"]["script"]
        assert "/embed/track.js" in body["embed"]["tracker"]

    def test_slugs_are_unique_and_tidy(self, client, f):
        r = client.post(
            "/api/functions/forms",
            json={"venue_id": f.w.venue.id, "name": "Copy", "slug": f.form.slug},
            headers=_auth(f.w.owner),
        )
        assert r.status_code == 409
        r = client.post(
            "/api/functions/forms",
            json={"venue_id": f.w.venue.id, "name": "Bad", "slug": "Not A Slug!"},
            headers=_auth(f.w.owner),
        )
        assert r.status_code == 422

    def test_settings_and_fields_save_with_versions(self, client, f):
        url = f"/api/functions/forms/{f.form.id}"
        current = client.get(url, headers=_auth(f.w.owner)).json()
        r = client.put(
            url,
            json={
                "expected_version": current["version"],
                "success_message": "Ka pai — talk soon.",
                "theme": {"primary": "#1c2b39", "radius": "8px"},
                "intro_html": "<p>Tell us more</p><script>alert(1)</script>",
            },
            headers=_auth(f.w.owner),
        )
        assert r.status_code == 200, r.text
        assert r.json()["intro_html"] == "<p>Tell us more</p>"
        # The builder may send back the whole form it loaded.
        loaded = r.json()
        roundtrip = client.put(
            url, json={**loaded, "name": "Renamed"}, headers=_auth(f.w.owner)
        )
        assert roundtrip.status_code == 200, roundtrip.text
        assert roundtrip.json()["name"] == "Renamed"
        assert (
            client.put(url, json={"owner": "x"}, headers=_auth(f.w.owner)).status_code
            == 422
        )
        garbage = client.put(
            url, json={"expected_version": "7"}, headers=_auth(f.w.owner)
        )
        assert garbage.status_code == 422
        not_text = client.put(url, json={"name": 5}, headers=_auth(f.w.owner))
        assert not_text.status_code == 422
        stale = client.put(
            url,
            json={"expected_version": current["version"], "name": "x"},
            headers=_auth(f.w.owner),
        )
        assert stale.status_code == 409
        bad_theme = client.put(
            url,
            json={"theme": {"primary": "red;} body{display:none"}},
            headers=_auth(f.w.owner),
        )
        assert bad_theme.status_code == 422
        r = client.put(
            f"{url}/fields",
            json={
                "fields": [
                    {"key": "who", "label": "Who", "type": "name", "required": True}
                ]
            },
            headers=_auth(f.w.owner),
        )
        assert r.status_code == 422  # reply_to_field_key "email" would point nowhere
        r = client.put(
            f"{url}/fields",
            json={
                "fields": [
                    {"key": "who", "label": "Who", "type": "name"},
                    {"key": "email", "label": "Email", "type": "email"},
                    {"label": "More", "type": "section"},
                ]
            },
            headers=_auth(f.w.owner),
        )
        assert r.status_code == 200, r.text
        assert [x["key"] for x in r.json()["fields"]] == ["who", "email", "section_3"]

    def test_a_full_documented_theme_saves(self, client, f):
        theme = {key: "#1c2b39" for key in forms_svc.THEME_KEYS}
        theme.update(heading_font="Playfair Display, serif", radius="8", field_gap=16)
        url = f"/api/functions/forms/{f.form.id}"
        r = client.put(url, json={"theme": theme}, headers=_auth(f.w.owner))
        assert r.status_code == 200, r.text
        saved = r.json()["theme"]
        assert set(saved) == forms_svc.THEME_KEYS and saved["field_gap"] == "16"

    def test_orbit_camel_case_theme_keys_are_normalised(self, client, f):
        url = f"/api/functions/forms/{f.form.id}"
        r = client.put(
            url,
            json={
                "theme": {
                    "inputRadius": "8",
                    "buttonBackground": "#111",
                    "primary": "#222",
                }
            },
            headers=_auth(f.w.owner),
        )
        assert r.status_code == 200, r.text
        assert r.json()["theme"] == {
            "input_radius": "8",
            "button_background": "#111",
            "primary": "#222",
        }

    def test_unknown_theme_keys_are_refused_by_name(self, client, f):
        url = f"/api/functions/forms/{f.form.id}"
        r = client.put(
            url,
            json={"theme": {"primary": "#222", "glow": "1", "zIndex": "9"}},
            headers=_auth(f.w.owner),
        )
        assert r.status_code == 422
        assert r.json()["detail"]["message"] == "Unknown theme settings: glow, zIndex."

    def test_theme_keys_mirror_forms_js(self):
        """The server accepts exactly the keys the embed applies — a key only
        one side knew would be refused, or saved and silently ignored."""
        forms_js = (
            pathlib.Path(__file__).resolve().parents[3]
            / "apps/web/public/embed/forms.js"
        ).read_text()
        keys = set()
        for table in ("THEME_ALIASES", "THEME_KEYS"):
            block = re.search(rf"var {table} = \{{(.*?)\n  \}};", forms_js, re.S).group(
                1
            )
            keys |= set(re.findall(r"^\s+([a-z_]+): \[", block, re.M))
        assert keys == set(forms_svc.THEME_KEYS)

    def test_bad_fields_are_refused(self, client, f):
        url = f"/api/functions/forms/{f.form.id}/fields"
        for fields in (
            [{"key": "a", "label": "A", "type": "file"}],
            [{"key": "Bad Key", "label": "A", "type": "text"}],
            [
                {"key": "a", "label": "A", "type": "text"},
                {"key": "a", "label": "B", "type": "text"},
            ],
            [{"key": "a", "label": "A", "type": "choice"}],
            [{"key": "a", "label": "A", "type": "text", "maps_to": "owner_user_id"}],
            [{"key": "a", "label": 5, "type": "text"}],
            [{"key": ["a"], "label": "A", "type": "text"}],
            [
                {"key": "email", "label": "Email", "type": "email"},
                {"key": "website", "label": "Your website", "type": "text"},
            ],
        ):
            r = client.put(url, json={"fields": fields}, headers=_auth(f.w.owner))
            assert r.status_code == 422, fields

    def test_other_organisations_and_venues_cannot_see_forms(self, client, f):
        url = f"/api/functions/forms/{f.form.id}"
        assert client.get(url, headers=_auth(f.w.other_user)).status_code == 404
        listed = client.get(
            "/api/functions/forms", headers=_auth(f.w.other_user)
        ).json()
        assert listed == {"forms": []}
        garden = forms_svc.create_form(
            f.db, f.w.ctx(f.w.owner), venue_id=f.w.venue2.id, name=f"Garden {_tag()}"
        )
        # Staff has Brew Bar only.
        assert (
            client.get(
                f"/api/functions/forms/{garden.id}", headers=_auth(f.w.staff)
            ).status_code
            == 404
        )
        r = client.post(
            "/api/functions/forms",
            json={"venue_id": f.w.venue2.id, "name": f"Sneaky {_tag()}"},
            headers=_auth(f.w.staff),
        )
        assert r.status_code == 403
        assert (
            client.put(
                url, json={"name": "x"}, headers=_auth(f.w.other_user)
            ).status_code
            == 404
        )

    def test_switched_off_is_403_and_shadow_is_read_only(self, client, f):
        f.w.org.functions_mode = "shadow"
        f.db.flush()
        assert (
            client.get("/api/functions/forms", headers=_auth(f.w.owner)).status_code
            == 200
        )
        r = client.post(
            "/api/functions/forms",
            json={"venue_id": f.w.venue.id, "name": f"Nope {_tag()}"},
            headers=_auth(f.w.owner),
        )
        assert r.status_code == 409 and r.json()["detail"]["code"] == "not_live"
        f.w.org.functions_mode = "off"
        f.db.flush()
        r = client.get("/api/functions/forms", headers=_auth(f.w.owner))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "functions_off"

    def test_delete_removes_an_unused_form_and_switches_off_a_used_one(self, client, f):
        spare = forms_svc.create_form(
            f.db, f.w.ctx(f.w.owner), venue_id=f.w.venue.id, name=f"Spare {_tag()}"
        )
        r = client.delete(f"/api/functions/forms/{spare.id}", headers=_auth(f.w.owner))
        assert r.json() == {"deleted": True, "active": False}
        assert f.db.get(FnWebForm, spare.id) is None
        _post(client, f, GOOD)
        r = client.delete(f"/api/functions/forms/{f.form.id}", headers=_auth(f.w.owner))
        assert r.json() == {"deleted": False, "active": False}
        f.db.refresh(f.form)
        assert f.form.active is False

    def test_submissions_list_hides_spam_and_private_details(self, client, f):
        _post(client, f, GOOD)
        _post(client, f, GOOD, age=0)
        url = f"/api/functions/forms/{f.form.id}/submissions"
        real = client.get(url, headers=_auth(f.w.owner)).json()["submissions"]
        assert len(real) == 1
        assert real[0]["attribution"] == {}
        assert "ip_hash" not in real[0] and "user_agent" not in real[0]
        everything = client.get(
            f"{url}?include_spam=true", headers=_auth(f.w.owner)
        ).json()
        assert len(everything["submissions"]) == 2
        listed = client.get("/api/functions/forms", headers=_auth(f.w.owner)).json()[
            "forms"
        ]
        mine = next(x for x in listed if x["id"] == f.form.id)
        assert mine["submissions"] == 1
        assert client.get(url, headers=_auth(f.w.other_user)).status_code == 404
