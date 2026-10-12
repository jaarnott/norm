"""Every blocker Norm raises points at a fix the card's Accept applies.

The card shows each thing Norm noticed as one row with one Accept. Before
this, rows with nothing to accept reached it: a unit blocker reading "no unit
could be read from the copy — set one" on a line whose unit the resolver HAD
answered ('Freight' → Each, high), totals that didn't add up with only "receive
on the values as read", "no order linked" with only "receive without one".

Since 12 Oct 2026 every answer is IN the Replica (``replica_finish``), the
card shows the Replica's differences from Loaded as suggestions, and a blocker
is never a decision of its own: ``fix`` names the suggestion(s) that clear it.
What Norm only guessed is ``sure: False`` — never applied unattended unless its
``gate`` (a venue switch) is on.
"""

from app.services import invoice_review as IR
from app.services import replica_finish as RF
from app.services import venue_autopilot as VA
from tests.test_invoice_review import DETAIL, EXTRACTION, _review


def _unit_issue(code="unit_missing", line_id="ld-1", **over):
    issue = {
        "id": f"{code}:{line_id}",
        "code": code,
        "blocking": True,
        "line_id": line_id,
        "message": "line 1 'Freight': no unit could be read from the invoice",
    }
    issue.update(over)
    return issue


def _decide_unit(data, replica, pairs, *, issue, units=(), choose=None):
    """The unit rule, the comparison and the blocker link — the three steps
    a review runs — on one line."""
    RF.settle_units(data, replica, pairs, list(units), choose, [])
    working = {ln["id"]: ln for ln in data["lines"]}
    suggestions: list = []
    for rl in replica["lines"]:
        wid = pairs.get(rl["id"])
        IR._unit_suggestion(
            suggestions, rl, working.get(wid), wid or rl["id"], "Freight"
        )
    IR.link_fixes(data, replica, [issue], suggestions)
    fix = [s for s in suggestions if s["id"] in issue["fix"]]
    return fix[0] if fix else None


