"""The Orbit → Norm Functions importer, run against a fake Orbit (no network).

What these pin down, beyond each transform:

* ids are deterministic (UUID5) and a reload is IDENTICAL, row for row;
* a dry run, a failed sanity gate and a refusal write nothing — and a failed
  gate leaves the previous import in place;
* a live organisation is refused; an unmapped Orbit venue with data, or a
  Norm venue without a timezone, stops the run before anything is read in;
* a mailbox connected in Norm keeps its row and its mirror (its sync owns
  them), and a form submission Norm received itself survives a reload;
* images are copied once (content-addressed) and proposal HTML points at
  Norm's public file path;
* Orbit is only ever read (the fake fails on anything but GET/HEAD).
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.db import functions_models as fm
from app.db.models import Connection
from app.services.functions import status
from app.services.functions.importer import (
    UNBOOKED_ARCHIVE_NOTE,
    UNBOOKED_FOLLOW_UP,
    ImportRefused,
    OrbitPostgrest,
    OrbitReadError,
    import_id,
    is_import_id,
    parse_clock,
    rewrite_storage_urls,
    run_import,
    to_cents,
)
from tests.functions_importer_fixtures import (
    A_DECK,
    C1,
    C2,
    C3,
    C4,
    E1,
    E2,
    E3,
    E4,
    E5,
    E6,
    E7,
    E8,
    E9,
    F1,
    F2,
    F3,
    F4,
    GA1,
    GA2,
    IMG_LIBRARY,
    IMG_LOGO,
    IMG_PROPOSAL,
    L1,
    L2,
    L3,
    ORBIT_URL,
    SUB1,
    T1,
    T9,
    WF1,
    FakeOrbit,
    make_import_world,
    oid,
    orbit_dataset,
)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 12, 2, 0, tzinfo=UTC)
SKIP = ["Closed Venue"]


@pytest.fixture
def w(db_session):
    return make_import_world(db_session)


def _fake(w) -> FakeOrbit:
    tables, files = orbit_dataset(approver_email=w.approver.email)
    return FakeOrbit(tables, files)


def _run(w, fake=None, **kw):
    fake = fake or _fake(w)
    kw.setdefault("skip_venues", SKIP)
    result = run_import(
        w.db,
        fake.client(kw.pop("page", 1000)),
        org=w.org.id,
        commit=False,
        now=NOW,
        log=lambda *a: None,
        **kw,
    )
    return result, fake


def _id(w, table, key):
    return import_id(w.org.id, table, key)


def _rows(w, model, **where):
    q = w.db.query(model).filter(model.organization_id == w.org.id)
    for k, v in where.items():
        q = q.filter(getattr(model, k) == v)
    return q.all()


def _one(w, model, row_id):
    return (
        w.db.query(model)
        .filter(model.organization_id == w.org.id, model.id == row_id)
        .one()
    )


def _snapshot(w) -> dict:
    out = {}
    for model in fm.FUNCTIONS_MODELS:
        t = model.__table__
        rows = w.db.execute(
            select(t).where(t.c.organization_id == w.org.id).order_by(t.c.id)
        ).all()
        out[t.name] = [tuple(r) for r in rows]
    return out


# ---------------------------------------------------------------------------
# The run itself
# ---------------------------------------------------------------------------


def test_ids_are_deterministic_uuid5(w):
    result, _ = _run(w)
    assert result.ok and result.committed
    enquiry = _one(w, fm.FnEnquiry, _id(w, "enquiries", E1))
    assert is_import_id(enquiry.id)
    assert enquiry.id == str(
        uuid.uuid5(
            uuid.UUID("38027f9b-6d4e-5498-98be-34d2db8b5488"),
            f"{w.org.id}:enquiries:{E1}",
        )
    )
    assert not is_import_id(str(uuid.uuid4()))
    # Another organisation importing the same Orbit row gets a different id.
    assert import_id(str(uuid.uuid4()), "enquiries", E1) != enquiry.id


def test_reload_is_identical(w):
    first, fake = _run(w)
    before = _snapshot(w)
    second, _ = _run(w, fake)
    assert second.ok
    assert _snapshot(w) == before
    # The second run really did wipe and reload, not skip.
    assert second.report.deleted["fn_enquiries"] == first.report.written["fn_enquiries"]
    assert "fn_files" not in second.report.written  # files are only ever added once


def test_dry_run_writes_nothing(w):
    before = _snapshot(w)
    result, _ = _run(w, dry_run=True)
    assert result.ok and not result.committed
    assert result.report.written["fn_enquiries"] == 8
    assert _snapshot(w) == before
    assert "dry run" in result.report.outcome


def test_refuses_a_live_org(w):
    w.org.functions_mode = "live"
    w.db.flush()
    before = _snapshot(w)
    with pytest.raises(ImportRefused, match="live"):
        _run(w)
    assert _snapshot(w) == before


def test_failed_sanity_gate_keeps_the_previous_import(w):
    _run(w)
    before = _snapshot(w)
    fake = _fake(w)
    fake.count_override["enquiries"] = 900  # Orbit says 900, the read got 6
    result, _ = _run(w, fake)
    assert not result.ok and not result.committed
    failed = [c for c in result.report.gate["checks"] if not c["ok"]]
    assert [c["check"] for c in failed] == ["read enquiries"]
    assert "GATE FAILED" in result.report.outcome
    assert _snapshot(w) == before


def test_a_concurrent_import_of_the_same_org_is_refused(w):
    """The per-org advisory lock: a second run waits for nothing and changes
    nothing while another holds it."""
    from sqlalchemy import text

    key = f"norm-functions-import:{w.org.id}"
    other = w.db.get_bind().engine.connect()
    try:
        other.execute(text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": key})
        before = _snapshot(w)
        with pytest.raises(ImportRefused, match="another import is running"):
            _run(w)
        assert _snapshot(w) == before
    finally:
        other.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": key})
        other.close()


def test_records_created_in_norm_stop_the_reload(w):
    """Second security review: an org that was live and is set back to shadow
    (a cut-over rollback) holds records Norm authored. A reload would delete
    them, so the importer refuses — before reading Orbit, and again under
    the lock — and writes nothing."""
    from sqlalchemy import insert

    _run(w)
    t = fm.FnEnquiry.__table__
    src = (
        w.db.execute(select(t).where(t.c.organization_id == w.org.id))
        .mappings()
        .first()
    )
    native_id = str(uuid.uuid4())  # what Norm itself creates while live
    w.db.execute(insert(t).values({**dict(src), "id": native_id}))
    w.db.flush()
    before = _snapshot(w)
    with pytest.raises(ImportRefused, match=r"created in Norm \(fn_enquiries 1\)"):
        _run(w)
    assert _snapshot(w) == before  # the Norm-authored enquiry is still there
    from app.services.functions.importer import norm_authored_rows

    assert norm_authored_rows(w.db, w.org.id) == {"fn_enquiries": 1}


def test_what_the_import_keeps_on_purpose_does_not_stop_it(w):
    """Norm-received submissions, files and a connected mailbox's mirror are
    Norm's own rows too, but the import keeps them — they never block it."""
    from app.services.functions.importer import norm_authored_rows

    _run(w)
    w.db.add(
        fm.FnFormSubmission(
            organization_id=w.org.id,
            form_id=_id(w, "web_forms", WF1),
            venue_id=w.venue2.id,
            payload={},
            delivery_status="sent",
        )
    )
    w.db.add(
        fm.FnFile(
            organization_id=w.org.id,
            sha256="0" * 64,
            content_type="image/png",
            size_bytes=1,
            data=b"x",
        )
    )
    w.db.flush()
    assert norm_authored_rows(w.db, w.org.id) == {}
    assert _run(w)[0].ok


