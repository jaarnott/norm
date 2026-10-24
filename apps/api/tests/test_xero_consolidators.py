"""The four Xero read consolidators, exec'd under the real sandbox namespace.

Same harness as the other consolidator tests: the canonical code from
config/consolidators/ (xero_common.py prepended, exactly as the sync script
installs it) runs with a scripted fake for call_api. Payloads mirror what Xero
returned live on 17 Oct 2026 — envelope keys, DateString dates, Contact
sub-object, the P&L report's Header/Section/Row/SummaryRow layout — so a
field renamed here is a field that would silently vanish in production.
"""

import datetime
import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
COMMON = (_DIR / "xero_common.py").read_text(encoding="utf-8")


def _code(name: str) -> str:
    return COMMON + "\n\n" + (_DIR / name).read_text(encoding="utf-8")


SETTINGS = {
    "connected": True,
    "connector": "xero",
    "venue_id": "v1",
    "settings": {
        "organisation": "Cook Brothers Bars Federal Street Limited",
        "xero_tenant_id": "11829a5f-0a36-438c-b908-54db9d2b5147",
        "xero_country": "NZ",
        "xero_currency": "NZD",
        "xero_short_code": "!cDQvM",
        "xero_mapping": {"accounts": {"default": "51200", "beverage": "51100"}},
    },
}
NOT_CONNECTED = {"connected": False, "connector": "xero", "venue_id": "v9"}
ACCOUNTS = {
    "Accounts": [
        {
            "Code": None,
            "Name": "BNZ Cheque Account",
            "Type": "BANK",
            "Class": "ASSET",
            "Status": "ACTIVE",
            "TaxType": "NONE",
            "AccountID": "a-bank",
        },
        {
            "Code": "51200",
            "Name": "Kitchen Purchases",
            "Type": "DIRECTCOSTS",
            "Class": "EXPENSE",
            "Status": "ACTIVE",
            "TaxType": "INPUT2",
            "AccountID": "a-51200",
        },
        {
            "Code": "51100",
            "Name": "Bar Purchases",
            "Type": "DIRECTCOSTS",
            "Class": "EXPENSE",
            "Status": "ACTIVE",
            "TaxType": "INPUT2",
            "AccountID": "a-51100",
        },
        {
            "Code": "200",
            "Name": "Bar Sales",
            "Type": "SALES",
            "Class": "REVENUE",
            "Status": "ACTIVE",
            "TaxType": "OUTPUT2",
            "AccountID": "a-200",
        },
    ]
}
TAX = {
    "TaxRates": [
        {
            "TaxType": "INPUT2",
            "Name": "15% GST on Expenses",
            "EffectiveRate": 15.0,
            "Status": "ACTIVE",
            "CanApplyToExpenses": True,
            "CanApplyToRevenue": False,
        },
        {
            "TaxType": "NONE",
            "Name": "No GST",
            "EffectiveRate": 0.0,
            "Status": "ACTIVE",
            "CanApplyToExpenses": True,
            "CanApplyToRevenue": True,
        },
        {
            "TaxType": "OLD",
            "Name": "Retired",
            "EffectiveRate": 12.5,
            "Status": "DELETED",
            "CanApplyToExpenses": True,
            "CanApplyToRevenue": True,
        },
    ]
}
CONTACTS = {
    "Contacts": [
        {
            "ContactID": "c-bidfood",
            "Name": "Bidfood",
            "EmailAddress": "ap@bidfood.co.nz",
            "IsSupplier": True,
            "IsCustomer": False,
            "ContactStatus": "ACTIVE",
            "Phones": [
                {
                    "PhoneType": "DEFAULT",
                    "PhoneNumber": "123",
                    "PhoneAreaCode": "09",
                    "PhoneCountryCode": "",
                }
            ],
        },
        {
            "ContactID": "c-merchant",
            "Name": "Merchant Charges",
            "EmailAddress": "",
            "IsSupplier": False,
            "IsCustomer": False,
            "ContactStatus": "ACTIVE",
            "Phones": [],
        },
        {
            "ContactID": "c-cust",
            "Name": "A Customer",
            "EmailAddress": "",
            "IsSupplier": False,
            "IsCustomer": True,
            "ContactStatus": "ACTIVE",
            "Phones": [],
        },
    ]
}


