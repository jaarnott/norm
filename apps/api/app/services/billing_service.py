"""Billing service — Stripe integration, quota checks, subscription management."""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import settings
from app.services import active_org

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing constants
# ---------------------------------------------------------------------------

# Each plan includes a month of usage, metered as what Norm's model calls
# actually cost (TokenUsage.cost_usd, USD). Customers never see tokens or
# dollars of usage: they see a meter (percent of the month's allowance) and
# how much more usage a bigger plan gives (`multiple`, vs Core). Oct 2026.
PLANS = {
    "core": {"name": "Core", "price_cents": 4900, "usage_usd": 25, "multiple": 1},
    "pro": {"name": "Pro", "price_cents": 14900, "usage_usd": 100, "multiple": 4},
    "max": {"name": "Max", "price_cents": 29900, "usage_usd": 275, "multiple": 11},
}
DEFAULT_PLAN = "core"
# The token plans these replaced; a row still carrying an old name reads as
# its successor (the 2026-10 migration renames them).
LEGACY_PLANS = {"basic": "core", "standard": "pro"}

# Every hired AI team member (tier='agent') bills the same: one Stripe price,
# quantity = members hired. Reports and App Builder are included.
AGENT_PRICE_CENTS = 1000

# A top-up adds a Core-sized month of usage until the cycle resets.
TOPUP_PRICE_CENTS = 3000
TOPUP_USAGE_USD = 25

# Past its allowance, an organisation's conversations carry on with the light
# model instead of stopping (see light_mode_for_thread).
FALLBACK_MODEL = "claude-haiku-4-5-20251001"


def plan_for(name: str | None) -> dict | None:
    """The plan a stored name means, old token-plan names included."""
    if not name:
        return None
    key = LEGACY_PLANS.get(name, name)
    plan = PLANS.get(key)
    return {"id": key, **plan} if plan else None


def get_agent_apps(db: Session, org_id: str, config_db=None) -> list[dict]:
    """The catalog's agent-bundle apps with this org's entitlement state.

    Hierarchy v2: the billable team members are the ``tier='agent'`` catalog
    rows. The marketplace entitlement drives BOTH access (the hired-member gates)
    and the charge, so they can never disagree — it replaced the three
    ``Organization.*_agent_enabled`` booleans. Prices live on the rows.
    """
    from app.db.config_models import MarketplaceApp
    from app.services.entitlements import entitled_slugs

    owns_config_db = config_db is None
    if owns_config_db:
        from app.db.engine import _ConfigSessionLocal

        config_db = _ConfigSessionLocal()
    try:
        active = (
            config_db.query(MarketplaceApp)
            .filter(MarketplaceApp.status == "active")
            .order_by(MarketplaceApp.slug)
            .all()
        )
        rows = [a for a in active if a.tier == "agent"]
        entitled = entitled_slugs(org_id, db, config_db)
        return [
            {
                "slug": a.slug,
                "name": a.name,
                "key": a.stripe_price_key or a.slug,
                "price_cents": a.price_cents or 0,
                "enabled": a.slug in entitled,
            }
            for a in rows
        ]
    finally:
        if owns_config_db:
            config_db.close()


def get_priced_apps(db: Session, org_id: str, config_db=None) -> list[dict]:
    """Priced Apps that are ON for this org — the second billing line.

    Every App has an enabled state; most are bundled with their team member
    and free, but an App may carry its own price (hierarchy v2). Charged iff
    it is actually on: used by a hired member AND entitled — the same
    ``apps_on`` resolver the tool/page gates read, so access and the charge
    ride one switch here too. Empty while gating is inactive."""
    from app.db.config_models import MarketplaceApp
    from app.services.entitlements import apps_on

    owns_config_db = config_db is None
    if owns_config_db:
        from app.db.engine import _ConfigSessionLocal

        config_db = _ConfigSessionLocal()
    try:
        on = apps_on(org_id, db, config_db)
        if not on:
            return []
        rows = (
            config_db.query(MarketplaceApp)
            .filter(MarketplaceApp.status == "active")
            .order_by(MarketplaceApp.slug)
            .all()
        )
        return [
            {
                "slug": a.slug,
                "name": a.name,
                "key": a.stripe_price_key or a.slug,
                "price_cents": a.price_cents or 0,
                "enabled": True,
            }
            for a in rows
            if a.tier in ("app", "user") and a.slug in on and (a.price_cents or 0) > 0
        ]
    finally:
        if owns_config_db:
            config_db.close()


