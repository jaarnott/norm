"""get_stocktakes — the stocktake list and the same-template variance report.

Exec'd under the REAL sandbox namespace against the canonical file. Fixtures
are shaped like the live payloads (Sep 2026): Orbit's stock_find_stocktakes
reply as the executor returns it ({"success", "data": {stocktakes, templates}}),
and Loaded's stocktake report ({totals..., totalVariance, lines: [{groupHeader,
stockItemName, unit: {…}, startQty, endQty, qtyReceived, expectedUsed,
hasPosLinks, unitCost}]}, with item-less group-header rows).

Facts pinned, each from production:
- the pair is two counts of the SAME template, chosen here: La Zeppa's
  23/06 Beverage -> 24/06 Food pair produced a report Loaded served without
  complaint — 548 items, none counted at both ends, COGS at 116x sales;
- ad-hoc counts are never paired;
- per-item variance = closing − (opening + received − expected use) × cost,
  which sums to Loaded's own totalVariance (287.7278 on La Zeppa Food
  11/08 -> 17/08);
- items with no POS link are usage, not variance, and are reported apart.
"""

import copy
import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
CODE = (_DIR / "get_stocktakes.py").read_text()

ZERO = "00000000-0000-0000-0000-000000000000"
FOOD = "665a5818-b76e-48e4-a121-494af4c8edf3"
BEV = "a134765a-241f-4cbc-a319-0a6c28a4b387"
KITCHEN = "5ca78a1f-a8ce-4e0a-a218-a02c691cc36b"
STAFF = "99999999-0000-0000-0000-000000000001"


def _st(sid, title, completed, tid, pending=False):
    return {
        "id": sid,
        "title": title,
        "datestampCompleted": None if pending else completed,
        "dateStampCreated": completed,
        "templateId": tid,
        "pending": pending,
    }


STOCKTAKES = [
    _st(
        "p-1",
        "19 September 2026 - Ad-hoc count",
        "2026-09-19T01:46:15+00:00",
        ZERO,
        pending=True,
    ),
    _st("adhoc-1", "25 August 2026 - Ad-hoc count", "2026-08-24T21:41:00+00:00", ZERO),
    _st("bev-18", "18/08/2026 - All Beverage", "2026-08-17T23:50:00+00:00", BEV),
    _st("food-17", "17/08/2026 - All Food", "2026-08-17T00:20:00+00:00", FOOD),
    _st("food-11", "11/08/2026 - All Food", "2026-08-11T00:26:00+00:00", FOOD),
    _st("food-04", "4/08/2026 - All Food", "2026-08-04T12:03:00+12:00", FOOD),
    _st(
        "kit-11",
        "11/07/2026 - DAILY STOCKTAKE - KITCHEN",
        "2026-07-11T21:10:44+12:00",
        KITCHEN,
    ),
    _st("food-29", "29/07/2026 - All Food", "2026-07-29T12:38:40+12:00", FOOD),
    _st("staff-1", "1/08/2026 - Staff Food", "2026-08-01T10:00:00+12:00", STAFF),
]
TEMPLATES = [
    {"id": BEV, "title": "All Beverage", "datestampDeleted": None},
    {"id": FOOD, "title": "All Food", "datestampDeleted": None},
    {"id": "bd216723", "title": "All Other Stock", "datestampDeleted": None},
]


def _unit(name, ratio=1.0):
    return {
        "id": "u-" + name,
        "unitType": 1,
        "ratio": ratio,
        "name": name,
        "datestampDeleted": None,
    }


def _line(name, group, unit, o, c, r, exp, cost, linked=True):
    return {
        "groupHeader": group,
        "stockItemId": "i-" + name,
        "stockItemName": name,
        "unit": _unit(unit),
        "startQty": o,
        "endQty": c,
        "qtyReceived": r,
        "expectedUsed": exp,
        "hasPosLinks": linked,
        "unitCost": cost,
    }


def _header(group):
    return {
        "groupHeader": group,
        "stockItemId": ZERO,
        "stockItemName": None,
        "unit": None,
        "startQty": 0,
        "endQty": 0,
        "qtyReceived": 0,
        "expectedUsed": 0,
        "hasPosLinks": False,
        "unitCost": 0,
    }


# As Loaded sends it: the group name is ONLY on the header row; the items under
# it carry groupHeader null (all 264 items in La Zeppa's real Food report).
LINES = [
    _header("Meats"),
    # counted 10, received 5, expected to use 8 -> expect 7 left; counted 4: short 3
    _line("BEEF RUMP", None, "Kilo", 10, 4, 5, 8, 30.0),
    # expect 6 - 2 + 0 = 4 left; counted 5: surplus 1
    _line("CHICKEN THIGH", None, "Kilo", 6, 5, 0, 2, 12.0),
    _header("Bakery"),
    # exact: no variance
    _line("BREAD LOAF", None, "850 Grams", 2, 1, 3, 4, 10.0),
    _header("Consumables"),
    # no POS link: used 3, expected 0 -> would read as a -3 "variance"
    _line("GLOVES", None, "Box", 5, 2, 0, 0, 7.0, linked=False),
    _header("Dry"),
    # only counted at the close
    _line("OIL", None, "Litre", 0, 3, 5, 1, 4.0),
]