def _inv(number, supplier, due_days, amount_due, status="AUTHORISED", total=None):
    # Today as the test runs, as get_bills reads it: a date taken when the file
    # was imported made "due today" overdue for a run crossing midnight UTC
    # (CI, 24 Oct 2026).
    due = datetime.date.today() + datetime.timedelta(days=due_days)
    return {
        "InvoiceID": f"inv-{number}",
        "Type": "ACCPAY",
        "InvoiceNumber": number,
        "Reference": "",
        "Contact": {"ContactID": f"c-{supplier}", "Name": supplier},
        "Date": "/Date(1757462400000+0000)/",
        "DateString": "2026-09-10T00:00:00",
        "DueDate": "/Date(1757462400000+0000)/",
        "DueDateString": due.isoformat() + "T00:00:00",
        "Status": status,
        "LineAmountTypes": "Inclusive",
        "SubTotal": round((total or amount_due) / 1.15, 2),
        "TotalTax": round((total or amount_due) - (total or amount_due) / 1.15, 2),
        "Total": total or amount_due,
        "AmountDue": amount_due,
        "AmountPaid": (total or amount_due) - amount_due,
        "CurrencyCode": "NZD",
        "HasAttachments": True,
    }


def invoices():
    return {
        "Invoices": [
            _inv("48954", "Arena", 0, 1922.57),
            _inv("INV-1", "Bidfood", -10, 500.0),
            _inv("INV-2", "Bidfood", 5, 250.0),
            _inv("INV-3", "Gilmours", 20, 1000.0),
        ]
    }


PNL = {
    "Reports": [
        {
            "ReportTitles": [
                "Profit & Loss",
                "Cook Brothers Bars Federal Street Limited",
                "1 September 2026 to 30 September 2026",
            ],
            "Rows": [
                {
                    "RowType": "Header",
                    "Cells": [
                        {"Value": ""},
                        {"Value": "30 Sep 26"},
                        {"Value": "30 Aug 26"},
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "Income",
                    "Rows": [
                        {
                            "RowType": "Row",
                            "Cells": [
                                {"Value": "Bar Sales"},
                                {"Value": "151969.69"},
                                {"Value": "207497.07"},
                            ],
                        },
                        {
                            "RowType": "Row",
                            "Cells": [
                                {"Value": "Kitchen Sales"},
                                {"Value": "58602.93"},
                                {"Value": "0.00"},
                            ],
                        },
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Total Income"},
                                {"Value": "210572.62"},
                                {"Value": "207497.07"},
                            ],
                        },
                    ],
                },
                {"RowType": "Section", "Title": "Less Cost of Sales", "Rows": []},
                {
                    "RowType": "Section",
                    "Title": "Cost of Goods",
                    "Rows": [
                        {
                            "RowType": "Row",
                            "Cells": [
                                {"Value": "Bar Purchases"},
                                {"Value": "28488.87"},
                                {"Value": "683.65"},
                            ],
                        },
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Total Cost of Goods"},
                                {"Value": "28488.87"},
                                {"Value": "683.65"},
                            ],
                        },
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "",
                    "Rows": [
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Total Cost of Sales"},
                                {"Value": "104220.01"},
                                {"Value": "81370.71"},
                            ],
                        },
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "",
                    "Rows": [
                        {
                            "RowType": "Row",
                            "Cells": [
                                {"Value": "Gross Profit"},
                                {"Value": "106352.61"},
                                {"Value": "126126.36"},
                            ],
                        },
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "Operating Costs",
                    "Rows": [
                        {
                            "RowType": "Row",
                            "Cells": [
                                {"Value": "Cleaning"},
                                {"Value": "1007.00"},
                                {"Value": "509.35"},
                            ],
                        },
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "",
                    "Rows": [
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Total Operating Expenses"},
                                {"Value": "50000.00"},
                                {"Value": "40000.00"},
                            ],
                        },
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "",
                    "Rows": [
                        {
                            "RowType": "Row",
                            "Cells": [
                                {"Value": "Net Profit"},
                                {"Value": "56352.61"},
                                {"Value": "86126.36"},
                            ],
                        },
                    ],
                },
            ],
        }
    ]
}


