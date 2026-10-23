"""``/api/payroll/*`` (DESIGN-CONTRACT §4, plan §9 "Security"): the
non-admin owner and payroll admin are served, a manager and a team member
are refused, another organisation's rows are not found, ``off`` is a 404,
shadow refuses an input unless it is a rehearsal, and the exports download
as CRLF text.
"""

from __future__ import annotations

import pytest

from app.services.payroll import snapshot as snapshot_svc
from app.services.payroll import weeks as weeks_svc
from tests.org_helpers import auth_headers
from tests.payroll_world import (
    MONDAY,
    FakeLoaded,
    install,
    make_world,
    payrun_result,
    shift,
)

ROWS = [
    shift("s1", "a", "Ana Lee", code="LZ001", hours=6.0, cost_cents=18000),
    shift("s2", "b", "Ben Roy", code="LZ002", hours=5.0, cost_cents=12500),
]


@pytest.fixture
def w(db_session):
    return make_world(db_session)


class TestWhoMayCall:
    def test_owner_and_payroll_admin_200_manager_and_team_member_403(self, client, w):
        for user in (w.owner, w.payroll_admin):
            r = client.get("/api/payroll/meta", headers=auth_headers(user))
            assert r.status_code == 200, r.text
            assert r.json()["mode"] == "live" and r.json()["can_write"] is True
        for user in (w.manager, w.team_member):
            r = client.get("/api/payroll/meta", headers=auth_headers(user))
            assert r.status_code == 403, r.text
            r = client.get("/api/payroll/weeks", headers=auth_headers(user))
            assert r.status_code == 403

    def test_the_owner_sees_every_entity_the_admin_their_venues(self, client, w):
        r = client.get("/api/payroll/entities", headers=auth_headers(w.owner))
        assert {e["venue_name"] for e in r.json()["entities"]} == {
            "La Zeppa",
            "Dunedin Social Club",
        }
        laz = next(e for e in r.json()["entities"] if e["venue_name"] == "La Zeppa")
        assert (
            laz["loaded_company_id"] == "9e850c10-laz"
            and laz["ace_pay_type_code"] == "50001"
        )
        assert laz["uniform_bond_cents"] == 15000 and laz["day_start"] == "07:00"
        r = client.get("/api/payroll/entities", headers=auth_headers(w.payroll_admin))
        assert [e["venue_name"] for e in r.json()["entities"]] == ["La Zeppa"]

    def test_cross_organisation_is_not_found(self, client, w):
        other = weeks_svc.get_or_create(
            w.db, w.ctx(w.other_owner), w.other_venue.id, MONDAY
        )
        r = client.get(f"/api/payroll/weeks/{other.id}", headers=auth_headers(w.owner))
        assert r.status_code == 404
        r = client.post(
            "/api/payroll/weeks",
            json={"venue_id": w.other_venue.id, "week_start": "2026-10-12"},
            headers=auth_headers(w.owner),
        )
        assert r.status_code in (403, 404)
        r = client.get(
            f"/api/payroll/entities/{w.other_venue.id}", headers=auth_headers(w.owner)
        )
        assert r.status_code in (403, 404)
        # And a venue the person holds no access to, inside their own organisation.
        r = client.get(
            f"/api/payroll/entities/{w.dsc.id}", headers=auth_headers(w.payroll_admin)
        )
        assert r.status_code == 403

    def test_off_is_a_404(self, client, db_session):
        w = make_world(db_session, payroll="off")
        r = client.get("/api/payroll/meta", headers=auth_headers(w.owner))
        assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"


