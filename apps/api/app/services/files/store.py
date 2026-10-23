"""Bytes for a file id — the executor's default ``file_bytes``.

A spec row that declares ``body_file_id`` (a Xero attachment, Oct 2026)
needs the file's bytes and type. The native modules keep files in their own
content-addressed tables (``ent_files``, ``pay_files``), so this looks an id
up across them; a caller with its own source passes ``file_bytes=`` to
``execute_spec`` and this is never asked.

Both tables are tenant rows, and the lookup keeps to the caller's
organisation (``caller_scope``): a file id from another tenant — one a
person saw in their own organisation and carried across — serves nothing
(security review, 20 Oct 2026). A caller with no scope at all is a system
job and reads by id, as before. Imported lazily — the tables belong to other
modules and may not be loaded where this is.
"""

from __future__ import annotations

from sqlalchemy.orm import Session


def _models():
    out = []
    try:
        from app.db.entertainers_models import EntFile

        out.append(EntFile)
    except ImportError:  # pragma: no cover
        pass
    try:
        from app.db.payroll_models import PayFile

        out.append(PayFile)
    except ImportError:  # pragma: no cover
        pass
    return out


def file_bytes(db: Session, file_id: str) -> tuple[bytes, str]:
    """``(bytes, content_type)`` of ``file_id``, or ``(b"", "")`` when no
    finance table of the caller's organisation holds it (the executor then
    refuses the send)."""
    from app.services import caller_scope

    scope = caller_scope.current()
    for model in _models():
        q = db.query(model).filter(model.id == str(file_id))
        if scope is not None and not scope.unrestricted:
            if not scope.org_id:
                return b"", ""  # a scope naming no organisation is nobody's
            q = q.filter(model.organization_id == scope.org_id)
        row = q.first()
        if row is not None and row.data:
            return bytes(row.data), str(row.content_type or "application/octet-stream")
    return b"", ""
