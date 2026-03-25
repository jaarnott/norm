'use client';

import Link from 'next/link';
import { useBreakpoint } from '../../hooks/useBreakpoint';

const PLANS = [
  {
    name: 'Basic', price: 50, tokens: '1M', tokensNum: '1,000,000',
    desc: 'For small venues getting started with AI operations.',
    features: ['1 million tokens/month', 'All connectors', 'Task history', 'Email support'],
  },
  {
    name: 'Standard', price: 100, tokens: '3M', tokensNum: '3,000,000', popular: true,
    desc: 'For growing operations that need more capacity.',
    features: ['3 million tokens/month', 'All connectors', 'Automated tasks', 'Priority support'],
  },
  {
    name: 'Max', price: 200, tokens: '10M', tokensNum: '10,000,000',
    desc: 'For high-volume multi-venue hospitality groups.',
    features: ['10 million tokens/month', 'All connectors', 'Automated tasks', 'Dedicated support'],
  },
];

const ADDONS = [
  { name: 'Per Venue', price: '$10/mo', desc: 'Each venue connected to the platform' },
  { name: 'HR Agent', price: '$10/mo', desc: 'Hiring, onboarding, roster management' },
  { name: 'Procurement Agent', price: '$5/mo', desc: 'Stock tracking, purchase orders, suppliers' },
  { name: 'Reports Agent', price: 'Free', desc: 'Sales reports, analytics, automated reporting' },
  { name: 'Token Top-Up', price: '$10 / 500K', desc: 'Buy more tokens anytime if you run out' },
];

const FAQ = [
  { q: 'What are tokens?', a: 'Tokens are the units of AI processing. Every message you send and response you receive uses tokens. Typical conversations use 2,000-10,000 tokens depending on complexity.' },
  { q: 'Can I change plans?', a: 'Yes, you can upgrade or downgrade anytime. Changes are prorated to your billing cycle.' },
  { q: 'What happens when I run out of tokens?', a: "You can still use the UI components like the roster editor and hiring board. AI-powered features pause until you top up or your plan renews next month." },
  { q: 'Which systems does Norm integrate with?', a: 'Norm connects to LoadedHub (rostering, stock), BambooHR (HR, hiring), and supplier systems like Bidfood. We can add custom connectors for your other tools.' },
  { q: 'Is there a free trial?', a: 'Yes, new accounts start with a trial period so you can explore the platform before committing to a plan.' },
];

export default function PricingPage() {
  const { isMobile } = useBreakpoint();
  const sectionStyle = { maxWidth: 1000, margin: '0 auto', padding: isMobile ? '0 1rem' : '0 2rem' };

  return (
    <div>
      <section style={{ ...sectionStyle, paddingTop: isMobile ? '2.5rem' : '4rem', paddingBottom: '3rem', textAlign: 'center' }}>
        <h1 style={{ fontSize: isMobile ? '1.8rem' : '2.5rem', fontWeight: 800, marginBottom: '0.75rem', color: '#2d2a26' }}>
          Simple, transparent pricing
        </h1>
        <p style={{ color: '#999', fontSize: isMobile ? '1rem' : '1.1rem', maxWidth: 500, margin: '0 auto' }}>
          Pick a token plan, add the agents you need, and connect your venues.
        </p>
      </section>

      {/* Plans */}
      <section style={{ ...sectionStyle, paddingBottom: '3rem' }}>
        <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(3, 1fr)', gap: '1.25rem' }}>
          {PLANS.map(p => (
            <div key={p.name} style={{
              backgroundColor: '#fff', borderRadius: 12, padding: '2rem 1.5rem', textAlign: 'center',
              border: p.popular ? '2px solid #a08060' : '1px solid #e8e4de',
              position: 'relative',
            }}>
              {p.popular && (
                <div style={{
                  position: 'absolute', top: -10, left: '50%', transform: 'translateX(-50%)',
                  backgroundColor: '#a08060', color: '#fff', fontSize: '0.65rem', fontWeight: 700,
                  padding: '2px 10px', borderRadius: 10, textTransform: 'uppercase',
                }}>Most Popular</div>
              )}
              <div style={{ fontSize: '1rem', fontWeight: 600, color: '#555', marginBottom: '0.5rem' }}>{p.name}</div>
              <div style={{ fontSize: '2.5rem', fontWeight: 800, marginBottom: '0.25rem', color: '#2d2a26' }}>
                ${p.price}<span style={{ fontSize: '1rem', fontWeight: 400, color: '#999' }}>/mo</span>
              </div>
              <div style={{ fontSize: '0.82rem', color: '#999', marginBottom: '1.25rem' }}>{p.tokensNum} tokens</div>
              <p style={{ fontSize: '0.82rem', color: '#aaa', marginBottom: '1.25rem' }}>{p.desc}</p>
              <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 1.5rem', textAlign: 'left' }}>
                {p.features.map(f => (
                  <li key={f} style={{ fontSize: '0.82rem', color: '#666', padding: '0.3rem 0', display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ color: '#a08060' }}>&#10003;</span> {f}
                  </li>
                ))}
              </ul>
              <Link href="/login" style={{
                display: 'block', padding: '0.6rem', borderRadius: 8, textDecoration: 'none', fontWeight: 600, fontSize: '0.85rem',
                ...(p.popular
                  ? { backgroundColor: '#a08060', color: '#fff' }
                  : { border: '1px solid #d0c8be', color: '#666' }
                ),
              }}>
                Get Started
              </Link>
            </div>
          ))}
        </div>
      </section>

      {/* Add-ons */}
      <section style={{ ...sectionStyle, paddingBottom: '3rem' }}>
        <h2 style={{ fontSize: '1.5rem', fontWeight: 700, textAlign: 'center', marginBottom: '2rem', color: '#2d2a26' }}>Add-ons</h2>
        <div style={{ backgroundColor: '#fff', borderRadius: 12, border: '1px solid #e8e4de', overflow: 'hidden' }}>
          {ADDONS.map((a, i) => (
            <div key={a.name} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem',
              padding: isMobile ? '1rem' : '1rem 1.5rem', borderBottom: i < ADDONS.length - 1 ? '1px solid #f0ece6' : 'none',
            }}>
              <div>
                <div style={{ fontSize: '0.9rem', fontWeight: 600, color: '#2d2a26' }}>{a.name}</div>
                <div style={{ fontSize: '0.78rem', color: '#999' }}>{a.desc}</div>
              </div>
              <div style={{ fontSize: '0.95rem', fontWeight: 700, color: a.price === 'Free' ? '#48bb78' : '#2d2a26', whiteSpace: 'nowrap' }}>
                {a.price}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* FAQ */}
      <section style={{ ...sectionStyle, paddingTop: '2rem', paddingBottom: '5rem' }}>
        <h2 style={{ fontSize: '1.5rem', fontWeight: 700, textAlign: 'center', marginBottom: '2rem', color: '#2d2a26' }}>
          Frequently asked questions
        </h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', maxWidth: 700, margin: '0 auto' }}>
          {FAQ.map(f => (
            <div key={f.q} style={{ backgroundColor: '#fff', borderRadius: 10, padding: '1.25rem', border: '1px solid #e8e4de' }}>
              <h3 style={{ fontSize: '0.9rem', fontWeight: 600, color: '#2d2a26', margin: '0 0 0.5rem' }}>{f.q}</h3>
              <p style={{ fontSize: '0.82rem', color: '#888', lineHeight: 1.6, margin: 0 }}>{f.a}</p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
