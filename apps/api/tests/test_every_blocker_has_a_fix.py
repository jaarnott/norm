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

import pytest

from app.services import invoice_review as IR
from app.services import replica_finish as RF
from app.services import venue_autopilot as VA
from tests.test_invoice_review import DETAIL, EXTRACTION, _issue_codes, _review


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
        # Norm always proposes a fix (user, 11 Oct 2026): a low or medium
        # answer is a guess for a person, never applied without the switch.
        # (The high case is the Oravida test below; the review's wiring of it
        # is test_invoice_review's test_resolver_pick_is_the_blockers_fix...)
        for confidence in ("low", "medium"):
            replica = self._replica(
                unit_resolved={
                    "unit_id": "u-each",
                    "unit_name": "Each",
                    "unit_ratio": 1,
                    "confidence": confidence,
                }
            )
            issue = _unit_issue()
            fix = _decide_unit(self._data(), replica, {"rep-0": "ld-1"}, issue=issue)
            assert fix["apply"] == {
                "linked_unit_id": "u-each",
                "unit": "Each",
                "unit_ratio": 1,
            }
            assert issue["fix"] == [fix["id"]]  # the blocker points at it
            # A guess is for a person, or for the switch that says Norm may pick.
            assert fix["sure"] is False and fix["gate"] == VA.RECEIVE_WITHOUT_UNIT
            assert fix["confidence"] == confidence
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


class TestSupplierBlockers:
    """(The phantom-line strike is pinned through a whole review in
    test_invoice_review.py::TestCoverage::test_loaded_money_line_not_on_copy_blocks.)"""

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

    def _decide(self, replica, suppliers, read_details=None, same_business=None):
        data = {"lines": []}
        RF.settle_supplier(data, replica, suppliers, [], read_details, same_business)
        suggestions: list = []
        IR._header_suggestions(data, replica, suggestions)
        issue = {
            "id": "supplier_unresolved",
            "code": "supplier_unresolved",
            "blocking": True,
            "gate": VA.AUTO_CREATE_SUPPLIERS,  # as _ISSUE_GATES assigns it
            "message": "x",
        }
        IR.link_fixes(data, replica, [issue], suggestions)
        return issue, suggestions

    def test_a_near_supplier_is_proposed(self):
        """Folded in: an undecided near match needs no switch, and linking an
        existing record writes nothing to it, so the invoice isn't read for
        its details."""
        asked = []
        issue, suggestions = self._decide(
            self._replica("SERVICE FOODS LTD"),
            [
                {"id": "sup-sf", "name": "SERVICE FOODS AUCKLAND"},
                {"id": "sup-bf", "name": "Bidfood"},
            ],
            read_details=lambda name: asked.append(name) or {},
        )
        [s] = suggestions
        assert s["kind"] == "supplier"
        assert s["apply"] == {
            "linked_supplier_id": "sup-sf",
            "supplier_name": "SERVICE FOODS AUCKLAND",
        }
        assert s["sure"] is False  # the closest record is a guess
        assert issue["fix"] == [s["id"]]
        assert issue.get("gate") is None
        assert asked == []

    def test_the_supplier_loaded_filed_it_under_is_no_answer(self):
        # Somewhere North: Loaded filed 19 Sydney Waste Services bills under
        # St. Malo Bakery, and Norm received them there (17 Oct 2026).
        data = {
            "lines": [],
            "linked_supplier_id": "sup-malo",
            "supplier_name": "St. Malo Bakery",
        }
        replica = self._replica("Sydney Waste Services")
        RF.settle_supplier(
            data, replica, [{"id": "sup-malo", "name": "St. Malo Bakery"}], []
        )
        suggestions: list = []
        IR._header_suggestions(data, replica, suggestions)
        [create] = suggestions
        assert (create["kind"], create["current"], create["proposed"]) == (
            "create_supplier",
            "St. Malo Bakery",
            "Sydney Waste Services",
        )
        [issue] = IR._finalise_issues(replica["issues"], [], {})
        assert "clears_when" not in issue  # St. Malo being linked settles nothing
        issue["blocking"] = True
        IR.link_fixes(data, replica, [issue], suggestions)
        doc = dict(data, issues=[issue], suggestions=suggestions)
        assert IR.compute_confidence(doc) == "needs_review"
        doc["suggestion_actions"] = [
            {"suggestion_id": create["id"], "action": "accepted"}
        ]
        assert IR.compute_confidence(doc) == "ready"

    def test_sharing_only_trade_words_is_not_close(self):
        # Applegum Cafe SI650438: proposed as PFD Food Services, while its
        # blocker said "auto-create suppliers" — which could never act.
        issue, suggestions = self._decide(
            self._replica("Blue Seas Food Services"),
            [{"id": "sup-pfd", "name": "PFD Food Services"}],
        )
        [s] = suggestions
        assert s["kind"] == "create_supplier"
        assert s["payload"]["supplier_name"] == "Blue Seas Food Services"
        assert issue["fix"] == [s["id"]]
        assert issue["gate"] == VA.AUTO_CREATE_SUPPLIERS  # the switch that acts

    SF = [{"id": "sup-sf", "name": "SERVICE FOODS AUCKLAND"}]

    def test_norm_decides_a_near_supplier_is_the_business(self):
        seen = []

        def same(name, details, candidates):
            seen.append((name, details, [c["id"] for c in candidates]))
            return {"id": "sup-sf", "why": "same address and account number"}

        issue, [s] = self._decide(
            self._replica("SERVICE FOODS LTD"),
            self.SF,
            read_details=lambda name: {"city": "Auckland"},
            same_business=same,
        )
        assert seen == [("SERVICE FOODS LTD", {"city": "Auckland"}, ["sup-sf"])]
        assert s["kind"] == "supplier" and s["sure"] is True
        assert s["apply"]["linked_supplier_id"] == "sup-sf"
        assert "same address and account number" in s["explanation"]
        assert issue["fix"] == [s["id"]]

    def test_norm_decides_a_near_supplier_is_another_business(self):
        # City Meats (N.S.W.) at Applegum was guessed as Allie's Cold Pressed.
        issue, [s] = self._decide(
            self._replica("SERVICE FOODS LTD"),
            self.SF,
            read_details=lambda name: {"city": "Sydney"},
            same_business=lambda n, d, c: {"id": None, "why": "a Sydney business"},
        )
        assert s["kind"] == "create_supplier"
        assert s["payload"]["not_these"] == ["sup-sf"]  # for autopilot's guard
        assert s["payload"]["details"] == {"city": "Sydney"}
        assert s["gate"] == VA.AUTO_CREATE_SUPPLIERS  # creating still needs it
        assert "'SERVICE FOODS AUCKLAND' is another business" in s["explanation"]
        assert issue["fix"] == [s["id"]]

    def test_undecided_the_closest_stays_a_guess(self):
        # The check failed (the model was down): nothing is linked unattended.
        _, [s] = self._decide(
            self._replica("SERVICE FOODS LTD"),
            self.SF,
            same_business=lambda n, d, c: {},
        )
        assert s["kind"] == "supplier" and s["sure"] is False

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

    def test_a_new_supplier_carries_what_its_invoice_prints(self):
        asked = []
        details = {
            "address_lines": ["12 Wharf Road"],
            "city": "Auckland",
            "phone": "09 555 0101",
            "email": "orders@tickety.co.nz",
        }

        def read(name):
            asked.append(name)
            return details

        replica = self._replica("Tickety-Boo Liquor Ltd")
        _, [s] = self._decide(
            replica, [{"id": "sup-bf", "name": "Bidfood"}], read_details=read
        )
        assert asked == ["Tickety-Boo Liquor Ltd"]
        assert replica["supplier_details"] == details
        assert s["payload"] == {
            "supplier_name": "Tickety-Boo Liquor Ltd",
            "details": details,
        }
        # The card says what the record will hold before anyone accepts it.
        assert s["explanation"].endswith(
            "with the details its invoice prints: 12 Wharf Road, Auckland · "
            "09 555 0101 · orders@tickety.co.nz"
        )

    def test_an_unreadable_invoice_still_creates_the_supplier(self):
        _, [s] = self._decide(
            self._replica("Tickety-Boo Liquor Ltd"),
            [],
            read_details=lambda name: {},
        )
        assert s["payload"] == {"supplier_name": "Tickety-Boo Liquor Ltd"}


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


