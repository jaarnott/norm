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
    "supplier_name": "string or null",
    "invoice_number": "string or null",
    "invoice_date": "string or null",
    "purchase_order_number": "string or null",
    "lines": [
        {
            "code": "string or null — the product/item code column",
            "description": "string",
            "quantity": "number — exactly as printed",
            "unit": "string or null — EXACTLY as printed on the document",
            "unit_of_measure": (
                "string or null — the DELIVERED unit of ONE item, per the "
                "unit rules in the instructions (e.g. 'Kilo', '5L', '500g', "
                "'750ml', '12 pack', '100 piece'); null if not determinable"
            ),
            "unit_price_ex_tax": "number — exactly as printed",
            "line_total_ex_tax": "number — exactly as printed",
        }
    ],
    "charges": [
        {
            "description": "string — non-product charges e.g. Freight, Credit Card Fee",
            "amount_ex_tax": "number",
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
    ("credit_note", "Not a credit note"),
    ("pdf_present", "Invoice copy attached"),
    ("po_linked", "Linked to a purchase order"),
    ("po_supplier", "Supplier matches the purchase order"),
    ("items_matched", "Stock items, brands and units all exist in Loaded (no NEW)"),
    ("totals", "Invoice totals consistent"),
    ("pdf_readable", "Invoice copy readable"),
    ("pdf_invoice_number", "Invoice number matches the copy"),
    ("pdf_lines", "Lines match the invoice copy"),
    ("unit_of_measure", "Unit of measure matches the copy"),
    ("pdf_total", "Total matches the invoice copy"),
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

    parse_unit deliberately can't compare these, and a ratio-equal but
    differently-named unit ('15 KG') is NOT the same pack, so a multipack
    delivered unit is compared by name instead of magnitude.
    """
    s = str(text or "").lower()
    i = s.find("x")
    return i > 0 and s[i - 1].isdigit() and i + 1 < len(s) and s[i + 1].isdigit()


def _delivered_unit(u):
    """The copy's delivered unit ONLY when it's a real weight/volume/count unit (or
    a multipack) — never a bare packaging word ('pkt', 'ea', 'box', 'ctn', …), which
    ``parse_unit`` already rejects. Gates the 'use X' recommendation so a
    mis-extracted packaging word is never suggested as a unit to switch to.
    """
    return u if u and (_is_multipack(u) or parse_unit(u)) else None


def _words(text):
    """Significant (length >= 4) lowercase words in a description — used to match a
    charge-type invoice line ('FREIGHT - FOOD') to a copy charge ('Freight (ex
    GST)') by a shared word like 'freight', which a substring test would miss.
    """
    cleaned = "".join(ch if ch.isalnum() else " " for ch in str(text or "").lower())
    return {w for w in cleaned.split() if len(w) >= 4}


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
        # supplier's own order number instead). Extraction is cached.
        if not det.get("fileId"):
            return None
        hdr = extract_document(
            "loadedhub",
            "download_invoice_file",
            dict(base, file_id=det["fileId"]),
            schema=PO_EXTRACT_SCHEMA,
            instructions=(
                "Extract the buyer's purchase order number and the supplier's "
                "own order number — they differ."
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
        pos = call_api("loadedhub", "list_purchase_orders", dict(base))
        if isinstance(pos, dict):
            pos = pos.get("data") or []
        if isinstance(pos, list):
            matches = [
                p
                for p in pos
                if isinstance(p, dict) and _po_norm(p.get("orderNumber")) == want
            ]
            if supplier_id and any(
                p.get("supplierId") == supplier_id for p in matches
            ):
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
                    "loadedhub", "get_invoice_detail", dict(base, invoice_id=r.get("id"))
                )
                if isinstance(det, dict) and det.get("linkedPurchaseOrderId"):
                    po_ids.add(det["linkedPurchaseOrderId"])
        if len(po_ids) == 1:
            pid = list(po_ids)[0]  # sandbox has no next()/iter()
            po = call_api(
                "loadedhub", "get_stock_purchase_order", dict(base, purchase_order_id=pid)
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
        invoices = call_api(
            "loadedhub",
            "list_stock_invoices",
            dict(base, from_date=from_date, to_date=to_date, page=0, pageSize=100),
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
    # Full editable "Receive Invoice" payloads — one per invoice that has a
    # concrete auto-fix (link_po or unit). Raw values (numbers, ids) for the
    # interactive card; built from data already fetched, no extra API calls.
    fix_invoices = []

    for stub in drafts:
        inv_id = stub.get("id")
        detail = call_api(
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
        if only_invoice_id and not detail.get("linkedPurchaseOrderId"):
            if only_po_id:
                detail["linkedPurchaseOrderId"] = only_po_id
                po_autolinked = True
            elif detail.get("purchaseOrderNumber"):
                supplier_id = detail.get("linkedSupplierId")
                resolved = _resolve_po(detail.get("purchaseOrderNumber"), supplier_id)
                if not resolved:
                    copy_po = _copy_po_number(detail)
                    if copy_po and _po_norm(copy_po) != _po_norm(
                        detail.get("purchaseOrderNumber")
                    ):
                        resolved = _resolve_po(copy_po, supplier_id)
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
        _by_code = {}
        for _ln in lines:
            _code = norm(_ln.get("code"))
            if _code:
                _by_code.setdefault(_code, []).append(_ln)
        for _group in _by_code.values():
            if len(_group) > 1 and any(
                dec(x.get("totalCost")) not in (None, D(0)) for x in _group
            ):
                for x in _group:
                    if dec(x.get("totalCost")) == D(0):
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
                    "invoice": opt_money(ln.get("unitCost")),
                    "copy": None,
                    "result": "—",
                },
                "line_total": {
                    "invoice": opt_money(ln.get("totalCost")),
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
                    "quantity_received": ln.get("quantityReceived"),
                    "unit_cost": ln.get("unitCost"),
                    "total_cost": ln.get("totalCost"),
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
                # The review's decision that this is a redundant $0 duplicate; the
                # component renders a "strike" affordance from it. Striking (drop
                # from the receive) is the user's applied action, done via a
                # working-doc line edit — mirrors copy_quantity_mismatch's "use".
                if ln.get("id") in strike_ids:
                    line["copy_duplicate"] = True
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
            if new_lines:
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
            card = {
                "invoice_id": inv_id,
                "reference_number": ref,
                "supplier_name": detail.get("supplierName"),
                "linked_supplier_id": detail.get("linkedSupplierId"),
                "purchase_order_number": po_number_hint,
                "linked_purchase_order_id": detail.get("linkedPurchaseOrderId"),
                "issued_at": detail.get("issuedAt"),
                "due_at": detail.get("dueAt"),
                "subtotal": detail.get("subtotal"),
                "tax_amount": detail.get("taxAmount"),
                "total": detail.get("total"),
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
                    {"pass": "p", "fail": "f", "suggest": "s"}.get(
                        checks.get(key), "-"
                    )
                    for key, _label in CHECK_LABELS
                ),
            }
            # The specific failure reasons (e.g. "Line 'X': quantity 2.25 does
            # not equal the document's quantity 2") so the single-invoice editor
            # card can show WHAT didn't match, not just which check failed. ONLY
            # on the single-invoice card: the batch verdict already carries the
            # reasons once, and duplicating them onto every batch card would blow
            # the tool-result slim cap.
            if only_invoice_id and reasons:
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

            # Compact each line record's nested comparison dicts into the
            # display-ready cell strings the playbook renders (e.g.
            # "inv 4.95 / copy 4.95 ✓"). Keeps the LLM payload small
            # enough to survive the tool-result size cap without losing values.
            def cell(pairs, result):
                vals = [lbl + " " + str(v) for lbl, v in pairs if v not in (None, "")]
                sym = result if result in ("✓", "✗") else "—"
                if not vals:
                    return sym
                return " / ".join(vals) + " " + sym

            def compact_line(rec):
                if "stock_item" not in rec:
                    return rec  # the "… N more lines" omission marker
                unit = rec.get("unit") or {}
                qty = rec.get("quantity") or {}
                cost = rec.get("unit_cost") or {}
                tot = rec.get("line_total") or {}
                return {
                    "line": rec.get("line"),
                    "stock_item": rec.get("stock_item", "—"),
                    "on_copy": rec.get("on_copy", "—"),
                    "unit": cell(
                        [
                            ("inv", unit.get("invoice")),
                            ("copy", unit.get("copy")),
                            ("rec", unit.get("derived")),
                        ],
                        unit.get("result"),
                    ),
                    "quantity": cell(
                        [("inv", qty.get("invoice")), ("copy", qty.get("copy"))],
                        qty.get("result"),
                    ),
                    "unit_cost": cell(
                        [("inv", cost.get("invoice")), ("copy", cost.get("copy"))],
                        cost.get("result"),
                    ),
                    "line_total": cell(
                        [("inv", tot.get("invoice")), ("copy", tot.get("copy"))],
                        tot.get("result"),
                    ),
                }

            def hdr(field, invoice_val, po_val, copy_val, key):
                return {
                    "field": field,
                    "invoice": invoice_val if invoice_val not in (None, "") else "—",
                    "po": po_val if po_val not in (None, "") else "—",
                    "copy": copy_val if copy_val not in (None, "") else "—",
                    "result": symbol.get(checks.get(key), "—"),
                }

            checklist_rows = [
                {"check": label, "result": symbol.get(checks.get(key), "—")}
                for key, label in CHECK_LABELS
            ]
            details = {
                "header": [
                    hdr(
                        "Invoice number",
                        ref,
                        None,
                        (pdf or {}).get("invoice_number"),
                        "pdf_invoice_number",
                    ),
                    hdr(
                        "Supplier",
                        detail.get("supplierName"),
                        (po or {}).get("supplierName"),
                        (pdf or {}).get("supplier_name"),
                        "po_supplier",
                    ),
                    hdr(
                        "PO number",
                        po_number_hint,
                        (po or {}).get("orderNumber"),
                        (pdf or {}).get("purchase_order_number"),
                        "po_linked",
                    ),
                    hdr(
                        "Subtotal (ex tax)",
                        opt_money(detail.get("subtotal")),
                        None,
                        opt_money((pdf or {}).get("subtotal_ex_tax")),
                        "totals",
                    ),
                    hdr(
                        "Tax",
                        opt_money(detail.get("taxAmount")),
                        None,
                        opt_money((pdf or {}).get("tax_amount")),
                        "totals",
                    ),
                    hdr(
                        "Total incl tax",
                        opt_money(detail.get("total")),
                        None,
                        opt_money((pdf or {}).get("total_incl_tax")),
                        "pdf_total",
                    ),
                ],
            }
            # Line records are only worth reporting once line-level comparison
            # started (the PO was fetched) — before that every cell is "—" and
            # the reasons tell the whole story. Their absence is also the
            # playbook's rendering signal: lines present ⇒ full audit tables,
            # lines absent ⇒ reason bullets only.
            if po is not None:
                details["lines"] = [compact_line(rec) for rec in line_records]
            return {
                "invoice_id": inv_id,
                "reference_number": ref,
                "supplier_name": detail.get("supplierName"),
                "po_number": (po or {}).get("orderNumber") or po_number_hint,
                "total": money(total),
                "reasons": reasons,
                "fixes": list(fixes),
                "checklist": (
                    "All " + str(len(checklist_rows)) + " checks passed ✓"
                    if all(r["result"] == "✓" for r in checklist_rows)
                    else checklist_rows
                ),
                "details": details,
            }

        # Gates are evaluated in LAYERS. In BATCH mode they short-circuit: once a
        # layer fails, later layers are neither evaluated nor reported (and the
        # expensive PDF extraction is skipped) — the first failure is the whole
        # story for the auto-receive decision. In SINGLE-INVOICE review
        # (``only_invoice_id``, the editor) we instead run EVERY check we can, so
        # the user sees the full picture — a later line-vs-copy mismatch isn't
        # hidden behind an earlier totals failure. Data-dependent layers still
        # guard on their inputs (``po`` fetched, ``fileId`` present).
        run_all = bool(only_invoice_id)

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

        # Layer 3: must be automatched to a purchase order
        po_id = detail.get("linkedPurchaseOrderId")
        if not po_id:
            msg = "Not linked to a purchase order"
            if po_number_hint:
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
            _fail(checks, reasons, "po_linked", msg)
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
                        "summary": (
                            "Matched purchase order "
                            + str(order_no)
                            + " (already invoiced on another order — used to "
                            "validate, not re-linked in Loaded)"
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
        line_sum = sum((dec(ln.get("totalCost")) or D(0)) for ln in lines)
        if not close(line_sum, subtotal, totals_tol):
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
        pdf = extract_document(
            "loadedhub",
            "download_invoice_file",
            dict(base, file_id=detail["fileId"]),
            schema=PDF_SCHEMA,
            instructions=(
                "Extract every product line, every separate charge (freight "
                "etc.), and the totals from this supplier invoice.\n\n"
                "For each line also derive unit_of_measure — the unit ONE "
                "delivered item is used in for recipe costing. Rules:\n"
                "- It must be a weight, volume or count (never a length or a "
                "bare packaging word like pkt/box/carton/outer/unit).\n"
                "- Check the unit/size columns first; if unhelpful, look for "
                "a size in the description (e.g. '900ml', '500g', '4 Litre').\n"
                "- Multipacks ('2x5L', '5x3kg'): use the individual INNER item "
                "('5L', '3kg') — UNLESS the line was delivered as a whole "
                "OUTER/carton (its delivered quantity is in an OUTER/carton "
                "column, not the INNER column). Then the unit is the WHOLE "
                "pack, in the same 'NxM' form as the size ('5X3KG' → '5x3kg').\n"
                "- Random weight billed at a per-kg price (e.g. 14.96 kg at "
                "$20.56/kg — common for meat/seafood/produce): use 'Kilo', "
                "never the total weight.\n"
                "- Counted formats where the count matters: 'N piece' / "
                "'N pack' (e.g. '100 piece', '12 pack').\n"
                "- Keep the specific delivered size ('500g', '5L') — do NOT "
                "convert to a base unit.\n"
                "- Exactly 1 of a base unit drops the 1: '1kg' → 'Kilo', "
                "'1L' → 'Litre', '1 each' → 'each'. Use 'Kilo' and 'Litre' "
                "(not 'KG'/'L') for those base units.\n"
                "- If no confident unit can be derived, return null."
            ),
        ) if detail.get("fileId") else None
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

        if pdf:
            # The copy must be for THIS invoice (only fails on a live conflict —
            # a copy with no printed number is caught by the line-level checks)
            if (
                norm(pdf.get("invoice_number"))
                and norm(ref)
                and norm(pdf.get("invoice_number")) != norm(ref)
            ):
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
            # The copy bills non-product amounts (freight, surcharges) in a
            # separate charges[] array, not as product lines. A freight INVOICE
            # line therefore has to be reconciled against these, not pdf_lines —
            # otherwise the one freight amount is flagged twice (line "not found"
            # AND charge "no matching line").
            charges_unclaimed = [
                c for c in (pdf.get("charges") or [])
                if dec(c.get("amount_ex_tax")) not in (None, D(0))
            ]
            for ln in lines:
                if ln.get("id") in strike_ids:
                    # Redundant $0 duplicate (strike suggestion already emitted) —
                    # it isn't on the copy, so don't match it or flag it "not
                    # found". A $0 line changes no total, so pdf_ok / the subtotal
                    # check are untouched.
                    continue
                rec = rec_by_id.get(ln.get("id"))
                match = None
                for cand in unclaimed:
                    if norm(cand.get("code")) and norm(cand.get("code")) == norm(
                        ln.get("code")
                    ):
                        match = cand
                        break
                if match is None:
                    for cand in unclaimed:
                        if norm(cand.get("description")) and (
                            norm(cand.get("description")) in norm(ln.get("description"))
                            or norm(ln.get("description"))
                            in norm(cand.get("description"))
                        ):
                            match = cand
                            break
                if match is None:
                    # Not a product line on the copy — try the copy's CHARGES
                    # (freight etc.). A match reconciles the amount instead of
                    # double-flagging it as both a missing line and an orphan charge.
                    charge = None
                    ln_words = _words(ln.get("description"))
                    for c in charges_unclaimed:
                        if ln_words & _words(c.get("description")):
                            charge = c
                            break
                    if charge is not None:
                        charges_unclaimed.remove(charge)
                        if rec:
                            rec["on_copy"] = "✓"
                        inv_amt, chg_amt = dec(ln.get("totalCost")), dec(
                            charge.get("amount_ex_tax")
                        )
                        if (
                            inv_amt is not None
                            and chg_amt is not None
                            and not close(inv_amt, chg_amt, line_tol)
                        ):
                            pdf_ok = False
                            reasons.append(
                                "Line '"
                                + str(ln.get("description"))
                                + "' "
                                + money(inv_amt)
                                + " does not equal the document's "
                                + str(charge.get("description"))
                                + " "
                                + money(chg_amt)
                            )
                        continue
                    pdf_ok = False
                    if rec:
                        rec["on_copy"] = "✗"
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "' not found on the attached invoice document"
                    )
                    continue
                unclaimed.remove(match)
                copy_by_line_id[ln.get("id")] = {
                    "unit": match.get("unit"),
                    "quantity": match.get("quantity"),
                    "unit_price_ex_tax": match.get("unit_price_ex_tax"),
                    "line_total_ex_tax": match.get("line_total_ex_tax"),
                    "unit_of_measure": match.get("unit_of_measure"),
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
                # A multipack delivered unit (e.g. an OUTER '5x3kg') compares by
                # NAME — parse_unit can't compare 'NxM' and a ratio-equal but
                # differently-named unit ('15 KG') is a different pack. Simple
                # units compare by (type, magnitude) as before. uom_mismatch:
                # None = not comparable, False = matches, True = mismatch.
                uom_mismatch = None
                if derived and _is_multipack(derived):
                    uom_mismatch = norm(ln.get("unit")) != norm(derived)
                else:
                    pi, pd = parse_unit(ln.get("unit")), parse_unit(derived)
                    if pi and pd:
                        uom_mismatch = not (
                            pi[0] == pd[0] and abs(pi[1] - pd[1]) < 0.001
                        )
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
                elif rec:
                    rec["quantity"]["result"] = "✓"
                if dec(match.get("unit_price_ex_tax")) != dec(ln.get("unitCost")):
                    pdf_ok = False
                    if rec:
                        rec["unit_cost"]["result"] = "✗"
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "': unit cost "
                        + money(ln.get("unitCost"))
                        + " does not equal the document's unit price "
                        + money(match.get("unit_price_ex_tax"))
                    )
                elif rec:
                    rec["unit_cost"]["result"] = "✓"
                if not close(
                    dec(match.get("line_total_ex_tax")),
                    dec(ln.get("totalCost")),
                    line_tol,
                ):
                    pdf_ok = False
                    if rec:
                        rec["line_total"]["result"] = "✗"
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "': line total "
                        + money(ln.get("totalCost"))
                        + " does not equal the document's line total "
                        + money(match.get("line_total_ex_tax"))
                    )
                elif rec:
                    rec["line_total"]["result"] = "✓"
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
            # Only charges NOT reconciled to an invoice line above remain.
            for charge in charges_unclaimed:
                amt = dec(charge.get("amount_ex_tax"))
                if amt and amt != D(0):
                    pdf_ok = False
                    line_records.append(
                        {
                            "line": str(charge.get("description"))
                            + " — charge on copy only",
                            "stock_item": "—",
                            "on_copy": "✗",
                            "line_total": {
                                "invoice": None,
                                "copy": money(amt),
                                "result": "✗",
                            },
                        }
                    )
                    reasons.append(
                        "Document includes charge '"
                        + str(charge.get("description"))
                        + "' "
                        + money(amt)
                        + " with no matching invoice line"
                    )
            checks["pdf_lines"] = "pass" if pdf_ok else "fail"
            # Only set when at least one line was confidently comparable;
            # otherwise the checklist honestly shows "—" (not checked).
            if uom_compared or not uom_ok:
                checks["unit_of_measure"] = "pass" if uom_ok else "fail"

            # Gate 11 (PDF side): document total vs invoice total
            if not close(dec(pdf.get("total_incl_tax")), total, totals_tol):
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

        verdict = verdict_now()

        if reasons:
            if fixes:
                card_once()
            skipped.append(verdict)
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
    # Flat list of every proposed fix across skipped invoices, each with a
    # stable id the interactive card selects by and the handler applies by.
    all_fixes = []
    for v in skipped:
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