def _report(lines=LINES):
    total = sum(
        (ln["endQty"] - (ln["startQty"] + ln["qtyReceived"] - ln["expectedUsed"]))
        * ln["unitCost"]
        for ln in lines
        if ln["stockItemName"]
    )
    return {
        "startDate": "2026-08-11T00:26:00+00:00",
        "endDate": "2026-08-17T00:20:00+00:00",
        "type": "Food",
        "totalSales": 8396.54,
        "totalSalesEx": 7210.3,
        "openingValue": 7170.17,
        "totalPurchases": 2500.82,
        "closingValue": 7490.28,
        "totalCostOfGoods": 2180.71,
        "totalCostOfGoodsPerc": 0.2597,
        "totalVariance": total,
        "lines": copy.deepcopy(lines),
    }


class Api:
    def __init__(
        self,
        stocktakes=STOCKTAKES,
        report=None,
        report_failures=0,
        window=None,
        orbit_error=None,
    ):
        self.calls = []
        self.stocktakes = stocktakes
        self.report = report if report is not None else _report()
        self.report_failures = report_failures
        self.window = window
        self.orbit_error = orbit_error

    def call_api(self, connector, action, params=None):
        p = dict(params or {})
        self.calls.append((action, p))
        if action == "stock_find_stocktakes":
            if self.orbit_error:
                return {"success": False, "error": self.orbit_error}
            return {
                "success": True,
                "data": {
                    "venue_id": "v",
                    "stocktakes": copy.deepcopy(self.stocktakes),
                    "templates": TEMPLATES,
                },
            }
        if action == "generate_stocktake_report":
            if self.report_failures:
                self.report_failures -= 1
                return {"error": "HTTP 500 timeout"}
            return copy.deepcopy(self.report)
        if action == "resolve_dates":
            return {"window": self.window}
        raise AssertionError(f"unexpected action {action}")

    def report_args(self):
        return [p for a, p in self.calls if a == "generate_stocktake_report"]