def test_unmapped_venue_with_data_stops_the_run(w):
    with pytest.raises(ImportRefused, match="Closed Venue"):
        _run(w, skip_venues=[])
    assert _rows(w, fm.FnEnquiry) == []


def test_unknown_skip_venue_is_refused(w):
    with pytest.raises(ImportRefused, match="Nowhere"):
        _run(w, skip_venues=["Closed Venue", "Nowhere"])


def test_norm_venue_without_timezone_stops_the_run(w):
    w.venue2.timezone = None
    w.db.flush()
    with pytest.raises(ImportRefused, match="Garden Bar"):
        _run(w)


def test_skipped_venue_rows_are_reported(w):
    result, _ = _run(w)
    src = result.report.sources
    assert src["enquiries"]["dropped"] == {"venue skipped (--skip-venue)": 1}
    assert src["function_reply_templates"]["dropped"] == {
        "venue skipped (--skip-venue)": 1
    }
    assert src["enquiry_status_events"]["dropped"] == {"enquiry not imported": 1}
    assert _rows(w, fm.FnEnquiry, id=_id(w, "enquiries", E6)) == []


def test_every_row_read_is_accounted_for(w):
    result, _ = _run(w)
    assert result.report.gate["passed"]
    assert not [
        c for c in result.report.gate["checks"] if c["check"].startswith("accounted")
    ]
    for table, s in result.report.sources.items():
        if table in ("venues", "team_members") or s["orbit_count"] is None:
            continue
        assert s["imported"] + s["merged"] + sum(s["dropped"].values()) == s["read"], (
            table
        )


def test_orbit_is_only_read_and_paging_reads_everything(w):
    paged, fake = _run(w, page=2)
    assert {method for method, _ in fake.requests} <= {"GET", "HEAD"}
    whole, _ = _run(w)
    for table in ("contacts", "enquiries", "gmail_folder_threads", "gmail_messages"):
        assert (
            paged.report.sources[table]["read"] == whole.report.sources[table]["read"]
        ), table


# ---------------------------------------------------------------------------
# Mailboxes the import must not touch; submissions Norm received itself
# ---------------------------------------------------------------------------


def test_connected_mailbox_keeps_its_rows(w):
    conn = Connection(connector_name="gmail_mailbox", venue_id=w.venue2.id)
    w.db.add(conn)
    w.db.flush()
    account = fm.FnMailboxAccount(
        organization_id=w.org.id,
        venue_id=w.venue2.id,
        connection_id=conn.id,
        google_email="Garden@Example.com",
        status="active",
        history_id=99,
    )
    w.db.add(account)
    w.db.flush()
    thread = fm.FnMailboxThread(
        organization_id=w.org.id,
        account_id=account.id,
        gmail_thread_id="synced",
        subject="Synced by Norm",
    )
    w.db.add(thread)
    w.db.flush()
    w.db.add(
        fm.FnMailboxMessage(
            organization_id=w.org.id,
            account_id=account.id,
            thread_id=thread.id,
            gmail_message_id="norm-1",
            gmail_thread_id="synced",
            internal_date=NOW,
            is_inbound=True,
        )
    )
    w.db.add(
        fm.FnThreadFolder(
            organization_id=w.org.id,
            account_id=account.id,
            gmail_thread_id="synced",
            folder="other",
        )
    )
    w.db.flush()

    result, _ = _run(w)
    assert result.ok
    kept = _one(w, fm.FnMailboxAccount, account.id)
    assert (kept.connection_id, kept.history_id, kept.status) == (conn.id, 99, "active")
    assert [
        m.gmail_message_id for m in _rows(w, fm.FnMailboxMessage, account_id=account.id)
    ] == ["norm-1"]
    assert [
        t.gmail_thread_id for t in _rows(w, fm.FnMailboxThread, account_id=account.id)
    ] == ["synced"]
    assert [f.folder for f in _rows(w, fm.FnThreadFolder, account_id=account.id)] == [
        "other"
    ]
    # No placeholder for the connected address; the link points at the real row.
    emails = sorted(a.google_email for a in _rows(w, fm.FnMailboxAccount))
    assert emails == ["Garden@Example.com", "events@brewbar.example"]
    link = _one(w, fm.FnThreadLink, _id(w, "enquiry_gmail_threads", oid("egt3")))
    assert link.account_id == account.id
    # A reload still leaves them alone.
    _run(w)
    assert [
        m.gmail_message_id for m in _rows(w, fm.FnMailboxMessage, account_id=account.id)
    ] == ["norm-1"]


