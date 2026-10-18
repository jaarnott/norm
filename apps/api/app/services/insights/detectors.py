"""Insight detectors: pure functions over a venue's tool results.

Each detector reads a :class:`VenueData` (what the daily run fetched through
Norm's own tools — get_sales, get_labour, get_purchase_cogs,
get_received_items_for_period) and returns :class:`Finding` s. Nothing here
calls anything, so every rule is tested on fixtures.

The thresholds come from the Oct 2026 research on nine real venues
(docs/plans/insights-research.md). The rules that matter most:

* **Against the venue's own normal.** Cook Brothers beats its budgets almost
  every week, so "over budget" alone is noise. An unusual day is one far from
  the last six of the same weekday; budgets get their own "out of date" check.
* **Data first.** A venue with no clock-ins, no pay rates or no budgets says so
  once, and its wage or budget detectors stay quiet rather than report a 0%.
* **Like with like.** Supplier prices are compared on lines with the same
  invoiced unit (a box at $197.62 is not a kilo at $16.80), never across sister
  venues trading stock, and never at implausible jumps that mean a unit error.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.services.insights.rules import render, threshold

WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


@dataclass
class Finding:
    detector: str
    subject: str
    area: str  # sales | cogs | wages | data
    audience: str  # a permission scope
    headline: str
    prompt: str
    value: float = 0.0  # dollars per week
    data: dict = field(default_factory=dict)
    #: How long the finding stays true enough to show, in days from the run.
    lasts_days: int = 7


@dataclass
class VenueData:
    venue_id: str
    venue_name: str
    #: The venue's local date of the run. The last complete trading day is the
    #: day before.
    as_of: date
    #: Daily sales EXCLUDING GST (wage %, discounts, trends) …
    sales_daily: list[dict] = field(default_factory=list)
    #: … and INCLUDING GST with budgets, as Loaded stores budgets, so the
    #: effective tax rate of the sales can't skew a budget comparison.
    sales_daily_incl: list[dict] = field(default_factory=list)
    orders_daily: list[dict] = field(default_factory=list)
    #: None when not read this run (days without the weekly set); [] when read
    #: and Loaded had nothing.
    labour_daily: list[dict] | None = None
    labour_staff: list[dict] = field(default_factory=list)
    roster_next: list[dict] = field(default_factory=list)
    pcogs_weekly: list[dict] = field(default_factory=list)
    discounts_recent: list[dict] = field(default_factory=list)
    discounts_prior: list[dict] = field(default_factory=list)
    item_supplier: list[dict] = field(default_factory=list)
    #: item_id -> its receipt lines (get_received_items_for_period history).
    histories: dict[str, list[dict]] = field(default_factory=dict)
    #: Sister venues' names: stock moved between them is not a supplier price.
    internal_suppliers: set[str] = field(default_factory=set)
    #: The last 4 complete weeks hour by hour: get_labour's detail rows (the
    #: clock-ins, or the roster where the venue doesn't clock in) and hourly
    #: sales excluding GST.
    shifts: list[dict] = field(default_factory=list)
    sales_hourly: list[dict] = field(default_factory=list)
    #: The hour the venue's trading day starts; earlier hours belong to the
    #: day before.
    day_start_hour: int = 7
    #: The admin's overrides of thresholds and templates (rules.py), by
    #: detector; None = the defaults.
    rules: dict | None = None


# ------------------------------------------------------------------ helpers
def money(x: float) -> str:
    return f"${x:,.0f}"


def pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def labour_date(label: str, as_of: date) -> date | None:
    """get_labour day rows say "Mon 22 Jun" — the year is the one that puts it
    on or before the run, within a year."""
    for year in (as_of.year, as_of.year - 1):
        try:
            d = datetime.strptime(f"{label} {year}", "%a %d %b %Y").date()
        except ValueError:
            return None
        if d <= as_of:
            return d
    return None


def nice(name: str) -> str:
    """Supplier and item names as people write them, not as an invoice shouts."""
    name = " ".join(str(name or "").split())
    return name.title() if name.isupper() else name


#: Priced by the day, so a change is the market, not the supplier.
MARKET_PRICED = ("market", "catch of the day", "seasonal")


def _iso(d) -> date | None:
    try:
        return date.fromisoformat(str(d)[:10])
    except (TypeError, ValueError):
        return None


def sales_by_day(v: VenueData) -> dict[date, dict]:
    out = {}
    for r in v.sales_daily:
        d = _iso(r.get("date"))
        if d:
            out[d] = r
    return out


def labour_by_day(v: VenueData) -> dict[date, dict]:
    out = {}
    for r in v.labour_daily or []:
        d = labour_date(str(r.get("date") or ""), v.as_of)
        if d:
            out[d] = r
    return out


def _actual(r: dict | None) -> float:
    return float((r or {}).get("actual") or 0)


def _cost(r: dict | None) -> float:
    return float((r or {}).get("actual_cost") or 0)


def complete_labour_weeks(v: VenueData) -> list[date]:
    """Trading weeks (Monday) before this one with wages on 3+ days and sales
    that week. Labour rows exist only for days with shifts — a cafe open
    Tuesday to Saturday has five — so a week is never judged by row count."""
    count: dict[date, int] = {}
    for d, r in labour_by_day(v).items():
        if _cost(r) > 0:
            count[week_start(d)] = count.get(week_start(d), 0) + 1
    sales = sales_by_day(v)
    this_week = week_start(v.as_of)
    return sorted(
        w
        for w, n in count.items()
        if n >= 3 and w < this_week and _week_sum(sales, w, _actual) > 0
    )


def fresh_labour_weeks(v: VenueData, n: int) -> list[date]:
    """The last ``n`` complete labour weeks — only if the latest is last week
    and none is more than a week apart. Older weeks would describe a venue
    whose timesheets have stopped (data_health says that instead)."""
    weeks = complete_labour_weeks(v)[-n:]
    last = week_start(v.as_of) - timedelta(days=7)
    if len(weeks) < n or weeks[-1] != last or (weeks[-1] - weeks[0]).days > 7 * n:
        return []
    return weeks


def _week_sum(series: dict[date, dict], week: date, fn) -> float:
    return sum(fn(series.get(week + timedelta(days=i))) for i in range(7))


def usual_wage_pct(v: VenueData, weeks: int = 8) -> float | None:
    sales, lab = sales_by_day(v), labour_by_day(v)
    rates = []
    for w in complete_labour_weeks(v)[-weeks:]:
        s = _week_sum(sales, w, _actual)
        if s > 0:
            rates.append(_week_sum(lab, w, _cost) / s)
    rates = [r for r in rates if r < 0.9]  # a week with sales missing is not a rate
    return statistics.median(rates) if len(rates) >= 4 else None


def typical_day(sales: dict[date, dict], day: date, weeks: int = 6) -> float | None:
    vals = [_actual(sales.get(day - timedelta(weeks=k))) for k in range(1, weeks + 1)]
    vals = [x for x in vals if x > 0]
    return statistics.median(vals) if len(vals) >= 3 else None


# ------------------------------------------------------------------ data health
def data_health(v: VenueData) -> list[Finding]:
    out: list[Finding] = []
    sales = sales_by_day(v)
    if not any(_actual(r) > 0 for r in sales.values()):
        return out
    if v.labour_daily is not None:
        lab = labour_by_day(v)
        recent = [r for d, r in lab.items() if (v.as_of - d).days <= 28]
        hours = [d for d, r in lab.items() if float(r.get("actual_hours") or 0) > 0]
        last = max(hours) if hours else None
        if last is None or (v.as_of - last).days > threshold(
            v.rules, "data_no_labour", "stale_days"
        ):
            since = f"since {last:%a %d %b}" if last else "in the last two months"
            facts = {"venue": v.venue_name, "since": since}
            out.append(
                Finding(
                    "data_no_labour",
                    "labour",
                    "data",
                    "org:manage",
                    headline=f"No hours worked have reached Loaded for {v.venue_name} {since}",
                    prompt=render(v.rules, "data_no_labour", "prompt", facts),
                    data={"facts": facts},
                )
            )
        elif sum(float(r.get("actual_hours") or 0) for r in recent) > 0 and all(
            float(r.get("actual_cost") or 0) == 0 for r in recent
        ):
            hrs = sum(float(r.get("actual_hours") or 0) for r in recent)
            facts = {"venue": v.venue_name, "hours": f"{hrs:.0f}"}
            out.append(
                Finding(
                    "data_pay_rates",
                    "pay_rates",
                    "data",
                    "org:manage",
                    headline=f"{v.venue_name}'s staff have no pay rates in Loaded ({hrs:.0f} hours at $0)",
                    prompt=render(v.rules, "data_pay_rates", "prompt", facts),
                    data={"facts": facts},
                )
            )
        elif len(recent) >= threshold(v.rules, "data_no_clock_ins", "min_days") and all(
            float(r.get("actual_hours") or 0) == float(r.get("rostered_hours") or 0)
            and float(r.get("actual_cost") or 0) == float(r.get("rostered_cost") or 0)
            for r in recent
        ):
            out.append(
                Finding(
                    "data_no_clock_ins",
                    "clock_ins",
                    "data",
                    "org:manage",
                    headline=f"Nobody clocks in at {v.venue_name}: Loaded counts the roster as hours worked",
                    prompt=render(
                        v.rules, "data_no_clock_ins", "prompt", {"venue": v.venue_name}
                    ),
                    data={"facts": {"venue": v.venue_name}},
                    lasts_days=28,
                )
            )
    if v.sales_daily_incl and not any(r.get("budget") for r in v.sales_daily_incl):
        out.append(
            Finding(
                "data_budgets",
                "budgets_missing",
                "data",
                "org:manage",
                headline=f"{v.venue_name} has no sales budgets in Loaded",
                prompt=render(
                    v.rules, "data_budgets", "prompt", {"venue": v.venue_name}
                ),
                data={"facts": {"venue": v.venue_name}},
                lasts_days=28,
            )
        )
    return out


# ------------------------------------------------------------------ sales
def unusual_day(v: VenueData) -> list[Finding]:
    sales = sales_by_day(v)
    y = v.as_of - timedelta(days=1)
    r = sales.get(y)
    typical = typical_day(sales, y)
    if not r or not typical:
        return []
    a = _actual(r)
    diff = a - typical
    if abs(diff) < max(
        threshold(v.rules, "unusual_day", "min_dollars"), 0.15 * typical
    ) or abs(diff) / typical < threshold(v.rules, "unusual_day", "min_change"):
        return []
    word = "up" if diff > 0 else "down"
    facts = {
        "day": f"{y:%A}",
        "venue": v.venue_name,
        "direction": word,
        "change": pct(abs(diff) / typical),
        "actual": money(a),
        "usual": money(typical),
    }
    return [
        Finding(
            "unusual_day",
            y.isoformat(),
            "sales",
            "reports:read",
            headline=f"{y:%A} at {v.venue_name} was {word} {pct(abs(diff) / typical)} on a usual {y:%A}",
            prompt=render(v.rules, "unusual_day", "prompt", facts),
            value=abs(diff),
            data={"actual": a, "typical": typical, "facts": facts},
            lasts_days=1,
        )
    ]


def sales_trend(v: VenueData) -> list[Finding]:
    sales = sales_by_day(v)
    end = week_start(v.as_of)
    cur = [sales.get(end - timedelta(days=i)) for i in range(1, 29)]
    prev = [sales.get(end - timedelta(days=i)) for i in range(29, 57)]
    if any(r is None for r in cur):
        return []
    a = sum(_actual(r) for r in cur)
    if a <= 0:
        return []
    ly = (
        sum(float(r.get("last_year") or 0) for r in cur)
        if all(r.get("last_year") for r in cur)
        else 0
    )
    if ly:
        base, label = ly, "the same 4 weeks last year"
    elif all(r is not None and _actual(r) > 0 for r in prev):
        base, label = sum(_actual(r) for r in prev), "the 4 weeks before"
    else:
        return []
    change = (a - base) / base
    if abs(change) < threshold(v.rules, "sales_trend", "min_change"):
        return []
    word = "up" if change > 0 else "down"
    facts = {
        "venue": v.venue_name,
        "direction": word,
        "change": pct(abs(change)),
        "basis": label,
        "sales": money(a),
        "base": money(base),
    }
    return [
        Finding(
            "sales_trend",
            "4_weeks",
            "sales",
            "reports:read",
            headline=f"{v.venue_name}'s sales are {word} {pct(abs(change))} on {label}",
            prompt=render(v.rules, "sales_trend", "prompt", facts),
            value=abs(a - base) / 4,
            data={"sales": a, "base": base, "basis": label, "facts": facts},
        )
    ]


def average_order(v: VenueData) -> list[Finding]:
    by = {}
    for r in v.orders_daily:
        d = _iso(r.get("date"))
        if d:
            by[d] = r
    end = week_start(v.as_of)

    def avg(days):
        rows = [by.get(end - timedelta(days=i)) for i in days]
        rows = [r for r in rows if r and r.get("orders")]
        if len(rows) < 20:
            return None, 0
        n = sum(int(r["orders"]) for r in rows)
        return sum(float(r.get("order_value") or 0) for r in rows) / n, n

    a, n = avg(range(1, 29))
    p, _ = avg(range(29, 57))
    if (
        not a
        or not p
        or abs(a - p) / p < threshold(v.rules, "average_order", "min_change")
    ):
        return []
    word = "up" if a > p else "down"
    facts = {
        "venue": v.venue_name,
        "direction": word,
        "change": pct(abs(a - p) / p),
        "average": f"${a:.2f}",
        "previous": f"${p:.2f}",
    }
    return [
        Finding(
            "average_order",
            "average_order",
            "sales",
            "reports:read",
            headline=f"The average order at {v.venue_name} is {word} {pct(abs(a - p) / p)} to ${a:.2f}",
            prompt=render(v.rules, "average_order", "prompt", facts),
            value=abs(a - p) * n / 4,
            data={"average": a, "previous": p, "orders": n, "facts": facts},
        )
    ]


def discounts(v: VenueData) -> list[Finding]:
    total = sum(float(r.get("discounts_amount") or 0) for r in v.discounts_recent)
    sales = sales_by_day(v)
    sales4 = sum(_actual(sales.get(v.as_of - timedelta(days=i))) for i in range(1, 29))
    if total <= 0 or sales4 <= 0:
        return []
    prior = {
        r.get("discount"): float(r.get("discounts_amount") or 0)
        for r in v.discounts_prior
    }
    share = total / (sales4 + total)
    out: list[Finding] = []
    rows = sorted(
        v.discounts_recent, key=lambda r: -float(r.get("discounts_amount") or 0)
    )
    for r in rows[:5]:
        name = str(r.get("discount") or "Unnamed")
        amount = float(r.get("discounts_amount") or 0)
        before = prior.get(r.get("discount"), 0.0)
        growing = (
            before > 0
            and amount >= before * threshold(v.rules, "discount", "growing_factor")
            and amount - before >= threshold(v.rules, "discount", "growing_min")
        )
        biggest = (
            r is rows[0]
            and share >= threshold(v.rules, "discount", "biggest_share")
            and amount >= threshold(v.rules, "discount", "biggest_min")
        )
        if not (growing or biggest):
            continue
        generic = name.strip().lower() in ("discount", "discounts")
        label = (
            "Discounts"
            if generic
            else name
            if "discount" in name.lower()
            else f"{name} discounts"
        )
        the = (
            "discounts"
            if generic
            else f"the {name} discount"
            if "discount" not in name.lower()
            else f"the {name}"
        )
        if growing:
            facts = {
                "discounts": label[0].lower() + label[1:] if generic else label,
                "venue": v.venue_name,
                "amount": money(amount),
                "before": money(before),
            }
            ask = render(v.rules, "discount", "growing", facts)
        else:
            facts = {
                "the_discount": the,
                "venue": v.venue_name,
                "amount": money(amount),
                "share": pct(share),
            }
            ask = render(v.rules, "discount", "biggest", facts)
        out.append(
            Finding(
                "discount",
                name,
                "sales",
                "reports:read",
                headline=f"{label} cost {v.venue_name} {money(amount)} in 4 weeks",
                prompt=ask,
                value=amount / 4,
                data={
                    "amount": amount,
                    "before": before,
                    "share": share,
                    "facts": facts,
                    "template": "growing" if growing else "biggest",
                },
            )
        )
    return out


def budgets_out_of_date(v: VenueData) -> list[Finding]:
    """Budgets beaten (or missed) by 10%+ in 6 of the last 8 weeks. Compared
    as Loaded stores them — including GST — so the effective tax rate of the
    sales doesn't skew it."""
    sales = {}
    for r in v.sales_daily_incl:
        d = _iso(r.get("date"))
        if d:
            sales[d] = r
    end = week_start(v.as_of)
    ratios = []
    for k in range(1, 9):
        w = end - timedelta(weeks=k)
        days = [sales.get(w + timedelta(days=i)) for i in range(7)]
        if any(r is None or not r.get("budget") for r in days):
            continue
        b = sum(float(r["budget"]) for r in days)
        if b > 0:
            ratios.append(sum(_actual(r) for r in days) / b)
    band = threshold(v.rules, "budgets_out_of_date", "band")
    need = threshold(v.rules, "budgets_out_of_date", "min_weeks")
    if len(ratios) < need:
        return []
    over = [r for r in ratios if r >= 1 + band]
    under = [r for r in ratios if r <= 1 - band]
    if len(over) < need and len(under) < need:
        return []
    group = over if len(over) >= need else under
    word = "beaten" if group is over else "missed"
    avg = statistics.mean(group) - 1
    facts = {
        "venue": v.venue_name,
        "direction": word,
        "gap": pct(abs(avg)),
        "weeks": str(len(group)),
    }
    return [
        Finding(
            "budgets_out_of_date",
            "budgets",
            "sales",
            "reports:read",
            headline=f"{v.venue_name} has {word} its budget by ~{pct(abs(avg))} in {len(group)} of 8 weeks",
            prompt=render(v.rules, "budgets_out_of_date", "prompt", facts),
            value=0.0,
            data={
                "weeks": len(ratios),
                "ratios": [round(r, 3) for r in ratios],
                "facts": facts,
            },
            lasts_days=28,
        )
    ]


