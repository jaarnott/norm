"""Email HTML made safe to show, and the text/HTML conversions mail needs.

Two directions, two allowlists:

* **Incoming bodies** (``safe_html``) come from anyone on the internet and are
  shown to staff inside Norm. They keep their layout — tables, inline styles,
  images — but lose everything that runs or reaches out on its own: scripts,
  event handlers, ``<style>`` blocks (CSS there can't be filtered property by
  property), forms, frames, objects, ``javascript:`` and ``data:`` URLs, and
  any CSS property that can load a URL (``background``, ``background-image``…).
  The Inbox renders the result in a sandboxed iframe as well; this is the
  layer that doesn't depend on the browser.
* **Outgoing bodies** (``clean_outbound_html``) are typed by staff or written
  by the agent. Same rules, so a paste from Word or a model's markup can't
  carry a script into a customer's inbox under the venue's name.

Proposal HTML is a different job with stricter rules (no external requests
at all) and lives in ``services/functions/sanitize.py``.
"""

from __future__ import annotations

import html as _html
import re

import nh3

_SAFE_CSS = frozenset(
    {
        "color",
        "background-color",
        "font",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "font-variant",
        "text-align",
        "text-decoration",
        "text-indent",
        "text-transform",
        "line-height",
        "letter-spacing",
        "word-spacing",
        "white-space",
        "vertical-align",
        "direction",
        "display",
        "width",
        "min-width",
        "max-width",
        "height",
        "min-height",
        "max-height",
        "margin",
        "margin-top",
        "margin-right",
        "margin-bottom",
        "margin-left",
        "padding",
        "padding-top",
        "padding-right",
        "padding-bottom",
        "padding-left",
        "border",
        "border-top",
        "border-right",
        "border-bottom",
        "border-left",
        "border-color",
        "border-style",
        "border-width",
        "border-radius",
        "border-collapse",
        "border-spacing",
        "list-style-type",
        "table-layout",
    }
)

_GENERIC_ATTRS = {
    "style",
    "dir",
    "lang",
    "title",
    "align",
    "valign",
    "width",
    "height",
    "bgcolor",
    "border",
    "cellpadding",
    "cellspacing",
    "colspan",
    "rowspan",
}

_TAGS = set(nh3.ALLOWED_TAGS) | {"font"}
_ATTRS = {
    "*": _GENERIC_ATTRS,
    "a": {"href", "title", "name"},
    "img": {"src", "alt", "width", "height"},
    "font": {"color", "face", "size"},
    "td": {"nowrap"},
    "th": {"nowrap", "scope"},
    "ol": {"start", "type"},
    "li": {"value"},
    "blockquote": {"cite"},
}
#: Gmail's own quote wrapper and our signature wrapper keep their classes so
#: a draft round-tripped through Gmail can be split back into its parts.
_CLASSES = {"div": {"gmail_quote", "norm-signature", "norm-quote"}}


#: Bodies are cut to this before any processing: a real email's HTML is far
#: smaller, and parsing time grows with an attacker's padding.
MAX_INPUT = 1_000_000
#: …and to this for plain text (the price check, the text alternative, the
#: agent's reading) — plenty of words, and bounded work.
MAX_TEXT_INPUT = 300_000
#: More "<" than a customer's email carries (Gmail and Outlook mail has a
#: few hundred tags; only a long newsletter nears this): such a body is not
#: parsed — parsing costs per bracket — but stripped to text in one linear
#: pass and shown as text.
MAX_BRACKETS = 10_000
#: Deeper block nesting than any real email (templates nest ~10–40): the
#: HTML parser scans its whole stack of open elements for each block tag,
#: so 30,000 nested <div>s took 11 seconds. Such a body is treated as text.
MAX_DEPTH = 128
#: Block elements that stay open inside each other. Tags the parser closes
#: implicitly (p, li, td, tr, dd, dt, option) are left out, so sloppy but real
#: HTML that never closes its paragraphs doesn't read as deep.
_NESTING = frozenset(
    {
        "div",
        "blockquote",
        "ul",
        "ol",
        "dl",
        "menu",
        "dir",
        "table",
        "section",
        "article",
        "aside",
        "header",
        "footer",
        "nav",
        "main",
        "form",
        "fieldset",
        "figure",
        "details",
        "address",
        "center",
        "pre",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)
_TAG_NAME = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]{0,15})")


def _unparseable(value: str) -> bool:
    """Too many brackets, or nested too deep, to hand to the parser — one
    linear pass (the name pattern is bounded, so it can't backtrack)."""
    if value.count("<") > MAX_BRACKETS:
        return True
    depth = 0
    for m in _TAG_NAME.finditer(value):
        if m.group(2).lower() not in _NESTING:
            continue
        if m.group(1):
            depth = max(0, depth - 1)
        else:
            depth += 1
            if depth > MAX_DEPTH:
                return True
    return False