def test_submission_received_by_norm_survives_a_reload(w):
    _run(w)
    form_id = _id(w, "web_forms", WF1)
    native = fm.FnFormSubmission(
        organization_id=w.org.id,
        form_id=form_id,
        venue_id=w.venue2.id,
        payload={"name": "Arrived in Norm"},
        delivery_status="sent",
    )
    w.db.add(native)
    w.db.flush()
    assert not is_import_id(native.id)
    result, _ = _run(w)
    assert result.ok
    assert _one(w, fm.FnFormSubmission, native.id).payload == {
        "name": "Arrived in Norm"
    }
    assert _one(w, fm.FnWebForm, form_id).slug == "garden-enquiry"  # updated in place
    assert result.report.kept["fn_form_submissions"] == 1


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def test_images_are_copied_once_and_proposal_urls_rewritten(w):
    result, _ = _run(w)
    files = {f.source_ref: f for f in _rows(w, fm.FnFile)}
    assert set(files) == {
        IMG_LOGO,
        IMG_PROPOSAL,
        IMG_LIBRARY,
        ORBIT_URL + "/storage/v1/object/public/function-photos/events/plan.png",
    }
    plan = files[
        ORBIT_URL + "/storage/v1/object/public/function-photos/events/plan.png"
    ]
    assert (
        _one(w, fm.FnEvent, _id(w, "venue_functions", F2)).table_plan_file_id == plan.id
    )
    picture = files[IMG_PROPOSAL]
    assert (picture.content_type, picture.width, picture.height) == ("image/png", 4, 3)
    assert picture.id == import_id(w.org.id, "fn_files", picture.sha256)

    proposal = _rows(w, fm.FnProposal)[0]
    assert f"/api/public/files/{picture.id}" in proposal.html
    assert f"url(/api/public/files/{picture.id})" in proposal.css.replace("'", "")
    assert "supabase.co/storage" not in proposal.html + proposal.css
    assert "fonts.googleapis.com" not in proposal.html + proposal.css  # sanitised
    version = _rows(w, fm.FnProposalVersion)[0]
    assert f"/api/public/files/{picture.id}" in version.html

    brand = _rows(w, fm.FnVenueBrand)[0]
    assert brand.logo_file_id == files[IMG_LOGO].id
    image = _rows(w, fm.FnProposalImage)[0]
    assert (image.file_id, image.name, image.tags) == (
        files[IMG_LIBRARY].id,
        "deck crowd",
        ["deck"],
    )
    assert image.created_by_user_id == w.approver.id

    again, _ = _run(w)
    assert len(_rows(w, fm.FnFile)) == 4
    assert again.report.tallies["files"]["already in Norm (brand logo)"] == 1


def test_files_already_present_by_bytes_are_reused(w):
    fake = _fake(w)
    body = fake.files[IMG_PROPOSAL]
    import hashlib

    existing = fm.FnFile(
        organization_id=w.org.id,
        sha256=hashlib.sha256(body).hexdigest(),
        content_type="image/png",
        size_bytes=len(body),
        data=body,
    )
    w.db.add(existing)
    w.db.flush()
    _run(w, fake)
    proposal = _rows(w, fm.FnProposal)[0]
    assert f"/api/public/files/{existing.id}" in proposal.html
    assert len(_rows(w, fm.FnFile)) == 4


def test_fetch_file_only_reads_orbit_public_storage():
    fake = FakeOrbit({}, {ORBIT_URL + "/storage/v1/object/public/b/ok.png": b"x" * 10})
    client = fake.client()
    assert (
        client.fetch_file(ORBIT_URL + "/storage/v1/object/public/b/ok.png?width=5")[0]
        == b"x" * 10
    )
    for bad in (
        "https://evil.example/storage/v1/object/public/b/ok.png",
        ORBIT_URL + "/storage/v1/object/public/b/../../rest/v1/contacts",
        ORBIT_URL + "/rest/v1/contacts",
    ):
        with pytest.raises(OrbitReadError, match="refused"):
            client.fetch_file(bad)
    small = OrbitPostgrest(
        ORBIT_URL, "service-key", client=client._client, max_file_bytes=5
    )
    with pytest.raises(OrbitReadError, match="over 5 bytes"):
        small.fetch_file(ORBIT_URL + "/storage/v1/object/public/b/ok.png")
    assert {m for m, _ in fake.requests} == {"GET"}


def test_file_copy_stops_reading_past_the_cap():
    """No Content-Length (or a false one) must not make the copy read a huge
    body: the stream is abandoned as soon as it passes the cap."""
    import httpx

    pulled = []

    def chunks():
        for _ in range(1000):
            pulled.append(1)
            yield b"x" * 4

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=chunks())
    )
    client = OrbitPostgrest(
        ORBIT_URL,
        "service-key",
        client=httpx.Client(transport=transport),
        max_file_bytes=10,
    )
    with pytest.raises(OrbitReadError, match="over 10 bytes"):
        client.fetch_file(ORBIT_URL + "/storage/v1/object/public/b/huge.png")
    assert len(pulled) <= 4


def test_rewrite_storage_urls_handles_entities_and_queries():
    url = ORBIT_URL + "/storage/v1/object/public/b/a.png"
    html = f'<img src="{url}?w=1&amp;h=2"><img src="{url}"><img src="{url}x">'
    out = rewrite_storage_urls(html, {url: "F"})
    assert out.count("/api/public/files/F") == 2
    assert f"{url}x" in out  # a different object is left alone


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


def test_stored_html_is_sanitised(w):
    """Rich text is stored clean, through each owner's own cleaner, after the
    image rewrite — copied images stay, everything unsafe goes, and the
    report says what was removed."""
    result, _ = _run(w)
    picture = next(f for f in _rows(w, fm.FnFile) if f.source_ref == IMG_PROPOSAL)
    for row in (_rows(w, fm.FnProposal)[0], _rows(w, fm.FnProposalVersion)[0]):
        assert f'src="/api/public/files/{picture.id}"' in row.html
        for bad in ("<script", "onclick", "elsewhere.example", "<link"):
            assert bad not in row.html, bad
        assert "Dear Casey" in row.html
    proposal = _rows(w, fm.FnProposal)[0]
    assert "@import" not in proposal.css
    reply = _one(w, fm.FnReplyTemplate, _id(w, "function_reply_templates", oid("rt1")))
    assert reply.body_html == "<p>Hi</p>"
    form = _one(w, fm.FnWebForm, _id(w, "web_forms", WF1))
    assert form.intro_html == "<p>Hello</p>"
    account = _one(w, fm.FnMailboxAccount, _id(w, "venue_gmail_accounts", GA1))
    assert "<script" not in account.signature_html and "Brew" in account.signature_html

    removed = result.report.tallies["sanitiser"]
    assert removed["proposal: <script> removed"] == 1
    assert removed["proposal: <link> removed"] == 1
    assert removed["proposal: event-handler attributes removed"] == 1
    assert removed["proposal: image sources not on Norm removed"] == 1
    assert removed["proposal: @import rules removed"] == 1
    assert removed["reply template: <script> removed"] == 1
    assert removed["form intro: <iframe> removed"] == 1
    assert removed["mailbox signature: <script> removed"] == 1
    assert removed["proposals with something removed"] == 1


