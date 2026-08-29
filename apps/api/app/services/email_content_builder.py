"""Convert assistant message content (markdown + display blocks) to email-safe HTML."""

import logging
import re

import markdown as md

logger = logging.getLogger(__name__)

# Email-safe table styles (inline CSS for email clients)
_TABLE_STYLE = (
    'style="border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 14px;"'
)
_TH_STYLE = (
    'style="border: 1px solid #e2ddd7; padding: 8px 12px; text-align: left; '
    'background-color: #f5f3f0; font-weight: 600; color: #333;"'
)
_TD_STYLE = 'style="border: 1px solid #e2ddd7; padding: 8px 12px; color: #444;"'


def build_report_html(
    markdown_text: str,
    display_blocks: list[dict] | None = None,
    subject: str | None = None,
) -> str:
    """Convert markdown text + display blocks into email-safe HTML.

    Returns the inner content HTML (not wrapped in a full template).

    `subject` is what the email template renders as its own <h1>. Pass it so a
    body that opens by restating the subject does not title the email twice.
    """
    parts: list[str] = []

    # Convert markdown to HTML
    if markdown_text:
        markdown_text = _drop_duplicate_title(markdown_text, subject)
        html = md.markdown(
            markdown_text,
            extensions=["tables", "fenced_code", "nl2br"],
        )
        # Apply inline styles to markdown-generated tables
        html = _style_tables(html)
        parts.append(html)

    # Convert display blocks
    if display_blocks:
        for block in display_blocks:
            block_html = _render_display_block(block)
            if block_html:
                parts.append(block_html)

    return "\n".join(parts)


_MONTHS = (
    "january february march april may june july august september october "
    "november december jan feb mar apr jun jul aug sep sept oct nov dec"
).split()
# What may trail a title without changing which report it names: a date, in any
# phrasing ("2026-08-29", "29 August 2026", "29th of August").
_DATE_WORDS = frozenset(_MONTHS) | {"st", "nd", "rd", "th", "of"}


def _title_key(text: str) -> str:
    """Normalise a title for comparison: case, dashes and punctuation don't count."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _title_stem(key: str) -> str:
    """A normalised title with any trailing date dropped, whatever its format."""
    words = re.sub(r"\d+", " ", key).split()
    while words and words[-1] in _DATE_WORDS:
        words.pop()
    return " ".join(words)


def _drop_duplicate_title(markdown_text: str, subject: str | None) -> str:
    """Remove a leading level-1 heading that just restates the email's subject.

    `report.html` already renders the subject as the email's <h1>, so an LLM
    that opens its body with the same title — reliably, and with its own dash
    style — gives the reader the headline twice. Only an H1 is considered: `##`
    and below are the report's own sections.

    Matching ignores case, punctuation and dash style ("A - B" vs "A — B"), and
    tolerates the date being written differently on each side. It does not
    tolerate anything else: a heading that says something the subject does not
    is the author's, and stays.
    """
    if not subject:
        return markdown_text

    match = re.match(r"[ \t\r\n]*#[ \t]+(.+?)[ \t]*(?:\n|$)", markdown_text)
    if not match:
        return markdown_text

    heading_key, subject_key = _title_key(match.group(1)), _title_key(subject)
    if not heading_key or not subject_key:
        return markdown_text

    if heading_key != subject_key:
        heading_stem, subject_stem = _title_stem(heading_key), _title_stem(subject_key)
        # A stem of only a word or two is too thin to be sure it is the same
        # report rather than a coincidence.
        if len(subject_stem) < 12 or heading_stem != subject_stem:
            return markdown_text

    return markdown_text[match.end() :].lstrip("\n")


def _style_tables(html: str) -> str:
    """Apply inline styles to <table>, <th>, <td> tags for email clients."""
    html = html.replace("<table>", f"<table {_TABLE_STYLE}>")
    html = html.replace("<th>", f"<th {_TH_STYLE}>")
    html = html.replace("<th ", f"<th {_TH_STYLE} ")
    html = html.replace("<td>", f"<td {_TD_STYLE}>")
    html = html.replace("<td ", f"<td {_TD_STYLE} ")
    return html


def _render_display_block(block: dict) -> str | None:
    """Convert a display block to email HTML, or None if not convertible."""
    component = block.get("component", "")
    data = block.get("data", {})

    if component == "chart":
        return _render_chart_as_table(data, block.get("props", {}))
    if component in ("generic_table", "roster_table"):
        return _render_table_data(data)
    return None


def _render_chart_as_table(data: dict, props: dict) -> str | None:
    """Convert chart data to an HTML table."""
    rows = data.get("rows", [])
    if not rows or not isinstance(rows[0], dict):
        return None

    # Get column headers from first row
    headers = list(rows[0].keys())
    # Filter out internal fields
    headers = [h for h in headers if not h.startswith("_")]
    if not headers:
        return None

    # Use field_labels from props if available
    labels = props.get("field_labels") or {}
    title = props.get("title") or data.get("title")

    parts = []
    if title:
        parts.append(
            f'<p style="font-weight: 600; color: #333; margin: 1rem 0 0.5rem;">{title}</p>'
        )

    parts.append(f"<table {_TABLE_STYLE}>")
    parts.append("<thead><tr>")
    for h in headers:
        label = labels.get(h, h)
        parts.append(f"<th {_TH_STYLE}>{label}</th>")
    parts.append("</tr></thead>")

    parts.append("<tbody>")
    for row in rows:
        parts.append("<tr>")
        for h in headers:
            val = row.get(h, "")
            formatted = _format_cell(val)
            parts.append(f"<td {_TD_STYLE}>{formatted}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")

    return "\n".join(parts)


def _render_table_data(data: dict) -> str | None:
    """Convert generic table data to HTML."""
    rows = data if isinstance(data, list) else data.get("rows", data.get("data", []))
    if not rows or not isinstance(rows, list) or not isinstance(rows[0], dict):
        return None

    headers = [h for h in rows[0].keys() if not h.startswith("_")]
    if not headers:
        return None

    parts = [f"<table {_TABLE_STYLE}>"]
    parts.append("<thead><tr>")
    for h in headers:
        parts.append(f"<th {_TH_STYLE}>{h}</th>")
    parts.append("</tr></thead>")

    parts.append("<tbody>")
    for row in rows:
        parts.append("<tr>")
        for h in headers:
            val = row.get(h, "")
            parts.append(f"<td {_TD_STYLE}>{_format_cell(val)}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")

    return "\n".join(parts)


def _format_cell(val) -> str:
    """Format a cell value for display in an email table."""
    if val is None:
        return ""
    if isinstance(val, float):
        if val == int(val):
            return str(int(val))
        return f"{val:,.2f}"
    if isinstance(val, int):
        return f"{val:,}"
    return str(val)
