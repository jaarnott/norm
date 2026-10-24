"""Create / update the ``xero`` connector spec — Phase 0 of docs/plans/xero-connector.md.

Writes ONE row to the shared config DB, ``connector_specs`` / ``xero``:

- OAuth2 authorization-code for a confidential Xero *web app* (client secret,
  no PKCE), Xero's global identity endpoints, and the wide granular scope list
  settled in the plan's §2.3. Xero scopes are requested at authorize time, not
  configured on the app, so this string IS the "which scopes" decision — change
  it and every connected venue has to reconsent.
- The raw endpoint rows (``engine_only`` — never shown to an LLM, no transform;
  a consolidator shapes what it reads). Every row carries the ``xero-tenant-id``
  header from the venue's ``creds``; the connect hook (Phase 1) writes that id.
- The LLM-facing tools (Phase 2): the four read consolidators from
  ``config/consolidators/xero_*.py`` — ``xero_common.py`` is prepended to each
  file's function_code, since the sandbox allows no imports — with their
  ``effect`` from ``sync_approval_labels``. ``get_bills`` carries the
  ``xero_bills`` display component.
- The Finance AI agent's ``agent_configs`` row (slug ``finance``). In
  sync_marketplace_catalog.py the Bills App claims the bill tools and the
  Payroll App the three payroll writes — run that seed AFTER this script, it
  refuses an unknown agent.

NOT in this file: ``client_id`` / ``client_secret``. Paste them in Settings →
Connector specs → Xero (OAuth section) once the row exists. Re-runs preserve
them, and preserve ``tools`` and any endpoint row this script doesn't own.

Payroll (Oct 2026, docs/plans/finance-migration.md §7.2, §10): four
more endpoint rows (``create_manual_journal``, ``get_manual_journal``,
``update_manual_journal``, ``attach_to_invoice`` — a binary body through the
executor's ``body_file_id``), the three payroll write tools
(``post_wage_journal``, ``post_intercompany_documents``,
``void_payroll_document``, each ``requires: ["payroll:write"]``),
``requires`` on the five existing tools (owner decision D8), and
``attachment_file_id`` on ``create_bill``. The payroll tools' approval labels
are carried inline here until scripts/sync_approval_labels.py lists them
(F11); the labels script wins once it does.

Not yet here: an ``If-Modified-Since`` header on invoice lists (an empty
header template would be sent as an empty header).

The config DB is shared by every environment, so committing here is live
everywhere at once — harmless until a venue connects, and invisible to the
agent until an App claims a tool.

Usage:
    .venv/bin/python scripts/sync_xero_connector.py --dry-run
    .venv/bin/python scripts/sync_xero_connector.py
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "xero"
DISPLAY_NAME = "Xero"
BASE_URL = "https://api.xero.com/api.xro/2.0"

# Every *open* granular scope (plan §2.3). Deliberately absent, because one
# unentitled scope fails the whole authorize step with ``invalid_scope``:
# the deprecated broad scopes (accounting.transactions, accounting.reports.read),
# accounting.journals.read (Advanced tier + Xero approval), bankfeeds,
# paymentservices and finance.* (financial-services contracts), payroll.*
# (approval unconfirmed; payroll lives in Loaded), accounting.classicexpenses
# (deprecated endpoint). Write scopes include read, so no ``.read`` twins.
SCOPES = " ".join(
    [
        "openid",
        "profile",
        "email",
        "offline_access",
        "accounting.invoices",
        "accounting.payments",
        "accounting.banktransactions",
        "accounting.manualjournals",
        "accounting.contacts",
        "accounting.attachments",
        "accounting.settings",
        "accounting.budgets.read",
        "accounting.reports.profitandloss.read",
        "accounting.reports.balancesheet.read",
        "accounting.reports.aged.read",
        "accounting.reports.banksummary.read",
        "accounting.reports.executivesummary.read",
        "accounting.reports.trialbalance.read",
        "accounting.reports.taxreports.read",
        "files",
        "assets",
        "projects",
    ]
)

OAUTH_CONFIG = {
    "authorize_url": "https://login.xero.com/identity/connect/authorize",
    "token_url": "https://identity.xero.com/connect/token",
    "scopes": SCOPES,
    # A web app authenticates with its secret; PKCE is Xero's public-client flow.
    "pkce": False,
    # Read by refresh_all_tokens once Phase 1 lands: Xero access tokens live 30
    # minutes and refresh tokens rotate on every use, so redeem only when the
    # access token is expiring — unlike LoadedHub, whose ~24h refresh-token
    # lifetime is why the keep-alive force-redeems every 15 minutes.
    "refresh_policy": "expiring",
}

# Fields the running app (or a person) writes onto oauth_config — never clobber
# them when re-running this script.
_PRESERVE_OAUTH_KEYS = ("client_id", "client_secret")

# Written by the connect hook, not typed by a person (the connect card shows a
# credential form only for non-OAuth connectors). Listed so Settings documents
# what a venue row carries.
CREDENTIAL_FIELDS = [
    {
        "key": "xero_tenant_id",
        "label": "Xero organisation id (set when the venue connects)",
        "secret": False,
        "required": False,
    },
]

TENANT_HEADERS = {
    "xero-tenant-id": "{{ creds.xero_tenant_id }}",
    "Accept": "application/json",
}


def _ep(
    action: str,
    method: str,
    path: str,
    description: str,
    *,
    required: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
    fields: dict[str, str] | None = None,
    body: str = "",
    headers: dict[str, str] | None = None,
    read_only: bool = True,
    body_file_id: str | None = None,
) -> dict:
    """One endpoint row. ``body_file_id`` names the field holding a file id:
    the executor then sends that file's bytes as the body with its content
    type instead of a rendered template (app/connectors/spec_executor.py)."""
    row = {
        "action": action,
        "description": description,
        "method": method,
        "path_template": path,
        "headers": {**TENANT_HEADERS, **(headers or {})},
        "required_fields": list(required),
        "optional_fields": list(optional),
        "field_mapping": {f: f for f in (*required, *optional)},
        "field_descriptions": fields or {},
        "request_body_template": body,
        "success_status_codes": [200],
        "response_ref_path": "",
        "timeout_seconds": 30,
        "read_only": read_only,
        "engine_only": True,
    }
    if body:
        row["headers"]["Content-Type"] = "application/json"
    if body_file_id:
        row["body_file_id"] = body_file_id
    return row


_WHERE = (
    "Xero 'where' filter, e.g. Status==\"ACTIVE\" or "
    'Type=="ACCPAY"&&Status=="AUTHORISED". Optional.'
)
_PAGE = "Page number (Xero returns 100 rows a page). Default 1."

ENDPOINTS = [
    _ep(
        "get_organisation",
        "GET",
        "/Organisation",
        "The connected organisation: Name, CountryCode, BaseCurrency, ShortCode "
        "(for deep links), Timezone. Read once by the connect hook.",
    ),
    _ep(
        "list_accounts",
        "GET",
        "/Accounts{% if where %}?where={{ where }}{% endif %}",
        "Chart of accounts: Code, Name, Type, Class, TaxType, Status.",
        optional=("where",),
        fields={"where": _WHERE},
    ),
    _ep(
        "list_tax_rates",
        "GET",
        "/TaxRates",
        "Tax rates for this organisation (TaxType codes differ by country: "
        "NZ INPUT2 15%, AU INPUT 10%).",
    ),
    _ep(
        "list_contacts",
        "GET",
        "/Contacts?page={{ page or 1 }}"
        "{% if search_term %}&searchTerm={{ search_term }}{% endif %}"
        "{% if where %}&where={{ where }}{% endif %}"
        "{% if include_archived %}&includeArchived=true{% endif %}",
        "Customers and suppliers, 100 a page. searchTerm matches Name, "
        "EmailAddress, ContactNumber.",
        optional=("page", "search_term", "where", "include_archived"),
        fields={
            "page": _PAGE,
            "search_term": "Case-insensitive match on name, email, contact number",
            "where": _WHERE,
            "include_archived": "'true' to include archived contacts",
        },
    ),
    _ep(
        "create_contact",
        "PUT",
        "/Contacts?summarizeErrors=false",
        "Create a contact (supplier or customer). PUT strictly creates.",
        required=("contact",),
        fields={
            "contact": "Xero Contact object: Name (required), EmailAddress, "
            "TaxNumber, IsSupplier/IsCustomer, Phones, Addresses"
        },
        body='{"Contacts": [{{ contact | tojson }}]}',
        read_only=False,
    ),
    _ep(
        "list_invoices",
        "GET",
        "/Invoices?page={{ page or 1 }}"
        "{% if where %}&where={{ where }}{% endif %}"
        "{% if statuses %}&Statuses={{ statuses }}{% endif %}"
        "{% if order %}&order={{ order }}{% endif %}"
        "{% if contact_ids %}&ContactIDs={{ contact_ids }}{% endif %}"
        "{% if invoice_numbers %}&InvoiceNumbers={{ invoice_numbers }}{% endif %}",
        "Invoices and bills (Type ACCREC / ACCPAY), 100 a page, headers only "
        "(no line items). Filter unpaid bills with Statuses=AUTHORISED,SUBMITTED.",
        optional=(
            "page",
            "where",
            "statuses",
            "order",
            "contact_ids",
            "invoice_numbers",
        ),
        fields={
            "page": _PAGE,
            "where": _WHERE,
            "statuses": "Comma-separated: DRAFT,SUBMITTED,AUTHORISED,PAID,VOIDED",
            "order": "e.g. DueDate or Date DESC",
            "contact_ids": "Comma-separated ContactIDs",
            "invoice_numbers": "Comma-separated invoice numbers",
        },
    ),
    _ep(
        "get_invoice",
        "GET",
        "/Invoices/{{ invoice_id }}",
        "One invoice or bill with its line items and attachment flags.",
        required=("invoice_id",),
        fields={"invoice_id": "Xero InvoiceID (GUID) or InvoiceNumber"},
    ),
    _ep(
        "create_invoice",
        "PUT",
        "/Invoices?unitdp=4&summarizeErrors=false",
        "Create an invoice (ACCREC) or bill (ACCPAY). PUT strictly creates; the "
        "Idempotency-Key makes a retry safe.",
        required=("invoice", "idempotency_key"),
        fields={
            "invoice": "Xero Invoice object: Type, Contact{ContactID}, Date, "
            "DueDate, LineAmountTypes, Reference, Status (DRAFT by default), "
            "LineItems[{Description, Quantity, UnitAmount, AccountCode, "
            "TaxType, Tracking[]}]",
            "idempotency_key": "Stable key for this write, e.g. the Norm "
            "invoice id (≤128 chars)",
        },
        body='{"Invoices": [{{ invoice | tojson }}]}',
        headers={"Idempotency-Key": "{{ idempotency_key }}"},
        read_only=False,
    ),
    _ep(
        "update_invoice",
        "POST",
        "/Invoices/{{ invoice_id }}?unitdp=4&summarizeErrors=false",
        "Update an invoice or bill — status changes (AUTHORISED, VOIDED), "
        "reference, due date. Only DRAFT/SUBMITTED lines can change.",
        required=("invoice_id", "invoice"),
        fields={
            "invoice_id": "Xero InvoiceID (GUID)",
            "invoice": 'Partial Xero Invoice object, e.g. {"Status": "VOIDED"}',
        },
        body='{"Invoices": [{{ invoice | tojson }}]}',
        read_only=False,
    ),
    _ep(
        "profit_and_loss",
        "GET",
        "/Reports/ProfitAndLoss?fromDate={{ from_date }}&toDate={{ to_date }}"
        "{% if periods %}&periods={{ periods }}&timeframe={{ timeframe or 'MONTH' }}{% endif %}"
        "{% if tracking_category_id %}&trackingCategoryID={{ tracking_category_id }}{% endif %}",
        "Profit and loss for a date range, optionally split into periods or by "
        "a tracking category.",
        required=("from_date", "to_date"),
        optional=("periods", "timeframe", "tracking_category_id"),
        fields={
            "from_date": "YYYY-MM-DD",
            "to_date": "YYYY-MM-DD",
            "periods": "Number of comparison periods (1-11)",
            "timeframe": "MONTH, QUARTER or YEAR (with periods)",
            "tracking_category_id": "Split columns by this tracking category",
        },
    ),
    # --- Payroll (Oct 2026): manual journals and attachments -------------
    _ep(
        "create_manual_journal",
        "PUT",
        "/ManualJournals?summarizeErrors=false",
        "Create a manual journal (the payroll journal: DRAFT, NoTax, balanced "
        "lines). PUT strictly creates; the Idempotency-Key makes a retry safe. "
        "summarizeErrors=false returns 200 with ManualJournals[0].HasErrors + "
        "ValidationErrors on a bad payload.",
        required=("journal", "idempotency_key"),
        fields={
            "journal": "Xero ManualJournal object: Narration, Date, Status (DRAFT), "
            "LineAmountTypes (NoTax), JournalLines[{LineAmount (+debit/−credit), "
            "AccountCode, Description, Tracking[]}]",
            "idempotency_key": "Stable key for this write (≤128 chars), e.g. "
            "norm:payroll-journal:<org>:<venue>:<week>:<hash>",
        },
        body='{"ManualJournals": [{{ journal | tojson }}]}',
        headers={"Idempotency-Key": "{{ idempotency_key }}"},
        read_only=False,
    ),
    _ep(
        "get_manual_journal",
        "GET",
        "/ManualJournals/{{ manual_journal_id }}",
        "One manual journal with its lines and Status (DRAFT, POSTED, DELETED, "
        "VOIDED) — the weekly status refresh and the void tool read it.",
        required=("manual_journal_id",),
        fields={"manual_journal_id": "Xero ManualJournalID (GUID)"},
    ),
    _ep(
        "update_manual_journal",
        "POST",
        "/ManualJournals/{{ manual_journal_id }}?summarizeErrors=false",
        "Update a manual journal — used to void or delete one "
        '({"Status": "VOIDED"} for a posted journal, "DELETED" for a draft).',
        required=("manual_journal_id", "journal"),
        fields={
            "manual_journal_id": "Xero ManualJournalID (GUID)",
            "journal": 'Partial Xero ManualJournal object, e.g. {"Status": "VOIDED"}',
        },
        body='{"ManualJournals": [{{ journal | tojson }}]}',
        read_only=False,
    ),
    _ep(
        "attach_to_invoice",
        "PUT",
        "/Invoices/{{ invoice_id }}/Attachments/{{ filename }}",
        "Attach a file Norm holds (an ent_files / pay_files id) to an invoice or "
        "bill. The body is the file's bytes with its content type (the "
        "executor's body_file_id); a PUT creates, a file of the same name is "
        "replaced by Xero on a POST.",
        required=("invoice_id", "filename", "file_id"),
        fields={
            "invoice_id": "Xero InvoiceID (GUID)",
            "filename": "The attachment's name in Xero, with its extension",
            "file_id": "The Norm file id whose bytes are sent",
        },
        read_only=False,
        body_file_id="file_id",
    ),
]


_CONSOLIDATORS = (
    pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
)


def _function_code(name: str) -> str:
    common = (_CONSOLIDATORS / "xero_common.py").read_text(encoding="utf-8")
    body = (_CONSOLIDATORS / name).read_text(encoding="utf-8")
    return common + "\n\n" + body


def _schema(**fields: str) -> dict:
    """field_schema: every parameter a string (what a model sends anyway)."""
    return {k: {"type": "string", "description": v} for k, v in fields.items()}


_BILLS_FIELDS = dict(
    status=(
        "'unpaid' (default: awaiting payment + awaiting approval), "
        "'awaiting_approval', 'draft', 'paid' or 'all'"
    ),
    period="Bill DATE window in plain English ('last month') — Norm resolves it",
    from_date="Only with to_date: bill date from, YYYY-MM-DD",
    to_date="Bill date to, YYYY-MM-DD",
    supplier="Case-insensitive supplier-name substring",
    limit="Max bills returned (default 200)",
)
_CONTACT_FIELDS = dict(
    query="Name / email / contact-number search (Xero's searchTerm)",
    kind="'supplier' (default), 'customer' or 'all'",
    include_archived="'true' to include archived contacts",
    limit="Max contacts returned (default 100)",
)
_ACCOUNT_FIELDS = dict(
    kind=(
        "'expense' (default: direct costs, expenses, overheads — where bill "
        "lines go), 'revenue', 'bank' or 'all'"
    ),
    query="Case-insensitive substring of the account code or name",
    limit="Max accounts returned (default 200)",
)
_PNL_FIELDS = dict(
    period="Plain-English period ('last month', 'this quarter') — Norm resolves it",
    from_date="Only with to_date: YYYY-MM-DD",
    to_date="YYYY-MM-DD",
    periods="Comparison periods to add (1-11), e.g. '5' for a 6-month trend",
    timeframe="MONTH (default), QUARTER or YEAR — the size of each comparison period",
    tracking_category_id="Split the columns by this Xero tracking category",
)

TOOLS = [
    {
        "action": "get_bills",
        "method": "GET",
        "read_only": True,
        "description": (
            "THE supplier-bill lookup in Xero (bills = ACCPAY invoices). "
            "status 'unpaid' (default) is awaiting payment + awaiting approval; "
            "'draft', 'paid', 'all'. period or from_date/to_date narrow by bill "
            "date; supplier filters by name. Returns summary (count, total, "
            "amount due, overdue, due in 7 days), by_supplier, and bills (number, "
            "supplier, date, due date, status, amounts, currency, a link into "
            "Xero). The bills view renders this result — reply with what the "
            "tiles don't say, not a re-typed table. Never quote an amount, due "
            "date or account you haven't read from the xero tools. If a tool "
            "says a venue isn't connected to Xero, show the connect card "
            "(norm.show_connect, connector_name 'xero')."
        ),
        "required_fields": [],
        "optional_fields": list(_BILLS_FIELDS),
        "field_descriptions": _BILLS_FIELDS,
        "field_schema": _schema(**_BILLS_FIELDS),
        "requires": ["finance:read"],
        "max_result_chars": 40_000,
        "display_component": "xero_bills",
        "display_props": {"title": "Bills in Xero"},
        "consolidator_config": {
            "max_api_calls": 8,
            "function_file": "get_bills.py",
        },
    },
    {
        "action": "get_contacts",
        "method": "GET",
        "read_only": True,
        "description": (
            "Suppliers and customers in the venue's Xero organisation: id, name, "
            "email, phone, tax number, supplier/customer flags. query searches "
            "by name; kind defaults to suppliers. Use it to find the Xero contact "
            "a bill should go to."
        ),
        "required_fields": [],
        "optional_fields": list(_CONTACT_FIELDS),
        "field_descriptions": _CONTACT_FIELDS,
        "field_schema": _schema(**_CONTACT_FIELDS),
        "requires": ["finance:read"],
        "max_result_chars": 30_000,
        "consolidator_config": {
            "max_api_calls": 4,
            "allowed_write_actions": [],
            "function_file": "get_contacts.py",
        },
    },
    {
        "action": "get_accounts",
        "method": "GET",
        "read_only": True,
        "description": (
            "The venue's Xero chart of accounts (code, name, type, default tax "
            "type) and its tax rates (NZ INPUT2 15%, AU INPUT 10%, NONE…). kind "
            "'expense' (default) lists where supplier-bill lines post; 'revenue', "
            "'bank', 'all'. Read this before choosing an account code."
        ),
        "required_fields": [],
        "optional_fields": list(_ACCOUNT_FIELDS),
        "field_descriptions": _ACCOUNT_FIELDS,
        "field_schema": _schema(**_ACCOUNT_FIELDS),
        "requires": ["finance:read"],
        "max_result_chars": 30_000,
        "consolidator_config": {
            "max_api_calls": 3,
            "allowed_write_actions": [],
            "function_file": "get_accounts.py",
        },
    },
    {
        "action": "get_profit_and_loss",
        "method": "GET",
        "read_only": True,
        "description": (
            "Xero's Profit & Loss for a period: headline totals (income, cost of "
            "sales, gross profit, operating expenses, net profit), every section "
            "with its lines, and one row per period column in `months` (oldest "
            "first) when periods is given. Figures are Xero's — accrual, ex-GST."
        ),
        "required_fields": [],
        "optional_fields": list(_PNL_FIELDS),
        "field_descriptions": _PNL_FIELDS,
        "field_schema": _schema(**_PNL_FIELDS),
        # D8: the Xero P&L holds the group's pay figures.
        "requires": ["payroll:read"],
        "max_result_chars": 40_000,
        "consolidator_config": {
            "max_api_calls": 3,
            "function_file": "get_profit_and_loss.py",
        },
    },
]

# Engine-only row on the `norm` spec for the built-in every xero consolidator
# calls first (app/agents/internal_tools.py: get_connection_settings). The
# sandbox resolves a tool from the spec row BEFORE routing to the handler, so
# without this row every call dies "Tool not found".
NORM_SETTINGS_TOOL = {
    "action": "get_connection_settings",
    "method": "GET",
    "read_only": True,
    "engine_only": True,
    "effect": "read",
    "description": (
        "Engine-only. The non-secret settings on a venue's connection row — "
        "for Xero: organisation, country, currency, short code and the account "
        "mapping — and whether that venue is connected at all. Consolidators "
        "call it first; never shown to the model."
    ),
    "required_fields": ["connector"],
    "optional_fields": ["venue", "venue_id"],
    "field_schema": {
        "connector": {"type": "string", "description": "Connector name, e.g. 'xero'"},
        "venue": {"type": "string", "description": "Venue name"},
        "venue_id": {"type": "string", "description": "Venue id"},
    },
}

_BILL_FIELDS = dict(
    supplier="Supplier name as the bill shows it — matched to a Xero contact, created if missing",
    supplier_contact_id="Xero ContactID when known (skips the name match)",
    invoice_number="The supplier's invoice number (Xero's InvoiceNumber on a bill)",
    reference="Optional reference",
    date="Bill date, YYYY-MM-DD",
    due_date="Due date, YYYY-MM-DD; default = date + the venue's due_days (20)",
    lines=(
        "JSON array of lines: [{description, quantity, unit_amount, account_code?, "
        "category? ('food'|'beverage'|'other' → the venue's mapping), gst? "
        "('yes'|'no'), tax_type?, total?}]"
    ),
    line_amount_types="'Exclusive' (default: unit amounts ex GST), 'Inclusive' or 'NoTax'",
    status="'DRAFT' (default, or the venue's bill_status), 'SUBMITTED' or 'AUTHORISED'",
    create_contact="'false' to refuse rather than create a missing supplier contact",
    idempotency_key="Stable key for this bill (default: venue + supplier + number); a retry never doubles up",
    attachment_file_id="A file Norm holds (an ent_files / pay_files id) to attach to the bill after it is created",
    attachment_filename="The attachment's name in Xero (default: the invoice number + .pdf)",
)
_BILL_SCHEMA = _schema(**{k: v for k, v in _BILL_FIELDS.items() if k != "lines"})
_BILL_SCHEMA["lines"] = {
    "type": "array",
    "description": _BILL_FIELDS["lines"],
    "items": {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "quantity": {"type": "number"},
            "unit_amount": {"type": "number"},
            "total": {"type": "number"},
            "account_code": {"type": "string"},
            "category": {"type": "string"},
            "gst": {"type": "string"},
            "tax_type": {"type": "string"},
        },
        "required": ["description"],
    },
}

CREATE_BILL_TOOL = {
    "action": "create_bill",
    "method": "PUT",
    "read_only": False,
    "description": (
        "Create a supplier bill (ACCPAY) in the venue's Xero organisation. "
        "Resolves the supplier to a Xero contact (creating one if missing), "
        "the GST tax types from the organisation's own rates, and each line's "
        "account code (the line's own, else the venue's mapping by category, "
        "else its default). Previews the whole bill for approval; lands as "
        "DRAFT unless told otherwise. Read get_accounts before choosing codes. "
        "Nothing is paid — this records the bill."
    ),
    "required_fields": ["supplier", "date", "lines"],
    "optional_fields": [
        k for k in _BILL_FIELDS if k not in ("supplier", "date", "lines")
    ],
    "field_descriptions": _BILL_FIELDS,
    "field_schema": _BILL_SCHEMA,
    # What receiving an invoice already needs, so bills-after-receive never
    # refuses someone who could receive the invoice (D8).
    "requires": ["orders:write"],
    "max_result_chars": 20_000,
    "consolidator_config": {
        "max_api_calls": 10,
        "allowed_write_actions": [
            "create_contact",
            "create_invoice",
            "attach_to_invoice",
        ],
        "function_file": "create_bill.py",
    },
}
TOOLS.append(CREATE_BILL_TOOL)

# --- The Payroll App's three write tools (plan §10) -----------------------
# Each reads Norm's stored draft through the engine-only built-in
# norm_payroll.get_journal_draft (which refuses unless the person holds
# payroll:write, payroll is live and the run is attended) and records the
# outcome through norm_payroll.record_push. `requires` keeps them out of a
# manager's menu and refuses one who names them anyway; the built-in checks
# the person again.
_JOURNAL_FIELDS = dict(
    venue="Venue name — the payroll entity whose journal this is",
    week_ending="The pay week's Sunday, YYYY-MM-DD (any day of the week is snapped)",
    draft_id="The stored journal draft to push (default: the week's current draft)",
    basis_sha256="The draft's basis hash the person saw; refused if the draft was rebuilt or its inputs moved",
    replace="'true' to void the journal already in Xero for this week and push again",
)
_ICO_FIELDS = dict(
    venue="Venue name — the payroll entity",
    week_ending="The pay week's Sunday, YYYY-MM-DD",
    replace="'true' to void documents already in Xero for this week and re-create them",
)
_VOID_FIELDS = dict(
    push_id="The push-ledger row (pay_xero_pushes id) of the document to void",
)

#: Carried inline so the rows are complete before scripts/sync_approval_labels.py
#: lists them (F11); the labels script's entry wins once it exists.
INLINE_LABELS = {
    "xero.post_wage_journal": {
        "effect": "write",
        "approval": {
            "default": "ask",
            "allow_auto": True,
            "label": "post the payroll journal to Xero",
        },
    },
    "xero.post_intercompany_documents": {
        "effect": "write",
        "approval": {
            "default": "ask",
            "allow_auto": True,
            "label": "post intercompany invoices and bills to Xero",
        },
    },
    "xero.void_payroll_document": {
        "effect": "write",
        "approval": {
            "default": "ask",
            "allow_auto": False,
            "label": "void a payroll document in Xero",
        },
    },
}

PAYROLL_TOOLS = [
    {
        "action": "post_wage_journal",
        "method": "PUT",
        "read_only": False,
        "requires": ["payroll:write"],
        "description": (
            "Post one entity's payroll journal for a pay week to Xero as a DRAFT "
            "manual journal — from the draft Norm built on the Payroll page's "
            "Reconciliation tab (net wages, payroll taxes, unpaid wages, security owed, the "
            "intercompany line, the team cost lines, leave and entitlements, "
            "entertainers, adjustments). Previews every line and the balance "
            "for approval; refuses while a live journal exists unless replace "
            "is 'true'. The accountant posts it inside Xero."
        ),
        "required_fields": ["venue", "week_ending"],
        "optional_fields": ["draft_id", "basis_sha256", "replace"],
        "field_descriptions": _JOURNAL_FIELDS,
        "field_schema": _schema(**_JOURNAL_FIELDS),
        "max_result_chars": 20_000,
        "consolidator_config": {
            "max_api_calls": 10,
            "allowed_write_actions": [
                "create_manual_journal",
                "update_manual_journal",
                "norm_payroll.record_push",
            ],
            "function_file": "post_wage_journal.py",
        },
    },
    {
        "action": "post_intercompany_documents",
        "method": "PUT",
        "read_only": False,
        "requires": ["payroll:write"],
        "description": (
            "Post the week's intercompany recharges for one entity to Xero: a "
            "DRAFT invoice to each entity whose venues its staff worked at, and "
            "a DRAFT bill from each entity whose staff worked here — one line "
            "per person at the labour cost plus the leave-and-entitlements "
            "uplift, GST from the organisation's own rates. Previews every "
            "document for approval; a counterparty with no Xero contact is "
            "refused by name; documents already pushed are skipped unless "
            "replace is 'true'."
        ),
        "required_fields": ["venue", "week_ending"],
        "optional_fields": ["replace"],
        "field_descriptions": _ICO_FIELDS,
        "field_schema": _schema(**_ICO_FIELDS),
        "max_result_chars": 30_000,
        "consolidator_config": {
            "max_api_calls": 60,
            "allowed_write_actions": [
                "create_invoice",
                "update_invoice",
                "norm_payroll.record_push",
            ],
            "function_file": "post_intercompany_documents.py",
        },
    },
    {
        "action": "void_payroll_document",
        "method": "POST",
        "read_only": False,
        "requires": ["payroll:write"],
        "description": (
            "Take one payroll document Norm pushed out of Xero — a wage journal, "
            "an intercompany invoice or bill, or an entertainer bill — by its "
            "push-ledger row. Reads its status first: a draft is deleted, an "
            "authorised document voided, a paid one refused. Always asks."
        ),
        "required_fields": ["push_id"],
        "optional_fields": [],
        "field_descriptions": _VOID_FIELDS,
        "field_schema": _schema(**_VOID_FIELDS),
        "max_result_chars": 10_000,
        "consolidator_config": {
            "max_api_calls": 8,
            "allowed_write_actions": [
                "update_manual_journal",
                "update_invoice",
                "norm_payroll.record_push",
            ],
            "function_file": "void_payroll_document.py",
        },
    },
]
TOOLS.extend(PAYROLL_TOOLS)

FINANCE_AGENT = {
    "agent_slug": "finance",
    "display_name": "Finance",
    "description": (
        "Keeps the books with you: bills and profit and loss from Xero, the "
        "weekly pay run, entertainers, and the sales budget and forecast."
    ),
    "persona": (
        "In here you're the finance assistant: exact about amounts, dates and "
        "GST; you read before you quote; anything that reaches Xero or Loaded "
        "goes through an approval card."
    ),
}


def _tool_rows() -> list[dict]:
    """TOOLS with function_code installed and effect/approval labels merged."""
    from scripts.sync_approval_labels import labels

    label_for = labels()
    rows = []
    for tool in TOOLS:
        row = {k: v for k, v in tool.items() if k != "consolidator_config"}
        cfg = dict(tool["consolidator_config"])
        fname = cfg.pop("function_file")
        cfg["function_code"] = _function_code(fname)
        row["consolidator_config"] = cfg
        key = f"{CONNECTOR}.{tool['action']}"
        fields = label_for.get(key) or INLINE_LABELS.get(key)
        if not fields:
            raise SystemExit(
                f"{key} has no approval label — add it to "
                "scripts/sync_approval_labels.py first"
            )
        row.update(fields)
        rows.append(row)
    return rows


def _merge_tools(existing: list | None) -> list:
    """Upsert this script's tool rows by action; keep rows it doesn't own and
    each row's added_at (the listener stamps new ones)."""
    ours = {row["action"]: row for row in _tool_rows()}
    merged = []
    seen = set()
    for row in existing or []:
        action = row.get("action") if isinstance(row, dict) else None
        if action in ours:
            new = dict(ours[action])
            if row.get("added_at"):
                new["added_at"] = row["added_at"]
            merged.append(new)
            seen.add(action)
        else:
            merged.append(row)
    for action, row in ours.items():
        if action not in seen:
            merged.append(row)
    return merged


