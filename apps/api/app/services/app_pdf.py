"""Candidate pack PDFs — the sandbox's ``pdf`` object for Norm's built-in apps
(Oct 2026).

Orbit attaches a "candidate pack" to every interview invite: a cover page with
the candidate's cover letter, then their documents merged in. This is that
pack, built on the server because the sandbox has no bytes and no imports:

* **One PDF per pack.** Each candidate gets a cover page (name, role, cover
  letter), then their PDFs page for page, JPG/PNG images fitted to A4, and the
  text of any Word document. Anything else — or anything that fails to read —
  is listed on a "not included" page instead of breaking the pack (Orbit's
  pdf-lib throws on a macron in a cover letter and the whole pack is lost).
* **Same door as every other file.** Documents are read with
  ``app_runtime.file_fetch`` and the pack is stored with ``file_put``, so a
  pack can only contain files the viewer could open themselves, in the app's
  own org and namespace, and it lands next to the record it belongs to.
* **Bounded.** A pack stops at ``MAX_PAGES`` pages / ``MAX_BYTES`` bytes and
  says so, so one enormous scan cannot hold a request hostage.

The cover page is written as raw PDF (Helvetica, WinAnsi) rather than through
a layout library: it is a page of wrapped text, and ``pypdf`` — the one
dependency — only has to merge.
"""

from __future__ import annotations

import io
import logging
import unicodedata
from types import SimpleNamespace

logger = logging.getLogger(__name__)

A4 = (595.0, 842.0)
MARGIN = 50.0
TEXT_WIDTH = A4[0] - 2 * MARGIN
MAX_PAGES = 300
MAX_BYTES = 14 * 1024 * 1024  # under the 15 MB app-file ceiling
MAX_CANDIDATES = 60
MAX_DOCUMENTS = 30
IMAGE_DPI = 150

#: Helvetica advance widths (1/1000 em) for printable ASCII, from the
#: standard AFM. Anything else is measured as 556 — a typical lowercase glyph.
_HELVETICA = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333,
    278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278,
    584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278,
    500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944,
    667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556,
    278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500,
    278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
]  # fmt: skip


# -- text --------------------------------------------------------------------


def _winansi(text: str) -> str:
    """Text the standard Helvetica can draw. WinAnsi is cp1252; a character
    outside it loses its accent if it has one (ā -> a) and becomes '?' if not,
    rather than failing the page."""
    out = []
    for ch in str(text or ""):
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFKD", ch).encode("cp1252", "ignore")
            out.append(base.decode("cp1252") if base else "?")
    return "".join(out)


#: The common punctuation beyond ASCII that cover letters are full of.
_HELVETICA_EXTRA = {
    "\u2014": 1000, "\u2013": 556, "\u2018": 222, "\u2019": 222,
    "\u201c": 333, "\u201d": 333, "\u2022": 350, "\u2026": 1000, "\u00a0": 278,
}  # fmt: skip


def _width(text: str, size: float, bold: bool = False) -> float:
    total = 0
    for ch in text:
        code = ord(ch)
        if 32 <= code < 127:
            total += _HELVETICA[code - 32]
        else:
            total += _HELVETICA_EXTRA.get(ch, 556)
    # Helvetica-Bold runs roughly 6% wider; close enough for wrapping.
    return total * size / 1000.0 * (1.06 if bold else 1.0)


def _wrap(text: str, size: float, width: float, bold: bool = False) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}" if line else word
        if line and _width(candidate, size, bold) > width:
            lines.append(line)
            line = word
        else:
            line = candidate
        # A single word wider than the page (a URL) is cut, not overflowed.
        while _width(line, size, bold) > width and len(line) > 1:
            cut = len(line) - 1
            while cut > 1 and _width(line[:cut], size, bold) > width:
                cut -= 1
            lines.append(line[:cut])
            line = line[cut:]
    if line:
        lines.append(line)
    return lines


def _pdf_string(text: str) -> bytes:
    raw = _winansi(text).encode("cp1252", "replace")
    return (
        b"("
        + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        + b")"
    )


