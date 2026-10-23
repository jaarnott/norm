"""The three payroll write tools on the ``xero`` connector, exec'd under the
real sandbox namespace with a scripted fake for ``call_api`` (the harness
every consolidator test shares): ``post_wage_journal``,
``post_intercompany_documents``, ``void_payroll_document``.

What they must get right (plan §10): the card shows Norm's stored draft —
never a figure the caller sent; the live-row guard and ``replace``; the
Idempotency-Key on every create; tax types from the tenant's own rates; a
missing contact refusing one group by name; the void reading the status
first; and the ledger call after every write. The route tests in
test_payroll_xero.py run the same code end to end with the real built-ins.
"""

from __future__ import annotations

import pytest

from tests import consolidator_harness as harness

COMMON = harness.source("xero_common")


def _code(name: str) -> str:
    return COMMON + "\n\n" + harness.source(name)


ORG, VENUE, CP = "org-1", "venue-a", "venue-b"
SETTINGS = {
    "connected": True,
    "connector": "xero",
    "venue_id": VENUE,
    "settings": {
        "organisation": "La Zeppa Ltd",
        "xero_tenant_id": "tenant-a",
        "xero_short_code": "!abc",
        "xero_mapping": {},
    },
}
LINES = [
    {
        "key": "wages_payable",
        "description": "Net Wages",
        "account_code": "814",
        "account_name": "Wages Payable - Payroll",
        "side": "CR",
        "amount_cents": 1492220,
        "source": {},
    },
    {
        "key": "payroll_taxes_payable",
        "description": "Payroll Taxes",
        "account_code": "21305",
        "account_name": "Payroll Taxes",
        "side": "CR",
        "amount_cents": 589620,
        "source": {},
    },
    {
        "key": "bar_floor",
        "description": "Bar & Floor Team",
        "account_code": "55150",
        "account_name": "Bar & Floor Team",
        "side": "DR",
        "amount_cents": 2081840,
        "source": {},
    },
]
JOURNAL = {
    "Narration": "Payroll Journal - La Zeppa - WE 18.10.2026",
    "Date": "2026-10-18",
    "Status": "DRAFT",
    "LineAmountTypes": "NoTax",
    "JournalLines": [
        {"LineAmount": -14922.20, "AccountCode": "814", "Description": "Net Wages"},
        {
            "LineAmount": -5896.20,
            "AccountCode": "21305",
            "Description": "Payroll Taxes",
        },
        {
            "LineAmount": 20818.40,
            "AccountCode": "55150",
            "Description": "Bar & Floor Team",
        },
    ],
}
BASIS = "f" * 64


def journal_draft(live=None):
    return {
        "organization_id": ORG,
        "venue_id": VENUE,
        "venue": "La Zeppa",
        "week_start": "2026-10-12",
        "week_ending": "2026-10-18",
        "blockers": [],
        "draft": {
            "id": "draft-1",
            "kind": "journal",
            "lines": LINES,
            "debit_cents": 2081840,
            "credit_cents": 2081840,
            "residual_cents": 0,
            "basis_sha256": BASIS,
        },
        "journal": JOURNAL,
        "narration": JOURNAL["Narration"],
        "live_push": live,
    }