# ------------------------------------------------------------------ wages
def quiet_day_wages(v: VenueData) -> list[Finding]:
    weeks = fresh_labour_weeks(v, 4)
    if not weeks:
        return []
    sales, lab = sales_by_day(v), labour_by_day(v)
    by = {i: [0.0, 0.0] for i in range(7)}
    for w in weeks:
        for i in range(7):
            d = w + timedelta(days=i)
            by[i][0] += _actual(sales.get(d))
            by[i][1] += _cost(lab.get(d))
    tot_s = sum(s for s, _ in by.values())
    tot_c = sum(c for _, c in by.values())
    if tot_s <= 0 or tot_c / tot_s >= 0.9:
        return []
    week_rate = tot_c / tot_s
    out = []
    for i, (s, c) in by.items():
        if s <= 0:
            continue
        rate = c / s
        excess = (rate - week_rate) * s / 4
        if rate - week_rate >= threshold(
            v.rules, "quiet_day_wages", "min_gap"
        ) and excess >= threshold(v.rules, "quiet_day_wages", "min_excess"):
            day = WEEKDAYS[i]
            facts = {
                "day": day,
                "venue": v.venue_name,
                "day_share": pct(rate),
                "week_share": pct(week_rate),
            }
            out.append(
                Finding(
                    "quiet_day_wages",
                    day,
                    "wages",
                    "reports:read",
                    headline=f"{day}s at {v.venue_name} run wages at {pct(rate)} of sales (the week: {pct(week_rate)})",
                    prompt=render(v.rules, "quiet_day_wages", "prompt", facts),
                    value=excess,
                    data={
                        "rate": rate,
                        "week_rate": week_rate,
                        "excess_per_day": excess,
                        "facts": facts,
                    },
                )
            )
    return sorted(out, key=lambda f: -f.value)[:2]


