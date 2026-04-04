"""Convert assistant message content (markdown + display blocks) to email-safe HTML."""

import logging

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
) -> str:
    """Convert markdown text + display blocks into email-safe HTML.

    Returns the inner content HTML (not wrapped in a full template).
    """
    parts: list[str] = []

    # Convert markdown to HTML
    if markdown_text:
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
