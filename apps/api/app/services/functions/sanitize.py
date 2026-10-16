"""Proposal HTML and CSS — written freely by a person or the agent, made safe
to show a customer.

Proposals are free HTML + CSS (owner's decision: an LLM should be able to
design whatever suits the event), and they are shown on a public page to
someone who never signed in. Two independent layers keep that safe:

1. **This module**, on every save (the proposal service) and again whenever a
   public page serves one — so HTML that reached the table some other way (the
   importer, an older save) is cleaned on the way out too.
2. **The page renders it in a sandboxed iframe with a strict CSP**
   (``proposal_document`` builds that document, CSP included), so even
   something this module missed cannot run script or reach another server.

HTML (nh3 — html5ever + ammonia — with an explicit allowlist):

* Rich layout survives: sections, headings, paragraphs, lists, tables,
  figures, images, links, inline emphasis, ``class`` / ``id`` / ``style``.
* Gone with their content: ``script``, ``style`` (its CSS is first lifted into
  the stylesheet, so an LLM's ``<style>`` block still styles the page),
  ``title``, ``noscript``, ``template``, ``iframe``, ``object``, ``embed``,
  ``svg``, ``math``. Gone, keeping their text: every other tag — ``form``,
  ``input``, ``button``, ``link``, ``meta``, ``base``, ``video`` ...
* No event handlers (``on*``) or any attribute not on the list.
* ``<a href>``: ``http(s)``, ``mailto``, ``tel`` or an in-page ``#anchor``;
  ``rel="noopener noreferrer"`` is added.
* ``<img src>``: only Norm's own public files, ``/api/public/files/<id>``
  (an absolute URL on Norm's own origin is rewritten to that path). Nothing
  else — no remote images, no ``data:`` URLs, no ``srcset``.
* ``style`` attributes go through the CSS declaration rules below.

CSS (tinycss2, token by token, so comments and escapes can't hide anything):

* Kept at-rules: ``@media``, ``@supports``, ``@container``, ``@layer``,
  ``@page``, ``@keyframes``. Dropped: ``@import``, ``@font-face`` (no remote
  fonts — font-family NAMES are fine), ``@namespace``, ``@charset`` and
  anything else.
* A declaration is dropped when its property is ``behavior`` /
  ``-moz-binding``, when it holds a parse error (a bad ``url(``), a function
  outside the allowlist (``expression()``, ``image-set()``, ``attr()``,
  ``element()`` ...), or a ``url()`` that is not ``/api/public/files/<id>``.
* ``<`` is written as the CSS escape ``\\3c `` so the stylesheet can never
  close the ``<style>`` element it is embedded in.

``sanitize_proposal(html, css) -> (html, css)`` is the entry point; the
returned CSS includes any ``<style>`` blocks lifted out of the HTML.
"""

from __future__ import annotations

import html as html_lib
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

import nh3
import tinycss2
from tinycss2.ast import URLToken

from app.config import settings
from app.services.functions.errors import Invalid

#: The one public path for Functions images (served by routers/public.py,
#: by ``fn_files.id``). Use ``public_file_url`` rather than formatting it.
PUBLIC_FILE_PATH = "/api/public/files/"
#: Web fonts Norm hosts itself (apps/web/public/fonts/brand, owner 16 Oct
#: 2026): a proposal names a family and Norm's stylesheet supplies it from
#: Norm's origin — no customer's browser calls Google. Remote ``@font-face``
#: and ``@import`` are still dropped.
HOSTED_FONTS_PATH = "/fonts/brand/"
HOSTED_FONTS = (
    "Inter",
    "Playfair Display",
    "Cormorant Garamond",
    "Libre Baskerville",
    "DM Serif Display",
    "Montserrat",
    "Poppins",
    "Bebas Neue",
    "JetBrains Mono",
    "Courier Prime",
)
_FILE_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
_FILE_REF = re.compile(re.escape(PUBLIC_FILE_PATH) + r"([A-Za-z0-9-]{1,64})")