class TestTotalsDoTheCardsArithmetic:
    """The totals rule works the invoice out the way the card and Loaded do
    (Loaded's calculateFooterTotals): lines, tax at each line's rate, less the
    discount. It used to keep the line's stored tax — Loaded's, for Loaded's
    cost — so every balancing balanced to the wrong total. Fresh Connection
    30049130 (14 Oct 2026): 5 × 42.99 moved to 29.4315 against tax still
    15% of 42.99, and the card came to $138.32 on a $179.40 invoice."""

    def _line(self, q, c, rate=0.15, **over):
        return {
            "id": "a",
            "quantity_received": q,
            "unit_cost": c,
            "sale_tax_rate": rate,
            **over,
        }

    def test_tax_is_worked_from_the_rate_not_the_stored_figure(self):
        data = {"total": 23.0, "lines": [self._line(2, 10.0, tax_amount=99.0)]}
        line_sum, tax, derived, declared = RF._totals_view(data)
        assert (line_sum, round(tax, 2), derived) == (20.0, 3.0, 23.0)

    def test_the_discount_comes_off_the_total(self):
        data = {"total": 18.0, "discount_amount": 5.0, "lines": [self._line(2, 10.0)]}
        assert RF._totals_view(data)[2] == 18.0

    def test_costs_that_include_tax(self):
        data = {"unit_cost_includes_tax": True, "lines": [self._line(1, 11.5)]}
        _, tax, derived, _ = RF._totals_view(data)
        assert (round(tax, 2), derived) == (1.5, 11.5)


