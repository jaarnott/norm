"""Home's suggestions tuned site-wide from the config DB (services/suggestions/
config.py; docs/plans/suggestions-config.md).

An empty table is the defaults exactly — every other suggestion and detector
test runs on it. Here: an override reaches the detectors, the ranking and the
prompts; a bad value is refused on save and ignored on read, so a typo never
empties Home; reset and undo; and the admin page's routes (platform admins
only).
"""

# The shared fixtures are imported (org, lz) and then taken as arguments.
# ruff: noqa: F811

from __future__ import annotations

from app.db.config_models import SuggestionConfig
from app.services.insights import detectors as det
from app.services.insights import rules, runner, wording
from app.services.suggestions import config
from tests.test_insight_detectors import labour, sales, venue
from tests.test_suggestions import (  # noqa: F401 — fixtures
    insight,
    lz,
    org,
    person,
    run,
)

QUIET_TUESDAY = dict(
    sales_daily=sales(by_weekday={1: 500.0}),
    labour_daily=labour(by_weekday={1: 450.0}),
)


def effective(db_session):
    config.clear_cache()
    return config.current(db_session)


# ------------------------------------------------------------------ defaults
def test_an_empty_table_is_the_code_s_defaults(db_session):
    cfg = effective(db_session)
    assert cfg.settings["insight_slots_desktop"] == 3
    assert [e.id for e in cfg.prompts] == [e.id for e in config.CATALOG]
    assert cfg.detector("price_rise")["weight"] == 1.4
    assert cfg.rules == {} and cfg.wording_version == wording.VERSION


# ------------------------------------------------------------------ detectors
def test_a_threshold_and_a_template_reach_the_detector(db_session):
    config.save_detector(
        db_session,
        "quiet_day_wages",
        {
            "thresholds": {"min_gap": 0.6},
            "templates": {
                "prompt": "{day}s at {venue}: wages {day_share} of sales. Why?"
            },
        },
        "admin@test",
    )
    cfg = effective(db_session)
    # Tuesday runs 90% against 35% for the week: under a 60-point gap, quiet.
    assert det.quiet_day_wages(venue(**QUIET_TUESDAY, rules=cfg.rules)) == []
    config.save_detector(
        db_session, "quiet_day_wages", {"thresholds": {"min_gap": None}}, "admin@test"
    )
    f = det.quiet_day_wages(venue(**QUIET_TUESDAY, rules=effective(db_session).rules))[
        0
    ]
    assert f.prompt == "Tuesdays at La Zeppa: wages 90% of sales. Why?"
    assert f.data["facts"]["day_share"] == "90%"


def test_a_template_with_an_unknown_placeholder_is_refused():
    problems = rules.check_template("Why {venue} {weekday}?", {"venue": "", "day": ""})
    assert problems and "{weekday}" in problems[0]
    assert rules.check_template("{venue:>10}", {"venue": ""})
    assert rules.check_template("{venue.name}", {"venue": ""})


def test_a_bad_row_is_ignored_not_fatal(db_session):
    """Written straight to the table, past the checks: the default stands."""
    db_session.add(
        SuggestionConfig(
            key="detector:quiet_day_wages",
            value={
                "thresholds": {"min_gap": "lots"},
                "templates": {"prompt": "{nope}"},
            },
        )
    )
    db_session.add(SuggestionConfig(key="settings", value={"venue_boost": -4}))
    db_session.flush()
    cfg = effective(db_session)
    assert cfg.rules == {} and cfg.settings["venue_boost"] == 1.5
    assert det.quiet_day_wages(venue(**QUIET_TUESDAY, rules=cfg.rules))[
        0
    ].prompt.startswith("How can I bring Tuesday wages down at La Zeppa?")


def test_a_switched_off_detector_is_hidden_at_once_and_not_found_again(
    db_session, org, lz
):
    user = person(db_session, org, [lz])
    row = insight(db_session, org, lz)
    assert f"insight:{row.id}" in [s["id"] for s in run(db_session, user, lz)]
    config.save_detector(db_session, "quiet_day_wages", {"enabled": False}, "a@test")
    config.clear_cache()
    assert f"insight:{row.id}" not in [s["id"] for s in run(db_session, user, lz)]
    found = det.quiet_day_wages(venue(**QUIET_TUESDAY))
    assert runner.switched_on(found, effective(db_session)) == []


# ------------------------------------------------------------------ ranking and prompts
def test_slots_and_members_follow_the_settings(db_session, org, lz):
    user = person(db_session, org, [lz])
    insight(db_session, org, lz)
    insight(
        db_session,
        org,
        lz,
        detector="price_rise",
        subject="Oil",
        area="cogs",
        audience="orders:read",
    )
    assert sum(s["kind"] == "insight" for s in run(db_session, user, lz, limit=4)) == 2
    config.save_settings(db_session, {"insight_slots_desktop": 1}, "a@test")
    assert sum(s["kind"] == "insight" for s in run(db_session, user, lz, limit=4)) == 1
    # Wage insights reach Time & Attendance by default; not once it's taken off.
    assert any(
        s["kind"] == "insight" for s in run(db_session, user, lz, "time_attendance")
    )
    config.save_detector(
        db_session, "quiet_day_wages", {"members": ["reports"]}, "a@test"
    )
    assert not any(
        s["kind"] == "insight" for s in run(db_session, user, lz, "time_attendance")
    )