def ico_docs(*, contact=True, live=None):
    return {
        "organization_id": ORG,
        "venue_id": VENUE,
        "venue": "La Zeppa",
        "week_start": "2026-10-12",
        "week_ending": "2026-10-18",
        "blockers": [],
        "wages_account": "21280",
        "leave_account": "55300",
        "uplift_bp": 1200,
        "documents": [
            {
                "draft_id": "d-inv",
                "kind": "ico_invoice",
                "counterparty_venue_id": CP,
                "counterparty": "Glass Goose",
                "contact": {
                    "contact_id": "c-gg",
                    "name": "Cook Brothers Bars Federal Street Limited",
                }
                if contact
                else None,
                "lines": [
                    {
                        "staff_member_id": "dana",
                        "staff_name": "Dana",
                        "hours": 10.0,
                        "amount_cents": 40000,
                        "gratuity": False,
                    },
                    {"key": "leave_uplift", "bp": 1200, "amount_cents": 4800},
                ],
                "wages_cents": 40000,
                "uplift_bp": 1200,
                "uplift_cents": 4800,
                "subtotal_cents": 44800,
                "live_push": live,
            },
            {
                "draft_id": "d-bill",
                "kind": "ico_bill",
                "counterparty_venue_id": CP,
                "counterparty": "Glass Goose",
                "contact": {
                    "contact_id": "c-gg",
                    "name": "Cook Brothers Bars Federal Street Limited",
                },
                "lines": [
                    {
                        "staff_member_id": "eli",
                        "staff_name": "Eli",
                        "hours": 4.0,
                        "amount_cents": 20000,
                        "gratuity": False,
                    },
                    {
                        "staff_member_id": "eli",
                        "staff_name": "Eli — Gratuities",
                        "hours": 0.0,
                        "amount_cents": 4000,
                        "gratuity": True,
                    },
                    {"key": "leave_uplift", "bp": 1200, "amount_cents": 2880},
                ],
                "wages_cents": 24000,
                "uplift_bp": 1200,
                "uplift_cents": 2880,
                "subtotal_cents": 26880,
                "live_push": None,
            },
        ],
    }


NZ_RATES = {
    "TaxRates": [
        {
            "TaxType": "OUTPUT2",
            "Name": "15% GST on Income",
            "EffectiveRate": 15.0,
            "Status": "ACTIVE",
            "CanApplyToRevenue": True,
            "CanApplyToExpenses": False,
        },
        {
            "TaxType": "INPUT2",
            "Name": "15% GST on Expenses",
            "EffectiveRate": 15.0,
            "Status": "ACTIVE",
            "CanApplyToRevenue": False,
            "CanApplyToExpenses": True,
        },
        {
            "TaxType": "NONE",
            "Name": "No GST",
            "EffectiveRate": 0.0,
            "Status": "ACTIVE",
            "CanApplyToRevenue": True,
            "CanApplyToExpenses": True,
        },
        {
            "TaxType": "OLD",
            "Name": "Retired",
            "EffectiveRate": 12.5,
            "Status": "DELETED",
            "CanApplyToRevenue": True,
            "CanApplyToExpenses": True,
        },
    ]
}
AU_RATES = {
    "TaxRates": [
        {
            "TaxType": "OUTPUT",
            "Name": "GST on Income",
            "EffectiveRate": 10.0,
            "Status": "ACTIVE",
            "CanApplyToRevenue": True,
            "CanApplyToExpenses": False,
        },
        {
            "TaxType": "INPUT",
            "Name": "GST on Expenses",
            "EffectiveRate": 10.0,
            "Status": "ACTIVE",
            "CanApplyToRevenue": False,
            "CanApplyToExpenses": True,
        },
    ]
}
ACCOUNTS = {
    "Accounts": [
        {
            "Code": "21280",
            "Name": "Intercompany Wages",
            "Type": "CURRLIAB",
            "Class": "LIABILITY",
            "Status": "ACTIVE",
        },
        {
            "Code": "55300",
            "Name": "Leave & Entitlements",
            "Type": "EXPENSE",
            "Class": "EXPENSE",
            "Status": "ACTIVE",
            "TaxType": "NONE",
        },
    ]
}


class _Stop(Exception):
    """What the real preview mode does: stop the run at preview(card)."""


class Api:
    """A scripted engine: the built-ins answer from here, Xero's endpoints
    from ``responses`` (a dict or a callable of the params)."""

    def __init__(self, responses=None, *, mode="live", draft=None, settings=SETTINGS):
        self.responses = responses or {}
        self.mode = mode
        self.draft = draft
        self.settings = settings
        self.calls = []
        self.recorded = []
        self.voided = []
        self.counter = 0

    def call_api(self, connector, action, params=None):
        params = dict(params or {})
        self.calls.append((connector, action, params))
        if action == "get_connection_settings":
            return self.settings
        if action == "get_mode":
            return {"mode": self.mode}
        if action == "get_journal_draft":
            return self.draft(params) if callable(self.draft) else self.draft
        if action == "record_push":
            if params.get("op") == "void":
                self.voided.append(params)
                return {"voided": True, "push": {"id": params["push_id"]}}
            docs = params.get("documents") or [params]
            self.recorded.extend(docs)
            pushes = []
            for d in docs:
                self.counter += 1
                pushes.append(
                    {
                        "id": f"push-{self.counter}",
                        "idempotency_key": d.get("idempotency_key"),
                    }
                )
            return {
                "recorded": len(pushes),
                "pushes": pushes,
                "push_id": pushes[0]["id"] if len(pushes) == 1 else None,
            }
        r = self.responses.get(action, {})
        return r(params) if callable(r) else r

    def sent(self, action):
        return [p for _, a, p in self.calls if a == action]