class TestPricesAndDiscountsAreReadExactly:
    """Prices printed including GST, and a discount off the bottom, are worked
    out exactly — every printed figure must then agree — instead of spread as
    a guess. The receive never sends Loaded a discount, so it comes off the
    line costs: what the venue paid per unit."""

    def _view(self, *lines, total, discount=None):
        return {
            "total": total,
            "discount_amount": discount,
            "lines": [
                {"id": i, "quantity_received": q, "unit_cost": c, "sale_tax_rate": 0.15}
                for i, q, c in lines
            ],
        }

    def _printed(self, **figures):
        return {
            "subtotal": None,
            "tax_amount": None,
            "discount_amount": None,
            **figures,
        }

    def test_fresh_connection_30049130(self):
        """5 × $42.99 incl GST, less $30.91 ex GST: $31.20 a carton ex GST,
        $23.40 GST, $179.40 — every figure the invoice prints."""
        r = RF.read_printed_prices(
            self._view(("a", 5, 42.99), total=179.4),
            self._printed(
                subtotal=186.91, discount_amount=30.91, tax_amount=23.4, total=179.4
            ),
        )
        assert r["lines"] == {"a": {"unit_cost": 31.2}} and r["discount"]
        assert "prices include GST" in r["whys"]["a"]
        assert "30.91 discount (ex GST)" in r["whys"]["a"]

    def test_a_discount_off_the_bottom(self):
        r = RF.read_printed_prices(
            self._view(("a", 6, 10.0), ("b", 4, 10.0), total=103.5),
            self._printed(
                subtotal=100.0, discount_amount=10.0, tax_amount=13.5, total=103.5
            ),
        )
        assert r["lines"] == {"a": {"unit_cost": 9.0}, "b": {"unit_cost": 9.0}}
        assert r["discount"]

    def test_a_discount_printed_including_gst(self):
        r = RF.read_printed_prices(
            self._view(("a", 10, 10.0), total=103.5),
            self._printed(
                subtotal=100.0, discount_amount=11.5, tax_amount=13.5, total=103.5
            ),
        )
        assert r["lines"] == {"a": {"unit_cost": 9.0}}
        assert "(incl GST)" in r["whys"]["a"]

    def test_prices_including_gst_alone(self):
        r = RF.read_printed_prices(
            self._view(("a", 2, 11.5), total=23.0),
            self._printed(subtotal=20.0, tax_amount=3.0, total=23.0),
        )
        assert r["lines"] == {"a": {"unit_cost": 10.0}} and not r["discount"]
        assert "discount" not in r["whys"]["a"]

    def test_lines_that_already_agree_are_left_alone(self):
        assert (
            RF.read_printed_prices(
                self._view(("a", 10, 10.0), total=115.0),
                self._printed(subtotal=100.0, tax_amount=15.0, total=115.0),
            )
            is None
        )

    def test_a_discount_already_inside_the_lines_is_not_taken_twice(self):
        # Lion prints line totals net of discount and the discount for
        # information (94817415): lines + tax already make the total.
        assert (
            RF.read_printed_prices(
                self._view(("a", 10, 10.0), total=115.0),
                self._printed(
                    subtotal=100.0, discount_amount=12.0, tax_amount=15.0, total=115.0
                ),
            )
            is None
        )

    def test_the_total_alone_is_not_enough_evidence(self):
        # Without a printed subtotal or tax, "the prices include GST" could be
        # a total that happens to equal the lines.
        assert (
            RF.read_printed_prices(
                self._view(("a", 2, 11.5), total=23.0), self._printed(total=23.0)
            )
            is None
        )

    def test_a_reading_the_subtotal_contradicts_is_refused(self):
        assert (
            RF.read_printed_prices(
                self._view(("a", 2, 11.5), total=23.0),
                self._printed(subtotal=23.0, total=23.0),
            )
            is None
        )

    def test_a_credit_note_is_left_to_the_other_rules(self):
        replica = self._printed(subtotal=20.0, tax_amount=3.0, total=23.0)
        replica["is_credit_note"] = True
        assert (
            RF.read_printed_prices(self._view(("a", 2, 11.5), total=23.0), replica)
            is None
        )

    def _settle(self, *, issue: bool):
        data = {
            "total": 179.4,
            "lines": [
                {
                    "id": "ld-1",
                    "quantity_received": 5.0,
                    "unit_cost": 42.99,
                    "total_cost": 214.95,
                    "tax_amount": 32.2425,
                    "sale_tax_rate": 0.15,
                }
            ],
        }
        replica = {
            "subtotal": 186.91,
            "tax_amount": 23.4,
            "discount_amount": 30.91,
            "total": 179.4,
            "issues": (
                [
                    {
                        "id": "totals_inconsistent",
                        "code": "totals_inconsistent",
                        "message": "x",
                    }
                ]
                if issue
                else []
            ),
            "lines": [
                {
                    "id": "rep-0",
                    "quantity_received": 5,
                    "unit_cost": 42.99,
                    "sale_tax_rate": 0.15,
                }
            ],
        }
        log: list = []
        RF.settle_totals(
            data,
            replica,
            {"rep-0": "ld-1"},
            None,
            lambda p: pytest.fail("the model was asked"),
            None,
            log,
        )
        return replica, log

    def test_the_answer_is_sure_and_fixes_the_blocker(self):
        replica, log = self._settle(issue=True)
        rl = replica["lines"][0]
        assert rl["unit_cost"] == 31.2
        assert rl["fixes"] == {"unit_cost": "totals_inconsistent"}
        assert rl["unit_cost_why"].endswith(
            "31.20 a unit ex GST makes every printed figure agree"
        )
        # Inside the costs now — as its own figure it would come off twice.
        assert replica["discount_amount"] is None
        assert replica["discount_absorbed"] == 30.91

    def test_it_runs_even_when_the_copy_raised_no_blocker(self):
        # A discount satisfies the copy's own check yet the receive can't send
        # it, so it is read whether or not anything was flagged.
        replica, _ = self._settle(issue=False)
        assert replica["lines"][0]["unit_cost"] == 31.2
        assert "fixes" not in replica["lines"][0]


