"""Billing service — Stripe integration, quota checks, subscription management."""

import logging
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy import func

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing constants
# ---------------------------------------------------------------------------

PLAN_QUOTAS = {
    "basic":    {"price_cents": 5000,  "tokens": 1_000_000},
    "standard": {"price_cents": 10000, "tokens": 3_000_000},
    "max":      {"price_cents": 20000, "tokens": 10_000_000},
}

VENUE_PRICE_CENTS = 1000       # $10/month per venue
AGENT_PRICES_CENTS = {
    "hr": 1000,                # $10/month
    "procurement": 500,        # $5/month
    "reports": 0,              # free
}
TOPUP_TOKENS = 500_000         # per top-up unit
TOPUP_PRICE_CENTS = 1000       # $10 per top-up unit


class QuotaExceededError(Exception):
    """Raised when an org has exhausted its token quota."""

    def __init__(self, used: int, quota: int):
        self.used = used
        self.quota = quota
        super().__init__(f"Token quota exceeded: {used:,}/{quota:,}")


# ---------------------------------------------------------------------------
# Quota checking
# ---------------------------------------------------------------------------

def _is_enforcement_enabled() -> bool:
    return os.environ.get("BILLING_ENFORCEMENT", "false").lower() == "true"


def get_monthly_usage(db: Session, org_id: str, cycle_start: datetime | None = None) -> int:
    """Sum total tokens for the current billing period."""
    from app.db.models import TokenUsage

    if cycle_start:
        start_date = cycle_start.strftime("%Y-%m-%d")
    else:
        # Default to start of current month
        start_date = datetime.now(timezone.utc).strftime("%Y-%m-01")

    rows = (
        db.query(func.sum(TokenUsage.input_tokens + TokenUsage.output_tokens))
        .filter(
            TokenUsage.organization_id == org_id,
            TokenUsage.date >= start_date,
        )
        .scalar()
    )
    return rows or 0


def get_available_topup_tokens(db: Session, org_id: str, cycle_start: datetime | None = None) -> int:
    """Sum tokens from completed top-ups in the current billing period."""
    from app.db.models import TokenTopUp

    query = db.query(func.sum(TokenTopUp.tokens)).filter(
        TokenTopUp.organization_id == org_id,
        TokenTopUp.status == "completed",
    )
    if cycle_start:
        query = query.filter(TokenTopUp.created_at >= cycle_start)
    return query.scalar() or 0


def check_quota(db: Session, org_id: str) -> dict:
    """Check token quota for an organization.

    Returns {"allowed": bool, "used": int, "quota": int, "remaining": int}.
    """
    from app.db.models import Subscription

    sub = db.query(Subscription).filter(
        Subscription.organization_id == org_id,
    ).first()

    if not sub:
        if _is_enforcement_enabled():
            return {"allowed": False, "used": 0, "quota": 0, "remaining": 0}
        return {"allowed": True, "used": 0, "quota": 0, "remaining": 0}

    # Canceled or past-due subscriptions cannot use tokens
    if sub.status in ("canceled", "past_due"):
        used = get_monthly_usage(db, org_id, sub.billing_cycle_start)
        return {"allowed": False, "used": used, "quota": sub.token_quota, "remaining": 0}

    if sub.status == "trialing":
        used = get_monthly_usage(db, org_id, sub.billing_cycle_start)
        return {"allowed": True, "used": used, "quota": sub.token_quota, "remaining": max(0, sub.token_quota - used)}

    used = get_monthly_usage(db, org_id, sub.billing_cycle_start)
    topup = get_available_topup_tokens(db, org_id, sub.billing_cycle_start)
    total_quota = sub.token_quota + topup
    remaining = max(0, total_quota - used)
    allowed = used < total_quota

    return {"allowed": allowed, "used": used, "quota": total_quota, "remaining": remaining}


def check_quota_for_user(db: Session, user_id: str | None) -> None:
    """Check quota for a user's organization. Raises QuotaExceededError if over limit."""
    if not user_id or not _is_enforcement_enabled():
        return

    from app.db.models import OrganizationMembership

    membership = db.query(OrganizationMembership).filter(
        OrganizationMembership.user_id == user_id,
    ).first()
    if not membership:
        return

    result = check_quota(db, membership.organization_id)
    if not result["allowed"]:
        raise QuotaExceededError(result["used"], result["quota"])


# ---------------------------------------------------------------------------
# Billing info
# ---------------------------------------------------------------------------