def run(name, api, *, capture=None, **params):
    """Exec the tool. With ``capture`` the preview stops the run (as the real
    preview mode does) and the card lands in capture[0]."""
    extra = {}
    if capture is not None:

        def preview(card, basis=None):
            capture.append((card() if callable(card) else card, basis))
            raise _Stop()

        extra["preview"] = preview
    try:
        return harness.call(
            _code(name), {"venue": "La Zeppa", **params}, api.call_api, **extra
        )
    except _Stop:
        return {"stopped_at_preview": True}


MJ_CREATED = {
    "ManualJournals": [
        {
            "ManualJournalID": "mj-1",
            "Status": "DRAFT",
            "Narration": JOURNAL["Narration"],
            "DateString": "2026-10-18T00:00:00",
        }
    ]
}


# ===========================================================================
# post_wage_journal
# ===========================================================================


class TestPostWageJournal:
    def test_the_card_is_the_stored_draft_line_by_line(self):
        api = Api({"create_manual_journal": MJ_CREATED}, draft=journal_draft())
        cards: list = []
        out = run(
            "post_wage_journal",
            api,
            capture=cards,
            week_ending="2026-10-18",
            basis_sha256=BASIS,
        )
        assert out == {"stopped_at_preview": True}
        card, basis = cards[0]
        assert (
            card["title"] == "Post the payroll journal to Xero"
            and card["target"] == "La Zeppa — WE 18.10.2026"
        )
        fields = {c["field"]: c["after"] for c in card["changes"]}
        assert fields["Net Wages → 814 Wages Payable - Payroll"] == "CR $14,922.20"
        assert fields["Bar & Floor Team → 55150 Bar & Floor Team"] == "DR $20,818.40"
        assert (
            fields["Total debits"] == "$20,818.40"
            and fields["Residual"] == "$0.00 (balanced)"
        )
        assert fields["Narration"] == JOURNAL["Narration"] and fields[
            "Lands as"
        ].startswith("Draft")
        assert (
            basis["journal"] == JOURNAL
            and basis["basis_sha256"] == BASIS
            and basis["replace"] is False
        )
        # The draft was read with what the caller saw, nothing written.
        assert api.sent("get_journal_draft")[0]["basis_sha256"] == BASIS
        assert api.sent("create_manual_journal") == [] and api.recorded == []

    def test_the_approved_run_creates_then_records_with_one_key(self):
        api = Api({"create_manual_journal": MJ_CREATED}, draft=journal_draft())
        out = run("post_wage_journal", api, week_ending="2026-10-18")
        assert (
            out["pushed"] is True
            and out["xero_id"] == "mj-1"
            and out["push_id"] == "push-1"
        )
        key = f"norm:payroll-journal:{ORG}:{VENUE}:2026-10-12:{BASIS[:16]}"
        assert out["idempotency_key"] == key and out["ledger_error"] is None
        sent = api.sent("create_manual_journal")[0]
        assert (
            sent["journal"] == JOURNAL
            and sent["idempotency_key"] == key
            and sent["venue"] == "La Zeppa"
        )
        rec = api.recorded[0]
        assert (
            rec["kind"],
            rec["venue_id"],
            rec["week_start"],
            rec["draft_id"],
            rec["xero_id"],
            rec["idempotency_key"],
        ) == ("journal", VENUE, "2026-10-12", "draft-1", "mj-1", key)
        assert (
            rec["request_payload"] == {"journal": JOURNAL}
            and rec["response"]["Status"] == "DRAFT"
        )
        assert out["url"].endswith("/Journal/View.aspx?invoiceID=mj-1")
        assert out["debit"] == 20818.40

    def test_a_live_journal_refuses_unless_replaced_and_replace_voids_first(self):
        live = {"id": "push-old", "xero_id": "mj-old", "xero_status": "DRAFT"}
        api = Api(
            {
                "create_manual_journal": MJ_CREATED,
                "update_manual_journal": {
                    "ManualJournals": [
                        {"ManualJournalID": "mj-old", "Status": "DELETED"}
                    ]
                },
            },
            draft=journal_draft(live),
        )
        out = run("post_wage_journal", api, week_ending="2026-10-18")
        assert "already in Xero" in out["error"] and "replace='true'" in out["error"]
        assert api.sent("create_manual_journal") == []
        out = run("post_wage_journal", api, week_ending="2026-10-18", replace="true")
        assert out["pushed"] is True and out["replaced_push_id"] == "push-old"
        assert api.sent("update_manual_journal")[0] == {
            "venue": "La Zeppa",
            "manual_journal_id": "mj-old",
            "journal": {"Status": "DELETED"},
        }
        assert (
            api.voided[0]["push_id"] == "push-old"
            and api.voided[0]["xero_status"] == "DELETED"
        )
        # A POSTED journal is voided, not deleted.
        api = Api(
            {
                "create_manual_journal": MJ_CREATED,
                "update_manual_journal": {"ManualJournals": [{"Status": "VOIDED"}]},
            },
            draft=journal_draft({**live, "xero_status": "POSTED"}),
        )
        run("post_wage_journal", api, week_ending="2026-10-18", replace="true")
        assert api.sent("update_manual_journal")[0]["journal"] == {"Status": "VOIDED"}

    def test_a_push_after_a_void_or_a_replace_carries_a_new_key(self):
        """Xero answers a repeated Idempotency-Key with the ORIGINAL document
        — the one just voided — so the attempt number rides on the key."""
        api = Api(
            {"create_manual_journal": MJ_CREATED},
            draft={**journal_draft(), "pushes_before": 2},
        )
        out = run("post_wage_journal", api, week_ending="2026-10-18")
        assert (
            out["idempotency_key"]
            == f"norm:payroll-journal:{ORG}:{VENUE}:2026-10-12:{BASIS[:16]}:r2"
        )
        docs = ico_docs()
        docs["documents"][0]["pushes_before"] = 1
        api = Api(
            {
                "list_tax_rates": NZ_RATES,
                "list_accounts": ACCOUNTS,
                "create_invoice": _created_invoice,
            },
            draft=docs,
        )
        run("post_intercompany_documents", api, week_ending="2026-10-18")
        keys = [s["idempotency_key"] for s in api.sent("create_invoice")]
        assert keys[0].endswith(f":invoice:{CP}:r1") and keys[1].endswith(f":bill:{CP}")

    def test_double_approve_is_one_journal(self):
        """The second run finds the ledger's live row (the partial unique) and
        refuses; Xero is asked once."""
        state = {"live": None}
        api = Api(
            {"create_manual_journal": MJ_CREATED},
            draft=lambda p: journal_draft(state["live"]),
        )
        first = run("post_wage_journal", api, week_ending="2026-10-18")
        state["live"] = {
            "id": first["push_id"],
            "xero_id": first["xero_id"],
            "xero_status": "DRAFT",
        }
        second = run("post_wage_journal", api, week_ending="2026-10-18")
        assert "already in Xero" in second["error"]
        assert len(api.sent("create_manual_journal")) == 1

    def test_refusals_come_from_the_engine_not_the_tool(self):
        assert (
            "shadow"
            in run(
                "post_wage_journal",
                Api(mode="shadow", draft=journal_draft()),
                week_ending="2026-10-18",
            )["error"]
        )
        assert (
            run(
                "post_wage_journal",
                Api(draft={"error": "unattended run"}),
                week_ending="2026-10-18",
            )["error"]
            == "unattended run"
        )
        assert run(
            "post_wage_journal",
            Api(draft={"error": "the draft's figures changed since you saw them"}),
            week_ending="2026-10-18",
            basis_sha256="0" * 64,
        )["error"].startswith("the draft's figures")
        assert (
            "not connected"
            in run(
                "post_wage_journal",
                Api(settings={"connected": False, "connector": "xero"}),
                week_ending="2026-10-18",
            )["error"]
        )

    def test_xeros_validation_errors_come_back_and_nothing_is_recorded(self):
        rejected = {
            "ManualJournals": [
                {
                    "HasErrors": True,
                    "ValidationErrors": [
                        {"Message": "The total debits must equal total credits"}
                    ],
                }
            ]
        }
        api = Api({"create_manual_journal": rejected}, draft=journal_draft())
        out = run("post_wage_journal", api, week_ending="2026-10-18")
        assert "total debits must equal" in out["error"] and api.recorded == []
        api = Api(
            {"create_manual_journal": {"error": "Xero 503"}}, draft=journal_draft()
        )
        assert (
            "Xero 503"
            in run("post_wage_journal", api, week_ending="2026-10-18")["error"]
        )

    def test_a_ledger_failure_after_the_create_is_reported_not_hidden(self):
        api = Api({"create_manual_journal": MJ_CREATED}, draft=journal_draft())
        api.call_api_real = api.call_api

        def call_api(connector, action, params=None):
            if action == "record_push":
                api.calls.append((connector, action, dict(params or {})))
                return {"error": "A live Xero document already exists for that week"}
            return api.call_api_real(connector, action, params)

        out = harness.call(
            _code("post_wage_journal"),
            {"venue": "La Zeppa", "week_ending": "2026-10-18"},
            call_api,
        )
        assert (
            out["pushed"] is True
            and out["push_id"] is None
            and "already exists" in out["ledger_error"]
        )