class QuotaExceededError(Exception):
    """Raised when an org may not use Norm at all: its subscription is
    cancelled or past due (or, with enforcement on, it has none). Running past
    the month's allowance never raises — Norm switches to the light model."""

    def __init__(self, used: float, quota: float):
        self.used = used
        self.quota = quota
        super().__init__(f"Billing inactive (usage ${used:.2f} of ${quota:.2f})")


# ---------------------------------------------------------------------------
# Quota checking
# ---------------------------------------------------------------------------


def _is_enforcement_enabled() -> bool:
    return settings.BILLING_ENFORCEMENT


def get_monthly_usage(
    db: Session, org_id: str, cycle_start: datetime | None = None
) -> int:
    """Billable tokens used in the current billing period.

    Billable tokens, not input + output (decided 7 Oct 2026): input + output
    left out every cached token, so once conversations were cached a month
    counted little more than its output. Days recorded before billable tokens
    existed count input + output, as they always did. See llm_cost.py.
    """
    from app.db.models import TokenUsage

    if cycle_start:
        start_date = cycle_start.strftime("%Y-%m-%d")
    else:
        # Default to start of current month
        start_date = datetime.now(timezone.utc).strftime("%Y-%m-01")

    rows = (
        db.query(
            func.sum(
                func.coalesce(
                    func.nullif(TokenUsage.billable_tokens, 0),
                    TokenUsage.input_tokens + TokenUsage.output_tokens,
                )
            )
        )
        .filter(
            TokenUsage.organization_id == org_id,
            TokenUsage.date >= start_date,
        )
        .scalar()
    )
    return rows or 0


def _cycle_start_date(cycle_start: datetime | None) -> str:
    if cycle_start:
        return cycle_start.strftime("%Y-%m-%d")
    return datetime.now(timezone.utc).strftime("%Y-%m-01")


def get_monthly_cost_usd(
    db: Session, org_id: str, cycle_start: datetime | None = None
) -> float:
    """What Norm's model calls cost this org in the current period (USD) —
    the figure the plan allowance is measured against."""
    from app.db.models import TokenUsage

    total = (
        db.query(func.sum(TokenUsage.cost_usd))
        .filter(
            TokenUsage.organization_id == org_id,
            TokenUsage.date >= _cycle_start_date(cycle_start),
        )
        .scalar()
    )
    return float(total or 0)


def get_topup_usage_usd(
    db: Session, org_id: str, cycle_start: datetime | None = None
) -> float:
    """Usage added by completed top-ups in the current period (USD)."""
    from app.db.models import TokenTopUp

    query = db.query(func.sum(TokenTopUp.usage_usd)).filter(
        TokenTopUp.organization_id == org_id,
        TokenTopUp.status == "completed",
    )
    if cycle_start:
        query = query.filter(TokenTopUp.created_at >= cycle_start)
    return float(query.scalar() or 0)


def get_available_topup_tokens(
    db: Session, org_id: str, cycle_start: datetime | None = None
) -> int:
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
    """Where an org stands against its plan this period.

    ``allowed`` is False only when it may not use Norm at all (cancelled or
    past due; no subscription while enforcement is on). ``light_mode`` is True
    once usage passes the allowance (plan + top-ups): Norm keeps working on
    FALLBACK_MODEL until the period resets, a top-up, or an upgrade.
    """
    from app.db.models import Subscription

    sub = db.query(Subscription).filter(Subscription.organization_id == org_id).first()
    plan = plan_for(sub.token_plan if sub else None) or plan_for(DEFAULT_PLAN)
    cycle_start = sub.billing_cycle_start if sub else None

    used = get_monthly_cost_usd(db, org_id, cycle_start)
    topups = get_topup_usage_usd(db, org_id, cycle_start) if sub else 0.0
    allowance = float(plan["usage_usd"]) + topups

    if not sub:
        allowed = not _is_enforcement_enabled()
    else:
        allowed = sub.status not in ("canceled", "past_due")

    return {
        "allowed": allowed,
        "light_mode": allowed and used >= allowance,
        "plan": plan["id"],
        "used_usd": round(used, 4),
        "allowance_usd": round(allowance, 2),
        "remaining_usd": round(max(0.0, allowance - used), 4),
        "percent": round(100 * used / allowance, 1) if allowance else 0.0,
    }