class Api:
    def __init__(
        self, responses=None, errors=None, settings=SETTINGS, wrapped_dates=False
    ):
        self.wrapped_dates = wrapped_dates
        self.responses = responses or {}
        self.errors = errors or {}
        self.settings = settings
        self.calls = []

    def call_api(self, connector, action, params=None):
        self.calls.append((connector, action, dict(params or {})))
        if action in self.errors:
            return {"error": self.errors[action]}
        if action == "resolve_dates":
            window = {
                "start": "2026-09-01T07:00:00+12:00",
                "end": "2026-10-01T06:59:59+13:00",
                "description": "September",
            }
            # call_api returns the handler's data UNWRAPPED (verified live);
            # the wrapped form is accepted as well.
            return (
                {"data": {"window": window}}
                if self.wrapped_dates
                else {"window": window}
            )
        if action == "get_connection_settings":
            return self.settings
        return self.responses.get(action, {})

    def params_for(self, action):
        return [p for _, a, p in self.calls if a == action]


def run(name, api, **params):
    namespace = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(_code(name), namespace)
    return namespace["run"](
        {"venue": "Glass Goose", **params}, api.call_api, lambda m: None
    )


class TestAccounts:
    def test_expense_accounts_with_tax_rates(self):
        api = Api({"list_accounts": ACCOUNTS, "list_tax_rates": TAX})
        out = run("get_accounts.py", api)
        assert out["organisation"] == "Cook Brothers Bars Federal Street Limited"
        assert out["currency"] == "NZD"
        assert out["mapping"] == {"accounts": {"default": "51200", "beverage": "51100"}}
        assert [a["code"] for a in out["accounts"]] == ["51100", "51200"]
        assert out["accounts"][0]["tax_type"] == "INPUT2"
        # Retired rates are dropped; the rest say what they apply to.
        assert [t["tax_type"] for t in out["tax_rates"]] == ["INPUT2", "NONE"]
        assert out["tax_rates"][0]["rate"] == 15.0
        # Only active accounts were asked for, with the venue forwarded.
        sent = api.params_for("list_accounts")[0]
        assert sent["venue"] == "Glass Goose"
        assert sent["where"] == 'Status=="ACTIVE"'

    def test_all_kinds_and_a_query(self):
        api = Api({"list_accounts": ACCOUNTS, "list_tax_rates": TAX})
        out = run("get_accounts.py", api, kind="all", query="sales")
        assert [a["name"] for a in out["accounts"]] == ["Bar Sales"]

    def test_a_venue_without_a_connection_is_refused_before_any_xero_call(self):
        """The executor would otherwise fall back to another venue's row and
        answer with another company's books under this venue's name."""
        api = Api({"list_accounts": ACCOUNTS}, settings=NOT_CONNECTED)
        out = run("get_accounts.py", api)
        assert "not connected to Xero" in out["error"]
        assert [a for _, a, _ in api.calls] == ["get_connection_settings"]
        sent = api.params_for("get_connection_settings")[0]
        assert sent == {"connector": "xero", "venue": "Glass Goose"}

    def test_an_upstream_failure_is_an_error_not_an_empty_chart(self):
        api = Api(errors={"list_accounts": "HTTP 401"})
        out = run("get_accounts.py", api)
        assert out == {"error": "Xero accounts unavailable: HTTP 401"}


class TestContacts:
    def test_suppliers_by_default(self):
        api = Api({"list_contacts": CONTACTS})
        out = run("get_contacts.py", api)
        assert [c["name"] for c in out["contacts"]] == ["Bidfood"]
        assert out["contacts"][0]["email"] == "ap@bidfood.co.nz"
        assert out["contacts"][0]["phone"] == "09 123"
        assert out["count"] == 1

    def test_a_query_uses_xeros_search_and_one_page(self):
        api = Api({"list_contacts": CONTACTS})
        run("get_contacts.py", api, query="bid", kind="all")
        sent = api.params_for("list_contacts")
        assert len(sent) == 1
        assert sent[0]["search_term"] == "bid"
        assert sent[0]["page"] == "1"

    def test_pages_until_a_short_page(self):
        full = {
            "Contacts": [
                dict(CONTACTS["Contacts"][0], ContactID=f"c{i}", Name=f"S{i}")
                for i in range(100)
            ]
        }
        api = Api({"list_contacts": full})
        out = run("get_contacts.py", api, limit=500)
        assert len(api.params_for("list_contacts")) == 3  # capped at 3 pages
        assert out["more_available"] is True
        assert out["count"] == 300