# ===========================================================================
# post_intercompany_documents
# ===========================================================================


def _created_invoice(params):
    inv = params["invoice"]
    n = 1 if inv["Type"] == "ACCREC" else 2
    return {
        "Invoices": [
            {
                "InvoiceID": f"inv-{n}",
                "InvoiceNumber": f"INV-{n}",
                "Status": "DRAFT",
                "SubTotal": 448.0,
                "TotalTax": 67.2,
                "Total": 515.2,
            }
        ]
    }


class TestPostIntercompanyDocuments:
    def _api(self, docs=None, rates=NZ_RATES, **responses):
        return Api(
            {
                "list_tax_rates": rates,
                "list_accounts": ACCOUNTS,
                "create_invoice": _created_invoice,
                **responses,
            },
            draft=docs or ico_docs(),
        )

    def test_the_card_lists_every_document_and_line_with_the_tenants_tax_types(self):
        api = self._api()
        cards: list = []
        assert run(
            "post_intercompany_documents", api, capture=cards, week_ending="2026-10-18"
        ) == {"stopped_at_preview": True}
        card, basis = cards[0]
        fields = {c["field"]: c["after"] for c in card["changes"]}
        # The leave line takes its account's default (NONE here), so GST is on the wages only.
        assert (
            fields["Invoice to Glass Goose"]
            == "Cook Brothers Bars Federal Street Limited · 1 people · wages $400.00 · leave $48.00 · GST $60.00 · total $508.00"
        )
        assert (
            fields["Bill from Glass Goose"]
            == "Cook Brothers Bars Federal Street Limited · 2 people · wages $240.00 · leave $28.80 · GST $40.32 · total $309.12"
        )
        assert (
            fields["↳ Dana — 10.00h"] == "$400.00 → 21280 Intercompany Wages (OUTPUT2)"
        )
        assert fields["↳ Eli — 4.00h"] == "$200.00 → 21280 Intercompany Wages (INPUT2)"
        assert (
            fields["↳ Eli — Gratuities — 0.00h"]
            == "$40.00 → 21280 Intercompany Wages (INPUT2)"
        )
        # The leave line goes to an EXPENSE account: on the invoice it carries
        # no tax type (Xero applies the account's default), on the bill INPUT2.
        leave = [
            c["after"]
            for c in card["changes"]
            if c["field"] == "↳ Leave & Entitlements (12%)"
        ]
        assert leave == [
            "$48.00 → 55300 Leave & Entitlements (account default NONE)",
            "$28.80 → 55300 Leave & Entitlements (INPUT2)",
        ]
        assert any("account's default rate" in x for x in card["warnings"])
        invoices = {d["invoice"]["Type"]: d["invoice"] for d in basis["documents"]}
        assert invoices["ACCREC"]["LineItems"][0] == {
            "Description": "Dana — 10.00h",
            "Quantity": 1,
            "UnitAmount": 400.0,
            "AccountCode": "21280",
            "TaxType": "OUTPUT2",
        }
        assert (
            "TaxType" not in invoices["ACCREC"]["LineItems"][1]
            and invoices["ACCPAY"]["LineItems"][2]["TaxType"] == "INPUT2"
        )
        assert (
            invoices["ACCREC"]["Reference"] == "ICO Wages WE 18.10.2026"
            and "InvoiceNumber" not in invoices["ACCREC"]
        )
        assert (
            invoices["ACCPAY"]["Reference"]
            == invoices["ACCPAY"]["InvoiceNumber"]
            == "ICO Wages WE 18.10.2026 — La Zeppa"
        )
        assert (
            invoices["ACCREC"]["Date"] == "2026-10-18"
            and invoices["ACCREC"]["DueDate"] == "2026-11-01"
        )
        assert (
            invoices["ACCREC"]["Status"] == "DRAFT"
            and invoices["ACCREC"]["LineAmountTypes"] == "Exclusive"
        )
        assert api.sent("create_invoice") == []

    def test_an_australian_tenant_gets_its_own_ten_percent(self):
        api = self._api(rates=AU_RATES)
        cards: list = []
        run("post_intercompany_documents", api, capture=cards, week_ending="2026-10-18")
        fields = {c["field"]: c["after"] for c in cards[0][0]["changes"]}
        assert (
            fields["↳ Dana — 10.00h"].endswith("(OUTPUT)")
            and "GST $40.00" in fields["Invoice to Glass Goose"]
        )

    def test_the_approved_run_creates_both_with_their_keys_and_records_once(self):
        api = self._api()
        out = run("post_intercompany_documents", api, week_ending="2026-10-18")
        assert [p["kind"] for p in out["pushed"]] == ["ico_invoice", "ico_bill"]
        assert (
            out["refused"] == []
            and out["skipped"] == []
            and out["errors"] == []
            and out["ledger_error"] is None
        )
        keys = [s["idempotency_key"] for s in api.sent("create_invoice")]
        assert keys == [
            f"norm:ico:{ORG}:{VENUE}:2026-10-12:invoice:{CP}",
            f"norm:ico:{ORG}:{VENUE}:2026-10-12:bill:{CP}",
        ]
        assert len([c for c in api.calls if c[1] == "record_push"]) == 1
        assert [d["xero_id"] for d in api.recorded] == ["inv-1", "inv-2"]
        assert (
            api.recorded[0]["counterparty_venue_id"] == CP
            and api.recorded[0]["draft_id"] == "d-inv"
        )
        assert out["pushed"][0]["push_id"] == "push-1" and out["pushed"][1][
            "url"
        ].endswith("InvoiceID=inv-2")

    def test_a_missing_contact_refuses_that_group_by_name_and_the_rest_post(self):
        api = self._api(ico_docs(contact=False))
        cards: list = []
        run("post_intercompany_documents", api, capture=cards, week_ending="2026-10-18")
        card = cards[0][0]
        assert [
            c["field"] for c in card["changes"] if not c["field"].startswith("↳")
        ] == ["Bill from Glass Goose"]
        assert any(
            "no Xero contact mapped for Glass Goose" in x for x in card["warnings"]
        )
        out = run("post_intercompany_documents", api, week_ending="2026-10-18")
        assert [p["kind"] for p in out["pushed"]] == ["ico_bill"]
        assert (
            out["refused"][0]["kind"] == "ico_invoice"
            and "Glass Goose" in out["refused"][0]["reason"]
        )

    def test_a_pushed_group_is_skipped_unless_replaced_then_voided_and_recreated(self):
        live = {"id": "push-old", "xero_id": "inv-old", "xero_status": "DRAFT"}
        api = self._api(
            ico_docs(live=live),
            update_invoice=lambda p: {
                "Invoices": [
                    {"InvoiceID": p["invoice_id"], "Status": p["invoice"]["Status"]}
                ]
            },
        )
        out = run("post_intercompany_documents", api, week_ending="2026-10-18")
        assert [p["kind"] for p in out["pushed"]] == ["ico_bill"] and out["skipped"][0][
            "xero_id"
        ] == "inv-old"
        api = self._api(
            ico_docs(live=live),
            update_invoice=lambda p: {
                "Invoices": [
                    {"InvoiceID": p["invoice_id"], "Status": p["invoice"]["Status"]}
                ]
            },
        )
        out = run(
            "post_intercompany_documents", api, week_ending="2026-10-18", replace="true"
        )
        assert [p["kind"] for p in out["pushed"]] == ["ico_invoice", "ico_bill"]
        assert api.sent("update_invoice") == [
            {
                "venue": "La Zeppa",
                "invoice_id": "inv-old",
                "invoice": {"Status": "DELETED"},
            }
        ]
        assert out["voided"] == [
            {"push_id": "push-old", "xero_id": "inv-old", "status": "DELETED"}
        ]
        assert api.voided[0]["push_id"] == "push-old"

    def test_nothing_to_push_and_the_refusals(self):
        empty = {**ico_docs(), "documents": []}
        out = run(
            "post_intercompany_documents", self._api(empty), week_ending="2026-10-18"
        )
        assert out["pushed"] == [] and "nothing to push" in out["note"]
        assert (
            "shadow"
            in run(
                "post_intercompany_documents",
                Api(mode="shadow", draft=ico_docs()),
                week_ending="2026-10-18",
            )["error"]
        )
        no_account = {**ico_docs(), "wages_account": None}
        assert (
            "intercompany wages account"
            in run(
                "post_intercompany_documents",
                self._api(no_account),
                week_ending="2026-10-18",
            )["error"]
        )
        api = self._api(list_tax_rates={"error": "Xero 500"})
        assert (
            "tax rates"
            in run("post_intercompany_documents", api, week_ending="2026-10-18")[
                "error"
            ]
        )

    def test_a_rejected_document_is_an_error_row_and_the_other_still_lands(self):
        def create(params):
            if params["invoice"]["Type"] == "ACCREC":
                return {
                    "Invoices": [
                        {
                            "HasErrors": True,
                            "ValidationErrors": [
                                {
                                    "Message": "The TaxType code 'OUTPUT2' cannot be used with account code '52100'"
                                }
                            ],
                        }
                    ]
                }
            return _created_invoice(params)

        api = self._api(create_invoice=create)
        out = run("post_intercompany_documents", api, week_ending="2026-10-18")
        assert [p["kind"] for p in out["pushed"]] == ["ico_bill"]
        assert (
            out["errors"][0]["kind"] == "ico_invoice"
            and "OUTPUT2" in out["errors"][0]["error"]
        )
        assert [d["kind"] for d in api.recorded] == ["ico_bill"]