class TestTheTotalsCheckIsWhatTheSuggestionsLeave:
    """The user, 14 Oct 2026: "the goal is autoreceive so we need to make sure
    that after all suggestions are applied the totals always match, that
    should be our check, not any check on the totals before". Fresh
    Connection 30049130 read "Ready to receive" at $138.32 on a $179.40
    invoice: accepting the totals fixes was enough to clear the blocker."""

    BLOCKER = {
        "id": "totals_inconsistent",
        "code": "totals_inconsistent",
        "blocking": True,
        "fix": ["line_value:unit_cost:ld-1"],
        "clears_when": {"scope": "header", "op": "balances"},
        "message": "x",
    }

    def _doc(self, cost):
        return {
            "total": 23.0,
            "lines": [
                {
                    "id": "ld-1",
                    "quantity_received": 2,
                    "unit_cost": cost,
                    "sale_tax_rate": 0.15,
                }
            ],
            "suggestion_actions": [
                {"suggestion_id": "line_value:unit_cost:ld-1", "action": "accepted"}
            ],
        }

    def test_accepted_fixes_are_not_enough_on_their_own(self):
        assert IR.is_settled(self._doc(11.0), self.BLOCKER) is False

    def test_it_clears_when_the_working_values_match(self):
        assert IR.is_settled(self._doc(10.0), self.BLOCKER) is True

    def test_within_loadeds_rounding_is_a_match(self):
        # Loaded receives up to 10c out as "Rounding"; 2 x 10.04 + tax = 23.09.
        assert IR.is_settled(self._doc(10.04), self.BLOCKER) is True
        assert IR.is_settled(self._doc(10.06), self.BLOCKER) is False

    def _balance(self, data, suggestions, issues):
        IR.balance_after_suggestions(data, suggestions, issues)
        return suggestions, issues

    def _off(self):
        # The invoice says 30.00 ex GST, but its price reads 11.50: accepting
        # it would still be $8.05 short.
        data = {
            "invoice_id": "inv-1",
            "total": 34.5,
            "lines": [
                {
                    "id": "ld-1",
                    "description": "Salmon",
                    "quantity_received": 2,
                    "unit_cost": 10.0,
                    "sale_tax_rate": 0.15,
                }
            ],
        }
        price = {
            "id": "line_value:unit_cost:ld-1",
            "kind": "line_value",
            "field": "unit_cost",
            "line_id": "ld-1",
            "proposed": 11.5,
            "apply": {"unit_cost": 11.5},
            "sure": True,
        }
        return data, price

    @staticmethod
    def _answer(*costs):
        asked: list = []

        def ask(payload):
            asked.append(payload)
            cost = costs[min(len(asked), len(costs)) - 1]
            return {
                "lines": [{"line_id": "ld-1", "unit_price": cost}],
                "why": "the price was misread",
            }

        ask.asked = asked
        return ask

    def test_no_line_explains_it_and_the_blocker_stands(self):
        # Never an adjustment line, never the difference spread over the
        # prices (the owner, 16 Oct 2026: "it should work out the lines").
        data, price = self._off()
        suggestions, issues = self._balance(data, [price], [])
        [issue] = issues
        assert issue["clears_when"] == {"scope": "header", "op": "balances"}
        assert "gate" not in issue  # Loaded won't receive it: no switch can
        [s] = suggestions
        assert s["proposed"] == 11.5 and s["sure"] is True
        assert s["fixes"] == issue["id"]
        assert not IR.totals_balance(IR._all_applied(data, suggestions))

    def test_the_model_works_out_the_wrong_line(self):
        # ...reading the invoice itself, because its figures alone didn't say.
        data, price = self._off()
        suggestions, issues = [price], []
        read: list = []
        IR.balance_after_suggestions(
            data,
            suggestions,
            issues,
            ask=self._answer(15.0),
            document=lambda: read.append(1) or {"type": "document"},
        )
        assert read == [1]
        [s] = suggestions  # the price suggestion now carries the right price
        assert s["proposed"] == 15.0 and s["sure"] is True
        assert "the price was misread" in s["explanation"]
        [issue] = issues  # the draft as it stands still doesn't add up
        assert s["fixes"] == issue["id"]
        assert IR.totals_balance(IR._all_applied(data, suggestions))

    def test_an_answer_that_doesnt_add_up_changes_nothing(self):
        data, price = self._off()
        suggestions, issues = [price], []
        ask = self._answer(14.0)
        IR.balance_after_suggestions(data, suggestions, issues, ask=ask)
        [s] = suggestions
        assert s["proposed"] == 11.5
        assert len(ask.asked) == 3  # the first answer, then two more tries

    def test_a_wrong_answer_goes_back_for_another_try(self):
        data, price = self._off()
        suggestions, issues = [price], []
        ask = self._answer(14.0, 15.0)
        IR.balance_after_suggestions(data, suggestions, issues, ask=ask)
        [s] = suggestions
        assert s["proposed"] == 15.0
        [first, second] = ask.asked
        assert first["earlier_answers"] == []
        [earlier] = second["earlier_answers"]
        assert earlier["problem"] == "with it the invoice comes to 32.20"

    def test_a_line_charged_gst_the_wrong_way_is_put_right(self):
        # 20.00 GST-free + 10.00 charged at 15% = 31.50; Loaded charged both.
        data = {
            "invoice_id": "inv-1",
            "total": 31.5,
            "lines": [
                {
                    "id": f"ld-{i}",
                    "quantity_received": 1,
                    "unit_cost": cost,
                    "sale_tax_rate": 0.15,
                }
                for i, cost in ((1, 20.0), (2, 10.0))
            ],
        }
        suggestions: list = []
        IR.balance_after_suggestions(
            data,
            suggestions,
            [],
            ask=lambda p: {
                "lines": [{"line_id": "ld-1", "gst": "free"}],
                "why": "the cheese is GST-free",
            },
        )
        [s] = suggestions
        assert (s["field"], s["proposed"], s["sure"]) == ("sale_tax_rate", 0.0, True)
        assert IR.totals_balance(IR._all_applied(data, suggestions))

    def test_an_invoice_that_adds_up_never_asks(self):
        # The invoice is read only when needed: not here.
        data, _ = self._off()
        data["total"] = 23.0

        def ask(payload):
            raise AssertionError("an invoice that adds up costs nothing extra")

        def document():
            raise AssertionError("nor is its copy read")

        IR.balance_after_suggestions(data, [], [], ask=ask, document=document)

    def test_loadeds_draft_adding_up_never_stands_in_for_norms_figures(self):
        # Loaded's 2 x 10.00 comes to its own 23.00 but the copy reads 11.50.
        # Loaded's draft used to win here; of four drafts that "added up" none
        # was right (17 Oct 2026). Now the model works the lines out, and when
        # it can't the receive stops rather than taking Loaded's figures.
        data, price = self._off()
        data["total"] = 23.0
        suggestions, issues = [price], []
        ask = self._answer(14.0)
        IR.balance_after_suggestions(data, suggestions, issues, ask=ask)
        assert len(ask.asked) == 3
        [s] = suggestions
        assert s["proposed"] == 11.5
        [issue] = issues
        assert issue["message"] == "the invoice comes to 26.45, not its total 23.00"
        received = IR._all_applied(dict(data, issues=issues), suggestions)
        assert IR.compute_confidence(received) == "needs_review"

    def test_a_draft_with_no_total_never_stands_in_either(self):
        # Alsco CCTN3779394 and two Lion Nathan credits: Loaded had no total,
        # so its $0 lines "added up" and Norm's own total was withdrawn.
        data, price = self._off()
        data["total"] = None
        data["lines"][0]["unit_cost"] = 0.0
        total = {
            "id": "header_value:total",
            "kind": "header_value",
            "field": "total",
            "proposed": 23.0,
            "apply": {"total": 23.0},
            "sure": True,
        }
        suggestions = [price, total]
        IR.balance_after_suggestions(data, suggestions, [], ask=self._answer(10.0))
        assert {s["id"]: s["proposed"] for s in suggestions} == {
            price["id"]: 10.0,
            "header_value:total": 23.0,
        }
        assert IR.totals_balance(IR._all_applied(data, suggestions))

    # -- any gap visible on the copy can be closed (the owner, 17 Oct 2026) --

    @staticmethod
    def _two_lines(total):
        # 2 x 10.00 and 1 x 5.00, both charged 15%.
        return {
            "invoice_id": "inv-1",
            "total": total,
            "lines": [
                {
                    "id": lid,
                    "description": desc,
                    "quantity_received": qty,
                    "unit_cost": cost,
                    "total_cost": qty * cost,
                    "sale_tax_rate": 0.15,
                }
                for lid, desc, qty, cost in (
                    ("ld-1", "Salmon", 2, 10.0),
                    ("ld-2", "Salmon", 1, 5.0),
                )
            ],
        }

    FREIGHT = {
        "description": "Freight",
        "code": "FRT",
        "quantity": 1,
        "unit_price": 10.0,
        "gst": "charged",
    }

    def test_a_line_the_read_missed_is_added_and_matched_like_any_other(self):
        # The copy bills $10 freight that neither read caught: 2 x 10 + 1 x 5
        # + 10 = 35.00 + 15% = 40.25.
        data = self._two_lines(40.25)
        issues: list = []
        asked: list = []
        matched = {
            "id": "rep-0",
            "description": "Freight",
            "code": "FRT",
            "quantity_received": 1,
            "unit_cost": 10.0,
            "total_cost": 10.0,
            "sale_tax_rate": 0.15,
            "linked_item_id": "item-freight",
            "item_name": "Freight",
        }
        suggestions: list = []
        IR.balance_after_suggestions(
            data,
            suggestions,
            issues,
            ask=lambda p: {"add": [self.FREIGHT], "why": "freight is printed"},
            resolve_lines=lambda lines: (
                asked.extend(lines) or {"lines": [matched], "issues": []}
            ),
        )
        assert asked[0]["unit_price_ex_tax"] == 10.0 and asked[0]["gst"] == "charged"
        [add] = suggestions
        assert add["id"] == "add_line:totals-0"
        assert add["payload"]["linked_item_id"] == "item-freight"
        [issue] = issues  # the draft as it stands is $11.50 short
        assert add["fixes"] == issue["id"]
        received = IR._all_applied(dict(data, issues=issues), suggestions)
        assert IR.compute_confidence(received) == "ready"

    def test_an_added_line_nothing_matches_never_goes_to_loaded_unlinked(self):
        data = self._two_lines(40.25)
        issues: list = []
        suggestions: list = []
        IR.balance_after_suggestions(
            data,
            suggestions,
            issues,
            ask=lambda p: {"add": [self.FREIGHT], "why": "freight is printed"},
        )
        assert IR.totals_balance(IR._all_applied(data, suggestions))
        assert "item_unmatched:totals-0" in {i["id"] for i in issues}

    def test_a_line_loadeds_draft_bills_twice_is_struck(self):
        # The copy bills only the 2 x 10.00: 23.00.
        data = self._two_lines(23.0)
        suggestions: list = []
        IR.balance_after_suggestions(
            data,
            suggestions,
            [],
            ask=lambda p: {"remove": ["ld-2"], "why": "the salmon is billed once"},
        )
        [strike] = suggestions
        assert (strike["kind"], strike["line_id"]) == ("strike", "ld-2")
        assert strike["gate"] == VA.AUTO_STRIKE_PHANTOM_LINES
        assert IR.totals_balance(IR._all_applied(data, suggestions))

    def test_a_line_norms_read_added_goes_with_what_hangs_off_it(self):
        data = self._two_lines(23.0)
        data["lines"].pop()
        added = {
            "id": "add_line:rep-1",
            "kind": "add_line",
            "line_id": "rep-1",
            "payload": {
                "id": "rep-1",
                "quantity_received": 1,
                "unit_cost": 5.0,
                "sale_tax_rate": 0.15,
            },
        }
        create = {"id": "create_item:linked_item_id:rep-1", "kind": "create_item"}
        create["line_id"] = "rep-1"
        issues = [{"id": "item_unmatched:rep-1", "line_id": "rep-1", "blocking": True}]
        suggestions = [added, create]
        IR.balance_after_suggestions(
            data,
            suggestions,
            issues,
            ask=lambda p: {"remove": ["rep-1"], "why": "read twice"},
        )
        assert suggestions == [] and issues == []

    def test_a_line_norm_would_strike_is_kept_when_the_invoice_bills_it(self):
        # Norm's read missed the 1 x 5.00 that Loaded's draft has, so it
        # would strike it; the invoice does bill it: 28.75.
        data = self._two_lines(28.75)
        strike = {
            "id": "strike:ld-2",
            "kind": "strike",
            "line_id": "ld-2",
            "apply": {"struck": True},
            "gate": VA.AUTO_STRIKE_PHANTOM_LINES,
        }
        issues = [
            {
                "id": "loaded_line_not_on_copy:ld-2",
                "code": "loaded_line_not_on_copy",
                "line_id": "ld-2",
                "blocking": True,
            }
        ]
        seen: list = []
        suggestions = [strike]
        IR.balance_after_suggestions(
            data,
            suggestions,
            issues,
            ask=lambda p: (
                seen.append(p) or {"keep": ["ld-2"], "why": "the 5.00 salmon is billed"}
            ),
        )
        assert {ln["id"]: ln["receive"] for ln in seen[0]["lines"]} == {
            "ld-1": True,
            "ld-2": False,
        }
        assert suggestions == [] and issues == []

    def test_an_invoice_nothing_makes_add_up_is_reported(self):
        data, price = self._off()
        told: list = []
        IR.balance_after_suggestions(
            data, [price], [], ask=self._answer(14.0), on_unresolved=told.append
        )
        [info] = told
        assert (info["invoice_id"], info["came_to"], info["total"]) == (
            "inv-1",
            26.45,
            34.5,
        )
        assert len(info["tried"]) == 3

    def test_a_total_correction_is_a_fix_of_the_totals(self):
        data = {
            "invoice_id": "inv-1",
            "total": 25.0,
            "lines": [
                {
                    "id": "ld-1",
                    "quantity_received": 2,
                    "unit_cost": 10.0,
                    "sale_tax_rate": 0.15,
                }
            ],
        }
        total = {
            "id": "header_value:total",
            "kind": "header_value",
            "field": "total",
            "proposed": 23.0,
            "apply": {"total": 23.0},
            "sure": True,
        }
        suggestions, issues = self._balance(data, [total], [])
        [issue] = issues
        assert suggestions[0]["fixes"] == issue["id"]

    def test_an_added_line_keeps_its_printed_cost(self):
        data = {
            "invoice_id": "inv-1",
            "total": 40.25,
            "lines": [
                {
                    "id": "ld-1",
                    "quantity_received": 1,
                    "unit_cost": 10.0,
                    "sale_tax_rate": 0.15,
                }
            ],
        }
        add = {
            "id": "add_line:rep-1",
            "kind": "add_line",
            "payload": {
                "id": "new-1",
                "description": "Freight",
                "quantity_received": 1,
                "unit_cost": 20.0,
                "sale_tax_rate": 0.15,
            },
            "sure": True,
        }
        seen: list = []

        def ask(payload):
            seen.extend(ln["id"] for ln in payload["lines"])
            return {
                "lines": [{"line_id": "ld-1", "unit_price": 15.0}],
                "why": "the cleaning line is 15.00",
            }

        suggestions: list = [add]
        IR.balance_after_suggestions(data, suggestions, [], ask=ask)
        assert seen == ["ld-1", "new-1"]  # the added line is on the invoice too
        moved = {
            s["line_id"]: s["proposed"]
            for s in suggestions
            if s["kind"] == "line_value"
        }
        assert moved == {"ld-1": 15.0}  # 15 + 20 = 35, + 5.25 GST = 40.25

    def test_a_copy_that_doesnt_reconcile_is_no_blocker_if_the_result_does(self):
        data = {
            "invoice_id": "inv-1",
            "total": 23.0,
            "lines": [
                {
                    "id": "ld-1",
                    "quantity_received": 2,
                    "unit_cost": 10.0,
                    "sale_tax_rate": 0.15,
                }
            ],
        }
        fix = {
            "id": "header_value:tax_amount",
            "kind": "header_value",
            "fixes": "totals_inconsistent",
            "apply": {"tax_amount": 3.0},
        }
        issue = {
            "id": "totals_inconsistent",
            "code": "totals_inconsistent",
            "blocking": True,
            "message": "x",
        }
        suggestions, issues = self._balance(data, [fix], [issue])
        assert issues == []
        assert suggestions[0]["fixes"] is None

    def test_a_draft_about_to_be_deleted_is_not_checked(self):
        data = {
            "total": 99.0,
            "lines": [{"id": "ld-1", "quantity_received": 1, "unit_cost": 1.0}],
        }
        _, issues = self._balance(
            data, [{"id": "delete_invoice", "kind": "delete_invoice"}], []
        )
        assert issues == []


