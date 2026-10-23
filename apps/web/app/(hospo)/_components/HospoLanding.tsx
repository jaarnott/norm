import type { ReactNode } from 'react';
import Link from 'next/link';
import { LOGIN_HREF, SIGNUP_HREF } from '../../(marketing)/_components/links';

/**
 * One Hospo app's landing page: hero with the app's board, its parts, what
 * adding Norm does, and its plans. Hospo HR and Hospo Functions share it and
 * differ in content and accent (hospo.css).
 */

export interface Plan { name: string; price: string; per: string; blurb: string; items: string[]; featured?: boolean }

export interface HospoProduct {
  variant: 'hr' | 'functions';
  name: string;
  /** Dark header and hero (Hospo Functions) or light (Hospo HR). */
  darkHero?: boolean;
  nav: [string, string][];
  title: string;
  lead: string;
  board: ReactNode;
  boardLabel: string;
  parts: { heading: string; lead: string; items: { id?: string; name: string; body: string; tags?: string }[] };
  norm: { heading: string; body: string; note: string; chat: ReactNode; chatLabel: string };
  plans: Plan[];
  addOn: { agent: string; points: string[] };
  other: { name: string; href: string };
}

function Mark({ size }: { size: number }) {
  return <span aria-hidden="true" className="hospo-mark" style={{ width: size, height: size }}><span /></span>;
}

function WorksWithNorm() {
  return (
    <Link href="/" className="hospo-works-norm">
      <span aria-hidden="true" className="hospo-norm-mark">N</span>Works with Norm
    </Link>
  );
}

function PlanCard({ plan }: { plan: Plan }) {
  const cta = plan.price === '$0' ? 'Start free' : plan.price === 'Custom' ? 'Talk to us' : 'Start a free trial';
  return (
    <div className={plan.featured ? 'hospo-plan hospo-plan--featured' : 'hospo-plan'}>
      <div className="hospo-stack" style={{ gap: 4 }}>
        <span className="hospo-plan-name">{plan.name}</span>
        <p>{plan.blurb}</p>
      </div>
      <div className="hospo-plan-price"><strong>{plan.price}</strong><span>{plan.per}</span></div>
      <ul>{plan.items.map(item => <li key={item}>{item}</li>)}</ul>
      <Link href={SIGNUP_HREF} className={plan.featured ? 'hospo-btn hospo-btn--primary' : 'hospo-btn hospo-btn--secondary'}>{cta}</Link>
    </div>
  );
}