# ===========================================================================
# void_payroll_document
# ===========================================================================


def push_view(kind="journal", xero_id="mj-1", voided_at=None):
    return {
        "organization_id": ORG,
        "push": {
            "id": "push-1",
            "kind": kind,
            "venue_id": VENUE,
            "week_start": "2026-10-12",
            "xero_id": xero_id,
            "xero_number": "INV-9" if kind != "journal" else None,
            "xero_status": "DRAFT",
            "voided_at": voided_at,
        },
        "draft": None,
    }


class TestVoidPayrollDocument:
    def _api(self, view, status, **responses):
        return Api(
            {
                "get_manual_journal": {
                    "ManualJournals": [{"ManualJournalID": "mj-1", "Status": status}]
                },
                "get_invoice": {
                    "Invoices": [
                        {
                            "InvoiceID": "inv-1",
                            "InvoiceNumber": "INV-9",
                            "Status": status,
                        }
                    ]
                },
                "update_manual_journal": lambda p: {
                    "ManualJournals": [
                        {
                            "ManualJournalID": p["manual_journal_id"],
                            "Status": p["journal"]["Status"],
                        }
                    ]
                },
                "update_invoice": lambda p: {
                    "Invoices": [
                        {"InvoiceID": p["invoice_id"], "Status": p["invoice"]["Status"]}
                    ]
                },
                **responses,
            },
            draft=view,
        )

    def test_the_status_is_read_first_and_a_draft_journal_is_deleted(self):
        api = self._api(push_view(), "DRAFT")
        cards: list = []
        assert run("void_payroll_document", api, capture=cards, push_id="push-1") == {
            "stopped_at_preview": True
        }
        card, basis = cards[0]
        fields = {c["field"]: c["after"] for c in card["changes"]}
        assert (
            fields["Status now"] == "DRAFT"
            and fields["What happens"] == "set DELETED in Xero (it is DRAFT)"
        )
        assert basis == {
            "push_id": "push-1",
            "xero_id": "mj-1",
            "status_before": "DRAFT",
            "target": "DELETED",
        }
        assert api.sent("update_manual_journal") == []
        # The venue comes from the ledger row, not the caller.
        assert api.sent("get_connection_settings")[0] == {
            "connector": "xero",
            "venue_id": VENUE,
        }
        out = run("void_payroll_document", api, push_id="push-1")
        assert (
            out["voided"] is True
            and out["xero_status_after"] == "DELETED"
            and out["sent"] is True
        )
        assert api.sent("update_manual_journal")[0]["journal"] == {"Status": "DELETED"}
        assert api.voided[0] == {
            "venue_id": VENUE,
            "op": "void",
            "push_id": "push-1",
            "xero_status": "DELETED",
        }

    def test_a_posted_journal_and_an_authorised_bill_are_voided(self):
        out = run(
            "void_payroll_document", self._api(push_view(), "POSTED"), push_id="push-1"
        )
        assert out["xero_status_after"] == "VOIDED"
        api = self._api(push_view("ico_bill", "inv-1"), "AUTHORISED")
        out = run("void_payroll_document", api, push_id="push-1")
        assert out["xero_status_after"] == "VOIDED" and out["number"] == "INV-9"
        assert api.sent("update_invoice")[0] == {
            "venue_id": VENUE,
            "invoice_id": "inv-1",
            "invoice": {"Status": "VOIDED"},
        }
        api = self._api(push_view("entertainer_bill", "inv-1"), "SUBMITTED")
        assert (
            run("void_payroll_document", api, push_id="push-1")["xero_status_after"]
            == "DELETED"
        )

    def test_a_paid_bill_refuses_and_a_gone_document_is_recorded_only(self):
        out = run(
            "void_payroll_document",
            self._api(push_view("ico_bill", "inv-1"), "PAID"),
            push_id="push-1",
        )
        assert "PAID" in out["error"] and "reverse the payment" in out["error"]
        api = self._api(push_view(), "VOIDED")
        out = run("void_payroll_document", api, push_id="push-1")
        assert (
            out["voided"] is True
            and out["sent"] is False
            and api.sent("update_manual_journal") == []
        )
        assert api.voided[0]["xero_status"] == "VOIDED"
        api = self._api(
            push_view("ico_invoice", "inv-1"),
            "DRAFT",
            get_invoice={"error": "HTTP 404: not found"},
        )
        out = run("void_payroll_document", api, push_id="push-1")
        assert out["xero_status_before"] == "DELETED" and out["sent"] is False

    def test_the_refusals(self):
        assert (
            "push_id is required"
            in run("void_payroll_document", Api(), venue="La Zeppa")["error"]
        )
        assert (
            "already voided"
            in run(
                "void_payroll_document",
                self._api(push_view(voided_at="2026-10-20T00:00:00+00:00"), "DRAFT"),
                push_id="push-1",
            )["error"]
        )
        assert (
            "shadow"
            in run(
                "void_payroll_document",
                Api({"get_manual_journal": {}}, mode="shadow", draft=push_view()),
                push_id="push-1",
            )["error"]
        )
        assert (
            "who is asking"
            in run(
                "void_payroll_document",
                Api(draft={"error": "Norm couldn't tell who is asking"}),
                push_id="push-1",
            )["error"]
        )


@pytest.mark.parametrize(
    "name",
    ["post_wage_journal", "post_intercompany_documents", "void_payroll_document"],
)
def test_the_installed_code_is_what_the_sync_script_ships(name):
    import scripts.sync_xero_connector as sync

    row = next(t for t in sync._tool_rows() if t["action"] == name)
    assert row["consolidator_config"]["function_code"] == _code(name)
    assert row["effect"] == "write" and row["approval"]["default"] == "ask"
    assert row["requires"] == ["payroll:write"]