class TestBidfoodLineDiscounts:
    """Bidfood prints its list price and charges about 5% less on the line
    (La Zeppa 110185654: 1 x 66.25, line 62.94). Loaded held the net 62.94 —
    right — and the review proposed the list price as a SURE fix, leaving the
    invoice $3.81 over once accepted. The line total is what was charged."""

    def _view(self):
        return {
            "total": 72.38,
            "lines": [
                {
                    "id": "ld-1",
                    "quantity_received": 1.0,
                    "unit_cost": 66.25,
                    "sale_tax_rate": 0.15,
                    "printed_total": 62.94,
                }
            ],
        }

    def test_the_line_total_is_the_cost(self):
        r = RF.read_printed_prices(
            self._view(),
            {
                "subtotal": 62.94,
                "tax_amount": 9.44,
                "total": 72.38,
                "discount_amount": None,
            },
        )
        assert r["lines"] == {"ld-1": {"unit_cost": 62.94}}
        assert "the line total is what was charged" in r["whys"]["ld-1"]

    def test_a_whole_multiple_is_a_misread_quantity(self):
        # Bidfood 110167263: "2 x 41.11", line 164.44 — 4 at 41.11. Norm
        # decides: 2 at 82.22 fits too, but needs two misreads.
        view = {
            "total": 189.11,
            "lines": [
                {
                    "id": "ld-1",
                    "quantity_received": 2.0,
                    "unit_cost": 41.11,
                    "sale_tax_rate": 0.15,
                    "printed_total": 164.44,
                }
            ],
        }
        printed = {
            "subtotal": 164.44,
            "tax_amount": 24.67,
            "total": 189.11,
            "discount_amount": None,
        }
        r = RF.read_printed_prices(view, printed)
        assert r["lines"] == {"ld-1": {"quantity_received": 4.0, "unit_cost": 41.11}}
        assert "4 at the printed 41.11, not the 2 it prints" in r["whys"]["ld-1"]

    def test_lines_the_invoice_doesnt_bill_and_zero_leftovers_are_ignored(self):
        # Bidfood 110185651 / 110167264: a $0 PECORINO and a duplicate feta.
        view = {
            "total": 15.82,
            "lines": [
                {
                    "id": "dup",
                    "quantity_received": 1.0,
                    "unit_cost": 13.76,
                    "sale_tax_rate": 0.15,
                    "struck": True,
                },
                {
                    "id": "zero",
                    "quantity_received": 0.0,
                    "unit_cost": 13.34,
                    "sale_tax_rate": 0.15,
                },
                {
                    "id": "feta",
                    "quantity_received": 1.0,
                    "unit_cost": 14.48,
                    "sale_tax_rate": 0.15,
                    "printed_total": 13.76,
                },
            ],
        }
        printed = {
            "subtotal": 13.76,
            "tax_amount": 2.06,
            "total": 15.82,
            "discount_amount": None,
        }
        r = RF.read_printed_prices(view, printed)
        assert r["lines"] == {"feta": {"unit_cost": 13.76}}

    def test_the_total_and_gst_are_enough_when_the_subtotal_is_odd(self):
        # Lion 94817415 prints a subtotal without its fuel surcharge.
        view = {
            "total": 126.5,
            "lines": [
                {
                    "id": "beer",
                    "quantity_received": 4.0,
                    "unit_cost": 28.69,
                    "sale_tax_rate": 0.15,
                    "printed_total": 99.4,
                },
                {
                    "id": "fuel",
                    "quantity_received": 1.0,
                    "unit_cost": 10.6,
                    "sale_tax_rate": 0.15,
                    "printed_total": 10.6,
                },
            ],
        }
        printed = {
            "subtotal": 99.4,
            "tax_amount": 16.5,
            "total": 126.5,
            "discount_amount": 15.36,
        }
        r = RF.read_printed_prices(view, printed)
        assert r["lines"] == {"beer": {"unit_cost": 24.85}}

    def test_not_when_the_invoice_says_otherwise(self):
        # Printed figures that add up to the list price: the line total is the
        # misread, not the price.
        assert (
            RF.read_printed_prices(
                {**self._view(), "total": 76.19},
                {
                    "subtotal": 66.25,
                    "tax_amount": 9.94,
                    "total": 76.19,
                    "discount_amount": None,
                },
            )
            is None
        )


