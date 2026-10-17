"""Insight detectors on small synthetic venues.

The rules and thresholds come from nine real venues (docs/plans/insights-
research.md). These tests pin the guards that research showed matter: judging
a venue against its own normal, refusing to speak on stale or missing data,
counting labour weeks by date (a cafe open five days has five rows a week),
and comparing supplier prices like with like.
"""

from datetime import date, timedelta

from app.services.insights import detectors as det

AS_OF = date(2026, 10, 19)  # a Monday: last week is 12–18 Oct
LAST_WEEK = date(2026, 10, 12)


def _label(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b}"


def sales(
    days=70, base=1000.0, by_weekday=None, budget=None, last_year=None, end=AS_OF
):
    rows = []
    for i in range(days, 0, -1):
        d = end - timedelta(days=i)
        amount = (by_weekday or {}).get(d.weekday(), base)
        row = {"date": d.isoformat(), "day": f"{d:%A}", "actual": amount}
        if budget is not None:
            row["budget"] = budget(d) if callable(budget) else budget
        if last_year is not None:
            row["last_year"] = last_year
        rows.append(row)
    return rows


def labour(
    days=70,
    cost=300.0,
    by_weekday=None,
    hours=10.0,
    open_days=range(7),
    roster_cost=None,
    end=AS_OF,
):
    rows = []
    for i in range(days, 0, -1):
        d = end - timedelta(days=i)
        if d.weekday() not in open_days:
            continue
        c = (by_weekday or {}).get(d.weekday(), cost)
        rows.append(
            {
                "date": _label(d),
                "actual_cost": c,
                "actual_hours": hours,
                "rostered_cost": roster_cost if roster_cost is not None else c,
                "rostered_hours": hours,
            }
        )
    return rows


def venue(**kw):
    return det.VenueData(venue_id="v1", venue_name="La Zeppa", as_of=AS_OF, **kw)


# ------------------------------------------------------------------ calendar
def test_labour_rows_carry_no_year_and_one_is_inferred_across_new_year():
    assert det.labour_date("Mon 22 Jun", date(2026, 10, 19)) == date(2026, 6, 22)
    assert det.labour_date("Wed 31 Dec", date(2027, 1, 5)) == date(2026, 12, 31)


def test_a_five_day_cafe_still_has_complete_labour_weeks():
    v = venue(sales_daily=sales(), labour_daily=labour(open_days=range(1, 6)))
    weeks = det.complete_labour_weeks(v)
    assert weeks[-1] == LAST_WEEK and len(weeks) >= 8


# ------------------------------------------------------------------ wages
def test_a_quiet_tuesday_staffed_like_a_busy_day_is_found():
    v = venue(
        sales_daily=sales(by_weekday={1: 500.0}),
        labour_daily=labour(by_weekday={1: 450.0}),
    )
    found = det.quiet_day_wages(v)
    assert [f.subject for f in found] == ["Tuesday"]
    f = found[0]
    assert "Tuesdays at La Zeppa run wages at 90% of sales" in f.headline
    assert f.audience == "reports:read" and f.area == "wages"
    assert f.value > 150


def test_wage_rules_stay_quiet_when_timesheets_have_stopped():
    """Weeks from before the data stopped describe a venue that may have
    changed — data_health speaks instead (Little West, Oct 2026 research)."""
    stale = labour(by_weekday={1: 450.0}, end=AS_OF - timedelta(days=21))
    v = venue(sales_daily=sales(by_weekday={1: 500.0}), labour_daily=stale)
    assert det.quiet_day_wages(v) == []


# ------------------------------------------------------------------ hour by hour
FOUR_WEEKS = [date(2026, 9, 21) + timedelta(days=i) for i in range(28)]
BUSY = {h: 700.0 for h in range(12, 22)}


def floor_staff(d, people=4, start="12:00 PM", end="10:00 PM", hours=10.0):
    """get_labour detail rows: `people` on from start to end at $35 an hour."""
    return [
        {
            "date": _label(d),
            "name": f"Staff {i}",
            "start": start,
            "end": end,
            "hours": hours,
            "cost": hours * 35.0,
        }
        for i in range(people)
    ]