def safe_html(value: str | None) -> str:
    """An incoming email body, safe to render inside Norm."""
    if not value:
        return ""
    value = value[:MAX_INPUT]
    if _unparseable(value):
        return text_to_html(_strip_tags(value))
    return nh3.clean(
        value,
        tags=_TAGS,
        clean_content_tags={"script", "style", "title", "head"},
        attributes=_ATTRS,
        url_schemes={"http", "https", "mailto", "tel", "cid"},
        link_rel="noopener noreferrer nofollow",
        filter_style_properties=_SAFE_CSS,
        allowed_classes=_CLASSES,
        strip_comments=True,
    )


def clean_outbound_html(value: str | None) -> str:
    """A body staff or the agent wrote, cleaned before it is sent."""
    if not value:
        return ""
    value = value[:MAX_INPUT]
    if _unparseable(value):
        return text_to_html(_strip_tags(value))
    return nh3.clean(
        value,
        tags=_TAGS,
        clean_content_tags={"script", "style", "title", "head"},
        attributes=_ATTRS,
        url_schemes={"http", "https", "mailto", "tel"},
        link_rel=None,
        filter_style_properties=_SAFE_CSS,
        allowed_classes=_CLASSES,
        strip_comments=True,
    )


#: The tags whose boundaries become line breaks in plain text; everything
#: else is unwrapped to its text.
_TEXT_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "ul",
    "ol",
    "tr",
    "td",
    "th",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "table",
    "section",
    "article",
    "span",
    "a",
    "b",
    "strong",
    "i",
    "em",
}
# These run only on nh3's output, which is well-formed: every "<" opens a
# tag that closes, and a "<" in text arrives as "&lt;". On raw mail they
# would go quadratic (many unclosed "<script>" or "<" without ">").
_BLOCK_END = re.compile(
    r"</(p|div|ul|ol|tr|h[1-6]|blockquote|table|section|article)>", re.I
)
_BR = re.compile(r"<br\s*/?>", re.I)
_LI = re.compile(r"<li>", re.I)
_TAG = re.compile(r"<[^<>]*>")
_SPACES = re.compile(r"[ \t]+")


def _strip_tags(value: str) -> str:
    """Absurd markup as text, in one linear pass: tags out (``<[^<>]*>``
    can't backtrack across a "<"), the common entities decoded."""
    return _unescape_serialised(_TAG.sub(" ", value))


def _unescape_serialised(text: str) -> str:
    """Undo nh3's serialiser, which writes exactly these entities in text
    (``&`` last, so a literal "&lt;" in the mail — serialised "&amp;lt;" —
    comes back as "&lt;"). ``html.unescape`` would cost a Python call per
    entity: a body of 200,000 "<" took half a second."""
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&nbsp;", " ")
        .replace("\xa0", " ")
        .replace("&amp;", "&")
    )


def html_to_text(value: str | None) -> str:
    """Readable plain text from HTML — the text/plain alternative of what we
    send, the input to the price check, and the agent's view of a body.

    Linear in the input: nh3 (an HTML parser, not a regex) first drops
    scripts, styles and comments and reduces the markup to a few well-formed
    tags; only then do the line-break substitutions run."""
    if not value:
        return ""
    value = value[:MAX_TEXT_INPUT]
    if _unparseable(value):
        text = _strip_tags(value)
    else:
        text = nh3.clean(
            value,
            tags=_TEXT_TAGS,
            attributes={},
            clean_content_tags={"script", "style", "head", "title"},
            link_rel=None,
            strip_comments=True,
        )
        text = _BR.sub("\n", text)
        text = _LI.sub("\n- ", text)
        text = _BLOCK_END.sub("\n", text)
        text = _TAG.sub("", text)
        text = _unescape_serialised(text)
    out: list[str] = []
    for line in _SPACES.sub(" ", text).splitlines():
        line = line.strip()
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def text_to_html(value: str | None) -> str:
    """Plain text as HTML paragraphs (escaped)."""
    if not value:
        return ""
    # Linear: a match consumes the whole whitespace run it starts.
    paragraphs = re.split(r"\n\s*\n", value[:MAX_INPUT].strip())
    return "".join(
        "<p>" + _html.escape(p).replace("\n", "<br>") + "</p>" for p in paragraphs
    )


#: A "<", a letter, then at most a bounded run before ">" — the bound keeps
#: the check linear on text full of "<a" with no ">".
_LOOKS_LIKE_TAG = re.compile(r"<[A-Za-z][^<>]{0,1000}>")


def looks_like_html(value: str | None) -> bool:
    return bool(value) and bool(_LOOKS_LIKE_TAG.search(value[:MAX_INPUT]))