def text_pdf(blocks: list[tuple[str, str]]) -> bytes:
    """A4 pages of wrapped text. ``blocks`` are ``(kind, text)`` with kind
    ``title`` (18pt bold), ``heading`` (13pt bold), ``muted`` (10pt grey) or
    ``body`` (10.5pt, blank lines kept as paragraph breaks)."""
    styles = {
        "title": ("F2", 18.0, 26.0, "0.16 0.2 0.26"),
        "heading": ("F2", 13.0, 20.0, "0 0 0"),
        "muted": ("F1", 10.0, 14.0, "0.4 0.4 0.4"),
        "body": ("F1", 10.5, 14.0, "0 0 0"),
    }
    pages: list[list[bytes]] = [[]]
    y = A4[1] - 42.0
    for kind, text in blocks:
        font, size, leading, colour = styles.get(kind, styles["body"])
        bold = font == "F2"
        paragraphs = str(text or "").replace("\r", "").split("\n")
        for para in paragraphs:
            lines = (
                _wrap(_winansi(para), size, TEXT_WIDTH, bold) if para.strip() else [""]
            )
            for line in lines:
                if y < 60:
                    pages.append([])
                    y = A4[1] - 42.0
                if line:
                    pages[-1].append(
                        f"BT {colour} rg /{font} {size} Tf {MARGIN:.0f} {y:.1f} Td ".encode()
                        + _pdf_string(line)
                        + b" Tj ET"
                    )
                y -= leading
        y -= 4 if kind != "body" else 0

    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(b"")  # Pages, filled in once the kids are known
    f1 = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    f2 = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )
    kids = []
    for ops in pages:
        stream = b"\n".join(ops) or b" "
        content = add(
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        )
        kids.append(
            add(
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {A4[0]:.0f} {A4[1]:.0f}] "
                    f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> >> "
                    f"/Contents {content} 0 R >>"
                ).encode()
            )
        )
    objects[1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    ).encode()

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref)
    )
    return out.getvalue()


# -- documents ---------------------------------------------------------------


def _kind(filename: str, content_type: str | None) -> str:
    name = str(filename or "").lower()
    mime = str(content_type or "").lower()
    if "pdf" in mime or name.endswith(".pdf"):
        return "pdf"
    if "jpeg" in mime or "jpg" in mime or name.endswith((".jpg", ".jpeg")):
        return "image"
    if "png" in mime or name.endswith(".png"):
        return "image"
    if "wordprocessingml" in mime or name.endswith(".docx"):
        return "docx"
    return "other"


def image_pdf(data: bytes) -> bytes:
    """One A4 page with the image fitted inside the margins."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        px = lambda pt: int(round(pt * IMAGE_DPI / 72.0))  # noqa: E731
        page = Image.new("RGB", (px(A4[0]), px(A4[1])), "white")
        box_w, box_h = px(TEXT_WIDTH), px(A4[1] - 2 * MARGIN)
        scale = min(box_w / img.width, box_h / img.height, 1.0)
        size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        fitted = img.resize(size) if size != img.size else img
        page.paste(fitted.convert("RGB"), (px(MARGIN), px(MARGIN)))
        out = io.BytesIO()
        page.save(out, "PDF", resolution=IMAGE_DPI)
        return out.getvalue()


def docx_text(data: bytes) -> str:
    """A Word document's words, paragraphs and table cells in order — enough
    to read a CV in the pack (layout and images are not kept)."""
    import docx

    document = docx.Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells if c.text))
    return "\n".join(parts).strip()


def build_candidate_pack(candidates: list[dict], fetch) -> tuple[bytes, dict]:
    """The merged pack, and what went into it.

    ``candidates``: ``[{"name", "job_title", "cover_letter", "documents":
    [{"file_name", "mime_type", "file_id"}]}]``. ``fetch(file_id)`` returns
    ``(bytes, filename, content_type)`` or ``None`` when the file can't be
    read (missing, or not the viewer's to see)."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    included: list[str] = []
    skipped: list[dict] = []
    truncated = False

    def pages() -> int:
        return len(writer.pages)

    def append(pdf_bytes: bytes) -> None:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            reader.decrypt("")
        if pages() + len(reader.pages) > MAX_PAGES:
            raise OverflowError("the pack is full")
        writer.append(reader)

    for cand in list(candidates or [])[:MAX_CANDIDATES]:
        if not isinstance(cand, dict):
            continue
        name = str(cand.get("name") or "Candidate").strip() or "Candidate"
        blocks: list[tuple[str, str]] = [("title", f"Candidate pack — {name}")]
        if cand.get("job_title"):
            blocks.append(("muted", str(cand["job_title"])))
        blocks.append(("heading", "Cover letter"))
        blocks.append(
            (
                "body",
                str(cand.get("cover_letter") or "").strip()
                or "No cover letter provided.",
            )
        )
        missing: list[str] = []
        try:
            append(text_pdf(blocks))
        except OverflowError:
            truncated = True
            break
        used = 0
        for doc in list(cand.get("documents") or [])[:MAX_DOCUMENTS]:
            if not isinstance(doc, dict):
                continue
            label = str(doc.get("file_name") or "document")
            got = fetch(doc.get("file_id")) if doc.get("file_id") else None
            if not got:
                missing.append(label)
                skipped.append(
                    {"candidate": name, "file": label, "reason": "not available"}
                )
                continue
            data, filename, content_type = got
            kind = _kind(filename or label, content_type or doc.get("mime_type"))
            if used + len(data) > MAX_BYTES:
                missing.append(label)
                skipped.append(
                    {
                        "candidate": name,
                        "file": label,
                        "reason": "too large for the pack",
                    }
                )
                continue
            try:
                if kind == "pdf":
                    append(data)
                elif kind == "image":
                    append(image_pdf(data))
                elif kind == "docx":
                    append(
                        text_pdf(
                            [
                                ("heading", f"{label} (text only)"),
                                (
                                    "body",
                                    docx_text(data) or "This document has no text.",
                                ),
                            ]
                        )
                    )
                else:
                    missing.append(label)
                    skipped.append(
                        {"candidate": name, "file": label, "reason": "file type"}
                    )
                    continue
                used += len(data)
                included.append(label)
            except OverflowError:
                truncated = True
                missing.append(label)
                skipped.append(
                    {"candidate": name, "file": label, "reason": "the pack is full"}
                )
                break
            except Exception as exc:  # noqa: BLE001 — one bad file never sinks the pack
                logger.info("candidate pack: could not include %s: %s", label, exc)
                missing.append(label)
                skipped.append(
                    {"candidate": name, "file": label, "reason": "could not be read"}
                )
        if missing:
            try:
                append(
                    text_pdf(
                        [("heading", f"Not included in this pack — {name}")]
                        + [
                            ("body", f"• {m} (open it in Norm to view)")
                            for m in missing
                        ]
                    )
                )
            except OverflowError:
                truncated = True
        if truncated:
            break

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), {
        "pages": pages(),
        "included": included,
        "skipped": skipped,
        "truncated": truncated,
    }