def by_hour(trade=lambda d: BUSY, **staff):
    """Four weeks hour by hour; `trade(d)` is {clock hour: sales} for a day."""
    shifts, rows = [], []
    for d in FOUR_WEEKS:
        shifts += floor_staff(d, **staff)
        for h, amount in trade(d).items():
            rows.append(
                {"date": d.isoformat(), "time": f"{h:02d}:00", "actual": amount}
            )
    return venue(shifts=shifts, sales_hourly=rows)


def quiet_tuesday_afternoons(d):
    if d.weekday() != 1:
        return BUSY
    return {**BUSY, **{h: 80.0 for h in range(14, 18)}}


def test_a_quiet_stretch_inside_a_busy_day_is_found():
    found = det.quiet_hours(by_hour(quiet_tuesday_afternoons))
    assert [f.subject for f in found] == ["Tuesday"]
    f = found[0]
    assert (f.data["start"], f.data["end"]) == (14, 18)
    assert (
        f.headline
        == "Tuesdays 2pm to 6pm at La Zeppa: about 4 on for $80 an hour of sales"
    )
    assert f.prompt.startswith(
        "Do we need 4 people on at La Zeppa on Tuesdays from 2pm to 6pm? "
        "Over the last 4 weeks those hours averaged $80 an hour in sales, "
        "so wages ran at 175% of sales then"
    )
    # Each quiet hour could lose two of its four: 2 × $35 × 4 hours.
    assert round(f.value) == 280 and f.area == "wages"


def test_prep_before_the_first_sale_and_the_close_after_are_not_judged():
    """Staff on from 9am to 1am for trade from noon to 10pm: the hours either
    side are work sales can't judge."""
    v = by_hour(start="9:00 AM", end="1:00 AM", hours=16.0)
    assert det.quiet_hours(v) == []


def test_a_stretch_quiet_only_half_the_time_is_not_a_pattern():
    tuesdays = [d for d in FOUR_WEEKS if d.weekday() == 1]

    def trade(d):
        if d not in tuesdays:
            return BUSY
        quiet = 0.0 if d in tuesdays[:2] else 450.0
        return {**BUSY, **{h: quiet for h in range(14, 18)}}

    assert det.quiet_hours(by_hour(trade)) == []


def test_a_weekday_quiet_all_day_is_left_to_quiet_day_wages():
    v = by_hour(quiet_tuesday_afternoons)
    v.sales_daily = sales(by_weekday={1: 500.0})
    v.labour_daily = labour(by_weekday={1: 450.0})
    assert [f.subject for f in det.quiet_day_wages(v)] == ["Tuesday"]
    assert det.quiet_hours(v) == []


def test_no_pay_rates_says_nothing_hour_by_hour():
    v = by_hour(quiet_tuesday_afternoons)
    for s in v.shifts:
        s["cost"] = 0.0
    assert det.quiet_hours(v) == []


def test_a_late_shift_counts_to_its_trading_day_without_its_unpaid_break():
    d = date(2026, 10, 16)  # a Friday
    v = venue(
        shifts=[
            {
                "date": _label(d),
                "start": "6:00 PM",
                "end": "2:00 AM",
                "hours": 7.5,
                "cost": 262.5,
                "breaks": [
                    {
                        "start": "9:00 PM",
                        "end": "9:30 PM",
                        "duration_mins": 30,
                        "paid": False,
                    }
                ],
            }
        ]
    )
    staff, _ = det.hourly(v)
    assert staff[(d, 21)] == [0.5, 17.5]
    assert staff[(d, 1)] == [1.0, 35.0]  # 1am Saturday is still Friday's trade
    assert sum(h for h, _ in staff.values()) == 7.5


def test_paid_more_than_rostered_last_week():
    v = venue(sales_daily=sales(), labour_daily=labour(cost=400.0, roster_cost=300.0))
    found = det.over_roster(v)
    assert found and found[0].value == 700.0  # 7 days × $100
    assert found[0].audience == "roster:read"


def test_staff_over_roster_names_people_only_for_roster_managers():
    v = venue(
        labour_staff=[
            {
                "name": "Charlie",
                "rostered_hours": 100,
                "actual_hours": 140,
                "rostered_cost": 3000,
                "actual_cost": 4200,
            }
        ]
    )
    f = det.staff_over_roster(v)[0]
    assert "Charlie" in f.prompt and f.audience == "roster:write"


