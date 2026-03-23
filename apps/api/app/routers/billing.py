"""Billing API endpoints for subscription management."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db.engine import get_db
from app.db.models import User, OrganizationMembership
from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/billing", tags=["billing"])


def _require_org_access(user: User, org_id: str, db: Session) -> OrganizationMembership:
    """Verify user belongs to the org. Returns the membership."""
    membership = db.query(OrganizationMembership).filter(
        OrganizationMembership.user_id == user.id,
        OrganizationMembership.organization_id == org_id,
    ).first()
    if not membership:
        raise HTTPException(403, "Not a member of this organization")
    return membership


def _require_org_owner(user: User, org_id: str, db: Session) -> OrganizationMembership:
    """Verify user is owner or admin of the org."""
    membership = _require_org_access(user, org_id, db)
    if membership.role not in ("owner", "admin"):
        raise HTTPException(403, "Only org owners/admins can manage billing")
    return membership


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@router.get("/{org_id}")
async def get_billing(
    org_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get billing info for the organization."""
    _require_org_access(user, org_id, db)
    from app.services.billing_service import get_billing_info
    return get_billing_info(db, org_id)


# ---------------------------------------------------------------------------
# Setup & Subscribe
# ---------------------------------------------------------------------------

class SetupBody(BaseModel):
    token_plan: str = "basic"


@router.post("/{org_id}/setup")
async def setup_billing(
    org_id: str,
    body: SetupBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create Stripe customer and return SetupIntent for payment method collection."""
    _require_org_owner(user, org_id, db)
    from app.services.billing_service import create_setup_intent
    try:
        client_secret = create_setup_intent(db, org_id)
        db.commit()
        return {"client_secret": client_secret}
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, str(exc))


class SubscribeBody(BaseModel):
    token_plan: str = "basic"


@router.post("/{org_id}/subscribe")
async def subscribe(
    org_id: str,
    body: SubscribeBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create the Stripe subscription after payment method is collected."""
    _require_org_owner(user, org_id, db)
    from app.services.billing_service import create_subscription
    try:
        result = create_subscription(db, org_id, body.token_plan)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# Plan changes
# ---------------------------------------------------------------------------

class ChangePlanBody(BaseModel):
    token_plan: str


@router.put("/{org_id}/plan")
async def update_plan(
    org_id: str,
    body: ChangePlanBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Change the token plan (prorated)."""
    _require_org_owner(user, org_id, db)
    from app.services.billing_service import change_plan
    try:
        result = change_plan(db, org_id, body.token_plan)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# Agent enablement
# ---------------------------------------------------------------------------

class AgentBody(BaseModel):
    hr: bool | None = None
    procurement: bool | None = None


@router.put("/{org_id}/agents")
async def update_agents(
    org_id: str,
    body: AgentBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Enable/disable paid agents."""
    _require_org_owner(user, org_id, db)
    from app.db.models import Organization
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, "Organization not found")
    if body.hr is not None:
        org.hr_agent_enabled = body.hr
    if body.procurement is not None:
        org.procurement_agent_enabled = body.procurement
    db.flush()
    db.commit()
    from app.services.billing_service import get_billing_info
    return get_billing_info(db, org_id)


# ---------------------------------------------------------------------------
# Top-ups
# ---------------------------------------------------------------------------

class TopUpBody(BaseModel):
    units: int = 1  # each unit = 500K tokens for $10


@router.post("/{org_id}/topup")
async def top_up(
    org_id: str,
    body: TopUpBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Purchase additional tokens."""
    _require_org_owner(user, org_id, db)
    from app.services.billing_service import purchase_top_up
    try:
        result = purchase_top_up(db, org_id, user.id, body.units)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# Payment method
# ---------------------------------------------------------------------------

@router.post("/{org_id}/payment-method")
async def update_payment_method(
    org_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get a new SetupIntent to update the payment method."""
    _require_org_owner(user, org_id, db)
    from app.services.billing_service import create_setup_intent
    try:
        client_secret = create_setup_intent(db, org_id)
        db.commit()
        return {"client_secret": client_secret}
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------

@router.get("/{org_id}/invoices")
async def list_invoices(
    org_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List recent invoices from Stripe."""
    _require_org_access(user, org_id, db)
    from app.db.models import Subscription

    sub = db.query(Subscription).filter(
        Subscription.organization_id == org_id,
    ).first()
    if not sub or not sub.stripe_customer_id:
        return {"invoices": []}

    try:
        import stripe
        stripe.api_key = settings.STRIPE_SECRET_KEY
        invoices = stripe.Invoice.list(customer=sub.stripe_customer_id, limit=12)
        return {"invoices": [
            {
                "id": inv.id,
                "amount_due": inv.amount_due,
                "amount_paid": inv.amount_paid,
                "currency": inv.currency,
                "status": inv.status,
                "created": inv.created,
                "invoice_pdf": inv.invoice_pdf,
                "hosted_invoice_url": inv.hosted_invoice_url,
            }
            for inv in invoices.data
        ]}
    except Exception as exc:
        raise HTTPException(400, f"Failed to fetch invoices: {exc}")


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

@router.delete("/{org_id}/subscription")
async def cancel_subscription(
    org_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cancel subscription at end of billing period."""
    _require_org_owner(user, org_id, db)
    from app.db.models import Subscription

    sub = db.query(Subscription).filter(
        Subscription.organization_id == org_id,
    ).first()
    if not sub or not sub.stripe_subscription_id:
        raise HTTPException(400, "No active subscription")

    try:
        import stripe
        stripe.api_key = settings.STRIPE_SECRET_KEY
        stripe.Subscription.modify(
            sub.stripe_subscription_id,
            cancel_at_period_end=True,
        )
        from app.services.billing_service import _log_event
        _log_event(db, org_id, "subscription_canceled")
        db.commit()
        return {"status": "canceling_at_period_end"}
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
