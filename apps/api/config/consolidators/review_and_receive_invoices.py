# ruff: noqa: F821 — `decimal`, `datetime`, `json`, `math` and
# `extract_document` are injected into the sandbox namespace by
# app/connectors/function_executor.py; they are not imports.
#
# Canonical function_code for the `loadedhub.review_and_receive_invoices`
# consolidator. This file is the reviewed, version-controlled source of truth;
# its contents are synced verbatim into the ConnectorSpec tool's
# consolidator_config.function_code in the config DB (see
# config/consolidators/README.md).
#
# Runs inside the consolidator sandbox (app/connectors/function_executor.py):
# no imports; `math`, `json`, `datetime`, `decimal` modules and the
# `extract_document(...)` helper are injected. Requires consolidator_config:
#   {"max_api_calls": 80, "allowed_write_actions": ["receive_invoice"]}
#
# Contract: reviews draft (unreceived) supplier invoices and AUTOMATICALLY
# RECEIVES the ones that pass every deterministic gate below. The LLM never
# decides what is written — this code does. `dry_run=true` reports without
# writing. Invoices failing any gate are never modified and are reported with
# exact reasons.

PDF_SCHEMA = {
    "document_type": (
        "one of 'invoice', 'credit_note', 'statement', 'other' — what the "
        "document IS. A STATEMENT summarises an account: it lists prior "
        "invoice numbers, payments and balances (e.g. 'Balance Brought "
        "Forward', 'Payment') instead of billing products. An INVOICE bills "
        "products/services with quantities and prices."
    ),
    "supplier_name": "string or null",
    "supplier_differs": (
        "boolean — ONLY meaningful when the instructions name Loaded's "
        "supplier for this invoice: true when the supplier printed on the "
        "document is a DIFFERENT BUSINESS from the named one. Naming "
        "variations of the same business ('Hancocks' vs 'Hancock Ltd' vs "
        "'Hancocks Family Merchants') are the SAME business — false. "
        "Omit/false when unsure or when no Loaded supplier was named."
    ),
    "invoice_number": "string or null",
    "invoice_date": "string or null",
    "purchase_order_number": "string or null",
    "lines": [
        {
            "code": "string or null — the product/item code column",
            "description": "string",
            "quantity": (
                "number — the TOTAL count of individual units billed for the "
                "line, per the quantity rules in the instructions (NOT "
                "necessarily a single printed column)"
            ),
            "unit": "string or null — EXACTLY as printed on the document",
            "unit_of_measure": (
                "string or null — the DELIVERED unit of ONE item, per the "
                "unit rules in the instructions (e.g. 'Kilo', '5L', '500g', "
                "'750ml', '12 pack', '100 piece'); null if not determinable"
            ),
            "unit_unrecognisable": (
                "boolean — true when the document DOES carry size/pack "
                "information for this line but it cannot be confidently "
                "determined: cut off (e.g. a description ending "
                "mid-parenthesis like '(1'), illegible, or ambiguous. "
                "Omit/false when the unit was derived, or when the document "
                "simply prints no size information at all."
            ),
            "unit_price_ex_tax": "number — exactly as printed",
            "line_total_ex_tax": "number — exactly as printed",
        }
    ],
    "subtotal_ex_tax": "number or null",
    "tax_amount": "number or null",
    "total_incl_tax": "number or null",
}

TOTALS_TOL = "0.02"  # user decision: differences <= 2c count as matching
LINE_TOL = "0.01"

# Header-only extraction to read the BUYER's purchase order number off the copy
# for unlinked invoices. Loaded's own purchaseOrderNumber field is often the
# supplier's OWN order number (e.g. Bidfood "O/N"), not the buyer PO that
# matches a Loaded purchase order — the buyer PO is only on the printed copy.
# Resolving it here (once, server-side) means the Fix card can pre-select the
# right PO instantly instead of an LLM call per card in the browser.
PO_EXTRACT_SCHEMA = {
    "customer_purchase_order_number": (
        "string or null — the BUYER's / customer's purchase order number (the "
        "number the buyer raised in their own system), labelled 'Customer Order "
        "No', 'Cust Order No', 'Your Order', 'Your Ref', 'PO Number', 'Order No'"
    ),
    "supplier_order_number": (
        "string or null — the SUPPLIER's own order/reference number (labelled "
        "'O/N', 'Our Order', 'Sales Order', etc.), NOT the buyer's PO"
    ),
}

# Ordered, human-readable labels for every gate — drives the per-invoice
# tick/cross checklist in reports. Keys match the `checks` map; a key absent
# from `checks` means an earlier layer failed first, shown as "—" (not checked).
CHECK_LABELS = [
    ("credit_note", "Not a credit note or statement"),
    ("pdf_present", "Invoice copy attached"),
    ("po_linked", "Linked to a purchase order"),
    ("po_supplier", "Supplier matches the purchase order"),
    ("items_matched", "No NEW stock items, brands or units"),
    ("totals", "Invoice totals consistent"),
    ("pdf_readable", "Invoice copy readable"),
    ("pdf_invoice_number", "Invoice number matches the copy"),
    ("pdf_lines", "Lines match the invoice copy"),
    ("unit_of_measure", "Unit of measure matches the copy"),
    ("pdf_total", "Total matches the invoice copy"),
    # Appended LAST deliberately: the packed `checks` string is positional, so
    # adding at the end keeps every existing index stable (an older cached
    # 11-char string simply decodes this one as "not checked").
    ("duplicate", "No duplicate"),
    ("supplier", "Supplier matches the copy"),
]

# Conservative unit-name normalisation for the invoice-vs-copy unit check.
# Both sides must be RECOGNISED here (or textually identical) before a
# mismatch counts as a failure — supplier PDFs print units too inconsistently
# ("5.6 KG", "CTN8", …) to fail on strings we can't confidently interpret.
UNIT_ALIASES = {
    "kg": "kg",
    "kgs": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "g": "g",
    "gm": "g",
    "gram": "g",
    "grams": "g",
    "l": "l",
    "lt": "l",
    "ltr": "l",
    "litre": "l",
    "liter": "l",
    "litres": "l",
    "liters": "l",
    "ml": "ml",
    "mls": "ml",
    "ea": "ea",
    "each": "ea",
    "unit": "ea",
    "un": "ea",
    "doz": "doz",
    "dozen": "doz",
    "dz": "doz",
    "pk": "pk",
    "pack": "pk",
    "pkt": "pk",
    "packet": "pk",
    "bx": "bx",
    "box": "bx",
    "boxes": "bx",
    "ctn": "ctn",
    "carton": "ctn",
    "cartons": "ctn",
    "cs": "cs",
    "case": "cs",
    "cases": "cs",
    "btl": "btl",
    "bottle": "btl",
    "bottles": "btl",
    "can": "can",
    "cans": "can",
    "bag": "bag",
    "bags": "bag",
    "tray": "tray",
    "trays": "tray",
    "punnet": "punnet",
    "punnets": "punnet",
    "roll": "roll",
    "rolls": "roll",
    "bunch": "bunch",
    "bunches": "bunch",
}

# Unit-of-measure parsing for the guideline-derived unit check. A unit string
# resolves to (type, magnitude) — weight in grams, volume in mls, count in
# items — or None when it can't be confidently interpreted (bare packaging
# words, lengths, free text). Comparison then requires same type AND same
# magnitude; anything unparseable is "not checked", never a failure.
_UOM_WORDS = {
    # word: (type, factor per 1 unit)
    "kg": ("weight", 1000),
    "kgs": ("weight", 1000),
    "kilo": ("weight", 1000),
    "kilos": ("weight", 1000),
    "kilogram": ("weight", 1000),
    "kilograms": ("weight", 1000),
    "g": ("weight", 1),
    "gm": ("weight", 1),
    "gr": ("weight", 1),
    "gram": ("weight", 1),
    "grams": ("weight", 1),
    "l": ("volume", 1000),
    "lt": ("volume", 1000),
    "ltr": ("volume", 1000),
    "litre": ("volume", 1000),
    "liter": ("volume", 1000),
    "litres": ("volume", 1000),
    "liters": ("volume", 1000),
    "ml": ("volume", 1),
    "mls": ("volume", 1),
    "ea": ("count", 1),
    "each": ("count", 1),
    "pc": ("count", 1),
    "pcs": ("count", 1),
    "piece": ("count", 1),
    "pieces": ("count", 1),
    "pack": ("count", 1),
    "pk": ("count", 1),
    "doz": ("count", 12),
    "dozen": ("count", 12),
    "dz": ("count", 12),
    "pair": ("count", 2),
}
# Bare packaging words: never confidently comparable without a count.
_UOM_VAGUE = {
    "pkt",
    "packet",
    "box",
    "carton",
    "ctn",
    "outer",
    "unit",
    "case",
    "cs",
    "bx",
    "un",
}


def parse_unit(text):
    """'500g' → ('weight', 500); '5L' → ('volume', 5000); '12 pack' →
    ('count', 12); 'Kilo' → ('weight', 1000); 'pkt' → None."""
    s = str(text or "").strip().lower()
    if not s:
        return None
    # split into leading number + word, e.g. "5.6 kg", "100piece", "12 pack"
    num, word = "", ""
    for ch in s:
        if ch.isdigit() or (ch == "." and num and "." not in num):
            if word:
                return None  # number after word ("ctn8") — not confident
            num += ch
        elif ch.isalpha():
            word += ch
        elif ch in (" ", "-"):
            continue
        else:
            return None  # "2x5l" and other compounds are the LLM's job
    if word in _UOM_VAGUE:
        return None
    entry = _UOM_WORDS.get(word)
    if not entry:
        return None
    utype, factor = entry
    if not num:
        return (utype, factor)
    try:
        magnitude = float(num) * factor
    except ValueError:
        return None
    return (utype, magnitude)


def _is_multipack(text):
    """True for an 'NxM' compound unit like '5x3kg' / '6x700ml' (digit-x-digit).

    Whitespace-tolerant: extraction keeps units AS PRINTED, and suppliers print
    '6x 750ml' / '4 x 6 pack' — the spaced forms are the same pack (norm-based
    name comparison ignores the spaces too). parse_unit deliberately can't
    compare these, and a ratio-equal but differently-named unit ('15 KG') is
    NOT the same pack, so a multipack delivered unit is compared by name
    instead of magnitude.
    """
    s = "".join(str(text or "").lower().split())
    i = s.find("x")
    return i > 0 and s[i - 1].isdigit() and i + 1 < len(s) and s[i + 1].isdigit()


def _unit_norm(text):
    # Unit-name key: lowercase, whitespace ignored ('6x 750ml' == '6X750ML',
    # 'Each' == 'each') — but digits and DOTS distinguish ('1.9 KG' vs
    # '19 KG'), so punctuation is kept. Mirror of invoice_units._unit_norm.
    return "".join(str(text or "").lower().split())


def _multipack_equal(a, b):
    """True when two unit NAMES denote the same pack.

    Multipacks compare component-wise: the counts must match and the inner
    sizes compare by parse_unit magnitude — '6x1L' == '6 X 1 Litre' (Allied
    Liquor prints the former, Loaded holds the latter). Unparseable inners
    fall back to name equality. A multipack never equals a differently-shaped
    name: '4x6 pack' vs '24 pack' is a mismatch even though the totals agree —
    units stay as printed, the ratio carries the arithmetic.
    """
    if _unit_norm(a) == _unit_norm(b):
        return True
    if not (_is_multipack(a) and _is_multipack(b)):
        return False

    def _split(u):
        s = "".join(str(u or "").lower().split())
        i = s.find("x")
        return s[:i], s[i + 1 :]

    ca, ia = _split(a)
    cb, ib = _split(b)
    if not ca or ca != cb:
        return False
    pa, pb = parse_unit(ia), parse_unit(ib)
    if pa and pb:
        return pa[0] == pb[0] and abs(pa[1] - pb[1]) < 0.001
    return _unit_norm(ia) == _unit_norm(ib)


def _outer_count(u):
    """The leading count of an 'NxM' multipack name ('6x750mL' → 6.0), else None."""
    if not _is_multipack(u):
        return None
    s = "".join(str(u or "").lower().split())
    try:
        return float(s[: s.find("x")])
    except ValueError:
        return None


def _units_equivalent(a, b):
    """True when two unit names denote the same DELIVERED pack.

    Beyond _multipack_equal and magnitude equality ('0.7 L' == '700 mL'),
    a copy that prints only a pack COUNT still names the same pack:
    '6 pack' == '6x750mL' (Hancocks prints Pack "6 PK"; Loaded holds the
    sized multipack), and 'each'/'EA' == a single sized-bottle unit
    ('750 mL', '375 mL' — volume only: an 'each' against a WEIGHT unit like
    '1.9 KG' stays a mismatch, weight-priced quantities mean kilos, not
    items). Count-vs-count still compares counts, so 'each' vs '12 pack'
    and '24 pack' vs '4x6 pack' remain mismatches — units stay as printed.
    """
    if _multipack_equal(a, b):
        return True
    pa, pb = parse_unit(a), parse_unit(b)
    if pa and pb and pa[0] == pb[0] and abs(pa[1] - pb[1]) < 0.001:
        return True
    for p, other in ((pa, b), (pb, a)):
        if not p or p[0] != "count":
            continue
        oc = _outer_count(other)
        if oc is not None:
            if abs(oc - p[1]) < 0.001:
                return True
        else:
            po = parse_unit(other)
            if po and po[0] == "volume" and abs(p[1] - 1) < 0.001:
                return True
    return False