def _minutes(clock: str) -> int | None:
    try:
        t = datetime.strptime(str(clock or "").strip(), "%I:%M %p")
    except ValueError:
        return None
    return t.hour * 60 + t.minute


def _worked(shift: dict, as_of: date) -> list[tuple[datetime, datetime]]:
    """A detail row's time on the floor, unpaid breaks out. One that ends at
    or before it starts ran past midnight."""
    d = labour_date(str(shift.get("date") or ""), as_of)
    s, e = _minutes(shift.get("start")), _minutes(shift.get("end"))
    if d is None or s is None or e is None:
        return []
    base = datetime.combine(d, datetime.min.time())
    start, end = base + timedelta(minutes=s), base + timedelta(minutes=e)
    if end <= start:
        end += timedelta(days=1)
    spans = [(start, end)]
    for b in shift.get("breaks") or []:
        bs = _minutes(b.get("start"))
        if b.get("paid") or bs is None:
            continue
        b0 = base + timedelta(minutes=bs)
        if b0 < start:
            b0 += timedelta(days=1)
        b1 = b0 + timedelta(minutes=float(b.get("duration_mins") or 0))
        kept = []
        for a, z in spans:
            if b1 <= a or b0 >= z:
                kept.append((a, z))
                continue
            if a < b0:
                kept.append((a, b0))
            if b1 < z:
                kept.append((b1, z))
        spans = kept
    return spans


