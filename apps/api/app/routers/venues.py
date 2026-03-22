from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import Venue, User
from app.auth.dependencies import get_current_user
from app.services.venue_service import get_user_venues

router = APIRouter()


@router.get("/venues")
async def list_venues(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """List venues accessible to the current user."""
    venues = get_user_venues(db, user.id)
    return {"venues": [{"id": v.id, "name": v.name, "location": v.location, "organization_id": v.organization_id} for v in venues]}