class TestShadow:
    def test_an_input_is_refused_unless_rehearsal(self, client, db_session):
        w = make_world(db_session, payroll="shadow")
        week = client.post(
            "/api/payroll/weeks",
            json={"venue_id": w.laz.id, "week_start": "2026-10-12"},
            headers=auth_headers(w.owner),
        ).json()
        body = {
            "gross_cents": 100000,
            "net_cents": 80000,
            "esct_cents": 1000,
            "kiwisaver_cents": 3000,
        }
        r = client.put(
            f"/api/payroll/weeks/{week['id']}/ace-inputs",
            json=body,
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 409 and r.json()["detail"]["code"] == "not_live"
        r = client.put(
            f"/api/payroll/weeks/{week['id']}/ace-inputs",
            json={**body, "rehearsal": True},
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 200, r.text
        assert (
            r.json()["rehearsal"] is True and r.json()["payroll_taxes_cents"] == 24000
        )
        r = client.post(
            "/api/payroll/leave/payouts",
            json={
                "venue_id": w.laz.id,
                "week_start": "2026-10-12",
                "loaded_request_id": "lr1",
                "leave_type": "NZ Bereavement",
                "requested_cents": 100,
            },
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 409

    def test_settings_and_snapshots_are_allowed_in_shadow(
        self, client, db_session, monkeypatch
    ):
        w = make_world(db_session, payroll="shadow")
        install(
            monkeypatch,
            FakeLoaded(get_payrun=lambda v, p: payrun_result(v, MONDAY, ROWS)),
        )
        entity = client.get(
            f"/api/payroll/entities/{w.laz.id}", headers=auth_headers(w.owner)
        ).json()
        r = client.put(
            f"/api/payroll/entities/{w.laz.id}",
            json={
                "version": entity["version"],
                "payroll_on": True,
                "sick_approver_name": "James",
            },
            headers=auth_headers(w.owner),
        )
        assert (
            r.status_code == 200
            and r.json()["payroll_on"] is True
            and r.json()["version"] == entity["version"] + 1
        )
        week = client.post(
            "/api/payroll/weeks",
            json={"venue_id": w.laz.id, "week_start": "2026-10-12"},
            headers=auth_headers(w.owner),
        ).json()
        r = client.post(
            f"/api/payroll/weeks/{week['id']}/start-close",
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 200, r.text
        assert (
            r.json()["status"] == "close_ready"
            and r.json()["close_snapshot"]["shift_count"] == 2
        )


class TestWeekAndExports:
    def test_week_detail_payrun_and_download(self, client, w, monkeypatch):
        install(
            monkeypatch,
            FakeLoaded(get_payrun=lambda v, p: payrun_result(v, MONDAY, ROWS)),
        )
        h = auth_headers(w.owner)
        week = client.post(
            "/api/payroll/weeks",
            json={"venue_id": w.laz.id, "week_start": "2026-10-12"},
            headers=h,
        ).json()
        assert (
            week["status"] == "open"
            and week["window"]["start"] == "2026-10-12T07:00:00+13:00"
        )
        # No snapshot yet: the page's empty state is a 200, not a 400 (F8).
        r = client.get(f"/api/payroll/weeks/{week['id']}/payrun", headers=h)
        assert r.status_code == 200 and r.json()["snapshot"] is None
        assert r.json()["staff"]["rows"] == []
        assert r.json()["gratuities"]["paid_elsewhere"] == []
        r = client.post(
            f"/api/payroll/weeks/{week['id']}/snapshots",
            json={"source": "manual"},
            headers=h,
        )
        assert r.status_code == 200 and r.json()["source"] == "manual"
        r = client.post(
            f"/api/payroll/weeks/{week['id']}/exports",
            json={"kinds": ["import"]},
            headers=h,
        )
        assert (
            r.status_code == 400 and "close snapshot" in r.json()["detail"]["message"]
        )
        client.post(f"/api/payroll/weeks/{week['id']}/start-close", headers=h)
        r = client.get(
            f"/api/payroll/weeks/{week['id']}/payrun",
            params={"snapshot": "close"},
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert [p["staff_name"] for p in r.json()["staff"]["rows"]] == [
            "Ana Lee",
            "Ben Roy",
        ]
        r = client.post(
            f"/api/payroll/weeks/{week['id']}/exports",
            json={"kinds": ["import"]},
            headers=h,
        )
        assert r.status_code == 200, r.text
        export = r.json()["exports"][0]
        r = client.get(f"/api/payroll/exports/{export['id']}/download", headers=h)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert (
            r.content == b"#52,LZ001,50001,6.00,30.00\r\n#52,LZ002,50001,5.00,25.00\r\n"
        )
        r = client.get(f"/api/payroll/weeks/{week['id']}", headers=h)
        assert r.json()["status"] == "exported" and len(r.json()["exports"]) == 1
        r = client.post(
            f"/api/payroll/weeks/{week['id']}/close",
            json={"version": r.json()["version"]},
            headers=h,
        )
        assert r.status_code == 200 and r.json()["status"] == "closed"
        r = client.get(
            f"/api/payroll/weeks/{week['id']}/fallback-pack/IMPORT.TXT", headers=h
        )
        assert r.content.startswith(b"#52,LZ001")
        assert (
            client.get(
                f"/api/payroll/weeks/{week['id']}/fallback-pack/NOPE.TXT", headers=h
            ).status_code
            == 404
        )

    def test_a_loaded_failure_is_a_400_with_its_message(self, client, w, monkeypatch):
        install(monkeypatch, FakeLoaded(get_payrun={"error": "token expired"}))
        h = auth_headers(w.owner)
        week = client.post(
            "/api/payroll/weeks",
            json={"venue_id": w.laz.id, "week_start": "2026-10-12"},
            headers=h,
        ).json()
        r = client.post(
            f"/api/payroll/weeks/{week['id']}/snapshots",
            json={"source": "manual"},
            headers=h,
        )
        assert r.status_code == 400 and "token expired" in r.json()["detail"]["message"]

    def test_a_typo_in_a_body_is_refused(self, client, w):
        h = auth_headers(w.owner)
        week = client.post(
            "/api/payroll/weeks",
            json={"venue_id": w.laz.id, "week_start": "2026-10-12"},
            headers=h,
        ).json()
        r = client.put(
            f"/api/payroll/weeks/{week['id']}/ace-inputs",
            json={
                "gross_cents": 1,
                "net_cents": 1,
                "esct_cents": 0,
                "kiwisaver_cents": 0,
                "grosscents": 5,
            },
            headers=h,
        )
        assert r.status_code == 422

    def test_adjustments_and_accruals_round_trip(self, client, w):
        h = auth_headers(w.owner)
        week = client.post(
            "/api/payroll/weeks",
            json={"venue_id": w.laz.id, "week_start": "2026-10-12"},
            headers=h,
        ).json()
        r = client.post(
            f"/api/payroll/weeks/{week['id']}/adjustments",
            json={
                "label": "Termination pay",
                "amount_cents": 45000,
                "report_target": "wages",
            },
            headers=h,
        )
        assert r.status_code == 200, r.text
        adj = r.json()
        r = client.put(
            f"/api/payroll/adjustments/{adj['id']}",
            json={"amount_cents": -500, "version": adj["version"]},
            headers=h,
        )
        assert (
            r.status_code == 200
            and r.json()["amount_cents"] == -500
            and r.json()["version"] == 2
        )
        r = client.put(
            f"/api/payroll/adjustments/{adj['id']}",
            json={"amount_cents": 1, "version": 1},
            headers=h,
        )
        assert r.status_code == 409
        r = client.put(
            f"/api/payroll/weeks/{week['id']}/accruals",
            json={"opening_cents": 1000, "accrued_cents": 500, "paid_cents": 200},
            headers=h,
        )
        assert r.status_code == 200 and r.json()["closing_cents"] == 1300
        assert (
            client.delete(
                f"/api/payroll/adjustments/{adj['id']}", headers=h
            ).status_code
            == 204
        )
        assert client.get(
            f"/api/payroll/weeks/{week['id']}/adjustments", headers=h
        ).json() == {"adjustments": []}

    def test_shadow_tab_lists_runs_and_signs_off(self, client, w, monkeypatch):
        install(
            monkeypatch,
            FakeLoaded(
                get_payrun=lambda v, p: payrun_result(
                    v, MONDAY, ROWS, dropped={"deleted": 1, "unfinished": 0}
                )
            ),
        )
        h = auth_headers(w.owner)
        snapshot_svc.take(w.db, w.ctx(w.owner), w.laz.id, MONDAY, source="close")
        r = client.post(
            "/api/payroll/shadow/runs",
            json={
                "module": "payroll",
                "venue_id": w.laz.id,
                "week_start": "2026-10-12",
            },
            headers=h,
        )
        assert r.status_code == 200, r.text
        keys = {i["key"]: i for i in r.json()["items"]}
        assert keys["snapshot:dropped"]["class"] == "expected"
        assert keys["snapshot:pay_summary"]["class"] == "expected"
        r = client.get("/api/payroll/shadow", headers=h)
        summary = r.json()["runs"][0]
        assert summary["module"] == "payroll" and r.json()["mode"] == "live"
        # The list carries the count and the venue; the detail carries the items.
        assert summary["item_count"] == 2 and "items" not in summary
        assert (
            summary["venue_name"] == "La Zeppa"
            and summary["week_start"] == "2026-10-12"
        )
        detail = client.get(
            f"/api/payroll/shadow/runs/{summary['id']}", headers=h
        ).json()
        assert detail["item_count"] == 2 and len(detail["items"]) == 2
        meta = client.get("/api/payroll/meta", headers=h).json()
        assert (meta["entities_on"], meta["entities_total"]) == (0, 2)
        hygiene = client.get(
            f"/api/payroll/weeks/{client.post('/api/payroll/weeks', json={'venue_id': w.laz.id, 'week_start': '2026-10-12'}, headers=h).json()['id']}/hygiene",
            headers=h,
        ).json()
        assert (
            hygiene["snapshot_report"]["of"] == 0
            and "switched on" in hygiene["snapshot_report"]["note"]
        )
        item = keys["snapshot:dropped"]
        r = client.post(
            f"/api/payroll/shadow/items/{item['id']}/sign-off",
            json={"explanation": "seen"},
            headers=h,
        )
        assert r.status_code == 200 and r.json()["signed_off_by"] == w.owner.id
        r = client.post(
            "/api/payroll/shadow/runs",
            json={
                "module": "payroll",
                "venue_id": w.laz.id,
                "week_start": "2026-10-12",
            },
            headers=auth_headers(w.manager),
        )
        assert r.status_code == 403
