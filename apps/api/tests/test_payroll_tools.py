"""The ``norm_payroll`` built-ins (app/agents/payroll_tools.py), called as
the agent loop calls them: ``get_mode`` names an unattended run, ``get_week``
reads for the conversation's person, ``record_week`` refuses a task run and
a person without ``payroll:write``, and its preview keeps nothing.
"""

from __future__ import annotations

import pytest

from app.agents import internal_tools as IT
from app.agents import payroll_tools as PT
from app.db.payroll_models import PayAdjustment
from tests.conftest import _make_thread
from tests.payroll_world import (
    MONDAY,
    FakeLoaded,
    install,
    make_world,
    payrun_result,
    shift,
)


@pytest.fixture
def w(db_session):
    return make_world(db_session)


def call(db, user, tool, intent="finance.tool_use", **params):
    thread = _make_thread(
        db, user, domain="finance", intent=intent, status="in_progress"
    )
    return IT.get_handler("norm_payroll", tool)(params, db, thread.id)


class TestGetMode:
    def test_names_the_mode_and_an_unattended_run(self, w):
        assert call(w.db, w.owner, "get_mode") == {
            "success": True,
            "data": {"mode": "live"},
            "error": None,
        }
        out = call(w.db, w.owner, "get_mode", intent="finance.automated_task")
        assert (
            out["data"] == {"mode": "live", "error": "unattended run"}
            and out["success"] is False
        )

    def test_rows_declare_the_contract(self):
        rows = {r["action"]: r for r in PT.TOOL_ROWS}
        assert set(rows) == {
            a for c, a in IT.registered_actions() if c == "norm_payroll" and a in rows
        }
        assert rows["get_week"]["requires"] == ["payroll:read"] and rows["record_week"][
            "requires"
        ] == ["payroll:write"]
        assert rows["get_mode"]["engine_only"] is True
        for r in rows.values():
            assert isinstance(r["field_schema"], dict)
            for name in r["required_fields"] + r["optional_fields"]:
                assert name in r["field_schema"], f"{r['action']}.{name} has no schema"


class TestGetWeek:
    def test_a_payroll_person_reads_the_week(self, w, monkeypatch):
        from app.services.payroll import snapshot as snapshot_svc

        install(
            monkeypatch,
            FakeLoaded(
                get_payrun=lambda v, p: payrun_result(
                    v,
                    MONDAY,
                    [
                        shift(
                            "s1", "a", "Ana Lee", code=None, hours=6.0, cost_cents=18000
                        )
                    ],
                )
            ),
        )
        snapshot_svc.take(w.db, w.ctx(w.owner), w.laz.id, MONDAY, source="monday")
        out = call(w.db, w.owner, "get_week", venue="La Zeppa", week_start="2026-10-12")
        assert out["success"], out["error"]
        data = out["data"]
        assert data["status"] == "snapshotted" and data["snapshot"]["shift_count"] == 1
        assert data["hygiene_from_snapshot"]["without_pay_code"] == ["Ana Lee"]
        assert data["parity"]["last_run"] is None and data["payouts"] == []

    def test_a_manager_is_refused_and_another_org_is_not_found(self, w):
        out = call(
            w.db, w.manager, "get_week", venue="La Zeppa", week_start="2026-10-12"
        )
        assert out["success"] is False and "payroll:read" in out["error"]
        out = call(
            w.db, w.owner, "get_week", venue="Rival Bar", week_start="2026-10-12"
        )
        assert out["success"] is False and "Rival Bar" in out["error"]

    def test_off_is_a_refusal(self, db_session):
        w = make_world(db_session, payroll="off")
        out = call(w.db, w.owner, "get_week", venue="La Zeppa")
        assert out["success"] is False and out["error"] == PT.OFF


class TestRecordWeek:
    FIELDS = {
        "label": "Termination pay",
        "amount_cents": 45000,
        "report_target": "wages",
    }

    def test_records_an_adjustment(self, w):
        out = call(
            w.db,
            w.owner,
            "record_week",
            venue="La Zeppa",
            week_start="2026-10-12",
            op="adjustment",
            fields=self.FIELDS,
        )
        assert out["success"], out["error"]
        assert out["data"]["adjustment"]["label"] == "Termination pay"
        assert (
            w.db.query(PayAdjustment)
            .filter(PayAdjustment.organization_id == w.org.id)
            .count()
            == 1
        )

    def test_fields_may_arrive_as_json_text(self, w):
        out = call(
            w.db,
            w.owner,
            "record_week",
            venue="La Zeppa",
            week_start="2026-10-12",
            op="adjustment",
            fields='{"label": "Rounding", "amount_cents": -3}',
        )
        assert out["success"], out["error"]
        assert out["data"]["adjustment"]["amount_cents"] == -3

    def test_preview_keeps_nothing(self, w):
        out = call(
            w.db,
            w.owner,
            "record_week",
            venue="La Zeppa",
            week_start="2026-10-12",
            op="adjustment",
            fields=self.FIELDS,
            preview="true",
        )
        assert out["success"] and out["data"]["preview"] is True
        assert w.db.query(PayAdjustment).count() == 0
        thread = _make_thread(
            w.db,
            w.owner,
            domain="finance",
            intent="finance.tool_use",
            status="in_progress",
        )
        card = IT.get_previewer("norm_payroll", "record_week")(
            {
                "venue": "La Zeppa",
                "week_start": "2026-10-12",
                "op": "adjustment",
                "fields": self.FIELDS,
            },
            w.db,
            thread.id,
        )
        assert (
            card["preview"]["title"] == "Record adjustment"
            and card["preview"]["venue"] == "La Zeppa"
        )
        assert {c["field"]: c["after"] for c in card["preview"]["changes"]}[
            "label"
        ] == "Termination pay"
        assert w.db.query(PayAdjustment).count() == 0

    def test_refuses_a_task_run_and_a_person_without_write(self, w):
        out = call(
            w.db,
            w.owner,
            "record_week",
            intent="finance.automated_task",
            venue="La Zeppa",
            op="adjustment",
            fields=self.FIELDS,
        )
        assert out["success"] is False and out["error"] == PT.UNATTENDED
        out = call(
            w.db,
            w.manager,
            "record_week",
            venue="La Zeppa",
            op="adjustment",
            fields=self.FIELDS,
        )
        assert out["success"] is False and "payroll:write" in out["error"]
        assert w.db.query(PayAdjustment).count() == 0

    def test_shadow_takes_only_a_rehearsal(self, db_session):
        w = make_world(db_session, payroll="shadow")
        out = call(
            w.db,
            w.owner,
            "record_week",
            venue="La Zeppa",
            week_start="2026-10-12",
            op="adjustment",
            fields=self.FIELDS,
        )
        assert out["success"] is False and "shadow" in out["error"]
        out = call(
            w.db,
            w.owner,
            "record_week",
            venue="La Zeppa",
            week_start="2026-10-12",
            op="adjustment",
            fields={**self.FIELDS, "rehearsal": True},
        )
        assert out["success"], out["error"]
        assert out["data"]["adjustment"]["rehearsal"] is True

    def test_unknown_op(self, w):
        out = call(
            w.db, w.owner, "record_week", venue="La Zeppa", op="journal", fields={}
        )
        assert out["success"] is False and "op is one of" in out["error"]
