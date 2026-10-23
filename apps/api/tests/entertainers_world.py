"""Shared fixtures for the entertainers tests: an organisation with the
module live, two venues, NON-admin people — a payroll person
(payroll:read/write, venue 1), a team member (venue 1 only), a member at
venue 2 only, an owner — and a rival organisation to cross the line with.

Built on ``tests/org_helpers.py`` like ``test_finance_foundation.py``: a
platform admin bypasses every org rule, so a test that used one would prove
nothing about them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

from app.db.entertainers_models import EntPerformance, EntPerformer
from app.db.models import OrganizationMembership, Role
from app.services.entertainers import tokens
from app.services.finance_core import modes
from app.services.finance_core.ctx import build_ctx
from tests.org_helpers import auth_headers, grant_venue, make_org, make_user, make_venue

UTC = dt.timezone.utc
NZ = "Pacific/Auckland"


def _role(db, org, name, permissions):
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name=name,
        display_name=name,
        permissions=list(permissions),
    )
    db.add(role)
    db.flush()
    return role


def _member(db, org, *, role=None, legacy="member", venues=(), email=None):
    user = make_user(db)
    if email:
        user.email = email
    db.add(
        OrganizationMembership(
            id=str(uuid.uuid4()),
            user_id=user.id,
            organization_id=org.id,
            role=legacy,
            role_id=role.id if role else None,
        )
    )
    db.flush()
    for v in venues:
        grant_venue(db, user, v)
    return user


def make_world(
    db, *, mode: str = "live", contact: str | None = "payroll@cookbros.test"
) -> SimpleNamespace:
    org = make_org(db, "Cook Bros")
    modes.set_mode(db, org.id, "entertainers", mode)
    org.payroll_contact_email = contact
    org.currency = "NZD"
    venue = make_venue(db, "Brew Bar", org)
    venue.timezone = NZ
    venue.day_start_time = "07:00"
    venue2 = make_venue(db, "Garden Bar", org)
    venue2.timezone = NZ
    db.flush()

    payroll_role = _role(
        db, org, "payroll", ["payroll:read", "payroll:write", "tasks:read"]
    )
    manager_role = _role(db, org, "manager", ["tasks:read", "team:manage"])
    team_role = _role(db, org, "team_member", ["tasks:read"])

    payroll_person = _member(db, org, role=payroll_role, venues=[venue, venue2])
    team_member = _member(db, org, role=team_role, venues=[venue])
    venue2_member = _member(db, org, role=team_role, venues=[venue2])
    manager = _member(
        db, org, role=manager_role, venues=[venue], email="closer@cookbros.test"
    )
    nobody = _member(db, org, role=team_role)
    owner = _member(db, org, legacy="owner")

    other_org = make_org(db, "Rival Co")
    modes.set_mode(db, other_org.id, "entertainers", "live")
    other_venue = make_venue(db, "Rival Bar", other_org)
    other_venue.timezone = NZ
    other_user = _member(db, other_org, legacy="owner")
    db.flush()

    w = SimpleNamespace(
        db=db,
        org=org,
        venue=venue,
        venue2=venue2,
        payroll_person=payroll_person,
        team_member=team_member,
        venue2_member=venue2_member,
        manager=manager,
        nobody=nobody,
        owner=owner,
        other_org=other_org,
        other_venue=other_venue,
        other_user=other_user,
    )
    w.ctx = lambda user=None, **kw: build_ctx(db, user or payroll_person, **kw)
    w.headers = lambda user=None: {
        **auth_headers(user or payroll_person),
        "X-Organization": org.id,
    }
    return w


def add_performer(
    db,
    org,
    *,
    name="Sam Smith",
    lane="payroll_contractor",
    status="accepted",
    email="sam@example.test",
    bank="12-3456-7890123-00",
    ird="049091850",
    declaration=True,
    **fields,
) -> EntPerformer:
    row = EntPerformer(
        organization_id=org.id,
        name=name,
        legal_name=fields.pop("legal_name", name),
        email=email,
        phone="021 000 000",
        lane=lane,
        performer_type=fields.pop("performer_type", "solo"),
        onboarding_status=status,
        submitted_at=dt.datetime(2026, 10, 1, tzinfo=UTC)
        if status != "invited"
        else None,
        accepted_at=dt.datetime(2026, 10, 2, tzinfo=UTC)
        if status == "accepted"
        else None,
        bank_account=bank,
        ird_number=ird,
        date_of_birth="1990-01-02",
        gst_registered=fields.pop("gst_registered", False),
        declaration_snapshot=(
            {
                "method": "form",
                "wording_version": "2026-07-13-v1",
                "signed_at": "2026-10-01T00:00:00+00:00",
            }
            if declaration and lane == "payroll_contractor"
            else None
        ),
        **fields,
    )
    db.add(row)
    db.flush()
    return row


def add_performance(
    db,
    org,
    venue,
    performer,
    *,
    date=dt.date(2026, 10, 10),
    start=dt.time(21, 0),
    end=dt.time(1, 0),
    rate_cents=10_000,
    **fields,
) -> EntPerformance:
    from app.services.entertainers._shared import amount_cents, clock_minutes

    ends_next_day = fields.pop("ends_next_day", False)
    row = EntPerformance(
        organization_id=org.id,
        venue_id=venue.id,
        performer_id=performer.id,
        performance_date=date,
        start_time=start,
        end_time=end,
        ends_next_day=ends_next_day,
        rate_cents=rate_cents,
        agreed_cents=amount_cents(rate_cents, clock_minutes(start, end, ends_next_day)),
        **fields,
    )
    db.add(row)
    db.flush()
    return row


def approved(
    db, org, venue, performer, *, date=dt.date(2026, 10, 10), cents=40_000, **fields
) -> EntPerformance:
    """An approved performance with the manager's actual times set."""
    row = add_performance(db, org, venue, performer, date=date, **fields)
    row.performance_status = "approved"
    row.actual_start_time = row.start_time
    row.actual_end_time = row.end_time
    row.actual_cents = cents
    row.approved_at = dt.datetime.combine(date + dt.timedelta(days=1), dt.time(9), UTC)
    db.flush()
    return row


