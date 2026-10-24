"""The world the Budget (F6, the old Norm Finance) tests share: one organisation with the
four system roles, three venues (two trading, one head office), and NON-admin
people — an owner, a manager with one venue, a team member, a payroll admin.
A platform admin bypasses every org rule, so none is used here.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

from app.db.models import (
    AutomatedTask,
    AutomatedTaskRun,
    OrganizationMembership,
    Thread,
)
from app.services.finance_core import modes
from app.services.finance_core.ctx import build_ctx
from tests.org_helpers import (
    grant_venue,
    make_org,
    make_user,
    make_venue,
    seed_system_roles,
)

#: A Monday, well inside NZDT.
MONDAY = dt.date(2026, 11, 16)
SUNDAY = dt.date(2026, 11, 22)


def _member(db, org, role, venues=()):
    user = make_user(db)
    db.add(
        OrganizationMembership(
            id=str(uuid.uuid4()),
            user_id=user.id,
            organization_id=org.id,
            role="member",
            role_id=role.id,
        )
    )
    db.flush()
    for v in venues:
        grant_venue(db, user, v)
    return user


def make_world(db, *, finance: str = "live") -> SimpleNamespace:
    roles = seed_system_roles(db)
    org = make_org(db, "Cook Brothers")
    org.currency = "NZD"
    org.fiscal_year_start_month = 4
    modes.set_mode(db, org.id, "finance", finance)
    laz = make_venue(db, "La Zeppa", org)
    laz.timezone, laz.day_start_time = "Pacific/Auckland", "07:00"
    dsc = make_venue(db, "Dunedin Social Club", org)
    dsc.timezone, dsc.day_start_time = "Pacific/Auckland", "06:00"
    cbi = make_venue(db, "Head Office", org)
    cbi.is_trading = False
    db.flush()
    owner = _member(db, org, roles["owner"])
    manager = _member(db, org, roles["manager"], venues=[laz])
    team_member = _member(db, org, roles["team_member"], venues=[laz])
    payroll_admin = _member(db, org, roles["payroll_admin"], venues=[laz, dsc])
    other_org = make_org(db, "Rival Co")
    modes.set_mode(db, other_org.id, "finance", "live")
    other_venue = make_venue(db, "Rival Bar", other_org)
    other_owner = _member(db, other_org, roles["owner"])
    db.flush()
    w = SimpleNamespace(
        db=db,
        org=org,
        roles=roles,
        laz=laz,
        dsc=dsc,
        cbi=cbi,
        owner=owner,
        manager=manager,
        team_member=team_member,
        payroll_admin=payroll_admin,
        other_org=other_org,
        other_venue=other_venue,
        other_owner=other_owner,
    )
    w.ctx = lambda user=None, **kw: build_ctx(db, user or owner, **kw)
    return w


def chat_thread(db, user, venue=None) -> Thread:
    thread = Thread(
        id=str(uuid.uuid4()),
        user_id=user.id,
        domain="finance",
        status="awaiting_user_input",
        intent="finance",
        raw_prompt="budgets?",
        venue_id=venue.id if venue else None,
        extracted_fields={},
        missing_fields=[],
    )
    db.add(thread)
    db.flush()
    return thread


def task_run_thread(db, user, venue=None) -> Thread:
    """A scheduled task's run: the thread has an AutomatedTaskRun row (and
    the automated_task intent)."""
    thread = chat_thread(db, user, venue)
    thread.intent = "finance.automated_task"
    task = AutomatedTask(
        id=str(uuid.uuid4()),
        created_by=user.id,
        title="Nightly budget check",
        agent_slug="finance",
        prompt="check budgets",
        status="active",
    )
    db.add(task)
    db.flush()
    db.add(
        AutomatedTaskRun(
            id=str(uuid.uuid4()),
            automated_task_id=task.id,
            thread_id=thread.id,
            status="running",
        )
    )
    db.flush()
    return thread


def weekday_series(
    first: dt.date, days: int, *, closed=(1,), seed_scale=100
) -> dict[dt.date, int]:
    """A deterministic weekly pattern in cents: Monday closed, a Saturday
    peak, a small per-week wobble so medians and means differ."""
    base = {1: 0, 2: 400, 3: 600, 4: 900, 5: 1500, 6: 2200, 7: 1000}
    out = {}
    for i in range(days):
        d = first + dt.timedelta(days=i)
        wd = d.isoweekday()
        if wd in closed:
            out[d] = 0
            continue
        wobble = ((i // 7) % 5) - 2  # -2..+2 by week
        out[d] = (base[wd] + wobble * 20) * seed_scale
    return out