def _org_for_thread(db: Session, thread_id: str) -> str | None:
    """The organisation a conversation belongs to: its venue's, else its
    starter's membership."""
    from app.db.models import Thread, Venue

    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if thread is None:
        return None
    if thread.organization_id:
        return thread.organization_id
    if thread.venue_id:
        venue = db.query(Venue).filter(Venue.id == thread.venue_id).first()
        if venue is not None:
            return venue.organization_id
    if thread.user_id:
        membership = active_org.membership_for(db, thread.user_id)
        if membership is not None:
            return membership.organization_id
    return None


def light_mode_for_thread(db: Session, thread_id: str) -> bool:
    """True when this conversation's organisation has used its month's
    allowance, so its next model call should use FALLBACK_MODEL. Off while
    billing enforcement is off; never raises (a billing lookup must not stop
    a conversation)."""
    if not _is_enforcement_enabled():
        return False
    try:
        org_id = _org_for_thread(db, thread_id)
        return bool(org_id) and check_quota(db, org_id)["light_mode"]
    except Exception:
        logger.exception("Light-mode check failed for thread %s", thread_id)
        return False


def check_quota_for_user(db: Session, user_id: str | None) -> None:
    """Check quota for a user's organization. Raises QuotaExceededError if over limit."""
    if not user_id or not _is_enforcement_enabled():
        return

    membership = active_org.membership_for(db, user_id)
    if not membership:
        return

    result = check_quota(db, membership.organization_id)
    if not result["allowed"]:
        raise QuotaExceededError(result["used_usd"], result["allowance_usd"])


# ---------------------------------------------------------------------------
# Billing info
# ---------------------------------------------------------------------------


def get_billing_info(db: Session, org_id: str, config_db=None) -> dict:
    """Billing for the dashboard: plan, the usage meter, the team and cost."""
    from app.db.models import Organization, Subscription

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        return {}

    sub = db.query(Subscription).filter(Subscription.organization_id == org_id).first()
    plan = plan_for(sub.token_plan if sub else None)
    usage = check_quota(db, org_id)
    # Billing says Norm is on the light model only when it really is: the
    # switch (light_mode_for_thread) is off while enforcement is off.
    usage["light_mode"] = usage["light_mode"] and _is_enforcement_enabled()

    # Team members and priced Apps both come from the marketplace catalog;
    # the same resolvers the access gates read, so the bill matches access.
    plan_cost = plan["price_cents"] if plan else 0
    agent_apps = get_agent_apps(db, org_id, config_db)
    agent_cost = sum(a["price_cents"] for a in agent_apps if a["enabled"])
    priced_apps = get_priced_apps(db, org_id, config_db)
    apps_cost = sum(a["price_cents"] for a in priced_apps)

    return {
        "subscription": {
            "plan": plan["id"] if plan else None,
            # Older clients read the plan under its old name.
            "token_plan": plan["id"] if plan else None,
            "status": sub.status if sub else None,
            "billing_cycle_start": sub.billing_cycle_start.isoformat()
            if sub and sub.billing_cycle_start
            else None,
            "payment_method_last4": sub.payment_method_last4 if sub else None,
            "payment_method_brand": sub.payment_method_brand if sub else None,
        },
        "plans": [
            {
                "id": key,
                "name": p["name"],
                "price_cents": p["price_cents"],
                "multiple": p["multiple"],
            }
            for key, p in PLANS.items()
        ],
        "usage": usage,
        "top_up": {"price_cents": TOPUP_PRICE_CENTS, "adds_core_months": 1},
        "agent_price_cents": AGENT_PRICE_CENTS,
        # Legacy: the deprecated PUT /agents speaks stripe_price_keys.
        "agents": {a["key"]: a["enabled"] for a in agent_apps},
        "agent_apps": agent_apps,
        "priced_apps": priced_apps,
        "monthly_cost_cents": plan_cost + agent_cost + apps_cost,
        "cost_breakdown": {
            "plan": plan_cost,
            "agents": agent_cost,
            "apps": apps_cost,
        },
    }


