"""Home's Today block: what is due for one person today, gathered from
providers (docs/plans/team-apps-skeleton.md §3.5).

A provider is a function registered under a name::

    @register("training")
    def training(db, config_db, user, organization_id, venue_ids) -> list[TodayItem]

``GET /api/me/today`` (routers/staff.py) calls ``items_for``, which runs every
provider, logs and skips one that raises (one module's bad day must never
empty the block for the others), sorts the items by due date (undated last)
then title, and serialises them. The Training provider lives in
``today_training.py``; the Kitchen modules (Food Control, Prep, Repairs)
register their own in the same way and add their module to
``PROVIDER_MODULES`` so it is imported before the first call.

An item with ``team`` and ``page_id`` opens that screen inside the team app;
one with ``app_slug`` opens that App-platform app; ``venue_id`` lets a
manager's block group items by venue.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: The tones a provider may use. The web shows ``error`` as overdue and
#: ``warn`` as due soon; ``ok`` and ``neutral`` carry no badge. (The Training
#: provider still says ``overdue`` / ``due`` / ``info``, which the web's
#: Today block read before providers existed — its output is unchanged.)
TONES = ("ok", "warn", "error", "neutral")

#: Modules that register a provider on import, in the order their items are
#: gathered. A Kitchen module adds itself here.
PROVIDER_MODULES: tuple[str, ...] = ("app.services.today_training",)


@dataclass
class TodayItem:
    """One row of the Today block."""

    kind: str
    title: str
    detail: str | None = None
    due_at: datetime | date | None = None
    tone: str = "neutral"
    venue_id: str | None = None
    team: str | None = None
    page_id: str | None = None
    app_slug: str | None = None
    params: dict | None = None

    def to_dict(self) -> dict:
        out = asdict(self)
        out["due_at"] = self.due_at.isoformat() if self.due_at is not None else None
        return out


Provider = Callable[[Session, Session, object, str, list[str]], list[TodayItem]]

_PROVIDERS: dict[str, Provider] = {}
_loaded = False


def register(name: str) -> Callable[[Provider], Provider]:
    """Register ``fn(db, config_db, user, organization_id, venue_ids) ->
    list[TodayItem]`` under ``name``. Registering a name twice replaces the
    earlier provider, so a reloaded module does not run its items twice."""

    def decorator(fn: Provider) -> Provider:
        _PROVIDERS[name] = fn
        return fn

    return decorator


def _load_builtin_providers() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    for module in PROVIDER_MODULES:
        try:
            importlib.import_module(module)
        except Exception:  # noqa: BLE001 — one module never takes the block down
            logger.exception("today: provider module %s failed to import", module)


def providers() -> dict[str, Provider]:
    """Every registered provider by name, built-in modules loaded."""
    _load_builtin_providers()
    return dict(_PROVIDERS)


def _due_key(due: datetime | date | None) -> datetime:
    """A comparable due date: a date is its midnight, a naive datetime is
    taken as UTC, so dates and datetimes from different providers sort
    together."""
    if due is None:
        return datetime.max.replace(tzinfo=timezone.utc)
    if isinstance(due, datetime):
        return due if due.tzinfo else due.replace(tzinfo=timezone.utc)
    return datetime(due.year, due.month, due.day, tzinfo=timezone.utc)


def sort_items(items: list[TodayItem]) -> list[TodayItem]:
    """Due date first (undated last), then title."""
    return sorted(
        items, key=lambda i: (i.due_at is None, _due_key(i.due_at), i.title or "")
    )


def items_for(
    db: Session,
    config_db: Session,
    user,
    organization_id: str,
    venue_ids: list[str],
) -> list[dict]:
    """Every provider's items for this person, sorted and serialised. A
    provider that raises is logged and skipped; one that returns something
    other than a list of TodayItem is skipped the same way."""
    items: list[TodayItem] = []
    for name, fn in providers().items():
        try:
            out = fn(db, config_db, user, organization_id, venue_ids)
        except Exception:  # noqa: BLE001 — see module docstring
            logger.exception("today: provider %s failed for user %s", name, user.id)
            continue
        if not isinstance(out, list) or not all(isinstance(i, TodayItem) for i in out):
            logger.error("today: provider %s returned %r, not TodayItems", name, out)
            continue
        items.extend(out)
    return [i.to_dict() for i in sort_items(items)]