# -- the sandbox door ----------------------------------------------------------


def pdf_door(db, app, version, user):
    """The sandbox's ``pdf`` object, bound to this app, org and viewer.

    Plain functions only (see ``app_runtime._email_door``): the sandbox blocks
    underscored attributes, not public ones, so an object holding ``db`` must
    never be handed over."""
    from fastapi import HTTPException

    from app.services.app_runtime import file_delete, file_fetch, file_put

    def fetch(file_id):
        try:
            row = file_fetch(
                db, app=app, version=version, user=user, file_id=str(file_id)
            )
        except HTTPException:
            return None
        return bytes(row.data or b""), row.filename, row.content_type

    def candidate_pack(
        candidates,
        filename="Candidate pack.pdf",
        collection=None,
        record_id=None,
        replace_file_id=None,
    ):
        """Build a pack and store it on ``collection``/``record_id``. Returns
        ``{ok, file_id, filename, size_bytes, pages, included, skipped}`` or
        ``{ok: False, message}`` — never raises into the app."""
        if not isinstance(candidates, list) or not candidates:
            return {"ok": False, "message": "a pack needs at least one candidate"}
        if not collection:
            return {"ok": False, "message": "say which record the pack belongs to"}
        try:
            data, summary = build_candidate_pack(candidates, fetch)
        except Exception as exc:  # noqa: BLE001
            logger.warning("candidate pack failed (%s): %s", app.slug, exc)
            return {"ok": False, "message": "the candidate pack could not be built"}
        name = str(filename or "Candidate pack.pdf").strip()
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        try:
            meta = file_put(
                db,
                app=app,
                version=version,
                user=user,
                collection=str(collection),
                record_id=str(record_id) if record_id else None,
                filename=name,
                content_type="application/pdf",
                data=data,
            )
        except HTTPException as exc:
            return {"ok": False, "message": str(exc.detail)}
        if replace_file_id:
            try:
                file_delete(
                    db,
                    app=app,
                    version=version,
                    user=user,
                    file_id=str(replace_file_id),
                )
            except HTTPException:
                pass  # already gone, or never ours: the new pack stands either way
        return {
            "ok": True,
            "file_id": meta["id"],
            "filename": meta["filename"],
            "size_bytes": meta["size_bytes"],
            **summary,
        }

    return SimpleNamespace(candidate_pack=candidate_pack)
