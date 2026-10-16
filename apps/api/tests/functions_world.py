"""Shared fixtures for the Norm Functions tests: a live organisation with two
venues, staff with access to one of them, an owner, the venue's approvers —
and a second organisation, so every test can try to cross the line."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from app.db.functions_models import (
    FnContact,
    FnContactEmail,
    FnEnquiry,
    FnVenueArea,
    FnVenueFunctionSettings,
)
from app.services.functions.ctx import build_ctx
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_user,
    _make_venue,
    _make_venue_access,
)

UTC = dt.timezone.utc


def make_world(db, *, mode: str = "live") -> SimpleNamespace:
    org = _make_organization(db, name="Cook Bros")
    org.functions_mode = mode
    venue = _make_venue(db, name="Brew Bar", organization_id=org.id)
    venue.timezone = "Pacific/Auckland"
    venue.day_start_time = "07:00"
    venue2 = _make_venue(db, name="Garden Bar", organization_id=org.id)
    venue2.timezone = "Pacific/Auckland"

    staff = _make_user(db, full_name="Sam Staff")
    _make_membership(db, staff, org, role="member")
    _make_venue_access(db, staff, venue)

    owner = _make_user(db, full_name="Olive Owner")
    _make_membership(db, owner, org, role="owner")

    approver = _make_user(db, full_name="Andy Approver")
    _make_membership(db, approver, org, role="member")
    _make_venue_access(db, approver, venue)

    overrider = _make_user(db, full_name="Fran Functions")
    _make_membership(db, overrider, org, role="member")
    _make_venue_access(db, overrider, venue)

    db.add(
        FnVenueFunctionSettings(
            organization_id=org.id,
            venue_id=venue.id,
            approver_user_id=approver.id,
            override_approver_user_id=overrider.id,
        )
    )
    deck = FnVenueArea(
        organization_id=org.id, venue_id=venue.id, name="Deck", requires_approval=True
    )
    snug = FnVenueArea(
        organization_id=org.id, venue_id=venue.id, name="Snug", requires_approval=False
    )
    db.add_all([deck, snug])

    other_org = _make_organization(db, name="Rival Co")
    other_org.functions_mode = "live"
    other_venue = _make_venue(db, name="Rival Bar", organization_id=other_org.id)
    other_venue.timezone = "Pacific/Auckland"
    other_user = _make_user(db, full_name="Rita Rival")
    _make_membership(db, other_user, other_org, role="owner")
    db.flush()

    w = SimpleNamespace(
        db=db,
        org=org,
        venue=venue,
        venue2=venue2,
        staff=staff,
        owner=owner,
        approver=approver,
        overrider=overrider,
        deck=deck,
        snug=snug,
        other_org=other_org,
        other_venue=other_venue,
        other_user=other_user,
    )
    w.ctx = lambda user=None, **kw: build_ctx(db, user or staff, **kw)
    return w


def add_contact(db, org_id: str, name: str = "Casey Client", email: str | None = None):
    c = FnContact(organization_id=org_id, name=name)
    db.add(c)
    db.flush()
    if email:
        db.add(
            FnContactEmail(
                organization_id=org_id, contact_id=c.id, email=email, is_primary=True
            )
        )
        db.flush()
    return c


def add_enquiry(db, org_id: str, contact_id: str, venue_id: str | None, **kw):
    e = FnEnquiry(
        organization_id=org_id,
        contact_id=contact_id,
        venue_id=venue_id,
        source=kw.pop("source", "email"),
        **kw,
    )
    db.add(e)
    db.flush()
    return e
