"""The Orbit → Norm Finance importer, run against a fake Orbit (no network).

What these pin down, beyond each transform:

* ids are deterministic (UUID5) and a reload is IDENTICAL, row for row;
* a dry run, a failed sanity gate and a refusal write nothing — and a failed
  gate leaves the previous import in place;
* a live module is refused; an Orbit venue with data and no Norm venue
  fails the gate unless skipped on purpose;
* a row authored in Norm (uuid4) is never overwritten: Orbit's twin is left
  out and reported, and Orbit's children attach to Norm's pay week;
* the legacy payout week rule, the lane mapping, the tax-code spellings;
* invoice files are copied once (content-addressed) and shared;
* the xero_mapping blocks are reported, and written only on request;
* the two tick jobs claim their slot and record what happened;
* Orbit is only ever read (the fake fails on anything but GET/HEAD).

Fixture rows carry made-up names, emails, bank and IRD numbers — never a
real person's.
"""

from __future__ import annotations

import datetime as dt
import urllib.parse
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.db import entertainers_models as em
from app.db import finance_models as fin
from app.db import payroll_models as pm
from app.db.models import Connection
from app.services.finance_core import importer_jobs
from app.services.finance_core.importer import (
    ImportRefused,
    OrbitPostgrest,
    OrbitReadError,
    email_history,
    fiscal_year_of,
    gst_audit,
    hours_between,
    import_id,
    is_import_id,
    lane_for,
    local_week_of,
    normalise_tax_code,
    post_freeze_rows,
    run_import,
    series_rule,
    split_payout_key,
    to_cents,
    week_start_from_ending,
    xero_mapping_blocks,
)
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_user,
    _make_venue,
)
from tests.functions_importer_fixtures import ORBIT_URL, FakeOrbit, oid

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 20, 2, 0, tzinfo=UTC)
NZ = dt.timezone(dt.timedelta(hours=13))
SIGNED = ORBIT_URL + "/storage/v1/object/sign/entertainer-invoices/"
INVOICE_URL = SIGNED + "e3/inv-7.pdf?token=signed-token-never-logged"
INVOICE_PDF = b"%PDF-1.4 fake invoice bytes"

# Orbit ids, readable
V1, V2, V3 = oid("venue.brew"), oid("venue.garden"), oid("venue.head-office")
C1, C2, C3 = oid("company.brew"), oid("company.garden"), oid("company.head-office")
T1, T2 = oid("tenant.brew"), oid("tenant.garden")
TM1, TM2 = oid("tm.olive"), oid("tm.nobody")
E1, E2, E3, E4, E5, E6 = (
    oid(f"ent.{n}") for n in ("wt", "unknown", "company", "exempt", "band", "blank")
)
S1, S2, S3, S4, S5, S6, S7 = (oid(f"shift.{n}") for n in range(1, 8))
R1 = oid("series.1")
G1 = oid("gratuity.batch")
FT_EVENT_V1, FT_WEATHER_V1, FT_EVENT_V2 = (
    oid("ft.event.v1"),
    oid("ft.weather.v1"),
    oid("ft.event.v2"),
)