def _merge_endpoints(existing: list | None) -> list:
    """Upsert this script's rows by action; keep rows it doesn't own."""
    ours = {row["action"]: row for row in ENDPOINTS}
    merged = []
    seen = set()
    for row in existing or []:
        action = row.get("action") if isinstance(row, dict) else None
        if action in ours:
            merged.append(ours[action])
            seen.add(action)
        else:
            merged.append(row)
    for action, row in ours.items():
        if action not in seen:
            merged.append(row)
    return merged


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CONNECTOR)
            .first()
        )
        if spec:
            merged_oauth = dict(OAUTH_CONFIG)
            existing_oauth = spec.oauth_config or {}
            for key in _PRESERVE_OAUTH_KEYS:
                if existing_oauth.get(key):
                    merged_oauth[key] = existing_oauth[key]
            spec.display_name = DISPLAY_NAME
            spec.category = "accounting"
            spec.execution_mode = "template"
            spec.auth_type = "oauth2"
            spec.auth_config = {"token_field": "access_token"}
            spec.base_url_template = BASE_URL
            spec.credential_fields = CREDENTIAL_FIELDS
            spec.oauth_config = merged_oauth
            spec.endpoints = _merge_endpoints(spec.endpoints)
            spec.tools = _merge_tools(spec.tools)
            spec.enabled = True
            spec.version = (spec.version or 0) + 1
            for col in (
                "auth_config",
                "credential_fields",
                "oauth_config",
                "endpoints",
                "tools",
            ):
                flag_modified(spec, col)
            print(
                f"updating existing '{CONNECTOR}' spec -> version {spec.version} "
                f"(client_id {'kept' if merged_oauth.get('client_id') else 'NOT SET'}, "
                f"client_secret {'kept' if merged_oauth.get('client_secret') else 'NOT SET'}, "
                f"tools={len(spec.tools)}, endpoints={len(spec.endpoints)})"
            )
        else:
            spec = ConnectionSpec(
                connector_name=CONNECTOR,
                display_name=DISPLAY_NAME,
                category="accounting",
                execution_mode="template",
                auth_type="oauth2",
                auth_config={"token_field": "access_token"},
                base_url_template=BASE_URL,
                tools=_tool_rows(),
                endpoints=list(ENDPOINTS),
                credential_fields=CREDENTIAL_FIELDS,
                oauth_config=dict(OAUTH_CONFIG),
                enabled=True,
            )
            db.add(spec)
            print(
                f"creating '{CONNECTOR}' spec (execution_mode=template, "
                f"auth_type=oauth2, tools={len(TOOLS)}, endpoints={len(ENDPOINTS)}); "
                "client_id / client_secret NOT SET — paste them in Settings"
            )

        print(f"  base_url: {BASE_URL}")
        print(f"  scopes ({len(SCOPES.split())}): {SCOPES}")
        print("  endpoints:")
        for row in ENDPOINTS:
            print(f"    {row['method']:<4} {row['action']:<18} {row['path_template']}")
        print("  tools:")
        for row in spec.tools:
            cfg = row.get("consolidator_config") or {}
            print(
                f"    {row['action']:<22} effect={row.get('effect')} "
                f"code={len(cfg.get('function_code') or '')} chars "
                f"display={row.get('display_component') or '-'}"
            )

        # --- the norm spec's engine-only settings row ----------------------
        norm_spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "norm")
            .first()
        )
        if not norm_spec:
            raise SystemExit(
                "no 'norm' connector spec — cannot install get_connection_settings"
            )
        norm_tools = [dict(t) for t in (norm_spec.tools or [])]
        current = next(
            (t for t in norm_tools if t.get("action") == NORM_SETTINGS_TOOL["action"]),
            None,
        )
        wanted = dict(NORM_SETTINGS_TOOL)
        if current and current.get("added_at"):
            wanted["added_at"] = current["added_at"]
        if current != wanted:
            norm_tools = [
                t for t in norm_tools if t.get("action") != NORM_SETTINGS_TOOL["action"]
            ] + [wanted]
            norm_spec.tools = norm_tools
            flag_modified(norm_spec, "tools")
            norm_spec.version = (norm_spec.version or 0) + 1
            print(
                f"  norm.{NORM_SETTINGS_TOOL['action']}: "
                f"{'updated' if current else 'added'} (engine_only)"
            )
        else:
            print(f"  norm.{NORM_SETTINGS_TOOL['action']}: unchanged")

        # --- the Finance member -------------------------------------------
        from app.db.config_models import AgentConfig

        agent = (
            db.query(AgentConfig)
            .filter(AgentConfig.agent_slug == FINANCE_AGENT["agent_slug"])
            .first()
        )
        if agent:
            for k, v in FINANCE_AGENT.items():
                setattr(agent, k, v)
            agent.enabled = True
            print(f"  agent '{agent.agent_slug}': updated")
        else:
            db.add(AgentConfig(**FINANCE_AGENT, enabled=True))
            print(
                f"  agent '{FINANCE_AGENT['agent_slug']}': created (system_prompt NULL)"
            )

        if args.dry_run:
            print("(dry run — nothing written)")
            return 0

        db.commit()
        print("committed")

        from app.services.config_validator import validate_config

        summary = validate_config(config_db=db)
        ours = [
            i for i in summary.get("issues", []) if CONNECTOR in json.dumps(i).lower()
        ]
        print(
            f"validate_config: {'ok' if summary.get('ok') else summary.get('issue_count')} "
            f"issue(s) overall, {len(ours)} mentioning {CONNECTOR}"
        )
        for issue in ours:
            print(f"  - {issue}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