def test_prompts_are_edited_added_turned_off_and_deleted(db_session, org, lz):
    user = person(db_session, org, [lz])
    config.save_prompt(
        db_session,
        "reports.yesterday",
        {"text": "How was yesterday at {venue}?"},
        "a@test",
    )
    config.save_prompt(
        db_session,
        "reports.covers",
        {
            "member": "reports",
            "app": "sales-analysis",
            "text": "How many covers did {venue} do last week?",
            "tools": ["loadedhub.get_sales"],
            "permission": "reports:read",
            "weight": 5,
        },
        "a@test",
    )
    texts = [s["prompt"] for s in run(db_session, user, lz, "reports", limit=4)]
    assert "How many covers did La Zeppa do last week?" in texts
    assert "How was yesterday at La Zeppa?" in [
        p.text.replace("{venue}", "La Zeppa") for p in effective(db_session).prompts
    ]
    config.save_prompt(db_session, "reports.yesterday", {"enabled": False}, "a@test")
    config.delete_prompt(db_session, "reports.covers", "a@test")
    ids = [e.id for e in effective(db_session).prompts]
    assert "reports.yesterday" not in ids and "reports.covers" not in ids


def test_a_prompt_for_an_unknown_member_or_with_a_bad_placeholder_is_refused(
    db_session,
):
    import pytest

    with pytest.raises(config.Invalid) as e:
        config.save_prompt(
            db_session,
            "reports.oops",
            {"member": "nobody", "app": "loaded-reports", "text": "At {site}?"},
            "a@test",
        )
    assert any("nobody" in p for p in e.value.problems)
    assert any("{site}" in p for p in e.value.problems)


def test_reset_and_undo(db_session):
    config.save_settings(db_session, {"venue_boost": 3.0}, "a@test")
    config.reset(db_session, "settings", "a@test")
    assert effective(db_session).settings["venue_boost"] == 1.5
    config.undo(db_session, "settings", "a@test")
    assert effective(db_session).settings["venue_boost"] == 3.0


# ------------------------------------------------------------------ words
def test_new_wording_instructions_reword_every_live_insight(db_session):
    config.save_settings(db_session, {"wording_instructions": "Be brief."}, "a@test")
    cfg = effective(db_session)
    assert cfg.wording_version == f"{wording.VERSION}.1"

    class Row:
        data = {"wording": {"template": "t", "prompt": "p", "v": wording.VERSION}}

    assert wording.needs_wording(Row(), cfg.wording_version)


def test_the_insight_chat_guidance_is_a_template(db_session):
    from app.services.insights.context import guidance

    config.save_settings(
        db_session,
        {
            "insight_guidance": "## From an insight\n{headline} at {venue} ({insight_id}){notes}"
        },
        "a@test",
    )
    config.clear_cache()
    out = guidance(
        {"id": "i1", "headline": "Tuesdays", "venue": "La Zeppa"}, db_session
    )
    assert out == "\n\n## From an insight\nTuesdays at La Zeppa (i1)"


# ------------------------------------------------------------------ the admin page
def test_the_page_is_for_platform_admins(
    client, db_session, admin_headers, manager_headers
):
    assert (
        client.get("/api/admin/suggestions", headers=manager_headers).status_code == 403
    )
    page = client.get("/api/admin/suggestions", headers=admin_headers).json()
    assert {d["name"] for d in page["detectors"]} == set(rules.DETECTORS)
    assert page["prompts"] and page["settings"]
    bad = client.put(
        "/api/admin/suggestions/detectors/quiet_hours",
        json={"changes": {"thresholds": {"crew": 99}}},
        headers=admin_headers,
    )
    assert bad.status_code == 422 and bad.json()["detail"]["problems"]


def test_the_preview_says_why(client, db_session, org, lz, admin_headers):
    user = person(db_session, org, [lz], permissions=["orders:read"])
    insight(db_session, org, lz)  # needs reports:read
    out = client.post(
        "/api/admin/suggestions/preview",
        json={"user_id": user.id, "venue_id": lz.id},
        headers=admin_headers,
    ).json()
    assert out["venue"] == "La Zeppa"
    assert any(
        "reports:read" in o["reason"] for o in out["others"] if o["kind"] == "insight"
    )


def test_a_template_is_previewed_against_its_example(client, admin_headers):
    out = client.post(
        "/api/admin/suggestions/render",
        json={
            "detector": "quiet_hours",
            "template": "prompt",
            "text": "{day}s {span} at {venue}?",
        },
        headers=admin_headers,
    ).json()
    assert out["example"] == "Tuesdays noon to 6pm at Mr Murdochs?"
    bad = client.post(
        "/api/admin/suggestions/render",
        json={"detector": "quiet_hours", "template": "prompt", "text": "{when}?"},
        headers=admin_headers,
    ).json()
    assert bad["problems"]