class TestLinesWithoutGst:
    """Loaded gives every line the venue's default rate; the printed GST says
    which lines actually carry it. Applegum Cafe's Blue Seas invoices (15 Oct
    2026): SI655075 printed GST $0.00, SI655016 only the 10% on its fuel levy,
    and the review cut every food price ~9% to make 10% fit."""

    def _settle(self, lines, *, tax, total):
        data = {
            "total": total,
            "lines": [
                {
                    "id": f"ld-{i}",
                    "quantity_received": q,
                    "unit_cost": c,
                    "sale_tax_rate": 0.1,
                }
                for i, (_d, q, c) in enumerate(lines)
            ],
        }
        replica = {
            "tax_amount": tax,
            "total": total,
            "lines": [
                {
                    "id": f"rep-{i}",
                    "description": d,
                    "quantity_received": q,
                    "unit_cost": c,
                    "total_cost": round(q * c, 2),
                    "sale_tax_rate": 0.1,
                }
                for i, (d, q, c) in enumerate(lines)
            ],
        }
        pairs = {f"rep-{i}": f"ld-{i}" for i in range(len(lines))}
        log: list = []
        RF.settle_tax_rates(data, replica, pairs, log)
        return data, replica, pairs

    def _rates(self, replica):
        return [rl["sale_tax_rate"] for rl in replica["lines"]]

    def test_nil_gst_means_every_line_is_tax_free(self):
        """Folded in: the review then suggests the rate on each line and moves
        no cost, and the result adds up."""
        data, replica, pairs = self._settle(
            [("BBQ Sauce 3Ltr", 1, 13.26), ("Invoice Rounding", 1, -0.01)],
            tax=0.0,
            total=13.25,
        )
        assert self._rates(replica) == [0.0, 0.0]
        assert "charges no GST" in replica["lines"][0]["sale_tax_rate_why"]
        suggestions: list = []
        for rl in replica["lines"]:
            ln = next(x for x in data["lines"] if x["id"] == pairs[rl["id"]])
            IR._line_suggestions(suggestions, rl, ln, ln["id"])
        rate = [s for s in suggestions if s["field"] == "sale_tax_rate"]
        assert [s["proposed"] for s in rate] == [0.0, 0.0]
        issues: list = []
        IR.balance_after_suggestions(data, suggestions, issues)
        assert not [s for s in suggestions if s["field"] == "unit_cost"]
        assert IR.totals_balance(IR._all_applied(data, suggestions))

    def test_only_the_line_whose_tax_makes_the_gst_keeps_it(self):
        _, replica, _ = self._settle(
            [
                ("Mozzarella 2kg", 1, 25.95),
                ("Yoghurt 500g", 1, 2.86),
                ("Fuel Levy", 1, 3.0),
            ],
            tax=0.30,
            total=32.11,
        )
        assert self._rates(replica) == [0.0, 0.0, 0.1]

    def test_two_ways_to_make_the_gst_changes_nothing(self):
        _, replica, _ = self._settle(
            [("A", 1, 3.0), ("B", 1, 3.0), ("C", 1, 10.0)], tax=0.30, total=16.30
        )
        assert self._rates(replica) == [0.1, 0.1, 0.1]

    def test_lines_that_dont_make_the_total_are_left_alone(self):
        _, replica, _ = self._settle([("A", 1, 10.0)], tax=0.0, total=99.0)
        assert self._rates(replica) == [0.1]

    def test_gst_on_everything_changes_nothing(self):
        _, replica, _ = self._settle(
            [("A", 1, 10.0), ("B", 2, 5.0)], tax=2.0, total=22.0
        )
        assert self._rates(replica) == [0.1, 0.1]