def test_contacts_and_shared_emails(w):
    result, _ = _run(w)
    casey = _one(w, fm.FnContact, _id(w, "contacts", C1))
    assert (casey.name, casey.company, casey.phone) == (
        "Casey Client",
        "Casey Co",
        "021 1",
    )
    emails = {e.email: e for e in _rows(w, fm.FnContactEmail)}
    assert (
        emails["casey@example.com"].contact_id == casey.id
        and emails["casey@example.com"].is_primary
    )
    assert emails["casey.alt@example.com"].contact_id == casey.id
    assert not emails["casey.alt@example.com"].is_primary
    # shared@ is C2's own address; C3 (a different name) claimed it too —
    # reported, not merged, and C3 keeps no email rather than stealing it.
    assert emails["shared@example.com"].contact_id == _id(w, "contacts", C2)
    assert not [e for e in emails.values() if e.contact_id == _id(w, "contacts", C3)]
    assert (
        result.report.tallies["contacts"][
            "addresses shared by several Orbit contacts — different names (reported, not merged)"
        ]
        == 1
    )
    assert _one(w, fm.FnContact, _id(w, "contacts", C4)).name == "Acme Ltd"


def test_enquiries(w):
    result, _ = _run(w)
    e2 = _one(w, fm.FnEnquiry, _id(w, "enquiries", E2))
    assert e2.booking_type == "group_booking"  # 'cocktail' → its booked event's type
    assert e2.owner_user_id == w.approver.id  # contact's assignee, matched by email
    e3 = _one(w, fm.FnEnquiry, _id(w, "enquiries", E3))
    assert (e3.source, e3.utm_source, e3.gclid, e3.landing_page) == (
        "web_form",
        "google",
        "gclid-1",
        "https://brew.example/functions",
    )
    assert (
        e3.preferred_date_start == dt.date(2026, 12, 11)
        and e3.preferred_date_end is None
    )
    e4 = _one(w, fm.FnEnquiry, _id(w, "enquiries", E4))
    assert (e4.follow_up_date, e4.follow_up_reason) == (
        dt.date(2026, 11, 2),
        "After budget",
    )
    e5 = _one(w, fm.FnEnquiry, _id(w, "enquiries", E5))
    assert (e5.venue_id, e5.booking_type) == (
        None,
        "seated_dining",
    )  # Orbit's AI classification
    assert e2.created_at == dt.datetime(2026, 8, 3, tzinfo=UTC)
    assert e2.created_by_label == "Orbit"

    closures = _rows(w, fm.FnEnquiryClosure, enquiry_id=_id(w, "enquiries", E1))
    assert [(c.kind, c.reason, c.actor_label) for c in closures] == [
        ("lost", "Too expensive", "George Eason")
    ]
    tallies = result.report.tallies["booking types"]
    assert tallies["enquiry 'cocktail' → group_booking (booked event's type)"] == 1


def test_events(w):
    result, _ = _run(w)
    f1 = _one(w, fm.FnEvent, _id(w, "venue_functions", F1))
    assert f1.enquiry_id == _id(w, "enquiries", E2)  # via Orbit's won_function_id
    assert (f1.name, f1.booking_type, f1.area_id) == (
        "Casey's 40th",
        "group_booking",
        _id(w, "venue_event_areas", A_DECK),
    )
    # 06:00Z–11:30Z on 4 Dec is 19:00–00:30 in Auckland (NZDT).
    assert (f1.event_date, f1.start_time, f1.end_time, f1.ends_next_day) == (
        dt.date(2026, 12, 4),
        dt.time(19, 0),
        dt.time(0, 30),
        True,
    )
    assert (f1.bar_tab_cents, f1.deposit_cents, f1.deposit_status) == (
        150056,
        20000,
        "paid",
    )
    assert (f1.food_time, f1.arrival_time, f1.seated_time) == (
        dt.time(19, 30),
        dt.time(18, 30),
        None,
    )
    assert (f1.created_via, f1.confirmation_channel) == ("import", "import")

    timings = sorted(
        _rows(w, fm.FnEventTiming, event_id=f1.id), key=lambda t: t.sort_order
    )
    assert [(t.at_time, t.label, t.ends_next_day) for t in timings] == [
        (dt.time(18, 30), None, False),
        (None, "after speeches", False),
        (dt.time(0, 15), None, True),
    ]
    assert [a.kind for a in _rows(w, fm.FnEventAv, event_id=f1.id)] == ["in_house_mic"]
    dj = _rows(w, fm.FnEventEntertainment, event_id=f1.id)[0]
    assert (dj.kind, dj.start_time, dj.end_time, dj.ends_next_day) == (
        "dj",
        dt.time(21),
        dt.time(0),
        True,
    )

    sections = {
        (s.section, s.kind): s
        for s in _rows(w, fm.FnEventSectionConfirmation, event_id=f1.id)
    }
    assert set(sections) == {
        ("details", "confirmed"),
        ("food", "confirmed"),
        ("beverages", "skipped"),
    }
    skip = sections[("beverages", "skipped")]
    assert (skip.actor_kind, skip.actor_user_id, skip.note) == (
        "staff",
        w.approver.id,
        "Paying on the night",
    )
    changes = {
        (c.kind, c.actor_kind) for c in _rows(w, fm.FnEventChange, event_id=f1.id)
    }
    assert changes == {
        ("created", "staff"),
        ("edited", "client"),
        ("link_sent", "import"),
        ("edited", "staff"),
    }
    client_edit = [
        c
        for c in _rows(w, fm.FnEventChange, event_id=f1.id)
        if c.actor_kind == "client"
    ][0]
    assert (client_edit.section, client_edit.after_confirmation) == ("details", True)

    f2 = _one(w, fm.FnEvent, _id(w, "venue_functions", F2))
    contact = _one(w, fm.FnContact, f2.contact_id)
    assert contact.name == "New Person"
    assert (
        _rows(w, fm.FnContactEmail, contact_id=contact.id)[0].email
        == "new.person@example.com"
    )
    assert "Area (from Orbit): Mystery Room" in f2.notes
    assert "Function sheet: https://docs.google.com/document/d/abc" in f2.notes
    link_sent = _rows(w, fm.FnEventChange, event_id=f2.id, kind="link_sent")[0]
    assert link_sent.occurred_at == dt.datetime(
        2026, 8, 27, 3, 0, tzinfo=UTC
    )  # from the client link

    f3 = _one(w, fm.FnEvent, _id(w, "venue_functions", F3))
    assert (f3.event_date, f3.start_time) == (
        dt.date(2026, 12, 1),
        None,
    )  # local midnight = date only
    assert _one(w, fm.FnContact, f3.contact_id).name == "Rhearne Herlihy"
    assert f3.cancelled_at == dt.datetime(2026, 8, 29, tzinfo=UTC)

    f4 = _one(w, fm.FnEvent, _id(w, "venue_functions", F4))
    assert f4.enquiry_id is None  # one live booking per enquiry: the won one keeps it
    assert (
        result.report.tallies["events"][
            "second live booking of one enquiry (kept, unlinked from it)"
        ]
        == 1
    )


