"""One merged answer across venues for get_received_items_for_period.

"Top 50 fruit & veg items across the group" used to be twelve per-venue
results (6 venues x Fruit/Vegetables, 11-14k chars each) that the model ranked
by eye — most of a 72k-token request and two 11.7k-token answers (prod thread
01688f94, 23 Sep 2026). `venues` ("all" or a list) now fans out inside the
consolidator and merges in code.
"""

from tests.test_received_items_consolidator import FUNCTION_CODE, invoice, line
from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

WINDOW = {
    "start": "2026-04-01T07:00:00+13:00",
    "end": "2026-07-01T06:59:59+12:00",
    "trading_aligned": True,
    "description": "April - June 2026",
}
KG = {"name": "1 KG", "ratio": 1.0, "stockUnitType": "Weight"}
TEN_KG = {"name": "10 KG", "ratio": 10.0, "stockUnitType": "Weight"}

# Each venue's Loaded has its OWN item ids for the same product.
VENUES = {
    "Bessie & Engineers": {
        "items": [
            {"id": "b-potato", "name": "POTATOES AGRIA", "groupName": "Vegetables"},
            {"id": "b-lemon", "name": "LEMONS", "groupName": "Fruit"},
        ],
        "invoices": [
            invoice(
                "b1",
                "City Produce",
                "2026-04-10",
                [
                    line("b-potato", "10 KG", 10.0, 2, 30.0, category="Food"),
                    line("b-lemon", "1 KG", 1.0, 5, 6.0, category="Food"),
                ],
            )
        ],
    },
    "The Glass Goose": {
        "items": [
            {"id": "g-potato", "name": "Potatoes Agria", "groupName": "Vegetables"},
        ],
        "invoices": [
            invoice(
                "g1",
                "City Produce",
                "2026-05-02",
                [line("g-potato", "1 KG", 1.0, 5, 3.5, category="Food")],
            )
        ],
    },
}


class Api:
    def __init__(self, venues=VENUES, failing=()):
        self.venues = venues
        self.failing = set(failing)
        self.calls = []

    def call_api(self, connector, action, params=None):
        params = params or {}
        venue = params.get("venue")
        self.calls.append((action, venue))
        if action == "resolve_dates":
            return {"window": WINDOW}
        if action == "list_venues":
            return {
                "connector": "loadedhub",
                "venues": [{"name": n, "connected": True} for n in self.venues],
            }
        data = self.venues[venue]
        if action == "get_received_invoices":
            if venue in self.failing:
                return {"error": "Loaded timed out"}
            return data["invoices"]
        if action == "get_stock_items_raw":
            return data["items"]
        if action == "get_stock_units":
            return [KG, TEN_KG]
        raise AssertionError(f"unexpected {connector}.{action}")


def run(api, **params):
    namespace = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(FUNCTION_CODE, namespace)
    return namespace["run"](
        {"period": "April to June 2026", **params}, api.call_api, lambda m: None
    )


class TestOneMergedRanking:
    def test_the_same_product_merges_across_venues_despite_different_ids(self):
        out = run(Api(), venues="all")
        potatoes = next(r for r in out["rows"] if "potato" in r["item_name"].lower())
        # 2 x 10kg at $30 (B&E) + 5 x 1kg at $3.50 (Glass Goose)
        assert potatoes["spend"] == 77.5
        assert potatoes["quantity_base"] == 25.0  # one unit: kg
        assert potatoes["base_unit"] == "kg"
        assert [v["venue"] for v in potatoes["venues"]] == [
            "Bessie & Engineers",
            "The Glass Goose",
        ]
        assert "item_id" not in potatoes  # an id belongs to ONE venue

    def test_rows_are_ranked_by_spend_across_the_group(self):
        out = run(Api(), venues="all")
        assert [r["spend"] for r in out["rows"]] == [77.5, 30.0]
        assert out["summary"]["net_spend"] == 107.5

    def test_every_venue_is_fetched_once_per_read(self):
        api = Api()
        run(api, venues="all")
        invoice_calls = [v for a, v in api.calls if a == "get_received_invoices"]
        assert sorted(invoice_calls) == sorted(VENUES)

    def test_per_venue_totals_come_back_with_the_group_answer(self):
        out = run(Api(), venues="all")
        assert out["venues"] == [
            {"venue": "Bessie & Engineers", "net_spend": 90.0, "lines": 2},
            {"venue": "The Glass Goose", "net_spend": 17.5, "lines": 1},
        ]

    def test_group_rollup_carries_a_venue_split(self):
        out = run(Api(), venues="all", group_by="group")
        veg = next(r for r in out["rows"] if r["group"] == "Vegetables")
        assert veg["spend"] == 77.5
        assert {v["venue"] for v in veg["venues"]} == set(VENUES)

    def test_an_explicit_list_of_venues(self):
        out = run(Api(), venues=["The Glass Goose"])
        assert out["summary"]["net_spend"] == 17.5
        assert "venues" not in out  # one venue: the unmerged single-venue shape


class TestAFailingVenueIsFlaggedNotHidden:
    def test_it_is_excluded_and_named(self):
        out = run(Api(failing={"The Glass Goose"}), venues="all")
        assert out["summary"]["net_spend"] == 90.0
        assert out["venue_errors"] == {"The Glass Goose": "Loaded timed out"}
        assert any("excluded from every total" in w for w in out["warnings"])

    def test_every_venue_failing_is_an_error(self):
        out = run(Api(failing=set(VENUES)), venues="all")
        assert "every venue failed" in out["error"]
