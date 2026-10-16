"""Is the Norm Functions App on for an organisation?

Two switches must agree: the marketplace (catalog App ``functions``, entitled
like any other App — services/entitlements.py) and the organisation's own
``functions_mode``. ``off`` always wins, so an org cannot reach Functions
before it has been imported or switched on, whatever the catalog says.

**Fallback while the catalog has no ``functions`` row.** The catalog lives in
the config DB shared with production, and local testing must not need a write
there. So until the row exists the App counts as on exactly when
``functions_mode != 'off'``; once it exists, on = catalog entitlement AND
mode != off.
"""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

FUNCTIONS_APP_SLUG = "functions"


def functions_mode(db: Session, organization_id: str | None) -> str:
    """The org's mode; anything unrecognised reads as ``off`` (fail closed)."""
    if not organization_id:
        return "off"
    from app.db.functions_models import FUNCTIONS_MODES
    from app.db.models import Organization

    row = (
        db.query(Organization.functions_mode)
        .filter(Organization.id == organization_id)
        .first()
    )
    mode = row[0] if row else None
    return mode if mode in FUNCTIONS_MODES else "off"


def adjust_apps_on(
    on: set[str], organization_id: str | None, db: Session, catalog_rows
) -> set[str]:
    """Apply the Functions rule to an ``apps_on`` result (the hook in
    ``entitlements.apps_on``): drop ``functions`` when the mode is off, add it
    under the no-catalog-row fallback."""
    mode = functions_mode(db, organization_id)
    has_row = any(getattr(a, "slug", None) == FUNCTIONS_APP_SLUG for a in catalog_rows)
    if mode == "off":
        on.discard(FUNCTIONS_APP_SLUG)
    elif not has_row:
        on.add(FUNCTIONS_APP_SLUG)
    return on


#: org id -> monotonic time until which a "yes" from the catalog is trusted.
_ENTITLED_UNTIL: dict[str, float] = {}
ENTITLEMENT_TTL_SECONDS = 30.0


def functions_enabled(
    db: Session, config_db: Session, organization_id: str | None
) -> bool:
    """The one check routers and tools make before serving Functions.

    The org's own ``functions_mode`` is read fresh every time (a local,
    indexed read), so switching Functions off bites immediately. The catalog
    half reads the shared config DB — a separate Cloud SQL instance, 40–280 ms
    a call from the dev box, and every page makes several — so only a "yes"
    from it is remembered, for 30 seconds per organisation: an App switched
    off in the marketplace disappears within half a minute."""
    if functions_mode(db, organization_id) == "off":
        return False
    if _ENTITLED_UNTIL.get(organization_id or "", 0.0) > time.monotonic():
        return True
    enabled = _catalog_allows(db, config_db, organization_id)
    if enabled and organization_id:
        _ENTITLED_UNTIL[organization_id] = time.monotonic() + ENTITLEMENT_TTL_SECONDS
    return enabled


def _catalog_allows(
    db: Session, config_db: Session, organization_id: str | None
) -> bool:
    from app.services.entitlements import _catalog, apps_on, entitled_slugs

    rows = _catalog(config_db)
    if not any(a.slug == FUNCTIONS_APP_SLUG for a in rows):
        return True
    on = apps_on(organization_id, db, config_db)
    if on is not None:
        return FUNCTIONS_APP_SLUG in on
    # Team-member gating inactive: the App's own entitlement decides.
    return FUNCTIONS_APP_SLUG in entitled_slugs(organization_id, db, config_db)
