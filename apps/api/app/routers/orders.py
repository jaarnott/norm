from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import User
from app.auth.dependencies import get_current_user
from app.services.order_service import (
    get_order,
    approve_order,
    reject_order,
    submit_order,
    list_orders,
)

router = APIRouter()


@router.get("/orders")
async def get_orders(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return {"orders": list_orders(db, user.id)}


@router.get("/orders/{order_id}")
async def get_order_detail(
    order_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    order = get_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("/orders/{order_id}/approve")
async def approve(
    order_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    order = approve_order(db, order_id, user=user)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("/orders/{order_id}/reject")
async def reject(
    order_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    order = reject_order(db, order_id, user=user)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("/orders/{order_id}/submit")
async def submit(
    order_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    order = submit_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found or not approved")
    return order