def hourly(v: VenueData) -> tuple[dict, dict]:
    """(trading date, clock hour) -> [staff-hours, wages], and -> sales."""
    staff: dict[tuple[date, int], list[float]] = {}
    for sh in v.shifts:
        spans = _worked(sh, v.as_of)
        hours = sum((z - a).total_seconds() for a, z in spans) / 3600
        if hours <= 0:
            continue
        rate = float(sh.get("cost") or 0) / hours
        for a, z in spans:
            t = a
            while t < z:
                nxt = min(z, t.replace(minute=0, second=0) + timedelta(hours=1))
                h = (nxt - t).total_seconds() / 3600
                key = ((t - timedelta(hours=v.day_start_hour)).date(), t.hour)
                cell = staff.setdefault(key, [0.0, 0.0])
                cell[0] += h
                cell[1] += h * rate
                t = nxt
    sales: dict[tuple[date, int], float] = {}
    for r in v.sales_hourly:
        d = _iso(r.get("date"))
        try:
            h = int(str(r.get("time") or "")[:2])
        except ValueError:
            continue
        if d is not None:
            sales[(d, h)] = sales.get((d, h), 0.0) + _actual(r)
    return staff, sales


def _clock_label(h: int) -> str:
    h %= 24
    if h == 0:
        return "midnight"
    if h == 12:
        return "noon"
    return f"{h % 12}{'am' if h < 12 else 'pm'}"


