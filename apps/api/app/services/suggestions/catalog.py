"""The suggested prompts Home can offer (Oct 2026).

Drafted by Haiku from each App's real tools and skills
(``scripts/draft_suggestion_prompts.py``), then reviewed: a prompt stays only
if the tools its App claims can answer it — no customer lists, no social
attribution, no training records Norm can't read. ``tests/test_suggestions``
checks every entry's App and tools against the reviewed catalog seed.

An entry is shown only when its member is on the team and its App is on, the
person holds its permission, and — if it names one — its connection works for
the venue. ``days`` and ``part`` are judged on the venue's own clock.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    id: str
    member: str  # the team member whose new chat shows it (and its icon)
    app: str  # the catalog App whose tools answer it
    text: str  # {venue} is the person's venue
    tools: tuple[str, ...] = ()  # used to tell a regular from something new
    kind: str = "regular"  # regular | discover
    permission: str | None = None
    connection: str | None = "loadedhub"
    days: tuple[int, ...] | None = None  # 0 = Monday
    part: str | None = None  # morning | afternoon | evening
    month_start: bool = False  # the first week of the month
    multi_venue: bool = False
    weight: float = 1.0


SALES = ("loadedhub.get_sales",)
LABOUR = ("loadedhub.get_labour",)

CATALOG: tuple[Entry, ...] = (
    # ---------------------------------------------------------------- Reports
    Entry(
        "reports.last_week_budget",
        "reports",
        "loaded-reports",
        "How did last week go at {venue} against budget?",
        SALES + ("loadedhub.get_budgets",),
        permission="reports:read",
        days=(0, 1),
        weight=1.3,
    ),
    Entry(
        "reports.yesterday",
        "reports",
        "loaded-reports",
        "How did yesterday go at {venue}?",
        SALES,
        permission="reports:read",
        part="morning",
    ),
    Entry(
        "reports.weekend_vs_last_year",
        "reports",
        "loaded-reports",
        "How did the weekend compare with last year at {venue}?",
        SALES,
        permission="reports:read",
        days=(0,),
    ),
    Entry(
        "reports.wages_last_week",
        "reports",
        "loaded-reports",
        "What were wages as a percentage of sales at {venue} last week?",
        SALES + LABOUR,
        permission="reports:read",
        days=(0, 1, 2),
        weight=1.2,
    ),
    Entry(
        "reports.top_products",
        "reports",
        "loaded-reports",
        "What sold best at {venue} last week?",
        SALES,
        kind="discover",
        permission="reports:read",
        days=(0, 1, 2),
    ),
    Entry(
        "reports.staff_sales",
        "reports",
        "loaded-reports",
        "Who sold the most at {venue} last week?",
        SALES,
        kind="discover",
        permission="reports:read",
        days=(0, 1),
    ),
    Entry(
        "reports.day_parts",
        "reports",
        "loaded-reports",
        "How do lunch and dinner compare at {venue} this month?",
        SALES,
        kind="discover",
        permission="reports:read",
    ),
    Entry(
        "reports.group_last_week",
        "reports",
        "loaded-reports",
        "Compare last week's sales across my venues",
        SALES,
        permission="reports:read",
        days=(0, 1),
        multi_venue=True,
        weight=1.2,
    ),
    Entry(
        "reports.cogs_last_month",
        "reports",
        "loaded-reports",
        "What was cost of goods at {venue} last month?",
        ("loadedhub.get_cogs_detail_for_period",),
        permission="reports:read",
        month_start=True,
    ),
    # ------------------------------------------------------------ Procurement
    Entry(
        "procurement.receive_invoices",
        "procurement",
        "loaded-stock",
        "Review this week's supplier invoices at {venue}",
        ("loadedhub.review_and_receive_invoices",),
        permission="orders:read",
    ),
    Entry(
        "procurement.what_to_order",
        "procurement",
        "loaded-stock",
        "What do I need to order for {venue} this week?",
        ("loadedhub.get_stock", "loadedhub.calculate_template_stock_requirements"),
        permission="orders:read",
        days=(0, 1, 2, 3),
        part="morning",
    ),
    Entry(
        "procurement.price_rises",
        "procurement",
        "loaded-stock",
        "Which supplier prices have gone up at {venue} lately?",
        ("loadedhub.get_received_items_for_period",),
        kind="discover",
        permission="orders:read",
        weight=1.2,
    ),
    Entry(
        "procurement.supplier_spend",
        "procurement",
        "loaded-stock",
        "What did we spend with each supplier last month at {venue}?",
        ("loadedhub.get_received_items_for_period",),
        permission="orders:read",
        month_start=True,
    ),
    Entry(
        "procurement.cogs_trend",
        "procurement",
        "loaded-stock",
        "What's our cost of goods at {venue} over the last 4 weeks?",
        ("loadedhub.get_purchase_cogs",),
        permission="orders:read",
    ),
    Entry(
        "procurement.reconcile",
        "procurement",
        "loaded-stock",
        "Check last week's invoices at {venue} against the orders",
        ("loadedhub.reconcile_received_invoices",),
        permission="orders:read",
        days=(0, 1),
    ),
    Entry(
        "procurement.stocktake_variance",
        "procurement",
        "loaded-stock",
        "Where did the last stocktake variance come from at {venue}?",
        ("loadedhub.get_stocktakes",),
        kind="discover",
        permission="orders:read",
    ),
    # ------------------------------------------------------ Time & Attendance
    Entry(
        "time.next_week_roster",
        "time_attendance",
        "loaded-reports",
        "What does next week's roster at {venue} cost against usual sales?",
        LABOUR + SALES,
        permission="roster:read",
        days=(3, 4, 5, 6),
        weight=1.3,
    ),
    Entry(
        "time.on_tonight",
        "time_attendance",
        "loaded-time",
        "Who's working at {venue} tonight?",
        ("norm.show_roster",),
        permission="roster:read",
        part="afternoon",
    ),
    Entry(
        "time.over_roster",
        "time_attendance",
        "loaded-reports",
        "Who worked over their rostered hours at {venue} last week?",
        LABOUR,
        permission="roster:read",
        days=(0, 1),
    ),
    Entry(
        "time.no_shows",
        "time_attendance",
        "loaded-reports",
        "Did anyone miss a shift at {venue} this week?",
        LABOUR,
        kind="discover",
        permission="roster:read",
    ),
    # --------------------------------------------------------- Executive Chef
    Entry(
        "chef.margins",
        "executive_chef",
        "loaded-kitchen",
        "Which dishes at {venue} make the least margin?",
        ("loadedhub.get_recipes", "loadedhub.get_menus"),
    ),
    Entry(
        "chef.recipe_from_photo",
        "executive_chef",
        "loaded-kitchen",
        "Turn a recipe card or photo into a costed recipe",
        ("loadedhub.draft_recipe",),
        kind="discover",
    ),
    Entry(
        "chef.menu_costs",
        "executive_chef",
        "loaded-kitchen",
        "What does each dish on the {venue} menu cost to make?",
        ("loadedhub.get_menus",),
        kind="discover",
    ),
    # --------------------------------------------------------------------- HR
    Entry(
        "hr.candidates",
        "hr",
        "bamboohr-app",
        "Which candidates are waiting to hear back from us?",
        ("bamboohr.get_hr",),
        permission="hr:read",
        connection="bamboohr",
    ),
    # -------------------------------------------------------------- Marketing
    Entry(
        "marketing.weekend_email",
        "marketing",
        "brevo-app",
        "Draft an email to our customers about this weekend at {venue}",
        (),
        kind="discover",
        connection="brevo",
        days=(2, 3, 4),
    ),
    # ------------------------------------------------------------ App Builder
    Entry(
        "apps.closing_checklist",
        "app_builder",
        "app-builder",
        "Build a closing checklist app for {venue}",
        ("norm.save_app",),
        kind="discover",
        permission="apps:build",
        connection=None,
    ),
    Entry(
        "apps.waste_log",
        "app_builder",
        "app-builder",
        "Build an app to log daily waste at {venue}",
        ("norm.save_app",),
        kind="discover",
        permission="apps:build",
        connection=None,
    ),
    # ------------------------------------------------------------------- Norm
    Entry(
        "norm.what_can_you_do",
        "norm",
        "norm-core",
        "What can my team help me with?",
        (),
        kind="discover",
        connection=None,
        weight=0.3,
    ),
)

BY_ID = {e.id: e for e in CATALOG}
