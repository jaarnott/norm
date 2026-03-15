from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import Venue, User
from app.auth.dependencies import get_current_user

router = APIRouter()


@router.get("/venues")
async def list_venues(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    venues = db.query(Venue).all()
    return {"venues": [{"id": v.id, "name": v.name, "location": v.location} for v in venues]}