def quiet_hours(v: VenueData) -> list[Finding]:
    """A stretch of trading hours on one weekday where the staff on don't
    follow the trade — Tuesday 12–6 with four on for $175 an hour (Mr
    Murdochs, Oct 2026). Only inside trading hours: prep before the first
    sale and the close after the last are work sales can't judge. A weekday
    quiet all day is quiet_day_wages' finding, not this one."""
    staff, sales = hourly(v)
    total_w = sum(c[1] for c in staff.values())
    total_s = sum(sales.values())
    total_h = sum(c[0] for c in staff.values())
    if total_w <= 0 or total_s <= 0:
        return []  # no pay rates or no sales: data_health says so
    usual, rate = total_w / total_s, total_w / total_h
    crew = threshold(v.rules, "quiet_hours", "crew")
    lengths = range(
        int(threshold(v.rules, "quiet_hours", "min_hours")),
        int(threshold(v.rules, "quiet_hours", "max_hours")) + 1,
    )
    min_excess = threshold(v.rules, "quiet_hours", "min_excess")
    whole_days = {f.subject for f in quiet_day_wages(v)}
    dates = sorted({d for d, _ in sales})
    clock = [(v.day_start_hour + i) % 24 for i in range(24)]
    out = []
    for wd, day in enumerate(WEEKDAYS):
        ds = [d for d in dates if d.weekday() == wd]
        if len(ds) < 3 or day in whole_days:
            continue
        avg = {h: sum(sales.get((d, h), 0.0) for d in ds) / len(ds) for h in clock}
        paid = {
            h: sum(staff.get((d, h), [0.0, 0.0])[1] for d in ds) / len(ds)
            for h in clock
        }
        # What each hour could save: its wages above the venue's usual share
        # of its sales, but never below the crew.
        spare = {h: min(paid[h] - usual * avg[h], paid[h] - crew * rate) for h in clock}
        floor = max(50.0, 0.05 * max(avg.values()))
        trading = [i for i, h in enumerate(clock) if avg[h] >= floor]
        if not trading:
            continue
        best, best_key = None, None
        for i in range(trading[0], trading[-1] + 1):
            for n in lengths:
                if i + n - 1 > trading[-1]:
                    break
                hs = clock[i : i + n]
                per = [
                    (
                        sum(staff.get((d, h), [0.0, 0.0])[1] for h in hs),
                        sum(sales.get((d, h), 0.0) for h in hs),
                    )
                    for d in ds
                ]
                w = sum(p[0] for p in per) / len(ds)
                s = sum(p[1] for p in per) / len(ds)
                on = sum(staff.get((d, h), [0.0, 0.0])[0] for d in ds for h in hs)
                on /= len(ds) * n
                if on < crew + 1 or s <= 0:
                    continue
                share = w / s
                steady = sum(1 for pw, ps in per if ps <= 0 or pw / ps >= 1.5 * usual)
                excess = sum(spare[h] for h in hs)
                if (
                    share < max(2 * usual, 0.5)
                    or steady < len(ds) - 1
                    or excess < min_excess
                ):
                    continue
                # The most to save; of equals, the tighter stretch.
                key = (round(excess), -n)
                if best_key is None or key > best_key:
                    best_key = key
                    best = {
                        "start": hs[0],
                        "end": (hs[-1] + 1) % 24,
                        "staff": on,
                        "sales_per_hour": s / n,
                        "share": share,
                        "usual": usual,
                        "excess": excess,
                    }
        if best is None:
            continue
        span = f"{_clock_label(best['start'])} to {_clock_label(best['end'])}"
        people = max(round(best["staff"]), 3)
        facts = {
            "people": str(people),
            "venue": v.venue_name,
            "day": day,
            "span": span,
            "sales_per_hour": money(best["sales_per_hour"]),
            "share": pct(best["share"]),
            "week_share": pct(usual),
        }
        out.append(
            Finding(
                "quiet_hours",
                day,
                "wages",
                "reports:read",
                headline=(
                    f"{day}s {span} at {v.venue_name}: about {people} on for "
                    f"{money(best['sales_per_hour'])} an hour of sales"
                ),
                prompt=render(v.rules, "quiet_hours", "prompt", facts),
                value=best["excess"],
                data={**best, "weeks": len(ds), "facts": facts},
            )
        )
    return sorted(out, key=lambda f: -f.value)[:2]


