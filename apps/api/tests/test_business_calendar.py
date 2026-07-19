"""The business calendar — the one definition of a trading day.

The case that prompted this module: sales for "yesterday" were computed
midnight-to-midnight, so a late-night venue's post-midnight trade fell outside
the window entirely and the venue read $0 for a Saturday. That looked like a
POS outage rather than a bad window, which is the expensive kind of wrong.
"""

import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.services import business_calendar as bc

NZ = ZoneInfo("Pacific/Auckland")


def venue(day_start="07:00", timezone="Pacific/Auckland"):
    return SimpleNamespace(day_start_time=day_start, timezone=timezone)


class TestVenueSettings:
    def test_uses_the_venues_own_day_start(self):
        assert bc.day_start_for(venue("05:00")) == "05:00"

    def test_falls_back_to_the_configured_default_not_another_venue(self):
        """The reports path used to fall back to 'the first venue in the table
        with a day_start_time', silently applying one venue's boundary to
        another. A missing value must resolve the same way everywhere."""
        assert bc.day_start_for(venue(day_start=None)) == settings.BUSINESS_DAY_START
        assert bc.day_start_for(None) == settings.BUSINESS_DAY_START

    def test_malformed_day_start_does_not_explode(self):
        # A typo in venue config should not 500 a dashboard.
        assert bc.day_start_for(venue("not-a-time")) == "00:00"
        assert bc.day_start_for(venue("99:99")) == "00:00"

    def test_venue_timezone_wins_over_default(self):
        assert (
            str(bc.timezone_for(venue(timezone="Australia/Sydney")))
            == "Australia/Sydney"
        )

    def test_unknown_timezone_falls_back(self):
        assert bc.timezone_for(venue(timezone="Mars/Olympus")) is not None


class TestTradingDay:
    def test_day_runs_from_day_start_to_one_second_before_next(self):
        now = dt.datetime(2026, 7, 18, 20, 0, tzinfo=NZ)  # Saturday evening
        w = bc.trading_day(venue("07:00"), now)
        assert (w.start.day, w.start.hour) == (18, 7)
        assert (w.end.day, w.end.hour, w.end.minute) == (19, 6, 59)

    def test_after_midnight_still_belongs_to_the_previous_day(self):
        """THE bug. At 02:00 Sunday, a 7am venue is still trading Saturday."""
        two_am_sunday = dt.datetime(2026, 7, 19, 2, 0, tzinfo=NZ)
        w = bc.trading_day(venue("07:00"), two_am_sunday)
        assert w.start.day == 18, "post-midnight trade must stay on Saturday"

    def test_yesterday_captures_the_late_session(self):
        """Asked on Sunday morning, 'yesterday' must include Saturday's
        post-midnight trade — the takings that read as $0 before."""
        sunday_morning = dt.datetime(2026, 7, 19, 10, 0, tzinfo=NZ)
        w = bc.trading_day(venue("07:00"), sunday_morning, -1)
        assert w.start == dt.datetime(2026, 7, 18, 7, 0, tzinfo=NZ)
        assert w.end.day == 19 and w.end.hour == 6
        # 1am Sunday trade belongs to Saturday's window
        assert w.start < dt.datetime(2026, 7, 19, 1, 0, tzinfo=NZ) < w.end

    def test_midnight_venue_behaves_like_a_civil_day(self):
        now = dt.datetime(2026, 7, 18, 20, 0, tzinfo=NZ)
        w = bc.trading_day(venue("00:00"), now)
        assert (w.start.hour, w.start.day) == (0, 18)
        assert w.end.day == 18 and w.end.hour == 23

    def test_venues_can_differ(self):
        now = dt.datetime(2026, 7, 19, 6, 0, tzinfo=NZ)  # 6am Sunday
        late = bc.trading_day(venue("07:00"), now)  # still Saturday
        early = bc.trading_day(venue("05:00"), now)  # already Sunday
        assert late.start.day == 18
        assert early.start.day == 19