def get_billing_info(db: Session, org_id: str) -> dict:
    """Get comprehensive billing info for the dashboard."""
    from app.db.models import Organization, Subscription

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        return {}

    sub = db.query(Subscription).filter(
        Subscription.organization_id == org_id,
    ).first()

    venue_count = len(org.venues) if org.venues else 0
    quota_info = check_quota(db, org_id)

    # Calculate monthly cost
    plan_cost = PLAN_QUOTAS.get(sub.token_plan, {}).get("price_cents", 0) if sub else 0
    agent_cost = 0
    if org.hr_agent_enabled:
        agent_cost += AGENT_PRICES_CENTS["hr"]
    if org.procurement_agent_enabled:
        agent_cost += AGENT_PRICES_CENTS["procurement"]
    venue_cost = venue_count * VENUE_PRICE_CENTS

    return {
        "subscription": {
            "token_plan": sub.token_plan if sub else None,
            "token_quota": sub.token_quota if sub else 0,
            "status": sub.status if sub else None,
            "billing_cycle_start": sub.billing_cycle_start.isoformat() if sub and sub.billing_cycle_start else None,
            "payment_method_last4": sub.payment_method_last4 if sub else None,
            "payment_method_brand": sub.payment_method_brand if sub else None,
        },
        "usage": quota_info,
        "agents": {
            "hr": org.hr_agent_enabled,
            "procurement": org.procurement_agent_enabled,
            "reports": org.reports_agent_enabled,
        },
        "venue_count": venue_count,
        "monthly_cost_cents": plan_cost + agent_cost + venue_cost,
        "cost_breakdown": {
            "plan": plan_cost,
            "agents": agent_cost,
            "venues": venue_cost,
        },
    }


# ---------------------------------------------------------------------------
# Stripe operations
# ---------------------------------------------------------------------------

def _get_stripe():
    """Lazy import and configure stripe."""
    import stripe
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    return stripe


def get_or_create_stripe_customer(db: Session, org_id: str) -> str:
    """Create a Stripe Customer for the org if one doesn't exist. Returns customer ID."""
    from app.db.models import Organization, Subscription

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise ValueError(f"Organization not found: {org_id}")

    sub = db.query(Subscription).filter(
        Subscription.organization_id == org_id,
    ).first()

    if sub and sub.stripe_customer_id:
        return sub.stripe_customer_id

    stripe = _get_stripe()
    customer = stripe.Customer.create(
        name=org.name,
        email=org.billing_email,
        metadata={"org_id": org.id, "org_slug": org.slug},
    )

    if not sub:
        sub = Subscription(
            organization_id=org_id,
            stripe_customer_id=customer.id,
            token_plan="basic",
            token_quota=PLAN_QUOTAS["basic"]["tokens"],
            status="trialing",
        )
        db.add(sub)
    else:
        sub.stripe_customer_id = customer.id

    db.flush()
    return customer.id


def create_setup_intent(db: Session, org_id: str) -> str:
    """Create a Stripe SetupIntent for collecting a payment method. Returns client_secret."""
    customer_id = get_or_create_stripe_customer(db, org_id)
    stripe = _get_stripe()
    intent = stripe.SetupIntent.create(
        customer=customer_id,
        payment_method_types=["card"],
    )
    return intent.client_secret


def create_subscription(db: Session, org_id: str, token_plan: str) -> dict:
    """Create a Stripe Subscription for the org."""
    from app.db.models import Organization, Subscription

    org = db.query(Organization).filter(Organization.id == org_id).first()
    sub = db.query(Subscription).filter(
        Subscription.organization_id == org_id,
    ).first()

    if not sub or not sub.stripe_customer_id:
        raise ValueError("Stripe customer not set up. Call setup first.")

    if not sub.payment_method_last4:
        raise ValueError("Add a payment method before subscribing. Complete the card setup first.")

    if sub.stripe_subscription_id:
        raise ValueError("Subscription already exists. Use change_plan instead.")

    plan_config = PLAN_QUOTAS.get(token_plan)
    if not plan_config:
        raise ValueError(f"Invalid plan: {token_plan}")

    stripe = _get_stripe()
    items = []

    # Token plan
    price_id = os.environ.get(f"STRIPE_PRICE_{token_plan.upper()}")
    if price_id:
        items.append({"price": price_id})

    # Agents
    if org.hr_agent_enabled:
        hr_price = os.environ.get("STRIPE_PRICE_HR")
        if hr_price:
            items.append({"price": hr_price})
    if org.procurement_agent_enabled:
        proc_price = os.environ.get("STRIPE_PRICE_PROCUREMENT")
        if proc_price:
            items.append({"price": proc_price})

    # Venues
    venue_count = len(org.venues) if org.venues else 0
    if venue_count > 0:
        venue_price = os.environ.get("STRIPE_PRICE_VENUE")
        if venue_price:
            items.append({"price": venue_price, "quantity": venue_count})

    if not items:
        raise ValueError("No Stripe price IDs configured. Set STRIPE_PRICE_* env vars.")

    stripe_sub = stripe.Subscription.create(
        customer=sub.stripe_customer_id,
        items=items,
        metadata={"org_id": org_id},
    )

    sub.stripe_subscription_id = stripe_sub.id
    sub.token_plan = token_plan
    sub.token_quota = plan_config["tokens"]
    sub.status = "active"
    sub.billing_cycle_start = datetime.fromtimestamp(
        stripe_sub.current_period_start, tz=timezone.utc,
    )
    db.flush()

    _log_event(db, org_id, "subscription_created", {"plan": token_plan})
    return get_billing_info(db, org_id)