#: A proposal is a page, not an archive: refuse absurd sizes instead of
#: spending a request sanitising them.
MAX_HTML_CHARS = 1_000_000
MAX_CSS_CHARS = 300_000
#: The HTML parser under nh3 rescans its stack of open elements for each
#: block tag, so nesting costs quadratically: 30,000 nested <div>s took ~11 s
#: (found by F4, whose mailbox display has the same guard). A proposal past
#: these is refused BEFORE parsing, by one linear scan — a designed page has
#: a few thousand tags and nests a few dozen deep.
MAX_TAGS = 20_000
MAX_DEPTH = 128
#: Elements that stay open inside each other. Those the parser closes on its
#: own (p, li, td, tr, dd, dt) are left out, so a page that never closes its
#: paragraphs doesn't read as deep.
_NESTING = frozenset(
    {
        "div",
        "blockquote",
        "ul",
        "ol",
        "dl",
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
        "span",
        "a",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "s",
        "small",
        "font",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)
_TAG_NAME = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]{0,15})")

TAGS = frozenset(
    {
        # structure
        "div",
        "section",
        "article",
        "header",
        "footer",
        "main",
        "aside",
        "nav",
        "address",
        "figure",
        "figcaption",
        "picture",
        "center",
        # text
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "span",
        "br",
        "hr",
        "wbr",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "s",
        "small",
        "sub",
        "sup",
        "mark",
        "abbr",
        "time",
        "blockquote",
        "q",
        "cite",
        "code",
        "pre",
        "del",
        "ins",
        # lists
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        # tables
        "table",
        "caption",
        "colgroup",
        "col",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        # media and links
        "img",
        "a",
    }
)
#: Removed together with everything inside them (the rest of the unlisted
#: tags are unwrapped, keeping their text).
CLEAN_CONTENT_TAGS = frozenset(
    {
        "script",
        "style",
        "title",
        "noscript",
        "template",
        "iframe",
        "object",
        "embed",
        "svg",
        "math",
    }
)
_GLOBAL_ATTRIBUTES = {"class", "id", "style", "title", "lang", "dir", "role"}
_CELL = {
    "colspan",
    "rowspan",
    "headers",
    "scope",
    "align",
    "valign",
    "width",
    "height",
    "bgcolor",
    "nowrap",
}
ATTRIBUTES = {
    "*": _GLOBAL_ATTRIBUTES,
    "a": {"href", "hreflang"},
    "img": {"src", "alt", "width", "height", "loading"},
    "table": {"width", "border", "cellpadding", "cellspacing", "align", "bgcolor"},
    "td": _CELL,
    "th": _CELL,
    "tr": {"align", "valign", "bgcolor"},
    "col": {"span", "width", "align", "valign"},
    "colgroup": {"span", "width", "align", "valign"},
    "ol": {"start", "type", "reversed"},
    "ul": {"type"},
    "li": {"value"},
    "time": {"datetime"},
    "hr": {"width", "size", "align"},
}
_LINK_SCHEMES = {"http", "https", "mailto", "tel"}

_DENIED_PROPERTIES = frozenset({"behavior", "-ms-behavior", "-moz-binding"})
#: CSS functions a declaration may use. Anything else drops the declaration —
#: an allowlist, so a function a browser adds next year starts out refused.
#: (No ``attr()``: typed attr() can turn an attribute into a URL.)
_ALLOWED_FUNCTIONS = frozenset(
    {
        # maths and variables
        "calc",
        "min",
        "max",
        "clamp",
        "round",
        "mod",
        "rem",
        "abs",
        "sign",
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
        "atan2",
        "pow",
        "sqrt",
        "hypot",
        "log",
        "exp",
        "var",
        "env",
        # colour
        "rgb",
        "rgba",
        "hsl",
        "hsla",
        "hwb",
        "lab",
        "lch",
        "oklab",
        "oklch",
        "color",
        "color-mix",
        "light-dark",
        # gradients
        "linear-gradient",
        "radial-gradient",
        "conic-gradient",
        "repeating-linear-gradient",
        "repeating-radial-gradient",
        "repeating-conic-gradient",
        # transforms and easing
        "translate",
        "translatex",
        "translatey",
        "translatez",
        "translate3d",
        "rotate",
        "rotatex",
        "rotatey",
        "rotatez",
        "rotate3d",
        "scale",
        "scalex",
        "scaley",
        "scalez",
        "scale3d",
        "skew",
        "skewx",
        "skewy",
        "matrix",
        "matrix3d",
        "perspective",
        "cubic-bezier",
        "steps",
        "linear",
        # filters
        "blur",
        "brightness",
        "contrast",
        "drop-shadow",
        "grayscale",
        "hue-rotate",
        "invert",
        "opacity",
        "saturate",
        "sepia",
        # layout
        "minmax",
        "repeat",
        "fit-content",
        "inset",
        "circle",
        "ellipse",
        "polygon",
        "counter",
        "counters",
    }
)
#: At-rules whose block holds rules (recursed) / declarations / nothing.
_RULE_BLOCK_AT_RULES = frozenset(
    {"media", "supports", "container", "layer", "keyframes", "-webkit-keyframes"}
)
_DECLARATION_AT_RULES = frozenset({"page"})
_STATEMENT_AT_RULES = frozenset({"layer"})  # "@layer a, b;"