class TestUnitBlockersCarryAUnit:
    def _data(self, unit=None, unit_id=None):
        return {
            "lines": [
                {
                    "id": "ld-1",
                    "description": "Freight",
                    "unit": unit,
                    "linked_unit_id": unit_id,
                }
            ]
        }

    def _replica(self, code="unit_missing", line_id="rep-0", **line):
        return {
            "lines": [{"id": line_id, "description": "Freight", **line}],
            "issues": [{"id": f"{code}:{line_id}", "code": code, "line_id": line_id}],
        }

    def test_the_resolvers_answer_is_proposed_at_any_confidence(self):
        replica = self._replica(
            unit_resolved={
                "unit_id": "u-each",
                "unit_name": "Each",
                "unit_ratio": 1,
                "confidence": "low",
            }
        )
        fix = _decide_unit(
            self._data(), replica, {"rep-0": "ld-1"}, issue=_unit_issue()
        )
        assert fix["apply"] == {
            "linked_unit_id": "u-each",
            "unit": "Each",
            "unit_ratio": 1,
        }
        # A guess is for a person, or for the switch that says Norm may pick.
        assert fix["sure"] is False and fix["gate"] == VA.RECEIVE_WITHOUT_UNIT
        # ...and the Replica says so: the Replica tab shows what the card does.
        assert replica["lines"][0]["linked_unit_id"] == "u-each"

    def test_a_line_already_holding_the_unit_is_kept(self):
        # The Aitkens freight shape: Loaded's line had Each, so nothing was
        # proposed and the blocker said "set one" with nothing to accept.
        issue = _unit_issue()
        fix = _decide_unit(
            self._data("Each", "u-each"),
            self._replica(),
            {"rep-0": "ld-1"},
            issue=issue,
        )
        assert fix["kind"] == "confirm" and fix["proposed"] == "Each"
        assert issue["gate"] == fix["gate"] == VA.RECEIVE_WITH_UNCONFIRMED_UNIT

    def test_an_unconfirmed_unit_takes_the_replicas_answer(self):
        # The invoice's unit can't be read; the resolver names a 6 pack while
        # Loaded holds Each. One decision: the Replica's answer.
        replica = self._replica(
            "unit_unconfirmed",
            unit_resolved={"unit_id": "u-6pk", "unit_name": "6 pack"},
        )
        issue = _unit_issue("unit_unconfirmed", gate=VA.RECEIVE_WITH_UNCONFIRMED_UNIT)
        fix = _decide_unit(
            self._data("Each", "u-each"), replica, {"rep-0": "ld-1"}, issue=issue
        )
        assert fix["kind"] == "line_value"
        assert fix["apply"]["linked_unit_id"] == "u-6pk"
        assert fix["gate"] == VA.RECEIVE_WITH_UNCONFIRMED_UNIT

    def test_with_nothing_anywhere_the_venue_chooser_picks(self):
        issue = _unit_issue()
        each = {"id": "u-each", "name": "Each", "ratio": 1}
        fix = _decide_unit(
            self._data(),
            self._replica(),
            {"rep-0": "ld-1"},
            issue=issue,
            units=[each],
            choose=lambda ln: each,
        )
        assert fix["proposed"] == "Each"
        assert issue["gate"] == VA.RECEIVE_WITHOUT_UNIT

    def test_a_high_pick_on_a_blocked_line_still_waits_for_the_switch(self):
        # Oravida 'Chep pallets': the resolver was sure. Sure enough to name,
        # not to receive on without the switch — the blocker stands for that.
        replica = self._replica(
            unit_resolved={
                "unit_id": "u-each",
                "unit_name": "Each",
                "confidence": "high",
            }
        )
        fix = _decide_unit(
            self._data(), replica, {"rep-0": "ld-1"}, issue=_unit_issue()
        )
        assert fix["proposed"] == "Each" and fix["confidence"] == "high"
        assert fix["gate"] == VA.RECEIVE_WITHOUT_UNIT

    def test_a_create_is_its_own_fix(self):
        issue = _unit_issue()
        fix = _decide_unit(
            self._data(),
            self._replica(unit_create_name="6x6pc"),
            {"rep-0": "ld-1"},
            issue=issue,
            choose=lambda ln: None,
        )
        assert fix["kind"] == "create_unit"
        assert fix["payload"] == {"unit_name": "6x6pc"}
        assert issue["gate"] == fix["gate"] == VA.AUTO_CREATE_UNITS

    def test_a_line_waiting_to_be_added_gets_a_unit_too(self):
        # Akaroa Salmon F59751718 (dev company, 12 Oct 2026): 'Freight' was on
        # the invoice but not in Loaded's draft, so its blocker sat on the
        # replica line and nothing proposed a unit for it.
        replica = self._replica(
            line_id="rep-2",
            unit_resolved={"unit_id": "u-each", "unit_name": "Each"},
        )
        fix = _decide_unit(
            {"lines": []}, replica, {}, issue=_unit_issue(line_id="rep-2")
        )
        assert fix["line_id"] == "rep-2"
        assert fix["apply"]["linked_unit_id"] == "u-each"