def test_min_spends(w):
    result, _ = _run(w)
    booked = _one(w, fm.FnMinSpend, _id(w, "contact_proposal_spend_lines", L1))
    assert (booked.event_id, booked.enquiry_id) == (
        _id(w, "venue_functions", F1),
        _id(w, "enquiries", E2),
    )
    assert (booked.amount_cents, booked.area_id) == (
        500050,
        _id(w, "venue_event_areas", A_DECK),
    )
    approvals = _rows(w, fm.FnMinSpendApproval, min_spend_id=booked.id)
    assert [(a.kind, a.actor_user_id) for a in approvals] == [
        ("approve", w.approver.id)
    ]
    responses = _rows(w, fm.FnMinSpendResponse, min_spend_id=booked.id)
    assert [(r.kind, r.channel) for r in responses] == [("confirm", "import")]
    # The booking's copy of the line is the same option, not a second one.
    assert (
        _rows(w, fm.FnMinSpend, id=_id(w, "venue_function_spend_options", oid("o1")))
        == []
    )

    sibling = _one(w, fm.FnMinSpend, _id(w, "contact_proposal_spend_lines", L2))
    assert (sibling.withdraw_reason, sibling.event_id) == (
        "Another option was confirmed",
        None,
    )
    override = _rows(w, fm.FnMinSpendApproval, min_spend_id=sibling.id)[0]
    assert (override.kind, override.reason, override.actor_label) == (
        "override",
        "Quiet night",
        "George Eason",
    )
    assert (sibling.event_date, sibling.start_time, sibling.end_time) == (
        dt.date(2026, 12, 5),
        dt.time(19),
        None,
    )

    accepted = _one(w, fm.FnMinSpend, _id(w, "contact_proposal_spend_lines", L3))
    assert accepted.event_id is None
    assert [
        r.kind for r in _rows(w, fm.FnMinSpendResponse, min_spend_id=accepted.id)
    ] == ["confirm"]

    direct = _one(w, fm.FnMinSpend, _id(w, "venue_function_spend_options", oid("o2")))
    assert (direct.event_id, direct.enquiry_id, direct.amount_cents) == (
        _id(w, "venue_functions", F2),
        None,
        250000,
    )
    src = result.report.sources["venue_function_spend_options"]
    assert src["dropped"] == {
        "unconfirmed option of a booking with no enquiry to hold it": 2
    }
    assert result.report.sources["min_spend_approval_events"]["dropped"] == {
        "its line was deleted in Orbit": 1
    }

    states = {
        r.min_spend_id: r.state
        for r in w.db.execute(
            select(status.min_spend_status_select(w.org.id).subquery())
        )
    }
    assert states[booked.id] == "confirmed" and states[sibling.id] == "withdrawn"


def test_menus(w):
    result, _ = _run(w)
    merged = _one(
        w, fm.FnPreorderMenu, _id(w, "venue_function_preorder_menus", oid("vm1"))
    )
    assert (merged.enquiry_id, merged.event_id) == (
        _id(w, "enquiries", E2),
        _id(w, "venue_functions", F1),
    )
    assert (merged.is_set_menu, merged.price_per_person_cents, merged.guest_count) == (
        True,
        6500,
        52,
    )
    assert merged.template_id == _id(w, "preorder_templates", oid("pt.set"))
    dishes = _rows(w, fm.FnPreorderMenuItem, menu_id=merged.id, kind="item")
    assert [
        (d.name, d.quantity, d.unit_price_cents, d.dietaries, d.loaded_recipe_id)
        for d in dishes
    ] == [("Steak", 30, None, ["GF"], "recipe-1")]  # set-menu dish price dropped
    assert (
        _rows(w, fm.FnPreorderMenu, id=_id(w, "enquiry_preorder_menus", oid("em1")))
        == []
    )

    enquiry_only = _one(
        w, fm.FnPreorderMenu, _id(w, "enquiry_preorder_menus", oid("em2"))
    )
    assert (enquiry_only.enquiry_id, enquiry_only.event_id) == (
        _id(w, "enquiries", E4),
        None,
    )
    assert [
        i.unit_price_cents
        for i in _rows(w, fm.FnPreorderMenuItem, menu_id=enquiry_only.id)
    ] == [450]

    food = _one(w, fm.FnPreorderMenu, _id(w, "venue_functions.food_preorders", F2))
    items = sorted(
        _rows(w, fm.FnPreorderMenuItem, menu_id=food.id), key=lambda i: i.sort_order
    )
    assert [(i.kind, i.name, i.quantity, i.unit_price_cents) for i in items] == [
        ("header", "Snacks", 0, None),
        ("item", "Fries", 10, 1200),
        ("item", "Olives", 0, 900),
        ("header", "Mains", 0, None),
        ("item", "Steak", 2, 3001),
    ]
    assert items[1].dietaries == ["V", "GF"]
    assert (
        _rows(w, fm.FnPreorderMenu, id=_id(w, "venue_functions.food_preorders", F1))
        == []
    )
    menus = result.report.tallies["menus"]
    assert menus["food_preorders copy dropped (the booking has menu rows)"] == 1
    assert (
        menus[
            "a-la-carte menu whose Orbit line totals ≠ quantity × price (Norm uses quantity × price)"
        ]
        == 1
    )


