import type { Metadata } from 'next';
import Link from 'next/link';
import { SIGNUP_HREF } from '../_components/links';

export const metadata: Metadata = {
  title: { absolute: 'Pricing — Norm' },
  description:
    'Free for 7 days. Then from $49 a month, plus $10 for each AI team member you hire. No charge per venue, and every plan includes every feature.',
};

// Keep in step with apps/api/app/services/billing_service.py (PLANS,
// AGENT_PRICE_CENTS, TOPUP_*) and scripts/sync_marketplace_catalog.py. The
// multiples are exact: Pro includes 4× Core's usage and Max 11× (tested in
// tests/test_usage_plans.py). Usage is never quoted in tokens or dollars.
const PLANS = [
  { name: 'Core', price: 49, usage: 'Reports and everyday use', fit: 'For a venue getting to know Norm.' },
  { name: 'Pro', price: 149, usage: '4× the usage of Core', fit: 'For busy venues using Norm all day. Our recommendation.', featured: true },
  { name: 'Max', price: 299, usage: '11× the usage of Core', fit: 'For groups running several venues through Norm.' },
];

const MEMBER_PRICE = '$10 / month';

// The swatch is the member's colour across the site (marketing.css --mkt-*).
const TEAM: { name: string; what: string; included?: boolean; swatch: string }[] = [
  { name: 'Procurement', what: 'Orders, invoices and stock', swatch: 'var(--mkt-saffron)' },
  { name: 'Time & Attendance', what: 'Rosters and labour', swatch: 'var(--mkt-sky)' },
  { name: 'HR', what: 'Hiring, training and employee records', swatch: 'var(--mkt-blush)' },
  { name: 'Executive Chef', what: 'Recipes, menus and margins', swatch: 'var(--mkt-sage)' },
  { name: 'Marketing', what: 'Campaigns and socials', swatch: 'var(--mkt-coral)' },
  { name: 'Reports', what: 'Sales, labour, stock and dashboards', included: true, swatch: 'var(--mkt-leaf)' },
  { name: 'App Builder', what: 'Build your own tools by describing them', included: true, swatch: 'var(--mkt-ink)' },
];

const FAQ: { q: string; a: string }[] = [
  {
    q: 'How is usage measured?',
    a: "By how much work Norm does for you. A quick question takes very little; checking a month of invoices takes a lot more. Settings shows how much of this month's usage you've had, as a simple percentage.",
  },
  {
    q: 'What happens if we use it all?',
    a: "Norm keeps working. For the rest of the month it answers on a lighter, faster model: fine for everyday questions, less thorough on big jobs. Top up for $30 or move to a bigger plan to bring back the full model straight away. Rosters, boards and editors are never affected.",
  },
  {
    q: 'Which plan do we need?',
    a: "Start on Core. Settings shows how much of each month you're using; if you regularly run out, Pro gives you 4× as much and Max 11×.",
  },
  { q: 'Do we pay per venue?', a: 'No. Connect every venue you run. Plans and team members are priced per organisation, across all your venues.' },
  { q: 'How does the free week work?', a: 'Your first 7 days are free. Pick a plan any time to keep going.' },
  { q: 'Can we change plans?', a: 'Yes, up or down at any time. The change is prorated on your bill.' },
  { q: 'Which systems does Norm work with?', a: 'LoadedHub, Bidfood, BambooHR, Brevo and Metricool. Tell us what else you use.' },
];

export default function PricingPage() {
  return (
    <>
      <section className="mkt-section mkt-section--cream" style={{ paddingTop: 56, paddingBottom: 72 }}>
        <div className="mkt-wrap mkt-stack mkt-stack--lg">
          <h1 className="mkt-h1 mkt-h1--page">A plan, and the team you need.</h1>
          <p className="mkt-lead">
            Your first 7 days are free. After that, pick a plan for how much you&apos;ll use Norm, and hire the AI team members you need at $10 a month each. Every plan includes every feature and reports, and there&apos;s no charge per venue.
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
                <div style={{ fontSize: 15, fontWeight: 600 }}>{plan.usage}</div>
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
            All plans include every feature, every integration, reports, approvals, scheduled tasks, phone and iPad.
          </p>
        </div>
      </section>

      <section className="mkt-section" style={{ paddingTop: 64 }}>
        <div className="mkt-wrap mkt-split" style={{ alignItems: 'start', gap: 32 }}>
          <div className="mkt-stack" style={{ gap: 14 }}>
            <h2 className="mkt-h2 mkt-h2--sm">Your AI team</h2>
            <p className="mkt-text">Hire the team members you need. Each is priced per organisation, across all your venues.</p>
            <div className="mkt-list">
              {TEAM.map(member => (
                <div key={member.name} className="mkt-list-row">
                  <span className="mkt-list-name">
                    <span className="mkt-swatch" style={{ background: member.swatch }} aria-hidden="true" />
                    <span><strong>{member.name}</strong><small>{member.what}</small></span>
                  </span>
                  {member.included
                    ? <span className="mkt-badge mkt-badge--neutral" style={{ fontSize: 13 }}>Included</span>
                    : <strong>{MEMBER_PRICE}</strong>}
                </div>
              ))}
            </div>
          </div>

          <div className="mkt-stack" style={{ gap: 14 }}>
            <h2 className="mkt-h2 mkt-h2--sm">Busy month?</h2>
            <p className="mkt-text">If you use your plan&apos;s month early, Norm keeps working on a lighter model. A top-up brings back the full model.</p>
            <div className="mkt-list">
              <div className="mkt-list-row">
                <span className="mkt-list-name"><span><strong>Top-up</strong><small>As much again as Core includes; lasts to the end of your billing month</small></span></span>
                <strong>$30</strong>
              </div>
              <div className="mkt-list-row">
                <span className="mkt-list-name"><span><strong>Venues</strong><small>Connect as many as you run</small></span></span>
                <strong>No charge</strong>
              </div>
            </div>
            <div className="mkt-callout">
              <strong>Pro, with Procurement and Time &amp; Attendance</strong>
              <span className="mkt-num">$149 plan + $20 team = $169 a month, for every venue you run</span>
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