def over_roster(v: VenueData) -> list[Finding]:
    last = week_start(v.as_of) - timedelta(days=7)
    if last not in complete_labour_weeks(v):
        return []
    lab = labour_by_day(v)
    rows = [lab.get(last + timedelta(days=i)) or {} for i in range(7)]
    rc = sum(float(r.get("rostered_cost") or 0) for r in rows)
    ac = sum(float(r.get("actual_cost") or 0) for r in rows)
    rh = sum(float(r.get("rostered_hours") or 0) for r in rows)
    ah = sum(float(r.get("actual_hours") or 0) for r in rows)
    if (
        rc <= 0
        or (ac - rc) / rc < threshold(v.rules, "over_roster", "min_over")
        or ac - rc < threshold(v.rules, "over_roster", "min_dollars")
    ):
        return []
    facts = {
        "venue": v.venue_name,
        "extra": money(ac - rc),
        "hours": f"{ah:.0f}",
        "rostered_hours": f"{rh:.0f}",
    }
    return [
        Finding(
            "over_roster",
            "last_week",
            "wages",
            "roster:read",
            headline=f"{v.venue_name} paid {money(ac - rc)} more in wages than rostered last week",
            prompt=render(v.rules, "over_roster", "prompt", facts),
            value=ac - rc,
            data={
                "rostered": rc,
                "actual": ac,
                "hours": ah,
                "rostered_hours": rh,
                "facts": facts,
            },
        )
    ]


def staff_over_roster(v: VenueData) -> list[Finding]:
    over = threshold(v.rules, "staff_over_roster", "over")
    min_hours = threshold(v.rules, "staff_over_roster", "min_rostered_hours")
    min_dollars = threshold(v.rules, "staff_over_roster", "min_dollars")
    hits = [
        r
        for r in v.labour_staff
        if float(r.get("rostered_hours") or 0) >= min_hours
        and float(r.get("actual_hours") or 0)
        > float(r.get("rostered_hours") or 0) * (1 + over)
        and float(r.get("actual_cost") or 0) - float(r.get("rostered_cost") or 0)
        >= min_dollars
    ]
    if not hits:
        return []
    extra = sum(float(r["actual_cost"]) - float(r["rostered_cost"]) for r in hits)
    top = max(hits, key=lambda r: float(r["actual_cost"]) - float(r["rostered_cost"]))
    facts = {
        "venue": v.venue_name,
        "staff": str(len(hits)),
        "over": pct(over),
        "extra": money(extra),
        "name": str(top.get("name")),
        "hours": f"{float(top['actual_hours']):.0f}",
        "rostered_hours": f"{float(top['rostered_hours']):.0f}",
    }
    return [
        Finding(
            "staff_over_roster",
            "staff",
            "wages",
            "roster:write",
            headline=f"{len(hits)} staff at {v.venue_name} worked well over their rostered hours",
            prompt=render(v.rules, "staff_over_roster", "prompt", facts),
            value=extra / 4,
            data={"staff": len(hits), "extra": extra, "facts": facts},
        )
    ]


def wage_rate_up(v: VenueData) -> list[Finding]:
    last = week_start(v.as_of) - timedelta(days=7)
    weeks = complete_labour_weeks(v)
    if last not in weeks:
        return []
    sales, lab = sales_by_day(v), labour_by_day(v)
    s = _week_sum(sales, last, _actual)
    c = _week_sum(lab, last, _cost)
    if s <= 0:
        return []
    rate = c / s
    hist = []
    for w in [w for w in weeks if w < last][-8:]:
        ws = _week_sum(sales, w, _actual)
        if ws > 0 and _week_sum(lab, w, _cost) / ws < 0.9:
            hist.append(_week_sum(lab, w, _cost) / ws)
    if len(hist) < 4 or rate >= 0.9:
        return []
    usual = statistics.median(hist)
    if rate - usual < threshold(v.rules, "wage_rate_up", "min_gap"):
        return []
    facts = {
        "venue": v.venue_name,
        "share": pct(rate),
        "usual": pct(usual),
        "wages": money(c),
        "sales": money(s),
    }
    return [
        Finding(
            "wage_rate_up",
            "last_week",
            "wages",
            "reports:read",
            headline=f"Wages at {v.venue_name} were {pct(rate)} of sales last week (usually {pct(usual)})",
            prompt=render(v.rules, "wage_rate_up", "prompt", facts),
            value=(rate - usual) * s,
            data={"rate": rate, "usual": usual, "wages": c, "sales": s, "facts": facts},
        )
    ]


