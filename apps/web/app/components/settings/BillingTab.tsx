'use client';

import { useState, useEffect, useCallback } from 'react';
import { loadStripe } from '@stripe/stripe-js';
import { Elements, CardElement, useStripe, useElements } from '@stripe/react-stripe-js';
import { apiFetch } from '../../lib/api';
import type { BillingInfo, StripeInvoice } from '../../types';

const stripePromise = loadStripe(process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY || '');

const PLANS = [
  { id: 'basic', name: 'Basic', price: 50, tokens: '1M', tokensNum: 1_000_000 },
  { id: 'standard', name: 'Standard', price: 100, tokens: '3M', tokensNum: 3_000_000 },
  { id: 'max', name: 'Max', price: 200, tokens: '10M', tokensNum: 10_000_000 },
] as const;

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(0)}`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

// ---------------------------------------------------------------------------
// Payment method form (wrapped in Stripe Elements)
// ---------------------------------------------------------------------------

function PaymentMethodForm({ orgId, onSuccess }: { orgId: string; onSuccess: () => void }) {
  const stripe = useStripe();
  const elements = useElements();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [clientSecret, setClientSecret] = useState<string | null>(null);

  useEffect(() => {
    apiFetch(`/api/billing/${orgId}/setup`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      .then(r => r.json())
      .then(d => setClientSecret(d.client_secret))
      .catch(e => setError(String(e)));
  }, [orgId]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!stripe || !elements || !clientSecret) return;
    setLoading(true);
    setError(null);
    const card = elements.getElement(CardElement);
    if (!card) return;
    const { error: stripeError } = await stripe.confirmCardSetup(clientSecret, {
      payment_method: { card },
    });
    if (stripeError) {
      setError(stripeError.message || 'Failed to save card');
      setLoading(false);
    } else {
      onSuccess();
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <div style={{ padding: '0.5rem', border: '1px solid #e2e8f0', borderRadius: 6, backgroundColor: '#fff', marginBottom: '0.5rem' }}>
        <CardElement options={{ style: { base: { fontSize: '14px', color: '#333' } } }} />
      </div>
      {error && <p style={{ color: '#e53e3e', fontSize: '0.8rem', margin: '0.25rem 0' }}>{error}</p>}
      <button
        type="submit"
        disabled={!stripe || loading || !clientSecret}
        style={{
          padding: '6px 16px', fontSize: '0.8rem', fontWeight: 600, border: 'none', borderRadius: 6,
          backgroundColor: '#2563eb', color: '#fff', cursor: loading ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
        }}
      >{loading ? 'Saving...' : 'Save Card'}</button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Main BillingTab
// ---------------------------------------------------------------------------

export default function BillingTab({ orgId }: { orgId: string }) {
  const [billing, setBilling] = useState<BillingInfo | null>(null);
  const [invoices, setInvoices] = useState<StripeInvoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [showCardForm, setShowCardForm] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [pendingPlan, setPendingPlan] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const fetchBilling = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/billing/${orgId}`);
      if (res.ok) { setBilling(await res.json()); setFetchError(null); }
      else setFetchError('Failed to load billing information.');
    } catch { setFetchError('Failed to load billing information.'); }
    setLoading(false);
  }, [orgId]);

  const fetchInvoices = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/billing/${orgId}/invoices`);
      if (res.ok) {
        const data = await res.json();
        setInvoices(data.invoices || []);
      }
    } catch { /* invoices are non-critical */ }
  }, [orgId]);

  useEffect(() => { fetchBilling(); fetchInvoices(); }, [fetchBilling, fetchInvoices]);

  const changePlan = async (plan: string) => {
    if (!confirm(`Switch to the ${plan} plan? Your billing will be prorated.`)) return;
    setActionLoading('plan');
    setError(null);
    try {
      const res = await apiFetch(`/api/billing/${orgId}/plan`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token_plan: plan }),
      });
      if (res.ok) {
        setBilling(await res.json());
        fetchInvoices();
      } else {
        const d = await res.json();
        setError(d.detail || 'Failed to change plan');
      }
    } catch (e) { setError(String(e)); }
    setActionLoading(null);
  };

  const subscribe = async (plan: string) => {
    setActionLoading('subscribe');
    setError(null);
    try {
      const res = await apiFetch(`/api/billing/${orgId}/subscribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token_plan: plan }),
      });
      if (res.ok) {
        setBilling(await res.json());
        setPendingPlan(null);
        fetchInvoices();
      } else {
        const d = await res.json();
        setError(d.detail || 'Failed to subscribe');
      }
    } catch (e) { setError(String(e)); }
    setActionLoading(null);
  };

  const handleSelectPlan = (plan: string) => {
    if (hasSubscription) {
      changePlan(plan);
    } else if (hasPaymentMethod) {
      subscribe(plan);
    } else {
      // No payment method — show card form first, then subscribe
      setPendingPlan(plan);
      setShowCardForm(true);
    }
  };

  const topUp = async (units: number) => {
    if (!confirm(`Purchase ${formatTokens(units * 500_000)} tokens for $${units * 10}?`)) return;
    setActionLoading('topup');
    setError(null);
    try {
      const res = await apiFetch(`/api/billing/${orgId}/topup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ units }),
      });
      if (res.ok) {
        await fetchBilling();
        fetchInvoices();
      } else {
        const d = await res.json();
        setError(d.detail || 'Top-up failed');
      }
    } catch (e) { setError(String(e)); }
    setActionLoading(null);
  };

  const toggleAgent = async (agent: 'hr' | 'procurement', enabled: boolean) => {
    setActionLoading(`agent-${agent}`);
    setError(null);
    try {
      const res = await apiFetch(`/api/billing/${orgId}/agents`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [agent]: enabled }),
      });
      if (res.ok) setBilling(await res.json());
      else { const d = await res.json(); setError(d.detail || 'Failed to update agent'); }
    } catch (e) { setError(String(e)); }
    setActionLoading(null);
  };

  if (loading) return <div style={{ padding: '1rem', color: '#888' }}>Loading billing...</div>;
  if (fetchError) return <div style={{ padding: '1rem', color: '#c53030' }}>{fetchError} <button onClick={fetchBilling} style={{ color: '#2563eb', border: 'none', background: 'none', cursor: 'pointer', textDecoration: 'underline' }}>Retry</button></div>;
  if (!billing) return <div style={{ padding: '1rem', color: '#888' }}>No billing information available.</div>;

  const sub = billing.subscription;
  const usage = billing.usage;
  const usagePercent = usage.quota > 0 ? Math.min(100, (usage.used / usage.quota) * 100) : 0;
  const hasSubscription = sub && sub.status && sub.status !== 'trialing';
  const hasPaymentMethod = sub && sub.payment_method_last4;

  const sectionStyle: React.CSSProperties = {
    marginBottom: '1.25rem', padding: '1rem', backgroundColor: '#fff',
    border: '1px solid #e2e8f0', borderRadius: 8,
  };
  const headingStyle: React.CSSProperties = {
    fontSize: '0.82rem', fontWeight: 600, color: '#333', marginBottom: '0.75rem', margin: 0,
  };

  return (
    <div>
      {error && (
        <div style={{ padding: '0.5rem 0.75rem', backgroundColor: '#fff5f5', border: '1px solid #feb2b2', borderRadius: 6, color: '#c53030', fontSize: '0.8rem', marginBottom: '0.75rem' }}>
          {error}
          <button onClick={() => setError(null)} style={{ float: 'right', border: 'none', background: 'none', cursor: 'pointer', color: '#c53030' }}>&times;</button>
        </div>
      )}

      {/* Usage */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Token Usage</h3>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.78rem', color: '#555', marginBottom: 4 }}>
          <span>{formatTokens(usage.used)} used</span>
          <span>{formatTokens(usage.remaining)} remaining of {formatTokens(usage.quota)}</span>
        </div>
        <div style={{ height: 8, backgroundColor: '#e2e8f0', borderRadius: 4, overflow: 'hidden' }}>
          <div style={{
            height: '100%', borderRadius: 4, transition: 'width 0.3s',
            width: `${usagePercent}%`,
            backgroundColor: usagePercent > 90 ? '#e53e3e' : usagePercent > 70 ? '#ed8936' : '#48bb78',
          }} />
        </div>
        {usagePercent > 80 && (
          <p style={{ fontSize: '0.72rem', color: '#e53e3e', marginTop: 4, marginBottom: 0 }}>
            Running low on tokens — consider upgrading your plan or purchasing a top-up.
          </p>
        )}
      </div>

      {/* Plans */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Token Plan</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.5rem' }}>
          {PLANS.map(plan => {
            const isActive = sub?.token_plan === plan.id;
            return (
              <div
                key={plan.id}
                style={{
                  padding: '0.75rem', borderRadius: 8, textAlign: 'center',
                  border: isActive ? '2px solid #2563eb' : '1px solid #e2e8f0',
                  backgroundColor: isActive ? '#eff6ff' : '#fff',
                }}
              >
                <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#333' }}>{plan.name}</div>
                <div style={{ fontSize: '1.2rem', fontWeight: 700, color: '#2563eb', margin: '0.25rem 0' }}>${plan.price}</div>
                <div style={{ fontSize: '0.72rem', color: '#888' }}>{plan.tokens} tokens/month</div>
                {!isActive && (
                  <button
                    onClick={() => handleSelectPlan(plan.id)}
                    disabled={actionLoading !== null}
                    style={{
                      marginTop: '0.5rem', padding: '4px 12px', fontSize: '0.72rem', fontWeight: 600,
                      border: '1px solid #2563eb', borderRadius: 5, backgroundColor: '#fff',
                      color: '#2563eb', cursor: actionLoading ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
                    }}
                  >{hasSubscription ? 'Switch' : 'Select'}</button>
                )}
                {isActive && <div style={{ marginTop: '0.5rem', fontSize: '0.72rem', color: '#2563eb', fontWeight: 600 }}>Current Plan</div>}
              </div>
            );
          })}
        </div>
      </div>

      {/* Monthly cost breakdown */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Monthly Cost</h3>
        <div style={{ fontSize: '0.78rem', color: '#555' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid #f0f0f0' }}>
            <span>Token plan ({sub?.token_plan || 'basic'})</span>
            <span>{formatCents(billing.cost_breakdown.plan)}/mo</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid #f0f0f0' }}>
            <span>Agents ({[billing.agents.hr && 'HR', billing.agents.procurement && 'Procurement'].filter(Boolean).join(', ') || 'none'})</span>
            <span>{formatCents(billing.cost_breakdown.agents)}/mo</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid #f0f0f0' }}>
            <span>Venues ({billing.venue_count})</span>
            <span>{formatCents(billing.cost_breakdown.venues)}/mo</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', fontWeight: 600, color: '#333' }}>
            <span>Total</span>
            <span>{formatCents(billing.monthly_cost_cents)}/mo</span>
          </div>
        </div>
      </div>

      {/* Agents */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Paid Agents</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {([
            { key: 'hr' as const, label: 'HR Agent', price: '$10/mo' },
            { key: 'procurement' as const, label: 'Procurement Agent', price: '$5/mo' },
          ]).map(agent => (
            <label key={agent.key} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.78rem', color: '#555', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={billing.agents[agent.key]}
                onChange={e => toggleAgent(agent.key, e.target.checked)}
                disabled={actionLoading !== null}
              />
              {agent.label} <span style={{ color: '#aaa' }}>({agent.price})</span>
            </label>
          ))}
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.78rem', color: '#999' }}>
            <input type="checkbox" checked={true} disabled />
            Reports Agent <span style={{ color: '#aaa' }}>(free)</span>
          </label>
        </div>
      </div>

      {/* Payment method */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Payment Method</h3>
        {hasPaymentMethod ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: '0.82rem', color: '#333' }}>
              {sub.payment_method_brand ? sub.payment_method_brand.toUpperCase() : 'Card'} ending in {sub.payment_method_last4}
            </span>
            <button
              onClick={() => { setShowCardForm(!showCardForm); setPendingPlan(null); }}
              style={{
                padding: '3px 10px', fontSize: '0.72rem', border: '1px solid #cbd5e1', borderRadius: 5,
                backgroundColor: '#fff', color: '#555', cursor: 'pointer', fontFamily: 'inherit',
              }}
            >{showCardForm ? 'Cancel' : 'Update'}</button>
          </div>
        ) : (
          <p style={{ fontSize: '0.78rem', color: '#888', margin: '0 0 0.5rem' }}>
            No payment method on file.{pendingPlan ? ' Add a card to activate your plan.' : ''}
          </p>
        )}
        {(showCardForm || !hasPaymentMethod) && (
          <div style={{ marginTop: '0.5rem' }}>
            <Elements stripe={stripePromise}>
              <PaymentMethodForm orgId={orgId} onSuccess={async () => {
                setShowCardForm(false);
                await fetchBilling();
                // If user selected a plan before adding card, auto-subscribe now
                if (pendingPlan) {
                  subscribe(pendingPlan);
                }
              }} />
            </Elements>
          </div>
        )}
      </div>

      {/* Top-ups */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Buy More Tokens</h3>
        <p style={{ fontSize: '0.72rem', color: '#888', margin: '0 0 0.5rem' }}>
          500K tokens per top-up at $10 each. Tokens expire at end of billing period.
          {!hasPaymentMethod && <span style={{ color: '#e53e3e' }}> Add a payment method first.</span>}
        </p>
        <div style={{ display: 'flex', gap: 8 }}>
          {[1, 2, 5].map(units => (
            <button
              key={units}
              onClick={() => topUp(units)}
              disabled={actionLoading !== null || !hasPaymentMethod}
              style={{
                padding: '6px 14px', fontSize: '0.78rem', fontWeight: 600,
                border: '1px solid #cbd5e1', borderRadius: 6, backgroundColor: '#fff',
                color: '#333', cursor: (actionLoading || !hasPaymentMethod) ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
              }}
            >
              {formatTokens(units * 500_000)} — ${units * 10}
            </button>
          ))}
        </div>
      </div>

      {/* Invoices */}
      {invoices.length > 0 && (
        <div style={sectionStyle}>
          <h3 style={headingStyle}>Invoice History</h3>
          <table style={{ width: '100%', fontSize: '0.75rem', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #e2e8f0', color: '#888', textAlign: 'left' }}>
                <th style={{ padding: '4px 0', fontWeight: 600 }}>Date</th>
                <th style={{ padding: '4px 0', fontWeight: 600 }}>Amount</th>
                <th style={{ padding: '4px 0', fontWeight: 600 }}>Status</th>
                <th style={{ padding: '4px 0', fontWeight: 600 }}></th>
              </tr>
            </thead>
            <tbody>
              {invoices.map(inv => (
                <tr key={inv.id} style={{ borderBottom: '1px solid #f0f0f0' }}>
                  <td style={{ padding: '4px 0', color: '#555' }}>{new Date(inv.created * 1000).toLocaleDateString()}</td>
                  <td style={{ padding: '4px 0', color: '#333' }}>${(inv.amount_paid / 100).toFixed(2)}</td>
                  <td style={{ padding: '4px 0' }}>
                    <span style={{
                      fontSize: '0.65rem', fontWeight: 600, padding: '1px 6px', borderRadius: 3,
                      backgroundColor: inv.status === 'paid' ? '#f0fff4' : '#fff5f5',
                      color: inv.status === 'paid' ? '#22543d' : '#c53030',
                    }}>{inv.status}</span>
                  </td>
                  <td style={{ padding: '4px 0' }}>
                    {inv.hosted_invoice_url && (
                      <a href={inv.hosted_invoice_url} target="_blank" rel="noreferrer" style={{ fontSize: '0.7rem', color: '#2563eb' }}>View</a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Subscription status */}
      {sub?.status && (
        <div style={{ fontSize: '0.7rem', color: '#aaa', textAlign: 'center' }}>
          Subscription status: {sub.status}
          {sub.billing_cycle_start && ` · Billing cycle started ${new Date(sub.billing_cycle_start).toLocaleDateString()}`}
        </div>
      )}
    </div>
  );
}