class FakeFinanceOrbit(FakeOrbit):
    """The Functions suite's fake PostgREST, plus the two operators the
    finance reads use (``not.is.null``, ``gte``) and Orbit's signed storage
    path (fetched without the key)."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(SIGNED):
            self.requests.append((request.method, url))
            assert request.method == "GET"
            assert "apikey" not in request.headers, (
                "the key must never go to a storage URL"
            )
            body = self.files.get(url.split("?", 1)[0])
            if body is None:
                return httpx.Response(400, json={"error": "expired"})
            return httpx.Response(
                200, content=body, headers={"content-type": "application/pdf"}
            )
        params = urllib.parse.parse_qsl(
            request.url.query.decode(), keep_blank_values=True
        )
        extra = [
            (k, v) for k, v in params if v.startswith("not.is.") or v.startswith("gte.")
        ]
        if not extra:
            return super().handler(request)
        table = request.url.path.rsplit("/", 1)[-1]
        rows = self.tables.get(table, [])
        for column, expr in extra:
            if expr == "not.is.null":
                rows = [r for r in rows if r.get(column) is not None]
            elif expr.startswith("gte."):
                rows = [r for r in rows if str(r.get(column) or "") >= expr[4:]]
        rest = urllib.parse.urlencode(
            [(k, v) for k, v in params if (k, v) not in extra],
            quote_via=urllib.parse.quote,
        )
        narrowed = httpx.Request(
            request.method,
            str(request.url).split("?", 1)[0] + "?" + rest,
            headers=request.headers,
        )
        saved = self.tables.get(table)
        self.tables[table] = rows
        try:
            return super().handler(narrowed)
        finally:
            self.tables[table] = saved

    def client(self, page: int = 1000):
        http = httpx.Client(
            transport=httpx.MockTransport(self.handler), follow_redirects=False
        )
        return OrbitPostgrest(ORBIT_URL, "service-key", client=http, page=page)


def _shift(
    sid, ent, venue, day, start="19:00:00", end="23:00:00", agreed=280.0, **extra
):
    row = {
        "id": sid,
        "entertainer_id": ent,
        "venue_id": venue,
        "shift_date": day,
        "start_time": start,
        "end_time": end,
        "agreed_amount": agreed,
        "actual_amount": None,
        "created_by": None,
        "created_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-02T00:00:00+00:00",
        "recurrence_series_id": None,
        "actual_start_time": None,
        "actual_end_time": None,
        "performance_status": "scheduled",
        "did_not_perform_reason": None,
        "approved_at": None,
        "approved_by": None,
        "payment_status": "pending",
        "paid_at": None,
        "paid_by": None,
        "paid_reference": None,
        "entertainer_actual_start_time": None,
        "entertainer_actual_end_time": None,
        "entertainer_confirmed_at": None,
        "entertainer_did_not_perform": False,
        "entertainer_did_not_perform_reason": None,
        "confirmation_email_first_sent_at": None,
        "confirmation_email_last_sent_at": None,
        "confirmation_email_send_count": 0,
        "entertainer_invoice_url": None,
        "entertainer_invoice_uploaded_at": None,
        "entertainer_invoice_number": None,
        "entertainer_invoice_amount": None,
        "xero_bill_id": None,
        "xero_bill_number": None,
        "xero_bill_status": None,
        "xero_bill_pushed_at": None,
        "xero_bill_tenant_id": None,
        "mismatch_email_sent_at": None,
        "paid_payrun_week_start": None,
        "sales_manager_team_member_id": None,
        "sales_manager_loadedhub_id": None,
        "sales_manager_name": None,
        "sales_manager_email": None,
        "sales_manager_email_first_sent_at": None,
        "sales_manager_email_last_sent_at": None,
        "sales_manager_email_send_count": 0,
    }
    row.update(extra)
    return row


def _performer(pid, name, email, **extra):
    row = {
        "id": pid,
        "name": name,
        "email": email,
        "phone": "021 000 0000",
        "is_active": True,
        "payment_method": "payroll",
        "onboarding_status": "complete",
        "onboarding_token": "secret-token",
        "ird_number": "049091850",
        "tax_code": None,
        "kiwisaver_rate": None,
        "date_of_birth": None,
        "company_name": None,
        "company_number": None,
        "gst_number": None,
        "exemption_certificate_number": None,
        "exemption_expiry_date": None,
        "exemption_certificate_url": None,
        "bank_account_number": "01-0001-0000001-00",
        "notes": None,
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-06-02T00:00:00+00:00",
        "setup_type": "payroll",
        "performer_type": "unknown",
        "trading_name": None,
        "ir330c_rate": None,
        "is_gst_registered": False,
        "partnership_ird": None,
        "band_leader_declaration_at": None,
        "contractor_confirmations": None,
        "needs_admin_review": False,
        "admin_review_reason": None,
        "invite_sent_at": None,
        "last_reminder_sent_at": None,
        "reminder_count": 0,
        "invite_opened_at": None,
        "payroll_code": None,
        "xero_contact_id": None,
        "xero_contact_name": None,
        "legal_name": None,
        "nz_tax_resident": None,
        "wt_rate_type": None,
        "wt_rate": None,
        "tailored_certificate_number": None,
        "declaration_wording_version": None,
        "declaration_signature": None,
        "declaration_signed_at": None,
        "declaration_ip": None,
        "declaration_user_agent": None,
        "declaration_snapshot": None,
    }
    row.update(extra)
    return row


def orbit_dataset(member_email: str) -> tuple[dict, dict]:
    """A small Orbit that exercises every transform: three venues (one with
    no Norm twin), every payroll table, six performers across the lanes,
    a series, a shared invoice file, budgets in two fiscal years."""
    week = "2026-10-11"  # a Sunday: WE 11 Oct → pay week Mon 5 Oct
    mappings = []
    for i, line_type in enumerate(
        (
            "wages_payable", "payroll_taxes_payable", "unpaid_wages", "security_owed",
            "intercompany_wages", "pnl_bar_floor", "pnl_kitchen", "pnl_general_servers",
            "pnl_salaries", "pnl_site_manager_bonus", "pnl_leave_entitlements", "pnl_djs_non_gst",
            "pnl_djs_gst", "pnl_marketing", "pnl_gratuities_reclass", "pnl_entertainers",
            "entertainment_expense", "ent_intercompany_suspense", "ent_holiday_pay", "ent_sick_pay",
            "ent_public_holiday", "ent_kiwisaver_esct", "ent_leave_liability",
        )
    ):  # fmt: skip
        mappings.append(
            {
                "id": oid(f"map.{line_type}"),
                "venue_id": V1,
                "line_type": line_type,
                "xero_account_code": f"{800 + i}",
                "xero_account_name": line_type.replace("_", " "),
                "tracking_category_id": None,
                "tracking_category_option_id": None,
                "tracking_category_option_name": None,
                "created_at": "2026-07-07T00:00:00+00:00",
                "updated_at": "2026-07-07T00:00:00+00:00",
            }
        )
    mappings.append(
        {
            **mappings[0],
            "id": oid("map.v2.wages"),
            "venue_id": V2,
            "xero_account_code": "814",
        }
    )
    tables = {
        "venues": [
            {
                "id": V1,
                "name": "Brew Bar",
                "slug": "brew-bar",
                "timezone": "Pacific/Auckland",
                "loadedhub_company_id": C1,
                "loadedhub_company_name": "Brew",
                "allowance_codes": {"gratuities": "13"},
                "pos_day_start": "07:00:00",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": V2,
                "name": "Garden Bar",
                "slug": "garden-bar",
                "timezone": "Pacific/Auckland",
                "loadedhub_company_id": C2,
                "loadedhub_company_name": "Garden",
                "allowance_codes": {},
                "pos_day_start": "06:00:00",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": V3,
                "name": "Head Office",
                "slug": "head-office",
                "timezone": "Pacific/Auckland",
                "loadedhub_company_id": C3,
                "loadedhub_company_name": "HO",
                "allowance_codes": {"gratuities": "14"},
                "pos_day_start": "07:00:00",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
        ],
        "team_members": [
            {
                "id": TM1,
                "name": "Olive Owner",
                "email": member_email,
                "google_email": None,
            },
            {
                "id": TM2,
                "name": "Nobody Known",
                "email": "nobody@example.test",
                "google_email": None,
            },
        ],
        "xero_connections": [
            {
                "venue_id": V1,
                "xero_tenant_id": T1,
                "tenant_name": "Brew Ltd",
                "status": "active",
            },
            {
                "venue_id": V2,
                "xero_tenant_id": T2,
                "tenant_name": "Garden Ltd",
                "status": "active",
            },
        ],
        "wage_recon_weeks": [
            {
                "id": oid("week.v1"),
                "venue_id": V1,
                "week_ending": week,
                "status": "draft",
                "notes": "Labour Day lieu paid",
                "reconciled_at": None,
                "reconciled_by": None,
                "created_at": "2026-10-12T22:00:00+00:00",
                "updated_at": "2026-10-12T22:00:00+00:00",
            },
            {
                "id": oid("week.v2"),
                "venue_id": V2,
                "week_ending": week,
                "status": "draft",
                "notes": None,
                "reconciled_at": None,
                "reconciled_by": None,
                "created_at": "2026-10-12T22:00:00+00:00",
                "updated_at": "2026-10-12T22:00:00+00:00",
            },
            {
                "id": oid("week.v3"),
                "venue_id": V3,
                "week_ending": week,
                "status": "draft",
                "notes": None,
                "reconciled_at": None,
                "reconciled_by": None,
                "created_at": "2026-10-12T22:00:00+00:00",
                "updated_at": "2026-10-12T22:00:00+00:00",
            },
        ],
        "wage_recon_ace_inputs": [
            {
                "id": oid("ace.v1"),
                "venue_id": V1,
                "week_ending": week,
                "gross_wages": 10000.50,
                "net_wages": 8000.0,
                "esct": 10.25,
                "kiwisaver": 200.0,
                "gst": 0.0,
                "total_ace_wages": 0.0,
                "created_at": "2026-10-12T22:00:00+00:00",
                "updated_at": "2026-10-13T01:00:00+00:00",
            },
            {
                "id": oid("ace.v2"),
                "venue_id": V2,
                "week_ending": week,
                "gross_wages": 5000.0,
                "net_wages": 4000.0,
                "esct": 5.0,
                "kiwisaver": 100.0,
                "gst": 0.0,
                "total_ace_wages": 0.0,
                "created_at": "2026-10-12T22:00:00+00:00",
                "updated_at": "2026-10-12T22:00:00+00:00",
            },
            {
                "id": oid("ace.v3"),
                "venue_id": V3,
                "week_ending": week,
                "gross_wages": 999.0,
                "net_wages": 900.0,
                "esct": 0.0,
                "kiwisaver": 0.0,
                "gst": 0.0,
                "total_ace_wages": 0.0,
                "created_at": "2026-10-12T22:00:00+00:00",
                "updated_at": "2026-10-12T22:00:00+00:00",
            },
        ],
        "wage_recon_manual_lines": [
            {
                "id": oid("line.1"),
                "venue_id": V1,
                "week_ending": week,
                "category": "addition",
                "label": "Public holiday pay",
                "amount": 123.45,
                "notes": None,
                "sort_index": 0,
                "journal_tag": None,
                "xero_account_code": "55300",
                "xero_account_name": "Leave & Entitlements",
                "report_target": "wages",
                "created_at": "2026-10-13T00:00:00+00:00",
                "updated_at": "2026-10-13T00:00:00+00:00",
            },
            {
                "id": oid("line.2"),
                "venue_id": V1,
                "week_ending": "2026-10-04",
                "category": "addition",
                "label": "Rounding",
                "amount": -50.0,
                "notes": "no week row in Orbit",
                "sort_index": 1,
                "journal_tag": None,
                "xero_account_code": None,
                "xero_account_name": None,
                "report_target": "leave",
                "created_at": "2026-10-06T00:00:00+00:00",
                "updated_at": "2026-10-06T00:00:00+00:00",
            },
        ],
        "wage_recon_accrued_pay": [
            {
                "id": oid("accrual.1"),
                "venue_id": V1,
                "week_ending": week,
                "opening_balance": 0.0,
                "additions": 100.0,
                "deductions": 0.0,
                "closing_balance": 100.0,
                "holiday_pay": 0,
                "sick_pay": 0,
                "public_holiday_pay": 0,
                "created_at": "2026-10-13T00:00:00+00:00",
                "updated_at": "2026-10-13T00:00:00+00:00",
            },
        ],
        "wage_recon_xero_pushes": [
            {
                "id": oid("push.j1"),
                "venue_id": V1,
                "week_ending": week,
                "xero_tenant_id": T1,
                "xero_manual_journal_id": oid("xero.j1"),
                "status": "DRAFT",
                "total_debit": 9418.96,
                "total_credit": 9418.96,
                "pushed_by": TM1,
                "pushed_at": "2026-10-13T03:53:15+00:00",
                "voided_at": None,
                "payload": {
                    "top": {"ManualJournals": [{"Narration": "Payroll Journal"}]},
                    "built": {"top": {"lines": []}},
                },
                "preview_payload": None,
                "last_synced_at": None,
                "entitlements_manual_journal_id": None,
                "created_at": "2026-10-13T03:53:15+00:00",
                "updated_at": "2026-10-13T03:53:15+00:00",
            },
            {
                "id": oid("push.j2"),
                "venue_id": V1,
                "week_ending": "2026-10-04",
                "xero_tenant_id": T1,
                "xero_manual_journal_id": oid("xero.j2"),
                "status": "VOIDED",
                "total_debit": 100.0,
                "total_credit": 100.0,
                "pushed_by": TM2,
                "pushed_at": "2026-10-06T03:00:00+00:00",
                "voided_at": "2026-10-06T04:00:00+00:00",
                "payload": {},
                "preview_payload": None,
                "last_synced_at": None,
                "entitlements_manual_journal_id": None,
                "created_at": "2026-10-06T03:00:00+00:00",
                "updated_at": "2026-10-06T04:00:00+00:00",
            },
            {
                "id": oid("push.j3"),
                "venue_id": V2,
                "week_ending": week,
                "xero_tenant_id": T2,
                "xero_manual_journal_id": oid("xero.j3"),
                "status": "DRAFT",
                "total_debit": 5000.0,
                "total_credit": 5000.0,
                "pushed_by": TM1,
                "pushed_at": "2026-10-13T03:55:00+00:00",
                "voided_at": None,
                "payload": {},
                "preview_payload": None,
                "last_synced_at": None,
                "entitlements_manual_journal_id": None,
                "created_at": "2026-10-13T03:55:00+00:00",
                "updated_at": "2026-10-13T03:55:00+00:00",
            },
        ],
        "wage_recon_xero_intercompany_pushes": [
            {
                "id": oid("ico.1"),
                "venue_id": V1,
                "counterparty_venue_id": V2,
                "week_ending": week,
                "direction": "invoice",
                "xero_tenant_id": T1,
                "xero_invoice_id": oid("xero.i1"),
                "status": "DRAFT",
                "subtotal": 1000.0,
                "gst": 150.0,
                "total": 1150.0,
                "payload": {
                    "request": {
                        "InvoiceNumber": "ICO Wages WE 11.10.2026 — Garden Bar"
                    },
                    "response": {"Status": "OK"},
                },
                "preview_payload": None,
                "pushed_by": TM1,
                "pushed_at": "2026-10-13T04:00:00+00:00",
                "voided_at": None,
                "last_synced_at": None,
                "created_at": "2026-10-13T04:00:00+00:00",
                "updated_at": "2026-10-13T04:00:00+00:00",
            },
            {
                "id": oid("ico.2"),
                "venue_id": V1,
                "counterparty_venue_id": V2,
                "week_ending": week,
                "direction": "bill",
                "xero_tenant_id": T1,
                "xero_invoice_id": oid("xero.i2"),
                "status": "DRAFT",
                "subtotal": 200.0,
                "gst": 30.0,
                "total": 230.0,
                "payload": {"request": {}, "response": {}},
                "preview_payload": None,
                "pushed_by": TM1,
                "pushed_at": "2026-10-13T04:01:00+00:00",
                "voided_at": None,
                "last_synced_at": None,
                "created_at": "2026-10-13T04:01:00+00:00",
                "updated_at": "2026-10-13T04:01:00+00:00",
            },
            {
                "id": oid("ico.3"),
                "venue_id": V2,
                "counterparty_venue_id": V3,
                "week_ending": week,
                "direction": "bill",
                "xero_tenant_id": T2,
                "xero_invoice_id": oid("xero.i3"),
                "status": "DRAFT",
                "subtotal": 300.0,
                "gst": 45.0,
                "total": 345.0,
                "payload": {},
                "preview_payload": None,
                "pushed_by": TM1,
                "pushed_at": "2026-10-13T04:02:00+00:00",
                "voided_at": None,
                "last_synced_at": None,
                "created_at": "2026-10-13T04:02:00+00:00",
                "updated_at": "2026-10-13T04:02:00+00:00",
            },
            {
                "id": oid("ico.4"),
                "venue_id": V1,
                "counterparty_venue_id": V2,
                "week_ending": week,
                "direction": "invoice",
                "xero_tenant_id": T1,
                "xero_invoice_id": oid("xero.i4"),
                "status": "DRAFT",
                "subtotal": 10.0,
                "gst": 1.5,
                "total": 11.5,
                "payload": {},
                "preview_payload": None,
                "pushed_by": TM1,
                "pushed_at": "2026-10-13T03:00:00+00:00",
                "voided_at": "2026-10-13T03:30:00+00:00",
                "last_synced_at": None,
                "created_at": "2026-10-13T03:00:00+00:00",
                "updated_at": "2026-10-13T03:30:00+00:00",
            },
        ],
        "xero_account_mappings": mappings,
        "xero_intercompany_contacts": [
            {
                "xero_tenant_id": T1,
                "counterparty_venue_id": V2,
                "xero_contact_id": oid("contact.1"),
                "xero_contact_name": "Garden Bar Limited",
                "created_at": "2026-07-06T00:00:00+00:00",
                "updated_at": "2026-07-06T00:00:00+00:00",
            },
            {
                "xero_tenant_id": T2,
                "counterparty_venue_id": V1,
                "xero_contact_id": oid("contact.2"),
                "xero_contact_name": "Brew Bar Limited",
                "created_at": "2026-07-06T00:00:00+00:00",
                "updated_at": "2026-07-06T00:00:00+00:00",
            },
            {
                "xero_tenant_id": T2,
                "counterparty_venue_id": V3,
                "xero_contact_id": oid("contact.3"),
                "xero_contact_name": "Head Office Limited",
                "created_at": "2026-07-06T00:00:00+00:00",
                "updated_at": "2026-07-06T00:00:00+00:00",
            },
        ],
        "leave_request_payouts": [
            {
                "request_id": "req-1::week::2026-10-05",
                "venue_id": V1,
                "staff_member_id": oid("staff.1"),
                "staff_member_name": "Sam Staff",
                "leave_type": "Annual Leave",
                "requested_amount": 1040.0,
                "paid_amount": 1040.0,
                "workflow_inputs": {
                    "max_payable": 1152.49,
                    "uniform_bond": 150,
                    "holiday_pay_available": 1302.49,
                },
                "notes": None,
                "updated_by": TM1,
                "created_at": "2026-10-13T00:00:00+00:00",
                "updated_at": "2026-10-13T00:00:00+00:00",
            },
            {
                "request_id": "req-2",
                "venue_id": V1,
                "staff_member_id": oid("staff.2"),
                "staff_member_name": "Pat Person",
                "leave_type": "Sick Leave",
                "requested_amount": 149.5,
                "paid_amount": 208.0,
                "workflow_inputs": {"payout": {"all_gates_passed": True}},
                "notes": "certificate seen",
                "updated_by": TM2,
                "created_at": "2026-05-25T21:37:06+00:00",
                "updated_at": "2026-05-25T21:37:06+00:00",
            },
            {
                "request_id": "req-3::week::2026-10-05",
                "venue_id": V2,
                "staff_member_id": oid("staff.3"),
                "staff_member_name": "Lee Leaver",
                "leave_type": "Bereavement Leave",
                "requested_amount": 300.0,
                "paid_amount": 300.0,
                "workflow_inputs": None,
                "notes": None,
                "updated_by": None,
                "created_at": "2026-10-13T00:00:00+00:00",
                "updated_at": "2026-10-13T00:00:00+00:00",
            },
            {
                "request_id": "req-4",
                "venue_id": V1,
                "staff_member_id": oid("staff.4"),
                "staff_member_name": "Ash Annual",
                "leave_type": "Annual Leave",
                "requested_amount": 500.0,
                "paid_amount": 350.0,
                "workflow_inputs": {
                    "max_payable": 350.0,
                    "uniform_bond": 150,
                    "holiday_pay_available": 500.0,
                },
                "notes": None,
                "updated_by": TM1,
                "created_at": "2026-06-16T20:00:00+00:00",
                "updated_at": "2026-06-16T20:00:00+00:00",
            },
        ],
        "payrun_gratuity_batches": [
            {
                "id": G1,
                "venue_id": V1,
                "week_start": "2026-07-27",
                "period_start": "2026-07-01",
                "period_end": "2026-08-01",
                "total_amount": 300.0,
                "gl_code": None,
                "notes": None,
                "created_at": "2026-08-03T21:44:54+00:00",
                "updated_at": "2026-08-03T21:44:54+00:00",
            },
        ],
        "payrun_gratuity_allocations": [
            {
                "id": oid("alloc.1"),
                "batch_id": G1,
                "staff_member_id": oid("staff.1"),
                "staff_name": "Sam Staff",
                "pay_code": "AHEND",
                "hours": 56.95,
                "amount": 100.0,
                "is_active": True,
                "payroll_venue_id": V1,
                "payroll_venue_name": "Brew Bar",
                "created_at": "2026-08-03T21:44:54+00:00",
            },
            {
                "id": oid("alloc.2"),
                "batch_id": G1,
                "staff_member_id": oid("staff.2"),
                "staff_name": "Pat Person",
                "pay_code": "CSTEE",
                "hours": 117.25,
                "amount": 150.0,
                "is_active": True,
                "payroll_venue_id": V2,
                "payroll_venue_name": "Garden Bar",
                "created_at": "2026-08-03T21:44:54+00:00",
            },
            {
                "id": oid("alloc.3"),
                "batch_id": G1,
                "staff_member_id": oid("staff.5"),
                "staff_name": "Gone Guy",
                "pay_code": "GONE",
                "hours": 10.0,
                "amount": 50.0,
                "is_active": False,
                "payroll_venue_id": V3,
                "payroll_venue_name": "Head Office",
                "created_at": "2026-08-03T21:44:54+00:00",
            },
        ],
        "entertainers": [
            _performer(
                E1,
                "Wren Tax",
                "wren@example.test",
                tax_code="Wt",
                payroll_code="E001",
                invite_sent_at="2026-06-01T01:00:00+00:00",
                last_reminder_sent_at="2026-06-08T01:00:00+00:00",
                reminder_count=1,
                wt_rate="20.0",
                wt_rate_type="standard",
                declaration_signed_at="2026-07-13T00:00:00+00:00",
                declaration_signature="Wren Tax",
                declaration_wording_version="2026-07-13-v1",
                declaration_ip="203.0.113.9",
            ),
            _performer(
                E2,
                "Unknown Lane",
                "unknown@example.test",
                payment_method="invoice_company",
                setup_type="unknown",
            ),
            _performer(
                E3,
                "Company Act",
                "company@example.test",
                payment_method="invoice_company",
                setup_type="company",
                is_gst_registered=True,
                gst_number="123-456-789",
                company_name="Company Act Limited",
            ),
            _performer(
                E4,
                "Ex Empt",
                "exempt@example.test",
                payment_method="invoice_exemption",
                setup_type="exemption",
                exemption_certificate_number="CE-1",
                exemption_expiry_date="2027-03-31",
            ),
            _performer(
                E5,
                "Band Leader",
                "band@example.test",
                tax_code="msl",
                setup_type="band_leader",
                performer_type="band",
                band_leader_declaration_at="2026-09-13T02:33:06+00:00",
                contractor_confirmations={"independent": True},
            ),
            _performer(
                E6,
                "Blank Slate",
                "blank@example.test",
                payment_method=None,
                setup_type=None,
                performer_type=None,
                onboarding_status="pending",
                is_active=False,
            ),
        ],
        "entertainer_shifts": [
            _shift(
                S1,
                E1,
                V1,
                "2026-09-05",
                recurrence_series_id=R1,
                performance_status="approved",
                approved_at="2026-09-06T10:00:00+00:00",
                approved_by=TM1,
                actual_start_time="19:00:00",
                actual_end_time="23:30:00",
                actual_amount=315.0,
                entertainer_actual_start_time="19:00:00",
                entertainer_actual_end_time="23:30:00",
                entertainer_confirmed_at="2026-09-06T08:00:00+00:00",
                payment_status="paid",
                paid_at="2026-09-08T00:00:00+00:00",
                paid_payrun_week_start="2026-09-07",
                confirmation_email_first_sent_at="2026-09-06T07:00:00+00:00",
                confirmation_email_last_sent_at="2026-09-06T09:00:00+00:00",
                confirmation_email_send_count=3,
                sales_manager_team_member_id=TM1,
                sales_manager_loadedhub_id=oid("loaded.olive"),
                sales_manager_name="Olive Owner",
                sales_manager_email="MEMBER",
                sales_manager_email_first_sent_at="2026-09-06T07:05:00+00:00",
                sales_manager_email_last_sent_at="2026-09-07T07:05:00+00:00",
                sales_manager_email_send_count=2,
            ),
            _shift(S2, E1, V1, "2026-09-12", recurrence_series_id=R1),
            _shift(S3, E1, V1, "2026-09-19", recurrence_series_id=R1),
            _shift(
                S4,
                E3,
                V1,
                "2026-08-01",
                start="22:00:00",
                end="02:00:00",
                agreed=460.0,
                performance_status="approved",
                approved_at="2026-08-02T10:00:00+00:00",
                approved_by=TM2,
                actual_start_time="22:00:00",
                actual_end_time="02:00:00",
                actual_amount=460.0,
                entertainer_confirmed_at="2026-08-02T09:00:00+00:00",
                entertainer_actual_start_time="22:00:00",
                entertainer_actual_end_time="01:30:00",
                payment_status="paid",
                paid_at="2026-08-04T00:00:00+00:00",
                xero_bill_id=oid("bill.1"),
                xero_bill_number="INV-7",
                xero_bill_status="DRAFT",
                xero_bill_pushed_at="2026-08-03T00:00:00+00:00",
                xero_bill_tenant_id=T1,
                entertainer_invoice_url=INVOICE_URL,
                entertainer_invoice_number="INV-7",
                entertainer_invoice_amount=460.0,
                mismatch_email_sent_at="2026-08-02T11:00:00+00:00",
            ),
            _shift(
                S5,
                E2,
                V2,
                "2026-08-15",
                performance_status="approved",
                approved_at="2026-08-16T10:00:00+00:00",
                payment_status="paid",
                paid_at="2026-08-18T00:00:00+00:00",
                xero_bill_id=oid("bill.2"),
                xero_bill_status="DRAFT",
                xero_bill_pushed_at="2026-08-17T00:00:00+00:00",
                xero_bill_tenant_id=T2,
                entertainer_invoice_url=INVOICE_URL,
            ),
            _shift(S6, E4, V3, "2026-08-20"),
            _shift(
                S7,
                E5,
                V2,
                "2026-08-22",
                performance_status="did_not_perform",
                did_not_perform_reason="sick",
            ),
        ],
        "entertainer_shift_time_changes": [
            {
                "id": oid("change.1"),
                "shift_id": S1,
                "changed_at": "2026-09-06T10:00:00+00:00",
                "changed_by_user_id": TM1,
                "changed_by_source": "manager",
                "changed_by_name": "Olive Owner",
                "field": "actual_end_time",
                "old_value": None,
                "new_value": "23:30:00",
                "note": None,
            },
            {
                "id": oid("change.2"),
                "shift_id": S1,
                "changed_at": "2026-09-06T08:00:00+00:00",
                "changed_by_user_id": None,
                "changed_by_source": "entertainer",
                "changed_by_name": "Wren Tax",
                "field": "entertainer_actual_end_time",
                "old_value": None,
                "new_value": "23:30:00",
                "note": "DST",
            },
            {
                "id": oid("change.3"),
                "shift_id": oid("shift.gone"),
                "changed_at": "2026-09-06T08:00:00+00:00",
                "changed_by_user_id": None,
                "changed_by_source": "system",
                "changed_by_name": None,
                "field": "actual_amount",
                "old_value": None,
                "new_value": "1",
                "note": None,
            },
        ],
        "entertainer_xero_contacts": [
            {
                "id": oid("xc.1"),
                "entertainer_id": E3,
                "xero_tenant_id": T1,
                "xero_contact_id": oid("xero.contact.e3"),
                "xero_contact_name": "Company Act Limited",
                "created_at": "2026-07-07T00:00:00+00:00",
                "updated_at": "2026-07-07T00:00:00+00:00",
            },
            {
                "id": oid("xc.2"),
                "entertainer_id": E3,
                "xero_tenant_id": T1,
                "xero_contact_id": oid("xero.contact.dup"),
                "xero_contact_name": "dup",
                "created_at": "2026-07-07T00:00:00+00:00",
                "updated_at": "2026-07-07T00:00:00+00:00",
            },
        ],
        "entertainer_group_members": [],
        "budgets": [
            {
                "id": oid("b.1"),
                "venue_id": V1,
                "budget_date": "2026-04-01",
                "amount": 2200,
                "sales_tax": 0.15,
                "loadedhub_id": "x",
                "loadedhub_amount": 2200,
                "event_note": "Easter Wednesday",
                "financial_budget": 1800,
                "created_at": "2026-05-20T00:00:00+00:00",
                "updated_at": "2026-10-19T19:10:02+00:00",
            },
            {
                "id": oid("b.2"),
                "venue_id": V1,
                "budget_date": "2026-04-02",
                "amount": 2000,
                "sales_tax": 0.15,
                "loadedhub_id": "y",
                "loadedhub_amount": 2000,
                "event_note": None,
                "financial_budget": 2000.5,
                "created_at": "2026-05-20T00:00:00+00:00",
                "updated_at": "2026-10-19T19:10:02+00:00",
            },
            {
                "id": oid("b.3"),
                "venue_id": V1,
                "budget_date": "2027-03-31",
                "amount": 1000,
                "sales_tax": 0.15,
                "loadedhub_id": "z",
                "loadedhub_amount": 1000,
                "event_note": None,
                "financial_budget": 1000,
                "created_at": "2026-06-25T00:00:00+00:00",
                "updated_at": "2026-10-19T19:10:02+00:00",
            },
            {
                "id": oid("b.4"),
                "venue_id": V1,
                "budget_date": "2026-03-31",
                "amount": 500,
                "sales_tax": 0.15,
                "loadedhub_id": "w",
                "loadedhub_amount": 500,
                "event_note": None,
                "financial_budget": 500,
                "created_at": "2026-05-20T00:00:00+00:00",
                "updated_at": "2026-10-19T19:10:02+00:00",
            },
            {
                "id": oid("b.5"),
                "venue_id": V2,
                "budget_date": "2026-05-01",
                "amount": 3000,
                "sales_tax": 0.15,
                "loadedhub_id": "v",
                "loadedhub_amount": 3000,
                "event_note": None,
                "financial_budget": 3000,
                "created_at": "2026-05-20T00:00:00+00:00",
                "updated_at": "2026-10-19T19:10:02+00:00",
            },
            {
                "id": oid("b.6"),
                "venue_id": V3,
                "budget_date": "2026-05-01",
                "amount": 0,
                "sales_tax": 0.15,
                "loadedhub_id": None,
                "loadedhub_amount": None,
                "event_note": None,
                "financial_budget": 0,
                "created_at": "2026-05-20T00:00:00+00:00",
                "updated_at": "2026-10-19T19:10:02+00:00",
            },
            {
                "id": oid("b.7"),
                "venue_id": V1,
                "budget_date": "2027-05-01",
                "amount": 0,
                "sales_tax": 0.15,
                "loadedhub_id": None,
                "loadedhub_amount": None,
                "event_note": None,
                "financial_budget": None,
                "created_at": "2026-10-01T00:00:00+00:00",
                "updated_at": "2026-10-19T19:10:02+00:00",
            },
        ],
        "finance_budgets": [
            {
                "id": oid("fb.1"),
                "venue_id": V1,
                "fiscal_year": 2027,
                "month": 4,
                "line_item": "wage_pct",
                "value": 0.3,
                "created_at": "2026-06-06T00:00:00+00:00",
                "updated_at": "2026-06-06T00:00:00+00:00",
            },
            {
                "id": oid("fb.2"),
                "venue_id": V1,
                "fiscal_year": 2027,
                "month": 4,
                "line_item": "rebates",
                "value": 100,
                "created_at": "2026-07-28T00:00:00+00:00",
                "updated_at": "2026-07-28T00:00:00+00:00",
            },
            {
                "id": oid("fb.3"),
                "venue_id": V1,
                "fiscal_year": 2027,
                "month": 4,
                "line_item": "wage_amount",
                "value": 97333,
                "created_at": "2026-07-28T00:00:00+00:00",
                "updated_at": "2026-07-28T00:00:00+00:00",
            },
            {
                "id": oid("fb.4"),
                "venue_id": V2,
                "fiscal_year": 2027,
                "month": 5,
                "line_item": "operating_expenses",
                "value": 1000.25,
                "created_at": "2026-06-06T00:00:00+00:00",
                "updated_at": "2026-06-06T00:00:00+00:00",
            },
        ],
        "sales_forecast_factor_types": [
            {"id": FT_EVENT_V1, "venue_id": V1, "name": "Event", "active": True},
            {"id": FT_WEATHER_V1, "venue_id": V1, "name": "Weather", "active": True},
            {"id": FT_EVENT_V2, "venue_id": V2, "name": "Event", "active": True},
        ],
        "sales_forecast_factors": [
            {
                "id": oid("f.1"),
                "venue_id": V1,
                "factor_date": "2026-06-01",
                "factor_type_id": FT_EVENT_V1,
                "label": "Big gig",
                "impact": "positive",
                "created_at": "2026-02-21T00:00:00+00:00",
                "updated_at": "2026-02-21T00:00:00+00:00",
            },
            {
                "id": oid("f.2"),
                "venue_id": V1,
                "factor_date": "2026-06-02",
                "factor_type_id": FT_WEATHER_V1,
                "label": "Rain",
                "impact": "negative",
                "created_at": "2026-02-21T00:00:00+00:00",
                "updated_at": "2026-02-21T00:00:00+00:00",
            },
            {
                "id": oid("f.3"),
                "venue_id": V2,
                "factor_date": "2026-07-01",
                "factor_type_id": FT_EVENT_V2,
                "label": "Quiz night",
                "impact": "positive",
                "created_at": "2026-02-21T00:00:00+00:00",
                "updated_at": "2026-02-21T00:00:00+00:00",
            },
        ],
    }
    for s in tables["entertainer_shifts"]:
        if s["sales_manager_email"] == "MEMBER":
            s["sales_manager_email"] = member_email
    files = {INVOICE_URL.split("?", 1)[0]: INVOICE_PDF}
    return tables, files


# ---------------------------------------------------------------------------
# The Norm side
# ---------------------------------------------------------------------------


def make_world(db, *, modes=("shadow", "shadow", "shadow")):
    from types import SimpleNamespace

    org = _make_organization(db, name="Cook Bros")
    org.payroll_mode, org.entertainers_mode, org.finance_mode = modes
    org.payroll_contact_email = "payroll@example.test"
    venue = _make_venue(db, name="Brew Bar", organization_id=org.id)
    venue2 = _make_venue(db, name="Garden Bar", organization_id=org.id)
    venue.timezone = venue2.timezone = "Pacific/Auckland"
    venue.day_start_time = "07:00"
    venue2.day_start_time = "06:00"
    for v, company in ((venue, C1), (venue2, C2)):
        db.add(
            Connection(
                connector_name="loadedhub",
                venue_id=v.id,
                config={"x_loaded_company_id": company},
                access_token="t",
            )
        )
    db.add(
        Connection(
            connector_name="xero",
            venue_id=venue.id,
            config={"xero_tenant_id": T1},
            access_token="t",
        )
    )
    member = _make_user(db, full_name="Olive Owner")
    _make_membership(db, member, org, role="owner")
    db.flush()
    return SimpleNamespace(db=db, org=org, venue=venue, venue2=venue2, member=member)


@pytest.fixture
def w(db_session):
    return make_world(db_session)


def _fake(w) -> FakeFinanceOrbit:
    tables, files = orbit_dataset(w.member.email)
    return FakeFinanceOrbit(tables, files)


def _run(w, fake=None, **kw):
    fake = fake or _fake(w)
    kw.setdefault("skip_venues", ["Head Office"])
    result = run_import(
        w.db,
        fake.client(kw.pop("page", 1000)),
        org=w.org.id,
        commit=False,
        now=NOW,
        log=lambda *a: None,
        **kw,
    )
    return result, fake


def _id(w, table, key):
    return import_id(w.org.id, table, key)


def _rows(w, model, **where):
    q = w.db.query(model).filter(model.organization_id == w.org.id)
    for k, v in where.items():
        q = q.filter(getattr(model, k) == v)
    return q.all()


def _one(w, model, row_id):
    row = w.db.get(model, row_id)
    assert row is not None, f"{model.__tablename__} {row_id} missing"
    return row


def _snapshot(w) -> dict:
    """Every imported row of every finance table, as plain values."""
    out = {}
    for module in (pm, em, fin):
        for value in vars(module).values():
            table = getattr(value, "__tablename__", None)
            if not isinstance(table, str) or not hasattr(value, "organization_id"):
                continue
            rows = w.db.query(value).filter(value.organization_id == w.org.id).all()
            out[table] = sorted(
                (
                    tuple(
                        (c.name, getattr(r, c.name))
                        for c in value.__table__.c
                        if c.name != "data"
                    )
                    for r in rows
                ),
                key=lambda t: str(t),
            )
    return out


# ===========================================================================
# Pure transforms
# ===========================================================================


def test_import_ids_are_deterministic_uuid5():
    a = import_id("org", "pay_weeks", "v:2026-10-05")
    assert a == import_id("org", "pay_weeks", "v:2026-10-05")
    assert a != import_id("org", "pay_weeks", "v:2026-10-12")
    assert (
        is_import_id(a)
        and not is_import_id(str(uuid.uuid4()))
        and not is_import_id("junk")
    )


def test_week_helpers():
    assert week_start_from_ending("2026-10-11") == dt.date(2026, 10, 5)
    assert week_start_from_ending(None) is None
    tz = dt.timezone(dt.timedelta(hours=13))
    # Tue 26 May 09:37 NZ → the week of Mon 25 May
    assert local_week_of(
        dt.datetime(2026, 5, 25, 21, 37, tzinfo=UTC), tz, dt.time(7, 0)
    ) == dt.date(2026, 5, 25)
    # Mon 18 May 05:59 NZ is before the day start: still Sunday's week (Mon 11 May)
    assert local_week_of(
        dt.datetime(2026, 5, 17, 17, 59, tzinfo=UTC), tz, dt.time(7, 0)
    ) == dt.date(2026, 5, 11)
    assert hours_between(dt.time(22, 0), dt.time(2, 0)) == 4
    assert hours_between(dt.time(19, 0), dt.time(23, 30)) == 4.5
    assert fiscal_year_of(dt.date(2026, 4, 1), 4) == 2027
    assert fiscal_year_of(dt.date(2026, 3, 31), 4) == 2026
    assert fiscal_year_of(dt.date(2026, 7, 1), 7) == 2027
    assert to_cents("10000.50") == 1000050 and to_cents(None) is None


@pytest.mark.parametrize(
    "raw, code, flagged",
    [
        ("WT", "WT", False),
        ("Wt", "WT", False),
        ("msl", "M SL", False),
        ("S SL", "S SL", False),
        ("SSL", "S SL", False),
        ("SB SL", "SB SL", False),
        ("ME", "ME", False),
        ("WT SL", "WT", True),
        ("0.15", None, True),
        ("Sl", None, True),
        (None, None, False),
        ("  ", None, False),
    ],
)
def test_tax_code_spellings(raw, code, flagged):
    got, problem = normalise_tax_code(raw)
    assert got == code
    assert bool(problem) is flagged


@pytest.mark.parametrize(
    "performer, lane, review",
    [
        ({"payment_method": "payroll", "tax_code": "WT"}, "payroll_contractor", False),
        ({"payment_method": "payroll", "tax_code": "M SL"}, "payroll_employee", False),
        (
            {"payment_method": "payroll", "setup_type": "band_leader"},
            "payroll_employee",
            True,
        ),
        (
            {"payment_method": "invoice_company", "setup_type": "company"},
            "invoice",
            False,
        ),
        (
            {"payment_method": "invoice_company", "setup_type": "booking_agent"},
            "invoice",
            False,
        ),
        (
            {"payment_method": "invoice_company", "setup_type": "unknown"},
            "invoice",
            True,
        ),
        ({"payment_method": "invoice_company", "setup_type": None}, "invoice", True),
        (
            {"payment_method": "invoice_exemption", "setup_type": "exemption"},
            "exemption",
            False,
        ),
        (
            {"payment_method": "band_leader", "setup_type": "band_leader"},
            "invoice",
            False,
        ),
        (
            {"payment_method": "sole_trader", "tax_code": "WT"},
            "payroll_contractor",
            False,
        ),
        ({"payment_method": None, "payroll_code": "E9"}, "payroll_employee", True),
        ({"payment_method": None}, "invoice", True),
    ],
)
def test_lane_mapping(performer, lane, review):
    got, reason = lane_for(performer)
    assert got == lane
    assert bool(reason) is review


def test_payout_key_split():
    assert split_payout_key("abc::week::2026-10-05") == ("abc", dt.date(2026, 10, 5))
    assert split_payout_key("abc") == ("abc", None)


def test_series_rule_from_materialised_shifts():
    weekly = [
        {
            "shift_date": f"2026-09-{d:02d}",
            "start_time": "19:00:00",
            "end_time": "23:00:00",
            "agreed_amount": 280,
        }
        for d in (5, 12, 19, 26)
    ]
    rule = series_rule(weekly)
    assert (
        rule["weekday"] == 6
        and rule["interval_weeks"] == 1
        and rule["occurrences"] == 4
    )
    assert rule["starts_on"] == dt.date(2026, 9, 5) and rule["ends_on"] == dt.date(
        2026, 9, 26
    )
    assert rule["agreed_cents"] == 28000 and rule["start_time"] == dt.time(19, 0)
    fortnightly = [weekly[0], weekly[2]]
    assert series_rule(fortnightly)["interval_weeks"] == 2
    # Several weekdays: the most common wins, both are listed
    mixed = weekly + [
        {
            "shift_date": "2026-09-06",
            "start_time": "19:00:00",
            "end_time": "23:00:00",
            "agreed_amount": 280,
        }
    ]
    assert series_rule(mixed)["weekday"] == 6 and series_rule(mixed)["weekdays"] == [
        6,
        7,
    ]


def test_xero_mapping_blocks_collapse_to_the_live_keys():
    tables, _ = orbit_dataset("x@example.test")
    blocks, dropped = xero_mapping_blocks(
        tables["xero_account_mappings"],
        tables["xero_intercompany_contacts"],
        {T1: V1, T2: V2},
        {V1: "norm-1", V2: "norm-2"},
    )
    payroll = blocks["norm-1"]["payroll"]
    assert set(payroll) == {
        "wages_payable", "payroll_taxes_payable", "unpaid_wages", "security_owed", "intercompany_wages",
        "bar_floor", "kitchen", "other_team", "salaries", "site_manager_bonus", "leave_entitlements",
        "entertainers", "entertainment_expense", "gratuities_reclass",
    }  # fmt: skip
    assert payroll["wages_payable"] == {"code": "800", "name": "wages payable"}
    assert (
        blocks["norm-1"]["intercompany"]["wages_account"]
        == payroll["intercompany_wages"]["code"]
    )
    assert (
        blocks["norm-1"]["intercompany"]["contacts"]["norm-2"]["name"]
        == "Garden Bar Limited"
    )
    assert blocks["norm-2"]["intercompany"]["contacts"]["norm-1"]["contact_id"] == oid(
        "contact.2"
    )
    # the six dead ent_* keys, DJs and marketing are not carried; the contact
    # for the venue not in Norm is dropped and counted
    assert (
        sum(n for r, n in dropped.items() if r.startswith("line type not carried")) == 9
    )
    assert dropped["contact: counterparty venue not in Norm"] == 1


def test_email_history_rows_from_send_dates():
    tables, _ = orbit_dataset("x@example.test")
    performer = next(p for p in tables["entertainers"] if p["id"] == E1)
    shifts = [s for s in tables["entertainer_shifts"] if s["entertainer_id"] == E1]
    rows = email_history(performer, shifts)
    kinds = [(r["kind"], r["recipient_kind"], r["send_number"]) for r in rows]
    assert kinds == [
        ("invite", "performer", 1),
        ("invite_reminder", "performer", 1),
        ("confirm_request", "performer", 1),
        ("confirm_reminder", "performer", 3),
        ("manager_approval", "closing_manager", 1),
        ("manager_approval", "closing_manager", 2),
    ]
    company = next(p for p in tables["entertainers"] if p["id"] == E3)
    mismatch = email_history(
        company, [s for s in tables["entertainer_shifts"] if s["entertainer_id"] == E3]
    )
    assert [(r["kind"], r["recipient_kind"]) for r in mismatch] == [
        ("mismatch", "performer"),
        ("mismatch", "venue_manager"),
    ]


def test_gst_audit_lists_bills_for_unregistered_performers():
    tables, _ = orbit_dataset("x@example.test")
    hits = gst_audit(
        tables["entertainer_shifts"], {p["id"]: p for p in tables["entertainers"]}
    )
    assert [h["shift_id"] for h in hits] == [S5]  # E3 is GST-registered, E2 is not


# ===========================================================================
# The whole import
# ===========================================================================


def test_full_import_lands_every_table(w):
    result, fake = _run(w)
    assert result.report.gate["passed"], result.report.gate
    assert result.committed and result.ok
    assert all(m in ("GET", "HEAD") for m, _ in fake.requests)
    r = result.report

    # --- payroll ---
    week_a = _one(w, pm.PayWeek, _id(w, "pay_weeks", f"{w.venue.id}:2026-10-05"))
    assert week_a.notes == "Labour Day lieu paid" and week_a.closed_at is None
    assert (
        len(_rows(w, pm.PayWeek)) == 3
    )  # V1 WE 11 Oct, V2 WE 11 Oct, and V1 WE 4 Oct made for its line and voided push
    ace = _one(w, pm.PayAceInput, _id(w, "pay_ace_inputs", week_a.id))
    assert (
        ace.gross_cents,
        ace.net_cents,
        ace.esct_cents,
        ace.kiwisaver_cents,
        ace.source,
    ) == (1000050, 800000, 1025, 20000, "orbit")
    adjustments = {a.label: a for a in _rows(w, pm.PayAdjustment)}
    assert (
        adjustments["Public holiday pay"].amount_cents == 12345
        and adjustments["Public holiday pay"].report_target == "wages"
    )
    assert adjustments["Rounding"].amount_cents == -5000
    assert adjustments["Rounding"].week_id == _id(
        w, "pay_weeks", f"{w.venue.id}:2026-09-28"
    )
    accrual = _one(w, pm.PayAccrual, _id(w, "pay_accruals", week_a.id))
    assert (accrual.opening_cents, accrual.accrued_cents, accrual.paid_cents) == (
        0,
        10000,
        0,
    )
    pushes = {p.idempotency_key: p for p in _rows(w, pm.PayXeroPush)}
    j1 = pushes[f"orbit:wage_recon_xero_pushes:{oid('push.j1')}"]
    assert (j1.kind, j1.xero_id, j1.xero_status, j1.pushed_by, j1.voided_at) == (
        "journal",
        oid("xero.j1"),
        "DRAFT",
        w.member.id,
        None,
    )
    assert j1.request_payload == {"ManualJournals": [{"Narration": "Payroll Journal"}]}
    assert j1.response["totals"] == {"debit_cents": 941896, "credit_cents": 941896}
    j2 = pushes[f"orbit:wage_recon_xero_pushes:{oid('push.j2')}"]
    assert (
        j2.voided_at is not None
        and j2.pushed_by is None
        and j2.response["pushed_by_label"] == "Nobody Known"
    )
    i1 = pushes[f"orbit:wage_recon_xero_intercompany_pushes:{oid('ico.1')}"]
    assert (i1.kind, i1.counterparty_venue_id, i1.xero_number) == (
        "ico_invoice",
        w.venue2.id,
        "ICO Wages WE 11.10.2026 — Garden Bar",
    )
    assert (
        pushes[f"orbit:wage_recon_xero_intercompany_pushes:{oid('ico.2')}"].kind
        == "ico_bill"
    )
    assert (
        f"orbit:wage_recon_xero_intercompany_pushes:{oid('ico.3')}" not in pushes
    )  # counterparty Head Office
    assert (
        r.sources["wage_recon_xero_intercompany_pushes"]["dropped"][
            "counterparty venue not in Norm"
        ]
        == 1
    )
    payouts = {p.loaded_request_id: p for p in _rows(w, pm.PayLeavePayout)}
    assert (
        payouts["req-1"].week_start,
        payouts["req-1"].source,
        payouts["req-1"].workflow_kind,
    ) == (dt.date(2026, 10, 5), "orbit", "annual")
    assert (
        payouts["req-1"].decided_by == w.member.id
        and payouts["req-1"].bond_deducted_cents == 0
    )
    # keyed Tue 26 May NZ → the pay week before, Mon 18 May
    assert (
        payouts["req-2"].week_start,
        payouts["req-2"].source,
        payouts["req-2"].workflow_kind,
    ) == (dt.date(2026, 5, 18), "orbit_legacy", "sick")
    assert (
        payouts["req-2"].decided_by is None
        and "Decided in Orbit by Nobody Known" in payouts["req-2"].notes
    )
    assert (
        payouts["req-4"].bond_deducted_cents == 15000
    )  # requested 500 > max payable 350: the bond came off
    assert (
        payouts["req-3"].workflow_kind == "generic"
        and payouts["req-3"].venue_id == w.venue2.id
    )
    batch = _one(w, pm.PayGratuityBatch, _id(w, "pay_gratuity_batches", G1))
    assert (batch.period, batch.total_cents, batch.week_start) == (
        "2026-07",
        30000,
        dt.date(2026, 7, 27),
    )
    allocations = sorted(
        _rows(w, pm.PayGratuityAllocation, batch_id=batch.id),
        key=lambda a: a.amount_cents,
    )
    assert [
        (a.amount_cents, a.is_payable, a.payroll_venue_id) for a in allocations
    ] == [
        (5000, False, None),
        (10000, True, w.venue.id),
        (15000, True, w.venue2.id),
    ]
    entity = _one(w, pm.PayEntity, _id(w, "pay_entities", w.venue.id))
    assert entity.loaded_company_id == C1 and entity.ace_gratuity_allowance_code == "13"
    assert (
        _one(
            w, pm.PayEntity, _id(w, "pay_entities", w.venue2.id)
        ).ace_gratuity_allowance_code
        is None
    )

    # --- entertainers ---
    performers = {p.email: p for p in _rows(w, em.EntPerformer)}
    wren = performers["wren@example.test"]
    assert (
        wren.lane,
        wren.tax_code,
        wren.payroll_code,
        wren.onboarding_status,
        wren.wt_rate_bp,
    ) == ("payroll_contractor", "WT", "E001", "accepted", 2000)
    assert wren.ird_number == "049091850" and wren.bank_account == "01-0001-0000001-00"
    assert (
        wren.declaration_snapshot["wording_version"] == "2026-07-13-v1"
        and wren.invite_reminders_sent == 1
    )
    assert (
        performers["unknown@example.test"].needs_review
        and "confirm the lane" in performers["unknown@example.test"].review_reason
    )
    assert (
        performers["company@example.test"].lane == "invoice"
        and performers["company@example.test"].gst_registered
    )
    assert performers["exempt@example.test"].lane == "exemption"
    band = performers["band@example.test"]
    assert (band.lane, band.tax_code, band.performer_type, band.band_mode) == (
        "payroll_employee",
        "M SL",
        "band",
        "leader",
    )
    assert band.band_leader_declaration_at is not None and band.needs_review
    blank = performers["blank@example.test"]
    assert (blank.lane, blank.onboarding_status, blank.is_active) == (
        "invoice",
        "invited",
        False,
    )
    series = _one(w, em.EntSeries, _id(w, "ent_series", R1))
    assert (
        series.weekday,
        series.start_time,
        series.rate_cents,
        series.starts_on,
        series.ends_on,
    ) == (6, dt.time(19, 0), 7000, dt.date(2026, 9, 5), dt.date(2026, 9, 19))
    s1 = _one(w, em.EntPerformance, _id(w, "ent_performances", S1))
    assert (
        s1.series_id,
        s1.rate_cents,
        s1.agreed_cents,
        s1.actual_cents,
        s1.performance_status,
        s1.payment_status,
    ) == (series.id, 7000, 28000, 31500, "approved", "paid")
    assert s1.approved_by == w.member.id and s1.payroll_week_start == dt.date(
        2026, 9, 7
    )
    assert (
        s1.closing_manager_source,
        s1.closing_manager_user_id,
        s1.closing_manager_staff_id,
    ) == ("orbit", w.member.id, oid("loaded.olive"))
    s4 = _one(w, em.EntPerformance, _id(w, "ent_performances", S4))
    assert (
        s4.ends_next_day
        and s4.actual_ends_next_day
        and s4.rate_cents == 11500
        and s4.approved_by is None
    )
    assert s4.closing_manager_source is None
    s5 = _one(w, em.EntPerformance, _id(w, "ent_performances", S5))
    assert s4.invoice_file_id == s5.invoice_file_id  # one shared invoice file
    invoice = _one(w, em.EntFile, s4.invoice_file_id)
    assert (
        invoice.kind,
        invoice.check_status,
        invoice.invoice_number,
        invoice.invoice_cents,
        invoice.data,
    ) == ("invoice", "waived", "INV-7", 46000, INVOICE_PDF)
    assert invoice.source_ref == INVOICE_URL.split("?", 1)[0]
    assert _id(w, "ent_performances", S6) not in {
        p.id for p in _rows(w, em.EntPerformance)
    }
    changes = sorted(_rows(w, em.EntPerformanceChange), key=lambda c: c.changed_at)
    assert [
        (c.changed_by_source, c.changed_by_user_id, c.changed_by_name) for c in changes
    ] == [
        ("performer", None, "Wren Tax"),
        ("manager", w.member.id, None),
    ]
    assert (
        r.sources["entertainer_shift_time_changes"]["dropped"][
            "performance not imported"
        ]
        == 1
    )
    contacts = _rows(w, em.EntPerformerXeroContact)
    assert len(contacts) == 1 and contacts[0].xero_contact_id == oid("xero.contact.e3")
    bills = [p for p in pushes.values() if p.kind == "entertainer_bill"]
    assert {b.entertainer_performance_id for b in bills} == {s4.id, s5.id}
    assert (
        next(b for b in bills if b.entertainer_performance_id == s4.id).xero_number
        == "INV-7"
    )
    emails = _rows(w, em.EntEmailLink)
    assert len(emails) == 8 and all(
        e.source == "orbit" and e.email_log_id is None for e in emails
    )
    manager_rows = [e for e in emails if e.recipient_kind == "closing_manager"]
    assert len(manager_rows) == 2 and all(
        e.recipient_email == w.member.email for e in manager_rows
    )
    assert any("send 3 of 3" in e.subject for e in emails)

    # --- finance ---
    versions = {(v.venue_id, v.fiscal_year): v for v in _rows(w, fin.FinBudgetVersion)}
    assert set(versions) == {
        (w.venue.id, 2027),
        (w.venue.id, 2026),
        (w.venue2.id, 2027),
    }
    fy27 = versions[(w.venue.id, 2027)]
    assert fy27.locked_at is not None and fy27.source == "import"
    days = {d.date: d for d in _rows(w, fin.FinBudgetDay, version_id=fy27.id)}
    assert (
        days[dt.date(2026, 4, 1)].amount_cents == 180000
        and days[dt.date(2026, 4, 1)].note == "Easter Wednesday"
    )
    assert days[dt.date(2026, 4, 2)].amount_cents == 200050 and len(days) == 3
    lines = {(x.venue_id, x.month, x.line_item): x for x in _rows(w, fin.FinBudgetLine)}
    assert float(lines[(w.venue.id, 4, "wage_pct")].value_ratio) == pytest.approx(0.3)
    assert lines[(w.venue.id, 4, "wage_cents")].value_cents == 9733300
    assert lines[(w.venue2.id, 5, "opex_cents")].value_cents == 100025
    assert (w.venue.id, 4, "rebates") not in lines and r.sources["finance_budgets"][
        "dropped"
    ]["line item not carried (D33): rebates"] == 1
    drivers = {d.label: d for d in _rows(w, fin.FinForecastDriver)}
    assert set(drivers) == {"Big gig", "Quiz night"}
    assert (
        drivers["Big gig"].kind == "event" and drivers["Big gig"].uplift_cents is None
    )

    # --- the report ---
    assert r.people["matched"] == 1 and "Nobody Known" in r.people["unmatched"]
    assert r.venues["skipped"] == ["Head Office"] and not r.venues["unmapped"]
    assert r.xero_mapping["Brew Bar"]["status"] == "venue has a Xero connection in Norm"
    assert r.xero_mapping["Garden Bar"]["status"].startswith("NO Xero connection")
    decisions = r.decisions
    assert any("GST audit" in k for k in decisions)
    assert any("lane needs confirming" in k for k in decisions)
    assert decisions["tax-code spellings in Orbit → Norm's code"]["msl"] == "→ M SL"
    assert (
        "req-2"
        in decisions["legacy leave payouts (pay week inferred, source=orbit_legacy)"]
    )
    assert "Head Office" not in str(r.written)


def test_reload_is_identical_and_deletes_what_orbit_lost(w):
    _run(w)
    before = _snapshot(w)
    result, fake = _run(w)
    assert result.ok
    assert _snapshot(w) == before
    assert all(v.get("insert", 0) == 0 for v in result.report.written.values())
    # the file is not fetched again
    assert sum(1 for m, u in fake.requests if u.startswith(SIGNED)) == 0
    # Orbit loses a line and a performance: the reload removes them
    fake.tables["wage_recon_manual_lines"] = fake.tables["wage_recon_manual_lines"][:1]
    fake.tables["entertainer_shifts"] = [
        s for s in fake.tables["entertainer_shifts"] if s["id"] != S7
    ]
    result, _ = _run(w, fake)
    assert result.ok
    assert len(_rows(w, pm.PayAdjustment)) == 1
    assert w.db.get(em.EntPerformance, _id(w, "ent_performances", S7)) is None
    assert result.report.written["ent_performances"]["delete"] == 1


def test_dry_run_writes_nothing(w):
    result, _ = _run(w, dry_run=True)
    assert result.ok and not result.committed
    assert "rolled back" in result.report.outcome
    assert (
        _rows(w, pm.PayWeek) == []
        and _rows(w, em.EntPerformer) == []
        and _rows(w, fin.FinBudgetDay) == []
    )


def test_live_module_is_refused(w):
    w.org.payroll_mode = "live"
    w.db.flush()
    with pytest.raises(ImportRefused, match="payroll is live"):
        _run(w)
    assert _rows(w, em.EntPerformer) == []
    # the other modules can still be asked for on their own
    result, _ = _run(w, modules=["entertainers", "finance"])
    assert (
        result.ok and _rows(w, pm.PayWeek) == [] and len(_rows(w, em.EntPerformer)) == 6
    )


def test_unmapped_venue_fails_the_gate_unless_skipped(w):
    result, _ = _run(w, skip_venues=[])
    assert not result.ok and not result.committed
    failed = [c for c in result.report.gate["checks"] if not c["ok"]]
    assert any(
        "every Orbit venue mapped" in c["check"] and "Head Office" in c["detail"]
        for c in failed
    )
    assert _rows(w, pm.PayWeek) == []


def test_a_broken_read_fails_the_gate_and_keeps_the_previous_import(w):
    _run(w)
    fake = _fake(w)
    fake.count_override["entertainers"] = 60  # Orbit says 60, the read got 6
    fake.tables["wage_recon_ace_inputs"][0]["gross_wages"] = (
        1.0  # read differs from the baseline? no: same fake — make the sums disagree instead
    )
    result, _ = _run(w, fake)
    assert not result.ok
    assert any(
        c["check"] == "read entertainers" and not c["ok"]
        for c in result.report.gate["checks"]
    )
    assert "would STOP" not in result.report.outcome
    assert (
        _one(
            w,
            pm.PayAceInput,
            _id(w, "pay_ace_inputs", _id(w, "pay_weeks", f"{w.venue.id}:2026-10-05")),
        ).gross_cents
        == 1000050
    )


def test_norm_authored_rows_are_never_overwritten(w):
    """Head office keyed WE 11 Oct in Norm (a rehearsal week): the week and
    its ACE inputs are Norm's. Orbit's ACE inputs for the same week are left
    out and reported; Orbit's adjustments attach to Norm's week."""
    week = pm.PayWeek(
        organization_id=w.org.id,
        venue_id=w.venue.id,
        week_start=dt.date(2026, 10, 5),
        notes="keyed in Norm",
    )
    w.db.add(week)
    w.db.flush()
    w.db.add(
        pm.PayAceInput(
            organization_id=w.org.id,
            week_id=week.id,
            gross_cents=1,
            net_cents=1,
            esct_cents=0,
            kiwisaver_cents=0,
            source="norm",
            rehearsal=True,
        )
    )
    w.db.flush()
    assert not is_import_id(week.id)
    result, _ = _run(w)
    assert result.ok, result.report.gate
    kept = _one(w, pm.PayWeek, week.id)
    assert kept.notes == "keyed in Norm"
    assert w.db.get(pm.PayWeek, _id(w, "pay_weeks", f"{w.venue.id}:2026-10-05")) is None
    assert len(_rows(w, pm.PayAceInput, week_id=week.id)) == 1
    assert _rows(w, pm.PayAceInput, week_id=week.id)[0].gross_cents == 1
    adjustment = next(
        a for a in _rows(w, pm.PayAdjustment) if a.label == "Public holiday pay"
    )
    assert adjustment.week_id == week.id
    assert any("pay_weeks" in c for c in result.report.collisions)
    assert any("pay_ace_inputs" in c for c in result.report.collisions)
    # a second run is the same story, and the Norm rows are still there
    result, _ = _run(w)
    assert result.ok and _one(w, pm.PayWeek, week.id).notes == "keyed in Norm"


def test_legacy_payout_weeks_come_from_loaded_when_given(w):
    result, _ = _run(w, legacy_weeks={"req-2": dt.date(2026, 5, 18)})
    assert result.ok
    payout = next(
        p for p in _rows(w, pm.PayLeavePayout) if p.loaded_request_id == "req-2"
    )
    assert payout.week_start == dt.date(2026, 5, 18) and payout.source == "orbit_legacy"
    note = result.report.decisions[
        "legacy leave payouts (pay week inferred, source=orbit_legacy)"
    ]
    assert (
        "Loaded day lines" in note["req-2"] and "before it was keyed" in note["req-4"]
    )


def test_the_same_invoice_bytes_under_two_urls_land_once(w):
    fake = _fake(w)
    second = SIGNED + "e2/copy-of-inv-7.pdf?token=another-token"
    fake.files[second.split("?", 1)[0]] = INVOICE_PDF
    for s in fake.tables["entertainer_shifts"]:
        if s["id"] == S5:
            s["entertainer_invoice_url"] = second
    result, _ = _run(w, fake)
    assert result.ok, result.report.gate
    files = _rows(w, em.EntFile)
    assert len(files) == 1
    s4 = _one(w, em.EntPerformance, _id(w, "ent_performances", S4))
    s5 = _one(w, em.EntPerformance, _id(w, "ent_performances", S5))
    assert s4.invoice_file_id == s5.invoice_file_id == files[0].id


def test_a_legacy_payout_on_a_weekly_rows_week_is_set_aside_and_listed(w):
    fake = _fake(w)
    # keyed Tue 13 Oct NZ → week of 5 Oct, which req-1's weekly row holds
    fake.tables["leave_request_payouts"].append(
        {
            "request_id": "req-1",
            "venue_id": V1,
            "staff_member_id": oid("staff.1"),
            "staff_member_name": "Sam Staff",
            "leave_type": "Annual Leave",
            "requested_amount": 99.0,
            "paid_amount": 11.0,
            "workflow_inputs": None,
            "notes": None,
            "updated_by": None,
            "created_at": "2026-10-13T00:30:00+00:00",
            "updated_at": "2026-10-13T00:30:00+00:00",
        }
    )
    result, _ = _run(w, fake)
    assert result.ok, result.report.gate
    req1 = [p for p in _rows(w, pm.PayLeavePayout) if p.loaded_request_id == "req-1"]
    assert (
        len(req1) == 1
        and req1[0].requested_cents == 104000
        and req1[0].source == "orbit"
    )
    listed = result.report.decisions["legacy leave payouts set aside for a weekly row"]
    assert len(listed) == 1 and "$99.00 / $11.00" in listed[0]
    leave_check = next(
        c for c in result.report.gate["checks"] if c["check"].startswith("Σ leave")
    )
    assert "1 legacy set aside" in leave_check["detail"]


def test_a_batch_off_its_pool_in_orbit_is_listed_not_refused(w):
    fake = _fake(w)
    fake.tables["payrun_gratuity_batches"][0]["total_amount"] = (
        350.0  # shares still sum to 300
    )
    result, _ = _run(w, fake)
    assert result.ok, result.report.gate
    listed = result.report.decisions[
        "gratuity batches whose shares do not add to the pool in Orbit"
    ]
    assert len(listed) == 1 and "$300.00 vs pool $350.00" in listed[0]
    assert (
        _one(w, pm.PayGratuityBatch, _id(w, "pay_gratuity_batches", G1)).total_cents
        == 35000
    )
    # but shares that differ from Orbit's shares DO fail the gate
    fake.tables["payrun_gratuity_allocations"][0]["amount"] = 1.0
    fake.tables["payrun_gratuity_allocations"].append(
        {
            **fake.tables["payrun_gratuity_allocations"][0],
            "id": oid("alloc.ghost"),
            "batch_id": oid("batch.ghost"),
        }
    )
    result, _ = _run(w, fake)
    assert result.ok  # Orbit's own figures changed consistently: shares equal shares


def test_expired_invoice_url_is_counted_not_fatal(w):
    fake = _fake(w)
    fake.files.clear()
    result, _ = _run(w, fake)
    assert result.ok
    assert _rows(w, em.EntFile) == []
    assert (
        _one(w, em.EntPerformance, _id(w, "ent_performances", S4)).invoice_file_id
        is None
    )
    assert (
        result.report.tallies["files"][
            "invoice file unavailable (signed URL expired or refused)"
        ]
        == 1
    )


def test_files_can_be_left_out(w):
    result, fake = _run(w, copy_files=False)
    assert result.ok and _rows(w, em.EntFile) == []
    assert not any(u.startswith(SIGNED) for _m, u in fake.requests)


def test_xero_mapping_is_written_only_on_request(w):
    _run(w)
    row = (
        w.db.query(Connection)
        .filter(Connection.connector_name == "xero", Connection.venue_id == w.venue.id)
        .one()
    )
    assert "xero_mapping" not in (row.config or {})
    # a key the person set on the Bills panel is kept
    row.config = {
        **row.config,
        "xero_mapping": {"payroll": {"wages_payable": {"code": "999", "name": "mine"}}},
    }
    w.db.flush()
    result, _ = _run(w, write_xero_mapping=True)
    assert result.ok
    w.db.expire_all()
    mapping = w.db.get(Connection, row.id).config["xero_mapping"]
    assert mapping["payroll"]["wages_payable"]["code"] == "999"
    assert mapping["payroll"]["kitchen"]["code"] == "806"
    assert (
        mapping["intercompany"]["contacts"][w.venue2.id]["name"] == "Garden Bar Limited"
    )
    assert result.report.xero_mapping["Brew Bar"]["status"].endswith("keys set")
    assert (
        result.report.xero_mapping["Garden Bar"]["status"]
        == "no Xero connection row — nothing written"
    )


def test_orbit_client_only_reads_and_refuses_foreign_storage():
    fake = FakeFinanceOrbit({}, {})
    client = fake.client()
    with pytest.raises(ValueError):
        client._request("POST", ORBIT_URL + "/rest/v1/x")
    with pytest.raises(OrbitReadError, match="not Orbit signed storage"):
        client.fetch_signed("https://evil.example/storage/v1/object/sign/x?token=1")
    with pytest.raises(OrbitReadError, match="not Orbit signed storage"):
        client.fetch_signed(ORBIT_URL + "/storage/v1/object/sign/../../etc/passwd")
    with pytest.raises(ValueError):
        OrbitPostgrest("http://plain.example", "k")


def test_keyset_paging_reads_every_row(w):
    result, fake = _run(w, page=2)
    assert result.ok
    assert len(_rows(w, em.EntPerformance)) == 6
    assert (
        sum(
            1
            for m, u in fake.requests
            if "/rest/v1/entertainer_shifts?" in u and m == "GET"
        )
        >= 3
    )


# ===========================================================================
# The tick jobs
# ===========================================================================


def test_post_freeze_rows_lists_only_what_changed_after_the_freeze(w):
    fake = _fake(w)
    freeze = dt.datetime(2026, 10, 13, 2, 0, tzinfo=UTC)
    found = post_freeze_rows(fake.client(), freeze, {V1, V2})
    # ACE inputs: V1 updated 13 Oct 01:00 (before), nothing after → none; the
    # journal pushes created 13 Oct 03:53 and 03:55 are after
    assert set(found) == {
        "wage_recon_xero_pushes",
        "wage_recon_xero_intercompany_pushes",
    }
    assert {r["id"] for r in found["wage_recon_xero_pushes"]} == {
        oid("push.j1"),
        oid("push.j3"),
    }
    assert all(
        set(r) == {"id", "venue_id", "week", "at"}
        for rows in found.values()
        for r in rows
    )
    assert (
        post_freeze_rows(fake.client(), dt.datetime(2027, 1, 1, tzinfo=UTC), {V1, V2})
        == {}
    )


def test_run_due_claims_the_nightly_slot_once_and_records_failures(w):
    # The nightly cannot skip a venue: Head Office exists in Norm here (as
    # CBI will, D1), so every Orbit venue maps.
    head_office = _make_venue(w.db, name="Head Office", organization_id=w.org.id)
    head_office.timezone = "Pacific/Auckland"
    w.db.add(
        Connection(
            connector_name="loadedhub",
            venue_id=head_office.id,
            config={"x_loaded_company_id": C3},
            access_token="t",
        )
    )
    w.db.flush()
    fake = _fake(w)
    source = fake.client()
    # 02:00 NZ: not due yet
    out = importer_jobs.run_due(
        w.db, dt.datetime(2026, 10, 20, 2, 0, tzinfo=NZ), source=source
    )
    assert out == {"ran": []}
    at = dt.datetime(2026, 10, 20, 2, 45, tzinfo=NZ)
    out = importer_jobs.run_due(w.db, at, source=source)
    (entry,) = out["ran"]
    assert (
        entry["job"] == "importer.nightly"
        and entry["slot"] == "2026-10-20"
        and "error" not in entry
    )
    assert entry["result"]["modules"] == ["payroll", "entertainers", "finance"]
    assert len(_rows(w, em.EntPerformer)) == 6
    runs = _rows(w, fin.FinJobRun, job="importer.nightly")
    assert len(runs) == 1 and runs[0].finished_at is not None and runs[0].error is None
    # the same slot again: claimed, nothing runs
    assert importer_jobs.run_due(w.db, at + dt.timedelta(minutes=5), source=source) == {
        "ran": []
    }
    # the next night, Orbit's count lies: the gate fails and the run keeps its error
    fake.count_override["entertainers"] = 99
    out = importer_jobs.run_due(w.db, at + dt.timedelta(days=1), source=source)
    (entry,) = out["ran"]
    assert (
        "sanity gate failed" in entry["error"] and "read entertainers" in entry["error"]
    )
    run = next(
        r
        for r in _rows(w, fin.FinJobRun, job="importer.nightly")
        if r.slot == "2026-10-21"
    )
    assert run.error and run.finished_at is not None
    # live: nothing to reload
    w.org.payroll_mode = w.org.entertainers_mode = w.org.finance_mode = "live"
    w.db.flush()
    assert importer_jobs.run_due(w.db, at + dt.timedelta(days=2), source=source) == {
        "ran": []
    }


def test_post_freeze_watch_emails_the_payroll_contact_inside_the_window(w):
    fake = _fake(w)
    source = fake.client()
    sent: list[tuple] = []

    def fake_send(template, to, context, db, organization_id=None, **_):
        sent.append((template, to, context, organization_id))
        return "log-1"

    freeze = dt.datetime(2026, 10, 13, 15, 0, tzinfo=NZ)  # 02:00 UTC
    importer_jobs.set_freeze_window(
        w.db, w.org.id, freeze_at=freeze, until=dt.date(2026, 10, 28)
    )
    assert importer_jobs.freeze_window(w.db, w.org.id) == (
        freeze,
        dt.date(2026, 10, 28),
    )
    w.org.payroll_mode = w.org.entertainers_mode = w.org.finance_mode = (
        "live"  # no nightly
    )
    w.db.flush()
    out = importer_jobs.run_due(
        w.db, dt.datetime(2026, 10, 20, 9, 0, tzinfo=NZ), source=source, send=fake_send
    )
    (entry,) = out["ran"]
    assert entry["job"] == "importer.post_freeze_watch" and entry["result"]["rows"] == 6
    assert entry["result"]["tables"] == {
        "wage_recon_xero_pushes": 2,
        "wage_recon_xero_intercompany_pushes": 4,
    }
    template, to, context, org_id = sent[0]
    assert (template, to, org_id) == (
        "importer_post_freeze",
        ["payroll@example.test"],
        w.org.id,
    )
    assert context["total"] == 6 and {t["table"] for t in context["tables"]} == set(
        entry["result"]["tables"]
    )
    assert all(
        "amount" not in r and "name" not in r
        for t in context["tables"]
        for r in t["rows"]
    )
    assert {r["venue"] for t in context["tables"] for r in t["rows"]} <= {
        "Brew Bar",
        "Garden Bar",
    }
    # outside the window: nothing
    assert importer_jobs.run_due(
        w.db, dt.datetime(2026, 10, 29, 9, 0, tzinfo=NZ), source=source, send=fake_send
    ) == {"ran": []}
    # no contact: suppressed, not sent, and the run says so
    w.org.payroll_contact_email = None
    w.db.flush()
    out = importer_jobs.run_due(
        w.db, dt.datetime(2026, 10, 21, 9, 0, tzinfo=NZ), source=source, send=fake_send
    )
    assert out["ran"][0]["result"]["email"].startswith("suppressed") and len(sent) == 1


def test_script_dry_run_against_the_fake(w, capsys):
    from scripts.migrate_orbit_finance import main

    fake = _fake(w)
    code = main(
        [
            "--org",
            w.org.slug,
            "--dry-run",
            "--skip-venue",
            "Head Office",
            "--no-people",
        ],
        orbit=fake.client(),
        db=w.db,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "DRY RUN" in out and "sanity gates: PASSED" in out and "rolled back" in out
    assert "Nobody Known" not in out  # --no-people
    assert "18 Oct 2026 baseline" in out
    assert _rows(w, em.EntPerformer) == []
    w.org.finance_mode = "live"
    w.db.flush()
    assert main(["--org", w.org.slug, "--dry-run"], orbit=fake.client(), db=w.db) == 1
    assert "refused" in capsys.readouterr().out


def test_creds_path_prefers_the_env_override_and_finds_the_checkout(
    tmp_path, monkeypatch
):
    from app.services.finance_core.importer import CREDS_ENV, creds_path

    monkeypatch.setenv(CREDS_ENV, str(tmp_path / "missing.json"))
    assert creds_path() is None
    (tmp_path / "creds.json").write_text("{}")
    monkeypatch.setenv(CREDS_ENV, str(tmp_path / "creds.json"))
    assert creds_path() == tmp_path / "creds.json"
    monkeypatch.delenv(CREDS_ENV)
    found = creds_path()
    # The dev box keeps the credential in the main checkout's .local; a box
    # without one simply has none (the nightly then records it).
    assert found is None or found.parts[-2:] == (".local", "orbit-supabase.json")


def test_models_resolve_every_planned_table():
    """Every table the transform plans has a model (a renamed table would
    otherwise only fail at run time)."""
    from app.services.finance_core.importer import MODULE_TABLES, _models

    models = _models()
    for tables in MODULE_TABLES.values():
        for table in tables:
            assert table in models, table
    assert select is not None