def test_proposals_and_their_facts(w):
    result, _ = _run(w)
    proposal = _rows(w, fm.FnProposal)[0]
    assert proposal.enquiry_id == _id(w, "enquiries", E3)
    assert proposal.updated_by_user_id == w.approver.id
    version = _rows(w, fm.FnProposalVersion)[0]
    assert (version.version_number, version.sent_at) == (
        1,
        dt.datetime(2026, 8, 21, 1, tzinfo=UTC),
    )
    events = {
        e.kind: e for e in _rows(w, fm.FnProposalEvent, enquiry_id=proposal.enquiry_id)
    }
    assert set(events) == {"sent", "viewed"}
    assert events["sent"].proposal_version_id == version.id
    assert events["sent"].account_id == _id(w, "venue_gmail_accounts", GA1)
    assert {e.source for e in events.values()} == {"import"}
    assert result.report.sources["enquiry_quote_events"]["dropped"] == {
        "'drafted' (not a send or a view)": 1
    }


def test_venue_profile_and_templates(w):
    _run(w)
    deck, snug = sorted(
        _rows(w, fm.FnVenueArea, venue_id=w.venue.id), key=lambda a: a.sort_order
    )
    assert (deck.name, deck.booking_types, deck.requires_approval) == (
        "Deck",
        ["large_event_exclusive"],
        True,
    )
    assert snug.booking_types == ["group_booking"]  # 'bogus' is not a Norm type
    settings = _rows(w, fm.FnVenueFunctionSettings, venue_id=w.venue.id)[0]
    assert settings.approver_user_id == w.approver.id
    assert settings.override_approver_user_id is None  # George is not a Norm user
    assert settings.policies.startswith("Overview\nRooftop bar.")
    assert settings.enabled_fields == {
        "group_booking": {"beverages.bar_tab": True},
        "seated_dining": {"details.av": False},
    }
    assert settings.menu_links[0]["url"] == "https://brew.example/food"
    brand = _rows(w, fm.FnVenueBrand)[0]
    assert brand.terms == ["Minimum spend — Food and drink count."]
    assert brand.colours["primary"] == "#0e5340" and brand.fonts == {
        "heading": "Georgia",
        "body": "Helvetica",
    }
    assert (brand.valid_days, brand.template_html) == (21, None)
    replies = {r.name: r for r in _rows(w, fm.FnReplyTemplate)}
    assert (replies["First reply"].stage, replies["First reply"].booking_type) == (
        "first_reply",
        "group_booking",
    )
    assert (
        replies["Nudge"].booking_type is None and replies["Nudge"].stage == "follow_up"
    )
    assert (
        replies["Nudge"].body_html
        == "<p>Line one<br>line two</p><p>Para two &amp; more</p>"
    )
    set_template = _one(
        w, fm.FnPreorderTemplate, _id(w, "preorder_templates", oid("pt.set"))
    )
    assert set_template.price_per_person_cents == 6500
    steak = _one(
        w, fm.FnPreorderTemplateItem, _id(w, "preorder_template_items", oid("pti2"))
    )
    assert (steak.unit_price_cents, steak.default_percent, steak.loaded_recipe_id) == (
        None,
        60,
        "recipe-1",
    )
    arancini = _one(
        w, fm.FnPreorderTemplateItem, _id(w, "preorder_template_items", oid("pti3"))
    )
    assert (arancini.unit_price_cents, arancini.loaded_stock_item_id) == (
        450,
        "stock-9",
    )


def test_brand_panel_is_a_readable_light_box(w):
    """Orbit's panelColor is a dark band with light text; Norm's panel sits
    behind body text. Glass Goose's import put its menus at 1.3:1."""
    from app.services.functions.importer import _contrast

    fake = _fake(w)
    brand = fake.tables["venue_brand_styles"][0]
    brand.update(accent_color="#0E5340", panel_color="#0E5340")  # Glass Goose
    brand["style"] = {
        **brand["style"],
        "foreground": "#1F3D2F",
        "background": "transparent",
    }
    result, _ = _run(w, fake)
    colours = _rows(w, fm.FnVenueBrand)[0].colours
    assert colours["background"] == "#ffffff"  # Orbit's rule for see-through
    assert colours["panel"] == "#eef3f2"  # the accent at 7%: Orbit's own soft tint
    assert _contrast(colours["text"], colours["panel"]) > 10
    assert (
        result.report.tallies["brand"][
            "Orbit panel colour (a dark band) not used as Norm's panel (a light box)"
        ]
        == 1
    )


def test_brand_colours_must_be_hex_and_readable(w):
    fake = _fake(w)
    brand = fake.tables["venue_brand_styles"][0]
    # Text the same colour as the accent: no tint of it can carry that text.
    brand.update(accent_color="#111111")
    brand["style"] = {
        **brand["style"],
        "foreground": "#111111",
        "headingColor": "rgba(0,0,0,.8)",
    }
    result, _ = _run(w, fake)
    colours = _rows(w, fm.FnVenueBrand)[0].colours
    assert (
        colours["primary"] == "#111111"
    )  # the non-hex heading colour fell back to the accent
    assert colours["text"] == "#111111"
    assert "panel" in colours  # the 7% grey still reads


def test_web_forms(w):
    result, _ = _run(w)
    form = _one(w, fm.FnWebForm, _id(w, "web_forms", WF1))
    assert (form.venue_id, form.slug, form.honeypot_field, form.submit_label) == (
        w.venue2.id,
        "garden-enquiry",
        "hp_1",
        "Send",
    )
    # camelCase → snake_case, only settings forms.js applies, plain values only.
    assert form.theme == {
        "brand_color": "#123456",
        "body_font": "Inter",
        "input_radius": "8",
    }
    themes = result.report.tallies["form themes"]
    assert themes["'checkboxShape' dropped (not a theme setting forms.js applies)"] == 1
    assert themes["'headingColor' dropped (not a plain value)"] == 1

    fields = {f.key: f for f in _rows(w, fm.FnWebFormField, form_id=form.id)}
    shapes = {k: (f.type, f.maps_to) for k, f in fields.items()}
    assert shapes == {
        "name": ("name", "contact.name"),
        "email": ("email", "contact.email"),
        "message": ("long_text", "client_note"),
        "type_of_event": ("choice", "booking_type"),
        # The lost-leads bug: a multi-date answer must fill preferred_dates.
        "preferred_dates": ("dates", "preferred_dates"),
        "contact_section": ("section", None),
        "start_finish_times": ("time_range", "preferred_time"),
        "estimated_group_size": ("number", "guests"),
        "area_preference": ("choice", None),
        "company_name": ("text", "contact.company"),
        "extras": ("checkbox", None),
    }
    assert fields["contact_section"].label == "Your details"  # blank in Orbit
    assert fields["estimated_group_size"].options == {"min": 1.0, "max": 400.0}
    assert fields["area_preference"].options == [
        {"value": "seated_dining", "label": "Seated dinner"},
        {"value": "party", "label": "Party"},
    ]
    sub = _one(w, fm.FnFormSubmission, _id(w, "web_form_submissions", SUB1))
    assert (sub.utm_campaign, sub.gclid, sub.ref_code, sub.delivery_status) == (
        "xmas",
        "gclid-1",
        "r1",
        "sent",
    )
    assert sub.enquiry_id == _id(w, "enquiries", E3)


