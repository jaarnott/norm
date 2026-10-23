import Link from 'next/link';
import { CONNECTIONS, type Agent, type App, type ConnectionKey } from './apps';

/** A system's logo, or the Hospo mark (a square with a square or a dot). */
export function ConnectionMark({ name, size }: { name: ConnectionKey; size: number }) {
  const c = CONNECTIONS[name];
  if (c.logo) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- small static logo; no optimiser in the standalone image
      <img src={c.logo} alt="" width={size} height={size} className="mkt-logo" style={{ width: size, height: size }} />
    );
  }
  const inner = Math.round(size * 0.46);
  return (
    <span aria-hidden="true" className={`mkt-hospo-mark mkt-hospo-mark--${name}`} style={{ width: size, height: size }}>
      <span style={{ width: inner, height: inner }} />
    </span>
  );
}

export function WorksWith({ works }: { works: ConnectionKey[] }) {
  return (
    <span className="mkt-works">
      <span className="mkt-works-label">Works with</span>
      {works.map(w => (
        <span key={w} className="mkt-works-chip">
          <ConnectionMark name={w} size={16} />
          {CONNECTIONS[w].name}
        </span>
      ))}
    </span>
  );
}

/** One app on the marketplace page: its job, maker, one line and what it works with. */
export function AppCard({ app, agent }: { app: App; agent: Agent }) {
  return (
    <Link href={`/marketplace/${app.slug}`} className="mkt-app-card">
      <span className="mkt-app-head">
        <span aria-hidden="true" className={`mkt-app-tile ${agent.tone}`}>{app.name[0]}</span>
        <span className="mkt-app-title">
          <strong>{app.name}</strong>
          <span>{app.maker ? `by ${app.maker}` : 'Made by a venue on Norm'}</span>
        </span>
      </span>
      <span className="mkt-app-blurb">{app.blurb}</span>
      {app.works.length > 0 && <WorksWith works={app.works} />}
      <span className="mkt-app-foot">
        <span>{app.price ?? 'Free'}</span>
        <span className="mkt-app-view">View</span>
      </span>
    </Link>
  );
}
