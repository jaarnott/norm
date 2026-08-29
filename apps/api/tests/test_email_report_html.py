"""The report email's body must not repeat the title the template already shows.

`report.html` renders the subject as the email's own <h1>. Every reconciliation
email sent on 28-29 Aug 2026 opened with TWO h1s — the template's, then the
model's own restatement of the same title in `content_markdown`, differing only
in dash style ("- 2026-08-29" vs "— 2026-08-29"). The subject is now passed to
the builder so the duplicate is dropped, and these tests hold that line without
letting it eat a heading that genuinely says something else.
"""

import re

from app.services.email_content_builder import build_report_html

SUBJECT = "Invoice Reconciliation (All Venues) - 2026-08-29"


def h1s(html: str) -> list[str]:
    return [
        re.sub(r"<[^>]+>", "", m).strip()
        for m in re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    ]


class TestTheBodyNeverTitlesTheEmailTwice:
    def test_the_production_case_em_dash_against_the_subject_s_hyphen(self):
        body = (
            "# Invoice Reconciliation (All Venues) — 2026-08-29\n\n"
            "8 of 19 invoices reconciled across six venues.\n"
        )
        html = build_report_html(body, None, subject=SUBJECT)
        assert h1s(html) == []
        assert "8 of 19 invoices reconciled" in html

    def test_a_heading_identical_to_the_subject_goes(self):
        html = build_report_html(f"# {SUBJECT}\n\nBody.\n", None, subject=SUBJECT)
        assert h1s(html) == []

    def test_the_same_title_dated_differently_still_goes(self):
        body = "# Invoice Reconciliation (All Venues) — 29th of August 2026\n\nBody.\n"
        assert h1s(build_report_html(body, None, subject=SUBJECT)) == []

    def test_the_title_without_any_date_still_goes(self):
        body = "# Invoice Reconciliation (All Venues)\n\nBody.\n"
        assert h1s(build_report_html(body, None, subject=SUBJECT)) == []

    def test_blank_lines_before_the_heading_do_not_hide_it(self):
        html = build_report_html(f"\n\n# {SUBJECT}\n\nBody.\n", None, subject=SUBJECT)
        assert h1s(html) == []


class TestWhatItMustNotEat:
    def test_a_heading_that_says_something_else_stays(self):
        body = "# Urgent: three invoices need a credit note\n\nBody.\n"
        html = build_report_html(body, None, subject=SUBJECT)
        assert h1s(html) == ["Urgent: three invoices need a credit note"]

    def test_the_subject_plus_a_real_clause_stays(self):
        """Only a trailing DATE may differ — an added clause is the author's."""
        body = "# Invoice Reconciliation (All Venues) failed at three venues\n\nB.\n"
        html = build_report_html(body, None, subject=SUBJECT)
        assert len(h1s(html)) == 1

    def test_a_level_two_heading_is_a_section_not_a_title(self):
        """`#` means "this is the document title"; `##` is a section — only the
        first is a duplicate of what the template already rendered."""
        body = f"## {SUBJECT}\n\nrows\n"
        html = build_report_html(body, None, subject=SUBJECT)
        assert SUBJECT in re.sub(r"<[^>]+>", "", html)

    def test_section_headings_are_untouched(self):
        body = "8 of 19 reconciled.\n\n## Needs a PO number added in Loaded\n\nrows\n"
        html = build_report_html(body, None, subject=SUBJECT)
        assert h1s(html) == []
        assert "Needs a PO number added in Loaded" in html

    def test_a_thin_subject_is_not_matched_on_its_words_alone(self):
        """'Sales 2026' vs 'Sales 2025' — too little left to be sure."""
        html = build_report_html("# Sales 2026\n\nBody.\n", None, subject="Sales 2025")
        assert h1s(html) == ["Sales 2026"]

    def test_without_a_subject_nothing_is_dropped(self):
        html = build_report_html(f"# {SUBJECT}\n\nBody.\n", None)
        assert h1s(html) == [SUBJECT]

    def test_the_body_survives_when_it_is_only_the_title(self):
        assert build_report_html(f"# {SUBJECT}", None, subject=SUBJECT).strip() == ""


class TestTheCallerPassesTheSubject:
    def test_send_report_email_hands_the_subject_to_the_builder(self):
        """The strip is dead code unless the call site passes `subject`."""
        import inspect
        from app.agents import internal_tools

        src = inspect.getsource(internal_tools._send_report_email)
        assert (
            "build_report_html(content_markdown, display_blocks, subject=subject)"
            in src
        )
