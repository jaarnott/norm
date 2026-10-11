import type { Metadata } from 'next';
import Link from 'next/link';
import { SIGNUP_HREF } from '../_components/links';

export const metadata: Metadata = {
  title: { absolute: 'Pricing — Norm' },
  description:
    'Free for 7 days. Then a plan from $50 a month, $10 per venue, and the AI team members you need. Every plan includes every feature.',
};

// Keep in step with apps/api/app/services/billing_service.py (PLAN_QUOTAS,
// VENUE_PRICE_CENTS, TOPUP_*) and the team members' prices in
// scripts/sync_marketplace_catalog.py.
const PLANS = [
  { name: 'Basic', price: 50, tokens: '1 million', fit: 'For one venue finding its feet with Norm.' },
  { name: 'Standard', price: 100, tokens: '3 million', fit: 'For a busy venue using Norm every day. Our recommendation.', featured: true },
  { name: 'Max', price: 200, tokens: '10 million', fit: 'For groups running several venues through Norm.' },
];

// The swatch is the member's colour across the site (marketing.css --mkt-*).
const TEAM: { name: string; what: string; price: string | null; swatch: string }[] = [
  { name: 'HR', what: 'Hiring, training and employee records', price: '$10 / month', swatch: 'var(--mkt-blush)' },
  { name: 'Procurement', what: 'Orders, invoices and stock', price: '$5 / month', swatch: 'var(--mkt-saffron)' },
  { name: 'Time & Attendance', what: 'Rosters and labour', price: 'Free', swatch: 'var(--mkt-sky)' },
  { name: 'Executive Chef', what: 'Recipes, menus and margins', price: 'Free', swatch: 'var(--mkt-sage)' },
  { name: 'Marketing', what: 'Campaigns and socials', price: 'Free', swatch: 'var(--mkt-coral)' },
  { name: 'Reports', what: 'Sales, labour, stock and dashboards', price: null, swatch: 'var(--mkt-leaf)' },
  { name: 'App Builder', what: 'Build your own tools by describing them', price: null, swatch: 'var(--mkt-ink)' },
];

const FAQ: { q: string; a: string }[] = [
  {
    q: "What's a token?",
    a: "Tokens measure how much work Norm does. A quick question uses a few thousand. A long job, like checking a month of invoices, can use a few hundred thousand. Settings shows exactly what you've used.",
  },
  { q: 'How does the free week work?', a: 'Your first 7 days are free. Pick a plan any time to keep going.' },
  {
    q: 'What happens if we run out of tokens?',
    a: "Rosters, boards and editors keep working. Norm's AI pauses until you top up or your plan renews.",
  },
  { q: 'Can we change plans?', a: 'Yes, up or down at any time. The change is prorated on your bill.' },
  { q: 'Which systems does Norm work with?', a: 'LoadedHub, Bidfood, BambooHR, Brevo and Metricool. Tell us what else you use.' },
];

export default function PricingPage() {
  return (
    <>
      <section className="mkt-section mkt-section--cream" style={{ paddingTop: 56, paddingBottom: 72 }}>
        <div className="mkt-wrap mkt-stack mkt-stack--lg">
          <h1 className="mkt-h1 mkt-h1--page">Pay for the work Norm does.</h1>
          <p className="mkt-lead">
            Your first 7 days are free. After that, every plan includes every feature, and plans differ only in how much Norm can do each month. Add the team members you need and the venues you run.
          </p>
        </div>
      </section>

      <section className="mkt-section" style={{ paddingTop: 64, paddingBottom: 32 }}>
        <div className="mkt-wrap mkt-stack" style={{ gap: 20 }}>
          <div className="mkt-plans">
            {PLANS.map(plan => (
              <div key={plan.name} className={plan.featured ? 'mkt-plan mkt-plan--featured' : 'mkt-plan'}>
                <div className="mkt-stack" style={{ gap: 4 }}>
                  <strong style={{ fontSize: 20 }}>{plan.name}</strong>
                  <span className="mkt-text" style={{ fontSize: 15 }}>{plan.fit}</span>
                </div>
                <div className="mkt-plan-price"><strong>${plan.price}</strong><span>a month</span></div>
                <div style={{ fontSize: 15, fontWeight: 600 }}>{plan.tokens} tokens a month</div>
                <Link
                  href={SIGNUP_HREF}
                  className={plan.featured ? 'mkt-btn mkt-btn--primary mkt-btn--block' : 'mkt-btn mkt-btn--secondary mkt-btn--block'}
                >
                  Start free
                </Link>
              </div>
            ))}
          </div>
          <p className="mkt-small" style={{ fontSize: 15 }}>
            All plans include every feature, every integration, approvals, scheduled tasks, phone and iPad.
          </p>
        </div>
      </section>

      <section className="mkt-section" style={{ paddingTop: 64 }}>
        <div className="mkt-wrap mkt-split" style={{ alignItems: 'start', gap: 32 }}>
          <div className="mkt-stack" style={{ gap: 14 }}>
            <h2 className="mkt-h2 mkt-h2--sm">Your AI team</h2>
            <p className="mkt-text">Hire the team members you need. Prices are per organisation, across all your venues.</p>
            <div className="mkt-list">
              {TEAM.map(member => (
                <div key={member.name} className="mkt-list-row">
                  <span className="mkt-list-name">
                    <span className="mkt-swatch" style={{ background: member.swatch }} aria-hidden="true" />
                    <span><strong>{member.name}</strong><small>{member.what}</small></span>
                  </span>
                  {member.price ? <strong>{member.price}</strong> : <span className="mkt-badge mkt-badge--neutral" style={{ fontSize: 13 }}>Included</span>}
                </div>
              ))}
            </div>
          </div>

          <div className="mkt-stack" style={{ gap: 14 }}>
            <h2 className="mkt-h2 mkt-h2--sm">Venues and top-ups</h2>
            <p className="mkt-text">Each venue connects its own systems.</p>
            <div className="mkt-list">
              <div className="mkt-list-row">
                <span className="mkt-list-name"><span><strong>Each venue</strong><small>Its own connections and reporting</small></span></span>
                <strong>$10 / month</strong>
              </div>
              <div className="mkt-list-row">
                <span className="mkt-list-name"><span><strong>Token top-up</strong><small>Buy more any time; they last to the end of your billing month</small></span></span>
                <strong>$10 per 500K</strong>
              </div>
            </div>
            <div className="mkt-callout">
              <strong>Two venues on Standard, with Procurement and HR</strong>
              <span className="mkt-num">$100 plan + $20 venues + $15 team = $135 a month</span>
            </div>
          </div>
        </div>
      </section>

      <section className="mkt-section mkt-section--cream">
        <div className="mkt-wrap mkt-split" style={{ alignItems: 'start' }}>
          <h2 className="mkt-h2">Questions</h2>
          <div className="mkt-faq">
            {FAQ.map((item, i) => (
              <details key={item.q} open={i === 0}>
                <summary>{item.q}</summary>
                <p>{item.a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
