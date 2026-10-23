"""The table-argument helpers every native Norm module shares.

Norm Functions wrote these first (``app/db/functions_models.py``) and keeps
its own copies; the Finance modules — ``payroll_models``, ``entertainers_models``,
``finance_models`` — import them from here so the three model files read the
same way without reaching into Functions' private names. The rules they
encode are the ones Functions' docstring sets out, and the metadata test in
``tests/test_finance_foundation.py`` asserts them for every finance table:

* **Tenancy in the schema.** ``organization_id NOT NULL``, ``UNIQUE
  (organization_id, id)``, and every reference to a sibling table or to
  ``venues`` is the composite ``(organization_id, x_id)``, so a row can never
  point at another organisation's row whatever a query forgets to filter.
  References to ``users`` stay plain: users are global.
* **Plans are local, facts are instants.** A venue-local plan is a ``Date`` +
  ``Time`` pair with an ``ends_next_day`` flag (``_local_window``); when
  something happened is a ``timestamptz``.
* **Money is integer cents** (``*_cents``).

``NativeRow`` is the base every table takes (``id``, ``organization_id``, the
two timestamps); ``_Versioned`` adds optimistic concurrency for rows people
edit. Nothing here is a model: this module is imported by the model files,
never registered on ``Base.metadata`` itself.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import declared_attr, mapped_column
from sqlalchemy.types import String

from app.db.models import _now, _uuid


class _Ref:
    """A composite, org-scoped reference from one table to another."""

    def __init__(
        self,
        cols: str | tuple[str, ...],
        target: str,
        ondelete: str,
        *,
        target_cols: tuple[str, ...] | None = None,
        index: bool = True,
    ):
        self.cols = (cols,) if isinstance(cols, str) else tuple(cols)
        self.target = target
        self.target_cols = target_cols or ("id",)
        self.ondelete = ondelete
        self.index = index


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def _check(table: str, column: str, values: tuple[str, ...]) -> CheckConstraint:
    """A vocabulary CHECK. NULL passes, as in every SQL CHECK — nullability is
    the column's business."""
    return CheckConstraint(_in(column, values), name=f"ck_{table}_{column}")


def _tenancy(table: str, *refs: _Ref, extra: tuple = ()) -> tuple:
    """``UNIQUE (organization_id, id)`` plus the composite FKs (each indexed:
    Postgres does not index the referencing side, and every one of these is a
    join or a cascade check)."""
    out: list = [UniqueConstraint("organization_id", "id", name=f"uq_{table}_org_id")]
    for r in refs:
        name = r.cols[-1]
        out.append(
            ForeignKeyConstraint(
                ["organization_id", *r.cols],
                [f"{r.target}.organization_id"]
                + [f"{r.target}.{c}" for c in r.target_cols],
                name=f"fk_{table}_{name}",
                ondelete=r.ondelete,
            )
        )
        if r.index:
            out.append(Index(f"ix_{table}_{name}", "organization_id", *r.cols))
    out.extend(extra)
    return tuple(out)


def _user_fk(table: str, column: str) -> Column:
    """A plain reference to a user. Users are global, and an actor leaving must
    not take the record of what they did with them."""
    return Column(
        String,
        ForeignKey("users.id", ondelete="SET NULL", name=f"fk_{table}_{column}"),
        nullable=True,
    )


class NativeRow:
    """The columns every native table carries (``sort_order`` keeps ``id`` and
    ``organization_id`` first and the timestamps last in the DDL)."""

    id = mapped_column(String, primary_key=True, default=_uuid, sort_order=-2)

    @declared_attr
    def organization_id(cls):
        return mapped_column(
            String,
            ForeignKey(
                "organizations.id",
                ondelete="CASCADE",
                name=f"fk_{cls.__tablename__}_organization_id",
            ),
            nullable=False,
            sort_order=-1,
        )

    created_at = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
        sort_order=10,
    )
    updated_at = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
        sort_order=11,
    )


class _Versioned:
    """Optimistic concurrency for rows people edit.

    The mapper's ``version_id_col`` puts ``WHERE version = :seen`` on every ORM
    UPDATE and bumps it, so two people saving the same row can never silently
    overwrite each other — the loser gets ``StaleDataError``, which the
    services turn into ``Conflict``.
    """

    version = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"), sort_order=9
    )

    @declared_attr.directive
    def __mapper_args__(cls):
        return {"version_id_col": cls.version}


def _local_window() -> tuple[Column, Column, Column, Column]:
    """A venue-local plan: date, start, end, and whether the end is tomorrow."""
    return (
        Column(Date, nullable=True),
        Column(Time, nullable=True),
        Column(Time, nullable=True),
        Column(Boolean, nullable=False, default=False, server_default=text("false")),
    )
