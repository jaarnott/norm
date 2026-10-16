"""Image uploads — the bytes behind area photos, brand logos and the proposal
image library.

``fn_files`` is content-addressed per organisation (``UNIQUE(org, sha256)``):
the same picture uploaded twice is one row, so the venue's logo, an area photo
and a library image can all point at the one copy, and a public URL can carry
the hash and be cached forever.

Only raster images are accepted, and the type is decided by **reading the
bytes** with Pillow — never by the browser's Content-Type or the file name,
which the uploader controls. SVG is refused outright: it is a document that can
carry script, and these images end up inside customer-facing proposals.
"""

from __future__ import annotations

import hashlib
import io

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.functions_models import FnFile
from app.services.functions._rows import load
from app.services.functions.ctx import FunctionsCtx, require_live, require_staff
from app.services.functions.errors import Invalid, NotFound

#: Proposal and area photos, not a photo archive — the bytes live in the
#: database (as AppFile / UploadedDocument do), so keep them modest.
MAX_BYTES = 10 * 1024 * 1024
#: A decompression bomb is a small file that decodes to an enormous canvas;
#: refuse it before Pillow allocates anything.
MAX_PIXELS = 40_000_000
#: Pillow format -> the content type we store and serve.
ALLOWED_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}


def sniff_image(data: bytes) -> tuple[str, int, int]:
    """``(content_type, width, height)`` of an accepted image, else
    ``Invalid``. Reads the header for the format and size, then ``verify()``
    walks the file so a truncated or corrupt upload is refused here rather
    than as a broken image in a customer's proposal."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            width, height = img.size
            if fmt not in ALLOWED_FORMATS:
                raise Invalid(
                    "Upload a JPEG, PNG, GIF or WebP image"
                    + (f" (this is {fmt})." if fmt else ".")
                )
            if width * height > MAX_PIXELS:
                raise Invalid("That image is too large — keep it under 40 megapixels.")
            img.verify()
    except Invalid:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise Invalid("That file is not an image Norm can read.") from exc
    except Image.DecompressionBombError as exc:
        raise Invalid("That image is too large — keep it under 40 megapixels.") from exc
    return ALLOWED_FORMATS[fmt], int(width), int(height)


def store_image(
    db: Session, ctx: FunctionsCtx, data: bytes, filename: str | None = None
) -> FnFile:
    """Store an uploaded image (or return the identical one already stored).

    The insert runs in a savepoint: two people uploading the same image at
    once both end up with the one row, instead of the loser's request failing
    on the unique hash."""
    require_live(ctx)
    require_staff(ctx)
    if not data:
        raise Invalid("The file is empty.")
    if len(data) > MAX_BYTES:
        raise Invalid("That image is over 10 MB — resize it and try again.")
    content_type, width, height = sniff_image(data)
    sha = hashlib.sha256(data).hexdigest()
    existing = _by_sha(db, ctx, sha)
    if existing is not None:
        return existing
    row = FnFile(
        organization_id=ctx.org_id,
        sha256=sha,
        filename=_clean_name(filename),
        content_type=content_type,
        size_bytes=len(data),
        width=width,
        height=height,
        data=data,
        created_by_user_id=ctx.user_id,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = _by_sha(db, ctx, sha)
        if existing is None:  # pragma: no cover — a different constraint
            raise
        return existing
    return row


def _by_sha(db: Session, ctx: FunctionsCtx, sha: str) -> FnFile | None:
    return (
        db.query(FnFile)
        .filter(FnFile.organization_id == ctx.org_id, FnFile.sha256 == sha)
        .first()
    )


def _clean_name(filename: str | None) -> str | None:
    """The name as shown in the library — no directory parts, bounded."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name[:200] or None


def get_file(db: Session, ctx: FunctionsCtx, file_id: str) -> FnFile:
    """A file of the caller's organisation (files are org-wide, not
    venue-scoped: one logo can serve several venues)."""
    return load(db, ctx, FnFile, file_id, label="File", venue_attr=None)


def require_file(db: Session, ctx: FunctionsCtx, file_id: str | None) -> str | None:
    """Validate a file id a caller wants to attach (a logo, a photo). The
    composite foreign key would refuse another org's file anyway; this says
    so as a 422 rather than a database error."""
    if file_id is None:
        return None
    try:
        return get_file(db, ctx, file_id).id
    except NotFound as exc:
        raise Invalid("That image was not found — upload it again.") from exc


def file_dict(row: FnFile) -> dict:
    """What the UI needs to show and reference an uploaded image. The bytes
    are served by ``GET /api/functions/files/{id}`` (staff) — public pages
    serve them by hash (F5's route)."""
    return {
        "id": row.id,
        "sha256": row.sha256,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "width": row.width,
        "height": row.height,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
