from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import User
from app.auth.dependencies import get_current_user
from app.services.supervisor import handle_message

router = APIRouter()


class MessageRequest(BaseModel):
    message: str
    task_id: str | None = None


@router.post("/messages")
async def post_message(
    req: MessageRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return handle_message(req.message, db, user_id=user.id, task_id=req.task_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        # Surface Anthropic auth / connection errors clearly
        exc_name = type(exc).__name__
        if "AuthenticationError" in exc_name:
            raise HTTPException(
                status_code=502,
                detail="Anthropic API key is invalid or missing. Check your ANTHROPIC_API_KEY setting.",
            )
        if "APIConnectionError" in exc_name:
            raise HTTPException(
                status_code=502,
                detail="Could not connect to Anthropic API. Check your network and API configuration.",
            )
        raise