def test_imported_forms_pass_f5s_own_rules(w):
    """What F9 found live: F5's builder must re-save an imported form as it
    is, and a submission with several dates must make a lead, not a 500."""
    from app.services.functions import forms

    _run(w)
    form = _one(w, fm.FnWebForm, _id(w, "web_forms", WF1))
    fields = sorted(
        _rows(w, fm.FnWebFormField, form_id=form.id), key=lambda f: f.sort_order
    )
    assert forms._clean_theme(form.theme) == form.theme
    as_saved = [
        {k: getattr(f, k) for k in ("key", "label", "type", "required", "options",
                                     "placeholder", "help_text", "maps_to")}
        for f in fields
    ]  # fmt: skip
    assert [
        {k: v for k, v in f.items() if k != "sort_order"}
        for f in forms._clean_fields(as_saved)
    ] == as_saved

    answers = forms.validate_answers(
        fields,
        {
            "name": "Pat Client",
            "email": "pat@example.com",
            "preferred_dates": ["2026-12-18", "2026-12-11"],
            "start_finish_times": {"start": "18:00", "finish": "23:00"},
            "estimated_group_size": "40",
            "type_of_event": "seated_dining",
        },
    )
    lead = forms.lead_from(form, fields, answers)
    assert (lead["preferred_date_start"], lead["preferred_date_end"]) == (
        dt.date(2026, 12, 11),
        dt.date(2026, 12, 18),
    )
    assert (lead["preferred_start_time"], lead["guests"], lead["booking_type"]) == (
        dt.time(18, 0),
        40,
        "seated_dining",
    )


def test_theme_keys_match_forms_js():
    """The importer keeps only the theme settings forms.js applies; the two
    lists must not drift."""
    import pathlib
    import re as _re

    from app.services.functions.importer import FORM_THEME_KEYS

    js = (
        pathlib.Path(__file__).resolve().parents[2] / "web/public/embed/forms.js"
    ).read_text()
    tables = _re.findall(r"var THEME_(?:ALIASES|KEYS) = \{(.*?)\n  \};", js, _re.S)
    keys = [k for body in tables for k in _re.findall(r"^\s+([a-z_]+):", body, _re.M)]
    assert len(keys) > 40
    assert sorted(FORM_THEME_KEYS) == sorted(keys)


def test_a_large_theme_keeps_every_known_setting(w):
    fake = _fake(w)
    form = fake.tables["web_forms"][0]
    form["style"] = {
        **{f"section{p}": "1" for p in ("TitleSpacing", "TitleMarginTop", "TitleMarginBottom")},
        **{k: "4" for k in ("inputPaddingX", "inputPaddingY", "buttonPaddingX", "buttonPaddingY")},
        **{k: "#111111" for k in (
            "background", "foreground", "headingColor", "labelColor", "inputBackground",
            "inputTextColor", "inputBorderColor", "buttonBackground", "buttonTextColor",
            "buttonBorderColor", "sectionTitleColor", "mutedForeground", "inputFocusColor",
            "inputPlaceholderColor")},
        **{k: "10" for k in (
            "maxWidth", "fieldGap", "bodySize", "headingSize", "labelSize", "inputBorderWidth",
            "inputRadius", "textareaRadius", "buttonRadius", "buttonBorderWidth",
            "sectionTitleSize", "headingWeight", "labelWeight", "buttonWeight")},
    }  # fmt: skip
    result, _ = _run(w, fake)
    theme = _one(w, fm.FnWebForm, _id(w, "web_forms", WF1)).theme
    assert len(theme) == 36  # 35 settings + brand_color: no cap (F5 takes them all)
    assert {
        "max_width",
        "input_padding_x",
        "section_title_margin_top",
        "brand_color",
    } <= set(theme)
    assert not [
        k for k in result.report.tallies.get("form themes", {}) if "dropped" in k
    ]


def test_mail(w):
    result, _ = _run(w)
    accounts = {a.google_email: a for a in _rows(w, fm.FnMailboxAccount)}
    brew = accounts["events@brewbar.example"]
    assert (brew.id, brew.status, brew.connection_id, brew.signature_html) == (
        _id(w, "venue_gmail_accounts", GA1),
        "needs_reconnect",
        None,
        "<p>Brew</p>",
    )
    assert accounts["garden@example.com"].id == _id(w, "venue_gmail_accounts", GA2)
    labels = {
        lab.gmail_label_id: lab
        for lab in _rows(w, fm.FnMailboxLabel, account_id=brew.id)
    }
    assert (
        labels["Label_1"].folder,
        labels["Label_2"].folder,
        labels["INBOX"].folder,
    ) == ("enquiry", "reservation", None)
    assert labels["INBOX"].label_type == "system"

    messages = {
        m.gmail_message_id: m for m in _rows(w, fm.FnMailboxMessage, account_id=brew.id)
    }
    assert set(messages) == {"m-in", "m-out"}  # the draft is not imported
    assert (messages["m-out"].is_inbound, messages["m-out"].is_internal) == (
        False,
        False,
    )
    thread = _rows(w, fm.FnMailboxThread, account_id=brew.id)[0]
    assert (thread.gmail_thread_id, thread.message_count, thread.unread_count) == (
        T1,
        2,
        1,
    )
    assert messages["m-in"].thread_id == thread.id

    links = {
        (lnk.enquiry_id, lnk.unlinked_at is None)
        for lnk in _rows(w, fm.FnThreadLink, account_id=brew.id)
    }
    assert links == {(_id(w, "enquiries", E4), True), (_id(w, "enquiries", E1), False)}
    assert result.report.sources["enquiry_gmail_threads"]["dropped"] == {
        "mailbox unknown (not a venue mailbox)": 1
    }
    folders = {
        f.gmail_thread_id: f.folder
        for f in _rows(w, fm.FnThreadFolder, account_id=brew.id)
    }
    assert folders == {
        T1: "enquiry",
        T9: "reservation",
    }  # Orbit's precedence for two labels


