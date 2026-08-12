"""Document uploads — a Norm-wide capability.

``POST /uploads`` accepts a file (multipart), stores the bytes in the DB
(UploadedDocument), and returns a handle. ``extraction_target`` says what the
upload is for, so a caller can route it to the right extractor. The first
extractor is recipes: ``POST /uploads/{id}/extract-recipe`` pulls the bytes back
and runs the recipe extractor, returning a structured draft for review.

The only in-repo binary-store precedent is the admin Spec-Dojo sample upload
(bytes in the DB); this generalises it to any user and any document.
"""

import base64
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.engine import get_db
from app.db.models import UploadedDocument, User
from app.services.recipe_extraction import RecipeExtractionError, extract_recipe

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_BYTES = (
    15 * 1024 * 1024
)  # 15 MB — recipe docs are small; the DB is not a blob store.


@router.post("/uploads")
async def upload_document(
    file: UploadFile = File(...),
    extraction_target: str = Form(""),
    venue_id: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="The file is empty.")
    if len(content) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 15 MB).")

    doc = UploadedDocument(
        user_id=user.id,
        venue_id=venue_id or None,
        filename=file.filename,
        content_type=file.content_type,
        size=len(content),
        data=content,
        extraction_target=extraction_target or None,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    logger.info(
        "upload_document",
        extra={
            "id": doc.id,
            "size": doc.size,
            "target": doc.extraction_target,
            "user_id": user.id,
        },
    )
    return {
        "id": doc.id,
        "filename": doc.filename,
        "content_type": doc.content_type,
        "size": doc.size,
        "extraction_target": doc.extraction_target,
    }


@router.post("/uploads/{doc_id}/extract-recipe")
def extract_recipe_from_upload(
    doc_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Extract a draft recipe from an uploaded document (the uploader's own)."""
    doc = (
        db.query(UploadedDocument)
        .filter(UploadedDocument.id == doc_id, UploadedDocument.user_id == user.id)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Upload not found.")
    b64 = base64.b64encode(doc.data).decode()
    try:
        recipe = extract_recipe(b64, doc.content_type, db)
    except RecipeExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"recipe": recipe}
