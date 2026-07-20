"""The LLM fallback in resolve_dates must not invent a timezone offset.

The bug this pins: the prompt carried today's UTC offset and instructed the
model to stamp it on every timestamp it produced. Asked in July (NZST, +12:00)
for a week in October (NZDT, +13:00), production returned

    2026-10-05T07:00:00+12:00

which is 08:00 local — an hour past the trading-day start, for the whole NZ
summer. Correct in winter by coincidence, silently wrong from late September.
Same shape as computing a civil day instead of a trading day: the number looks
fine and the window is wrong.

The fix inverts responsibility. The model returns local wall-clock times and
Norm attaches the offset with ZoneInfo, which derives it from the date being
resolved rather than from today.
"""

import datetime as dt
from unittest.mock import patch

from app.agents.internal_tools import get_handler

NZ_SUMMER = dt.timedelta(hours=13)  # NZDT, from Sun 27 Sep 2026
NZ_WINTER = dt.timedelta(hours=12)  # NZST


def _resolve(query, llm_periods, db=None):
    """Drive resolve_dates through its LLM fallback with a canned reply."""
    handler = get_handler("norm", "resolve_dates")
    with patch(
        "app.interpreter.llm_interpreter.call_llm",
        return_value=({"periods": llm_periods}, "llm-1"),
    ):
        return handler({"query": query, "timezone": "Pacific/Auckland"}, db, None)


class TestOffsetIsNotInherited:
    def test_a_naive_summer_time_gets_the_summer_offset(self):
        """The failing case, verbatim. A date inside NZDT must resolve to +13:00
        no matter when the question is asked."""
        out = _resolve(
            "the week beginning 5 October 2026",
            [{"label": "x", "start": "2026-10-05T07:00:00", "end": "2026-10-12T06:59:59"}],
        )
        start = dt.datetime.fromisoformat(out["data"]["periods"][0]["start"])
        assert start.utcoffset() == NZ_SUMMER
        assert start.strftime("%H:%M") == "07:00"

    def test_a_naive_winter_time_gets_the_winter_offset(self):
        out = _resolve(
            "the week beginning 6 July 2026",
            [{"label": "x", "start": "2026-07-06T07:00:00", "end": "2026-07-13T06:59:59"}],
        )
        start = dt.datetime.fromisoformat(out["data"]["periods"][0]["start"])
        assert start.utcoffset() == NZ_WINTER

    def test_each_end_is_localised_independently(self):
        """A window spanning the transition has different offsets at its ends.
        Applying one offset to both is the bug in miniature."""
        out = _resolve(
            "the week beginning 21 September 2026",
            [{"label": "x", "start": "2026-09-21T07:00:00", "end": "2026-09-28T06:59:59"}],
        )
        period = out["data"]["periods"][0]
        assert dt.datetime.fromisoformat(period["start"]).utcoffset() == NZ_WINTER
        assert dt.datetime.fromisoformat(period["end"]).utcoffset() == NZ_SUMMER

    def test_an_offset_the_model_supplies_is_honoured_not_overwritten(self):
        """Only naive values are localised. Second-guessing an explicit offset
        would break a caller who deliberately asked in another zone."""
        out = _resolve(
            "something unusual",
            [{"label": "x", "start": "2026-10-05T07:00:00+00:00", "end": "2026-10-05T08:00:00+00:00"}],
        )
        start = dt.datetime.fromisoformat(out["data"]["periods"][0]["start"])
        assert start.utcoffset() == dt.timedelta(0)


class TestPromptCarriesNoOffset:
    def test_the_system_prompt_no_longer_dictates_an_offset(self):
        """Belt and braces: if a fixed offset is ever reintroduced into the
        prompt the model will start stamping it again."""
        captured = {}

        def fake_call_llm(system_prompt, **kwargs):
            captured["system"] = system_prompt
            return ({"periods": [{"label": "x", "start": "2026-10-05T07:00:00",
                                  "end": "2026-10-12T06:59:59"}]}, "llm-1")

        handler = get_handler("norm", "resolve_dates")
        with patch("app.interpreter.llm_interpreter.call_llm", side_effect=fake_call_llm):
            handler({"query": "an unusual phrase", "timezone": "Pacific/Auckland"}, None, None)

        assert "+12:00" not in captured["system"]
        assert "+13:00" not in captured["system"]
        assert "NO timezone offset" in captured["system"]


class TestDeterministicPhrasesSkipTheLlm:
    def test_a_navigator_phrase_never_reaches_the_model(self):
        """week-beginning is handled by business_calendar, so the navigator
        cannot be affected by this class of bug at all."""
        handler = get_handler("norm", "resolve_dates")
        with patch("app.interpreter.llm_interpreter.call_llm") as llm:
            out = handler(
                {"query": "week beginning 5 October 2026", "timezone": "Pacific/Auckland"},
                None,
                None,
            )
        llm.assert_not_called()
        start = dt.datetime.fromisoformat(out["data"]["periods"][0]["start"])
        assert start.utcoffset() == NZ_SUMMER
