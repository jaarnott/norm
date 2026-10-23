"""The world the payroll tests share (tests/test_payroll_*.py): one
organisation with the four SYSTEM roles (the scopes F0's migration grants
them), a 07:00 and a 06:00 NZ venue, non-admin people with each role, the
module's modes, and a faked ``snapshot.loaded`` — the one seam to Loaded —
answering with ``get_payrun`` results shaped like config-api.md.

Non-admin users throughout: a platform admin bypasses every org rule, so a
test that used one would prove nothing about them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.db.models import Connection, OrganizationMembership
from app.services.finance_core import modes
from app.services.finance_core.ctx import build_ctx, for_org
from app.services.payroll import snapshot as snapshot_svc
from tests.org_helpers import (
    grant_venue,
    make_org,
    make_user,
    make_venue,
    seed_system_roles,
)

NZ = ZoneInfo("Pacific/Auckland")
MONDAY = dt.date(2026, 10, 12)  # WE 18 Oct 2026
LAZ_LOADED = "9e850c10-laz"
DSC_LOADED = "7b2f0a44-dsc"


def _member(db, org, role, *, legacy="member", venues=()):
    user = make_user(db)
    db.add(
        OrganizationMembership(
            id=str(uuid.uuid4()),
            user_id=user.id,
            organization_id=org.id,
            role=legacy,
            role_id=role.id,
        )
    )
    db.flush()
    for v in venues:
        grant_venue(db, user, v)
    return user


def _connect(db, venue, company_id):
    db.add(
        Connection(
            connector_name="loadedhub",
            venue_id=venue.id,
            enabled="true",
            config={"x_loaded_company_id": company_id},
            access_token="tok",
        )
    )
    db.flush()


def make_world(db, *, payroll="live", entertainers="off", finance="off"):
    roles = seed_system_roles(db)
    org = make_org(db, "Cook Brothers")
    org.payroll_contact_email = "pay@cookbrothers.test"
    for module, value in (
        ("payroll", payroll),
        ("entertainers", entertainers),
        ("finance", finance),
    ):
        modes.set_mode(db, org.id, module, value)
    laz = make_venue(db, "La Zeppa", org)
    laz.timezone, laz.day_start_time = "Pacific/Auckland", "07:00"
    dsc = make_venue(db, "Dunedin Social Club", org)
    dsc.timezone, dsc.day_start_time = "Pacific/Auckland", "06:00"
    db.flush()
    _connect(db, laz, LAZ_LOADED)
    _connect(db, dsc, DSC_LOADED)

    owner = _member(db, org, roles["owner"], legacy="owner", venues=[laz, dsc])
    manager = _member(db, org, roles["manager"], venues=[laz, dsc])
    payroll_admin = _member(db, org, roles["payroll_admin"], venues=[laz])
    team_member = _member(db, org, roles["team_member"], venues=[laz])

    other_org = make_org(db, "Rival Co")
    modes.set_mode(db, other_org.id, "payroll", "live")
    other_venue = make_venue(db, "Rival Bar", other_org)
    other_owner = _member(
        db, other_org, roles["owner"], legacy="owner", venues=[other_venue]
    )
    db.flush()

    w = SimpleNamespace(
        db=db,
        org=org,
        roles=roles,
        laz=laz,
        dsc=dsc,
        owner=owner,
        manager=manager,
        payroll_admin=payroll_admin,
        team_member=team_member,
        other_org=other_org,
        other_venue=other_venue,
        other_owner=other_owner,
    )
    w.ctx = lambda user=None, **kw: build_ctx(db, user or payroll_admin, **kw)
    w.system = lambda: for_org(db, org.id, actor_label="tick")
    return w


# ------------------------------------------------------------- payrun ----


def shift(
    sid,
    staff,
    name,
    *,
    code="LZ001",
    start="2026-10-13T17:00:00+13:00",
    end="2026-10-13T23:00:00+13:00",
    hours=6.0,
    cost_cents=18000,
    ph_hours=0.0,
    ph_cost_cents=0,
    shift_type="Clockin",
    remuneration="HourlyRate",
    role="FLOOR TEAM",
    cost_group="bar_floor",
    worked=LAZ_LOADED,
    other=False,
    leave_request_id=None,
    leave_type=None,
    approved=True,
):
    trading = dt.datetime.fromisoformat(start)
    day = (
        trading.date()
        if trading.time() >= dt.time(7, 0)
        else trading.date() - dt.timedelta(days=1)
    )
    return {
        "source_shift_id": sid,
        "staff_member_id": staff,
        "staff_name": name,
        "pay_code": code,
        "shift_type": shift_type,
        "remuneration_type": remuneration,
        "role_id": "r1",
        "role_name": role,
        "cost_group": cost_group,
        "worked_venue_loaded_id": worked,
        "worked_venue_name": "La Zeppa",
        "is_from_other_venue": other,
        "clockin_at": start,
        "clockout_at": end,
        "trading_date": day.isoformat(),
        "hours": hours,
        "cost_cents": cost_cents,
        "export_cost_cents": cost_cents,
        "ph_hours": ph_hours,
        "ph_cost_cents": ph_cost_cents,
        "leave_request_id": leave_request_id,
        "leave_type_name": leave_type,
        "is_approved": approved,
        "breakdown": {"pay_rate_breakdown": [{"total": cost_cents / 100}], "rules": []},
    }


def payrun_result(venue, week_start, rows, *, dropped=None, view="all", parity=None):
    """A ``get_payrun`` answer in config-api.md's shape."""
    sunday = week_start + dt.timedelta(days=6)
    worked = [r for r in rows if r["shift_type"] != "Leave"]
    leave = [r for r in rows if r["shift_type"] == "Leave"]
    hygiene = {
        "unapproved_clockins": [
            {
                "staff_name": r["staff_name"],
                "clockin_at": r["clockin_at"],
                "clockout_at": r["clockout_at"],
            }
            for r in rows
            if r["shift_type"] == "Clockin" and not r["is_approved"]
        ],
        "staff_without_pay_code": sorted(
            {r["staff_name"] for r in worked if not r["pay_code"]}
        ),
        "unfinished_clockins": [],
        "staff_without_timeline": [],
        "salaried_count": len(
            {r["staff_member_id"] for r in rows if r["remuneration_type"] == "Salary"}
        ),
        "public_holiday_dates": sorted(
            {r["trading_date"] for r in rows if r["ph_cost_cents"] > 0}
        ),
        "parity": parity
        or {
            "pay_summary_total_wages": round(
                sum(r["cost_cents"] for r in worked) / 100, 2
            ),
            "rows_wages": round(sum(r["cost_cents"] for r in worked) / 100, 2),
            "difference": 0.0,
            "pay_summary_leave": round(sum(r["cost_cents"] for r in leave) / 100, 2),
            "rows_leave": round(sum(r["cost_cents"] for r in leave) / 100, 2),
            "leave_difference": 0.0,
            "within_tolerance": True,
            "tolerance": 0.05,
        },
    }
    grouped = {}
    for r in leave:
        g = grouped.setdefault(
            (r["leave_request_id"], r["staff_member_id"]),
            {
                "leave_request_id": r["leave_request_id"],
                "staff_member_id": r["staff_member_id"],
                "staff_name": r["staff_name"],
                "pay_code": r["pay_code"],
                "leave_type_name": r["leave_type_name"],
                "dates": [],
                "hours": 0.0,
                "cost_cents": 0,
                "is_paid_type": True,
            },
        )
        g["dates"].append(r["trading_date"])
        g["hours"] += r["hours"]
        g["cost_cents"] += r["cost_cents"]
    return {
        "venue": venue.name,
        "venue_id": venue.id,
        "week_ending": sunday.isoformat(),
        "week_start": week_start.isoformat(),
        "window": {
            "start": "",
            "end": "",
            "day_start": venue.day_start_time,
            "timezone": venue.timezone,
        },
        "view": view,
        "rows": rows
        if view in ("all", "staff")
        else (
            [r for r in rows if r["shift_type"] == "Leave"] if view == "leave" else []
        ),
        "dropped": dropped or {"deleted": 0, "unfinished": 0},
        "dropped_unfinished": [],
        "totals": {},
        "sections": {
            "hygiene": hygiene,
            "leave": {"rows": list(grouped.values()), "totals": {}},
        },
        "parity": hygiene["parity"],
    }


class FakeLoaded:
    """Scripted answers for ``snapshot.loaded`` by action; ``get_payrun``
    takes a callable of (venue, params) or a fixed result."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, db, ctx, venue, action, params, *, config_db=None):
        self.calls.append((venue.name, action, dict(params)))
        if action not in self.answers:
            from app.services.finance_core.errors import Invalid

            raise Invalid(f"Loaded could not be read: no fake for {action}")
        answer = self.answers[action]
        out = answer(venue, params) if callable(answer) else answer
        if isinstance(out, Exception):
            raise out
        if isinstance(out, dict) and out.get("error"):
            # The real seam turns a consolidator's error into the service's.
            from app.services.finance_core.errors import Invalid

            raise Invalid(f"Loaded could not be read: {out['error']}")
        return out


def install(monkeypatch, fake: FakeLoaded):
    monkeypatch.setattr(snapshot_svc, "loaded", fake)
    return fake