class TestSupplierAndPhantomLines:
    def _replica(self, name):
        return {
            "issues": [
                {
                    "id": "supplier_unresolved",
                    "code": "supplier_unresolved",
                    "data": {"supplier_name": name},
                }
            ]
        }

    def _decide(self, replica, suppliers):
        data = {"lines": []}
        RF.settle_supplier(data, replica, suppliers, [])
        suggestions: list = []
        IR._header_suggestions(data, replica, suggestions)
        issue = {
            "id": "supplier_unresolved",
            "code": "supplier_unresolved",
            "blocking": True,
            "message": "x",
        }
        IR.link_fixes(data, replica, [issue], suggestions)
        return issue, suggestions

    def test_a_near_supplier_is_proposed(self):
        issue, suggestions = self._decide(
            self._replica("SERVICE FOODS LTD"),
            [
                {"id": "sup-sf", "name": "SERVICE FOODS AUCKLAND"},
                {"id": "sup-bf", "name": "Bidfood"},
            ],
        )
        [s] = suggestions
        assert s["kind"] == "supplier"
        assert s["apply"] == {
            "linked_supplier_id": "sup-sf",
            "supplier_name": "SERVICE FOODS AUCKLAND",
        }
        assert s["sure"] is False  # the closest record is a guess
        assert issue["fix"] == [s["id"]]

    def test_nothing_close_is_created(self):
        issue, suggestions = self._decide(
            self._replica("Tickety-Boo Liquor Ltd"),
            [{"id": "sup-bf", "name": "Bidfood"}],
        )
        [s] = suggestions
        assert s["kind"] == "create_supplier"
        assert s["payload"] == {"supplier_name": "Tickety-Boo Liquor Ltd"}
        assert s["gate"] == VA.AUTO_CREATE_SUPPLIERS
        assert issue["fix"] == [s["id"]]

    def test_a_phantom_line_is_struck(self):
        data = {
            "lines": [
                {
                    "id": "ld-9",
                    "description": "Ghost",
                    "quantity_received": 1,
                    "unit_cost": 12.0,
                    "total_cost": 12.0,
                }
            ]
        }
        suggestions, issues, _pairs = IR.build_suggestions(data, {"lines": []})
        IR.link_fixes(data, None, issues, suggestions)
        strike = next(s for s in suggestions if s["kind"] == "strike")
        issue = next(i for i in issues if i["code"] == "loaded_line_not_on_copy")
        assert issue["fix"] == [strike["id"]]
        assert strike["gate"] == VA.AUTO_STRIKE_PHANTOM_LINES


class _OrdersLoaded:
    def __init__(self, orders, lines_by_id):
        self._orders = orders
        self._lines = lines_by_id

    def get(self, path):
        if path.startswith("/1.0/stock/internal/purchase-orders?"):
            return self._orders
        po_id = path.rsplit("/", 1)[-1]
        return {
            "id": po_id,
            "lines": [{"itemId": i} for i in self._lines.get(po_id, [])],
        }


class TestLikelyOrder:
    def _data(self):
        return {
            "invoice_id": "inv-1",
            "linked_supplier_id": "sup-1",
            "linked_purchase_order_id": None,
            "lines": [
                {"id": "a", "linked_item_id": "item-a"},
                {"id": "b", "linked_item_id": "item-b"},
                {"id": "c", "linked_item_id": "item-c"},
            ],
        }

    def test_the_order_carrying_most_of_the_delivery_is_proposed(self):
        orders = [
            {
                "id": "po-old",
                "orderNumber": "100",
                "supplierId": "sup-1",
                "createdAt": "2026-09-01",
            },
            {
                "id": "po-new",
                "orderNumber": "101",
                "supplierId": "sup-1",
                "createdAt": "2026-09-08",
            },
            # Another supplier's, already invoiced, and claimed: never proposed.
            {
                "id": "po-x",
                "orderNumber": "102",
                "supplierId": "sup-2",
                "createdAt": "2026-09-09",
            },
            {
                "id": "po-i",
                "orderNumber": "103",
                "supplierId": "sup-1",
                "invoicedAt": "2026-09-09",
            },
            {
                "id": "po-c",
                "orderNumber": "104",
                "supplierId": "sup-1",
                "linkedInvoiceId": "inv-9",
            },
        ]
        lh = _OrdersLoaded(
            orders, {"po-old": ["item-a", "item-b", "item-c"], "po-new": ["item-a"]}
        )
        data, replica = self._data(), {"issues": []}
        RF.settle_order(data, replica, lh, [])
        assert replica["linked_purchase_order_id"] == "po-old"
        suggestions: list = []
        IR._header_suggestions(data, replica, suggestions)
        [s] = suggestions
        assert s["apply"] == {
            "linked_purchase_order_id": "po-old",
            "purchase_order_number": "100",
        }
        assert "3 of this delivery's 3 items" in s["explanation"]
        assert s["sure"] is False

    def test_an_order_sharing_nothing_is_not_the_order(self):
        orders = [{"id": "po-1", "orderNumber": "100", "supplierId": "sup-1"}]
        replica: dict = {"issues": []}
        RF.settle_order(
            self._data(), replica, _OrdersLoaded(orders, {"po-1": ["item-z"]}), []
        )
        assert "linked_purchase_order_id" not in replica

    def test_autopilot_never_links_a_guess(self):
        data = {
            "lines": [],
            "suggestions": [
                {
                    "id": "link_po:x",
                    "kind": "link_po",
                    "apply": {"linked_purchase_order_id": "po-1"},
                    "sure": False,
                }
            ],
        }
        settings = VA.settings_from({g: True for g in VA.GATES})
        IR.autopilot_accept(None, None, "v-1", "inv-1", data, settings)
        assert data.get("linked_purchase_order_id") is None


