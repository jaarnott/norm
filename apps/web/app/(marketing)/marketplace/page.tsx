import type { Metadata } from 'next';
import Link from 'next/link';
import { AGENTS, ALL_APPS, CONNECTIONS } from '../_components/apps';
import { AppCard, ConnectionMark } from '../_components/AppParts';
import { publishedApps } from '../_components/published';
import { SIGNUP_HREF } from '../_components/links';

export const metadata: Metadata = {
  title: { absolute: 'App Marketplace — Norm' },
  description:
    'Norm’s AI agents do the work in apps built for each job: ordering, invoices, rosters, recipes, hiring, functions and more. Connect the software you already use and their apps switch on.',
};

const STEPS: [string, string, string][] = [
  ['Connections', 'Connect what you already use', 'Loaded, Xero and BambooHR. Norm works inside them; nothing moves.'],
  ['AI agents', 'Hire the agents you need', 'Procurement, HR, Executive Chef and the rest. Each does a role a large group would hire for.'],
  ['Apps', 'Their apps switch on', 'The screens each job needs. Switch any of them off in Settings.'],
];

const TRUST: [string, string][] = [
  ['Connected per venue', 'Each venue connects its own software. One venue never sees another’s data.'],
  ['Norm asks first', 'An order, a roster change or a bill arrives as a card showing exactly what will happen. You approve it, or tell Norm once that it may.'],
  ['Switch off any time', 'Turning an app off stops Norm using it. Nothing in your software is deleted, and your records stay.'],
];

export default async function MarketplacePage() {
  const connections = Object.values(CONNECTIONS).filter(c => c.listed);
  // Norm's own apps, then the ones venues built and published, under each agent.
  const published = await publishedApps();
  const shelves = AGENTS.map(agent => {
    const byVenues = published.filter(p => p.agent.slug === agent.slug).map(p => p.app);
    return { ...agent, own: agent.apps.length, byVenues: byVenues.length, apps: [...agent.apps, ...byVenues] };
  });
  return (
    <>
      <section className="mkt-section mkt-section--cream" style={{ paddingTop: 56, paddingBottom: 72 }}>
        <div className="mkt-wrap mkt-stack mkt-stack--lg">
          <h1 className="mkt-h1 mkt-h1--page">The App Marketplace</h1>
          <p className="mkt-lead">
            An app for every job in a venue. Norm’s AI agents do the work in apps built for each job: connect the software you already use, hire the agents you need, and their apps switch on.
          </p>
          <nav aria-label="AI agents" className="mkt-agent-strip">
            {shelves.map(agent => (
              <a key={agent.slug} href={`#${agent.slug}`} className={`mkt-agent-chip ${agent.tone}`}>
                <strong>{agent.name}</strong>
                <span>{agent.apps.length} {agent.apps.length === 1 ? 'app' : 'apps'}</span>
              </a>
            ))}
          </nav>
        </div>
      </section>

      <section className="mkt-section" style={{ paddingTop: 64, paddingBottom: 16 }}>
        <div className="mkt-wrap mkt-steps" aria-label="How it works">
          {STEPS.map(([eyebrow, title, body], i) => (
            <div key={eyebrow} className="mkt-step-card">
              <span className="mkt-step-label">{i + 1} · {eyebrow}</span>
              <strong>{title}</strong>
              <span>{body}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="mkt-section" style={{ paddingTop: 64 }}>
        <div className="mkt-wrap mkt-stack mkt-stack--xl">
          <div className="mkt-head">
            <h2 className="mkt-h2">Apps by AI agent</h2>
            <p className="mkt-body">{ALL_APPS.length + published.length} apps. Hire an agent and its apps switch on.</p>
          </div>
          {shelves.map(agent => (
            <div key={agent.slug} id={agent.slug} className="mkt-agent-apps">
              <div className="mkt-agent-apps-head">
                <span aria-hidden="true" className={`mkt-swatch ${agent.tone}`} />
                <h3 className="mkt-h3">{agent.name}</h3>
                <span className="mkt-small">
                  {agent.own} {agent.own === 1 ? 'app' : 'apps'} · hire {agent.name} and {agent.own === 1 ? 'it switches' : 'they switch'} on
                  {agent.byVenues > 0 && ` · ${agent.byVenues} built by venues`}
                </span>
              </div>
              <div className="mkt-app-grid">
                {agent.apps.map(app => <AppCard key={app.slug} app={app} agent={agent} />)}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="mkt-section mkt-section--cream" id="connections">
        <div className="mkt-wrap mkt-stack mkt-stack--lg">
          <div className="mkt-head">
            <h2 className="mkt-h2">Connections</h2>
            <p className="mkt-body">The software you already use. Connect it once for each venue, and Norm’s agents and apps work inside it. Your data stays where it is.</p>
          </div>
          <div className="mkt-connection-grid">
            {connections.map(c => (
              <div key={c.key} className="mkt-connection">
                <ConnectionMark name={c.key} size={48} />
                <span>
                  <strong>{c.name}</strong>
                  <span>{c.what}</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mkt-section">
        <div className="mkt-wrap mkt-split" style={{ alignItems: 'stretch', gap: 16 }}>
          <div className="mkt-field mkt-field--saffron mkt-stack">
            <h2 className="mkt-h2 mkt-h2--sm">Build an app for your venue</h2>
            <p className="mkt-text" style={{ color: '#3a332c' }}>
              Describe the tool you wish you had, like a closing checklist or a waste tracker. App Builder makes it on your real data, with your permissions, and you can share it with your team.
            </p>
            <div className="mkt-actions" style={{ marginTop: 'auto' }}>
              <Link href={SIGNUP_HREF} className="mkt-btn mkt-btn--primary">Start your free week</Link>
            </div>
          </div>
          <div className="mkt-field mkt-stack" style={{ background: 'var(--mkt-cream)', border: '1px solid var(--mkt-line)' }}>
            <h2 className="mkt-h2 mkt-h2--sm">Use something we don’t list?</h2>
            <p className="mkt-text">
              Tell us the software you run and what you’d like Norm to do in it. The apps here started as requests from venues.
            </p>
            <div className="mkt-actions" style={{ marginTop: 'auto' }}>
              <Link href={SIGNUP_HREF} className="mkt-btn mkt-btn--secondary">Start free and tell us</Link>
            </div>
          </div>
        </div>
      </section>

      <section className="mkt-section mkt-section--leaf">
        <div className="mkt-wrap mkt-stack mkt-stack--xl">
          <h2 className="mkt-h2" style={{ maxWidth: 760 }}>Every app works inside Norm’s permissions.</h2>
          <div className="mkt-columns mkt-columns--leaf">
            {TRUST.map(([title, body]) => (
              <div key={title}>
                <h3 className="mkt-h3">{title}</h3>
                <p className="mkt-text">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <div className="mkt-wrap" style={{ paddingBlock: 24 }}>
        <p className="mkt-caption">
          Loaded, Xero and BambooHR are trademarks of their owners. Norm works with them through each venue’s own account.
        </p>
      </div>
    </>
  );
}