def roster_ahead(v: VenueData) -> list[Finding]:
    """Next week's roster against the sales a normal week brings. Shown before
    the week starts, when it can still change."""
    if not v.roster_next:
        return []
    cost = sum(float(r.get("rostered_cost") or 0) for r in v.roster_next)
    if cost <= 0:
        return []  # no pay rates — data_health says so
    usual = usual_wage_pct(v)
    sales = sales_by_day(v)
    start = week_start(v.as_of) + timedelta(days=7)
    expected = 0.0
    for i in range(7):
        t = typical_day(sales, start + timedelta(days=i), weeks=6)
        expected += t or 0
    if not usual or expected <= 0:
        return []
    rate = cost / expected
    excess = (rate - usual) * expected
    if rate - usual < threshold(
        v.rules, "roster_ahead", "min_gap"
    ) or excess < threshold(v.rules, "roster_ahead", "min_dollars"):
        return []
    facts = {
        "venue": v.venue_name,
        "cost": money(cost),
        "share": pct(rate),
        "usual": pct(usual),
    }
    return [
        Finding(
            "roster_ahead",
            start.isoformat(),
            "wages",
            "roster:read",
            headline=f"Next week's roster at {v.venue_name} is {pct(rate)} of usual sales (you run at {pct(usual)})",
            prompt=render(v.rules, "roster_ahead", "prompt", facts),
            value=excess,
            data={
                "cost": cost,
                "expected": expected,
                "rate": rate,
                "usual": usual,
                "facts": facts,
            },
            lasts_days=(start - v.as_of).days + 1,
        )
    ]


# ------------------------------------------------------------------ cost of goods
def cogs_trend(v: VenueData) -> list[Finding]:
    rows = [r for r in v.pcogs_weekly if r.get("sales") and r.get("bucket_start")]
    rows.sort(key=lambda r: r["bucket_start"])
    done = [
        r
        for r in rows
        if (_iso(r["bucket_start"]) or v.as_of) + timedelta(days=7) <= v.as_of
    ]
    if len(done) < 12:
        return []
    last4, prev8 = done[-4:], done[-12:-4]

    def ratio(rs):
        s = sum(float(r["sales"]) for r in rs)
        return sum(float(r.get("purchases") or 0) for r in rs) / s if s else None

    a, b = ratio(last4), ratio(prev8)
    if a is None or b is None or a - b < threshold(v.rules, "cogs_trend", "min_gap"):
        return []
    sales4 = sum(float(r["sales"]) for r in last4)
    facts = {"venue": v.venue_name, "recent": pct(a), "before": pct(b)}
    return [
        Finding(
            "cogs_trend",
            "4_weeks",
            "cogs",
            "orders:read",
            headline=f"{v.venue_name}'s cost of goods is {pct(a)} over 4 weeks, up from {pct(b)}",
            prompt=render(v.rules, "cogs_trend", "prompt", facts),
            value=(a - b) * sales4 / 4,
            data={"recent": a, "before": b, "facts": facts},
        )
    ]


def _price_series(rows: list[dict], internal: set[str]) -> dict[tuple, list[dict]]:
    out: dict[tuple, list[dict]] = {}
    for r in rows:
        sup = str(r.get("supplier_name") or "").strip()
        if not sup or sup.lower() in internal or r.get("is_credit"):
            continue
        cost = float(r.get("unit_cost") or 0)
        if cost <= 0 or not r.get("date"):
            continue
        out.setdefault((sup, str(r.get("unit_name") or "")), []).append(r)
    for rs in out.values():
        rs.sort(key=lambda r: str(r["date"]))
    return out