def _ln_cost(ln):
    # Loaded renamed invoice-line cost fields (unitCost → unitCostExclTax,
    # observed live 05 Aug 2026); read the new name, fall back to the old.
    # PO lines still use unitCost — these helpers are for INVOICE lines only.
    v = ln.get("unitCostExclTax")
    return v if v is not None else ln.get("unitCost")


def _ln_tot(ln):
    v = ln.get("totalCostExclTax")
    return v if v is not None else ln.get("totalCost")


def _fingerprint(det):
    # Mirror of received_invoice.invoice_fingerprint (FNV-1a — the sandbox has
    # no hashlib; keep the material list IN SYNC with that function and with
    # prepare_receive_invoice.py). Stamped on the card so the chat-seeded
    # working document carries the same change-detection hash the draft shaper
    # produces.
    material = {
        "lines": [
            [
                ln.get("id"),
                ln.get("quantityReceived"),
                _ln_cost(ln),
                _ln_tot(ln),
                ln.get("linkedItemId"),
                ln.get("linkedUnitId"),
                ln.get("unit"),
                ln.get("code"),
                bool(ln.get("deletedAt")),
            ]
            for ln in (det.get("lines") or [])
            if isinstance(ln, dict)
        ],
        "subtotal": det.get("subtotal"),
        "tax": det.get("taxAmount"),
        "total": det.get("total"),
        "po": det.get("linkedPurchaseOrderId"),
        "file": det.get("fileId"),
    }
    h = 0xCBF29CE484222325
    for b in json.dumps(material, sort_keys=True, default=str).encode():
        h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return format(h, "016x")


def _delivered_unit(u):
    """The copy's delivered unit ONLY when it's a real weight/volume/count unit (or
    a multipack) — never a bare packaging word ('pkt', 'ea', 'box', 'ctn', …), which
    ``parse_unit`` already rejects. Gates the 'use X' recommendation so a
    mis-extracted packaging word is never suggested as a unit to switch to.
    """
    return u if u and (_is_multipack(u) or parse_unit(u)) else None


# Line-level detail is capped so a 200-line invoice can't blow out the report;
# every line is still CHECKED — only the per-line display rows are capped.
MAX_DETAIL_LINES = 25


