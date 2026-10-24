"""Norm's copy of an invoice that came with none (owner, 24 Oct 2026).

An Ordermentum order reaches Loaded as data — lines, prices, totals — with no
PDF, so wherever Norm shows an invoice's copy beside its lines (the receive
card, the daily unit check, the activity page) there was nothing to show. This
draws one from Loaded's own figures, laid out like the invoices Ordermentum
sends for its suppliers (Single O OMI89351, The Lott, 19 Oct 2026): a panel of
numbers and dates, who it is to and from, the items with quantity, price and
subtotal, and the totals.

It is never passed off as the supplier's invoice: every page says Norm rebuilt
it from the data, and it carries no Ordermentum or supplier branding. Like
Ordermentum's own invoice it has no unit column — the unit on a Loaded line is
the one Norm booked, not one the supplier printed. Nothing reads it back:
it is for people to look at, never a source for a review.
"""

from __future__ import annotations

import datetime as _dt
import pathlib

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

FONTS = pathlib.Path(__file__).resolve().parents[1] / "assets" / "fonts"

INK = (34, 34, 34)
SOFT = (85, 85, 85)
MUTED = (138, 138, 138)
PANEL = (245, 245, 245)
STRIPE = (241, 242, 244)
RULE = (90, 90, 90)
NOTE_INK = (122, 92, 32)
NOTE_FILL = (255, 246, 226)
MARGIN = 14.0


def _date(value: object) -> str:
    """'2026-10-25…' → '25/10/2026', as Ordermentum prints it."""
    text = str(value or "")[:10]
    try:
        return _dt.date.fromisoformat(text).strftime("%d/%m/%Y")
    except ValueError:
        return ""


