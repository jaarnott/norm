"""Suggested prompts on Home (Oct 2026, docs/plans/home-suggested-prompts.md).

A suggestion that fails is worse than none, so most of these pin eligibility:
the member hired, the App on, the permission held, the venue's connection
working. The rest pin repeats and feedback — an insight shown this week stays
away, a dismissed one stays away, and what a person told Norm about it
(``insight_notes``) quietens it until it moves well past that level.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import (
    Connection,
    Insight,
    InsightNote,
    Role,
    SuggestionEvent,
    Thread,
)
from app.services.suggestions import engine
from app.services.suggestions.catalog import CATALOG
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_user,
    _make_venue,
    _make_venue_access,
)

# Monday 10:00 in Auckland: Monday-only prompts are timely.
MONDAY_MORNING = datetime(2026, 10, 18, 21, 0, tzinfo=timezone.utc)
MANAGER = [
    "reports:read",
    "orders:read",
    "roster:read",
    "roster:write",
    "apps:build",
    "hr:read",
]


@pytest.fixture()
def org(db_session):
    return _make_organization(db_session, name="Cook Brothers")


@pytest.fixture()
def lz(db_session, org):
    v = _make_venue(db_session, name="La Zeppa", organization_id=org.id)
    v.timezone = "Pacific/Auckland"
    db_session.add(
        Connection(connector_name="loadedhub", venue_id=v.id, enabled="true", config={})
    )
    db_session.flush()
    return v


def person(db_session, org, venues, permissions=MANAGER, email=None):
    user = _make_user(db_session, email=email or f"m-{uuid.uuid4().hex[:6]}@test.local")
    m = _make_membership(db_session, user, org)
    role = Role(
        organization_id=org.id,
        name=f"r{uuid.uuid4().hex[:6]}",
        display_name="Manager",
        permissions=permissions,
    )
    db_session.add(role)
    db_session.flush()
    m.role_id = role.id
    for v in venues:
        _make_venue_access(db_session, user, v)
    db_session.flush()
    return user


def insight(
    db_session,
    org,
    venue,
    *,
    detector="quiet_day_wages",
    subject="Tuesday",
    area="wages",
    audience="reports:read",
    value=1171.0,
    headline=None,
):
    row = Insight(
        organization_id=org.id,
        venue_id=venue.id,
        detector=detector,
        subject=subject,
        subject_key=f"{venue.id}:{detector}:{subject}",
        area=area,
        audience=audience,
        headline=headline or f"Tuesdays at {venue.name} run wages at 104% of sales",
        prompt="Over the last 4 weeks, Tuesdays …",
        value=value,
        as_of="2026-10-18",
        expires_at=MONDAY_MORNING + timedelta(days=5),
    )
    db_session.add(row)
    db_session.flush()
    return row


def ids(out):
    return [s["id"] for s in out]


def run(db_session, user, venue=None, member=None, limit=3, now=MONDAY_MORNING):
    return engine.suggestions_for(
        db_session,
        db_session,
        user,
        member,
        venue.id if venue else None,
        limit,
        now=now,
    )


# ------------------------------------------------------------------ catalog
def test_every_catalog_prompt_is_answered_by_its_apps_real_tools():
    """Haiku drafted, a person reviewed: an entry may only name tools its App
    claims in the reviewed catalog seed, and only real members."""
    from scripts.sync_marketplace_catalog import APPS

    apps = {a["slug"]: a for a in APPS}
    members = {(a.get("composition") or {}).get("member") for a in APPS} | {"norm"}
    for e in CATALOG:
        assert e.app in apps, e.id
        comp = apps[e.app].get("composition") or {}
        assert set(e.tools) <= set(comp.get("tools") or []), e.id
        assert e.member in members, e.id
        assert len(e.text) <= 80, e.id


def test_catalog_ids_are_unique():
    assert len({e.id for e in CATALOG}) == len(CATALOG)


# ------------------------------------------------------------------ eligibility
def test_three_varied_suggestions_naming_the_venue(db_session, org, lz):
    user = person(db_session, org, [lz])
    out = run(db_session, user, lz)
    assert len(out) == 3
    assert len({s["member"] for s in out}) == 3
    assert any("La Zeppa" in s["text"] for s in out)
    assert all(s["prompt"] for s in out)


def test_a_phone_asks_for_two(db_session, org, lz):
    user = person(db_session, org, [lz])
    assert len(run(db_session, user, lz, limit=2)) == 2


def test_no_permission_no_suggestion(db_session, org, lz):
    user = person(db_session, org, [lz], permissions=["apps:build"])
    out = run(db_session, user, lz)
    assert out and all(
        s["member"] in ("app_builder", "norm", "executive_chef") for s in out
    )


def test_a_dead_connection_offers_nothing_that_needs_it(db_session, org, lz):
    conn = db_session.query(Connection).filter(Connection.venue_id == lz.id).one()
    conn.needs_reconnect = True
    db_session.flush()
    user = person(db_session, org, [lz])
    out = run(db_session, user, lz)
    loaded = {e.id for e in CATALOG if e.connection == "loadedhub"}
    assert not {i.split(":", 1)[1] for i in ids(out)} & loaded


def test_only_hired_members_and_apps_that_are_on(db_session, org, lz, monkeypatch):
    from app.services import entitlements

    monkeypatch.setattr(entitlements, "hired_agent_slugs", lambda *a: {"procurement"})
    monkeypatch.setattr(
        entitlements, "apps_on", lambda *a: {"loaded-stock", "norm-core"}
    )
    user = person(db_session, org, [lz])
    out = run(db_session, user, lz)
    assert {s["member"] for s in out} <= {"procurement", "norm"}


def test_a_member_s_new_chat_shows_that_member_only(db_session, org, lz):
    user = person(db_session, org, [lz])
    out = run(db_session, user, lz, member="procurement")
    assert out and {s["member"] for s in out} == {"procurement"}


def test_group_prompts_need_more_than_one_venue(db_session, org, lz):
    user = person(db_session, org, [lz])
    assert "catalog:reports.group_last_week" not in ids(
        engine.catalog_candidates(
            engine.Context(db_session, db_session, user, lz.id, None, MONDAY_MORNING)
        )
    )


def test_timing_is_the_venue_s_own_clock(db_session, org, lz):
    user = person(db_session, org, [lz])
    monday = {
        c["id"]
        for c in engine.catalog_candidates(
            engine.Context(db_session, db_session, user, lz.id, None, MONDAY_MORNING)
        )
    }
    wednesday = {
        c["id"]
        for c in engine.catalog_candidates(
            engine.Context(
                db_session,
                db_session,
                user,
                lz.id,
                None,
                MONDAY_MORNING + timedelta(days=2),
            )
        )
    }
    assert "catalog:reports.weekend_vs_last_year" in monday
    assert "catalog:reports.weekend_vs_last_year" not in wednesday


# ------------------------------------------------------------------ insights
def test_an_insight_leads(db_session, org, lz):
    row = insight(db_session, org, lz)
    user = person(db_session, org, [lz])
    out = run(db_session, user, lz)
    assert out[0]["id"] == f"insight:{row.id}"
    assert out[0]["kind"] == "insight" and out[0]["venue_id"] == lz.id


def test_at_most_two_insights(db_session, org, lz):
    for i, area in enumerate(("wages", "sales", "cogs")):
        insight(
            db_session,
            org,
            lz,
            detector=f"d{i}",
            subject="s",
            area=area,
            audience="reports:read" if area != "cogs" else "orders:read",
            value=100.0 * (i + 1),
        )
    user = person(db_session, org, [lz])
    assert sum(1 for s in run(db_session, user, lz) if s["kind"] == "insight") == 2


def test_an_insight_for_roster_managers_is_hidden_from_others(db_session, org, lz):
    insight(
        db_session,
        org,
        lz,
        detector="staff_over_roster",
        subject="staff",
        audience="roster:write",
    )
    user = person(db_session, org, [lz], permissions=["reports:read"])
    assert not any(s["kind"] == "insight" for s in run(db_session, user, lz))


def test_an_insight_about_a_venue_you_cannot_see_is_hidden(db_session, org, lz):
    other = _make_venue(db_session, name="Mr Murdochs", organization_id=org.id)
    insight(db_session, org, other)
    user = person(db_session, org, [lz])
    assert not any(s["kind"] == "insight" for s in run(db_session, user, lz))


def test_an_explained_insight_goes_quiet_until_it_moves(db_session, org, lz):
    row = insight(
        db_session,
        org,
        lz,
        detector="discount",
        subject="First Table",
        area="sales",
        value=4183.0,
        headline="First Table discounts cost La Zeppa $16,733 in 4 weeks",
    )
    user = person(db_session, org, [lz])
    db_session.add(
        InsightNote(
            organization_id=org.id,
            venue_id=lz.id,
            subject_key=row.subject_key,
            detector=row.detector,
            verdict="expected",
            reason="First Table promo",
            until="2026-11-30",
            baseline=4183.0,
            created_by=user.id,
            created_at=MONDAY_MORNING - timedelta(days=3),
        )
    )
    db_session.flush()
    assert f"insight:{row.id}" not in ids(run(db_session, user, lz))
    row.value = 6500.0  # +55% on the level that was explained
    db_session.flush()
    assert f"insight:{row.id}" in ids(run(db_session, user, lz))


def test_an_explanation_quietens_it_for_the_whole_organisation(db_session, org, lz):
    row = insight(db_session, org, lz)
    manager = person(db_session, org, [lz])
    owner = person(db_session, org, [lz])
    db_session.add(
        InsightNote(
            organization_id=org.id,
            venue_id=lz.id,
            subject_key=row.subject_key,
            detector=row.detector,
            verdict="expected",
            reason="minimum crew",
            baseline=row.value,
            created_by=manager.id,
            created_at=MONDAY_MORNING - timedelta(days=1),
        )
    )
    db_session.flush()
    assert f"insight:{row.id}" not in ids(run(db_session, owner, lz))


def test_shown_earlier_this_week_stays_away_unless_it_moved(db_session, org, lz):
    row = insight(db_session, org, lz)
    user = person(db_session, org, [lz])
    db_session.add(
        SuggestionEvent(
            user_id=user.id,
            organization_id=org.id,
            suggestion_id=f"insight:{row.id}",
            subject_key=row.subject_key,
            event="shown",
            value=row.value,
            created_at=MONDAY_MORNING - timedelta(days=3),
        )
    )
    db_session.flush()
    assert f"insight:{row.id}" not in ids(run(db_session, user, lz))
    row.value = float(row.value) * 1.4
    db_session.flush()
    assert f"insight:{row.id}" in ids(run(db_session, user, lz))


def test_shown_today_stays_put_for_the_day(db_session, org, lz):
    row = insight(db_session, org, lz)
    user = person(db_session, org, [lz])
    db_session.add(
        SuggestionEvent(
            user_id=user.id,
            organization_id=org.id,
            suggestion_id=f"insight:{row.id}",
            subject_key=row.subject_key,
            event="shown",
            value=row.value,
            created_at=MONDAY_MORNING - timedelta(hours=1),
        )
    )
    db_session.flush()
    assert f"insight:{row.id}" in ids(run(db_session, user, lz))


def test_dismissed_stays_away(db_session, org, lz):
    user = person(db_session, org, [lz])
    first = run(db_session, user, lz)
    gone = first[-1]["id"]
    db_session.add(
        SuggestionEvent(
            user_id=user.id,
            organization_id=org.id,
            suggestion_id=gone,
            event="dismissed",
            created_at=MONDAY_MORNING,
        )
    )
    db_session.flush()
    assert gone not in ids(run(db_session, user, lz))


# ------------------------------------------------------------------ routes
def _login(client, user):
    from app.auth.security import create_access_token

    token = create_access_token({"sub": user.id})
    return {"Authorization": f"Bearer {token}"}


def test_suggestions_route_and_events(client, db_session, org, lz):
    user = person(db_session, org, [lz])
    row = insight(db_session, org, lz)
    h = _login(client, user)
    res = client.get(f"/api/suggestions?venue_id={lz.id}&limit=2", headers=h)
    assert res.status_code == 200
    out = res.json()["suggestions"]
    assert len(out) == 2 and out[0]["id"] == f"insight:{row.id}"
    res = client.post(
        "/api/suggestions/events",
        headers=h,
        json={
            "events": [
                {"suggestion_id": out[0]["id"], "event": "shown"},
                {"suggestion_id": out[1]["id"], "event": "dismissed"},
            ]
        },
    )
    assert res.status_code == 200
    ev = {
        e.event: e
        for e in db_session.query(SuggestionEvent).filter_by(user_id=user.id).all()
    }
    assert (
        ev["shown"].subject_key == row.subject_key
        and float(ev["shown"].value) == 1171.0
    )
    assert ev["dismissed"].suggestion_id == out[1]["id"]


def test_admin_routes_are_admin_only(client, db_session, org, lz):
    user = person(db_session, org, [lz])
    h = _login(client, user)
    assert client.get("/api/admin/insights", headers=h).status_code == 403
    assert (
        client.post(
            "/api/admin/insights/run", headers=h, json={"venue_ids": [lz.id]}
        ).status_code
        == 403
    )


# ------------------------------------------------------------------ the conversation
def test_a_clicked_insight_follows_its_conversation(db_session, org, lz, monkeypatch):
    """The click is recorded, the new thread is about the insight's venue and
    remembers it, and every turn — the first and later ones — is told it."""
    from app.services import supervisor

    row = insight(db_session, org, lz)
    user = person(db_session, org, [lz])
    calls = []

    class FakeNorm:
        def handle_message(self, message, db, user_id, thread_id=None, **kw):
            calls.append(kw)
            if thread_id is None:
                t = Thread(
                    user_id=user_id,
                    venue_id=kw.get("venue_id"),
                    title="t",
                    raw_prompt=message,
                )
                db.add(t)
                db.flush()
                thread_id = t.id
            return {"id": thread_id}

    monkeypatch.setattr(supervisor, "norm_agent", lambda: FakeNorm())
    monkeypatch.setattr(
        "app.services.billing_service.check_quota_for_user", lambda *a, **k: None
    )
    out = supervisor.handle_message(
        "Tuesdays …", db_session, db_session, user.id, suggestion_id=f"insight:{row.id}"
    )
    thread = db_session.query(Thread).filter(Thread.id == out["id"]).one()
    assert thread.insight_id == row.id and thread.venue_id == lz.id
    assert calls[0]["insight"]["headline"] == row.headline
    clicked = (
        db_session.query(SuggestionEvent)
        .filter_by(user_id=user.id, event="clicked")
        .one()
    )
    assert clicked.subject_key == row.subject_key

    supervisor.handle_message(
        "It's our minimum crew", db_session, db_session, user.id, thread_id=thread.id
    )
    assert calls[1]["insight"]["id"] == row.id


def test_someone_else_s_insight_is_not_carried(db_session, org, lz, monkeypatch):
    from app.services.insights.context import from_suggestion

    other_org = _make_organization(db_session, name="Other")
    other = _make_venue(db_session, name="Elsewhere", organization_id=other_org.id)
    row = insight(db_session, other_org, other)
    user = person(db_session, org, [lz])
    assert from_suggestion(db_session, user.id, f"insight:{row.id}") is None


def test_the_prompt_tells_norm_to_record_what_the_person_says():
    from app.services.insights.context import guidance

    text = guidance(
        {
            "id": "i1",
            "venue": "La Zeppa",
            "as_of": "2026-10-18",
            "headline": "First Table discounts cost La Zeppa $16,733 in 4 weeks",
            "notes": [
                {"verdict": "expected", "reason": "promo", "until": "2026-11-30"}
            ],
        }
    )
    assert "note_insight" in text and "`i1`" in text
    assert "expected: promo (until 2026-11-30)" in text
    assert guidance(None) == ""


# ------------------------------------------------------------------ note_insight
def _note(db_session, thread, **params):
    from app.agents.internal_tools import get_handler

    return get_handler("norm", "note_insight")(params, db_session, thread.id)


def test_note_insight_records_against_the_conversation_s_insight(db_session, org, lz):
    row = insight(
        db_session,
        org,
        lz,
        detector="discount",
        subject="First Table",
        area="sales",
        value=4183.0,
    )
    user = person(db_session, org, [lz])
    thread = Thread(user_id=user.id, venue_id=lz.id, insight_id=row.id, raw_prompt="x")
    db_session.add(thread)
    db_session.flush()
    out = _note(
        db_session,
        thread,
        verdict="expected",
        reason="First Table promo",
        until="2026-11-30",
    )
    assert out["success"], out
    note = db_session.query(InsightNote).filter_by(subject_key=row.subject_key).one()
    assert (note.verdict, note.until, float(note.baseline), note.created_by) == (
        "expected",
        "2026-11-30",
        4183.0,
        user.id,
    )
    assert "until 2026-11-30" in out["data"]["what_happens"]


def test_note_insight_refuses_without_an_insight_or_a_bad_verdict(db_session, org, lz):
    user = person(db_session, org, [lz])
    thread = Thread(user_id=user.id, venue_id=lz.id, raw_prompt="x")
    db_session.add(thread)
    db_session.flush()
    assert not _note(db_session, thread, verdict="expected")["success"]
    row = insight(db_session, org, lz)
    thread.insight_id = row.id
    db_session.flush()
    assert not _note(db_session, thread, verdict="sure")["success"]
    assert not _note(db_session, thread, verdict="expected", until="end of Nov")[
        "success"
    ]


def test_note_insight_cannot_touch_another_organisation_s_insight(db_session, org, lz):
    other_org = _make_organization(db_session, name="Other")
    other = _make_venue(db_session, name="Elsewhere", organization_id=other_org.id)
    foreign = insight(db_session, other_org, other)
    user = person(db_session, org, [lz])
    thread = Thread(user_id=user.id, venue_id=lz.id, raw_prompt="x")
    db_session.add(thread)
    db_session.flush()
    assert not _note(db_session, thread, verdict="expected", insight_id=foreign.id)[
        "success"
    ]


# ------------------------------------------------------------------ Settings → Suggestions
def _note_for(db_session, org, venue, user, **kw):
    row = insight(
        db_session,
        org,
        venue,
        detector="discount",
        subject="First Table",
        area="sales",
        headline="First Table discounts cost La Zeppa $16,733 in 4 weeks",
    )
    note = InsightNote(
        organization_id=org.id,
        venue_id=venue.id,
        subject_key=row.subject_key,
        detector=row.detector,
        verdict=kw.get("verdict", "expected"),
        reason=kw.get("reason", "First Table promo"),
        until=kw.get("until"),
        baseline=row.value,
        created_by=user.id,
    )
    db_session.add(note)
    db_session.flush()
    return note


def test_notes_are_listed_with_what_they_are_about(client, db_session, org, lz):
    user = person(db_session, org, [lz])
    note = _note_for(db_session, org, lz, user, until="2026-11-30")
    out = client.get("/api/insight-notes", headers=_login(client, user)).json()["notes"]
    assert [n["id"] for n in out] == [note.id]
    n = out[0]
    assert n["about"] == "First Table discounts cost La Zeppa $16,733 in 4 weeks"
    assert (
        n["verdict_label"],
        n["reason"],
        n["until"],
        n["venue"],
        n["can_clear"],
    ) == ("Expected", "First Table promo", "2026-11-30", "La Zeppa", True)


def test_a_note_is_cleared_by_its_maker_or_an_owner_only(client, db_session, org, lz):
    maker = person(db_session, org, [lz])
    other = person(db_session, org, [lz])
    owner = person(db_session, org, [lz], permissions=MANAGER + ["org:manage"])
    note = _note_for(db_session, org, lz, maker)
    assert (
        client.delete(
            f"/api/insight-notes/{note.id}", headers=_login(client, other)
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/insight-notes/{note.id}", headers=_login(client, owner)
        ).status_code
        == 200
    )
    assert db_session.query(InsightNote).filter_by(id=note.id).first() is None


def test_another_organisation_s_notes_are_invisible(client, db_session, org, lz):
    other_org = _make_organization(db_session, name="Other")
    elsewhere = _make_venue(db_session, name="Elsewhere", organization_id=other_org.id)
    stranger = person(db_session, other_org, [elsewhere])
    note = _note_for(db_session, other_org, elsewhere, stranger)
    me = person(db_session, org, [lz], permissions=MANAGER + ["org:manage"])
    h = _login(client, me)
    assert client.get("/api/insight-notes", headers=h).json()["notes"] == []
    assert client.delete(f"/api/insight-notes/{note.id}", headers=h).status_code == 404