def run(params, call_api, log, call_api_parallel=None):
    D = decimal.Decimal
    totals_tol = D(TOTALS_TOL)
    line_tol = D(LINE_TOL)

    def dec(value):
        if value is None:
            return None
        try:
            return D(str(value))
        except Exception:
            return None

    def money(value):
        d = dec(value)
        return "$" + format(d if d is not None else D("0"), ",.2f")

    def close(a, b, tol):
        if a is None or b is None:
            return False
        return abs(a - b) <= tol

    def norm(text):
        return "".join(ch for ch in str(text or "").lower() if ch.isalnum())

    def unit_key(text):
        return UNIT_ALIASES.get(norm(text))

    venue = params.get("venue")
    # Per-user run mode (injected by execute_consolidator):
    #   approve_all / unset → present everything, write nothing (dry run)
    #   approve_fixes       → auto-receive perfect invoices; fixes need the card
    #   autopilot           → auto-receive perfect; cards auto-apply confident fixes
    mode = params.get("mode") or "unset"
    mode_unset = mode == "unset"
    approve_all = mode in ("approve_all", "unset")
    autopilot = mode == "autopilot"
    # No separate dry-run concept: the run mode alone decides whether anything
    # is written. approve_all / unset present everything read-only for approval;
    # approve_fixes / autopilot write.
    dry_run = approve_all
    # "No valid purchase order" is a blocking validation error by default —
    # autopilot will not receive an invoice whose PO reference resolves to
    # nothing (or to another supplier's order that isn't a split). Venues
    # that don't care about PO validity set require_valid_po false on the
    # task config (norm.update_task_config): the check still shows ✗ on the
    # card, but the reason no longer blocks the autopilot receive.
    require_valid_po = params.get("require_valid_po") is not False
    to_date = params.get("to_date") or params.get("today")
    from_date = params.get("from_date")
    if not from_date and params.get("today"):
        from_date = (
            datetime.date.fromisoformat(params["today"]) - datetime.timedelta(days=60)
        ).isoformat()

    base = {"venue": venue} if venue else {}

    # PO resolution — owned here (previously the draft's _autolink_po). Loaded has
    # no PO-by-number search and its open-PO list omits received POs, so a number
    # is resolved in two passes. The received feed reaches already-received POs, so
    # it needs a wide window regardless of the review window.
    try:
        _t = datetime.date.fromisoformat(params.get("today"))
        _feed_from = (_t - datetime.timedelta(days=400)).isoformat()
        _feed_to = (_t + datetime.timedelta(days=1)).isoformat()
    except Exception:
        _feed_from, _feed_to = from_date, to_date

    def _po_norm(v):
        # Match invoice_fixes._po_key: alphanumerics only, drop a leading "po"
        # so "PO#1520987" == "1520987".
        k = "".join(ch for ch in str(v or "").lower() if ch.isalnum())
        return k[2:] if k.startswith("po") else k

    def _copy_po_number(det):
        # The BUYER PO printed on the copy (Loaded's own field can hold the
        # supplier's own order number instead). Extraction is cached. The
        # matching supplier spec's notes ride along, exactly like the line
        # extraction — an admin rule such as "use CUST. ORDER for the order
        # number" must steer THIS read too (and, being part of the cache key,
        # a spec edit re-extracts affected invoices once).
        if not det.get("fileId"):
            return None
        notes = _supplier_notes(det.get("supplierName"))
        hdr = extract_document(
            "loadedhub",
            "download_invoice_file",
            dict(base, file_id=det["fileId"]),
            schema=PO_EXTRACT_SCHEMA,
            instructions=(
                "Extract the buyer's purchase order number and the supplier's "
                "own order number — they differ."
                + (
                    "\n\nSupplier-specific notes for "
                    + str(det.get("supplierName"))
                    + ":\n"
                    + notes
                    if notes
                    else ""
                )
            ),
        )
        return (
            hdr.get("customer_purchase_order_number") if isinstance(hdr, dict) else None
        )

    def _resolve_po(number, supplier_id):
        # Resolve a PO NUMBER to a Loaded PO id. Two passes: (1) the open-PO list;
        # (2) the received feed — find an invoice carrying the same number and read
        # its linkedPurchaseOrderId (the only route to a received PO's id). Returns
        # {"id", "order_number", "linked_invoice_id"} or None (missing/ambiguous).
        want = _po_norm(number)
        if not want:
            return None
        if _po_list_cache:
            pos = _po_list_cache[0]
        else:
            pos = call_api("loadedhub", "list_purchase_orders", dict(base))
            _po_list_cache.append(pos)
        if isinstance(pos, dict):
            pos = pos.get("data") or []
        if isinstance(pos, list):
            matches = [
                p
                for p in pos
                if isinstance(p, dict) and _po_norm(p.get("orderNumber")) == want
            ]
            if supplier_id and any(p.get("supplierId") == supplier_id for p in matches):
                matches = [p for p in matches if p.get("supplierId") == supplier_id]
            if len({p.get("id") for p in matches}) == 1:
                p = matches[0]
                return {
                    "id": p.get("id"),
                    "order_number": p.get("orderNumber"),
                    "linked_invoice_id": p.get("linkedInvoiceId"),
                }
            if len(matches) > 1:
                return None  # ambiguous in the open list
        feed = call_api(
            "loadedhub",
            "list_received_invoices",
            dict(base, from_date=_feed_from, to_date=_feed_to),
        )
        if isinstance(feed, dict):
            feed = feed.get("data") or []
        po_ids = set()
        if isinstance(feed, list):
            for r in feed:
                if (
                    not isinstance(r, dict)
                    or _po_norm(r.get("purchaseOrderNumber")) != want
                ):
                    continue
                det = call_api(
                    "loadedhub",
                    "get_invoice_detail",
                    dict(base, invoice_id=r.get("id")),
                )
                if isinstance(det, dict) and det.get("linkedPurchaseOrderId"):
                    po_ids.add(det["linkedPurchaseOrderId"])
        if len(po_ids) == 1:
            pid = list(po_ids)[0]  # sandbox has no next()/iter()
            po = call_api(
                "loadedhub",
                "get_stock_purchase_order",
                dict(base, purchase_order_id=pid),
            )
            return {
                "id": pid,
                "order_number": po.get("orderNumber") if isinstance(po, dict) else None,
                "linked_invoice_id": (
                    po.get("linkedInvoiceId") if isinstance(po, dict) else None
                ),
            }
        return None

    # Single-invoice review: the Invoices page opening ONE invoice to receive
    # it. Reuse every gate below unchanged (so the checks never drift from the
    # batch playbook), but present-only — never auto-write — and skip the
    # window list entirely; the one draft is fetched by id in the loop.
    only_invoice_id = params.get("invoice_id")
    # A PO the Norm draft resolved but hasn't written back to Loaded. Single-
    # invoice mode injects it into the fetched detail so the PO-dependent gates
    # run against it (validate-without-writeback). Ignored in batch mode.
    only_po_id = params.get("purchase_order_id")
    # Single-invoice review always surfaces the editable card so the editor gets
    # the checks that ran — even when the invoice fails a gate (e.g. a freight
    # line with no stock item fails items_matched). The batch flow is unchanged
    # (force_card is False there): a skipped invoice still gets no card.
    force_card = bool(only_invoice_id)
    if only_invoice_id:
        approve_all = True
        autopilot = False
        dry_run = True
        drafts = [{"id": only_invoice_id}]
        invoices = drafts
        log("Single-invoice review: " + str(only_invoice_id))
    else:
        # Loaded's `to` filter EXCLUDES the end date (verified live 03 Aug 2026:
        # to=2026-08-03 omitted that day's invoices; to=2026-08-04 included
        # them) — widen the list call by one day so invoices dated TODAY are
        # reviewed. The reported window stays the human one.
        try:
            _list_to = (
                datetime.date.fromisoformat(str(to_date)) + datetime.timedelta(days=1)
            ).isoformat()
        except Exception:
            _list_to = to_date
        invoices = call_api(
            "loadedhub",
            "list_stock_invoices",
            dict(base, from_date=from_date, to_date=_list_to, page=0, pageSize=100),
        )
        if isinstance(invoices, dict) and invoices.get("error"):
            return {"error": "Could not list invoices: " + invoices["error"]}
        if isinstance(invoices, dict):
            invoices = invoices.get("data") or []

        drafts = [
            inv
            for inv in invoices
            if isinstance(inv, dict)
            and not inv.get("isReceived")
            and not inv.get("deletedAt")
        ]
        log(
            "Drafts to review: "
            + str(len(drafts))
            + " of "
            + str(len(invoices))
            + " listed"
        )

    received, skipped = [], []
    # Open-PO list, fetched at most once per run (PO resolution in batch would
    # otherwise refetch it for every unlinked invoice).
    _po_list_cache = []
    # Supplier invoice specs (admin-maintained: per-supplier extraction notes +
    # aliases), fetched at most once per run. The outer list is the fetched
    # sentinel; element 0 is the specs list (possibly empty).
    _supplier_specs_cache = []
    # Stock items fetched for supplier-VARIANT line matching (Loaded's draft
    # line often carries the stock ITEM's name while the copy prints the
    # variant's description — the variants live in the item's suppliers[]).
    # Cached per item id across invoices; budgeted so a pathological run can
    # never burn the executor's API-call cap (overflowing THAT raises and kills
    # the whole run) — past the budget a line just keeps its "not found" verdict.
    _item_cache = {}
    _variant_fetch_budget = [20]

    def _plain_match(ln, pool):
        # The two first-class pairing rules, shared by the dry-run and the real
        # pass: exact normalized code, then description substring either way.
        for cand in pool:
            if norm(cand.get("code")) and norm(cand.get("code")) == norm(
                ln.get("code")
            ):
                return cand
        for cand in pool:
            if norm(cand.get("description")) and (
                norm(cand.get("description")) in norm(ln.get("description"))
                or norm(ln.get("description")) in norm(cand.get("description"))
            ):
                return cand
        return None

    def _variant_claim(ln, item, supplier_id, candidates):
        # Match a Loaded line against doc lines via the stock item's supplier
        # variants. Conservative by design: a wrong claim clears the mismatch
        # reason and can let autopilot auto-receive, so only a UNIQUE hit (or a
        # tie broken by the line total) claims. Exact tiers first, then
        # substring with a length floor so short generic fragments can't match.
        variants = [
            v
            for v in (item.get("suppliers") or [])
            if isinstance(v, dict)
            and not (
                v.get("datestampDeleted") or v.get("removedAt") or v.get("deletedAt")
            )
        ]
        scoped = [v for v in variants if v.get("supplierId") == supplier_id]
        variants = scoped or variants
        texts = [norm(v.get("description")) for v in variants] + [
            norm(item.get("name"))
        ]
        texts = [t for t in texts if t]
        codes = [norm(v.get("stockCode")) for v in variants]
        codes = [c for c in codes if c]

        hits = []
        for cand in candidates:
            cdesc = norm(cand.get("description"))
            ccode = norm(cand.get("code"))
            if (ccode and ccode in codes) or (cdesc and cdesc in texts):
                hits.append(cand)
        if not hits:
            for cand in candidates:
                cdesc = norm(cand.get("description"))
                if not cdesc:
                    continue
                for t in texts:
                    small, big = (t, cdesc) if len(t) <= len(cdesc) else (cdesc, t)
                    if len(small) >= 8 and small in big:
                        hits.append(cand)
                        break
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            ln_tot = dec(_ln_tot(ln))
            if ln_tot is not None:
                close_hits = [
                    c
                    for c in hits
                    if dec(c.get("line_total_ex_tax")) is not None
                    and close(dec(c.get("line_total_ex_tax")), ln_tot, line_tol)
                ]
                if len(close_hits) == 1:
                    return close_hits[0]
        return None

    # Reserved spec-row name: the admin-editable MAIN extraction prompt
    # (Settings → Supplier Specs). When present + enabled its instructions
    # REPLACE the built-in base prompt below; supplier rows still append.
    MAIN_PROMPT_SPEC = "Main prompt"

    def _specs():
        if not _supplier_specs_cache:
            r = call_api("norm", "get_supplier_invoice_specs", dict(base))
            specs = (r.get("specs") if isinstance(r, dict) else None) or []
            _supplier_specs_cache.append(specs)
        return _supplier_specs_cache[0]

    def _supplier_notes(supplier_name):
        # The matching spec's extraction instructions for this supplier, or "".
        # Matched on normalized name/alias equality or substring ("Service
        # Foods" matches "Service Foods Auckland"); aliases shorter than 3
        # normalized chars are ignored as unsafe. First match wins.
        sname = norm(supplier_name)
        if not sname:
            return ""
        for sp in _specs():
            if not isinstance(sp, dict):
                continue
            if norm(sp.get("name")) == norm(MAIN_PROMPT_SPEC):
                continue  # the main prompt row, not a supplier
            for candidate in [sp.get("name")] + list(sp.get("aliases") or []):
                c = norm(candidate)
                if len(c) >= 3 and (c == sname or c in sname):
                    return str(sp.get("instructions") or "")
        return ""

    def _main_prompt():
        # Admin-edited main prompt from the reserved spec row, else the
        # built-in text — a missing or emptied row can never break reviews.
        for sp in _specs():
            if isinstance(sp, dict) and norm(sp.get("name")) == norm(MAIN_PROMPT_SPEC):
                text = str(sp.get("instructions") or "").strip()
                if text:
                    return text
        return _BUILTIN_MAIN_PROMPT

    # The GENERIC extraction prompt — deliberately simple. Layout quirks and
    # per-supplier conventions belong in supplier spec rows, not here. This
    # text doubles as the seed/fallback for the admin-editable "Main prompt"
    # spec row (see _main_prompt above).
    _BUILTIN_MAIN_PROMPT = (
        "Extract every billed LINE and the totals from this supplier "
        "invoice. Non-product charges (freight, delivery, card fees) "
        "are LINES too, wherever the document prints them — quantity 1 "
        "unless printed otherwise; unit may be null.\n\n"
        "FIRST determine document_type: a document headed "
        "'Statement' or structured as an account summary (rows of "
        "invoice numbers, payments, balances brought forward) is a "
        "'statement', NOT an invoice — still extract what you "
        "can.\n\n"
        "QUANTITY rules — quantity is the TOTAL number of individual "
        "units billed for the line:\n"
        "- Some suppliers SPLIT the quantity across columns (e.g. a "
        "cartons/CTN column and a single-units column): the billed "
        "quantity is cartons x pack size + singles (1 carton of 12 "
        "plus 4 singles = 16). Never report just one column of a "
        "split.\n"
        "- SELF-CHECK every line: quantity x unit_price_ex_tax must "
        "equal line_total_ex_tax (within a cent). If your quantity "
        "fails this check, re-read the line.\n"
        "- unit_price_ex_tax is the price of ONE unit exactly as "
        "printed; never adjust it to make the arithmetic work.\n\n"
        "For each line also derive unit_of_measure — the unit ONE "
        "delivered item is used in for recipe costing:\n"
        "- A weight, volume or count — never a length or a bare "
        "packaging word (pkt/box/carton/outer/unit).\n"
        "- Find the size in the unit/size columns first, then in the "
        "item description ('900ml', '500g', '4 Litre', 'Cider 330ml "
        "4x6').\n"
        "- Quantity and unit price stay AS PRINTED in their columns "
        "too — never decompose a pack into inner items: 2 cases of "
        "'6x 750ml' at $104.04/case is quantity 2 at 104.04, NEVER "
        "quantity 12 bottles at $17.34.\n"
        "- Keep it exactly as printed — never convert, multiply or "
        "split pack notation: a 5X3KG pack → '5x3kg', a '4 x 6 Pack' "
        "→ '4x6 pack', a 12PK → '12 pack', a single 2L bottle → "
        "'2L'.\n"
        "- Delivered as single inner items out of a larger pack → the "
        "inner size alone.\n"
        "- Random weight billed per kg (meat/seafood/produce) → "
        "'Kilo', never the total weight.\n"
        "- Exactly 1 of a base unit drops the 1: '1kg' → 'Kilo', "
        "'1L' → 'Litre', '1 each' → 'each'.\n"
        "- No confident unit → return null. Size present but "
        "unreadable (cut off, illegible) → null AND unit_unrecognisable "
        "true — never guess from partial text."
    )

    # Loaded's stored per-supplier aliases, fetched at most ONCE per supplier
    # per run (a batch reviews many invoices from few suppliers). Sorted so
    # the instruction text — and with it the extraction cache key — is stable
    # across runs; an alias edit in Loaded re-keys and re-extracts once.
    _alias_cache = {}

    def _supplier_aliases(supplier_id):
        if not supplier_id:
            return []
        if supplier_id not in _alias_cache:
            al = call_api(
                "loadedhub",
                "get_supplier_aliases",
                dict(base, supplier_id=supplier_id),
            )
            _alias_cache[supplier_id] = sorted(
                str(a.get("name"))
                for a in (al if isinstance(al, list) else [])
                if isinstance(a, dict) and a.get("name")
            )
        return _alias_cache[supplier_id]

    def _pdf_instructions(detail):
        # ONE source for the Layer-6 extraction instructions. The parallel
        # prefetch below and the in-loop extract_document must build
        # byte-identical strings — the instruction text is part of the
        # extraction cache key, so any drift makes the prefetch worthless.
        # Composition: admin-editable main prompt + the supplier-differs
        # clause (per-invoice: names Loaded's supplier AND its stored aliases
        # so the MODEL's same-business judgment covers every known name —
        # a variant of an ALIAS must not churn any more than a variant of the
        # primary name) + the matching supplier spec's notes. All part of the
        # cache key, so an edit re-extracts affected invoices exactly once.
        notes = _supplier_notes(detail.get("supplierName"))
        aliases = (
            _supplier_aliases(detail.get("linkedSupplierId"))
            if detail.get("supplierName")
            else []
        )
        return (
            _main_prompt()
            + (
                "\n\nLoaded records this invoice's supplier as '"
                + str(detail.get("supplierName"))
                + "'"
                + (
                    " (also known as: "
                    + ", ".join("'" + a + "'" for a in aliases)
                    + ")"
                    if aliases
                    else ""
                )
                + ". In supplier_name return the supplier printed on the "
                "document; set supplier_differs true ONLY when that is a "
                "DIFFERENT BUSINESS from ALL of those names (naming "
                "variations are the same business)."
                if detail.get("supplierName")
                else ""
            )
            + (
                "\n\nSupplier-specific notes for "
                + str(detail.get("supplierName"))
                + ":\n"
                + notes
                if notes
                else ""
            )
        )

    # Received invoices indexed by normalized invoice number, for duplicate
    # detection. Fetched lazily ONCE per run (wide window). None = not yet
    # fetched; False = fetch failed (don't retry, leave the check unchecked).
    received_by_ref = None
    # Full editable "Receive Invoice" payloads — one per invoice that has a
    # concrete auto-fix (link_po or unit). Raw values (numbers, ids) for the
    # interactive card; built from data already fetched, no extra API calls.
    fix_invoices = []

    # Parallel prefetch: fetch every draft's detail concurrently, then warm the
    # extraction cache for every attached invoice copy (the executor fans out
    # in a rolling window of 10). The per-invoice loop below is unchanged —
    # its extract_document calls become cache hits, so a 30-invoice review
    # collapses from ~30 sequential LLM extractions to ~3 parallel waves.
    # Feature-detected because this code is served from the shared config DB
    # to every environment: an executor that predates the batch helper (no
    # extract_documents_parallel in its namespace) just runs sequentially.
    _details_by_id = {}
    _extract_batch = None
    try:
        _extract_batch = extract_documents_parallel
    except Exception:
        _extract_batch = None
    if call_api_parallel and len(drafts) > 1:
        _fetched = call_api_parallel(
            [
                ("loadedhub", "get_invoice_detail", dict(base, invoice_id=s.get("id")))
                for s in drafts
            ]
        )
        for _i, _d in enumerate(_fetched):
            if _i < len(drafts) and isinstance(_d, dict) and not _d.get("error"):
                _details_by_id[drafts[_i].get("id")] = _d
        if _extract_batch:
            _reqs = []
            for _s in drafts:
                _d = _details_by_id.get(_s.get("id"))
                if isinstance(_d, dict) and _d.get("fileId"):
                    _reqs.append(
                        {
                            "connector": "loadedhub",
                            "action": "download_invoice_file",
                            "params": dict(base, file_id=_d["fileId"]),
                            "schema": PDF_SCHEMA,
                            "instructions": _pdf_instructions(_d),
                        }
                    )
            if _reqs:
                _extract_batch(_reqs)

    for stub in drafts:
        inv_id = stub.get("id")
        detail = _details_by_id.get(inv_id) or call_api(
            "loadedhub", "get_invoice_detail", dict(base, invoice_id=inv_id)
        )
        if isinstance(detail, dict) and detail.get("error"):
            skipped.append(
                _verdict(stub, ["Could not fetch invoice detail: " + detail["error"]])
            )
            continue

        # Validate against a PO that isn't linked in Loaded yet (single-invoice /
        # editor mode only; fills a gap, never overrides a real Loaded link) so
        # every PO-dependent gate below runs. The PO id comes from the user's own
        # pick in the editor (only_po_id) or, failing that, is RESOLVED here from
        # the referenced number / buyer PO on the copy — the retrieval the draft
        # used to do. Either way it reads as a SUGGESTED change (link the found
        # PO), not a clean pass. po_unresolved: we tried and no Loaded PO matched,
        # so a "Link PO X" suggestion would be noise (a supplier's own ref).
        po_autolinked = False
        po_unresolved = False
        # The already-received sibling when this draft is a duplicate — set by
        # the duplicate gate, emitted on the card so the editor can deep-link
        # it in Loaded and serve its copy for side-by-side comparison. The
        # file id comes off the feed row directly. When the goods were
        # receipted straight against the ORDER (feed type "PurchaseOrder"),
        # there is no invoice entity at all — the PO id is emitted instead.
        duplicate_of_id = None
        duplicate_of_file_id = None
        duplicate_of_po_id = None
        # Split-order state: the referenced PO is already linked to a sibling
        # invoice (Loaded is 1:1). split_order = a genuine second delivery
        # (sibling carries DIFFERENT goods/quantities) — informational, the
        # card shows the order's lines and receives without re-linking.
        # split_po_suggested = the number came off the COPY only (scenario 3):
        # the card offers setting the reference before adopting that state.
        # split_remove_po = NOT a split (sibling already contains the same
        # goods and total) — the card offers removing the bogus reference.
        split_order = False
        split_po_suggested = False
        split_remove_po = False
        split_po_id = None
        split_po_number = None
        split_sibling_invoice_id = None
        split_sibling_reference = None
        split_sibling_file_id = None
        # The BUYER PO read off the attached copy (cached extraction) — kept
        # for the reason text and the card: Loaded's own field often holds the
        # supplier's ref (e.g. Bidfood O/N), so when nothing resolves, the
        # copy's number is the one the user needs to see.
        copy_po_seen = None
        # Where the resolved PO number CAME from — Loaded's own
        # purchaseOrderNumber field ("loaded") vs the copy's buyer PO
        # ("copy"). A split order found via Loaded's field shows its state
        # automatically; one found only on the copy needs the user to accept
        # setting the reference first.
        po_match_source = None
        if not detail.get("linkedPurchaseOrderId"):
            if only_po_id:
                detail["linkedPurchaseOrderId"] = only_po_id
                po_autolinked = True
                po_match_source = "loaded"
            elif detail.get("purchaseOrderNumber") or detail.get("fileId"):
                supplier_id = detail.get("linkedSupplierId")
                resolved = (
                    _resolve_po(detail.get("purchaseOrderNumber"), supplier_id)
                    if detail.get("purchaseOrderNumber")
                    else None
                )
                if resolved:
                    po_match_source = "loaded"
                else:
                    copy_po_seen = _copy_po_number(detail)
                    if copy_po_seen and _po_norm(copy_po_seen) != _po_norm(
                        detail.get("purchaseOrderNumber")
                    ):
                        resolved = _resolve_po(copy_po_seen, supplier_id)
                        if resolved:
                            po_match_source = "copy"
                if resolved:
                    detail["linkedPurchaseOrderId"] = resolved["id"]
                    po_autolinked = True
                else:
                    po_unresolved = True

        reasons = []
        checks = {}
        # Proposed one-click fixes for the interactive card (applied later by
        # the invoice_fixes component-API). Built ONLY from data already in
        # hand — no extra API calls during review. The card/handler resolve
        # ids (PO, unit, variant) at apply time.
        fixes = []
        # Copy (pdf) values paired to each invoice line id, captured during the
        # Layer 6 line match so the editable card can show copy-vs-invoice.
        copy_by_line_id = {}
        ref = detail.get("referenceNumber") or "(no number)"
        total = dec(detail.get("total"))
        lines = [ln for ln in detail.get("lines") or [] if not ln.get("deletedAt")]
        # A redundant $0 duplicate: another line on THIS invoice already carries
        # the same code and a real amount, and this one contributes nothing ($0
        # total). Loaded lets these slip in (a re-scan, a split that zeroed out).
        # The copy has the item once, so the empty twin would otherwise be flagged
        # "not found on the document". Detect it order-independently and suggest
        # striking it (excluded from the receive) rather than flagging it missing.
        strike_ids = set()
        # Line ids the Layer-6 comparison found on the draft but NOT on the
        # attached copy — drives the per-line copy_missing flag (the editor's
        # "remove" suggestion), mirroring how strike_ids drives copy_duplicate.
        missing_on_copy_ids = set()
        # Line ids whose unit exists on the copy but CANNOT be read (cut off /
        # illegible / ambiguous) — drives unit_needs_confirmation: the editor
        # asks the user to confirm, and the reason blocks autopilot. Never a
        # suggestion: guessing a unit from partial text is how wrong units
        # get received.
        unit_confirm_ids = set()
        _by_code = {}
        for _ln in lines:
            _code = norm(_ln.get("code"))
            if _code:
                _by_code.setdefault(_code, []).append(_ln)
        for _group in _by_code.values():
            if len(_group) > 1 and any(
                dec(_ln_tot(x)) not in (None, D(0)) for x in _group
            ):
                for x in _group:
                    if dec(_ln_tot(x)) == D(0):
                        strike_ids.add(x.get("id"))
                        # Deterministic from the invoice's own lines (no copy
                        # needed), so emit the strike suggestion here — the copy
                        # comparison (Layer 6) may be short-circuited by an earlier
                        # gate (e.g. no PO linked) and never reached.
                        fixes.append(
                            {
                                "type": "strike",
                                "invoice_id": inv_id,
                                "reference": ref,
                                "line_id": x.get("id"),
                                "line_code": x.get("code"),
                                "description": str(x.get("description")),
                                "summary": str(x.get("code") or ref)
                                + " · "
                                + str(x.get("description"))
                                + ": $0 duplicate line — strike it "
                                + "(excluded from receive)",
                            }
                        )
        po = None
        pdf = None
        po_number_hint = detail.get("purchaseOrderNumber")
        # The copy's printed totals when they DISAGREE with the Loaded header
        # (e.g. a feed that left the invoice total $0) — carried on the card so
        # the editor can offer "Invoice total X → Y (per the invoice copy)" as
        # a local edit, written to Loaded on receive.
        copy_totals = None
        # The copy's printed invoice number when it DISAGREES with the draft's
        # reference — carried on the card so the editor can offer
        # "Invoice number X → Y (per the invoice copy)" as a local edit
        # (referenceNumber already rides the receive write-through).
        copy_invoice_number = None
        # The supplier printed on the copy + the LLM-matched Loaded supplier,
        # set by Gate 12 when the invoice's supplier is missing or the copy
        # names a different business — drives the editor's supplier suggestion.
        copy_supplier_seen = None
        supplier_differs_seen = False
        supplier_match = None
        # The linked PO's supplier when it is NOT this invoice's supplier —
        # drives the editor's "unlink this order" suggestion (shown only when
        # no supplier-switch suggestion covers the same conflict).
        po_supplier_mismatch = False
        po_supplier_name = None

        def opt_money(value):
            return money(value) if dec(value) is not None else None

        def opt_num(value):
            return str(value) if value is not None else None

        # Per-line audit records: the invoice's ACTUAL values, filled in with
        # the compared PO / invoice-copy values and ✓/✗ as each layer runs.
        # "—" always means "not checked" (an earlier layer failed first).
        line_records = []
        rec_by_id = {}
        for ln in lines[:MAX_DETAIL_LINES]:
            rec = {
                "line": str(ln.get("description") or ln.get("code") or "?"),
                "code": ln.get("code"),
                "stock_item": "✓"
                if (
                    ln.get("linkedItemId")
                    and ln.get("linkedUnitId")
                    and not (ln.get("brand") and not ln.get("linkedBrandId"))
                )
                else "✗",
                "on_copy": "—",
                "unit": {"invoice": ln.get("unit"), "copy": None, "result": "—"},
                "quantity": {
                    "invoice": opt_num(ln.get("quantityReceived")),
                    "copy": None,
                    "result": "—",
                },
                "unit_cost": {
                    "invoice": opt_money(_ln_cost(ln)),
                    "copy": None,
                    "result": "—",
                },
                "line_total": {
                    "invoice": opt_money(_ln_tot(ln)),
                    "copy": None,
                    "result": "—",
                },
            }
            line_records.append(rec)
            rec_by_id[ln.get("id")] = rec
        if len(lines) > MAX_DETAIL_LINES:
            line_records.append(
                {
                    "line": "… "
                    + str(len(lines) - MAX_DETAIL_LINES)
                    + " more lines checked but omitted from this detail view"
                }
            )

        def make_fix_invoice():
            # Raw, machine-usable payload for the editable Receive Invoice card.
            # All lines (uncapped — only display rows are capped), numeric
            # values as numbers, paired with the copy where available.
            raw_lines = []
            for ln in lines:
                cp = copy_by_line_id.get(ln.get("id")) or {}
                line = {
                    "id": ln.get("id"),
                    "code": ln.get("code"),
                    "description": ln.get("description"),
                    "brand": ln.get("brand"),
                    "linked_item_id": ln.get("linkedItemId"),
                    "linked_unit_id": ln.get("linkedUnitId"),
                    "linked_brand_id": ln.get("linkedBrandId"),
                    "unit": ln.get("unit"),
                    # Creation-time snapshot + Loaded-parity fields so a card is
                    # a COMPLETE received_invoice doc payload (the chat flow
                    # seeds working documents straight from it; keep in sync
                    # with received_invoice._line_from_detail).
                    "original_unit_id": ln.get("linkedUnitId"),
                    "unit_ratio": ln.get("linkedUnitRatio"),
                    "quantity_ordered": ln.get("quantityOrdered"),
                    "tax_amount": ln.get("taxAmount"),
                    "sale_tax_rate": ln.get("saleTaxRate"),
                    "item_type": ln.get("itemType"),
                    "quantity_received": ln.get("quantityReceived"),
                    "unit_cost": _ln_cost(ln),
                    "total_cost": _ln_tot(ln),
                    "copy_unit": cp.get("unit"),
                    "copy_quantity": cp.get("quantity"),
                    "copy_unit_price": cp.get("unit_price_ex_tax"),
                    "copy_line_total": cp.get("line_total_ex_tax"),
                    # Gated: only a real unit is surfaced as "use X" — never a
                    # packaging word the extraction mis-read into unit_of_measure.
                    "recommended_unit": _delivered_unit(cp.get("unit_of_measure")),
                }
                # Whether this line's unit disagreed with the copy (authoritative
                # per-line result) — set ONLY on a mismatch so the editor can flag
                # it even with no derivable unit to suggest, without bloating the
                # common all-match card past the tool-result slim cap.
                if (rec_by_id.get(ln.get("id")) or {}).get("unit", {}).get(
                    "result"
                ) == "✗":
                    line["copy_unit_mismatch"] = True
                # Whether Qty received disagreed with the copy — the playbook's
                # decision that the copy's qty is a candidate edit; the component
                # only renders the "use copy qty" action from it.
                if (rec_by_id.get(ln.get("id")) or {}).get("quantity", {}).get(
                    "result"
                ) == "✗":
                    line["copy_quantity_mismatch"] = True
                # Whether the line's unit COST disagreed with the copy — drives
                # the "use copy price" affordance (unpriced-feed invoices).
                if (rec_by_id.get(ln.get("id")) or {}).get("unit_cost", {}).get(
                    "result"
                ) == "✗":
                    line["copy_unit_cost_mismatch"] = True
                # The review's decision that this is a redundant $0 duplicate; the
                # component renders a "strike" affordance from it. Striking (drop
                # from the receive) is the user's applied action, done via a
                # working-doc line edit — mirrors copy_quantity_mismatch's "use".
                if ln.get("id") in strike_ids:
                    line["copy_duplicate"] = True
                # The review's decision that this line is NOT on the attached
                # copy; the component renders a "remove" affordance from it
                # (strike-style, soft-deleted at receive).
                if ln.get("id") in missing_on_copy_ids:
                    line["copy_missing"] = True
                # The unit exists on the copy but is unreadable — the editor
                # renders a "confirm the unit" ask (no proposed value).
                if ln.get("id") in unit_confirm_ids:
                    line["unit_needs_confirmation"] = True
                raw_lines.append(line)
            # NEW-item lines: ask the item-match LLM function (norm.match_stock_
            # items — the resolve_dates pattern) for a link-or-create suggestion
            # per unlinked line, so the artifact is COMPLETE and every surface
            # (web editor, embedded card, chat narration) renders the same
            # suggestions. Input-hash cached in the handler; best-effort — a
            # failure just leaves the fields absent (plain create).
            new_lines = [
                {
                    "id": rl.get("id"),
                    "description": rl.get("description") or "",
                    "code": rl.get("code") or "",
                    "brand": rl.get("brand") or "",
                    "unit": rl.get("unit") or "",
                }
                for rl in raw_lines
                if not rl.get("linked_item_id")
            ]
            # add_line suggestions (document lines missing from the draft) go
            # through the SAME matcher under synthetic ids, so an accepted add
            # can land already linked to a stock item instead of tripping the
            # new-item gate.
            _add_fixes = [f for f in fixes if f.get("type") == "add_line"]
            for _ai, _af in enumerate(_add_fixes):
                new_lines.append(
                    {
                        "id": "doc:" + str(_ai),
                        "description": str(_af.get("description") or ""),
                        "code": str(_af.get("code") or ""),
                        "brand": "",
                        "unit": str(_af.get("unit") or ""),
                    }
                )
            # A statement's rows ("Balance Brought Forward", invoice numbers)
            # are not products — never offer to create/link them as stock
            # items. A DUPLICATE draft's delete fix is different: its lines
            # ARE real products (SF IN9757146, 08 Aug 2026 — the guard
            # silently withheld every item match), so they still match; the
            # duplicate fix carries duplicate_of_* markers, a statement's
            # doesn't.
            _statement_delete = any(
                f.get("type") == "delete_invoice"
                and not f.get("duplicate_of_invoice_id")
                and not f.get("duplicate_of_purchase_order_id")
                for f in fixes
            )
            if new_lines and not _statement_delete:
                matched = call_api(
                    "norm", "match_stock_items", dict(base, lines=new_lines)
                )
                sug_by_id = (
                    matched.get("suggestions") if isinstance(matched, dict) else None
                ) or {}
                for rl in raw_lines:
                    s = sug_by_id.get(rl.get("id"))
                    if isinstance(s, dict):
                        rl["matched_item"] = s.get("matched_item")
                        rl["suggested_name"] = s.get("suggested_name")
                        rl["suggested_group_id"] = s.get("suggested_group_id")
                for _ai, _af in enumerate(_add_fixes):
                    s = sug_by_id.get("doc:" + str(_ai))
                    if isinstance(s, dict) and s.get("matched_item"):
                        _af["matched_item"] = s.get("matched_item")
            # Reword the items_matched reason now that the item-match results
            # are known (Layer 4 runs BEFORE the matcher): a value the card's
            # own suggestions resolve — an item the matcher linked to an
            # existing stock item, or a unit with a suggested change — is NOT
            # "created as NEW"; saying so contradicts the suggestion right
            # above it. In-place, because the verdict holds this same list.
            if any("(would be created as NEW)" in _r for _r in reasons):
                _new_parts, _fixable = [], []
                for rl in raw_lines:
                    _nm = str(rl.get("description") or rl.get("code") or "?")
                    if not rl.get("linked_item_id"):
                        _m = rl.get("matched_item")
                        if isinstance(_m, dict) and _m.get("id"):
                            _fixable.append(
                                "stock item on line '"
                                + _nm
                                + "' → link to existing '"
                                + str(_m.get("name"))
                                + "'"
                            )
                        else:
                            _new_parts.append("stock item on line '" + _nm + "'")
                    if not rl.get("linked_unit_id"):
                        if rl.get("recommended_unit"):
                            _fixable.append(
                                "unit '"
                                + str(rl.get("unit"))
                                + "' on line '"
                                + _nm
                                + "' → '"
                                + str(rl.get("recommended_unit"))
                                + "'"
                            )
                        else:
                            _new_parts.append(
                                "unit '"
                                + str(rl.get("unit"))
                                + "' on line '"
                                + _nm
                                + "'"
                            )
                    if rl.get("brand") and not rl.get("linked_brand_id"):
                        _new_parts.append(
                            "brand '" + str(rl.get("brand")) + "' on line '" + _nm + "'"
                        )
                _msgs = []
                if _new_parts:
                    _shown = "; ".join(_new_parts[:5])
                    if len(_new_parts) > 5:
                        _shown += "; … " + str(len(_new_parts) - 5) + " more"
                    _msgs.append(
                        str(len(_new_parts))
                        + " value(s) are not in the Loaded database "
                        "(would be created as NEW): " + _shown
                    )
                if _fixable:
                    _shown = "; ".join(_fixable[:5])
                    if len(_fixable) > 5:
                        _shown += "; … " + str(len(_fixable) - 5) + " more"
                    _msgs.append(
                        str(len(_fixable))
                        + " value(s) are not linked in Loaded yet — the "
                        "suggested changes resolve them: " + _shown
                    )
                for _i, _r in enumerate(list(reasons)):
                    if "(would be created as NEW)" in _r:
                        reasons[_i : _i + 1] = _msgs
                        break
            card = {
                "invoice_id": inv_id,
                "reference_number": ref,
                "supplier_name": detail.get("supplierName"),
                "linked_supplier_id": detail.get("linkedSupplierId"),
                "purchase_order_number": po_number_hint,
                # The buyer PO read off the copy + whether resolution failed —
                # the editor shows "copy says X — no matching purchase order"
                # under the Order Number picker so the number the user needs
                # is visible even when there is nothing to link.
                "copy_po": copy_po_seen,
                "po_unresolved": po_unresolved,
                # Copy-printed totals when they disagree with the Loaded header
                # (see Gate 11) — the editor's "Invoice total X → Y" suggestion.
                "copy_total_mismatch": bool(copy_totals),
                **(copy_totals or {}),
                # The copy's printed invoice number when it disagrees with the
                # draft's reference — the editor's "Invoice number X → Y"
                # suggestion (a local header edit; referenceNumber already
                # rides the receive write-through).
                "copy_invoice_number": copy_invoice_number,
                # The supplier printed on the copy + the matched Loaded record
                # (see Gate 12) — the editor's supplier link suggestion.
                "copy_supplier": copy_supplier_seen,
                "supplier_differs": supplier_differs_seen,
                "matched_supplier_id": (supplier_match or {}).get("supplier_id"),
                "matched_supplier_name": (supplier_match or {}).get("supplier_name"),
                # The linked PO's supplier when it isn't this invoice's
                # supplier (Layer 4) — the editor's "unlink this order"
                # suggestion.
                "po_supplier_mismatch": po_supplier_mismatch,
                "po_supplier_name": po_supplier_name,
                # The already-received sibling when this draft is a duplicate
                # (see the duplicate gate) — the editor deep-links it in Loaded
                # and serves its copy so both invoices can be compared. The PO
                # id variant means the goods were receipted against the order
                # and no invoice document exists in Loaded.
                "duplicate_of_invoice_id": duplicate_of_id,
                "duplicate_of_file_id": duplicate_of_file_id,
                "duplicate_of_purchase_order_id": duplicate_of_po_id,
                # Split-order state (the referenced PO is already linked to a
                # sibling invoice): split_order = genuine second delivery
                # (adopted automatically when Loaded's own number matched);
                # split_po_suggested = the number came off the copy only —
                # the editor offers setting the reference first;
                # split_remove_po = NOT a split (sibling has the same goods)
                # — the editor offers removing the bogus reference.
                "split_order": split_order,
                "split_po_suggested": split_po_suggested,
                "split_remove_po": split_remove_po,
                "split_po_id": split_po_id,
                "split_po_number": split_po_number,
                "split_sibling_invoice_id": split_sibling_invoice_id,
                "split_sibling_reference": split_sibling_reference,
                "split_sibling_file_id": split_sibling_file_id,
                # Loaded's REAL link only (strict mirror): a PO the engine
                # resolved for validation was INJECTED into detail — it must
                # ride as the link_po SUGGESTION (picker shows it amber), never
                # as an established link the receive would silently write.
                "linked_purchase_order_id": (
                    None if po_autolinked else detail.get("linkedPurchaseOrderId")
                ),
                "issued_at": detail.get("issuedAt"),
                "due_at": detail.get("dueAt"),
                "subtotal": detail.get("subtotal"),
                "tax_amount": detail.get("taxAmount"),
                "discount_amount": detail.get("discountAmount"),
                "total": detail.get("total"),
                "received_at": detail.get("receivedAt"),
                "unit_cost_includes_tax": bool(
                    detail.get("displayUnitCostInclusiveOfTax")
                    if detail.get("displayUnitCostInclusiveOfTax") is not None
                    else detail.get("unitCostIncludesTax")
                ),
                "is_received": bool(detail.get("isReceived")),
                "status": "draft",
                "notes": detail.get("notes") or "",
                # Same content hash the draft shaper stamps — lets the cached
                # review invalidate when the live invoice changes (keep in sync
                # with received_invoice.invoice_fingerprint).
                "loaded_invoice_fingerprint": _fingerprint(detail),
                "file_id": detail.get("fileId"),
                "lines": raw_lines,
                "suggestions": list(fixes),
                # The review's authoritative checklist so the card can show
                # every check (not just the ones it re-derives client-side).
                # Encoded as one compact char per check in CHECK_LABELS order —
                # 'p' pass, 'f' fail, '-' not reached (the gates short-circuit
                # at the first failure) — because this rides on every card and
                # must stay well under the LLM tool-result slim cap. The card
                # decodes it against the same fixed order. The failure detail
                # (`reasons`) is already carried once in the skipped/received
                # verdicts, so it is not duplicated onto every card here.
                "checks": "".join(
                    {"pass": "p", "fail": "f", "suggest": "s"}.get(checks.get(key), "-")
                    for key, _label in CHECK_LABELS
                ),
            }
            # The specific failure reasons (e.g. "Line 'X': quantity 2.25 does
            # not equal the document's quantity 2") so the editor card can show
            # WHAT didn't match, not just which check failed. On every card —
            # the chat flow seeds a working document from the card, and the
            # editor renders these under Needs Attention. Capped, and paid for
            # by dropping the per-invoice `details` audit sections.
            if reasons:
                card["check_reasons"] = list(reasons)[:12]
            return card

        # Append the editable card at most once for this invoice. The explicit
        # card sites below route through here so forced carding (single-invoice
        # review) never double-appends.
        _carded = [False]

        def card_once():
            if not _carded[0]:
                _carded[0] = True
                fix_invoices.append(make_fix_invoice())

        def verdict_now():
            # In single-invoice review, every terminal path computes a verdict,
            # so carding here guarantees the editor always receives the checks
            # that ran — including for invoices that fail a gate and are skipped.
            if force_card:
                card_once()
            symbol = {"pass": "✓", "fail": "✗"}

            checklist_rows = [
                {"check": label, "result": symbol.get(checks.get(key), "—")}
                for key, label in CHECK_LABELS
            ]
            return {
                "invoice_id": inv_id,
                "reference_number": ref,
                "supplier_name": detail.get("supplierName"),
                "po_number": (po or {}).get("orderNumber") or po_number_hint,
                "total": money(total),
                "reasons": reasons,
                "fixes": list(fixes),
                # The per-invoice `details` audit sections (header/line compare
                # tables) were retired with the long markdown report: the LLM now
                # writes a SHORT summary from reasons/rows, and the editor cards
                # carry the full per-line data. Dropping them keeps the payload
                # small enough for the slim cap with reasons on every card.
                "checklist": (
                    "All " + str(len(checklist_rows)) + " checks passed ✓"
                    if all(r["result"] == "✓" for r in checklist_rows)
                    else checklist_rows
                ),
            }

        # Gates are evaluated in LAYERS, and EVERY check that can run does run —
        # in batch AND single-invoice mode (ONE unified process): a later
        # line-vs-copy mismatch is never hidden behind an earlier failure, and
        # the chat's per-invoice cards carry the same complete picture as the
        # editor. Data-dependent layers still guard on their inputs (``po``
        # fetched, ``fileId`` present), the extraction is content-cached, and
        # the auto-receive decision is unchanged (any reason blocks it).
        run_all = True

        # Layer 0: duplicate of an already-received invoice? Same normalized
        # invoice number + same supplier in the received feed, different id.
        # Loaded's own UI banners this but its API carries NO marker on the
        # detail (verified live: CN-19980) — so the check is ours, from the
        # received feed we already pull for PO resolution.
        if ref and ref != "(no number)":
            if received_by_ref is None:
                feed = call_api(
                    "loadedhub",
                    "list_received_invoices",
                    dict(base, from_date=_feed_from, to_date=_feed_to),
                )
                if isinstance(feed, dict):
                    feed = None if feed.get("error") else (feed.get("data") or [])
                if feed is None:
                    received_by_ref = False  # fetch failed — leave unchecked
                else:
                    received_by_ref = {}
                    for r in feed:
                        if isinstance(r, dict) and r.get("invoiceNumber"):
                            received_by_ref.setdefault(
                                norm(r.get("invoiceNumber")), []
                            ).append(r)
            if received_by_ref is not False:
                dup = None
                for r in received_by_ref.get(norm(ref), []):
                    if r.get("id") != inv_id and norm(r.get("supplierName")) == norm(
                        detail.get("supplierName")
                    ):
                        dup = r
                        break
                if dup is None:
                    checks["duplicate"] = "pass"
                else:
                    dup_date = str(dup.get("receivedAt") or "")[:10]
                    # The feed mixes two record kinds (its `type` field):
                    # "Invoice" rows are real invoice entities — the row id is
                    # an invoice id (detail resolves, deep link works, may
                    # carry a copy). "PurchaseOrder" rows are goods receipted
                    # directly against the ORDER with the supplier's invoice
                    # number noted — NO invoice document exists in Loaded, and
                    # the row id is the PO's id (verified live: IN9757146 →
                    # PO 341521200). Linking that id as an invoice 404s.
                    dup_is_po = str(dup.get("type") or "") == "PurchaseOrder"
                    _fail(
                        checks,
                        reasons,
                        "duplicate",
                        "An invoice with number "
                        + ref
                        + " from "
                        + str(detail.get("supplierName"))
                        + (
                            " was already receipted on "
                            + dup_date
                            + " against purchase order "
                            + str(dup.get("purchaseOrderNumber") or "?")
                            + " (total "
                            + money(dup.get("total"))
                            + ") — the goods came in on the order, so no"
                            " separate invoice document exists in Loaded;"
                            " this draft is a duplicate and should be deleted"
                            if dup_is_po
                            else " was already received on "
                            + dup_date
                            + " (total "
                            + money(dup.get("total"))
                            + ") — this draft is a duplicate and should be"
                            " deleted"
                        ),
                    )
                    if dup_is_po:
                        duplicate_of_po_id = dup.get("id")
                    else:
                        duplicate_of_id = dup.get("id")
                        duplicate_of_file_id = dup.get("fileId")
                    fixes.append(
                        {
                            "type": "delete_invoice",
                            "invoice_id": inv_id,
                            "reference": ref,
                            "duplicate_of_invoice_id": duplicate_of_id,
                            "duplicate_of_file_id": duplicate_of_file_id,
                            "duplicate_of_purchase_order_id": duplicate_of_po_id,
                            "summary": ref
                            + (
                                " was already receipted on "
                                + dup_date
                                + " against order "
                                + str(dup.get("purchaseOrderNumber") or "?")
                                + " — delete this duplicate draft"
                                if dup_is_po
                                else " was already received on "
                                + dup_date
                                + " — delete this duplicate draft"
                            ),
                        }
                    )
                    if not run_all:
                        card_once()
                        skipped.append(verdict_now())
                        continue

        # Layer 1: credit notes are out of scope
        if total is not None and total < 0:
            _fail(
                checks,
                reasons,
                "credit_note",
                "Credit note (total "
                + money(total)
                + ") — out of scope for auto-receiving",
            )
            if not run_all:
                skipped.append(verdict_now())
                continue
        else:
            checks["credit_note"] = "pass"

        # Layer 2: an invoice copy must be attached. Without the source
        # document the copy checks cannot run (guarded below by fileId).
        if not detail.get("fileId"):
            _fail(
                checks,
                reasons,
                "pdf_present",
                "No invoice copy attached — cannot verify; attach the supplier's invoice in Loaded",
            )
            if not run_all:
                skipped.append(verdict_now())
                continue
        else:
            checks["pdf_present"] = "pass"

        # Layer 3: must be automatched to a purchase order. "No valid
        # purchase order" is a BLOCKING validation error by default;
        # require_valid_po=false keeps the ✗ on the card but lets autopilot
        # receive anyway (venues that don't care about PO validity).
        po_id = detail.get("linkedPurchaseOrderId")
        if not po_id:
            msg = "No valid purchase order"
            if copy_po_seen and _po_norm(copy_po_seen) != _po_norm(
                po_number_hint or ""
            ):
                # The buyer PO from the copy is the number that matters —
                # Loaded's own field held the supplier's ref, which is what
                # made "invoice references <O/N>" read like a wrong pickup.
                msg += " (copy says order " + str(copy_po_seen)
                if po_number_hint:
                    msg += "; supplier ref " + str(po_number_hint)
                msg += " — no matching purchase order found in Loaded)"
            elif po_number_hint:
                msg += (
                    " (invoice references "
                    + str(po_number_hint)
                    + " — needs matching in Loaded)"
                )
                # Suggest linking the referenced PO — UNLESS the editor already
                # tried to resolve it and no Loaded PO matched (po_unresolved), in
                # which case the number is a supplier's own ref, not a Loaded PO,
                # and the suggestion is just noise. The buyer PO on the copy gives
                # a better label than Loaded's field (which may hold that ref).
                if not po_unresolved:
                    copy_po = _copy_po_number(detail)
                    fixes.append(
                        {
                            "type": "link_po",
                            "invoice_id": inv_id,
                            "reference": ref,
                            "po_number": str(copy_po or po_number_hint),
                            "referenced_po": str(po_number_hint),
                            "copy_po": copy_po,
                            "summary": "Link purchase order "
                            + str(copy_po or po_number_hint)
                            + " to invoice "
                            + ref,
                        }
                    )
            if require_valid_po or not autopilot:
                _fail(checks, reasons, "po_linked", msg)
            else:
                # require_valid_po off: the check still reads ✗ on any card,
                # but no reason is appended — an otherwise-clean invoice
                # auto-receives despite the missing/unmatchable PO.
                checks["po_linked"] = "fail"
            # In batch, card here (this is the terminal for a skipped invoice). In
            # run-all we keep going and card ONCE at the end, so the snapshot
            # includes every later check — don't card early here.
            if fixes and not run_all:
                card_once()
            if not run_all:
                skipped.append(verdict_now())
                continue
        else:
            checks["po_linked"] = "pass"
            po = call_api(
                "loadedhub",
                "get_stock_purchase_order",
                dict(base, purchase_order_id=po_id),
            )
            if isinstance(po, dict) and po.get("error"):
                # An unfetchable PO reads as "not usably linked" — same key.
                _fail(
                    checks,
                    reasons,
                    "po_linked",
                    "Could not fetch linked purchase order: " + po["error"],
                )
                po = None
                if not run_all:
                    skipped.append(verdict_now())
                    continue
            elif po_autolinked and isinstance(po, dict):
                # PO found by Norm, not originally linked in Loaded: not validated
                # as-is. Show po_linked as a SUGGESTED change rather than a clean
                # pass, and add the link as a suggestion. Validation continues
                # against this PO; the link is written to Loaded on receive
                # (skipped if the PO is already invoiced on another invoice).
                checks["po_linked"] = "suggest"
                order_no = po.get("orderNumber") or po_number_hint
                other_inv = po.get("linkedInvoiceId")
                split = bool(other_inv and other_inv != inv_id)
                sib = None
                doubled_up = False
                if split:
                    # The PO is taken by a sibling invoice (Loaded is 1:1).
                    # Classify: SECOND DELIVERY of a split order (sibling
                    # carries different goods/quantities) vs DOUBLED-UP
                    # invoice (sibling already contains the same lines and
                    # total). One sibling fetch; deterministic comparison;
                    # any doubt → split, never removal.
                    sib = call_api(
                        "loadedhub",
                        "get_invoice_detail",
                        dict(base, invoice_id=other_inv),
                    )
                    if not isinstance(sib, dict) or sib.get("error"):
                        sib = None
                    if sib:
                        sib_lines = [
                            sl
                            for sl in sib.get("lines") or []
                            if isinstance(sl, dict) and not sl.get("deletedAt")
                        ]
                        my_lines = [
                            ln
                            for ln in detail.get("lines") or []
                            if isinstance(ln, dict) and not ln.get("deletedAt")
                        ]

                        def _dup_pair(ln):
                            m = _plain_match(
                                {
                                    "code": ln.get("code"),
                                    "description": ln.get("description"),
                                },
                                [
                                    {
                                        "code": sl.get("code"),
                                        "description": sl.get("description"),
                                        "_sl": sl,
                                    }
                                    for sl in sib_lines
                                ],
                            )
                            if not m:
                                return False
                            sl = m["_sl"]
                            return dec(sl.get("quantityReceived")) == dec(
                                ln.get("quantityReceived")
                            ) and close(dec(_ln_cost(sl)), dec(_ln_cost(ln)), line_tol)

                        doubled_up = (
                            bool(my_lines)
                            and all(_dup_pair(ln) for ln in my_lines)
                            and close(
                                dec(detail.get("total")),
                                dec(sib.get("total")),
                                totals_tol,
                            )
                        )
                    split_po_id = po.get("id") or detail.get("linkedPurchaseOrderId")
                    split_po_number = str(order_no)
                    split_sibling_invoice_id = other_inv
                    split_sibling_reference = (sib or {}).get("referenceNumber")
                    split_sibling_file_id = (sib or {}).get("fileId")
                sib_label = str(split_sibling_reference or "another invoice")
                if split and doubled_up:
                    # NOT a split: the order was already fully invoiced by
                    # the sibling. The reference on THIS invoice is bogus —
                    # offer removing it (dismiss = "genuinely a second
                    # delivery"). Reuses the duplicate check; the card gets
                    # the sibling links for side-by-side comparison.
                    split_remove_po = True
                    duplicate_of_id = other_inv
                    duplicate_of_file_id = split_sibling_file_id
                    _fail(
                        checks,
                        reasons,
                        "duplicate",
                        ref
                        + ": order "
                        + str(order_no)
                        + " was already fully invoiced by "
                        + sib_label
                        + " on "
                        + str((sib or {}).get("receivedAt") or "")[:10]
                        + " (same lines, total "
                        + money((sib or {}).get("total"))
                        + ") — remove the order reference from this invoice "
                        "(dismiss if this genuinely is a second delivery)",
                    )
                elif split:
                    # Genuine split delivery. Loaded-sourced numbers adopt
                    # the state automatically (scenario 1); a number found
                    # only on the COPY needs the user to accept setting the
                    # reference first (scenario 3).
                    split_order = True
                    split_po_suggested = po_match_source == "copy"
                fixes.append(
                    {
                        "type": "link_po",
                        "invoice_id": inv_id,
                        "reference": ref,
                        "po_number": str(order_no),
                        # The RESOLVED Loaded PO id, so the editor can show this
                        # suggestion IN the Order Number picker (pre-filled,
                        # marked suggested) — not just as a list row.
                        "purchase_order_id": po.get("id")
                        or detail.get("linkedPurchaseOrderId"),
                        "already_linked_elsewhere": split,
                        # The invoice the PO is already linked to (split-order
                        # case) — the editor links to it + its copy in Loaded.
                        "linked_invoice_id": other_inv or None,
                        "linked_invoice_reference": split_sibling_reference,
                        "linked_invoice_file_id": split_sibling_file_id,
                        "summary": (
                            (
                                "Order "
                                + str(order_no)
                                + " was already fully invoiced by "
                                + sib_label
                                + " — remove the order reference (see Needs "
                                "Attention)"
                                if doubled_up
                                else "Order "
                                + str(order_no)
                                + " was split across deliveries — "
                                + sib_label
                                + " carries the order link; this invoice is "
                                "validated against the order and received "
                                "without re-linking"
                            )
                            if split
                            else "Link purchase order "
                            + str(order_no)
                            + " — found automatically, saved to Loaded on receive"
                        ),
                    }
                )

        # Layer 4: the linked PO must belong to the same supplier
        if po:
            if (
                po.get("supplierId")
                and detail.get("linkedSupplierId")
                and po["supplierId"] != detail["linkedSupplierId"]
            ):
                # Alias-aware: venues carry DUPLICATE supplier records for one
                # business (PO on 'Ellesmere Butchery', invoice on 'Tamar
                # Farming Company' whose stored alias is 'Ellesmere Butchery
                # Ltd'). When the PO supplier's NAME matches the invoice
                # supplier's name or aliases (normalized containment, ≥3
                # chars — the supplier-matching convention), it is the same
                # business: pass instead of a false mismatch.
                al = call_api(
                    "loadedhub",
                    "get_supplier_aliases",
                    dict(base, supplier_id=detail.get("linkedSupplierId")),
                )
                known = [detail.get("supplierName")] + [
                    a.get("name")
                    for a in (al if isinstance(al, list) else [])
                    if isinstance(a, dict)
                ]

                def _same_supplier(a, b):
                    na, nb = norm(a), norm(b)
                    return len(na) >= 3 and len(nb) >= 3 and (na in nb or nb in na)

                po_name = po.get("supplierName")
                if po_name and any(_same_supplier(po_name, n) for n in known if n):
                    checks["po_supplier"] = "pass"
                else:
                    po_supplier_mismatch = True
                    po_supplier_name = po.get("supplierName")
                    _fail(
                        checks,
                        reasons,
                        "po_supplier",
                        "Purchase order supplier ("
                        + str(po.get("supplierName"))
                        + ") does not match invoice supplier ("
                        + str(detail.get("supplierName"))
                        + ")",
                    )
            else:
                checks["po_supplier"] = "pass"

        # Layer 4 (cont.): every stock item, brand and unit must already exist
        # in Loaded — anything Loaded would show with a NEW tag on the receive
        # screen (a value with no linked id) blocks auto-receiving.
        new_values = []
        for ln in lines:
            name = str(ln.get("description") or ln.get("code") or "?")
            if not ln.get("linkedItemId"):
                new_values.append("stock item on line '" + name + "'")
            if not ln.get("linkedUnitId"):
                new_values.append(
                    "unit '" + str(ln.get("unit")) + "' on line '" + name + "'"
                )
            if ln.get("brand") and not ln.get("linkedBrandId"):
                new_values.append(
                    "brand '" + str(ln.get("brand")) + "' on line '" + name + "'"
                )
        if new_values:
            shown = "; ".join(new_values[:5])
            if len(new_values) > 5:
                shown += "; … " + str(len(new_values) - 5) + " more"
            _fail(
                checks,
                reasons,
                "items_matched",
                str(len(new_values))
                + " value(s) are not in the Loaded database (would be created as NEW): "
                + shown,
            )
        else:
            checks["items_matched"] = "pass"

        # NOTE: PO lines are deliberately not compared or displayed — invoices
        # legitimately differ from their purchase order (substitutions, catch
        # weight, extra items); the copy is the source of truth. Per-line
        # arithmetic (qty × cost = line total) is not checked either: Loaded
        # enforces it on entry.

        if reasons and not run_all:
            skipped.append(verdict_now())
            continue

        # Layer 5: internal totals
        subtotal, tax = dec(detail.get("subtotal")), dec(detail.get("taxAmount"))
        line_sum = sum((dec(_ln_tot(ln)) or D(0)) for ln in lines)
        # Loaded's own receive flow absorbs up to 10c of drift between the
        # line-computed total and the stated invoice total as a "rounding
        # amount" (verified in their app bundle: tolerance +-0.10, shown as a
        # Rounding row in the totals; the stated total is kept). Mirror it:
        # inside that band the totals check passes and nothing is rewritten.
        _discount = dec(detail.get("discountAmount")) or D(0)
        _computed_total = (line_sum or D(0)) + (tax or D(0)) - _discount
        rounding_ok = total is not None and abs((total or D(0)) - _computed_total) <= D(
            "0.10"
        )
        if rounding_ok:
            checks["totals"] = "pass"
        elif not close(line_sum, subtotal, totals_tol):
            _fail(
                checks,
                reasons,
                "totals",
                "Line items sum to "
                + money(line_sum)
                + " but the invoice subtotal is "
                + money(subtotal)
                + " (difference "
                + money(abs(line_sum - (subtotal or D(0))))
                + ")",
            )
        elif not close((subtotal or D(0)) + (tax or D(0)), total, totals_tol):
            _fail(
                checks,
                reasons,
                "totals",
                "Subtotal "
                + money(subtotal)
                + " + tax "
                + money(tax)
                + " = "
                + money((subtotal or D(0)) + (tax or D(0)))
                + " but the invoice total is "
                + money(total),
            )
        else:
            checks["totals"] = "pass"

        if reasons and not run_all:
            skipped.append(verdict_now())
            continue

        # Layer 6: verify against the supplier's attached invoice copy
        # (only reached when every cheaper gate passed — this is the one
        # LLM-extraction call per invoice; the copy's presence was checked
        # up front in layer 2)
        pdf = (
            extract_document(
                "loadedhub",
                "download_invoice_file",
                dict(base, file_id=detail["fileId"]),
                schema=PDF_SCHEMA,
                instructions=_pdf_instructions(detail),
            )
            if detail.get("fileId")
            else None
        )
        # The copy checks run only when a copy is attached; without a fileId pdf
        # stays None and pdf_readable (and the checks below) stay "—" not checked.
        if detail.get("fileId") and (not isinstance(pdf, dict) or pdf.get("error")):
            err = pdf.get("error") if isinstance(pdf, dict) else "unreadable"
            _fail(
                checks,
                reasons,
                "pdf_readable",
                "Could not read the attached invoice document: " + str(err),
            )
            pdf = None
        elif isinstance(pdf, dict):
            checks["pdf_readable"] = "pass"

        # A non-invoice uploaded as a draft: a supplier STATEMENT (lists prior
        # invoices, payments, balances) or a LETTER/NOTICE (document_type
        # 'other' with NO product lines — e.g. a delivery-surcharge notice).
        # Neither is receivable — fail the document-type check, suggest
        # deleting the draft, and skip the line-vs-copy comparison entirely.
        # 'other' WITH extracted lines is deliberately NOT treated as
        # deletable: a misclassified real invoice must never be offered a
        # delete — the normal line comparison handles it.
        _pdf_type = pdf.get("document_type") if isinstance(pdf, dict) else None
        _is_letter = _pdf_type == "other" and not (
            isinstance(pdf, dict) and pdf.get("lines")
        )
        if pdf and (_pdf_type == "statement" or _is_letter):
            if _pdf_type == "statement":
                _fail(
                    checks,
                    reasons,
                    "credit_note",
                    "The attached copy is a supplier STATEMENT (it lists "
                    "invoices, payments and balances), not an invoice — this "
                    "draft should be deleted from Loaded",
                )
                _del_summary = (
                    "This document is a supplier statement, not "
                    "an invoice — delete this draft in Loaded"
                )
            else:
                _fail(
                    checks,
                    reasons,
                    "credit_note",
                    "The attached copy is not an invoice (a supplier letter "
                    "or notice with no product lines) — this draft should be "
                    "deleted from Loaded",
                )
                _del_summary = (
                    "This document is a supplier letter/notice, not "
                    "an invoice — delete this draft in Loaded"
                )
            if not any(f.get("type") == "delete_invoice" for f in fixes):
                # (the duplicate check may already have proposed the delete —
                # one delete suggestion per draft is enough)
                fixes.append(
                    {
                        "type": "delete_invoice",
                        "invoice_id": inv_id,
                        "reference": ref,
                        "summary": _del_summary,
                    }
                )
            pdf = None
            if not run_all:
                card_once()
                skipped.append(verdict_now())
                continue

        if pdf:
            # The copy must be for THIS invoice (only fails on a live conflict —
            # a copy with no printed number is caught by the line-level checks)
            if (
                norm(pdf.get("invoice_number"))
                and norm(ref)
                and norm(pdf.get("invoice_number")) != norm(ref)
            ):
                copy_invoice_number = str(pdf.get("invoice_number"))
                _fail(
                    checks,
                    reasons,
                    "pdf_invoice_number",
                    "Attached copy is for invoice '"
                    + str(pdf.get("invoice_number"))
                    + "' but this invoice is '"
                    + ref
                    + "'",
                )
            else:
                checks["pdf_invoice_number"] = "pass"

            pdf_ok = True
            uom_ok, uom_compared = True, False
            pdf_lines = list(pdf.get("lines") or [])
            unclaimed = list(pdf_lines)
            # Dry-run the plain rules first: (a) find lines that will need a
            # VARIANT lookup (unmatched but linked to a stock item), (b) mark
            # which doc lines a plain rule will claim — a variant match must
            # never steal one of those from a later plainly-matching line.
            _dry_pool = list(pdf_lines)
            _deferred_ids = []
            for _ln in lines:
                if _ln.get("id") in strike_ids:
                    continue
                _m = _plain_match(_ln, _dry_pool)
                if _m is not None:
                    _dry_pool.remove(_m)
                elif _ln.get("linkedItemId"):
                    _deferred_ids.append(_ln.get("linkedItemId"))
            _need = []
            for _iid in _deferred_ids:
                if (
                    _iid not in _item_cache
                    and _iid not in _need
                    and _variant_fetch_budget[0] > 0
                ):
                    _variant_fetch_budget[0] -= 1
                    _need.append(_iid)
            if _need:
                if call_api_parallel and len(_need) > 1:
                    _fetched_items = call_api_parallel(
                        [
                            (
                                "loadedhub",
                                "get_stock_item",
                                dict(base, item_id=_iid, include_deleted="true"),
                            )
                            for _iid in _need
                        ]
                    )
                else:
                    _fetched_items = [
                        call_api(
                            "loadedhub",
                            "get_stock_item",
                            dict(base, item_id=_iid, include_deleted="true"),
                        )
                        for _iid in _need
                    ]
                for _iid, _it in zip(_need, _fetched_items):
                    if isinstance(_it, dict) and not _it.get("error"):
                        _item_cache[_iid] = _it
            for ln in lines:
                if ln.get("id") in strike_ids:
                    # Redundant $0 duplicate (strike suggestion already emitted) —
                    # it isn't on the copy, so don't match it or flag it "not
                    # found". A $0 line changes no total, so pdf_ok / the subtotal
                    # check are untouched.
                    continue
                rec = rec_by_id.get(ln.get("id"))
                match = _plain_match(ln, unclaimed)
                if match is None and ln.get("linkedItemId") in _item_cache:
                    # Variant retry — only over doc lines no plain rule will
                    # claim (value membership in the dry-run's leftovers).
                    match = _variant_claim(
                        ln,
                        _item_cache[ln.get("linkedItemId")],
                        detail.get("linkedSupplierId"),
                        [c for c in unclaimed if c in _dry_pool],
                    )
                if match is None:
                    pdf_ok = False
                    if rec:
                        rec["on_copy"] = "✗"
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "' not found on the attached invoice document"
                    )
                    # Actionable form: the copy_missing flag (set on the card
                    # line below) renders as a "remove" suggestion in the
                    # editor — strike-style, soft-deleted at receive. A flag,
                    # not a fixes entry: the reason above already narrates it,
                    # and per-line fix dicts were what blew the payload cap.
                    # Suppressed for statement/duplicate drafts whose real fix
                    # is delete_invoice. The reason stays regardless — reasons
                    # are what keep autopilot from auto-receiving.
                    if not any(f.get("type") == "delete_invoice" for f in fixes):
                        missing_on_copy_ids.add(ln.get("id"))
                    continue
                unclaimed.remove(match)
                copy_by_line_id[ln.get("id")] = {
                    "unit": match.get("unit"),
                    "quantity": match.get("quantity"),
                    "unit_price_ex_tax": match.get("unit_price_ex_tax"),
                    "line_total_ex_tax": match.get("line_total_ex_tax"),
                    "unit_of_measure": match.get("unit_of_measure"),
                    "unit_unrecognisable": match.get("unit_unrecognisable"),
                }
                if rec:
                    rec["on_copy"] = "✓"
                    rec["unit"]["copy"] = match.get("unit")
                    rec["quantity"]["copy"] = opt_num(match.get("quantity"))
                    rec["unit_cost"]["copy"] = opt_money(match.get("unit_price_ex_tax"))
                    rec["line_total"]["copy"] = opt_money(
                        match.get("line_total_ex_tax")
                    )
                # Unit: the ONE meaningful comparison is Loaded's unit vs the
                # guideline-derived DELIVERED unit from the copy (below). The
                # literal printed unit column ('ea', 'CTN', 'pkt') is a packaging
                # label, not the delivered unit, so it is NOT compared — comparing
                # it produced a confusing second "copy says <packaging>" note
                # alongside the real delivered-unit recommendation.
                # Unit of measure: Loaded's unit vs the guideline-derived
                # delivered unit from the copy. Both sides must parse to a
                # (type, magnitude) before a mismatch counts — otherwise
                # the check stays "not checked" for this line.
                derived = match.get("unit_of_measure")
                if rec:
                    rec["unit"]["derived"] = derived
                # The copy CARRIES unit/size information but it can't be read
                # (cut off, illegible, ambiguous): never guess — fail the check
                # with a confirm ask and NO proposed unit. The reason blocks
                # autopilot (reasons gate runs before the receive).
                if not derived and match.get("unit_unrecognisable"):
                    uom_compared = True
                    uom_ok = False
                    if rec:
                        rec["unit"]["result"] = "✗"
                    unit_confirm_ids.add(ln.get("id"))
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "': the unit can't be determined from the invoice "
                        "copy — confirm the unit before receiving (currently '"
                        + str(ln.get("unit"))
                        + "')"
                    )
                # Semantic equality first (_units_equivalent): multipacks
                # component-wise ('6x1L' == '6 X 1 Litre'), magnitudes
                # ('0.7 L' == '700 mL'), a count-only pack vs the sized
                # multipack ('6 pack' == '6x750mL'), 'each' vs a single
                # bottle size. Then the mismatch shapes. uom_mismatch:
                # None = not comparable, False = matches, True = mismatch.
                uom_mismatch = None
                if derived and _units_equivalent(ln.get("unit"), derived):
                    uom_mismatch = False
                elif derived and _is_multipack(derived):
                    # A real multipack on the copy vs a line unit it does NOT
                    # denote — including an unparseable packaging word
                    # ('Case(s)' vs '6x 750ml', the Eurovintage case).
                    uom_mismatch = True
                else:
                    pi, pd = parse_unit(ln.get("unit")), parse_unit(derived)
                    if pi and pd:
                        uom_mismatch = True
                    elif pd and not pi:
                        # The copy derived a REAL unit but the line's unit is
                        # unreadable — a bare packaging word ('Case(s)',
                        # 'Carton') or an unknown pack name. That IS a
                        # mismatch: the silent "not comparable" skip hid the
                        # Eurovintage Case(s)-vs-'6x 750ml' case entirely.
                        uom_mismatch = norm(ln.get("unit")) != norm(derived)
                if uom_mismatch is not None:
                    uom_compared = True
                    if not uom_mismatch:
                        if rec and rec["unit"]["result"] != "✗":
                            rec["unit"]["result"] = "✓"
                    else:
                        uom_ok = False
                        if rec:
                            rec["unit"]["result"] = "✗"
                        reasons.append(
                            "Line '"
                            + str(ln.get("description"))
                            + "': Loaded unit '"
                            + str(ln.get("unit"))
                            + "' but the copy indicates the delivered unit is '"
                            + str(derived)
                            + "' — correct the unit in Loaded (on the stock "
                            + "item) or on the invoice line"
                        )
                        # Fixable: set the line unit to the derived unit and
                        # update the matched supplier variant (Loaded's own
                        # "update variant?" flow). Needs the line's item +
                        # supplier + code to resolve the variant at apply time.
                        if ln.get("linkedItemId") and detail.get("linkedSupplierId"):
                            fixes.append(
                                {
                                    "type": "unit",
                                    "invoice_id": inv_id,
                                    "reference": ref,
                                    "line_id": ln.get("id"),
                                    "line_code": ln.get("code"),
                                    "description": str(ln.get("description")),
                                    "linked_item_id": ln.get("linkedItemId"),
                                    "linked_supplier_id": detail.get(
                                        "linkedSupplierId"
                                    ),
                                    "current_unit": str(ln.get("unit")),
                                    "proposed_unit": str(derived),
                                    "summary": str(ln.get("code") or ref)
                                    + " · "
                                    + str(ln.get("description"))
                                    + ": unit "
                                    + str(ln.get("unit"))
                                    + " → "
                                    + str(derived)
                                    + " (updates the variant too)",
                                }
                            )
                if dec(match.get("quantity")) != dec(ln.get("quantityReceived")):
                    pdf_ok = False
                    if rec:
                        rec["quantity"]["result"] = "✗"
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "': quantity "
                        + str(ln.get("quantityReceived"))
                        + " does not equal the document's quantity "
                        + str(match.get("quantity"))
                    )
                    # Fixable: set Qty received to the copy's quantity. A LOCAL
                    # draft edit (applied on receive, never a Loaded write now) —
                    # the editor lists it under Suggested Changes and also offers
                    # it inline on the line.
                    #
                    # ONLY when the extracted line is self-consistent: quantity x
                    # unit price must equal the line total. If it doesn't, one of
                    # the three extracted numbers is a misread (seen live: qty 4
                    # x $4.53 vs line total $72.48 — the real qty was 16, printed
                    # as a carton/singles split), so we keep the mismatch FLAG
                    # but withhold the one-click Accept for a provably wrong
                    # number.
                    _q = dec(match.get("quantity"))
                    _p = dec(match.get("unit_price_ex_tax"))
                    _t = dec(match.get("line_total_ex_tax"))
                    if (
                        _q is not None
                        and _p is not None
                        and _t is not None
                        and close(_q * _p, _t, line_tol)
                    ):
                        fixes.append(
                            {
                                "type": "quantity",
                                "invoice_id": inv_id,
                                "reference": ref,
                                "line_id": ln.get("id"),
                                "line_code": ln.get("code"),
                                "description": str(ln.get("description")),
                                "current_quantity": ln.get("quantityReceived"),
                                "proposed_quantity": match.get("quantity"),
                                "summary": str(ln.get("code") or ref)
                                + " · "
                                + str(ln.get("description"))
                                + ": Qty received "
                                + str(ln.get("quantityReceived"))
                                + " → "
                                + str(match.get("quantity"))
                                + " (per the invoice copy)",
                            }
                        )
                elif rec:
                    rec["quantity"]["result"] = "✓"
                # None (unpriced feed) and 0 are the same displayed value —
                # compare them as equal so "$0.00 does not equal $0.00" never
                # flags, and a $0 copy price is never suggested onto a $0 line.
                _inv_cost = dec(_ln_cost(ln))
                _inv_cost = _inv_cost if _inv_cost is not None else D(0)
                _copy_cost = dec(match.get("unit_price_ex_tax"))
                _copy_cost = _copy_cost if _copy_cost is not None else D(0)
                if _copy_cost != _inv_cost:
                    pdf_ok = False
                    if rec:
                        rec["unit_cost"]["result"] = "✗"
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "': unit cost "
                        + money(_ln_cost(ln))
                        + " does not equal the document's unit price "
                        + money(match.get("unit_price_ex_tax"))
                    )
                    # Fixable: set the line's unit cost to the copy's printed
                    # unit price — a LOCAL draft edit like the quantity fix
                    # (common with unpriced feeds: Loaded ingests the lines
                    # with no costs and only the copy carries the prices).
                    # Same self-consistency guard as quantity: only offer a
                    # one-click Accept when the copy line multiplies out.
                    _cq = dec(match.get("quantity"))
                    _cp = dec(match.get("unit_price_ex_tax"))
                    _ct = dec(match.get("line_total_ex_tax"))
                    if (
                        _cq is not None
                        and _cp is not None
                        and _ct is not None
                        and close(_cq * _cp, _ct, line_tol)
                    ):
                        fixes.append(
                            {
                                "type": "unit_cost",
                                "invoice_id": inv_id,
                                "reference": ref,
                                "line_id": ln.get("id"),
                                "line_code": ln.get("code"),
                                "description": str(ln.get("description")),
                                "current_unit_cost": _ln_cost(ln),
                                "proposed_unit_cost": match.get("unit_price_ex_tax"),
                                "summary": str(ln.get("code") or ref)
                                + " · "
                                + str(ln.get("description"))
                                + ": unit cost "
                                + money(_ln_cost(ln))
                                + " → "
                                + money(match.get("unit_price_ex_tax"))
                                + " (per the invoice copy)",
                            }
                        )
                elif rec:
                    rec["unit_cost"]["result"] = "✓"
                _inv_tot = dec(_ln_tot(ln))
                _inv_tot = _inv_tot if _inv_tot is not None else D(0)
                _copy_tot = dec(match.get("line_total_ex_tax"))
                _copy_tot = _copy_tot if _copy_tot is not None else D(0)
                if not close(_copy_tot, _inv_tot, line_tol):
                    pdf_ok = False
                    if rec:
                        rec["line_total"]["result"] = "✗"
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "': line total "
                        + money(_ln_tot(ln))
                        + " does not equal the document's line total "
                        + money(match.get("line_total_ex_tax"))
                    )
                elif rec:
                    rec["line_total"]["result"] = "✓"
            # Prevailing tax rate of the invoice's real lines — an add_line
            # suggestion must carry it or the added line contributes zero tax
            # and trips the editor's totals check on every GST invoice.
            _rate_counts = {}
            for _ln in lines:
                _r = _ln.get("saleTaxRate")
                if _r is not None:
                    _rate_counts[_r] = _rate_counts.get(_r, 0) + 1
            _prevailing_rate = None
            for _r in _rate_counts:
                if _prevailing_rate is None or (
                    _rate_counts[_r] > _rate_counts[_prevailing_rate]
                ):
                    _prevailing_rate = _r
            _statement_like = any(f.get("type") == "delete_invoice" for f in fixes)
            for cand in unclaimed:
                pdf_ok = False
                line_records.append(
                    {
                        "line": str(cand.get("description")) + " — on copy only",
                        "stock_item": "—",
                        "on_copy": "✗",
                        "quantity": {
                            "invoice": None,
                            "copy": opt_num(cand.get("quantity")),
                            "result": "✗",
                        },
                        "unit_cost": {
                            "invoice": None,
                            "copy": opt_money(cand.get("unit_price_ex_tax")),
                            "result": "✗",
                        },
                        "line_total": {
                            "invoice": None,
                            "copy": opt_money(cand.get("line_total_ex_tax")),
                            "result": "✗",
                        },
                    }
                )
                reasons.append(
                    "Document line '"
                    + str(cand.get("description"))
                    + "' ("
                    + money(cand.get("line_total_ex_tax"))
                    + ") has no matching invoice line"
                )
                # Actionable form of the same finding: offer to ADD the copy's
                # line to the draft. Suppressed for statement/duplicate drafts
                # (their real fix is delete_invoice — their rows aren't
                # products). The reason above stays regardless: reasons are
                # what keep autopilot from auto-receiving.
                if not _statement_like:
                    fixes.append(
                        {
                            "type": "add_line",
                            "invoice_id": inv_id,
                            "reference": ref,
                            "code": cand.get("code"),
                            "description": cand.get("description"),
                            "quantity": cand.get("quantity"),
                            "unit": cand.get("unit"),
                            "unit_price_ex_tax": cand.get("unit_price_ex_tax"),
                            "line_total_ex_tax": cand.get("line_total_ex_tax"),
                            "sale_tax_rate": _prevailing_rate,
                            "summary": "Add '"
                            + str(cand.get("description"))
                            + "' ("
                            + money(cand.get("line_total_ex_tax"))
                            + ") from the invoice copy",
                        }
                    )
            checks["pdf_lines"] = "pass" if pdf_ok else "fail"
            # Only set when at least one line was confidently comparable;
            # otherwise the checklist honestly shows "—" (not checked).
            if uom_compared or not uom_ok:
                checks["unit_of_measure"] = "pass" if uom_ok else "fail"

            # Gate 11 (PDF side): document total vs invoice total
            if not close(dec(pdf.get("total_incl_tax")), total, totals_tol):
                # A readable printed total that Loaded's header disagrees with
                # (e.g. the feed left the total $0): carry the copy's totals to
                # the card so the editor can offer them as a one-click edit.
                if dec(pdf.get("total_incl_tax")) is not None:
                    copy_totals = {
                        "copy_total": pdf.get("total_incl_tax"),
                        "copy_subtotal": pdf.get("subtotal_ex_tax"),
                        "copy_tax_amount": pdf.get("tax_amount"),
                    }
                _fail(
                    checks,
                    reasons,
                    "pdf_total",
                    "Invoice total "
                    + money(total)
                    + " does not match the document total "
                    + money(pdf.get("total_incl_tax")),
                )
            else:
                checks["pdf_total"] = "pass"

            # Gate 12 (PDF side): the supplier printed on the copy. Fires only
            # when the invoice has NO linked supplier or the model judged the
            # printed supplier a DIFFERENT business (same-business naming
            # variation never churns) — then one bounded LLM match over the
            # venue's supplier list proposes the link.
            copy_supplier_seen = pdf.get("supplier_name")
            supplier_missing = not detail.get("linkedSupplierId")
            supplier_differs_seen = (
                bool(pdf.get("supplier_differs")) and not supplier_missing
            )
            if copy_supplier_seen and supplier_differs_seen:
                # Stage 1½ (deterministic belt): an EXACT hit on the linked
                # supplier's name or stored aliases is the same business,
                # whatever the model said. The primary path is now stage 1
                # itself — the extraction clause names the aliases, so the
                # model's same-business judgment covers name VARIANTS of an
                # alias too. Uses the per-run alias cache (no extra call).
                known = [detail.get("supplierName")] + _supplier_aliases(
                    detail.get("linkedSupplierId")
                )
                if any(norm(copy_supplier_seen) == norm(n) for n in known if n):
                    supplier_differs_seen = False
            if copy_supplier_seen and (supplier_missing or supplier_differs_seen):
                sm = call_api(
                    "norm",
                    "match_supplier",
                    dict(base, supplier_name=copy_supplier_seen),
                )
                m = sm.get("match") if isinstance(sm, dict) else None
                if (
                    not supplier_missing
                    and isinstance(m, dict)
                    and m.get("supplier_id") == detail.get("linkedSupplierId")
                ):
                    # The copy's name resolves to the ALREADY-linked supplier
                    # — same business after all; pass instead of churn.
                    supplier_differs_seen = False
                    checks["supplier"] = "pass"
                else:
                    if isinstance(m, dict) and m.get("supplier_id"):
                        supplier_match = m
                    msg = (
                        "No supplier linked in Loaded — the copy names '"
                        + str(copy_supplier_seen)
                        + "'"
                        if supplier_missing
                        else "Loaded supplier '"
                        + str(detail.get("supplierName"))
                        + "' but the copy names '"
                        + str(copy_supplier_seen)
                        + "'"
                    )
                    if supplier_match:
                        msg += (
                            " — matches Loaded supplier '"
                            + str(supplier_match.get("supplier_name"))
                            + "'"
                        )
                    else:
                        msg += " — no matching Loaded supplier found"
                    _fail(checks, reasons, "supplier", msg)
            elif copy_supplier_seen and detail.get("linkedSupplierId"):
                checks["supplier"] = "pass"

        verdict = verdict_now()

        if reasons:
            # Card EVERY invoice that needs the user — with one-click fixes or
            # not, the editor card is where they resolve it (pick a PO, edit
            # lines) and receive. Invoices that failed before a detail fetch
            # never reach here, so a card is always renderable.
            card_once()
            skipped.append(verdict)
            continue

        if fixes and not autopilot:
            # A SUGGESTED change is pending (e.g. a PO the engine resolved but
            # Loaded doesn't have linked): approve_fixes only auto-receives
            # exact matches, so this waits on a card for the user — only
            # autopilot applies confident fixes unattended (the resolved link
            # rides the receive write below).
            verdict["outcome"] = "awaiting your approval"
            received.append(verdict)
            card_once()
            continue

        if dry_run:
            verdict["outcome"] = "awaiting your approval"
            received.append(verdict)
            # approve_all: a perfect invoice still needs the user's OK, so it
            # gets an approval card (full receive view, no suggested changes).
            if approve_all:
                card_once()
            continue

        body = dict(detail)
        body["isReceived"] = True
        body["receivedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if any(f.get("already_linked_elsewhere") for f in fixes):
            # Split order: the resolved PO id was INJECTED into detail for
            # validation, but the PO belongs to the sibling invoice (Loaded
            # is 1:1) — writing it would steal the link. Drop ONLY the
            # injected id (purchaseOrderNumber is Loaded's own field);
            # the API path's do_receive guard does the same.
            body.pop("linkedPurchaseOrderId", None)
        result = call_api(
            "loadedhub", "receive_invoice", dict(base, invoice_id=inv_id, invoice=body)
        )
        if isinstance(result, dict) and result.get("error"):
            verdict["reasons"] = ["Receive failed: " + result["error"]]
            skipped.append(verdict)
        elif isinstance(result, dict) and not result.get("isReceived"):
            verdict["reasons"] = [
                "Receive call succeeded but Loaded did not mark the invoice as received"
            ]
            skipped.append(verdict)
        else:
            verdict["outcome"] = "received"
            received.append(verdict)

    def checks_summary(v):
        checklist = v.get("checklist")
        if isinstance(checklist, str):  # "All N checks passed ✓"
            return str(len(CHECK_LABELS)) + "✓"
        results = [c["result"] for c in checklist or []]
        if not results:
            return "—"
        parts = [str(results.count("✓")) + "✓"]
        if "✗" in results:
            parts.append(str(results.count("✗")) + "✗")
        if "—" in results:
            parts.append(str(results.count("—")) + " not checked")
        return " ".join(parts)

    rows = [
        {
            "reference": v["reference_number"],
            "supplier": v.get("supplier_name"),
            "po": v.get("po_number") or "—",
            "total": v["total"],
            "checks": checks_summary(v),
            "outcome": v.get("outcome", "skipped"),
            "reasons": " • ".join(v["reasons"]) if v.get("reasons") else "—",
        }
        for v in received + skipped
    ]
    # Flat list of every proposed fix across ALL verdicts (awaiting-approval
    # invoices carry fixes too — e.g. an auto-resolved PO link), each with a
    # stable id the interactive card selects by and the handler applies by.
    all_fixes = []
    for v in received + skipped:
        for i, fx in enumerate(v.get("fixes") or []):
            fx = dict(fx)
            fx["id"] = str(fx.get("invoice_id")) + ":" + fx["type"] + ":" + str(i)
            all_fixes.append(fx)

    return {
        "venue": venue,
        "dry_run": dry_run,
        "from_date": from_date,
        "to_date": to_date,
        "reviewed": len(drafts),
        "results": rows,
        "received": received,
        "skipped": skipped,
        "fixes": all_fixes,
        "fix_invoices": fix_invoices,
        "mode": mode,
        "mode_unset": mode_unset,
        "auto_submit": autopilot,
        "summary": {"received": len(received), "skipped": len(skipped)},
    }


def _fail(checks, reasons, key, message):
    checks[key] = "fail"
    reasons.append(message)


def _verdict(stub, reasons):
    return {
        "invoice_id": stub.get("id"),
        "reference_number": stub.get("referenceNumber") or "(no number)",
        "supplier_name": stub.get("supplierName"),
        "po_number": None,
        "total": "$" + str(stub.get("total")),
        "reasons": reasons,
    }
