"""Is this upload a PDF Norm will store and show to people?

The entertainer portal and onboarding form take invoices, exemption
certificates and company documents from the public internet; a Payroll
Administrator opens them later inside Norm. So a file is checked before it is
stored, not when it is served:

* it starts with the ``%PDF-`` magic and ``pypdf`` can parse it — a renamed
  executable or an HTML page is refused;
* it carries no active content — ``/JavaScript``, ``/JS``, ``/OpenAction``
  (runs on open) or ``/AcroForm`` (a form that can post) — the four names
  the design contract lists, looked for in the raw bytes so an object pypdf
  skips is still seen;
* it is 10 MB or under.

Images go through ``functions/uploads.py`` (call it; this module is PDFs only).
"""

from __future__ import annotations

import io

from app.services.finance_core.errors import Invalid

MAX_BYTES = 10 * 1024 * 1024
_MAGIC = b"%PDF-"
#: Names that mean active content. ``/JS`` is the short form of
#: ``/JavaScript``; ``/OpenAction`` runs something when the file opens;
#: ``/AcroForm`` is a form, which can submit.
_ACTIVE = (b"/JavaScript", b"/JS", b"/OpenAction", b"/AcroForm")


def has_active_content(data: bytes) -> bool:
    """Whether PDF bytes name scripts, an open action or a form — the part of
    the check the importer applies to Orbit's files, which it cannot ask a
    performer to re-export."""
    return any(name in data for name in _ACTIVE)


def check_pdf(data: bytes) -> None:
    """Raise ``Invalid`` unless ``data`` is a plain, parseable PDF of 10 MB or less."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise Invalid("The file is empty.")
    if len(data) > MAX_BYTES:
        raise Invalid("The file is larger than 10 MB.")
    if not bytes(data[: len(_MAGIC)]) == _MAGIC:
        raise Invalid("The file isn't a PDF.")
    for name in _ACTIVE:
        if name in data:
            raise Invalid(
                "The PDF contains scripts or a form, which Norm doesn't accept — "
                "export it as a plain PDF and try again."
            )
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(bytes(data)))
        if len(reader.pages) < 1:
            raise Invalid("The PDF has no pages.")
    except Invalid:
        raise
    except Exception as exc:  # noqa: BLE001 — pypdf raises its own family
        raise Invalid(f"The PDF couldn't be read ({type(exc).__name__}).") from exc