# ---------------------------------------------------------------------------
# Stripe operations
# ---------------------------------------------------------------------------


def _get_stripe():
    """Lazy import and configure stripe."""
    import stripe

    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def get_or_create_stripe_customer(db: Session, org_id: str) -> str:
    """Create a Stripe Customer for the org if one doesn't exist. Returns customer ID."""
    from app.db.models import Organization, Subscription

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise ValueError(f"Organization not found: {org_id}")

    sub = (
        db.query(Subscription)
        .filter(
            Subscription.organization_id == org_id,
        )
        .first()
    )

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
            token_plan=DEFAULT_PLAN,
            token_quota=0,  # unused since plans moved to usage allowances
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


def create_subscription(
    db: Session, org_id: str, token_plan: str, config_db=None
) -> dict:
    """Create a Stripe Subscription for the org."""
    from app.db.models import Subscription

    sub = (
        db.query(Subscription)
        .filter(
            Subscription.organization_id == org_id,
        )
        .first()
    )

    if not sub or not sub.stripe_customer_id:
        raise ValueError("Stripe customer not set up. Call setup first.")

    if not sub.payment_method_last4:
        raise ValueError(
            "Add a payment method before subscribing. Complete the card setup first."
        )

    if sub.stripe_subscription_id:
        raise ValueError("Subscription already exists. Use change_plan instead.")

    plan = plan_for(token_plan)
    if not plan:
        raise ValueError(f"Invalid plan: {token_plan}")

    stripe = _get_stripe()
    items = []

    price_id = settings.get_stripe_price_id(plan["id"])
    if price_id:
        items.append({"price": price_id})

    # Hired team members, then priced Apps that are on — the same resolvers
    # the access gates read, so the subscription can never bill what the org
    # can't use. Members sharing a price (all of them, "agent") are one item.
    for price, quantity in _agent_items(db, org_id, config_db).items():
        items.append({"price": price, "quantity": quantity})
    for priced_app in get_priced_apps(db, org_id, config_db):
        app_price = settings.get_stripe_price_id(priced_app["key"])
        if app_price:
            items.append({"price": app_price})

    if not items:
        raise ValueError("No Stripe price IDs configured. Set STRIPE_PRICE_* env vars.")

    stripe_sub = stripe.Subscription.create(
        customer=sub.stripe_customer_id,
        items=items,
        metadata={"org_id": org_id},
    )

    sub.stripe_subscription_id = stripe_sub.id
    sub.token_plan = plan["id"]
    sub.status = "active"
    sub.billing_cycle_start = datetime.fromtimestamp(
        stripe_sub.current_period_start,
        tz=timezone.utc,
    )
    db.flush()

    _log_event(db, org_id, "subscription_created", {"plan": plan["id"]})
    return get_billing_info(db, org_id, config_db)