class TestTheReviewLeavesNoBlockerWithoutAFix:
    """Real reviews over the shapes that used to reach the card bare. The
    contract is blockers_without_a_fix(data) == [] — also logged as a warning
    on every production review."""

    def test_totals_no_line_explains_keep_their_blocker(self):
        # The invoice's printed total disagrees with its own lines and
        # nothing explains it. Norm never bends the costs to fit (the spread
        # cut 64 Applegum invoices' prices, 16 Oct 2026): the blocker stands,
        # and the run names the invoice among those not received.
        data = _review(
            detail=DETAIL(total=300.0),
            extraction=EXTRACTION(total_incl_tax=300.0),
            totals_ask=lambda p: {},
        )
        assert "totals_inconsistent" in _issue_codes(data)
        assert not [s for s in data["suggestions"] if s.get("field") == "unit_cost"]

    def test_totals_the_model_works_out_are_fixed(self):
        # Loaded and the copy both read 4.95; the invoice's subtotal, GST and
        # total say 5. The copy's own check finds nothing; on the values Norm
        # would receive, the model works out the quantity.
        def ask(payload):
            if "earlier_answers" not in payload:
                return {}  # the copy's own check, on its figures alone
            return {
                "lines": [{"line_id": "ld-1", "quantity": 5.0}],
                "why": "5 at 44.40 is the 222.00 subtotal",
            }

        data = _review(
            detail=DETAIL(total=255.30, subtotal=222.0, taxAmount=33.30),
            extraction=EXTRACTION(
                subtotal_ex_tax=222.0, tax_amount=33.30, total_incl_tax=255.30
            ),
            totals_ask=ask,
        )
        [s] = [s for s in data["suggestions"] if s.get("field") == "quantity_received"]
        assert s["proposed"] == 5.0 and s["sure"] is True
        assert not [s for s in data["suggestions"] if s.get("field") == "unit_cost"]
        assert IR.totals_balance(IR._all_applied(data, data["suggestions"]))
        assert IR.blockers_without_a_fix(data) == []

    def test_totals_where_the_lines_already_balance_need_nothing(self):
        # Only the invoice's printed total is off; Loaded's lines add up to
        # Loaded's total. The Replica keeps Loaded's total (proposing the
        # misprint would unbalance it), so accept-all matches: no blocker.
        data = _review(
            extraction=EXTRACTION(total_incl_tax=300.0), totals_ask=lambda p: {}
        )
        assert not [i for i in data["issues"] if i["code"] == "totals_inconsistent"]
        assert not [s for s in data["suggestions"] if s.get("field") == "total"]
        assert data["replica"]["total"] == 252.75
        assert IR.blockers_without_a_fix(data) == []

    def test_a_header_misread_becomes_a_header_fix(self):
        # The copy misread the printed total; the totals model corrects the
        # HEADER, not a line. The Replica holds the corrected total, says why,
        # and marks it a fix — the only test of replica_finish's header-
        # correction branch (restored 17 Oct 2026: its assertions sat behind
        # an `if` that never fired, so it looked inert, but it was the one
        # test driving that code).
        data = _review(
            detail=DETAIL(total=252.75),
            extraction=EXTRACTION(total_incl_tax=999.0),
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
        rep = data["replica"]
        assert rep["total"] == 252.75
        assert "the total was misread" in rep["total_why"]
        assert rep["fixes_confidence"] == "high"
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


class TestEveryBlockerHasAnAutopilotDecision:
    """The tripwire (docs/plans/autopilot-fallbacks.md). On Autopilot every
    invoice is received: Norm decides, and never hands an invoice to a person
    (the owner, 16 Oct 2026). Every blocker code the review can raise says
    here what settles it with no one there. A new code fails this test until
    it does; the ones that still stop Autopilot are KNOWN_GAPS, by name."""

    _SOURCES = ("invoice_replica.py", "invoice_review.py", "replica_finish.py")

    #: blocker → the suggestion that settles it, applied by Autopilot when
    #: Norm is sure of it or its switch is on.
    SETTLED_BY = {
        "item_unmatched": {"kind": "create_item", "field": "linked_item_id"},
        "unit_missing": {"kind": "create_unit", "field": "unit"},
        "unit_would_be_created": {"kind": "create_unit", "field": "unit"},
        "unit_unconfirmed": {"kind": "line_value", "field": "unit"},
        "brand_unknown": {"kind": "create_brand"},
        "supplier_unresolved": {"kind": "create_supplier"},
        "loaded_line_not_on_copy": {"kind": "strike"},
        "po_missing": {"kind": "link_po"},
        # A "receive it anyway" confirm, applied when its switch is on.
        "unit_kind_mismatch": {"kind": "confirm", "fixes": "unit_kind_mismatch"},
        "po_unresolved": {"kind": "link_po"},
        "po_split_order": {"kind": "split_reference"},
        "po_doubled_up": {"kind": "unlink_po"},
        "duplicate_invoice": {"kind": "delete_invoice"},
        "not_an_invoice": {"kind": "delete_invoice"},
        "no_copy_attached": {"kind": "delete_invoice"},
        "copy_unreadable": {"kind": "delete_invoice"},
        # Norm works out which lines are wrong, looking at the invoice itself
        # when its figures alone don't say (balance_after_suggestions). Only
        # a model that finds nothing in three tries leaves it standing.
        "totals_inconsistent": {
            "kind": "line_value",
            "field": "unit_cost",
            "fixes": "totals_inconsistent",
        },
    }
    #: Blockers that still stop Autopilot — each one a decision still owed.
    KNOWN_GAPS = {
        "credit_zero_quantity": (
            "Lion Nathan rebate credits (14 Oct 2026): how a rebate goes into "
            "Loaded is the owner's call"
        ),
        "copy_not_checked": "a service was down: this run stops, the next retries",
    }
    NOT_BLOCKING = {"credit_note", "po_supplier_mismatch", "pairing_arbitrated"}

    def _raised(self) -> set[str]:
        import pathlib
        import re

        here = pathlib.Path(IR.__file__).parent
        src = "".join((here / f).read_text() for f in self._SOURCES)
        return set(re.findall(r'_issue\(\s*"([a-z_]+)"', src)) | set(
            re.findall(r'"code":\s*"([a-z_]+)"', src)
        )

    def test_every_blocker_the_review_raises_has_a_decision(self):
        known = set(self.SETTLED_BY) | set(self.KNOWN_GAPS) | self.NOT_BLOCKING
        assert self._raised() - known == set()

    def test_no_decision_is_left_for_a_blocker_that_is_gone(self):
        known = set(self.SETTLED_BY) | set(self.KNOWN_GAPS) | self.NOT_BLOCKING
        assert known - self._raised() == set()

    @pytest.mark.parametrize("code", sorted(SETTLED_BY))
    def test_its_suggestion_really_settles_it(self, code):
        issue = {"id": code, "code": code, "line_id": "ln-1", "blocking": True}
        s = {"id": "s-1", "line_id": "ln-1", **self.SETTLED_BY[code]}
        assert IR._answers(s, issue)


class TestAUnitOfAnotherKind:
    """Owner, 17 Oct 2026: a unit of a different kind (weight, volume, count)
    from the product's stocktake unit needs its own switch. Off: the invoice
    is not received; on: it is. Loaded can't convert one kind to another."""

    UNITS = [
        {"id": "u-kilo", "name": "Kilo", "ratio": 1, "stockUnitType": "Weight"},
        {"id": "u-each", "name": "Each", "ratio": 1, "stockUnitType": "Count"},
        {"id": "u-10kg", "name": "10 KG", "ratio": 10, "stockUnitType": "Weight"},
    ]
    #: Potatoes counted each in stocktakes, delivered by the 10 kg box.
    CATALOGUE = [{"id": "item-potato", "name": "Potato", "countingUnitId": "u-each"}]

    def _data(self, unit_id="u-10kg", unit="10 KG"):
        return {
            "invoice_id": "inv-1",
            "lines": [
                {
                    "id": "ld-1",
                    "description": "Dutch Cream Potato",
                    "item_name": "Potato",
                    "linked_item_id": "item-potato",
                    "linked_unit_id": unit_id,
                    "unit": unit,
                    "quantity_received": 1,
                    "unit_cost": 58.0,
                }
            ],
            "suggestions": [],
            "suggestion_actions": [],
            "issues": [],
        }

    def _review(self, data):
        issues: list = []
        suggestions: list = []
        IR.unit_kind_issues(data, suggestions, self.CATALOGUE, self.UNITS, issues)
        IR.link_fixes(data, None, issues, suggestions)
        data["issues"], data["suggestions"] = issues, suggestions
        return issues, suggestions

    def test_a_weight_on_a_product_counted_each_is_a_blocker(self):
        [issue], [fix] = self._review(self._data())
        assert issue["code"] == "unit_kind_mismatch" and issue["blocking"]
        assert issue["gate"] == VA.RECEIVE_UNIT_KIND_MISMATCH
        assert "'10 KG' (a weight)" in issue["message"]
        assert "'Each' (a count)" in issue["message"]
        assert fix["kind"] == "confirm" and fix["gate"] == VA.RECEIVE_UNIT_KIND_MISMATCH
        assert issue["fix"] == [fix["id"]]

    def test_the_same_kind_is_no_blocker(self):
        issues, _ = self._review(self._data(unit_id="u-each", unit="Each"))
        assert issues == []

    def test_the_switch_on_receives_it_off_stops_it(self):
        for on, expect in ((True, "ready"), (False, "needs_review")):
            data = self._data()
            self._review(data)
            settings = {
                "mode": "autopilot",
                **{g: False for g in VA.GATES},
                VA.RECEIVE_UNIT_KIND_MISMATCH: on,
            }
            IR.autopilot_accept(None, None, "v-1", "inv-1", data, settings)
            assert IR.compute_confidence(data) == expect, on
