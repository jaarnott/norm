"""Stripe webhook handler — no JWT auth, uses Stripe signature verification."""

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.orm import Session

from app.db.engine import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["billing-webhooks"])


@router.post("/billing/webhooks")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events."""
    import stripe

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    from app.config import settings
    webhook_secret = settings.STRIPE_WEBHOOK_SECRET

    if not webhook_secret:
        raise HTTPException(500, "STRIPE_WEBHOOK_SECRET not configured")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(400, "Invalid signature")
    except Exception as exc:
        raise HTTPException(400, f"Webhook error: {exc}")

    db = SessionLocal()
    try:
        _handle_event(event, db)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to process webhook event: %s", event["type"])
    finally:
        db.close()

    return {"received": True}


def _handle_event(event: dict, db: Session) -> None:
    """Dispatch Stripe events to handlers."""
    from app.db.models import Subscription, TokenTopUp, BillingEvent

    event_type = event["type"]
    data = event["data"]["object"]

    # Deduplicate
    existing = db.query(BillingEvent).filter(
        BillingEvent.stripe_event_id == event["id"],
    ).first()
    if existing:
        logger.info("Duplicate webhook event: %s", event["id"])
        return

    if event_type == "invoice.payment_succeeded":
        customer_id = data.get("customer")
        sub = db.query(Subscription).filter(
            Subscription.stripe_customer_id == customer_id,
        ).first()
        if sub:
            sub.status = "active"
            # Update billing cycle start from the subscription period
            period_start = data.get("period_start")
            if period_start:
                sub.billing_cycle_start = datetime.fromtimestamp(period_start, tz=timezone.utc)
            db.add(BillingEvent(
                organization_id=sub.organization_id,
                event_type="payment_succeeded",
                stripe_event_id=event["id"],
                details={"amount": data.get("amount_paid"), "invoice_id": data.get("id")},
            ))

    elif event_type == "invoice.payment_failed":
        customer_id = data.get("customer")
        sub = db.query(Subscription).filter(
            Subscription.stripe_customer_id == customer_id,
        ).first()
        if sub:
            sub.status = "past_due"
            db.add(BillingEvent(
                organization_id=sub.organization_id,
                event_type="payment_failed",
                stripe_event_id=event["id"],
                details={"attempt_count": data.get("attempt_count")},
            ))

    elif event_type == "customer.subscription.deleted":
        sub_id = data.get("id")
        sub = db.query(Subscription).filter(
            Subscription.stripe_subscription_id == sub_id,
        ).first()
        if sub:
            sub.status = "canceled"
            db.add(BillingEvent(
                organization_id=sub.organization_id,
                event_type="subscription_canceled",
                stripe_event_id=event["id"],
            ))

    elif event_type == "customer.subscription.updated":
        sub_id = data.get("id")
        sub = db.query(Subscription).filter(
            Subscription.stripe_subscription_id == sub_id,
        ).first()
        if sub:
            status = data.get("status")
            if status:
                sub.status = status
            # Sync billing cycle
            period_start = data.get("current_period_start")
            if period_start:
                sub.billing_cycle_start = datetime.fromtimestamp(period_start, tz=timezone.utc)
            db.add(BillingEvent(
                organization_id=sub.organization_id,
                event_type="subscription_updated",
                stripe_event_id=event["id"],
                details={"status": status},
            ))

    elif event_type == "payment_intent.succeeded":
        # Check if this is a top-up payment
        pi_id = data.get("id")
        topup = db.query(TokenTopUp).filter(
            TokenTopUp.stripe_payment_intent_id == pi_id,
        ).first()
        if topup:
            topup.status = "completed"

    elif event_type == "payment_intent.failed":
        pi_id = data.get("id")
        topup = db.query(TokenTopUp).filter(
            TokenTopUp.stripe_payment_intent_id == pi_id,
        ).first()
        if topup:
            topup.status = "failed"
            db.add(BillingEvent(
                organization_id=topup.organization_id,
                event_type="topup_failed",
                stripe_event_id=event["id"],
                details={"payment_intent_id": pi_id},
            ))

    elif event_type == "setup_intent.succeeded":
        # Update payment method info
        customer_id = data.get("customer")
        pm_id = data.get("payment_method")
        sub = db.query(Subscription).filter(
            Subscription.stripe_customer_id == customer_id,
        ).first()
        if sub and pm_id:
            import stripe as stripe_mod
            stripe_mod.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
            try:
                pm = stripe_mod.PaymentMethod.retrieve(pm_id)
                card = pm.get("card", {})
                sub.payment_method_last4 = card.get("last4")
                sub.payment_method_brand = card.get("brand")
                # Set as default payment method on customer
                stripe_mod.Customer.modify(
                    customer_id,
                    invoice_settings={"default_payment_method": pm_id},
                )
            except Exception:
                logger.exception("Failed to update payment method from SetupIntent")

    else:
        logger.debug("Unhandled webhook event type: %s", event_type)