def price_rises(v: VenueData) -> list[Finding]:
    """A supplier's price for one item, in one invoiced unit, stepping up 5%+
    and staying there for the next two deliveries, in the last 8 weeks."""
    internal = {s.lower() for s in v.internal_suppliers}
    cutoff = (
        v.as_of - timedelta(weeks=threshold(v.rules, "price_rise", "weeks"))
    ).isoformat()
    min_step = threshold(v.rules, "price_rise", "min_step")
    max_step = threshold(v.rules, "price_rise", "max_step")
    min_yearly = threshold(v.rules, "price_rise", "min_yearly")
    out: list[Finding] = []
    for item_id, rows in v.histories.items():
        item = next(
            (str(r.get("item_name")) for r in rows if r.get("item_name")), item_id
        )
        if any(word in item.lower() for word in MARKET_PRICED):
            continue
        for (sup, unit), rs in _price_series(rows, internal).items():
            for i in range(1, len(rs)):
                a, b = float(rs[i - 1]["unit_cost"]), float(rs[i]["unit_cost"])
                step = (b - a) / a
                if step < min_step or step > max_step or str(rs[i]["date"]) < cutoff:
                    continue
                after = rs[i : i + 3]
                if len(after) < 2 or any(
                    abs(float(x["unit_cost"]) - b) / b >= 0.03 for x in after[:2]
                ):
                    continue
                qty_year = (
                    sum(float(x.get("quantity") or 0) for x in rs) * 4
                )  # 13 weeks -> a year
                yearly = (b - a) * qty_year
                if yearly < min_yearly:
                    continue
                when = _iso(rs[i]["date"])
                facts = {
                    "supplier": nice(sup),
                    "item": nice(item),
                    "change": pct(step),
                    "venue": v.venue_name,
                    "old_price": f"${a:.2f}",
                    "new_price": f"${b:.2f}",
                    "per_unit": " a " + unit.lower() if unit else "",
                    "date": f"{when:%d %b}",
                    "yearly": money(yearly),
                }
                out.append(
                    Finding(
                        "price_rise",
                        f"{item} · {sup}",
                        "cogs",
                        "orders:read",
                        headline=f"{nice(sup)} put {nice(item)} up {pct(step)} at {v.venue_name}",
                        prompt=render(v.rules, "price_rise", "prompt", facts),
                        value=yearly / 52,
                        data={
                            "from": a,
                            "to": b,
                            "date": str(rs[i]["date"]),
                            "unit": unit,
                            "facts": facts,
                        },
                        lasts_days=7,
                    )
                )
    best: dict[str, Finding] = {}
    for f in out:  # the latest step per item+supplier
        if f.subject not in best or f.data["date"] > best[f.subject].data["date"]:
            best[f.subject] = f
    return sorted(best.values(), key=lambda f: -f.value)[:3]


def group_price_gaps(venues: list[VenueData]) -> dict[str, list[Finding]]:
    """The same item, in the same base unit, costing one venue 8%+ more than a
    sister venue. Same-supplier gaps first: "ask Bidfood for Dunedin's price".
    Returns findings keyed by the venue that pays more."""
    internal = {s.lower() for v in venues for s in v.internal_suppliers}
    rules = next((v.rules for v in venues if v.rules), None)
    min_gap = threshold(rules, "group_price_gap", "min_gap")
    max_gap = threshold(rules, "group_price_gap", "max_gap")
    min_yearly = threshold(rules, "group_price_gap", "min_yearly")
    by_item: dict[tuple, list[tuple[VenueData, dict]]] = {}
    for v in venues:
        for r in v.item_supplier:
            sup = str(r.get("supplier_name") or "").strip().lower()
            name = str(r.get("item_name") or "").lower()
            if (
                sup in internal
                or "freight" in name
                or any(w in name for w in MARKET_PRICED)
            ):
                continue
            if not r.get("unit_cost_last") or int(r.get("invoice_count") or 0) < 2:
                continue
            key = (str(r.get("item_name") or "").strip().upper(), r.get("base_unit"))
            by_item.setdefault(key, []).append((v, r))
    out: dict[str, list[Finding]] = {}
    for (item, unit), rows in by_item.items():
        if len({v.venue_id for v, _ in rows}) < 2:
            continue
        cheapest_v, cheapest = min(rows, key=lambda x: float(x[1]["unit_cost_last"]))
        low = float(cheapest["unit_cost_last"])
        for v, r in rows:
            if v.venue_id == cheapest_v.venue_id:
                continue
            high = float(r["unit_cost_last"])
            ratio = high / low
            yearly = (high - low) * float(r.get("quantity_base") or 0) / 13 * 52
            if not 1 + min_gap <= ratio <= 1 + max_gap or yearly < min_yearly:
                continue
            same = (
                str(r.get("supplier_name")).lower()
                == str(cheapest.get("supplier_name")).lower()
            )
            per = f"/{unit}" if unit else ""
            facts = {
                "venue": v.venue_name,
                "gap": pct(ratio - 1),
                "cheaper_venue": cheapest_v.venue_name,
                "item": nice(item),
                "our_price": f"${high:.2f}{per}",
                "our_supplier": nice(r.get("supplier_name")),
                "their_price": f"${low:.2f}{per}",
                "their_supplier": "the same supplier"
                if same
                else nice(cheapest.get("supplier_name")),
                "yearly": money(yearly),
            }
            out.setdefault(v.venue_id, []).append(
                Finding(
                    "group_price_gap",
                    item,
                    "cogs",
                    "orders:read",
                    headline=(
                        f"{v.venue_name} pays {pct(ratio - 1)} more for {nice(item)} than "
                        f"{cheapest_v.venue_name}"
                    ),
                    prompt=render(rules, "group_price_gap", "prompt", facts),
                    value=yearly / 52 * (1.5 if same else 1.0),
                    data={
                        "high": high,
                        "low": low,
                        "unit": unit,
                        "same_supplier": same,
                        "cheaper_venue": cheapest_v.venue_name,
                        "facts": facts,
                    },
                )
            )
    for vid in out:
        out[vid] = sorted(out[vid], key=lambda f: -f.value)[:3]
    return out


#: Run every day, Monday, and while next week's roster can still change.
DAILY = [unusual_day]
WEEKLY = [
    data_health,
    sales_trend,
    average_order,
    discounts,
    budgets_out_of_date,
    quiet_day_wages,
    quiet_hours,
    over_roster,
    staff_over_roster,
    wage_rate_up,
    cogs_trend,
    price_rises,
]
ROSTER = [roster_ahead]