class TestTradingWeek:
    def test_week_starts_monday_at_day_start(self):
        wed = dt.datetime(2026, 7, 22, 12, 0, tzinfo=NZ)
        w = bc.trading_week(venue("07:00"), wed)
        assert w.start.weekday() == 0
        assert (w.start.day, w.start.hour) == (20, 7)
        assert (w.end.day, w.end.hour, w.end.minute) == (27, 6, 59)

    def test_last_week_is_the_completed_one(self):
        wed = dt.datetime(2026, 7, 22, 12, 0, tzinfo=NZ)
        w = bc.trading_week(venue("07:00"), wed, -1)
        assert w.start.day == 13 and w.end.day == 20

    def test_monday_before_day_start_is_still_last_week(self):
        """03:00 Monday — the new week hasn't started for a 7am venue."""
        early_monday = dt.datetime(2026, 7, 20, 3, 0, tzinfo=NZ)
        w = bc.trading_week(venue("07:00"), early_monday)
        assert w.start.day == 13, "week must not roll over before the day does"


class TestPhrases:
    @pytest.mark.parametrize(
        "phrase,kind",
        [
            ("today", "trading_day"),
            ("yesterday", "trading_day"),
            ("this week", "trading_week"),
            ("last week", "trading_week"),
            ("this month", "month"),
            ("last month", "month"),
        ],
    )
    def test_common_phrases_resolve_deterministically(self, phrase, kind):
        w = bc.resolve_phrase(
            venue(), phrase, dt.datetime(2026, 7, 22, 12, 0, tzinfo=NZ)
        )
        assert w is not None and w.kind == kind

    def test_case_and_whitespace_tolerant(self):
        assert bc.resolve_phrase(venue(), "  Last Week  ") is not None

    def test_fuzzy_phrase_returns_none_for_the_llm(self):
        """None means 'not deterministic' — a handoff, not an error."""
        assert bc.resolve_phrase(venue(), "the week before the long weekend") is None


class TestCustomWindowAndDescription:
    def test_custom_range_is_honoured_verbatim_never_snapped(self):
        """Someone reconciling a bank statement legitimately wants civil days.
        Overriding them would just be a different way of being wrong."""
        start = dt.datetime(2026, 7, 18, 0, 0, tzinfo=NZ)
        end = dt.datetime(2026, 7, 19, 0, 0, tzinfo=NZ)
        w = bc.custom_window(venue("07:00"), start, end)
        assert w.start == start and w.end == end

    def test_misaligned_custom_window_says_so(self):
        """The echo is the safety net: a window that isn't a trading day must
        announce it, so a wrong window is visible rather than silent."""
        w = bc.custom_window(
            venue("07:00"),
            dt.datetime(2026, 7, 18, 0, 0, tzinfo=NZ),
            dt.datetime(2026, 7, 19, 0, 0, tzinfo=NZ),
        )
        assert not w.is_trading_aligned
        assert "Not a trading day" in w.describe()

    def test_aligned_custom_window_is_not_flagged(self):
        w = bc.custom_window(
            venue("07:00"),
            dt.datetime(2026, 7, 18, 7, 0, tzinfo=NZ),
            dt.datetime(2026, 7, 19, 6, 59, tzinfo=NZ),
        )
        assert w.is_trading_aligned
        assert "Not a trading day" not in w.describe()

    def test_description_names_the_window(self):
        w = bc.trading_day(venue("07:00"), dt.datetime(2026, 7, 18, 20, 0, tzinfo=NZ))
        text = w.describe()
        assert "07:00" in text and "Pacific/Auckland" in text

    def test_as_dict_is_serialisable(self):
        d = bc.trading_day(venue()).as_dict()
        assert set(d) >= {"start", "end", "kind", "trading_aligned", "description"}