def change_plan(db: Session, org_id: str, new_plan: str, config_db=None) -> dict:
    """Change the token plan on an existing subscription."""
    from app.db.models import Subscription

    sub = (
        db.query(Subscription)
        .filter(
            Subscription.organization_id == org_id,
        )
        .first()
    )
    if not sub or not sub.stripe_subscription_id:
        raise ValueError("No active subscription")

    plan = plan_for(new_plan)
    if not plan:
        raise ValueError(f"Invalid plan: {new_plan}")

    stripe = _get_stripe()
    stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)

    # Find the plan item and update it
    new_price_id = settings.get_stripe_price_id(plan["id"])
    if not new_price_id:
        raise ValueError(f"No Stripe price configured for plan: {plan['id']}")

    # The current plan item: the item on any plan's price, old names included.
    plan_prices = {
        settings.get_stripe_price_id(p)
        for p in [*PLANS, *LEGACY_PLANS]
        if settings.get_stripe_price_id(p)
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
    sub.token_plan = plan["id"]
    db.flush()

    _log_event(db, org_id, "plan_changed", {"from": old_plan, "to": plan["id"]})
    return get_billing_info(db, org_id, config_db)


def purchase_top_up(
    db: Session, org_id: str, user_id: str | None, units: int = 1
) -> dict:
    """Buy more usage this period: each unit adds a Core-sized month of
    usage (TOPUP_USAGE_USD) for TOPUP_PRICE_CENTS."""
    from app.db.models import Subscription, TokenTopUp

    sub = (
        db.query(Subscription)
        .filter(
            Subscription.organization_id == org_id,
        )
        .first()
    )
    if not sub or not sub.stripe_customer_id:
        raise ValueError("No billing set up for this organization")

    if units < 1:
        raise ValueError("Buy at least one top-up")
    usage_usd = units * TOPUP_USAGE_USD
    amount_cents = units * TOPUP_PRICE_CENTS

    stripe = _get_stripe()
    intent = stripe.PaymentIntent.create(
        amount=amount_cents,
        currency="usd",
        customer=sub.stripe_customer_id,
        metadata={"org_id": org_id, "usage_usd": usage_usd, "type": "topup"},
        confirm=True,
        automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
    )

    topup = TokenTopUp(
        organization_id=org_id,
        tokens=0,
        usage_usd=usage_usd,
        amount_cents=amount_cents,
        stripe_payment_intent_id=intent.id,
        status="completed" if intent.status == "succeeded" else "pending",
        purchased_by=user_id,
    )
    db.add(topup)
    db.flush()

    _log_event(
        db,
        org_id,
        "topup_purchased",
        {"usage_usd": usage_usd, "amount_cents": amount_cents},
    )
    return {
        "usage_usd": usage_usd,
        "amount_cents": amount_cents,
        "status": topup.status,
    }


def _agent_items(db: Session, org_id: str, config_db=None) -> dict[str, int]:
    """Stripe price → quantity for the org's hired, priced team members."""
    items: dict[str, int] = {}
    for agent_app in get_agent_apps(db, org_id, config_db):
        if agent_app["enabled"] and agent_app["price_cents"]:
            price = settings.get_stripe_price_id(agent_app["key"])
            if price:
                items[price] = items.get(price, 0) + 1
    return items


def sync_agent_count(db: Session, org_id: str, config_db=None) -> None:
    """Make the Stripe subscription bill the team members hired right now —
    called after a member is hired or retired. Best effort: a Stripe failure
    is logged, never raised (the hire itself already happened)."""
    from app.db.models import Subscription

    sub = db.query(Subscription).filter(Subscription.organization_id == org_id).first()
    if not sub or not sub.stripe_subscription_id:
        return

    stripe = _get_stripe()
    try:
        wanted = _agent_items(db, org_id, config_db)
        agent_prices = {
            settings.get_stripe_price_id(a["key"])
            for a in get_agent_apps(db, org_id, config_db)
            if settings.get_stripe_price_id(a["key"])
        }
        stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)
        seen: set[str] = set()
        for item in stripe_sub["items"]["data"]:
            price = item["price"]["id"]
            if price not in agent_prices:
                continue
            seen.add(price)
            quantity = wanted.get(price, 0)
            if quantity == 0:
                stripe.SubscriptionItem.delete(
                    item["id"], proration_behavior="create_prorations"
                )
            elif item.get("quantity") != quantity:
                stripe.SubscriptionItem.modify(item["id"], quantity=quantity)
        for price, quantity in wanted.items():
            if price not in seen:
                stripe.SubscriptionItem.create(
                    subscription=sub.stripe_subscription_id,
                    price=price,
                    quantity=quantity,
                )
    except Exception:
        logger.exception("Failed to sync team members with Stripe for org %s", org_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log_event(
    db: Session,
    org_id: str,
    event_type: str,
    details: dict | None = None,
    stripe_event_id: str | None = None,
):
    from app.db.models import BillingEvent

    db.add(
        BillingEvent(
            organization_id=org_id,
            event_type=event_type,
            stripe_event_id=stripe_event_id,
            details=details,
        )
    )
    db.flush()
