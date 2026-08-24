"""Extract a draft recipe from an uploaded document.

Reuses the same extraction primitive the invoice pipeline uses
(``llm_interpreter.call_llm`` with a base64 document/image block, and the
generic ``extraction_system_prompt`` envelope). PDFs and images go straight to
Claude, which reads them natively — no local PDF/OCR library. DOCX would need a
converter (python-docx) and is not handled yet.

The output is a loose, human-readable recipe (names/quantities/units as printed)
for the user to REVIEW — it is not resolved to Loaded stock-item / unit ids, and
not saved. Saving it as a NEW Loaded recipe needs a recipe-create path (the Cook
Brothers App's kitchen_loadedhub_update_recipe only UPDATES existing versions).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Media types Claude reads directly, so we can extract with no local library.
SUPPORTED_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/gif",
        "image/webp",
    }
)

RECIPE_SCHEMA = {
    "name": "string — the recipe's name, exactly as printed",
    "yield_quantity": "number or null — how much the recipe makes (a number only)",
    "yield_unit": "string or null — the unit of the yield (e.g. 'kg', 'portions', 'each')",
    "ingredients": [
        {
            "name": "string — the ingredient or sub-recipe name",
            "quantity": "number or null — the amount, as printed",
            "unit": "string or null — the unit (e.g. 'g', 'ml', 'each')",
        }
    ],
    "method": "string or null — the preparation steps, as printed",
}


class RecipeExtractionError(Exception):
    """Recoverable failure extracting a recipe; message goes back to the caller."""


def extract_recipe(
    content_b64: str,
    content_type: str | None,
    db: Session,
    thread_id: str | None = None,
) -> dict:
    """Extract a structured draft recipe from a base64 PDF/image."""
    ct = (content_type or "application/pdf").lower()
    if ct not in SUPPORTED_TYPES:
        raise RecipeExtractionError(
            f"Can't extract from {ct or 'this file'} yet — upload a PDF or an image."
        )

    from app.interpreter.llm_interpreter import call_llm
    from app.services.invoice_extraction import extraction_system_prompt

    is_image = ct.startswith("image/")
    if is_image:
        # Fit a large photo inside Anthropic's per-image limits, same as the chat
        # path — a raw phone photo would otherwise fail the extraction request.
        import base64

        from app.services.attachments import normalize_image_for_anthropic

        raw, ct = normalize_image_for_anthropic(base64.b64decode(content_b64), ct)
        content_b64 = base64.b64encode(raw).decode()
    block = {
        "type": "image" if is_image else "document",
        "source": {"type": "base64", "media_type": ct, "data": content_b64},
    }
    try:
        parsed, _ = call_llm(
            system_prompt=extraction_system_prompt(RECIPE_SCHEMA),
            user_prompt="Extract the recipe from the attached document.",
            db=db,
            thread_id=thread_id,
            call_type="extraction",
            max_tokens=4096,
            documents=[block],
        )
    except Exception as exc:  # noqa: BLE001 — surface a clean message, not a trace
        logger.exception("recipe extraction failed")
        raise RecipeExtractionError(f"Extraction failed: {exc}") from exc

    if not isinstance(parsed, dict) or parsed.get("error"):
        raise RecipeExtractionError("Couldn't read a recipe from that document.")
    parsed.setdefault("ingredients", [])
    return parsed