def run(api, **params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(CODE, ns)
    return ns["run"]({"venue": "La Zeppa", **params}, api.call_api, lambda m: None)


class TestList:
    def test_default_view_is_the_list_newest_first_with_pending_on_top(self):
        api = Api()
        out = run(api)
        assert out["view"] == "list"
        assert [s["id"] for s in out["stocktakes"][:4]] == [
            "p-1",
            "adhoc-1",
            "bev-18",
            "food-17",
        ]
        assert out["completed_by_template"]["All Food"] == 4
        assert (
            out["completed_by_template"]["DAILY STOCKTAKE - KITCHEN"] == 1
        )  # recovered from its title
        assert "template_id" not in out["stocktakes"][0]
        find = api.calls[0][1]
        assert find["include_templates"] is True and find["limit"] == 200

    def test_list_for_one_template(self):
        out = run(Api(), view="list", template="food")
        assert [s["id"] for s in out["stocktakes"]] == [
            "food-17",
            "food-11",
            "food-04",
            "food-29",
        ]


class TestPairing:
    def test_template_word_pairs_the_two_latest_counts_of_that_template(self):
        api = Api()
        out = run(api, template="Food")
        assert out["view"] == "variance"
        # the Beverage count and the ad-hoc count in between are skipped
        assert api.report_args() == [
            {
                "venue": "La Zeppa",
                "start_stocktake_id": "food-11",
                "end_stocktake_id": "food-17",
            }
        ]
        assert out["opening"]["title"] == "11/08/2026 - All Food"
        assert out["template"] == "All Food"
        assert out["days"] == 6.0

    def test_food_never_quietly_becomes_staff_food(self):
        api = Api()
        run(api, template="food")
        assert api.report_args()[0]["end_stocktake_id"] == "food-17"

    def test_a_named_non_category_template_works(self):
        api = Api(
            stocktakes=STOCKTAKES
            + [
                _st(
                    "kit-12",
                    "12/07/2026 - DAILY STOCKTAKE - KITCHEN",
                    "2026-07-12T21:00:00+12:00",
                    KITCHEN,
                )
            ]
        )
        run(api, template="daily stocktake - kitchen")
        assert api.report_args()[0] == {
            "venue": "La Zeppa",
            "start_stocktake_id": "kit-11",
            "end_stocktake_id": "kit-12",
        }

    def test_ids_of_different_templates_are_refused_before_the_report(self):
        api = Api()
        out = run(api, opening_id="bev-18", closing_id="food-17")
        assert "different templates" in out["error"] and "All Beverage" in out["error"]
        assert api.report_args() == []

    def test_ad_hoc_counts_are_never_paired(self):
        api = Api()
        assert "can't be paired" in run(api, template="ad-hoc")["error"]
        assert (
            "can't be paired"
            in run(api, opening_id="adhoc-1", closing_id="p-1")["error"]
            or api.report_args() == []
        )
        assert api.report_args() == []

    def test_reversed_ids_are_put_in_order(self):
        api = Api()
        run(api, opening_id="food-17", closing_id="food-11")
        assert api.report_args()[0]["start_stocktake_id"] == "food-11"

    def test_one_id_alone_is_refused(self):
        assert (
            "both opening_id and closing_id"
            in run(Api(), opening_id="food-11")["error"]
        )

    def test_fewer_than_two_counts_lists_what_exists(self):
        out = run(Api(), template="all beverage")
        assert "Need two completed 'All Beverage' counts" in out["error"]
        assert "18/08/2026 - All Beverage" in out["error"]

    def test_unknown_template_names_the_choices(self):
        out = run(Api(), template="cellar")
        assert (
            "No stocktake template 'cellar'" in out["error"]
            and "All Food" in out["error"]
        )

    def test_a_period_opens_on_the_last_count_before_it_and_closes_inside_it(self):
        api = Api(
            window={
                "start": "2026-08-05T07:00:00+12:00",
                "end": "2026-08-12T06:59:59+12:00",
            }
        )
        run(api, template="Food", period="last week")
        assert api.report_args()[0]["start_stocktake_id"] == "food-04"
        assert api.report_args()[0]["end_stocktake_id"] == "food-11"
        find = [p for a, p in api.calls if a == "stock_find_stocktakes"][0]
        assert (
            find["from"] == "2026-04-07" and find["to"] == "2026-08-12"
        )  # reaches back for the opener

    def test_unresolvable_period_is_an_error(self):
        assert (
            "could not resolve"
            in run(Api(window=None), template="Food", period="whenever")["error"]
        )


class TestVarianceMath:
    def test_totals_come_from_loaded_and_variance_sums_to_its_total(self):
        out = run(Api(), template="Food")
        t = out["totals"]
        # (4-7)*30 + (5-4)*12 + 0 + (2-5)*7 + (3-4)*4 = -90 + 12 - 21 - 4 = -103
        assert t["total_variance"] == -103.0
        assert t["shortfall_value"] == -94.0  # linked only: rump -90, oil -4
        assert t["surplus_value"] == 12.0
        assert t["cogs_pct"] == 26.0 and t["sales_ex_tax"] == 7210.3

    def test_rows_rank_by_value_and_carry_names_not_objects(self):
        out = run(Api(), template="Food")
        v = out["variances"]
        assert [r["item"] for r in v] == ["BEEF RUMP", "CHICKEN THIGH", "OIL"]
        assert v[0] == {
            "item": "BEEF RUMP",
            "group": "Meats",
            "unit": "Kilo",
            "opening": 10.0,
            "received": 5.0,
            "closing": 4.0,
            "expected_use": 8.0,
            "actual_use": 11.0,
            "variance_qty": -3.0,
            "variance_value": -90.0,
        }

    def test_unlinked_items_are_usage_not_variance(self):
        out = run(Api(), template="Food")
        assert "GLOVES" not in [r["item"] for r in out["variances"]]
        nl = out["not_pos_linked"]
        assert nl["count"] == 1 and nl["top"][0] == {
            "item": "GLOVES",
            "group": "Consumables",
            "unit": "Box",
            "actual_use": 3.0,
            "usage_value": 21.0,
        }

    def test_coverage_and_group_headers(self):
        out = run(Api(), template="Food")
        assert out["coverage"] == {
            "items": 5,
            "counted_both_ends": 4,
            "only_in_opening": 0,
            "only_in_closing": 1,
            "not_pos_linked": 1,
        }

    def test_top_rolls_the_rest_into_others(self):
        out = run(Api(), template="Food", top=1)
        assert len(out["variances"]) == 1 and out["others"] == {
            "count": 2,
            "variance_value": 8.0,
        }

    def test_group_and_query_filter_the_rows(self):
        assert [
            r["item"] for r in run(Api(), template="Food", group="meat")["variances"]
        ] == ["BEEF RUMP", "CHICKEN THIGH"]
        assert [
            r["item"] for r in run(Api(), template="Food", query="oil")["variances"]
        ] == ["OIL"]

    def test_a_pair_with_little_overlap_is_warned(self):
        lines = (
            [_header("X")]
            + [_line(f"A{i}", "X", "Each", 5, 0, 0, 1, 1.0) for i in range(5)]
            + [_line(f"B{i}", "X", "Each", 0, 5, 0, 0, 1.0) for i in range(5)]
        )
        out = run(Api(report=_report(lines)), template="Food")
        assert "counted at both ends" in out["warning"]


class TestFailures:
    def test_a_slow_report_is_retried_once(self):
        api = Api(report_failures=1)
        out = run(api, template="Food")
        assert "variances" in out and len(api.report_args()) == 2

    def test_a_loaded_failure_says_so(self):
        out = run(Api(report_failures=2), template="Food")
        assert "LoadedHub failure" in out["error"]

    def test_an_orbit_failure_says_so(self):
        out = run(Api(orbit_error="401 unauthorized"), template="Food")
        assert "could not list stocktakes" in out["error"] and "401" in out["error"]

    def test_bad_view_refused_without_calls(self):
        api = Api()
        assert "view must be one of" in run(api, view="summary")["error"]
        assert api.calls == []