def test_wage_rate_up_against_the_venue_s_usual():
    rows = labour(cost=300.0)
    for r in rows:  # last week's wages run hot
        d = det.labour_date(r["date"], AS_OF)
        if d >= LAST_WEEK:
            r["actual_cost"] = 400.0
    v = venue(sales_daily=sales(), labour_daily=rows)
    f = det.wage_rate_up(v)[0]
    assert "40% of sales last week (usually 30%)" in f.headline


def test_next_week_s_roster_against_the_sales_a_normal_week_brings():
    thursday = date(2026, 10, 22)
    v = det.VenueData(
        venue_id="v1",
        venue_name="La Zeppa",
        as_of=thursday,
        sales_daily=sales(end=thursday),
        labour_daily=labour(end=thursday),
        roster_next=[{"rostered_cost": 3000.0, "rostered_hours": 80}],
    )
    f = det.roster_ahead(v)[0]
    assert "43% of usual sales (you run at 30%)" in f.headline  # 3000 / 7000
    assert f.subject == "2026-10-26"


def test_a_roster_with_no_pay_rates_is_not_a_cheap_roster():
    v = det.VenueData(
        venue_id="v1",
        venue_name="X",
        as_of=date(2026, 10, 22),
        sales_daily=sales(end=date(2026, 10, 22)),
        labour_daily=labour(end=date(2026, 10, 22)),
        roster_next=[{"rostered_cost": 0.0, "rostered_hours": 114}],
    )
    assert det.roster_ahead(v) == []


# ------------------------------------------------------------------ data health
def test_hours_at_no_cost_means_missing_pay_rates():
    v = venue(sales_daily=sales(), labour_daily=labour(cost=0.0, roster_cost=0.0))
    assert [f.detector for f in det.data_health(v)] == ["data_pay_rates"]


def test_actual_always_equal_to_roster_means_nobody_clocks_in():
    v = venue(sales_daily=sales(), labour_daily=labour(open_days=range(1, 6)))
    assert "data_no_clock_ins" in [f.detector for f in det.data_health(v)]


def test_clock_ins_that_differ_from_roster_are_healthy():
    v = venue(sales_daily=sales(), labour_daily=labour(cost=310.0, roster_cost=300.0))
    assert det.data_health(v) == []


def test_no_hours_lately_is_said_once():
    v = venue(sales_daily=sales(), labour_daily=labour(end=AS_OF - timedelta(days=20)))
    assert [f.detector for f in det.data_health(v)] == ["data_no_labour"]


def test_no_budgets_is_read_from_the_tax_inclusive_series():
    v = venue(sales_daily=sales(), sales_daily_incl=sales(), labour_daily=None)
    assert [f.detector for f in det.data_health(v)] == ["data_budgets"]


# ------------------------------------------------------------------ sales
def test_an_unusual_day_is_judged_against_the_same_weekday():
    rows = sales(base=4000.0)
    rows[-1]["actual"] = 6000.0  # yesterday, a Sunday, +50%
    f = det.unusual_day(venue(sales_daily=rows))[0]
    assert f.headline == "Sunday at La Zeppa was up 50% on a usual Sunday"
    assert f.lasts_days == 1


def test_a_small_wobble_is_not_unusual():
    rows = sales(base=1000.0)
    rows[-1]["actual"] = 1300.0  # +30% but only $300
    assert det.unusual_day(venue(sales_daily=rows)) == []


def test_budgets_beaten_week_after_week_look_out_of_date():
    v = venue(sales_daily_incl=sales(base=1200.0, budget=1000.0))
    f = det.budgets_out_of_date(v)[0]
    assert "beaten its budget by ~20% in 8 of 8 weeks" in f.headline


def test_budgets_close_to_trading_are_fine():
    v = venue(sales_daily_incl=sales(base=1050.0, budget=1000.0))
    assert det.budgets_out_of_date(v) == []


def test_the_biggest_discount_when_discounts_are_a_real_share_of_sales():
    v = venue(
        sales_daily=sales(base=1000.0),
        discounts_recent=[
            {"discount": "First Table", "discounts_amount": 3000.0},
            {"discount": "Staff", "discounts_amount": 200.0},
        ],
    )
    found = det.discounts(v)
    assert [f.subject for f in found] == ["First Table"]
    assert found[0].headline == "First Table discounts cost La Zeppa $3,000 in 4 weeks"


def test_a_discount_named_discount_reads_naturally():
    v = venue(
        sales_daily=sales(base=1000.0),
        discounts_recent=[{"discount": "Discount", "discounts_amount": 3000.0}],
    )
    assert det.discounts(v)[0].headline.startswith("Discounts cost La Zeppa")


