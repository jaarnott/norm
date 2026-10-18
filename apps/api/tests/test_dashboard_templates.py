"""The seeded dashboard templates are well-formed: a tile that names a key its
tool never returns, or a card with nothing to show, is invisible until someone
opens the board in production (the Operations board of April 2026 carried
column orders for fields that had stopped existing)."""

from app.db.dashboard_templates import DASHBOARD_TEMPLATES, get_template

GRID = 24
CHART_TYPES = {
    "kpi",
    "bar",
    "stacked_bar",
    "line",
    "pie",
    "table",
    "component",
    "scatter",
}


def _charts():
    for t in DASHBOARD_TEMPLATES:
        for c in t["charts"]:
            yield t["slug"], c


class TestShape:
    def test_every_template_has_a_slug_member_and_charts(self):
        slugs = [t["slug"] for t in DASHBOARD_TEMPLATES]
        assert len(slugs) == len(set(slugs))
        for t in DASHBOARD_TEMPLATES:
            assert t["agent_slug"] and t["title"] and t["charts"]

    def test_tiles_fit_the_grid(self):
        for slug, c in _charts():
            lay = c["layout"]
            assert (
                1 <= lay["col"] <= GRID and lay["col"] + lay["colSpan"] - 1 <= GRID
            ), (slug, c["title"])
            assert lay["rowSpan"] >= 1 and lay["row"] >= 1

    def test_tiles_do_not_overlap(self):
        for t in DASHBOARD_TEMPLATES:
            cells = {}
            for c in t["charts"]:
                lay = c["layout"]
                for r in range(lay["row"], lay["row"] + lay["rowSpan"]):
                    for col in range(lay["col"], lay["col"] + lay["colSpan"]):
                        assert (r, col) not in cells, (
                            t["slug"],
                            c["title"],
                            cells[(r, col)],
                        )
                        cells[(r, col)] = c["title"]

    def test_a_chart_calls_a_tool_or_embeds_a_component(self):
        for slug, c in _charts():
            assert c["chart_type"] in CHART_TYPES, (slug, c["title"])
            script = c.get("script") or {}
            if c["chart_type"] == "component":
                assert c["chart_spec"].get("component_key"), (slug, c["title"])
                assert not script.get("action")
            else:
                assert script.get("connector") and script.get("action"), (
                    slug,
                    c["title"],
                )
                assert script.get("rows") or slug in ("procurement-dashboard",), (
                    slug,
                    c["title"],
                )

    def test_a_card_knows_what_to_show(self):
        for slug, c in _charts():
            if c["chart_type"] != "kpi":
                continue
            s = c["chart_spec"]
            assert (
                s.get("value_key")
                or s.get("aggregate") == "count"
                or (s.get("numerator_key") and s.get("denominator_key"))
            ), (slug, c["title"])

    def test_series_charts_name_their_axes(self):
        for slug, c in _charts():
            if c["chart_type"] in ("bar", "stacked_bar", "line"):
                s = c["chart_spec"]
                assert s.get("x_axis", {}).get("key") and s.get("series"), (
                    slug,
                    c["title"],
                )


class TestPeriods:
    def test_the_new_boards_open_on_a_named_period(self):
        for slug, want in (
            ("reports-dashboard", "today"),
            ("hr-dashboard", "this_week"),
            ("kitchen-costs", "this_month"),
            ("finance-overview", "last_month"),
            ("procurement-cogs", "this_month"),
        ):
            t = get_template(slug)
            assert t["charts"][0]["_default_period"] == want, slug

    def test_a_tile_pinned_to_today_uses_placeholders_not_a_period(self):
        t = get_template("reports-dashboard")
        c = next(c for c in t["charts"] if c["title"].startswith("Sales by half hour"))
        assert "period" not in c["script"]["params"]
        assert c["script"]["params"]["start"] == "today_start"

    def test_no_board_hard_codes_a_venue(self):
        venue_names = (
            "Bessie",
            "Freeman",
            "Glass Goose",
            "Murdochs",
            "Dunedin",
            "Velvet",
            "La Zeppa",
            "Applegum",
        )
        for slug, c in _charts():
            if slug == "procurement-dashboard":
                continue
            for s in c["chart_spec"].get("series") or []:
                assert not any(v in str(s.get("key")) for v in venue_names), (
                    slug,
                    c["title"],
                )