def portal_link(db, org, performer):
    _row, token, url = tokens.mint(
        db, org_id=org.id, purpose="portal", performer_id=performer.id
    )
    return token, url


def join_link(db, org, performer, *, purpose="onboarding", member=None):
    _row, token, url = tokens.mint(
        db,
        org_id=org.id,
        purpose=purpose,
        performer_id=performer.id,
        band_member_id=member.id if member else None,
    )
    return token, url


def set_code(db, token: str, code: str = "123456") -> None:
    """Issue a known code on a link by writing its hash (the tests never
    read a real email)."""
    from app.db.entertainers_models import EntPublicToken
    from app.db.models import _now

    row = (
        db.query(EntPublicToken)
        .filter(EntPublicToken.token_hash == tokens.hash_token(token))
        .one()
    )
    row.otp_hash = tokens._otp_hash(row.id, code)
    row.otp_expires_at = _now() + tokens.OTP_TTL
    row.otp_attempts = 0
    db.flush()


def verified(db, token: str) -> None:
    from app.db.entertainers_models import EntPublicToken
    from app.db.models import _now

    row = (
        db.query(EntPublicToken)
        .filter(EntPublicToken.token_hash == tokens.hash_token(token))
        .one()
    )
    row.otp_verified_at = _now()
    db.flush()


SENSITIVE_KEYS = ("bank_account", "ird_number", "date_of_birth")
SECRET_VALUES = ("12-3456-7890123-00", "049091850", "49091850", "1990-01-02")


def walk(value, found: list, path: str = "") -> list:
    """Every (path, value) in a JSON document, for "never in any response"."""
    if isinstance(value, dict):
        for k, v in value.items():
            walk(v, found, f"{path}.{k}" if path else k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            walk(v, found, f"{path}[{i}]")
    else:
        found.append((path, value))
    return found


def assert_no_sensitive(payload, *, allow_keys=()) -> None:
    for path, value in walk(payload, []):
        leaf = path.rsplit(".", 1)[-1]
        assert leaf not in SENSITIVE_KEYS or leaf in allow_keys, (
            f"{path} is a sensitive field"
        )
        if isinstance(value, str):
            assert value not in SECRET_VALUES, f"{path} carries a stored secret value"