class TestSpreadToTotal:
    def _data(self):
        return {
            "total": 115.0,
            "tax_amount": 15.0,
            "lines": [
                {
                    "id": "a",
                    "description": "A",
                    "quantity_received": 2,
                    "unit_cost": 30.0,
                },
                {
                    "id": "b",
                    "description": "B",
                    "quantity_received": 3,
                    "unit_cost": 10.0,
                },
                {
                    "id": "c",
                    "description": "C",
                    "quantity_received": 1,
                    "unit_cost": 9.0,
                },
            ],
        }

    def test_the_lines_then_balance_to_the_total(self):
        data = self._data()  # 60 + 30 + 9 = 99, the total says 100 ex tax
        moved = RF.spread_costs(data)
        assert moved
        for ln in data["lines"]:
            if ln["id"] in moved:
                ln["unit_cost"] = moved[ln["id"]]
        _, _, derived, declared = IR._totals_view(data)
        assert abs(derived - declared) < 0.005

    def test_balanced_lines_move_nothing(self):
        data = self._data()
        data["total"] = 114.0
        assert RF.spread_costs(data) == {}


class TestTheReviewLeavesNoBlockerWithoutAFix:
    """Real reviews over the shapes that used to reach the card bare. The
    contract is blockers_without_a_fix(data) == [] — also logged as a warning
    on every production review."""

    def test_totals_with_no_misread_figure_are_balanced(self):
        # The invoice's total disagrees with its own lines and the arbiter
        # finds no misread figure: the Replica spreads the costs (a guess).
        data = _review(
            detail=DETAIL(total=300.0),
            extraction=EXTRACTION(total_incl_tax=300.0),
            totals_ask=lambda p: {},
        )
        issue = next(i for i in data["issues"] if i["code"] == "totals_inconsistent")
        fix = [s for s in data["suggestions"] if s["id"] in issue["fix"]]
        assert fix and all(s["sure"] is False for s in fix)
        assert all(s.get("fixes") == issue["id"] for s in fix)
        assert IR.blockers_without_a_fix(data) == []

    def test_totals_where_the_lines_already_balance_say_so(self):
        # Only the invoice's printed total is off; Loaded's lines add up.
        data = _review(
            extraction=EXTRACTION(total_incl_tax=300.0), totals_ask=lambda p: {}
        )
        issue = next(i for i in data["issues"] if i["code"] == "totals_inconsistent")
        [confirm] = [s for s in data["suggestions"] if s["id"] in issue["fix"]]
        assert confirm["kind"] == "confirm"
        assert "add up to the invoice total (252.75)" in confirm["explanation"]
        assert IR.blockers_without_a_fix(data) == []

    def test_a_header_misread_becomes_a_header_fix(self):
        det = DETAIL(total=252.75)
        ext = EXTRACTION(total_incl_tax=999.0)
        data = _review(
            detail=det,
            extraction=ext,
            totals_ask=lambda p: {
                "corrections": [
                    {
                        "scope": "header",
                        "field": "total",
                        "current": 999.0,
                        "proposed": 252.75,
                    }
                ],
                "confidence": "high",
                "why": "the total was misread",
            },
        )
        issue = next(
            (i for i in data["issues"] if i["code"] == "totals_inconsistent"), None
        )
        if issue is not None:
            assert data["replica"]["total"] == 252.75  # the Replica holds the fix
        assert IR.blockers_without_a_fix(data) == []


