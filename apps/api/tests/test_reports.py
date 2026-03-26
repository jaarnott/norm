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

    def test_create_report_default_title(self, client, admin_headers):
        resp = client.post("/api/reports", json={}, headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["title"] == "Untitled Report"

    def test_create_report_with_venue(self, client, db_session, admin_headers, venue):
        resp = client.post(
            "/api/reports",
            json={
                "title": "Venue Report",
                "venue_id": venue.id,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["venue_id"] == venue.id

    def test_create_report_without_auth_returns_401(self, client):
        resp = client.post("/api/reports", json={"title": "No Auth"})
        assert resp.status_code in (401, 403)


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

    def test_get_report_not_found_returns_404(self, client, admin_headers):
        resp = client.get(f"/api/reports/{uuid.uuid4()}", headers=admin_headers)
        assert resp.status_code == 404


class TestUpdateReport:
    """PATCH /api/reports/{report_id}"""

    def test_update_report_title(self, client, db_session, admin_user, admin_headers):
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

    def test_update_report_layout(self, client, db_session, admin_user, admin_headers):
        report = Report(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            title="Report",
        )
        db_session.add(report)
        db_session.flush()

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

    def test_update_report_not_found_returns_404(self, client, admin_headers):
        resp = client.patch(
            f"/api/reports/{uuid.uuid4()}",
            json={
                "title": "Nope",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 404


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

    def test_delete_report_not_found_returns_404(self, client, admin_headers):
        resp = client.delete(f"/api/reports/{uuid.uuid4()}", headers=admin_headers)
        assert resp.status_code == 404


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

    def test_add_chart_report_not_found_returns_404(self, client, admin_headers):
        resp = client.post(
            f"/api/reports/{uuid.uuid4()}/charts",
            json={
                "title": "Chart",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 404

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

    def test_update_chart_not_found_returns_404(
        self, client, db_session, admin_user, admin_headers
    ):
        report = Report(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            title="Report",
        )
        db_session.add(report)
        db_session.flush()

        resp = client.patch(
            f"/api/reports/{report.id}/charts/{uuid.uuid4()}",
            json={"title": "Nope"},
            headers=admin_headers,
        )
        assert resp.status_code == 404


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

    def test_remove_chart_not_found_returns_404(
        self, client, db_session, admin_user, admin_headers
    ):
        report = Report(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            title="Report",
        )
        db_session.add(report)
        db_session.flush()

        resp = client.delete(
            f"/api/reports/{report.id}/charts/{uuid.uuid4()}",
            headers=admin_headers,
        )
        assert resp.status_code == 404
