'use client';

import { useState, useEffect, useCallback } from 'react';
import type { ReactNode } from 'react';
import { loadStripe } from '@stripe/stripe-js';
import { Elements, CardElement, useStripe, useElements } from '@stripe/react-stripe-js';
import { Check, CreditCard, ExternalLink, TriangleAlert, X } from 'lucide-react';
import { apiFetch, getStoredUser } from '../../lib/api';
import { can } from '../../lib/permissions';
import type { BillingInfo, PlanId, StripeInvoice } from '../../types';
import Badge from '../ui/Badge';
import type { BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import { ConfirmDialog } from '../ui/Dialog';
import Icon from '../ui/Icon';
import IconButton from '../ui/IconButton';
import PageState from '../ui/PageState';

const stripePromise = loadStripe(process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY || '');

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(0)}`;
}

/** How much usage a plan includes, said the way the pricing page says it:
 *  Core is the yardstick, bigger plans are multiples of it. */
function planUsage(multiple: number): string {
  return multiple > 1 ? `${multiple}× Core’s usage` : 'A month of everyday use';
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

  const [pendingPlan, setPendingPlan] = useState<PlanId | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  // A plan switch or a top-up waiting on "are you sure?" — both cost money.
  const [confirming, setConfirming] = useState<
    { kind: 'plan'; plan: PlanId } | { kind: 'start'; plan: PlanId } | { kind: 'topup'; units: number } | null
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
  const changePlan = async (plan: PlanId) => {
    const res = await send(() => apiFetch(`/api/billing/${orgId}/plan`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token_plan: plan }),
    }), 'The plan wasn’t changed');
    setBilling(await res.json());
    fetchInvoices();
  };

  const subscribe = async (plan: PlanId) => {
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
  const startPlan = async (plan: PlanId) => {
    const res = await send(() => apiFetch(`/api/billing/${orgId}/subscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token_plan: plan }),
    }), 'The plan wasn’t started');
    setBilling(await res.json());
    setPendingPlan(null);
    fetchInvoices();
  };

  const handleSelectPlan = (plan: PlanId) => {
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
    }), 'The top-up wasn’t bought');
    // Paid: close now; the new allowance and invoice follow when they load.
    fetchBilling();
    fetchInvoices();
  };

  if (loading) return <PageState kind="loading" title="Loading billing…" />;
  if (fetchError) return <PageState kind="error" title={fetchError} action={<Button size="sm" onClick={fetchBilling}>Retry</Button>} />;
  if (!billing) return <PageState kind="empty" title="No billing information available." />;

  const sub = billing.subscription;
  const usage = billing.usage;
  const plans = billing.plans ?? [];
  const usagePercent = Math.max(0, usage.percent ?? 0);
  const meterPercent = Math.min(100, usagePercent);
  const hasSubscription = sub && sub.status && sub.status !== 'trialing';
  const hasPaymentMethod = sub && sub.payment_method_last4;
  const lightMode = usage.light_mode;
  // The bar stays neutral until the "running low" point, then turns amber.
  const nearLimit = lightMode || usagePercent >= 80;
  const currentPlan = plans.find(p => p.id === (sub?.plan ?? usage.plan));
  const planName = currentPlan?.name ?? 'Core';
  const hired = (billing.agent_apps ?? []).filter(a => a.enabled);
  const teamRows = (billing.agent_apps ?? []).length + (billing.priced_apps ?? []).length;
  const topUpCents = billing.top_up?.price_cents ?? 3000;
  const card = hasPaymentMethod
    ? `the ${sub.payment_method_brand ? `${sub.payment_method_brand.toUpperCase()} ` : ''}card ending ${sub.payment_method_last4}`
    : 'your card';
  const upgradeHint = canManage
    ? 'Top up below, or move to a bigger plan,'
    : 'Ask whoever manages billing to top up or move to a bigger plan';

  // What the open "are you sure?" says and does.
  const confirmation = (() => {
    if (confirming?.kind === 'plan') {
      const target = plans.find(p => p.id === confirming.plan);
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
            {target ? `${formatCents(target.price_cents)} a month for ${planUsage(target.multiple).toLowerCase()}` : 'The new plan'}
            {currentPlan ? `, instead of ${currentPlan.name} at ${formatCents(currentPlan.price_cents)}` : ''}.
            {' '}It starts now, and this month’s bill is prorated for the change.
          </p>
        </ConfirmDialog>
      );
    }
    if (confirming?.kind === 'start') {
      const target = plans.find(p => p.id === confirming.plan);
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
            Billing starts today on {card}: {target ? `${formatCents(target.price_cents)} a month` : 'the plan’s monthly price'}, plus {formatCents(billing.agent_price_cents)} a month for each AI agent you’ve hired.
          </p>
        </ConfirmDialog>
      );
    }
    if (confirming?.kind === 'topup') {
      const { units } = confirming;
      const price = formatCents(units * topUpCents);
      return (
        <ConfirmDialog
          title={`Top up for ${price}?`}
          confirmLabel={`Pay ${price}`}
          busyLabel="Paying…"
          onConfirm={() => topUp(units)}
          onClose={() => setConfirming(null)}
        >
          <p style={{ margin: 0 }}>
            {price} is charged to {card} now, and {units === 1 ? 'a month of Core’s usage is' : `${units} months of Core’s usage are`} added straight away. It lasts until the end of this billing period.
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

      {/* Usage — a share of the plan's allowance; never tokens or dollars */}
      <Section
        title="Usage this period"
        meta={sub?.billing_cycle_start ? <span>Since {new Date(sub.billing_cycle_start).toLocaleDateString()}</span> : undefined}
      >
        <div className="n-card" style={{ padding: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', marginBottom: 8, fontVariantNumeric: 'tabular-nums' }}>
            <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>
              {usagePercent >= 100 ? 'All used' : `${Math.round(usagePercent)}% used`}
            </span>
            <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>of what the {planName} plan includes</span>
          </div>
          <div
            role="progressbar"
            aria-label="Usage this period"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(meterPercent)}
            style={{ height: 8, backgroundColor: 'var(--line-soft)', borderRadius: 999, overflow: 'hidden' }}
          >
            <div style={{
              height: '100%', borderRadius: 999, transition: 'width 0.3s',
              width: `${meterPercent}%`,
              backgroundColor: nearLimit ? 'var(--warn)' : 'var(--text-soft)',
            }} />
          </div>
          {lightMode ? (
            <p data-testid="light-mode-notice" style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--warn)', margin: '10px 0 0' }}>
              <Icon icon={TriangleAlert} size="dense" style={{ marginTop: 2 }} />
              <span>
                This period’s usage is used up, so Norm is answering on a lighter, faster model until it resets.
                {' '}{upgradeHint} to bring back the full model.
              </span>
            </p>
          ) : nearLimit ? (
            <p style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--warn)', margin: '10px 0 0' }}>
              <Icon icon={TriangleAlert} size="dense" style={{ marginTop: 2 }} />
              <span>
                Running low. When it’s used up Norm keeps working, on a lighter model, until the period resets.
                {' '}{upgradeHint} to stay on the full model.
              </span>
            </p>
          ) : null}
        </div>
      </Section>

      {/* Plans */}
      <Section
        title="Plan"
        meta={sub?.status ? <Badge tone={subscriptionTone(sub.status)}>{sentence(sub.status)}</Badge> : undefined}
        note={canManage ? undefined : 'Only someone who manages billing — usually the owner — can change the plan or the card, or top up.'}
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 200px), 1fr))', gap: 12 }}>
          {plans.map(plan => {
            const isActive = currentPlan?.id === plan.id;
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
                <div style={{ fontSize: 'var(--fs-xl)', fontWeight: 700, lineHeight: 1.3, color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>
                  {formatCents(plan.price_cents)}
                  <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 400, color: 'var(--muted)' }}> /month</span>
                </div>
                <div style={{ fontSize: 'var(--fs-sm)', color: isActive ? 'var(--text-soft)' : 'var(--muted)' }}>{planUsage(plan.multiple)}</div>
              </div>
            );
          })}
        </div>
      </Section>

      {/* Monthly cost breakdown */}
      <Section title="Monthly cost" footnote="Reports and App Builder come with every plan.">
        <div className="n-card" style={{ overflow: 'hidden', fontSize: 'var(--fs-base)' }}>
          <CostRow label={`Plan (${planName})`} amount={`${formatCents(billing.cost_breakdown.plan)}/mo`} />
          <CostRow
            label={hired.length > 0 ? `AI team (${hired.map(a => a.name).join(', ')})` : 'AI team (none hired)'}
            amount={`${formatCents(billing.cost_breakdown.agents)}/mo`}
          />
          {(billing.cost_breakdown.apps ?? 0) > 0 && (
            <CostRow
              label={`Apps (${(billing.priced_apps ?? []).map(a => a.name).join(', ')})`}
              amount={`${formatCents(billing.cost_breakdown.apps ?? 0)}/mo`}
            />
          )}
          <CostRow total label="Total" amount={`${formatCents(billing.monthly_cost_cents)}/mo`} />
        </div>
      </Section>

      {/* Your AI team — hired and paid state; managed on Settings → AI team */}
      <Section
        title="Your AI team"
        footnote="Hire and retire AI agents — and switch their Apps — in Settings → AI team."
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
          title="Top up"
          note={(
            <>
              Each top-up adds as much usage as Core includes in a month, for {formatCents(topUpCents)}. It lasts until the end of this billing period.
              {!hasPaymentMethod && <span style={{ color: 'var(--error)' }}> Add a payment method first.</span>}
            </>
          )}
        >
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {[1, 2, 3].map(units => (
              <Button
                key={units}
                data-testid={`topup-${units}`}
                onClick={() => setConfirming({ kind: 'topup', units })}
                disabled={actionLoading !== null || !hasPaymentMethod}
                style={{ fontVariantNumeric: 'tabular-nums' }}
              >
                {units === 1 ? 'One top-up' : `${units} top-ups`} — {formatCents(units * topUpCents)}
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
