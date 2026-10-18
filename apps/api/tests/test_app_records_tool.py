"""norm.app_records — a dashboard tile's (and the HR member's) read of the
built-in apps' rows, scoped to the calling organisation."""

import uuid

from app.agents.internal_tools import get_handler
from app.db.models import AppRecord
from app.services import caller_scope
from tests.conftest import _make_organization


def _row(db, org, collection, data, namespace="hr_suite"):
    row = AppRecord(
        id=str(uuid.uuid4()),
        namespace=namespace,
        organization_id=org.id,
        collection=collection,
        data=data,
    )
    db.add(row)
    db.flush()
    return row


def _call(db, org, **params):
    handler = get_handler("norm", "app_records")
    assert handler is not None
    with caller_scope.use(caller_scope.Scope(org.id)):
        return handler(params, db, None)


class TestReads:
    def test_rows_of_a_collection_for_the_calling_org_only(self, db_session):
        ours = _make_organization(db_session, name="Ours " + uuid.uuid4().hex[:6])
        theirs = _make_organization(db_session, name="Theirs " + uuid.uuid4().hex[:6])
        _row(db_session, ours, "job_openings", {"title": "Chef", "status": "open"})
        _row(db_session, ours, "job_openings", {"title": "Host", "status": "closed"})
        _row(db_session, theirs, "job_openings", {"title": "Spy", "status": "open"})
        out = _call(db_session, ours, app="hiring", collection="job_openings")
        assert out["success"] is True
        assert out["data"]["count"] == 2
        assert {r["title"] for r in out["data"]["rows"]} == {"Chef", "Host"}

    def test_where_filters_and_count_only(self, db_session):
        org = _make_organization(db_session, name="Org " + uuid.uuid4().hex[:6])
        _row(db_session, org, "applications", {"status": "active"})
        _row(db_session, org, "applications", {"status": "active"})
        _row(db_session, org, "applications", {"status": "rejected"})
        out = _call(
            db_session,
            org,
            app="hiring",
            collection="applications",
            where={"status": "active"},
            count_only=True,
        )
        assert out["data"]["count"] == 2 and out["data"]["rows"] == []
        out = _call(
            db_session,
            org,
            app="hiring",
            collection="applications",
            where={"status": {"ne": "active"}},
        )
        assert out["data"]["count"] == 1

    def test_a_date_window_and_overdue(self, db_session):
        org = _make_organization(db_session, name="Org " + uuid.uuid4().hex[:6])
        _row(
            db_session,
            org,
            "assignments",
            {"due_date": "2026-10-14", "status": "assigned"},
        )
        _row(
            db_session,
            org,
            "assignments",
            {"due_date": "2026-10-20", "status": "assigned"},
        )
        _row(
            db_session,
            org,
            "assignments",
            {"due_date": "2001-01-01", "status": "assigned"},
        )
        out = _call(
            db_session,
            org,
            app="training",
            collection="assignments",
            date_field="due_date",
            start="2026-10-12T07:00:00+13:00",
            end="2026-10-19T06:59:59+13:00",
        )
        assert [r["due_date"] for r in out["data"]["rows"]] == ["2026-10-14"]
        out = _call(
            db_session,
            org,
            app="training",
            collection="assignments",
            date_field="due_date",
            before_today=True,
            where={"status": {"ne": "completed"}},
        )
        assert out["data"]["count"] >= 1
        assert all(r["due_date"] < "2026-10-18" for r in out["data"]["rows"])

    def test_fields_trim_the_rows(self, db_session):
        org = _make_organization(db_session, name="Org " + uuid.uuid4().hex[:6])
        _row(db_session, org, "people", {"name": "Ana", "email": "a@x", "pay": 1})
        out = _call(
            db_session, org, app="training", collection="people", fields=["name"]
        )
        assert out["data"]["rows"][0].keys() == {"id", "name"}


class TestRefusals:
    def test_only_built_in_apps_and_declared_collections(self, db_session):
        org = _make_organization(db_session, name="Org " + uuid.uuid4().hex[:6])
        assert _call(db_session, org, app="payroll", collection="x")["success"] is False
        out = _call(db_session, org, app="hiring", collection="remuneration")
        assert out["success"] is False and "no collection" in out["error"]

    def test_no_scope_no_rows(self, db_session):
        handler = get_handler("norm", "app_records")
        with caller_scope.use(None):
            out = handler(
                {"app": "hiring", "collection": "job_openings"}, db_session, None
            )
        assert out["success"] is False and "organisation" in out["error"]
