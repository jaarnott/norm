"""Recipe editor save route — the web front door for recipe writes.

Reads for the recipe editor go through the generic component-API route
(recipe_editor/list_recipes, get_recipe, list_stock_items, list_units — all
direct loadedhub). The SAVE is the one write, and it can't ride the component-API
path: it goes through the Cook Brothers App MCP connector, not a loadedhub HTTP
call. Both this route and the MCP ``norm__save_recipe`` app tool call the single
``recipe_save.save_recipe`` writer.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.engine import get_config_db, get_db
from app.db.models import User
from app.services.recipe_save import RecipeSaveError, save_recipe
from app.services.venue_service import user_can_access_venue

logger = logging.getLogger(__name__)

router = APIRouter()


class SaveRecipeRequest(BaseModel):
    venue_id: str
    recipe: dict


@router.post("/recipe-editor/save")
def save_recipe_route(
    req: SaveRecipeRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    if not user_can_access_venue(db, user.id, req.venue_id):
        raise HTTPException(
            status_code=403, detail="You don't have access to that venue."
        )
    try:
        return save_recipe(req.venue_id, req.recipe, db, config_db)
    except RecipeSaveError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