def test_derived_state_matches_orbit(w):
    result, _ = _run(w)
    stages = {
        r.enquiry_id: r.stage
        for r in w.db.execute(
            select(
                status.enquiry_status_select(
                    w.org.id, NOW, valid_tz=status.valid_timezones(w.db, w.org.id)
                ).subquery()
            )
        )
    }
    assert stages[_id(w, "enquiries", E1)] == "lost"
    assert stages[_id(w, "enquiries", E2)] == "confirmed"
    assert stages[_id(w, "enquiries", E3)] == "quoted"
    assert (
        stages[_id(w, "enquiries", E4)] == "replied"
    )  # outbound mail on the linked thread
    assert stages[_id(w, "enquiries", E5)] == "new"
    f1 = status.event_status_for(w.db, w.org.id, _id(w, "venue_functions", F1))
    assert (f1.status, f1.edited_since_confirmed) == ("confirmed", True)
    assert (
        status.event_status_for(w.db, w.org.id, _id(w, "venue_functions", F2)).status
        == "sent_to_client"
    )
    assert (
        status.event_status_for(w.db, w.org.id, _id(w, "venue_functions", F3)).status
        == "cancelled"
    )

    parity = result.report.parity
    assert parity["enquiries"]["match"] == parity["enquiries"]["total"] == 5
    assert parity["events"]["match"] == parity["events"]["total"] == 4
    # Orbit-"confirmed" without a booking: handled by rule, not compared.
    assert {
        g: r["total"] for g, r in parity["enquiries"]["reviewed_by_rule"].items()
    } == {
        "archived": 1,
        "follow_up": 2,
    }


def test_unbooked_confirmed_enquiries_follow_the_rule(w):
    """Orbit says confirmed, nothing is booked: no event is invented. Recent
    ones get a follow-up dated today (the run's clock, venue-local) so they
    surface; historic ones are archived with a note."""
    result, _ = _run(w)
    recent = _one(w, fm.FnEnquiry, _id(w, "enquiries", E7))  # preferred date ahead
    assert (recent.follow_up_date, recent.follow_up_reason, recent.archived_at) == (
        dt.date(2026, 10, 12),  # NOW is 15:00 on 12 Oct in Auckland
        UNBOOKED_FOLLOW_UP,
        None,
    )
    assert "Orbit callback: 2026-10-30 Check numbers" in recent.notes
    new = _one(w, fm.FnEnquiry, _id(w, "enquiries", E9))  # created within 60 days
    assert (new.follow_up_date, new.archived_at) == (dt.date(2026, 10, 12), None)
    old = _one(w, fm.FnEnquiry, _id(w, "enquiries", E8))
    assert (old.archived_at, old.follow_up_date) == (NOW, None)
    assert old.notes == f"Loves the deck\n\n{UNBOOKED_ARCHIVE_NOTE}"
    assert _rows(w, fm.FnEvent, enquiry_id=old.id) == []

    reviewed = result.report.reviewed
    assert [x["orbit_id"] for x in reviewed["follow_up"]] == [E7, E9]
    assert [(x["orbit_id"], x["why"]) for x in reviewed["archived"]] == [
        (E8, "approved minimum spend, no booking")
    ]
    assert reviewed["follow_up"][0]["why"] == "status set by hand, no booking"
    assert "reviewed by rule" in "\n".join(result.report.lines())

    stage, action = status.enquiry_status(w.db, w.org.id, recent.id, NOW)
    assert (action.kind, action.due_today) == ("requested", True)


def test_the_runs_clock_decides_recent_or_historic(w):
    later = dt.datetime(2027, 6, 1, tzinfo=UTC)
    tables, files = orbit_dataset(approver_email=w.approver.email)
    run_import(
        w.db,
        FakeOrbit(tables, files).client(),
        org=w.org.id,
        skip_venues=SKIP,
        commit=False,
        now=later,
        log=lambda *a: None,
    )
    e7 = _one(w, fm.FnEnquiry, _id(w, "enquiries", E7))
    # Historic by then: archived, Orbit's own callback left as it was.
    assert (e7.archived_at, e7.follow_up_date) == (later, dt.date(2026, 10, 30))
    assert e7.notes.endswith(UNBOOKED_ARCHIVE_NOTE)


# ---------------------------------------------------------------------------
# Helpers and the CLI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("18:30", dt.time(18, 30)),
        ("6:30pm", dt.time(18, 30)),
        ("6pm", dt.time(18)),
        ("12am", dt.time(0)),
        ("12:15 am", dt.time(0, 15)),
        ("18.45", dt.time(18, 45)),
        ("7:05:30", dt.time(7, 5, 30)),
        ("", None),
        (None, None),
        ("7", False),
        ("after speeches", False),
        ("25:00", False),
        ("13pm", False),
    ],
)
def test_parse_clock(raw, expected):
    assert parse_clock(raw) == expected


def test_to_cents_rounds_half_up():
    assert to_cents(1500.555) == 150056
    assert to_cents("12.345") == 1235
    assert to_cents(0.005) == 1
    assert to_cents(None) is None and to_cents("n/a") is None


def test_cli_dry_run_and_refusal(w, capsys):
    from scripts.migrate_orbit_functions import main

    fake = _fake(w)
    code = main(
        ["--org", w.org.slug, "--dry-run", "--skip-venue", "Closed Venue"],
        orbit=fake.client(),
        db=w.db,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "DRY RUN" in out and "sanity gate: PASSED" in out and "parity" in out
    assert _rows(w, fm.FnEnquiry) == []
    assert main(["--org", w.org.slug, "--dry-run"], orbit=fake.client(), db=w.db) == 1
    assert "refused: Orbit venues with data" in capsys.readouterr().out
    assert (
        main(
            ["--org", w.org.slug, "--threshold", "nonsense=1"],
            orbit=fake.client(),
            db=w.db,
        )
        == 1
    )