class TestBills:
    def test_unpaid_bills_with_summary_and_links(self):
        api = Api({"list_invoices": invoices()})
        out = run("get_bills.py", api)
        sent = api.params_for("list_invoices")[0]
        assert sent["statuses"] == "AUTHORISED,SUBMITTED"
        assert sent["where"] == 'Type=="ACCPAY"'
        assert sent["summary_only"] == "true"
        assert out["organisation"] == "Cook Brothers Bars Federal Street Limited"
        assert out["currency"] == "NZD"
        assert out["summary"]["count"] == 4
        assert out["summary"]["amount_due"] == 3672.57
        assert out["summary"]["overdue"] == 500.0
        assert out["summary"]["overdue_count"] == 1
        # Due today and within a week, not yet overdue.
        assert out["summary"]["due_7_days"] == 2172.57
        # Sorted by due date; the overdue one first.
        assert [b["number"] for b in out["bills"]] == [
            "INV-1",
            "48954",
            "INV-2",
            "INV-3",
        ]
        overdue = out["bills"][0]
        assert overdue["overdue"] is True and overdue["days_overdue"] == 10
        assert (
            overdue["due_date"]
            == (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
        )
        assert overdue["date"] == "2026-09-10"
        assert overdue["url"] == (
            "https://go.xero.com/organisationlogin/default.aspx?shortcode=!cDQvM"
            "&redirecturl=/AccountsPayable/Edit.aspx?InvoiceID=inv-INV-1"
        )
        assert out["by_supplier"][0] == {
            "supplier": "Arena",
            "count": 1,
            "total": 1922.57,
            "amount_due": 1922.57,
            "overdue": 0.0,
        }
        assert [s["supplier"] for s in out["by_supplier"]] == [
            "Arena",
            "Gilmours",
            "Bidfood",
        ]

    def test_supplier_filter_and_period(self):
        api = Api({"list_invoices": invoices()})
        out = run(
            "get_bills.py",
            api,
            supplier="bidfood",
            period="september",
            venue_id="v1",
        )
        assert [b["number"] for b in out["bills"]] == ["INV-1", "INV-2"]
        assert out["filters"]["window"] == "September"
        assert (
            out["filters"]["from"] == "2026-09-01"
            and out["filters"]["to"] == "2026-09-30"  # the window ends 06:59 on 1 Oct
        )
        sent = api.params_for("list_invoices")[0]
        assert (
            sent["where"]
            == 'Type=="ACCPAY"&&Date>=DateTime(2026,9,1)&&Date<=DateTime(2026,9,30)'
        )
        resolve = api.params_for("resolve_dates")[0]
        assert resolve == {"query": "september", "venue_id": "v1"}

    def test_paid_bills_are_never_overdue(self):
        api = Api(
            {
                "list_invoices": {
                    "Invoices": [
                        _inv("P1", "Arena", -30, 0.0, status="PAID", total=100.0)
                    ]
                }
            }
        )
        out = run("get_bills.py", api, status="paid")
        assert api.params_for("list_invoices")[0]["statuses"] == "PAID"
        assert out["bills"][0]["overdue"] is False
        assert out["bills"][0]["amount_paid"] == 100.0
        assert out["summary"]["overdue"] == 0.0

    def test_pages_while_full(self):
        full = {"Invoices": [_inv(f"N{i}", "Arena", i, 1.0) for i in range(100)]}
        api = Api({"list_invoices": full})
        out = run("get_bills.py", api)
        assert len(api.params_for("list_invoices")) == 6
        assert out["summary"]["count"] == 600

    def test_an_upstream_failure_is_an_error(self):
        api = Api(errors={"list_invoices": "HTTP 503"})
        assert run("get_bills.py", api) == {"error": "Xero bills unavailable: HTTP 503"}


class TestProfitAndLoss:
    def test_parses_sections_totals_and_months(self):
        api = Api({"profit_and_loss": PNL})
        out = run(
            "get_profit_and_loss.py",
            api,
            from_date="2026-08-01",
            to_date="2026-09-30",
            periods=2,
        )
        sent = api.params_for("profit_and_loss")[0]
        assert sent["from_date"] == "2026-08-01" and sent["to_date"] == "2026-09-30"
        assert sent["periods"] == "2" and sent["timeframe"] == "MONTH"
        assert out["columns"] == ["30 Sep 26", "30 Aug 26"]
        assert out["totals"]["income"] == [210572.62, 207497.07]
        assert out["totals"]["cost_of_sales"] == [104220.01, 81370.71]
        assert out["totals"]["gross_profit"] == [106352.61, 126126.36]
        assert out["totals"]["net_profit"] == [56352.61, 86126.36]
        titles = [s["title"] for s in out["sections"]]
        assert titles == ["Income", "Cost of Goods", "Operating Costs"]
        assert out["sections"][0]["rows"][0] == {
            "name": "Bar Sales",
            "values": [151969.69, 207497.07],
        }
        assert out["sections"][0]["total"] == [210572.62, 207497.07]
        # Oldest first for charts.
        assert [m["column"] for m in out["months"]] == ["30 Aug 26", "30 Sep 26"]
        assert out["months"][1]["net_profit"] == 56352.61
        # A plain month label for trend axes.
        assert [m["month"] for m in out["months"]] == ["Aug 26", "Sep 26"]

    def test_a_dashboards_timestamp_window_ends_on_the_last_day(self):
        """The dashboard period control sends full timestamps; the window
        ends 06:59 on the 1st of the next period. Xero's toDate is inclusive,
        so that is the previous calendar day (the tile read 1 Oct in)."""
        api = Api({"profit_and_loss": PNL})
        out = run(
            "get_profit_and_loss.py",
            api,
            from_date="2026-09-01T07:00:00+12:00",
            to_date="2026-10-01T06:59:59+13:00",
        )
        sent = api.params_for("profit_and_loss")[0]
        assert sent["from_date"] == "2026-09-01" and sent["to_date"] == "2026-09-30"
        assert out["to"] == "2026-09-30"

    def test_a_plain_to_date_is_inclusive_as_given(self):
        api = Api({"profit_and_loss": PNL})
        run("get_profit_and_loss.py", api, from_date="2026-09-01", to_date="2026-10-01")
        assert api.params_for("profit_and_loss")[0]["to_date"] == "2026-10-01"

    def test_an_afternoon_end_is_that_day(self):
        api = Api({"profit_and_loss": PNL})
        run(
            "get_profit_and_loss.py",
            api,
            from_date="2026-09-01",
            to_date="2026-09-30T18:00:00+12:00",
        )
        assert api.params_for("profit_and_loss")[0]["to_date"] == "2026-09-30"

    def test_a_wrapped_resolver_answer_works_too(self):
        api = Api({"profit_and_loss": PNL}, wrapped_dates=True)
        out = run("get_profit_and_loss.py", api, period="last month")
        assert out["from"] == "2026-09-01" and out["to"] == "2026-09-30"

    def test_a_period_resolves_through_norms_calendar(self):
        api = Api({"profit_and_loss": PNL})
        out = run("get_profit_and_loss.py", api, period="last month")
        assert out["from"] == "2026-09-01" and out["to"] == "2026-09-30"
        assert "periods" not in api.params_for("profit_and_loss")[0]

    def test_no_dates_is_an_error(self):
        out = run("get_profit_and_loss.py", Api({"profit_and_loss": PNL}))
        assert "period" in out["error"]


# ---------------------------------------------------------------------------
# create_bill — the one write
# ---------------------------------------------------------------------------


class _Stop(Exception):
    """What the real preview mode does: stop the run at preview(card)."""


CREATED_CONTACT = {
    "Contacts": [{"ContactID": "c-new", "Name": "Fresh Fish Co", "IsSupplier": True}]
}
CREATED_BILL = {
    "Invoices": [
        {
            "InvoiceID": "inv-created",
            "InvoiceNumber": "FF-100",
            "Status": "DRAFT",
            "SubTotal": 300.0,
            "TotalTax": 45.0,
            "Total": 345.0,
            "CurrencyCode": "NZD",
            "HasErrors": False,
        }
    ]
}
LINES = [
    {
        "description": "Snapper fillets",
        "quantity": 10,
        "unit_amount": 25.0,
        "category": "food",
    },
    {
        "description": "Delivery",
        "quantity": 1,
        "unit_amount": 50.0,
        "gst": "no",
        "account_code": "51100",
    },
]


def run_bill(api, *, capture=None, **params):
    """Run create_bill. With ``capture`` the preview stops the run (like the
    real preview mode) and the card lands in capture[0]."""
    namespace = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    if capture is not None:

        def preview(card, basis=None):
            capture.append((card() if callable(card) else card, basis))
            raise _Stop()

        namespace["preview"] = preview
    exec(_code("create_bill.py"), namespace)
    base = {
        "venue": "Glass Goose",
        "supplier": "Fresh Fish Co",
        "invoice_number": "FF-100",
        "date": "2026-10-01",
        "lines": LINES,
    }
    try:
        return namespace["run"]({**base, **params}, api.call_api, lambda m: None)
    except _Stop:
        return {"stopped_at_preview": True}


class TestCreateBill:
    def _api(self, contacts=None, **responses):
        return Api(
            {
                "list_contacts": contacts if contacts is not None else {"Contacts": []},
                "list_tax_rates": TAX,
                "list_accounts": ACCOUNTS,
                "create_contact": CREATED_CONTACT,
                "create_invoice": CREATED_BILL,
                **responses,
            }
        )

    def test_the_preview_names_everything_the_person_approves(self):
        api = self._api()
        cards: list = []
        out = run_bill(api, capture=cards)
        assert out == {"stopped_at_preview": True}
        card, basis = cards[0]
        assert card["title"] == "Create a bill in Xero"
        assert card["target"] == "Fresh Fish Co — FF-100"
        assert card["venue"] == "Glass Goose"
        fields = {c["field"]: c["after"] for c in card["changes"]}
        assert fields["Supplier"] == "Fresh Fish Co (new contact in Xero)"
        assert fields["Date"] == "2026-10-01"
        assert fields["Due"] == "2026-10-21"  # date + the default 20 days
        # Each line: its account (mapping by category / its own code) and tax type.
        assert (
            fields["Snapper fillets"]
            == "10 × NZD 25.00 → 51200 Kitchen Purchases (INPUT2)"
        )
        assert fields["Delivery"] == "1 × NZD 50.00 → 51100 Bar Purchases (NONE)"
        assert fields["Total ex GST"] == "NZD 300.00"
        assert fields["GST"] == "NZD 37.50"  # 15% on the GST line only
        assert fields["Total"] == "NZD 337.50"
        assert fields["Lands as"] == "Draft"
        # The basis is the payload itself, so a changed mapping voids the card.
        bill = basis["bill"]
        assert bill["Type"] == "ACCPAY"
        assert bill["InvoiceNumber"] == "FF-100"
        assert bill["LineAmountTypes"] == "Exclusive"
        assert bill["Status"] == "DRAFT"
        assert [li["AccountCode"] for li in bill["LineItems"]] == ["51200", "51100"]
        assert [li["TaxType"] for li in bill["LineItems"]] == ["INPUT2", "NONE"]
        # Nothing was written before the preview.
        assert not [
            a for _, a, _ in api.calls if a in ("create_contact", "create_invoice")
        ]

    def test_the_approved_run_creates_the_contact_then_the_bill(self):
        api = self._api()
        out = run_bill(api)
        assert out["created"] is True
        assert out["contact_created"] is True
        assert out["invoice_id"] == "inv-created"
        assert out["status"] == "DRAFT"
        assert out["total"] == 345.0
        assert out["url"].endswith("InvoiceID=inv-created")
        contact = api.params_for("create_contact")[0]
        assert contact["contact"] == {"Name": "Fresh Fish Co", "IsSupplier": True}
        sent = api.params_for("create_invoice")[0]
        assert sent["invoice"]["Contact"] == {"ContactID": "c-new"}
        assert sent["idempotency_key"] == "norm:glass goose:fresh fish co:ff-100:300.0"
        assert len(sent["idempotency_key"]) <= 128

    def test_an_existing_contact_is_matched_by_exact_name(self):
        api = self._api(
            contacts={
                "Contacts": [
                    {
                        "ContactID": "c-ff",
                        "Name": "Fresh Fish Co",
                        "ContactStatus": "ACTIVE",
                    },
                    {
                        "ContactID": "c-ff2",
                        "Name": "Fresh Fish Co (old)",
                        "ContactStatus": "ACTIVE",
                    },
                ]
            }
        )
        out = run_bill(api)
        assert out["contact_id"] == "c-ff" and out["contact_created"] is False
        assert not api.params_for("create_contact")
        assert api.params_for("list_contacts")[0]["search_term"] == "Fresh Fish Co"

    def test_several_loose_matches_ask_for_the_id(self):
        api = self._api(
            contacts={
                "Contacts": [
                    {
                        "ContactID": "a",
                        "Name": "Fresh Fish Co Ltd",
                        "ContactStatus": "ACTIVE",
                    },
                    {
                        "ContactID": "b",
                        "Name": "Fresh Fish Company",
                        "ContactStatus": "ACTIVE",
                    },
                ]
            }
        )
        out = run_bill(api)
        assert "matches 2 Xero contacts" in out["error"]
        assert not api.params_for("create_invoice")

    def test_a_single_loose_match_is_used_with_a_warning(self):
        api = self._api(
            contacts={
                "Contacts": [
                    {
                        "ContactID": "a",
                        "Name": "Fresh Fish Co Ltd",
                        "ContactStatus": "ACTIVE",
                    },
                ]
            }
        )
        cards: list = []
        run_bill(api, capture=cards)
        card, _ = cards[0]
        assert any(
            "matched Xero contact 'Fresh Fish Co Ltd'" in w for w in card["warnings"]
        )

    def test_a_line_with_no_account_anywhere_is_refused(self):
        api = Api(
            {
                "list_contacts": {"Contacts": []},
                "list_tax_rates": TAX,
                "list_accounts": ACCOUNTS,
            },
            settings={
                **SETTINGS,
                "settings": {**SETTINGS["settings"], "xero_mapping": {}},
            },
        )
        out = run_bill(
            api, lines=[{"description": "Mystery", "quantity": 1, "unit_amount": 9.0}]
        )
        assert "no account code for: Mystery" in out["error"]

    def test_xero_validation_errors_come_back_as_an_error(self):
        api = self._api(
            create_invoice={
                "Invoices": [
                    {
                        "HasErrors": True,
                        "ValidationErrors": [
                            {"Message": "Account code '51200' is not valid"}
                        ],
                    }
                ]
            }
        )
        out = run_bill(api)
        assert (
            out["error"] == "Xero rejected the bill: Account code '51200' is not valid"
        )

    def test_inclusive_amounts_back_out_the_gst(self):
        api = self._api()
        cards: list = []
        run_bill(
            api,
            capture=cards,
            line_amount_types="inclusive",
            lines=[
                {
                    "description": "Beer",
                    "quantity": 1,
                    "unit_amount": 115.0,
                    "category": "beverage",
                },
            ],
        )
        fields = {c["field"]: c["after"] for c in cards[0][0]["changes"]}
        assert fields["Total"] == "NZD 115.00"
        assert fields["GST"] == "NZD 15.00"
        assert fields["Total ex GST"] == "NZD 100.00"
        assert fields["Beer"] == "1 × NZD 115.00 → 51100 Bar Purchases (INPUT2)"

    def test_no_tax_is_honoured_and_puts_no_gst_on_a_line(self):
        """The type "NoTax" became "Notax" under .capitalize(), so the bill went
        as Exclusive and an unflagged line took 15% on top (23 Oct 2026)."""
        api = self._api()
        cards: list = []
        run_bill(
            api,
            capture=cards,
            line_amount_types="NoTax",
            lines=[
                {
                    "description": "DJ set",
                    "quantity": 1,
                    "unit_amount": 200.0,
                    "account_code": "51100",
                },
            ],
        )
        card, basis = cards[0]
        fields = {c["field"]: c["after"] for c in card["changes"]}
        assert basis["bill"]["LineAmountTypes"] == "NoTax"
        assert [li["TaxType"] for li in basis["bill"]["LineItems"]] == ["NONE"]
        assert fields["GST"] == "NZD 0.00" and fields["Total"] == "NZD 200.00"
        assert "no GST on these amounts" in card["note"]

    def test_an_unconnected_venue_is_refused_first(self):
        api = Api({}, settings=NOT_CONNECTED)
        out = run_bill(api)
        assert "not connected to Xero" in out["error"]
        assert [a for _, a, _ in api.calls] == ["get_connection_settings"]

    def test_lines_may_arrive_as_a_json_string(self):
        api = self._api()
        out = run_bill(api, lines=json.dumps(LINES))
        assert out["created"] is True


import json  # noqa: E402  (used by the json-string test above)
