"""Insight prompts reworded in the organisation's words (services/insights/wording.py).

A small model rewords what a detector wrote; a check refuses any number the
template doesn't have and keeps the venue, or the template stands. The call is Norm's cost — recorded with no
organisation, never counted against a plan. Nothing here calls a model: the
call is faked.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config import settings
from app.db.models import Insight, LlmCall, Memory, TokenUsage
from app.services.insights import detectors as det
from app.services.insights import runner, wording
from tests.conftest import _make_organization, _make_venue

NOW = datetime(2026, 10, 18, 19, 0, tzinfo=timezone.utc)
TEMPLATE = (
    "Is the First Table discount at Dunedin Social Club worth it? It cost $7,553 "
    "over the last 4 weeks (14% of sales went on discounts in all) — what do those "
    "bills look like, and when is it used?"
)
WORDED = (
    "Is our FT promo at Dunedin Social Club paying off? $7,553 off over the last 4 "
    "weeks, 14% of sales in discounts — show me those bills and when they happen."
)


@pytest.fixture()
def place(db_session):
    org = _make_organization(db_session, name="Cook Brothers")
    venue = _make_venue(db_session, name="Dunedin Social Club", organization_id=org.id)
    db_session.flush()
    return org, venue


def _insight(db, org, venue, prompt=TEMPLATE, subject="First Table"):
    row = Insight(
        organization_id=org.id,
        venue_id=venue.id,
        detector="discount",
        subject=subject,
        subject_key=f"{venue.id}:discount:{subject}",
        area="sales",
        audience="reports:read",
        headline="First Table discounts",
        prompt=prompt,
        value=1888.0,
        data={},
        as_of="2026-10-18",
        expires_at=NOW + timedelta(days=7),
    )
    db.add(row)
    db.flush()
    return row


# ------------------------------------------------------------------ the check
def test_a_rewording_that_keeps_every_number_and_the_venue_passes():
    assert wording.keeps_the_facts(TEMPLATE, WORDED, "Dunedin Social Club")


def test_a_secondary_figure_may_be_left_out_but_not_every_figure():
    shorter = "Is our FT promo at Dunedin Social Club worth the $7,553 it cost us?"
    assert wording.keeps_the_facts(TEMPLATE, shorter, "Dunedin Social Club")
    vague = "Is our FT promo at Dunedin Social Club worth what it costs?"
    assert not wording.keeps_the_facts(TEMPLATE, vague, "Dunedin Social Club")


@pytest.mark.parametrize(
    "bad",
    [
        WORDED.replace("$7,553", "$7.5k"),  # a number rounded away
        WORDED.replace("14%", "15%"),  # a number changed
        WORDED + " (about 22% of sales)",  # a number made up
        WORDED.replace("Dunedin Social Club", "DSC"),  # the venue lost
        WORDED.replace(" — ", "\n"),  # two lines
        WORDED + " " + "x" * 200,  # longer than Home can show
        "",
    ],
)
def test_a_rewording_that_loses_a_fact_keeps_the_template(bad):
    assert not wording.keeps_the_facts(TEMPLATE, bad, "Dunedin Social Club")


# ------------------------------------------------------------------ word()
def test_changed_insights_are_reworded_in_one_call(db_session, place, monkeypatch):
    org, venue = place
    good = _insight(db_session, org, venue)
    bad = _insight(db_session, org, venue, subject="Staff", prompt=TEMPLATE)
    asked = []

    def fake_call(db, user_prompt):
        asked.append(user_prompt)
        return {
            "prompts": [
                {"id": "1", "prompt": WORDED},
                {"id": "2", "prompt": "Are discounts too high?"},
            ]
        }

    monkeypatch.setattr(settings, "INSIGHT_WORDING_ENABLED", True)
    monkeypatch.setattr(wording, "_call", fake_call)
    out = wording.word(db_session, venue, [good, bad])
    assert out == {"asked": 2, "worded": 1} and len(asked) == 1
    assert good.prompt == WORDED
    assert good.data["wording"] == {
        "template": TEMPLATE,
        "prompt": WORDED,
        "v": wording.VERSION,
    }
    # The failed one keeps its template and is not asked again for it.
    assert bad.prompt == TEMPLATE and bad.data["wording"]["prompt"] is None
    assert not wording.needs_wording(bad)


def test_an_older_rewording_is_redone_from_the_template(db_session, place, monkeypatch):
    """New instructions (VERSION) reword what was worded before — from the
    detector's words, and a redo that fails the check shows the template,
    never the older rewording (Dunedin, 17 Oct: "up 37% on last year" had
    become "$143,046 vs $104,765 last 4 weeks")."""
    org, venue = place
    old = "Is FT worth it at Dunedin Social Club? $7,553 vs 14% last week"
    row = _insight(db_session, org, venue, prompt=old)
    row.data = {"wording": {"template": TEMPLATE, "prompt": old}}
    assert wording.needs_wording(row)
    asked = []

    def fake_call(db, user_prompt):
        asked.append(user_prompt)
        return {"prompts": [{"id": "1", "prompt": "Is FT worth it?"}]}

    monkeypatch.setattr(settings, "INSIGHT_WORDING_ENABLED", True)
    monkeypatch.setattr(wording, "_call", fake_call)
    wording.word(db_session, venue, [row])
    assert TEMPLATE in asked[0] and old not in asked[0]
    assert row.prompt == TEMPLATE
    assert row.data["wording"] == {
        "template": TEMPLATE,
        "prompt": None,
        "v": wording.VERSION,
    }


def test_switched_off_nothing_is_asked(db_session, place, monkeypatch):
    org, venue = place
    row = _insight(db_session, org, venue)
    monkeypatch.setattr(settings, "INSIGHT_WORDING_ENABLED", False)
    monkeypatch.setattr(wording, "_call", lambda *a: pytest.fail("called"))
    assert wording.word(db_session, venue, [row]) == {"asked": 0}
    assert row.prompt == TEMPLATE


def test_a_failed_call_leaves_the_template_to_try_again(db_session, place, monkeypatch):
    org, venue = place
    row = _insight(db_session, org, venue)

    def boom(db, user_prompt):
        raise RuntimeError("overloaded")

    monkeypatch.setattr(settings, "INSIGHT_WORDING_ENABLED", True)
    monkeypatch.setattr(wording, "_call", boom)
    out = wording.word(db_session, venue, [row])
    assert "overloaded" in out["error"]
    assert row.prompt == TEMPLATE and wording.needs_wording(row)


def test_the_organisation_s_own_words_go_with_the_ask(db_session, place):
    org, venue = place
    other = _make_organization(db_session, name="Someone Else")

    def memory(org_id, title, **kw):
        db_session.add(
            Memory(
                scope="org",
                organization_id=org_id,
                type=kw.pop("type", "vocabulary"),
                title=title,
                body=kw.pop("body", "what the team calls it"),
                **kw,
            )
        )

    memory(org.id, "FT means the First Table discount")
    memory(org.id, "Proposed only", status="candidate")
    memory(org.id, "A preference", type="preference")
    memory(other.id, "Another organisation's word")
    db_session.flush()
    assert wording.vocabulary(db_session, org.id, venue.id) == [
        "FT means the First Table discount: what the team calls it"
    ]


# ------------------------------------------------------------------ Norm pays
def test_the_call_is_recorded_with_no_organisation(db_session, place, monkeypatch):
    """Norm's own cost: an llm_calls row with no organisation, and nothing
    added to any organisation's usage."""
    import anthropic

    reply = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"prompts": []}')],
        usage=SimpleNamespace(
            input_tokens=900,
            output_tokens=120,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )

    class FakeClient:
        def __init__(self, **kw):
            self.messages = SimpleNamespace(create=lambda **kw: reply)

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr("app.services.secrets.get_api_key", lambda *a, **k: "test-key")
    before = db_session.query(TokenUsage).count()
    assert wording._call(db_session, "{}") == {"prompts": []}
    call = db_session.query(LlmCall).filter_by(call_type=wording.CALL_TYPE).one()
    assert call.organization_id is None
    assert call.input_tokens == 900 and float(call.cost_usd) > 0
    assert db_session.query(TokenUsage).count() == before


