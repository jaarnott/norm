"""Usage-allowance plans (Oct 2026): Core, Pro and Max include a month of
usage measured as what Norm's model calls cost, not tokens. Past the
allowance a conversation carries on with the light model (Haiku) instead of
stopping; only an inactive subscription stops Norm. Every hired AI team
member is $10 on one shared Stripe price; venues no longer cost anything."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.services import billing_service as bs
from tests.conftest import _make_membership, _make_thread, _make_user


def _subscription(db, org, plan="core", status="active"):
    from app.db.models import Subscription

    sub = Subscription(
        organization_id=org.id, token_plan=plan, token_quota=0, status=status
    )
    db.add(sub)
    db.flush()
    return sub


def _spend(db, org, usd):
    from app.db.models import TokenUsage

    db.add(
        TokenUsage(
            organization_id=org.id,
            date=date.today().strftime("%Y-%m-%d"),
            cost_usd=usd,
            llm_call_count=1,
        )
    )
    db.flush()


def _agent_row(db, slug, key="agent", price=bs.AGENT_PRICE_CENTS, bundled=True):
    from app.db.config_models import MarketplaceApp

    db.add(
        MarketplaceApp(
            slug=slug,
            name=slug,
            tier="agent",
            status="active",
            bundled=bundled,
            price_cents=price,
            stripe_price_key=key,
            composition={"owns_agents": [slug]},
        )
    )
    db.flush()


class TestPlans:
    def test_the_three_plans_and_their_usage(self):
        assert {k: (p["price_cents"], p["usage_usd"]) for k, p in bs.PLANS.items()} == {
            "core": (4900, 25),
            "pro": (14900, 100),
            "max": (29900, 275),
        }

    def test_the_multiples_the_site_quotes_are_true(self):
        core = bs.PLANS["core"]["usage_usd"]
        for plan in bs.PLANS.values():
            assert plan["usage_usd"] == core * plan["multiple"]

    def test_old_token_plan_names_read_as_their_successors(self):
        assert bs.plan_for("basic")["id"] == "core"
        assert bs.plan_for("standard")["id"] == "pro"
        assert bs.plan_for("max")["id"] == "max"
        assert bs.plan_for("nonsense") is None


class TestAllowance:
    def test_usage_is_metered_in_dollars_against_the_plan(
        self, db_session, organization
    ):
        _subscription(db_session, organization, "core")
        _spend(db_session, organization, 12.0)
        q = bs.check_quota(db_session, organization.id)
        assert q["allowed"] is True
        assert q["light_mode"] is False
        assert q["used_usd"] == 12.0
        assert q["allowance_usd"] == 25
        assert q["percent"] == 48.0

    def test_past_the_allowance_norm_keeps_working_in_light_mode(
        self, db_session, organization
    ):
        _subscription(db_session, organization, "core")
        _spend(db_session, organization, 31.0)
        q = bs.check_quota(db_session, organization.id)
        assert q["allowed"] is True
        assert q["light_mode"] is True

    def test_a_bigger_plan_has_more_room(self, db_session, organization):
        _subscription(db_session, organization, "pro")
        _spend(db_session, organization, 31.0)
        q = bs.check_quota(db_session, organization.id)
        assert q["allowance_usd"] == 100
        assert q["light_mode"] is False

    def test_a_top_up_adds_a_core_month(self, db_session, organization):
        from app.db.models import TokenTopUp

        _subscription(db_session, organization, "core")
        _spend(db_session, organization, 31.0)
        db_session.add(
            TokenTopUp(
                organization_id=organization.id,
                tokens=0,
                usage_usd=bs.TOPUP_USAGE_USD,
                amount_cents=bs.TOPUP_PRICE_CENTS,
                status="completed",
            )
        )
        db_session.flush()
        q = bs.check_quota(db_session, organization.id)
        assert q["allowance_usd"] == 50
        assert q["light_mode"] is False

    def test_an_inactive_subscription_stops_norm(self, db_session, organization):
        _subscription(db_session, organization, "core", status="past_due")
        q = bs.check_quota(db_session, organization.id)
        assert q["allowed"] is False
        assert q["light_mode"] is False


class TestLightModeForThread:
    @pytest.fixture()
    def thread_over_allowance(self, db_session, organization):
        user = _make_user(db_session, email="light@test.local")
        _make_membership(db_session, user, organization)
        _subscription(db_session, organization, "core")
        _spend(db_session, organization, 45.0)
        return _make_thread(db_session, user)

    def test_on_when_the_org_is_over_and_billing_is_enforced(
        self, db_session, thread_over_allowance, monkeypatch
    ):
        monkeypatch.setattr(settings, "BILLING_ENFORCEMENT", True)
        assert bs.light_mode_for_thread(db_session, thread_over_allowance.id) is True

    def test_off_while_billing_enforcement_is_off(
        self, db_session, thread_over_allowance, monkeypatch
    ):
        monkeypatch.setattr(settings, "BILLING_ENFORCEMENT", False)
        assert bs.light_mode_for_thread(db_session, thread_over_allowance.id) is False

    def test_an_unknown_thread_is_never_light(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "BILLING_ENFORCEMENT", True)
        assert bs.light_mode_for_thread(db_session, "no-such-thread") is False


class TestFallbackRequest:
    """The request that actually goes to Anthropic: over the allowance it is
    Haiku with fixed-budget thinking and no refusal-fallback beta; otherwise
    the normal model with adaptive thinking, exactly as before."""

    def _send(self, monkeypatch, light):
        from app.interpreter import llm_interpreter as li
        from tests.test_llm_retry import _FakeStreamCtx, _final, _text_delta

        monkeypatch.setattr(
            "app.services.secrets.get_api_key", lambda *a, **k: "sk-test"
        )
        monkeypatch.setattr(
            "app.services.models.agent_model", lambda *a, **k: "claude-opus-5-5"
        )
        monkeypatch.setattr(
            "app.services.billing_service.light_mode_for_thread", lambda db, tid: light
        )
        monkeypatch.setattr("app.agents.tool_loop._emit_event", lambda *a, **k: None)
        monkeypatch.setattr(li, "_persist_llm_call_isolated", lambda **kw: None)
        monkeypatch.setattr(li, "_persist_llm_call", lambda db, **kw: "id")
        final = _final("ok")
        final.stop_reason = "end_turn"
        client = MagicMock()
        client.beta.messages.stream.side_effect = [
            _FakeStreamCtx(events=[_text_delta("ok")], final=final)
        ]
        with patch("anthropic.Anthropic", return_value=client):
            li.call_llm_with_tools(
                system_prompt="s",
                messages=[{"role": "user", "content": "hi"}],
                tools=[
                    {
                        "name": "t",
                        "description": "d",
                        "input_schema": {"type": "object"},
                    }
                ],
                db=MagicMock(),
                thread_id="thread-1",
            )
        return client.beta.messages.stream.call_args.kwargs

    def test_over_the_allowance_the_call_goes_to_haiku(self, monkeypatch):
        kw = self._send(monkeypatch, light=True)
        assert kw["model"] == bs.FALLBACK_MODEL
        assert kw["thinking"] == {"type": "enabled", "budget_tokens": 4096}
        assert kw["betas"] == ["context-management-2025-06-27"]
        assert "extra_body" not in kw
        assert "output_config" not in kw

    def test_within_the_allowance_nothing_changes(self, monkeypatch):
        kw = self._send(monkeypatch, light=False)
        assert kw["model"] == "claude-opus-5-5"
        assert kw["thinking"]["type"] == "adaptive"
        assert "server-side-fallback-2026-07-01" in kw["betas"]
        assert kw["extra_body"] == {"fallbacks": "default"}


class TestBillingInfo:
    def test_plan_plus_ten_dollars_per_hired_member_and_no_venue_fee(
        self, db_session, organization, venue
    ):
        _subscription(db_session, organization, "core")
        _agent_row(db_session, "procurement-agent")
        _agent_row(db_session, "hr-agent")
        info = bs.get_billing_info(db_session, organization.id, db_session)
        assert info["subscription"]["plan"] == "core"
        assert info["cost_breakdown"] == {"plan": 4900, "agents": 2000, "apps": 0}
        assert info["monthly_cost_cents"] == 6900
        assert [p["id"] for p in info["plans"]] == ["core", "pro", "max"]
        assert info["top_up"]["price_cents"] == bs.TOPUP_PRICE_CENTS

    def test_billing_claims_light_mode_only_when_norm_is_in_it(
        self, db_session, organization, monkeypatch
    ):
        _subscription(db_session, organization, "core")
        _spend(db_session, organization, 31.0)
        monkeypatch.setattr(settings, "BILLING_ENFORCEMENT", False)
        info = bs.get_billing_info(db_session, organization.id, db_session)
        assert info["usage"]["light_mode"] is False
        assert info["usage"]["percent"] > 100
        monkeypatch.setattr(settings, "BILLING_ENFORCEMENT", True)
        info = bs.get_billing_info(db_session, organization.id, db_session)
        assert info["usage"]["light_mode"] is True


class TestStripeItems:
    def test_hired_members_share_one_agent_item(
        self, db_session, organization, monkeypatch
    ):
        monkeypatch.setattr(settings, "STRIPE_PRICE_AGENT", "price_agent")
        _agent_row(db_session, "procurement-agent")
        _agent_row(db_session, "hr-agent")
        _agent_row(db_session, "marketing-agent")
        assert bs._agent_items(db_session, organization.id, db_session) == {
            "price_agent": 3
        }

    def test_hiring_updates_the_quantity_on_the_subscription(
        self, db_session, organization, monkeypatch
    ):
        monkeypatch.setattr(settings, "STRIPE_PRICE_AGENT", "price_agent")
        sub = _subscription(db_session, organization, "core")
        sub.stripe_subscription_id = "sub_1"
        _agent_row(db_session, "procurement-agent")
        _agent_row(db_session, "hr-agent")
        stripe = MagicMock()
        stripe.Subscription.retrieve.return_value = {
            "items": {
                "data": [{"id": "si_1", "price": {"id": "price_agent"}, "quantity": 1}]
            }
        }
        with patch.object(bs, "_get_stripe", return_value=stripe):
            bs.sync_agent_count(db_session, organization.id, db_session)
        stripe.SubscriptionItem.modify.assert_called_once_with("si_1", quantity=2)
        stripe.SubscriptionItem.create.assert_not_called()

    def test_retiring_the_last_member_removes_the_item(
        self, db_session, organization, monkeypatch
    ):
        monkeypatch.setattr(settings, "STRIPE_PRICE_AGENT", "price_agent")
        sub = _subscription(db_session, organization, "core")
        sub.stripe_subscription_id = "sub_1"
        _agent_row(db_session, "hr-agent", bundled=False)  # not hired
        stripe = MagicMock()
        stripe.Subscription.retrieve.return_value = {
            "items": {
                "data": [{"id": "si_1", "price": {"id": "price_agent"}, "quantity": 1}]
            }
        }
        with patch.object(bs, "_get_stripe", return_value=stripe):
            bs.sync_agent_count(db_session, organization.id, db_session)
        stripe.SubscriptionItem.delete.assert_called_once()


class TestTopUpPurchase:
    def test_a_top_up_charges_thirty_dollars_for_a_core_month_of_usage(
        self, db_session, organization
    ):
        from app.db.models import TokenTopUp

        sub = _subscription(db_session, organization, "core")
        sub.stripe_customer_id = "cus_1"
        stripe = MagicMock()
        stripe.PaymentIntent.create.return_value = MagicMock(
            id="pi_1", status="succeeded"
        )
        with patch.object(bs, "_get_stripe", return_value=stripe):
            out = bs.purchase_top_up(db_session, organization.id, None, units=1)
        assert out == {"usage_usd": 25, "amount_cents": 3000, "status": "completed"}
        assert stripe.PaymentIntent.create.call_args.kwargs["amount"] == 3000
        row = (
            db_session.query(TokenTopUp)
            .filter_by(organization_id=organization.id)
            .one()
        )
        assert float(row.usage_usd) == 25