class TestHumanLabels:
    """Prose that quotes the boundary is generated from the boundary.

    The MCP instructions used to hand-write "7:00am", so changing the
    configured start left Norm computing one boundary and telling Claude about
    another — with a test pinning the same literal, nothing caught it.
    """

    @pytest.mark.parametrize(
        "hhmm,expected",
        [
            ("07:00", "7:00am"),
            ("05:30", "5:30am"),
            ("00:00", "12:00am"),
            ("12:00", "12:00pm"),
            ("13:15", "1:15pm"),
            ("23:59", "11:59pm"),
        ],
    )
    def test_humanize(self, hhmm, expected):
        assert bc.humanize_hhmm(hhmm) == expected

    @pytest.mark.parametrize(
        "hhmm,expected",
        [("07:00", "6:59am"), ("05:30", "5:29am"), ("00:00", "11:59pm")],
    )
    def test_day_end_label_is_one_minute_before(self, hhmm, expected):
        assert bc.day_end_label(hhmm) == expected


class TestPerVenueResolution:
    """The reason this matters: today every venue is 07:00, so a venue-blind
    resolver looks correct. It stops being correct the day one differs."""

    def test_venues_with_different_starts_get_different_yesterdays(self):
        sunday_3am = dt.datetime(2026, 7, 19, 3, 0, tzinfo=NZ)
        late = bc.resolve_phrase(venue("07:00"), "yesterday", sunday_3am)
        early = bc.resolve_phrase(venue("02:00"), "yesterday", sunday_3am)
        # At 3am Sunday the 7am venue is still trading Saturday, so "yesterday"
        # is Friday; the 2am venue has already rolled over, so it is Saturday.
        assert late.start.day == 17
        assert early.start.day == 18

    def test_venue_timezone_changes_the_window(self):
        moment = dt.datetime(2026, 7, 18, 20, 0, tzinfo=NZ)
        nz = bc.trading_day(venue(timezone="Pacific/Auckland"), moment)
        syd = bc.trading_day(venue(timezone="Australia/Sydney"), moment)
        assert nz.timezone != syd.timezone
        assert nz.start.utcoffset() != syd.start.utcoffset()


class TestResolveDatesExplicitRange:
    """resolve_dates is the single entry point for both paths, so a consolidator
    can hand it either a phrase or a range and get the same analysis back."""

    def _run(self, params):
        from unittest.mock import MagicMock

        from app.agents.internal_tools import get_handler

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        return get_handler("norm", "resolve_dates")(params, db, None)

    def test_deviating_range_is_reported_as_not_a_trading_day(self):
        r = self._run(
            {
                "start": "2026-07-18T00:00:00+12:00",
                "end": "2026-07-19T00:00:00+12:00",
            }
        )
        window = r["data"]["window"]
        assert window["trading_aligned"] is False
        assert "Not a trading day" in window["description"]

    def test_deviating_range_is_returned_verbatim_not_snapped(self):
        r = self._run(
            {
                "start": "2026-07-18T00:00:00+12:00",
                "end": "2026-07-19T00:00:00+12:00",
            }
        )
        period = r["data"]["periods"][0]
        assert period["start"].startswith("2026-07-18T00:00")
        assert period["end"].startswith("2026-07-19T00:00")

    def test_aligned_range_is_not_flagged(self):
        r = self._run(
            {
                "start": "2026-07-18T07:00:00+12:00",
                "end": "2026-07-19T06:59:00+12:00",
            }
        )
        assert r["data"]["window"]["trading_aligned"] is True

    def test_phrase_path_still_works(self):
        r = self._run({"query": "yesterday"})
        assert r["success"] is True
        assert r["data"]["window"]["kind"] == "trading_day"

    def test_neither_phrase_nor_range_is_an_error(self):
        assert self._run({})["success"] is False

    def test_malformed_range_is_rejected_not_guessed(self):
        r = self._run({"start": "last tuesday", "end": "whenever"})
        assert r["success"] is False
        assert "ISO 8601" in r["error"]