# ------------------------------------------------------------------ the run
def test_an_unchanged_template_keeps_its_wording_and_a_changed_one_loses_it(
    db_session, place
):
    org, venue = place
    finding = det.Finding(
        "discount", "First Table", "sales", "reports:read", "First Table", TEMPLATE
    )
    runner.save(db_session, org.id, venue.id, NOW.date(), [finding], NOW)
    row = db_session.query(Insight).filter_by(venue_id=venue.id).one()
    row.data = {
        "wording": {"template": TEMPLATE, "prompt": WORDED, "v": wording.VERSION}
    }
    row.prompt = WORDED
    db_session.flush()

    runner.save(db_session, org.id, venue.id, NOW.date(), [finding], NOW)
    assert row.prompt == WORDED and not wording.needs_wording(row)

    moved = det.Finding(
        "discount",
        "First Table",
        "sales",
        "reports:read",
        "First Table",
        TEMPLATE.replace("$7,553", "$8,104"),
    )
    runner.save(db_session, org.id, venue.id, NOW.date(), [moved], NOW)
    assert "$8,104" in row.prompt and wording.needs_wording(row)


def test_the_run_rewords_what_it_wrote(db_session, place, monkeypatch):
    org, venue = place
    _insight(db_session, org, venue)
    monkeypatch.setattr(settings, "INSIGHT_WORDING_ENABLED", True)
    monkeypatch.setattr(
        wording,
        "_call",
        lambda db, p: {"prompts": [{"id": "1", "prompt": WORDED}]},
    )
    out = runner.word_pending(db_session, {org.id}, NOW)
    assert out == {"Dunedin Social Club": {"asked": 1, "worded": 1}}
    row = db_session.query(Insight).filter_by(venue_id=venue.id).one()
    assert row.prompt == WORDED