class TestWhichItemACodeIs:
    """replica_finish.settle_items, pinned by the production replay of 258
    drafts (12 Oct 2026) — each case a line that went the wrong way while the
    rule was being built."""

    SUP = "sup-1"

    def _item(self, iid, name, desc=None):
        return {
            "id": iid,
            "name": name,
            "suppliers": [
                {"supplierId": self.SUP, "stockCode": "C1", "description": desc}
            ],
        }

    def _settle(
        self, description, catalogue, *, working_item=None, rows=None, replica_po=None
    ):
        data = {
            "linked_supplier_id": self.SUP,
            "lines": [{"id": "ld-1", "code": "C1", "linked_item_id": working_item}],
        }
        if rows is not None:
            data["po_reference"] = {"po_id": replica_po, "lines": rows}
        replica = {
            "linked_purchase_order_id": replica_po,
            "lines": [{"id": "rep-0", "code": "C1", "description": description}],
            "issues": [],
        }
        RF.settle_items(data, replica, {"rep-0": "ld-1"}, catalogue, [])
        return replica["lines"][0]

    def test_letters_and_digits_are_separate_words(self):
        # Glass Goose TLC-689922: "NO10" names TANQUERAY 10 as much as GIN
        # names TANQUERAY GIN — a tie, so the order decides (and was right).
        rl = self._settle(
            "TANQUERAY GIN NO10 1L",
            [self._item("i-gin", "TANQUERAY GIN"), self._item("i-10", "TANQUERAY 10")],
            rows=[{"itemId": "i-10", "itemName": "TANQUERAY 10", "quantityOrdered": 1}],
            replica_po="po-1",
        )
        assert rl["linked_item_id"] == "i-10" and rl["matched_by"] == "order"

    def test_the_item_named_most_fully_wins(self):
        rl = self._settle(
            "PEPPERONI SLICED G/F",
            [self._item("i-sal", "SALAMI"), self._item("i-pep", "PEPPERONI SLICED")],
        )
        assert rl["linked_item_id"] == "i-pep"

    def test_the_suppliers_own_description_counts_as_naming_it(self):
        rl = self._settle(
            "SALAMI PEPPERONI SLICED G/F VERKERKS",
            [
                self._item("i-sal", "SALAMI", desc="SALAMI PEPPERONI SLICED G/F"),
                self._item("i-pep", "PEPPERONI SLICED"),
            ],
        )
        assert rl["linked_item_id"] == "i-sal"

    def test_with_nothing_else_it_keeps_what_loaded_has(self):
        # Freeman & Grey VEGF16 is on CORIANDER FRESH and EGGPLANT; Loaded had
        # the coriander, the catalogue lists the eggplant first.
        rl = self._settle(
            "HERB CORIANDER (PER KG)",
            [self._item("i-egg", "EGGPLANT"), self._item("i-cor", "CORIANDER FRESH")],
            working_item="i-cor",
        )
        assert rl["linked_item_id"] == "i-cor"

    def test_the_order_the_invoice_names_counts_too(self):
        # Glass Goose 110185577: Loaded held only the order NUMBER; the
        # Replica resolved the order, and its row named the straws.
        rl = self._settle(
            "STRAWS PAP BLK 6X197MM BIOPAK",
            [
                self._item(
                    "i-kraft", "Premium Kraft Regular Straw 6 X 200mm (Pkt) 250"
                ),
                self._item("i-blk", "STRAWS PAPER BLACK 6 X 197MM"),
            ],
            rows=[
                {
                    "itemId": "i-blk",
                    "itemName": "STRAWS PAPER BLACK",
                    "quantityOrdered": 5,
                }
            ],
            replica_po="po-7",
        )
        assert rl["linked_item_id"] == "i-blk" and rl["matched_by"] == "order"