def _num(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def money(value: object, *, precise: bool = False) -> str:
    """'$1728.00' (Ordermentum prints no thousands separator). A price kept to
    a tenth of a cent in Loaded shows it (``precise``), so the copy never
    rounds a figure away."""
    f = _num(value)
    if f is None:
        return ""
    sign = "-" if f < 0 else ""
    f = abs(f)
    if precise and round(f, 3) != round(f, 2):
        return f"{sign}${f:.3f}"
    return f"{sign}${f:.2f}"


def quantity(value: object) -> str:
    f = _num(value)
    if f is None:
        return ""
    return f"{f:g}"


def lines_of(detail: dict) -> list[dict]:
    """The invoice's lines as the copy shows them, deleted ones left out."""
    out = []
    for ln in detail.get("lines") or []:
        if not isinstance(ln, dict) or ln.get("deletedAt"):
            continue
        qty = _num(ln.get("quantityReceived"))
        if qty is None:
            qty = _num(ln.get("quantityOrdered"))
        price = _num(ln.get("unitCostExclTax"))
        total = _num(ln.get("totalCostExclTax"))
        if total is None and qty is not None and price is not None:
            total = round(qty * price, 2)
        out.append(
            {
                "description": str(ln.get("description") or "").strip()
                or "(no description)",
                "code": str(ln.get("code") or "").strip(),
                "quantity": qty,
                "price": price,
                "total": total,
                "taxed": (_num(ln.get("taxAmount")) or 0) > 0,
            }
        )
    return out


def provenance(detail: dict) -> str:
    """What the copy was rebuilt from, said on every page."""
    if detail.get("_from_record"):
        return (
            "Rebuilt by Norm from Loaded's data as Norm last read it. The invoice is no "
            "longer in Loaded."
        )
    ordermentum = str(detail.get("source") or "").lower() == "ordermentum"
    said = (
        "Rebuilt by Norm from the order data Ordermentum sent."
        if ordermentum
        else "Rebuilt by Norm from this invoice's data in Loaded."
    )
    if detail.get("fileId"):
        return f"{said} The supplier's own copy is attached in Loaded."
    if ordermentum:
        return f"{said} The supplier sent no invoice copy."
    return f"{said} There is no invoice copy."


def detail_from_record(data: dict | None) -> dict | None:
    """A Loaded-shaped detail from Norm's own record of the invoice (the
    working document's ``loaded_snapshot``), for an invoice Loaded no longer
    returns — one deleted since, such as a duplicate removed (Sonoma
    OMI1112847, 24 Oct 2026)."""
    snap = (data or {}).get("loaded_snapshot")
    if not isinstance(snap, dict) or not isinstance(snap.get("header"), dict):
        return None
    h = snap["header"]
    lines = [
        {
            "description": ln.get("description") or ln.get("item_name"),
            "code": ln.get("code"),
            "quantityReceived": ln.get("quantity_received"),
            "quantityOrdered": ln.get("quantity_ordered"),
            "unitCostExclTax": ln.get("unit_cost"),
            "totalCostExclTax": ln.get("total_cost"),
            "taxAmount": ln.get("tax_amount"),
        }
        for ln in snap.get("lines") or []
        if isinstance(ln, dict)
    ]
    return {
        "id": (data or {}).get("invoice_id"),
        "referenceNumber": h.get("reference_number"),
        "issuedAt": h.get("issued_at"),
        "dueAt": h.get("due_at"),
        "receivedAt": h.get("received_at"),
        "subtotal": h.get("subtotal"),
        "discountAmount": h.get("discount_amount"),
        "taxAmount": h.get("tax_amount"),
        "total": h.get("total"),
        "supplierName": h.get("supplier_name"),
        "purchaseOrderNumber": h.get("purchase_order_number"),
        "lines": lines,
        "_from_record": True,
    }


class _Copy(FPDF):
    def __init__(self, footer_text: str):
        super().__init__(format="A4", unit="mm")
        self.footer_text = footer_text
        self.has_figtree = (FONTS / "Figtree-Medium-latin.ttf").exists()
        if self.has_figtree:
            self.add_font("Figtree", "", str(FONTS / "Figtree-Medium-latin.ttf"))
            self.add_font("Figtree", "B", str(FONTS / "Figtree-ExtraBold-latin.ttf"))
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(True, margin=20)

    def font(self, size: float, bold: bool = False, color=INK) -> None:
        self.set_font(
            "Figtree" if self.has_figtree else "Helvetica", "B" if bold else "", size
        )
        self.set_text_color(*color)

    def text_ok(self, text: str) -> str:
        """The built-in font holds Latin-1 only (used only if the bundled font
        is missing)."""
        if self.has_figtree:
            return text
        return text.encode("latin-1", "replace").decode("latin-1")

    def footer(self) -> None:
        self.set_y(-14)
        self.font(7.5, color=MUTED)
        self.cell(0, 5, self.text_ok(self.footer_text), align="L")
        self.set_x(MARGIN)
        self.cell(0, 5, f"Page {self.page_no()} of {{nb}}", align="R")


def render(detail: dict, *, venue_name: str | None = None) -> bytes:
    """The PDF for one Loaded invoice (its detail, as Loaded returns it)."""
    note = provenance(detail)
    pdf = _Copy(note)
    t = pdf.text_ok
    pdf.add_page()
    width = pdf.w - 2 * MARGIN
    credit = "credit" in str(detail.get("type") or "").lower()
    number = str(detail.get("referenceNumber") or "").strip()
    supplier = str(detail.get("supplierName") or "").strip() or "Supplier"

    # The heading, and the supplier where its logo would be.
    pdf.font(24)
    pdf.set_xy(MARGIN, MARGIN)
    pdf.cell(
        110,
        11,
        "Credit Note" if credit else "Tax Invoice",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.font(14, color=MUTED)
    pdf.cell(110, 8, t(number), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_fill_color(239, 234, 224)
    box_w, box_h = 44.0, 26.0
    pdf.set_xy(pdf.w - MARGIN - box_w, MARGIN)
    pdf.rect(pdf.w - MARGIN - box_w, MARGIN, box_w, box_h, style="F")
    pdf.font(10.5, bold=True, color=SOFT)
    pdf.set_xy(pdf.w - MARGIN - box_w + 3, MARGIN + 7)
    pdf.multi_cell(box_w - 6, 5, t(supplier), align="C", max_line_height=5)

    # What it was rebuilt from, where nobody can miss it.
    pdf.font(8, color=NOTE_INK)
    pdf.set_fill_color(*NOTE_FILL)
    pdf.set_xy(MARGIN, MARGIN + 22)
    pdf.cell(pdf.get_string_width(t(note)) + 6, 6, t(note), fill=True, align="C")

    # The numbers and dates, and who it is to and from.
    top = MARGIN + 34
    rows = [
        ("INVOICE NUMBER", number),
        ("INVOICE DATE", _date(detail.get("issuedAt"))),
        ("DUE DATE", _date(detail.get("dueAt"))),
        ("RECEIVED", _date(detail.get("receivedAt"))),
        ("SOURCE", str(detail.get("source") or "")),
    ]
    rows = [(k, v) for k, v in rows if v]
    panel_h = 8 + 6.5 * len(rows)
    pdf.set_fill_color(*PANEL)
    pdf.rect(0, top, MARGIN + 60, panel_h, style="F")
    y = top + 5
    for label, value in rows:
        pdf.set_xy(MARGIN, y)
        pdf.font(7.5, color=MUTED)
        label_w = pdf.get_string_width(label) + 2.5
        pdf.cell(label_w, 5, label)
        pdf.font(8.5)
        pdf.cell(0, 5, t(value))
        y += 6.5

    def party(x: float, heading: str, name: str, lines: list[str]) -> None:
        pdf.set_xy(x, top + 1)
        pdf.font(7.5, color=MUTED)
        pdf.cell(55, 5, heading, new_x=XPos.LEFT, new_y=YPos.NEXT)
        pdf.font(9.5)
        pdf.multi_cell(55, 5, t(name), align="L", new_x=XPos.LEFT, new_y=YPos.NEXT)
        pdf.font(8.5, color=SOFT)
        for text in lines:
            if text:
                pdf.multi_cell(
                    55, 5, t(text), align="L", new_x=XPos.LEFT, new_y=YPos.NEXT
                )

    customer = str(detail.get("billingName") or "").strip() or (venue_name or "")
    party(
        MARGIN + 68,
        "TO",
        customer or "—",
        [
            str(detail.get("billingAddress") or "").strip(),
            f"Deliver to: {venue_name}"
            if venue_name and venue_name != customer
            else "",
        ],
    )
    abn = str(detail.get("taxNumber") or "").strip()
    party(
        MARGIN + 126,
        "FROM",
        supplier,
        [
            str(detail.get("supplierAddress") or "").strip(),
            f"ABN: {abn}" if abn else "",
        ],
    )

    y = top + panel_h + 6
    po = str(detail.get("purchaseOrderNumber") or "").strip()
    if po:
        pdf.set_xy(MARGIN, y)
        pdf.font(8.5, color=MUTED)
        pdf.cell(60, 5, "PO NUMBER", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.font(9.5, color=SOFT)
        pdf.cell(60, 5, t(po))
        y += 13

    # The items: Ordermentum's columns, a star on each line that carries GST.
    items = lines_of(detail)
    pdf.set_draw_color(*RULE)
    pdf.set_line_width(0.3)
    pdf.line(MARGIN, y, MARGIN + width, y)
    pdf.set_xy(MARGIN, y)
    heading = FontFace(
        family="Figtree" if pdf.has_figtree else "Helvetica",
        emphasis="",
        size_pt=8,
        color=MUTED,
        fill_color=(255, 255, 255),
    )
    pdf.font(9.5, color=SOFT)
    with pdf.table(
        col_widths=(width - 70, 18, 24, 28),
        text_align=("LEFT", "RIGHT", "RIGHT", "RIGHT"),
        borders_layout="SINGLE_TOP_LINE",
        headings_style=heading,
        cell_fill_color=STRIPE,
        cell_fill_mode="EVEN_ROWS",
        line_height=6,
        padding=(1, 2),
    ) as table:
        head = table.row()
        for text in ("ITEM", "QTY", "PRICE", "SUBTOTAL"):
            head.cell(text)
        for it in items:
            row = table.row()
            name = ("* " if it["taxed"] else "") + it["description"]
            if it["code"]:
                name += f"  ·  {it['code']}"
            row.cell(t(name))
            row.cell(quantity(it["quantity"]))
            row.cell(money(it["price"], precise=True))
            row.cell(money(it["total"], precise=True))

    total_qty = sum(it["quantity"] or 0 for it in items)
    pdf.set_line_width(0.3)
    pdf.line(MARGIN, pdf.get_y(), MARGIN + width, pdf.get_y())
    pdf.set_x(MARGIN)
    pdf.font(8, color=MUTED)
    pdf.cell(22, 7, "ITEM TOTAL")
    pdf.font(9.5, color=SOFT)
    pdf.cell(14, 7, str(len(items)))
    pdf.font(8, color=MUTED)
    pdf.cell(30, 7, "TOTAL QUANTITY")
    pdf.font(9.5, color=SOFT)
    pdf.cell(20, 7, quantity(total_qty), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_line_width(0.6)
    pdf.line(MARGIN, pdf.get_y(), MARGIN + width, pdf.get_y())

    # The totals panel.
    subtotal = _num(detail.get("subtotal"))
    if subtotal is None:
        subtotal = round(sum(it["total"] or 0 for it in items), 2)
    totals = [("Subtotal", subtotal)]
    discount = _num(detail.get("discountAmount"))
    if discount:
        totals.append(("Discount", -abs(discount)))
    totals.append(("*GST", _num(detail.get("taxAmount")) or 0))
    totals.append(("Total", _num(detail.get("total"))))
    block_h = 9.5 * len(totals) + 6
    if pdf.get_y() + block_h + 4 > pdf.h - 20:
        pdf.add_page()
    y = pdf.get_y() + 2
    x = pdf.w - MARGIN - 64
    pdf.set_fill_color(*PANEL)
    pdf.rect(x, y, pdf.w - x, block_h, style="F")
    y += 3
    for label, value in totals:
        pdf.set_xy(x + 5, y)
        pdf.font(10, color=MUTED)
        pdf.cell(28, 9.5, label)
        pdf.font(11.5, bold=label == "Total")
        pdf.cell(26, 9.5, money(value), align="R")
        y += 9.5

    return bytes(pdf.output())
