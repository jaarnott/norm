"""Tests for report CRUD endpoints."""

import uuid


from app.db.models import Report, ReportChart


class TestCreateReport:
    """POST /api/reports"""

    def test_create_report(self, client, db_session, admin_user, admin_headers):
        resp = client.post(
            "/api/reports",
            json={
                "title": "Sales Report",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Sales Report"
        assert data["status"] == "draft"
        assert data["charts"] == []
        assert data["id"]

        # no title → "Untitled Report"
        resp = client.post("/api/reports", json={}, headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["title"] == "Untitled Report"


class TestListReports:
    """GET /api/reports"""

    def test_list_reports(self, client, db_session, admin_user, admin_headers):
        # Create reports
        for i in range(3):
            db_session.add(
                Report(
                    id=str(uuid.uuid4()),
                    user_id=admin_user.id,
                    title=f"Report {i}",
                )
            )
        db_session.flush()

        resp = client.get("/api/reports", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "reports" in data
        assert len(data["reports"]) == 3

    def test_list_reports_only_own_reports(
        self,
        client,
        db_session,
        admin_user,
        manager_user,
        manager_headers,
    ):
        # Create report for admin
        db_session.add(
            Report(
                id=str(uuid.uuid4()),
                user_id=admin_user.id,
                title="Admin Report",
            )
        )
        db_session.flush()

        # Manager should not see it
        resp = client.get("/api/reports", headers=manager_headers)
        assert resp.status_code == 200
        assert len(resp.json()["reports"]) == 0


class TestGetReport:
    """GET /api/reports/{report_id}"""

    def test_get_report(self, client, db_session, admin_user, admin_headers):
        report = Report(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            title="My Report",
        )
        db_session.add(report)
        db_session.flush()

        resp = client.get(f"/api/reports/{report.id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["title"] == "My Report"


class TestUpdateReport:
    """PATCH /api/reports/{report_id}"""

    def test_update_report_title_and_layout(
        self, client, db_session, admin_user, admin_headers
    ):
        report = Report(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            title="Old Title",
        )
        db_session.add(report)
        db_session.flush()

        resp = client.patch(
            f"/api/reports/{report.id}",
            json={
                "title": "New Title",
                "description": "Updated description",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New Title"
        assert resp.json()["description"] == "Updated description"

        layout = [{"chart_id": "abc", "col": 1, "row": 1, "colSpan": 12, "rowSpan": 8}]
        resp = client.patch(
            f"/api/reports/{report.id}",
            json={
                "layout": layout,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["layout"] == layout


class TestDeleteReport:
    """DELETE /api/reports/{report_id}"""

    def test_delete_report(self, client, db_session, admin_user, admin_headers):
        report = Report(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            title="To Delete",
        )
        db_session.add(report)
        db_session.flush()

        resp = client.delete(f"/api/reports/{report.id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestAddChart:
    """POST /api/reports/{report_id}/charts"""

    def test_add_chart(self, client, db_session, admin_user, admin_headers):
        report = Report(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            title="Report",
        )
        db_session.add(report)
        db_session.flush()

        resp = client.post(
            f"/api/reports/{report.id}/charts",
            json={
                "title": "Sales Bar Chart",
                "chart_type": "bar",
                "chart_spec": {"x_axis": "month", "y_axis": "revenue"},
                "data": [{"month": "Jan", "revenue": 1000}],
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["charts"]) == 1
        assert data["charts"][0]["title"] == "Sales Bar Chart"
        assert data["charts"][0]["chart_type"] == "bar"
        # Layout should be auto-populated
        assert len(data["layout"]) == 1

    def test_add_chart_missing_title_returns_422(
        self,
        client,
        db_session,
        admin_user,
        admin_headers,
    ):
        report = Report(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            title="Report",
        )
        db_session.add(report)
        db_session.flush()

        resp = client.post(
            f"/api/reports/{report.id}/charts", json={}, headers=admin_headers
        )
        assert resp.status_code == 422


class TestUpdateChart:
    """PATCH /api/reports/{report_id}/charts/{chart_id}"""

    def test_update_chart(self, client, db_session, admin_user, admin_headers):
        report = Report(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            title="Report",
        )
        db_session.add(report)
        db_session.flush()

        chart = ReportChart(
            id=str(uuid.uuid4()),
            report_id=report.id,
            title="Old Chart",
            chart_type="bar",
            position=0,
        )
        db_session.add(chart)
        db_session.flush()

        resp = client.patch(
            f"/api/reports/{report.id}/charts/{chart.id}",
            json={"title": "New Chart", "chart_type": "line"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New Chart"
        assert resp.json()["chart_type"] == "line"


class TestRemoveChart:
    """DELETE /api/reports/{report_id}/charts/{chart_id}"""

    def test_remove_chart(self, client, db_session, admin_user, admin_headers):
        report = Report(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            title="Report",
            layout=[],
        )
        db_session.add(report)
        db_session.flush()

        chart = ReportChart(
            id=str(uuid.uuid4()),
            report_id=report.id,
            title="To Remove",
            chart_type="bar",
            position=0,
        )
        db_session.add(chart)
        db_session.flush()

        resp = client.delete(
            f"/api/reports/{report.id}/charts/{chart.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestNotFound:
    def test_every_route_404s_for_an_unknown_report_or_chart(
        self, client, db_session, admin_user, admin_headers
    ):
        """Get, update and delete a report, and add, update and remove a
        chart: an id that does not exist is a 404 on each (folds six tests)."""
        report = Report(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            title="Report",
        )
        db_session.add(report)
        db_session.flush()
        missing = uuid.uuid4()
        calls = [
            ("GET", f"/api/reports/{missing}", None),
            ("PATCH", f"/api/reports/{missing}", {"title": "Nope"}),
            ("DELETE", f"/api/reports/{missing}", None),
            ("POST", f"/api/reports/{missing}/charts", {"title": "Chart"}),
            ("PATCH", f"/api/reports/{report.id}/charts/{missing}", {"title": "Nope"}),
            ("DELETE", f"/api/reports/{report.id}/charts/{missing}", None),
        ]
        for method, url, body in calls:
            resp = client.request(method, url, json=body, headers=admin_headers)
            assert resp.status_code == 404, (method, url, resp.status_code)


class TestWhoMaySeeAndChange:
    """Until 21 Oct 2026 every report route checked only "signed in": anyone
    could read, change or delete any dashboard by its id, and a published one
    showed in every organisation's picker. A report is its owner's to change;
    a published one is seen only by people sharing an organisation with its
    owner."""

    def _world(self, db_session):
        from tests.conftest import _make_membership
        from tests.org_helpers import auth_headers, make_org, make_user

        mine, theirs = make_org(db_session, "Mine"), make_org(db_session, "Theirs")
        owner, colleague, outsider = (make_user(db_session) for _ in range(3))
        _make_membership(db_session, owner, mine)
        _make_membership(db_session, colleague, mine)
        _make_membership(db_session, outsider, theirs)
        dash = Report(
            id=str(uuid.uuid4()),
            user_id=owner.id,
            title="Operations",
            is_dashboard=True,
            agent_slug="reports",
            is_published=True,
        )
        db_session.add(dash)
        db_session.flush()
        chart = ReportChart(
            id=str(uuid.uuid4()), report_id=dash.id, title="Sales", position=0
        )
        db_session.add(chart)
        db_session.flush()
        h = {u: auth_headers(u) for u in (owner, colleague, outsider)}
        return type(
            "W",
            (),
            dict(
                dash=dash,
                chart=chart,
                h=h,
                owner=owner,
                colleague=colleague,
                outsider=outsider,
            ),
        )

    def test_another_organisation_cannot_reach_it_by_id(self, client, db_session):
        w = self._world(db_session)
        d, c = w.dash.id, w.chart.id
        calls = [
            ("GET", f"/api/reports/{d}", None),
            ("PATCH", f"/api/reports/{d}", {"title": "Mine now"}),
            ("DELETE", f"/api/reports/{d}", None),
            ("POST", f"/api/reports/{d}/charts", {"title": "Chart"}),
            ("PATCH", f"/api/reports/{d}/charts/{c}", {"title": "Nope"}),
            ("DELETE", f"/api/reports/{d}/charts/{c}", None),
            ("POST", f"/api/reports/{d}/promote-to-dashboard", {}),
            ("POST", "/api/reports/dashboards/reports/set-active", {"dashboard_id": d}),
        ]
        for method, url, body in calls:
            resp = client.request(method, url, json=body, headers=w.h[w.outsider])
            assert resp.status_code == 404, (method, url, resp.status_code)
        for url in (
            "/api/reports",
            "/api/reports/dashboards",
            "/api/reports/dashboards/reports/available",
        ):
            assert d not in client.get(url, headers=w.h[w.outsider]).text, url
        got = client.get("/api/reports/dashboards/reports", headers=w.h[w.outsider])
        assert got.json()["dashboard"] is None
        db_session.refresh(w.dash)
        assert w.dash.title == "Operations"

    def test_a_colleague_sees_it_but_only_its_owner_changes_it(
        self, client, db_session
    ):
        w = self._world(db_session)
        d = w.dash.id
        seen = client.get(f"/api/reports/{d}", headers=w.h[w.colleague])
        assert seen.status_code == 200 and seen.json()["is_own"] is False
        shared = client.get(
            "/api/reports/dashboards/reports/available", headers=w.h[w.colleague]
        ).json()["shared"]
        assert [s["id"] for s in shared] == [d]
        assert (
            client.post(
                "/api/reports/dashboards/reports/set-active",
                json={"dashboard_id": d},
                headers=w.h[w.colleague],
            ).status_code
            == 200
        )
        for method, url, body in (
            ("PATCH", f"/api/reports/{d}", {"is_published": False}),
            ("DELETE", f"/api/reports/{d}", None),
            ("PATCH", f"/api/reports/{d}/charts/{w.chart.id}", {"title": "x"}),
        ):
            resp = client.request(method, url, json=body, headers=w.h[w.colleague])
            assert resp.status_code == 403, (method, url, resp.status_code)
        own = client.patch(
            f"/api/reports/{d}", json={"title": "Ops"}, headers=w.h[w.owner]
        )
        assert own.status_code == 200 and own.json()["is_own"] is True

    def test_a_choice_that_is_no_longer_shared_falls_back(self, client, db_session):
        w = self._world(db_session)
        w.colleague.dashboard_preferences = {"reports": w.dash.id}
        w.dash.is_published = False
        db_session.flush()
        got = client.get("/api/reports/dashboards/reports", headers=w.h[w.colleague])
        assert got.json()["dashboard"] is None

    def test_with_nothing_to_show_the_agents_template_is_copied_for_them(
        self, client, db_session
    ):
        """Six people outside Cook Brothers were shown the platform admin's
        dashboards because nothing else existed. With no dashboard of their own
        and one template for the AI agent, they get their own copy instead."""
        from app.db.config_models import DashboardTemplate

        w = self._world(db_session)
        db_session.add(
            DashboardTemplate(
                id=str(uuid.uuid4()),
                slug=f"ops-{uuid.uuid4().hex[:6]}",
                agent_slug="reports",
                title="Operations",
                charts=[
                    {
                        "title": "Note",
                        "chart_type": "text",
                        "chart_spec": {"text_content": "hi"},
                        "script": {},
                        "layout": {"col": 1, "row": 1, "colSpan": 24, "rowSpan": 4},
                    }
                ],
                enabled=True,
            )
        )
        db_session.flush()
        first = client.get("/api/reports/dashboards/reports", headers=w.h[w.outsider])
        got = first.json()["dashboard"]
        assert got["is_own"] is True and got["id"] != w.dash.id
        assert [c["title"] for c in got["charts"]] == ["Note"]
        again = client.get("/api/reports/dashboards/reports", headers=w.h[w.outsider])
        assert (
            again.json()["dashboard"]["id"] == got["id"]
        )  # one copy, not one per visit

    def test_the_template_list_needs_a_sign_in(self, client):
        assert client.get("/api/reports/templates").status_code in (401, 403)