def change_plan(db: Session, org_id: str, new_plan: str) -> dict:
    """Change the token plan on an existing subscription."""
    from app.db.models import Subscription

    sub = db.query(Subscription).filter(
        Subscription.organization_id == org_id,
    ).first()
    if not sub or not sub.stripe_subscription_id:
        raise ValueError("No active subscription")

    plan_config = PLAN_QUOTAS.get(new_plan)
    if not plan_config:
        raise ValueError(f"Invalid plan: {new_plan}")

    stripe = _get_stripe()
    stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)

    # Find the plan item and update it
    new_price_id = os.environ.get(f"STRIPE_PRICE_{new_plan.upper()}")
    if not new_price_id:
        raise ValueError(f"No Stripe price configured for plan: {new_plan}")

    # Find the current plan item (first item that matches a plan price)
    plan_prices = {
        os.environ.get(f"STRIPE_PRICE_{p.upper()}")
        for p in PLAN_QUOTAS
        if os.environ.get(f"STRIPE_PRICE_{p.upper()}")
    }
    plan_item = None
    for item in stripe_sub["items"]["data"]:
        if item["price"]["id"] in plan_prices:
            plan_item = item
            break

    if plan_item:
        stripe.Subscription.modify(
            sub.stripe_subscription_id,
            items=[{"id": plan_item["id"], "price": new_price_id}],
            proration_behavior="create_prorations",
        )

    old_plan = sub.token_plan
    sub.token_plan = new_plan
    sub.token_quota = plan_config["tokens"]
    db.flush()

    _log_event(db, org_id, "plan_changed", {"from": old_plan, "to": new_plan})
    return get_billing_info(db, org_id)


def purchase_top_up(db: Session, org_id: str, user_id: str | None, units: int = 1) -> dict:
    """Purchase additional tokens. Each unit = 500K tokens for $10."""
    from app.db.models import Subscription, TokenTopUp

    sub = db.query(Subscription).filter(
        Subscription.organization_id == org_id,
    ).first()
    if not sub or not sub.stripe_customer_id:
        raise ValueError("No billing set up for this organization")

    tokens = units * TOPUP_TOKENS
    amount_cents = units * TOPUP_PRICE_CENTS

    stripe = _get_stripe()
    intent = stripe.PaymentIntent.create(
        amount=amount_cents,
        currency="usd",
        customer=sub.stripe_customer_id,
        metadata={"org_id": org_id, "tokens": tokens, "type": "topup"},
        confirm=True,
        automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
    )

    topup = TokenTopUp(
        organization_id=org_id,
        tokens=tokens,
        amount_cents=amount_cents,
        stripe_payment_intent_id=intent.id,
        status="completed" if intent.status == "succeeded" else "pending",
        purchased_by=user_id,
    )
    db.add(topup)
    db.flush()

    _log_event(db, org_id, "topup_purchased", {"tokens": tokens, "amount_cents": amount_cents})
    return {"tokens": tokens, "amount_cents": amount_cents, "status": topup.status}


def sync_venue_count(db: Session, org_id: str) -> None:
    """Update the venue quantity on the Stripe subscription."""
    from app.db.models import Organization, Subscription

    org = db.query(Organization).filter(Organization.id == org_id).first()
    sub = db.query(Subscription).filter(
        Subscription.organization_id == org_id,
    ).first()

    if not sub or not sub.stripe_subscription_id:
        return

    venue_count = len(org.venues) if org.venues else 0
    venue_price = os.environ.get("STRIPE_PRICE_VENUE")
    if not venue_price:
        return

    stripe = _get_stripe()
    try:
        stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)
        for item in stripe_sub["items"]["data"]:
            if item["price"]["id"] == venue_price:
                stripe.SubscriptionItem.modify(item["id"], quantity=venue_count)
                return
    except Exception:
        logger.exception("Failed to sync venue count with Stripe")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log_event(db: Session, org_id: str, event_type: str, details: dict | None = None, stripe_event_id: str | None = None):
    from app.db.models import BillingEvent
    db.add(BillingEvent(
        organization_id=org_id,
        event_type=event_type,
        stripe_event_id=stripe_event_id,
        details=details,
    ))
    db.flush()