export default function HospoLanding({ p }: { p: HospoProduct }) {
  const dark = !!p.darkHero;
  return (
    <div className={`hospo hospo--${p.variant}`}>
      <header className={dark ? 'hospo-header hospo-header--navy' : 'hospo-header'}>
        <nav aria-label="Main" className="hospo-wrap hospo-nav">
          <Link href={`/hospo-${p.variant}`} className="hospo-brand">
            <Mark size={34} />
            <span className="hospo-brand-name">{p.name}</span>
          </Link>
          <div className="hospo-nav-links">
            {p.nav.map(([label, href]) => <a key={href} href={href} className="hospo-hide-phone">{label}</a>)}
            <WorksWithNorm />
            <Link href={LOGIN_HREF} className="hospo-hide-phone">Log in</Link>
            <Link href={SIGNUP_HREF} className={dark ? 'hospo-btn hospo-btn--accent hospo-btn--sm' : 'hospo-btn hospo-btn--primary hospo-btn--sm'}>Get started</Link>
          </div>
        </nav>
      </header>

      <main>
        <section className={dark ? 'hospo-section hospo-section--navy' : 'hospo-section'} style={{ paddingTop: 64 }}>
          <div className="hospo-wrap hospo-split">
            <div className="hospo-stack hospo-stack--lg">
              <h1>{p.title}</h1>
              <p className="hospo-lead">{p.lead}</p>
              <div className="hospo-actions">
                <Link href={SIGNUP_HREF} className={dark ? 'hospo-btn hospo-btn--accent' : 'hospo-btn hospo-btn--primary'}>Get started</Link>
                <a href="#with-norm" className="hospo-btn hospo-btn--secondary">See it working with Norm</a>
              </div>
              <p className="hospo-small" style={dark ? { color: '#9fb2c3' } : undefined}>Free for one venue, no card needed. Add Norm’s AI agent whenever you’re ready.</p>
            </div>
            <div className={dark ? undefined : 'hospo-board-field'} role="img" aria-label={p.boardLabel}>{p.board}</div>
          </div>
        </section>

        <section className="hospo-section hospo-section--tint">
          <div className="hospo-wrap hospo-stack" style={{ gap: 36 }}>
            <div className="hospo-stack" style={{ gap: 12, maxWidth: 760 }}>
              <h2>{p.parts.heading}</h2>
              <p className="hospo-body">{p.parts.lead}</p>
            </div>
            <div className="hospo-parts">
              {p.parts.items.map(item => (
                <div key={item.name} id={item.id} className="hospo-part">
                  <h3>{item.name}</h3>
                  <p>{item.body}</p>
                  {item.tags && <div className="hospo-part-tags">{item.tags}</div>}
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="with-norm" className={dark ? 'hospo-section' : 'hospo-section hospo-section--navy'}>
          <div className="hospo-wrap hospo-split">
            <div className="hospo-stack hospo-stack--lg">
              <WorksWithNorm />
              <h2>{p.norm.heading}</h2>
              <p className="hospo-body">{p.norm.body}</p>
              <p className="hospo-body" style={{ fontSize: 16 }}>{p.norm.note}</p>
            </div>
            <div className="hospo-chat" role="img" aria-label={p.norm.chatLabel} style={dark ? { border: '1px solid #e3e9ee', boxShadow: '0 18px 50px rgba(17, 42, 70, 0.12)' } : undefined}>
              {p.norm.chat}
            </div>
          </div>
        </section>

        <section id="pricing" className="hospo-section hospo-section--tint">
          <div className="hospo-wrap hospo-stack" style={{ gap: 32 }}>
            <div className="hospo-stack" style={{ gap: 12, maxWidth: 760 }}>
              <h2>Free to start. Pay when you grow.</h2>
              <p className="hospo-body">{p.name} works on its own. Start free with one venue, move up when you need more, and add Norm whenever you want an AI agent doing the legwork.</p>
            </div>
            <div className="hospo-plans">{p.plans.map(plan => <PlanCard key={plan.name} plan={plan} />)}</div>
            <div className="hospo-addon">
              <div className="hospo-stack" style={{ gap: 12 }}>
                <span className="hospo-works-norm" style={{ alignSelf: 'flex-start' }}><span aria-hidden="true" className="hospo-norm-mark">N</span>Add-on</span>
                <h3>Add Norm’s {p.addOn.agent} agent</h3>
                <p>An AI agent that works inside {p.name} on any plan. Norm starts at $49 a month, plus $10 for the {p.addOn.agent} agent, free for the first 7 days.</p>
              </div>
              <div className="hospo-stack" style={{ gap: 14 }}>
                <ul>{p.addOn.points.map(point => <li key={point}>{point}</li>)}</ul>
                <Link href="/" className="hospo-btn hospo-btn--accent" style={{ alignSelf: 'flex-start', minHeight: 48 }}>See what Norm adds</Link>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="hospo-footer">
        <div className="hospo-wrap hospo-footer-row">
          <span className="hospo-stack" style={{ gap: 4 }}>
            <span className="hospo-footer-brand"><Mark size={22} />{p.name}</span>
            <span>Made by Hospo Apps. Works with Norm. © {new Date().getFullYear()} Hospo Apps.</span>
          </span>
          <span className="hospo-footer-links">
            <Link href={p.other.href}>{p.other.name}</Link>
            <Link href="/marketplace">On the Norm App Marketplace</Link>
            <Link href="/privacy">Privacy</Link>
            <Link href="/terms">Terms</Link>
          </span>
        </div>
      </footer>
    </div>
  );
}

/** A Norm conversation: an optional question, Norm's reply and an approval card. */
export function NormChat({ agent, ask, reply, approval, buttons }: {
  agent: string; ask?: string; reply: string; approval: string; buttons: [string, string];
}) {
  return (
    <>
      <div className="hospo-chat-bar">
        <span className="hospo-chat-title"><span aria-hidden="true" className="hospo-norm-mark">N</span>{agent}</span>
        <span className="hospo-chat-venue">Harbourside Kitchen</span>
      </div>
      <div className="hospo-chat-body">
        {ask && <span className="hospo-chat-ask">{ask}</span>}
        <span>{reply}</span>
        <span className="hospo-chat-card">
          <span className="hospo-chat-card-head" style={{ display: 'flex' }}><span>Approval needed</span>{approval}</span>
          <span className="hospo-chat-card-actions" style={{ display: 'flex' }}><span>{buttons[0]}</span><span>{buttons[1]}</span></span>
        </span>
      </div>
    </>
  );
}

/** A product board: a title row, then three columns of cards. */
export function Board({ title, sub, pill, pillStyle, cols }: {
  title: string; sub: string; pill: string; pillStyle: { color: string; background: string };
  cols: { label: string; cards: { name: string; note: string; hot?: boolean }[]; more?: string }[];
}) {
  return (
    <div className="hospo-board">
      <div className="hospo-board-head">
        <span><strong>{title}</strong><span>{sub}</span></span>
        <span className="hospo-pill" style={pillStyle}>{pill}</span>
      </div>
      <div className="hospo-board-cols">
        {cols.map(col => (
          <div key={col.label} className="hospo-board-col">
            <span>{col.label}</span>
            {col.cards.map(card => (
              <span key={card.name} className={card.hot ? 'hospo-board-card hospo-board-card--hot' : 'hospo-board-card'}>
                <strong>{card.name}</strong>
                <span>{card.note}</span>
              </span>
            ))}
            {col.more && <span className="hospo-board-card"><span>{col.more}</span></span>}
          </div>
        ))}
      </div>
    </div>
  );
}
