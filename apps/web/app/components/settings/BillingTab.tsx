'use client';

import { useState, useEffect, useCallback } from 'react';
import type { ReactNode } from 'react';
import { loadStripe } from '@stripe/stripe-js';
import { Elements, CardElement, useStripe, useElements } from '@stripe/react-stripe-js';
import { Check, CreditCard, ExternalLink, TriangleAlert, X } from 'lucide-react';
import { apiFetch, getStoredUser } from '../../lib/api';
import { can } from '../../lib/permissions';
import type { BillingInfo, StripeInvoice } from '../../types';
import Badge from '../ui/Badge';
import type { BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import { ConfirmDialog } from '../ui/Dialog';
import Icon from '../ui/Icon';
import IconButton from '../ui/IconButton';
import PageState from '../ui/PageState';

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

/**
 * Runs a billing write and hands back the response when it worked; when it
 * didn't, throws with the API's reason — what a ConfirmDialog shows.
 */
async function send(call: () => Promise<Response>, failed: string): Promise<Response> {
  let res: Response;
  try {
    res = await call();
  } catch {
    throw new Error(`${failed} — check your connection and try again.`);
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = typeof body?.detail === 'string' ? body.detail : `error ${res.status}`;
    throw new Error(`${failed}: ${detail}`);
  }
  return res;
}

// ---------------------------------------------------------------------------
// Presentation helpers
// ---------------------------------------------------------------------------

/** A status slug as words: "past_due" → "Past due". */
function sentence(s: string): string {
  const words = s.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Stripe invoice states: paid is done, open is still owed, uncollectible
 *  failed, a draft is informational, void is neither. */
function invoiceTone(status: string): BadgeTone {
  if (status === 'paid') return 'ok';
  if (status === 'open') return 'warn';
  if (status === 'uncollectible') return 'error';
  if (status === 'draft') return 'info';
  return 'neutral';
}

function subscriptionTone(status: string): BadgeTone {
  if (status === 'active') return 'ok';
  if (status === 'past_due') return 'warn';
  if (status === 'trialing') return 'info';
  return 'neutral';
}

/** One settings section, as in the Settings design: an 18px title with a muted
 *  meta beside it and actions on the right, an optional note under it, then
 *  the content (usually a white card) and an optional footnote. */
function Section({ title, meta, note, actions, footnote, children }: {
  title: string;
  meta?: ReactNode;
  note?: ReactNode;
  actions?: ReactNode;
  footnote?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section style={{ marginBottom: 28 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: note ? 0 : 12 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', minWidth: 0 }}>
          <h2 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{title}</h2>
          {meta && <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{meta}</span>}
        </div>
        {actions}
      </div>
      {note && <p style={{ margin: '2px 0 12px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{note}</p>}
      {children}
      {footnote && <p style={{ margin: '12px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{footnote}</p>}
    </section>
  );
}

/** A line of the monthly cost breakdown: label left, amount right. */
function CostRow({ label, amount, total = false }: { label: ReactNode; amount: string; total?: boolean }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 16, padding: '10px 16px',
      borderBottom: total ? 'none' : '1px solid var(--line)',
      background: total ? 'var(--surface)' : undefined,
      fontWeight: total ? 600 : 400,
      color: total ? 'var(--text)' : 'var(--text-soft)',
    }}>
      <span style={{ minWidth: 0 }}>{label}</span>
      <span style={{ flex: '0 0 auto', color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>{amount}</span>
    </div>
  );
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
      <span className="n-label">Card details</span>
      {/* Edged like .n-input. Stripe draws the field in its own iframe, which
          cannot read CSS variables: the literals below are --text, --muted
          and --error. */}
      <div style={{ padding: '9px 10px', border: '1px solid var(--field)', borderRadius: 'var(--radius)', backgroundColor: 'var(--bg)' }}>
        <CardElement options={{ style: {
          base: { fontSize: '14px', color: '#1a1a1a', '::placeholder': { color: '#69615a' } },
          invalid: { color: '#a93a2a' },
        } }} />
      </div>
      {error && <p role="alert" style={{ color: 'var(--error)', fontSize: 'var(--fs-sm)', margin: '6px 0 0' }}>{error}</p>}
      <Button type="submit" variant="primary" size="sm" disabled={!stripe || loading || !clientSecret} style={{ marginTop: 12 }}>
        {loading ? 'Saving…' : 'Save card'}
      </Button>
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
  // A plan switch or a top-up waiting on "are you sure?" — both cost money.
  const [confirming, setConfirming] = useState<
    { kind: 'plan'; plan: string } | { kind: 'start'; plan: string } | { kind: 'topup'; units: number } | null
  >(null);
  // Anyone who can read billing sees the plan, usage and invoices; changing
  // what the organisation pays for needs billing:manage (owners).
  const canManage = can(getStoredUser(), 'billing:manage');

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

  // Confirmed in the dialog, which shows the reason if it fails.
  const changePlan = async (plan: string) => {
    const res = await send(() => apiFetch(`/api/billing/${orgId}/plan`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token_plan: plan }),
    }), 'The plan wasn’t changed');
    setBilling(await res.json());
    fetchInvoices();
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

  // Confirmed in the dialog: with a card on file, starting a plan charges it
  // straight away.
  const startPlan = async (plan: string) => {
    const res = await send(() => apiFetch(`/api/billing/${orgId}/subscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token_plan: plan }),
    }), 'The plan wasn’t started');
    setBilling(await res.json());
    setPendingPlan(null);
    fetchInvoices();
  };

  const handleSelectPlan = (plan: string) => {
    if (hasSubscription) {
      setConfirming({ kind: 'plan', plan });
    } else if (hasPaymentMethod) {
      setConfirming({ kind: 'start', plan });
    } else {
      // No payment method — show card form first, then subscribe
      setPendingPlan(plan);
      setShowCardForm(true);
    }
  };

  // Confirmed in the dialog, which shows the reason if it fails.
  const topUp = async (units: number) => {
    await send(() => apiFetch(`/api/billing/${orgId}/topup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ units }),
    }), 'No tokens were bought');
    // Paid: close now; the new quota and invoice follow when they load.
    fetchBilling();
    fetchInvoices();
  };

  if (loading) return <PageState kind="loading" title="Loading billing…" />;
  if (fetchError) return <PageState kind="error" title={fetchError} action={<Button size="sm" onClick={fetchBilling}>Retry</Button>} />;
  if (!billing) return <PageState kind="empty" title="No billing information available." />;

  const sub = billing.subscription;
  const usage = billing.usage;
  const usagePercent = usage.quota > 0 ? Math.min(100, (usage.used / usage.quota) * 100) : 0;
  const hasSubscription = sub && sub.status && sub.status !== 'trialing';
  const hasPaymentMethod = sub && sub.payment_method_last4;
  // The bar stays neutral until the "running low" point, then turns amber.
  const nearLimit = usagePercent > 80;
  const planName = PLANS.find(p => p.id === (sub?.token_plan || 'basic'))?.name ?? (sub?.token_plan || 'basic');
  const teamRows = (billing.agent_apps ?? []).length + (billing.priced_apps ?? []).length;
  const currentPlan = PLANS.find(p => p.id === sub?.token_plan);
  const card = hasPaymentMethod
    ? `the ${sub.payment_method_brand ? `${sub.payment_method_brand.toUpperCase()} ` : ''}card ending ${sub.payment_method_last4}`
    : 'your card';

  // What the open "are you sure?" says and does.
  const confirmation = (() => {
    if (confirming?.kind === 'plan') {
      const target = PLANS.find(p => p.id === confirming.plan);
      const name = target?.name ?? confirming.plan;
      return (
        <ConfirmDialog
          title={`Switch to the ${name} plan?`}
          confirmLabel="Switch plan"
          busyLabel="Switching…"
          onConfirm={() => changePlan(confirming.plan)}
          onClose={() => setConfirming(null)}
        >
          <p style={{ margin: 0 }}>
            {target ? `$${target.price} a month for ${target.tokens} tokens` : 'The new plan'}
            {currentPlan ? `, instead of ${currentPlan.name} at $${currentPlan.price} for ${currentPlan.tokens}` : ''}.
            {' '}It starts now, and this month’s bill is prorated for the change.
          </p>
        </ConfirmDialog>
      );
    }
    if (confirming?.kind === 'start') {
      const target = PLANS.find(p => p.id === confirming.plan);
      const name = target?.name ?? confirming.plan;
      return (
        <ConfirmDialog
          title={`Start the ${name} plan?`}
          confirmLabel="Start plan"
          busyLabel="Starting…"
          onConfirm={() => startPlan(confirming.plan)}
          onClose={() => setConfirming(null)}
        >
          <p style={{ margin: 0 }}>
            Billing starts today on {card}: {target ? `$${target.price} a month for ${target.tokens} tokens` : 'the plan’s monthly price'}, plus the venue and team charges shown on this page.
          </p>
        </ConfirmDialog>
      );
    }
    if (confirming?.kind === 'topup') {
      const { units } = confirming;
      return (
        <ConfirmDialog
          title={`Buy ${formatTokens(units * 500_000)} tokens for $${units * 10}?`}
          confirmLabel={`Pay $${units * 10}`}
          busyLabel="Paying…"
          onConfirm={() => topUp(units)}
          onClose={() => setConfirming(null)}
        >
          <p style={{ margin: 0 }}>
            ${units * 10} is charged to {card} now and the tokens are added straight away. They expire at the end of this billing period.
          </p>
        </ConfirmDialog>
      );
    }
    return null;
  })();

  return (
    <div data-testid="billing-tab" style={{ lineHeight: 1.45 }}>
      {error && (
        <div style={{ marginBottom: 16 }}>
          <PageState
            kind="error"
            title={error}
            action={<IconButton icon={X} label="Dismiss" iconSize={16} onClick={() => setError(null)} style={{ margin: '-6px -6px -6px 0' }} />}
          />
        </div>
      )}
      {confirmation}

      {/* Usage */}
      <Section title="Token usage">
        <div className="n-card" style={{ padding: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', marginBottom: 8, fontVariantNumeric: 'tabular-nums' }}>
            <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{formatTokens(usage.used)} used</span>
            <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{formatTokens(usage.remaining)} remaining of {formatTokens(usage.quota)}</span>
          </div>
          <div
            role="progressbar"
            aria-label="Token usage"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(usagePercent)}
            style={{ height: 8, backgroundColor: 'var(--line-soft)', borderRadius: 999, overflow: 'hidden' }}
          >
            <div style={{
              height: '100%', borderRadius: 999, transition: 'width 0.3s',
              width: `${usagePercent}%`,
              backgroundColor: nearLimit ? 'var(--warn)' : 'var(--text-soft)',
            }} />
          </div>
          {usagePercent > 80 && (
            <p style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--warn)', margin: '10px 0 0' }}>
              <Icon icon={TriangleAlert} size="dense" style={{ marginTop: 2 }} />
              {canManage
                ? 'Running low on tokens — consider upgrading your plan or purchasing a top-up.'
                : 'Running low on tokens — ask whoever manages billing to upgrade the plan or buy a top-up.'}
            </p>
          )}
        </div>
      </Section>

      {/* Plans */}
      <Section
        title="Token plan"
        meta={sub?.status ? (
          <>
            <Badge tone={subscriptionTone(sub.status)}>{sentence(sub.status)}</Badge>
            {sub.billing_cycle_start && <span>Billing cycle started {new Date(sub.billing_cycle_start).toLocaleDateString()}</span>}
          </>
        ) : undefined}
        note={canManage ? undefined : 'Only someone who manages billing — usually the owner — can change the plan or the card, or buy more tokens.'}
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 200px), 1fr))', gap: 12 }}>
          {PLANS.map(plan => {
            const isActive = sub?.token_plan === plan.id;
            return (
              <div
                key={plan.id}
                data-testid="plan-selector"
                aria-current={isActive ? 'true' : undefined}
                style={{
                  display: 'flex', flexDirection: 'column', gap: 2,
                  padding: 16, borderRadius: 'var(--radius-lg)',
                  border: `1px solid ${isActive ? 'var(--brand-soft)' : 'var(--line)'}`,
                  // A second pixel of the tan edge on the chosen plan, without shifting the layout.
                  boxShadow: isActive ? 'inset 0 0 0 1px var(--brand-soft)' : undefined,
                  backgroundColor: isActive ? 'var(--selected)' : 'var(--bg)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, minHeight: 30 }}>
                  <span style={{ fontSize: 'var(--fs-base)', fontWeight: isActive ? 600 : 500, color: 'var(--text)' }}>{plan.name}</span>
                  {isActive ? (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--accent-strong)' }}>
                      <Icon icon={Check} size="dense" strokeWidth={2.25} />
                      Current plan
                    </span>
                  ) : canManage ? (
                    <Button size="sm" onClick={() => handleSelectPlan(plan.id)} disabled={actionLoading !== null}>
                      {hasSubscription ? 'Switch' : 'Select'}
                    </Button>
                  ) : null}
                </div>
                <div style={{ fontSize: 'var(--fs-xl)', fontWeight: 700, lineHeight: 1.3, color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>${plan.price}</div>
                <div style={{ fontSize: 'var(--fs-sm)', color: isActive ? 'var(--text-soft)' : 'var(--muted)' }}>{plan.tokens} tokens/month</div>
              </div>
            );
          })}
        </div>
      </Section>

      {/* Monthly cost breakdown */}
      <Section title="Monthly cost">
        <div className="n-card" style={{ overflow: 'hidden', fontSize: 'var(--fs-base)' }}>
          <CostRow label={`Token plan (${planName})`} amount={`${formatCents(billing.cost_breakdown.plan)}/mo`} />
          <CostRow
            label={`Team (${(billing.agent_apps ?? []).filter(a => a.enabled).map(a => a.name).join(', ') || 'none'})`}
            amount={`${formatCents(billing.cost_breakdown.agents)}/mo`}
          />
          {(billing.cost_breakdown.apps ?? 0) > 0 && (
            <CostRow
              label={`Apps (${(billing.priced_apps ?? []).map(a => a.name).join(', ')})`}
              amount={`${formatCents(billing.cost_breakdown.apps ?? 0)}/mo`}
            />
          )}
          <CostRow label={`Venues (${billing.venue_count})`} amount={`${formatCents(billing.cost_breakdown.venues)}/mo`} />
          <CostRow total label="Total" amount={`${formatCents(billing.monthly_cost_cents)}/mo`} />
        </div>
      </Section>

      {/* Your AI team — hired and paid state; managed on the team page */}
      <Section
        title="Your AI team"
        footnote="Hire and retire team members — and switch their Apps — on the team page (the + button in the sidebar)."
      >
        {teamRows > 0 && (
          <div className="n-card" style={{ overflowX: 'auto' }}>
            <table className="n-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Status</th>
                  <th className="num">Price</th>
                </tr>
              </thead>
              <tbody>
                {(billing.agent_apps ?? []).map(a => (
                  <tr key={a.slug}>
                    <td style={{ color: a.enabled ? 'var(--text)' : 'var(--muted)' }}>{a.name}</td>
                    <td>{a.enabled ? <Badge tone="ok">Hired</Badge> : <Badge>Not hired</Badge>}</td>
                    <td className="num" style={{ color: 'var(--text-soft)', whiteSpace: 'nowrap' }}>
                      {a.price_cents > 0 ? `${formatCents(a.price_cents)}/mo` : 'Free'}
                    </td>
                  </tr>
                ))}
                {(billing.priced_apps ?? []).map(a => (
                  <tr key={a.slug}>
                    <td>
                      {a.name}
                      <span style={{ marginLeft: 8, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>App</span>
                    </td>
                    <td><Badge tone="ok">On</Badge></td>
                    <td className="num" style={{ color: 'var(--text-soft)', whiteSpace: 'nowrap' }}>{formatCents(a.price_cents)}/mo</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* Payment method */}
      <Section
        title="Payment method"
        actions={hasPaymentMethod && canManage ? (
          <Button size="sm" onClick={() => { setShowCardForm(!showCardForm); setPendingPlan(null); }}>
            {showCardForm ? 'Cancel' : 'Update'}
          </Button>
        ) : undefined}
      >
        <div className="n-card" style={{ padding: 16 }}>
          {hasPaymentMethod ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-base)', color: 'var(--text)' }}>
              <Icon icon={CreditCard} tone="muted" />
              <span>
                {sub.payment_method_brand ? sub.payment_method_brand.toUpperCase() : 'Card'} ending in {sub.payment_method_last4}
              </span>
            </div>
          ) : (
            <p style={{ fontSize: 'var(--fs-base)', color: 'var(--text-soft)', margin: 0 }}>
              No payment method on file.{pendingPlan ? ' Add a card to activate your plan.' : ''}
            </p>
          )}
          {canManage && (showCardForm || !hasPaymentMethod) && (
            <div style={{ marginTop: 14, maxWidth: 480 }}>
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
      </Section>

      {/* Top-ups — only for someone who may buy them */}
      {canManage && (
        <Section
          title="Buy more tokens"
          note={(
            <>
              500K tokens per top-up at $10 each. Tokens expire at end of billing period.
              {!hasPaymentMethod && <span style={{ color: 'var(--error)' }}> Add a payment method first.</span>}
            </>
          )}
        >
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {[1, 2, 5].map(units => (
              <Button
                key={units}
                onClick={() => setConfirming({ kind: 'topup', units })}
                disabled={actionLoading !== null || !hasPaymentMethod}
                style={{ fontVariantNumeric: 'tabular-nums' }}
              >
                {formatTokens(units * 500_000)} — ${units * 10}
              </Button>
            ))}
          </div>
        </Section>
      )}

      {/* Invoices */}
      {invoices.length > 0 && (
        <Section title="Invoice history">
          <div className="n-card" style={{ overflowX: 'auto' }}>
            <table className="n-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th className="num">Amount</th>
                  <th>Status</th>
                  <th><span style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap' }}>Invoice</span></th>
                </tr>
              </thead>
              <tbody>
                {invoices.map(inv => (
                  <tr key={inv.id}>
                    <td style={{ whiteSpace: 'nowrap' }}>{new Date(inv.created * 1000).toLocaleDateString()}</td>
                    <td className="num">${(inv.amount_paid / 100).toFixed(2)}</td>
                    <td><Badge tone={invoiceTone(inv.status)}>{sentence(inv.status)}</Badge></td>
                    <td className="num">
                      {inv.hosted_invoice_url && (
                        <a
                          href={inv.hosted_invoice_url}
                          target="_blank"
                          rel="noreferrer"
                          style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--accent)' }}
                        >
                          View
                          <Icon icon={ExternalLink} size="meta" />
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}