def test_trend_against_last_year_when_last_year_is_complete():
    v = venue(sales_daily=sales(base=1200.0, last_year=1000.0))
    assert "up 20% on the same 4 weeks last year" in det.sales_trend(v)[0].headline


# ------------------------------------------------------------------ cost of goods
def _line(d, cost, unit="Kilo", sup="Bidfood", qty=10.0, item="CHIP SHOESTRING"):
    return {
        "date": d,
        "unit_cost": cost,
        "unit_name": unit,
        "supplier_name": sup,
        "quantity": qty,
        "item_name": item,
    }


def test_a_supplier_price_step_that_sticks():
    rows = [
        _line("2026-09-01", 52.21),
        _line("2026-09-10", 52.21),
        _line("2026-09-20", 65.47),
        _line("2026-09-27", 65.47),
        _line("2026-10-04", 65.47),
    ]
    f = det.price_rises(venue(histories={"i1": rows}))[0]
    assert f.headline == "Bidfood put Chip Shoestring up 25% at La Zeppa"
    assert f.data["from"] == 52.21 and f.data["to"] == 65.47


def test_a_price_that_only_looks_higher_in_another_unit_is_ignored():
    """A box at $197.62 is not a kilo at $16.80 (seen on real receipts)."""
    rows = [
        _line("2026-09-20", 16.80, unit="Kilo"),
        _line("2026-09-27", 197.62, unit="Box"),
        _line("2026-10-04", 197.62, unit="Box"),
        _line("2026-10-08", 16.80, unit="Kilo"),
    ]
    assert det.price_rises(venue(histories={"i1": rows})) == []


def test_a_one_off_spike_is_not_a_price_rise():
    rows = [
        _line("2026-09-01", 50.0),
        _line("2026-09-10", 60.0),
        _line("2026-09-20", 50.0),
        _line("2026-09-27", 50.0),
    ]
    assert det.price_rises(venue(histories={"i1": rows})) == []


def test_stock_from_a_sister_venue_and_market_priced_items_are_not_supplier_prices():
    sister = [
        _line(d, c, sup="The Glass Goose")
        for d, c in (
            ("2026-09-01", 20.0),
            ("2026-09-20", 30.0),
            ("2026-09-27", 30.0),
            ("2026-10-04", 30.0),
        )
    ]
    market = [
        _line(d, c, item="MARKET FISH")
        for d, c in (
            ("2026-09-01", 25.0),
            ("2026-09-20", 40.0),
            ("2026-09-27", 40.0),
            ("2026-10-04", 40.0),
        )
    ]
    v = venue(
        histories={"a": sister, "b": market}, internal_suppliers={"The Glass Goose"}
    )
    assert det.price_rises(v) == []


def test_sister_venues_paying_different_prices_for_the_same_item():
    def row(cost, sup="Bidfood"):
        return {
            "item_name": "OIL OLIVE EXTRA VIRGIN",
            "base_unit": "L",
            "supplier_name": sup,
            "unit_cost_last": cost,
            "invoice_count": 5,
            "quantity_base": 300.0,
        }

    a = det.VenueData(
        venue_id="lz", venue_name="La Zeppa", as_of=AS_OF, item_supplier=[row(26.27)]
    )
    b = det.VenueData(
        venue_id="dsc",
        venue_name="Dunedin Social Club",
        as_of=AS_OF,
        item_supplier=[row(20.16)],
    )
    out = det.group_price_gaps([a, b])
    assert list(out) == ["lz"]
    f = out["lz"][0]
    assert (
        f.headline
        == "La Zeppa pays 30% more for Oil Olive Extra Virgin than Dunedin Social Club"
    )
    assert "the same supplier" in f.prompt and f.data["same_supplier"] is True


def test_cost_of_goods_rising_on_purchases_against_sales():
    rows = []
    for k in range(13, 0, -1):
        start = det.week_start(AS_OF) - timedelta(weeks=k)
        purchases = 440.0 if k <= 4 else 340.0
        rows.append(
            {"bucket_start": start.isoformat(), "purchases": purchases, "sales": 1000.0}
        )
    f = det.cogs_trend(venue(pcogs_weekly=rows))[0]
    assert f.headline == "La Zeppa's cost of goods is 44% over 4 weeks, up from 34%"