# ---------------------------------------------------------------------------
# Public file URLs
# ---------------------------------------------------------------------------


def public_file_url(file_id: str) -> str:
    """The path a proposal (or a public page) uses for an ``fn_files`` image."""
    if not _FILE_ID.fullmatch(file_id or ""):
        raise Invalid("Not a file id.")
    return f"{PUBLIC_FILE_PATH}{file_id}"


def _own_origin() -> str:
    parts = urlsplit(settings.app_url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def file_id_from_url(url: str | None) -> str | None:
    """The file id when ``url`` is one of Norm's public file URLs — the path
    itself, or an absolute URL on Norm's own origin — else None."""
    value = (url or "").strip()
    if not value:
        return None
    if not value.startswith("/"):
        parts = urlsplit(value)
        if f"{parts.scheme}://{parts.netloc}".lower() != _own_origin():
            return None
        if parts.query or parts.fragment:
            return None
        value = parts.path
    if not value.startswith(PUBLIC_FILE_PATH) or value.startswith("//"):
        return None
    file_id = value[len(PUBLIC_FILE_PATH) :]
    return file_id if _FILE_ID.fullmatch(file_id) else None


def referenced_file_ids(*texts: str | None) -> set[str]:
    """Every file id a proposal's HTML or CSS points at (for the public files
    route, which serves only images something public references)."""
    found: set[str] = set()
    for text in texts:
        found.update(_FILE_REF.findall(text or ""))
    return found


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------


def _clean_value(tokens) -> list | None:
    """The value's tokens with every allowed ``url()`` rewritten to the
    canonical public path, or None when the declaration must go."""
    out = []
    for tok in tokens:
        kind = tok.type
        if kind == "error":
            return None  # bad url / bad string / unmatched bracket
        if kind == "url":
            file_id = file_id_from_url(tok.value)
            if file_id is None:
                return None
            out.append(_url_token(tok, file_id))
        elif kind == "function":
            if tok.lower_name == "url":
                args = [a for a in tok.arguments if a.type not in ("whitespace",)]
                if len(args) != 1 or args[0].type != "string":
                    return None
                file_id = file_id_from_url(args[0].value)
                if file_id is None:
                    return None
                out.append(_url_token(tok, file_id))
                continue
            if tok.lower_name not in _ALLOWED_FUNCTIONS:
                return None
            if _clean_value(tok.arguments) is None:
                return None
            out.append(tok)
        elif kind in ("() block", "[] block", "{} block"):
            if _clean_value(tok.content) is None:
                return None
            out.append(tok)
        else:
            out.append(tok)
    return out


def _url_token(tok, file_id: str) -> URLToken:
    path = f"{PUBLIC_FILE_PATH}{file_id}"
    return URLToken(tok.source_line, tok.source_column, path, f"url({path})")


def _strip_ws(tokens: list) -> list:
    start, end = 0, len(tokens)
    while start < end and tokens[start].type == "whitespace":
        start += 1
    while end > start and tokens[end - 1].type == "whitespace":
        end -= 1
    return tokens[start:end]


def _clean_declaration(decl) -> str | None:
    if decl.lower_name in _DENIED_PROPERTIES:
        return None
    value = _clean_value(decl.value)
    if value is None:
        return None
    value = _strip_ws(value)
    if not value:
        return None
    important = " !important" if decl.important else ""
    return f"{decl.name}: {tinycss2.serialize(value)}{important}"


def _clean_block(content, *, allow_rules: bool) -> list[str]:
    """Declarations (and, inside a style rule or a rule-holding at-rule,
    nested rules — CSS nesting) of one ``{}`` block, cleaned."""
    out = []
    for node in tinycss2.parse_blocks_contents(
        content, skip_comments=True, skip_whitespace=True
    ):
        if node.type == "declaration":
            cleaned = _clean_declaration(node)
            if cleaned:
                out.append(cleaned + ";")
        elif allow_rules and node.type in ("qualified-rule", "at-rule"):
            cleaned = _clean_rule(node)
            if cleaned:
                out.append(cleaned)
    return out


def _prelude_ok(prelude) -> bool:
    """A selector or at-rule prelude can't fetch anything, but one carrying a
    parse error or a URL is not something a designer wrote."""

    def walk(tokens) -> bool:
        for tok in tokens:
            if tok.type in ("error", "url"):
                return False
            if tok.type == "function":
                if tok.lower_name == "url" or not walk(tok.arguments):
                    return False
            elif tok.type in ("() block", "[] block", "{} block"):
                if not walk(tok.content):
                    return False
        return True

    return walk(prelude or [])


def _clean_rule(node) -> str | None:
    if node.type == "qualified-rule":
        if not _prelude_ok(node.prelude):
            return None
        body = _clean_block(node.content, allow_rules=True)
        selector = tinycss2.serialize(_strip_ws(list(node.prelude))).strip()
        if not selector or not body:
            return None
        return f"{selector} {{ {' '.join(body)} }}"
    if node.type != "at-rule":
        return None
    name = node.lower_at_keyword
    if not _prelude_ok(node.prelude):
        return None
    prelude = tinycss2.serialize(_strip_ws(list(node.prelude or []))).strip()
    head = f"@{name} {prelude}".rstrip()
    if node.content is None:
        return f"{head};" if name in _STATEMENT_AT_RULES and prelude else None
    if name in _RULE_BLOCK_AT_RULES:
        body = [
            r
            for r in (
                _clean_rule(n)
                for n in tinycss2.parse_rule_list(
                    node.content, skip_comments=True, skip_whitespace=True
                )
            )
            if r
        ]
    elif name in _DECLARATION_AT_RULES:
        body = _clean_block(node.content, allow_rules=False)
    else:
        return None  # @import, @font-face, @namespace, @charset, unknown
    if not body:
        return None
    return f"{head} {{ {' '.join(body)} }}"


def _html_safe_css(css: str) -> str:
    # The stylesheet ends up inside a <style> element: no "<" may reach it,
    # or "</style>" in a string would end the element. "\3c " is the same
    # character to CSS.
    return css.replace("<", "\\3c ")


def sanitize_css(css: str | None) -> str:
    """A stylesheet with only what the rules above allow, one rule per line."""
    text = css or ""
    if len(text) > MAX_CSS_CHARS:
        raise Invalid("The proposal's CSS is too large.")
    try:
        rules = [
            r
            for r in (
                _clean_rule(n)
                for n in tinycss2.parse_stylesheet(
                    text, skip_comments=True, skip_whitespace=True
                )
            )
            if r
        ]
    except RecursionError as exc:
        # Hundreds of nested brackets: no designer writes that.
        raise Invalid("The proposal's CSS is nested too deeply.") from exc
    return _html_safe_css("\n".join(rules))


def sanitize_style_attribute(value: str | None) -> str | None:
    """An inline ``style="..."``, declarations only, or None when nothing is
    left."""
    if not value:
        return None
    try:
        cleaned = [
            c
            for c in (
                _clean_declaration(n)
                for n in tinycss2.parse_blocks_contents(
                    value, skip_comments=True, skip_whitespace=True
                )
                if n.type == "declaration"
            )
            if c
        ]
    except RecursionError:
        return None  # absurdly nested: drop the attribute
    return "; ".join(cleaned) or None


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


class _StyleBlocks(HTMLParser):
    """Collects the text of every ``<style>`` element (the parser treats
    their content as raw text, as a browser does)."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.blocks: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "style":
            self._depth += 1
            self.blocks.append("")

    def handle_endtag(self, tag):
        if tag == "style" and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if self._depth:
            self.blocks[-1] += data


def _style_blocks(html: str) -> list[str]:
    parser = _StyleBlocks()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — lifting CSS is a nicety, never a failure
        return parser.blocks
    return parser.blocks


def _too_deep(html: str) -> bool:
    """Too many tags, or nested too deep, to hand to the parser — one linear
    pass (the tag-name pattern is bounded, so it can't backtrack)."""
    if html.count("<") > MAX_TAGS:
        return True
    depth = 0
    for match in _TAG_NAME.finditer(html):
        if match.group(2).lower() not in _NESTING:
            continue
        if match.group(1):
            depth = max(0, depth - 1)
        else:
            depth += 1
            if depth > MAX_DEPTH:
                return True
    return False


def _attribute_filter(tag: str, attr: str, value: str) -> str | None:
    """nh3 has already dropped unlisted attributes and URL schemes outside
    ``_LINK_SCHEMES``; this applies the rules a scheme list can't express."""
    if attr == "style":
        return sanitize_style_attribute(value)
    if tag == "img" and attr == "src":
        file_id = file_id_from_url(value)
        return f"{PUBLIC_FILE_PATH}{file_id}" if file_id else None
    if tag == "a" and attr == "href":
        href = value.strip()
        if href.startswith("#"):
            return href
        scheme = urlsplit(href).scheme.lower()
        return href if scheme in _LINK_SCHEMES else None  # no relative links
    return value


def sanitize_html(html: str | None) -> tuple[str, list[str]]:
    """``(clean_html, style_blocks)`` — the HTML through the allowlist, and
    the raw CSS of any ``<style>`` elements it held (for ``sanitize_css``)."""
    text = html or ""
    if len(text) > MAX_HTML_CHARS:
        raise Invalid("The proposal is too large.")
    if _too_deep(text):
        raise Invalid("This proposal is too deeply nested to save — simplify the HTML.")
    blocks = _style_blocks(text) if "<style" in text.lower() else []
    clean = nh3.clean(
        text,
        tags=set(TAGS),
        clean_content_tags=set(CLEAN_CONTENT_TAGS),
        attributes={k: set(v) for k, v in ATTRIBUTES.items()},
        attribute_filter=_attribute_filter,
        url_schemes=set(_LINK_SCHEMES),
        strip_comments=True,
        link_rel="noopener noreferrer",
    )
    return clean, blocks


def sanitize_proposal(html: str | None, css: str | None) -> tuple[str, str]:
    """Clean a proposal's HTML and CSS. ``<style>`` blocks in the HTML move to
    the end of the returned CSS (cleaned like the rest), so what is stored is
    one HTML fragment plus one stylesheet."""
    clean_html, blocks = sanitize_html(html)
    sheet = "\n".join([css or "", *blocks]) if blocks else (css or "")
    return clean_html, sanitize_css(sheet)


def proposal_document(html: str, css: str, *, title: str = "Proposal") -> str:
    """The proposal as a complete document for a sandboxed iframe's
    ``srcdoc`` — the second layer. It sanitises again (cheap, and it means a
    caller can't hand it raw HTML) and carries its own CSP: no script, no
    connections, images only from Norm's public files, fonts only from Norm's
    hosted set. Links open in a new tab (the iframe needs ``allow-popups
    allow-popups-to-escape-sandbox`` for that, and nothing else)."""
    clean_html, clean_css = sanitize_proposal(html, css)
    origin = _own_origin()
    fonts = f"{origin}{HOSTED_FONTS_PATH}"
    csp = (
        "default-src 'none'; "
        f"img-src {origin}{PUBLIC_FILE_PATH}; "
        f"style-src 'unsafe-inline' {fonts}; "
        f"font-src {fonts}; "
        "base-uri 'none'; form-action 'none'"
    )
    return (
        "<!doctype html>\n<html><head>"
        '<meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        '<meta name="referrer" content="no-referrer">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<base target="_blank">'
        f"<title>{html_lib.escape(title)}</title>"
        f'<link rel="stylesheet" href="{fonts}fonts.css">'
        f"<style>{clean_css}</style>"
        f"</head><body>{clean_html}</body></html>"
    )
