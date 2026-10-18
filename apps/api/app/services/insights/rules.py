"""What each insight detector looks for, and the defaults an admin can tune.

The detectors (detectors.py) hold the arithmetic; this holds the knobs:
whether a detector runs, how much its findings count in Home's ranking, whose
team-member chats show them, its thresholds, and the prompt templates it fills
in. Every value here is a DEFAULT. Settings → AI team → Suggestions stores
overrides in the shared config DB (suggestions/config.py, docs/plans/
suggestions-config.md), and a detector reads its effective values through
``threshold`` and ``render`` from the ``rules`` it is given.

Templates take named placeholders only — {venue}, {day} — never attribute
access or format specs, and each template declares the placeholders it fills
in, so a template can be checked before it is saved. A template that still
fails when it is filled in falls back to its default: a typo never stops a
finding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from string import Formatter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Threshold:
    default: float
    label: str
    #: How the admin page shows it: percent (a fraction, 0.25 = 25%),
    #: dollars, days, hours, count or times (a multiplier).
    kind: str = "count"
    minimum: float = 0.0
    maximum: float = 1_000_000.0


@dataclass(frozen=True)
class Template:
    default: str
    #: Placeholder -> what it holds, as the admin page explains it.
    placeholders: dict[str, str]
    #: Facts to show the template with before any live insight has them.
    example: dict[str, str]
    label: str = "Prompt"


@dataclass(frozen=True)
class Detector:
    name: str
    label: str
    description: str
    area: str  # sales | wages | cogs | data
    cadence: str  # daily | weekly | roster | follow-up
    audience: str  # the permission a person needs to see its findings
    #: Ranking: weight × log10(1 + $/week) when it has dollars; a fixed lift
    #: when it doesn't (data problems, budgets).
    weight: float = 1.0
    lift: float = 0.5
    valued: bool = True
    #: Whose new chats show it (Home shows every insight). None = by area.
    members: tuple[str, ...] | None = None
    thresholds: dict[str, Threshold] = field(default_factory=dict)
    templates: dict[str, Template] = field(default_factory=dict)


#: Which members' new chats show an insight of each area, by default.
AREA_MEMBERS: dict[str, tuple[str, ...]] = {
    "sales": ("reports",),
    "wages": ("reports", "time_attendance"),
    "cogs": ("procurement", "reports"),
    "data": (),  # Home only
}

V = "the venue's name"
P = lambda what, eg: f"{what} (e.g. {eg})"  # noqa: E731


def _d(**kw) -> Detector:
    return Detector(**kw)


DETECTORS: dict[str, Detector] = {
    d.name: d
    for d in (
        # ------------------------------------------------------------ data
        _d(
            name="data_no_labour",
            label="No hours worked",
            description="No hours worked have reached Loaded for over a week, so wage insights can't run.",
            area="data",
            cadence="weekly",
            audience="org:manage",
            lift=1.0,
            valued=False,
            thresholds={
                "stale_days": Threshold(
                    8, "Days without hours worked before it speaks", "days", 1, 60
                )
            },
            templates={
                "prompt": Template(
                    "No hours worked have come through from Loaded for {venue} {since} — "
                    "what's happening with the roster and timesheets there?",
                    {"venue": V, "since": P("since when", "since Mon 05 Oct")},
                    {"venue": "La Zeppa", "since": "since Mon 05 Oct"},
                )
            },
        ),
        _d(
            name="data_pay_rates",
            label="Missing pay rates",
            description="Hours worked show as $0 in the last 4 weeks: staff are missing pay rates in Loaded.",
            area="data",
            cadence="weekly",
            audience="org:manage",
            lift=1.0,
            valued=False,
            templates={
                "prompt": Template(
                    "Which staff at {venue} are missing pay rates in Loaded? {hours} hours over "
                    "the last 4 weeks show as $0, so wages can't be worked out.",
                    {"venue": V, "hours": P("hours at $0", "456")},
                    {"venue": "Somewhere North", "hours": "456"},
                )
            },
        ),
        _d(
            name="data_no_clock_ins",
            label="Nobody clocks in",
            description="Every shift's hours worked equal the roster, so Loaded is counting the roster as worked.",
            area="data",
            cadence="weekly",
            audience="org:manage",
            lift=0.6,
            valued=False,
            thresholds={
                "min_days": Threshold(
                    10,
                    "Days of identical roster and hours before it speaks",
                    "days",
                    3,
                    28,
                )
            },
            templates={
                "prompt": Template(
                    "Nobody seems to clock in at {venue} — Loaded shows the rostered hours as "
                    "worked on every shift. How are staff recording their hours there?",
                    {"venue": V},
                    {"venue": "Little West"},
                )
            },
        ),
        _d(
            name="data_budgets",
            label="No sales budgets",
            description="The venue has no sales budgets in Loaded.",
            area="data",
            cadence="weekly",
            audience="org:manage",
            lift=0.5,
            valued=False,
            templates={
                "prompt": Template(
                    "{venue} has no sales budgets in Loaded — can you suggest weekly budgets "
                    "from its recent trading?",
                    {"venue": V},
                    {"venue": "Applegum Cafe"},
                )
            },
        ),
        # ------------------------------------------------------------ sales
        _d(
            name="unusual_day",
            label="An unusual day",
            description="Yesterday's sales were far from the usual for that weekday (the median of the last six).",
            area="sales",
            cadence="daily",
            audience="reports:read",
            weight=0.6,
            thresholds={
                "min_change": Threshold(
                    0.25, "Change from a usual day", "percent", 0.05, 2
                ),
                "min_dollars": Threshold(500, "And at least", "dollars", 0, 100_000),
            },
            templates={
                "prompt": Template(
                    "Why was {day} at {venue} {direction} {change} on a normal {day}? We took "
                    "{actual} (ex GST) against the usual {usual} — which hours, products and "
                    "staff made the difference?",
                    {
                        "day": P("the weekday", "Friday"),
                        "venue": V,
                        "direction": "up or down",
                        "change": P("how much", "39%"),
                        "actual": P("what it took", "$10,366"),
                        "usual": P("a usual day", "$7,442"),
                    },
                    {
                        "day": "Friday",
                        "venue": "La Zeppa",
                        "direction": "up",
                        "change": "39%",
                        "actual": "$10,366",
                        "usual": "$7,442",
                    },
                )
            },
        ),
        _d(
            name="sales_trend",
            label="Sales trend",
            description="The last 4 weeks of sales against the same weeks last year (or the 4 weeks before).",
            area="sales",
            cadence="weekly",
            audience="reports:read",
            weight=0.3,
            thresholds={"min_change": Threshold(0.10, "Change", "percent", 0.02, 2)},
            templates={
                "prompt": Template(
                    "Why are sales at {venue} {direction} {change} on {basis}? {sales} over "
                    "the last 4 weeks against {base} — which days, hours and products are "
                    "driving it?",
                    {
                        "venue": V,
                        "direction": "up or down",
                        "change": P("how much", "18%"),
                        "basis": P("what it's against", "the same 4 weeks last year"),
                        "sales": P("the last 4 weeks", "$241,618"),
                        "base": P("the comparison", "$205,040"),
                    },
                    {
                        "venue": "The Glass Goose",
                        "direction": "up",
                        "change": "18%",
                        "basis": "the 4 weeks before",
                        "sales": "$241,618",
                        "base": "$205,040",
                    },
                )
            },
        ),
        _d(
            name="average_order",
            label="Average order",
            description="The average order over the last 4 weeks against the 4 weeks before.",
            area="sales",
            cadence="weekly",
            audience="reports:read",
            weight=0.35,
            thresholds={"min_change": Threshold(0.06, "Change", "percent", 0.01, 1)},
            templates={
                "prompt": Template(
                    "Why has our average order at {venue} gone {direction} {change} to "
                    "{average}? It was {previous} the 4 weeks before — is it items per order, "
                    "prices, discounts or what people are buying?",
                    {
                        "venue": V,
                        "direction": "up or down",
                        "change": P("how much", "6%"),
                        "average": P("now", "$39.65"),
                        "previous": P("before", "$42.20"),
                    },
                    {
                        "venue": "La Zeppa",
                        "direction": "down",
                        "change": "6%",
                        "average": "$39.65",
                        "previous": "$42.20",
                    },
                )
            },
        ),
        _d(
            name="discount",
            label="Discounts",
            description="The venue's biggest discount when discounts are a real share of sales, or any discount growing fast.",
            area="sales",
            cadence="weekly",
            audience="reports:read",
            weight=1.0,
            thresholds={
                "biggest_share": Threshold(
                    0.04,
                    "Biggest discount: discounts' share of sales at least",
                    "percent",
                    0,
                    1,
                ),
                "biggest_min": Threshold(
                    1000, "Biggest discount: at least (4 weeks)", "dollars", 0, 100_000
                ),
                "growing_factor": Threshold(
                    1.5,
                    "Growing: at least this many times the 4 weeks before",
                    "times",
                    1,
                    10,
                ),
                "growing_min": Threshold(
                    500, "Growing: and up by at least", "dollars", 0, 100_000
                ),
            },
            templates={
                "biggest": Template(
                    "Is {the_discount} at {venue} worth it? It cost {amount} over the last 4 "
                    "weeks ({share} of sales went on discounts in all) — what do those bills "
                    "look like, and when is it used?",
                    {
                        "the_discount": P("the discount", "the First Table discount"),
                        "venue": V,
                        "amount": P("its cost, 4 weeks", "$16,733"),
                        "share": P("all discounts' share of sales", "16%"),
                    },
                    {
                        "the_discount": "the First Table discount",
                        "venue": "La Zeppa",
                        "amount": "$16,733",
                        "share": "16%",
                    },
                    label="The biggest discount",
                ),
                "growing": Template(
                    "Why are {discounts} up at {venue}? They cost {amount} over the last 4 "
                    "weeks, up from {before} the 4 weeks before — what's changed, and is it "
                    "paying for itself?",
                    {
                        "discounts": P("the discount", "Member Deal discounts"),
                        "venue": V,
                        "amount": P("its cost, 4 weeks", "$4,882"),
                        "before": P("the 4 weeks before", "$2,556"),
                    },
                    {
                        "discounts": "Member Deal discounts",
                        "venue": "La Zeppa",
                        "amount": "$4,882",
                        "before": "$2,556",
                    },
                    label="A growing discount",
                ),
            },
        ),
        _d(
            name="budgets_out_of_date",
            label="Budgets out of date",
            description="Budgets beaten (or missed) by a wide margin in most of the last 8 weeks.",
            area="sales",
            cadence="weekly",
            audience="reports:read",
            lift=0.9,
            valued=False,
            thresholds={
                "band": Threshold(
                    0.10, "Beaten or missed by at least", "percent", 0.02, 1
                ),
                "min_weeks": Threshold(
                    6, "In at least this many of 8 weeks", "count", 1, 8
                ),
            },
            templates={
                "prompt": Template(
                    "Are our budgets at {venue} out of date? We've {direction} them by about "
                    "{gap} in {weeks} of the last 8 weeks — can you suggest new weekly "
                    "budgets, day by day, from recent trading?",
                    {
                        "venue": V,
                        "direction": "beaten or missed",
                        "gap": P("by how much", "45%"),
                        "weeks": P("how many weeks", "8"),
                    },
                    {
                        "venue": "Dunedin Social Club",
                        "direction": "beaten",
                        "gap": "45%",
                        "weeks": "8",
                    },
                )
            },
        ),
        # ------------------------------------------------------------ wages
        _d(
            name="quiet_day_wages",
            label="A quiet day's wages",
            description="A weekday whose wages run far above the week's wage share of sales, over the last 4 complete weeks.",
            area="wages",
            cadence="weekly",
            audience="reports:read",
            weight=1.0,
            thresholds={
                "min_gap": Threshold(
                    0.10, "Points above the week's wage share", "percent", 0.01, 1
                ),
                "min_excess": Threshold(
                    150, "And costing at least (a week)", "dollars", 0, 100_000
                ),
            },
            templates={
                "prompt": Template(
                    "How can I bring {day} wages down at {venue}? They've run at {day_share} "
                    "of sales on {day}s over the last 4 weeks, against {week_share} across "
                    "the week — show me who's rostered, the hours, and {day} trade by hour.",
                    {
                        "day": P("the weekday", "Tuesday"),
                        "venue": V,
                        "day_share": P("that day's wages ÷ sales", "104%"),
                        "week_share": P("the week's", "30%"),
                    },
                    {
                        "day": "Tuesday",
                        "venue": "La Zeppa",
                        "day_share": "104%",
                        "week_share": "30%",
                    },
                )
            },
        ),
        _d(
            name="quiet_hours",
            label="Quiet hours",
            description="A stretch of trading hours on one weekday where the staff on don't follow the trade, over the last 4 complete weeks.",
            area="wages",
            cadence="weekly",
            audience="reports:read",
            weight=1.1,
            thresholds={
                "crew": Threshold(2, "Never suggest fewer on than", "count", 1, 10),
                "min_hours": Threshold(3, "Shortest stretch", "hours", 1, 12),
                "max_hours": Threshold(6, "Longest stretch", "hours", 1, 12),
                "min_excess": Threshold(
                    100, "Saving at least (a week)", "dollars", 0, 100_000
                ),
            },
            templates={
                "prompt": Template(
                    "Do we need {people} people on at {venue} on {day}s from {span}? Over the "
                    "last 4 weeks those hours averaged {sales_per_hour} an hour in sales, so "
                    "wages ran at {share} of sales then ({week_share} across the week) — show "
                    "me who's on in those hours and {day} trade hour by hour.",
                    {
                        "people": P("staff on", "4"),
                        "venue": V,
                        "day": P("the weekday", "Tuesday"),
                        "span": P("the hours", "noon to 6pm"),
                        "sales_per_hour": P("sales an hour", "$175"),
                        "share": P("wages ÷ sales then", "73%"),
                        "week_share": P("the week's", "31%"),
                    },
                    {
                        "people": "4",
                        "venue": "Mr Murdochs",
                        "day": "Tuesday",
                        "span": "noon to 6pm",
                        "sales_per_hour": "$175",
                        "share": "73%",
                        "week_share": "31%",
                    },
                )
            },
        ),
        _d(
            name="over_roster",
            label="Paid over roster",
            description="Last week's wages above the roster.",
            area="wages",
            cadence="weekly",
            audience="roster:read",
            weight=1.0,
            thresholds={
                "min_over": Threshold(
                    0.05, "Over the roster by at least", "percent", 0, 1
                ),
                "min_dollars": Threshold(300, "And at least", "dollars", 0, 100_000),
            },
            templates={
                "prompt": Template(
                    "Why did {venue} pay {extra} more in wages than rostered last week? "
                    "{hours} hours were worked against {rostered_hours} rostered — which "
                    "shifts ran over, and who?",
                    {
                        "venue": V,
                        "extra": P("paid over the roster", "$1,658"),
                        "hours": P("hours worked", "413"),
                        "rostered_hours": P("hours rostered", "367"),
                    },
                    {
                        "venue": "La Zeppa",
                        "extra": "$1,658",
                        "hours": "413",
                        "rostered_hours": "367",
                    },
                )
            },
        ),
        _d(
            name="staff_over_roster",
            label="Staff over roster",
            description="Staff working well over their rostered hours in the last 4 weeks. Names people, so only those who can change the roster see it.",
            area="wages",
            cadence="weekly",
            audience="roster:write",
            weight=0.9,
            thresholds={
                "over": Threshold(
                    0.15, "Over their roster by more than", "percent", 0.01, 2
                ),
                "min_rostered_hours": Threshold(
                    20, "Rostered at least", "hours", 0, 500
                ),
                "min_dollars": Threshold(
                    200, "Costing at least", "dollars", 0, 100_000
                ),
            },
            templates={
                "prompt": Template(
                    "Who's working well over their rostered hours at {venue}? {staff} staff "
                    "went more than {over} over in the last 4 weeks ({extra} in all), most of "
                    "all {name} — {hours} hours against {rostered_hours} rostered.",
                    {
                        "venue": V,
                        "staff": P("how many staff", "3"),
                        "over": P("the threshold", "15%"),
                        "extra": P("their extra cost", "$1,507"),
                        "name": P("who most", "Joshua Laird"),
                        "hours": P("their hours", "78"),
                        "rostered_hours": P("rostered", "56"),
                    },
                    {
                        "venue": "La Zeppa",
                        "staff": "3",
                        "over": "15%",
                        "extra": "$1,507",
                        "name": "Joshua Laird",
                        "hours": "78",
                        "rostered_hours": "56",
                    },
                )
            },
        ),
        _d(
            name="wage_rate_up",
            label="Wages up last week",
            description="Last week's wage share of sales against the venue's usual (the median of 8 weeks).",
            area="wages",
            cadence="weekly",
            audience="reports:read",
            weight=1.0,
            thresholds={
                "min_gap": Threshold(0.03, "Points above usual", "percent", 0.005, 1)
            },
            templates={
                "prompt": Template(
                    "Why were wages at {venue} {share} of sales last week when we usually run "
                    "at {usual}? That's {wages} on {sales} — which days and shifts made the "
                    "difference?",
                    {
                        "venue": V,
                        "share": P("last week", "33%"),
                        "usual": P("usually", "29%"),
                        "wages": P("wages", "$14,535"),
                        "sales": P("sales", "$43,794"),
                    },
                    {
                        "venue": "La Zeppa",
                        "share": "33%",
                        "usual": "29%",
                        "wages": "$14,535",
                        "sales": "$43,794",
                    },
                )
            },
        ),
        _d(
            name="roster_ahead",
            label="Next week's roster",
            description="Next week's roster against the sales a normal week brings, while it can still change (Thursday to Sunday).",
            area="wages",
            cadence="roster",
            audience="roster:read",
            weight=1.25,
            thresholds={
                "min_gap": Threshold(0.03, "Points above usual", "percent", 0.005, 1),
                "min_dollars": Threshold(300, "And at least", "dollars", 0, 100_000),
            },
            templates={
                "prompt": Template(
                    "Is next week's roster at {venue} too heavy? It costs {cost} — {share} of "
                    "what a normal week takes, when we usually run wages at {usual}. Where "
                    "could it come down, day by day?",
                    {
                        "venue": V,
                        "cost": P("the roster's cost", "$15,200"),
                        "share": P("of a normal week's sales", "36%"),
                        "usual": P("usually", "30%"),
                    },
                    {
                        "venue": "La Zeppa",
                        "cost": "$15,200",
                        "share": "36%",
                        "usual": "30%",
                    },
                )
            },
        ),
        # ------------------------------------------------------------ cost of goods
        _d(
            name="cogs_trend",
            label="Cost of goods trend",
            description="Purchases ÷ sales over the last 4 weeks against the 8 weeks before.",
            area="cogs",
            cadence="weekly",
            audience="orders:read",
            weight=1.1,
            thresholds={"min_gap": Threshold(0.03, "Points up", "percent", 0.005, 1)},
            templates={
                "prompt": Template(
                    "Why has cost of goods at {venue} gone up to {recent}? It was {before} over "
                    "the 8 weeks before (what we bought against what we sold) — which "
                    "suppliers and categories are behind it?",
                    {
                        "venue": V,
                        "recent": P("the last 4 weeks", "39%"),
                        "before": P("the 8 weeks before", "30%"),
                    },
                    {"venue": "Somewhere North", "recent": "39%", "before": "30%"},
                )
            },
        ),
        _d(
            name="price_rise",
            label="Supplier price rise",
            description="A supplier's price for one item, in the same invoiced unit, stepping up and staying there for the next two deliveries.",
            area="cogs",
            cadence="weekly",
            audience="orders:read",
            weight=1.4,
            thresholds={
                "min_step": Threshold(0.05, "A step of at least", "percent", 0.01, 1),
                "max_step": Threshold(
                    0.8, "Ignore steps over (unit errors)", "percent", 0.1, 5
                ),
                "min_yearly": Threshold(
                    300, "Costing at least (a year)", "dollars", 0, 1_000_000
                ),
                "weeks": Threshold(8, "Within the last", "count", 1, 13),
            },
            templates={
                "prompt": Template(
                    "{supplier} put {item} up {change} at {venue} — who sells it cheaper? It "
                    "went from {old_price} to {new_price}{per_unit} on {date}, about {yearly} "
                    "a year at our volumes.",
                    {
                        "supplier": P("the supplier", "Neat Meats"),
                        "item": P("the item", "Bavette Speckle Park"),
                        "change": P("the rise", "21%"),
                        "venue": V,
                        "old_price": P("before", "$32.95"),
                        "new_price": P("after", "$39.95"),
                        "per_unit": P("the unit, with a leading space", " a kilo"),
                        "date": P("when", "02 Sep"),
                        "yearly": P("a year at current volumes", "$4,138"),
                    },
                    {
                        "supplier": "Neat Meats",
                        "item": "Bavette Speckle Park",
                        "change": "21%",
                        "venue": "La Zeppa",
                        "old_price": "$32.95",
                        "new_price": "$39.95",
                        "per_unit": " a kilo",
                        "date": "02 Sep",
                        "yearly": "$4,138",
                    },
                )
            },
        ),
        _d(
            name="group_price_gap",
            label="Sister venue pays less",
            description="The same item, in the same unit, costing one venue more than a sister venue in the same organisation.",
            area="cogs",
            cadence="weekly",
            audience="orders:read",
            weight=1.4,
            thresholds={
                "min_gap": Threshold(
                    0.08, "Paying more by at least", "percent", 0.01, 1
                ),
                "max_gap": Threshold(
                    1.0, "Ignore gaps over (unit errors)", "percent", 0.1, 10
                ),
                "min_yearly": Threshold(
                    500, "Worth at least (a year)", "dollars", 0, 1_000_000
                ),
            },
            templates={
                "prompt": Template(
                    "Why does {venue} pay {gap} more than {cheaper_venue} for {item}? We pay "
                    "{our_price} from {our_supplier}; {cheaper_venue} pays {their_price} from "
                    "{their_supplier} — about {yearly} a year. Should we switch, or ask for "
                    "the better price?",
                    {
                        "venue": V,
                        "gap": P("how much more", "30%"),
                        "cheaper_venue": P("the sister venue", "Dunedin Social Club"),
                        "item": P("the item", "Oil Olive Extra Virgin"),
                        "our_price": P("our price", "$26.27/L"),
                        "our_supplier": P("our supplier", "Bidfood"),
                        "their_price": P("their price", "$20.16/L"),
                        "their_supplier": P(
                            "their supplier, or 'the same supplier'",
                            "the same supplier",
                        ),
                        "yearly": P("a year", "$1,123"),
                    },
                    {
                        "venue": "La Zeppa",
                        "gap": "30%",
                        "cheaper_venue": "Dunedin Social Club",
                        "item": "Oil Olive Extra Virgin",
                        "our_price": "$26.27/L",
                        "our_supplier": "Bidfood",
                        "their_price": "$20.16/L",
                        "their_supplier": "the same supplier",
                        "yearly": "$1,123",
                    },
                )
            },
        ),
        # ------------------------------------------------------------ the loop
        _d(
            name="follow_up",
            label="Did it work?",
            description="A few weeks after someone told Norm they'd act on an insight, whether it worked. Shows in the same chats as the insight it follows.",
            area="wages",
            cadence="follow-up",
            audience="reports:read",
            weight=1.3,
            thresholds={"after_days": Threshold(21, "Follow up after", "days", 7, 90)},
            templates={
                "prompt": Template(
                    "Has {change} worked at {venue}? {about} — it {result}. Show me how it's "
                    "moved week by week since, and what's left to do.",
                    {
                        "change": P(
                            "what they did", "cutting the Tuesday roster to three"
                        ),
                        "venue": V,
                        "about": P(
                            "the original insight",
                            "Tuesdays at La Zeppa run wages at 104% of sales",
                        ),
                        "result": P(
                            "what changed", "was about $1,171 a week, now $380"
                        ),
                    },
                    {
                        "change": "cutting the Tuesday roster to three",
                        "venue": "La Zeppa",
                        "about": "Tuesdays at La Zeppa run wages at 104% of sales",
                        "result": "was about $1,171 a week, now $380",
                    },
                )
            },
        ),
    )
}


# ------------------------------------------------------------------ templates
def placeholders_in(text: str) -> list[str]:
    """The named placeholders a template uses. Raises ValueError for anything
    but plain {name}: positional {}, {0}, attribute or index access, a
    conversion (!r) or a format spec (:.2f), or unbalanced braces."""
    names = []
    for _literal, name, spec, conv in Formatter().parse(text):
        if name is None:
            continue
        if not name or not name.isidentifier():
            raise ValueError(
                f"{{{name}}} isn't a placeholder — use a name like {{venue}}"
            )
        if spec or conv:
            raise ValueError(f"{{{name}}} can't carry a format — write just {{{name}}}")
        names.append(name)
    return names


def check_template(text: str, allowed: dict[str, str] | set[str]) -> list[str]:
    """Problems with a template, as a person can read them (empty = fine)."""
    if not isinstance(text, str) or not text.strip():
        return ["The template is empty."]
    if len(text) > 1000:
        return ["The template is over 1,000 characters."]
    try:
        used = placeholders_in(text)
    except ValueError as exc:
        return [str(exc)]
    unknown = sorted({n for n in used if n not in allowed})
    if unknown:
        return [
            "Unknown placeholder"
            + ("s " if len(unknown) > 1 else " ")
            + ", ".join("{" + n + "}" for n in unknown)
            + ". This one can use: "
            + ", ".join("{" + n + "}" for n in allowed)
        ]
    return []


def render_text(text: str, facts: dict) -> str:
    """Fill a checked template. Raises on anything a check would refuse."""
    placeholders_in(text)
    return text.format_map({k: str(v) for k, v in facts.items()})


# ------------------------------------------------------------------ effective values
def _overrides(rules: dict | None, detector: str) -> dict:
    return ((rules or {}).get(detector) or {}) if isinstance(rules, dict) else {}


def threshold(rules: dict | None, detector: str, name: str) -> float:
    """The effective threshold: the admin's value, else the default."""
    spec = DETECTORS[detector].thresholds[name]
    value = (_overrides(rules, detector).get("thresholds") or {}).get(name)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if spec.minimum <= value <= spec.maximum:
            return float(value)
    return float(spec.default)


def render(rules: dict | None, detector: str, template: str, facts: dict) -> str:
    """The detector's prompt: the admin's template filled in, else (or if it
    fails) the default. The facts are kept on the finding so a new template
    can be previewed against real insights."""
    spec = DETECTORS[detector].templates[template]
    custom = (_overrides(rules, detector).get("templates") or {}).get(template)
    if isinstance(custom, str) and custom.strip():
        try:
            if not check_template(custom, spec.placeholders):
                return render_text(custom, facts)
        except (KeyError, ValueError, IndexError):
            logger.warning(
                "insight template %s.%s failed; using the default", detector, template
            )
    return render_text(spec.default, facts)
