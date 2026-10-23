import { headers } from 'next/headers';
import { AGENTS, type Agent, type App, type ConnectionKey } from './apps';

// Apps venues built in Norm and published, once approved
// (GET /api/public/marketplace/apps). Filed under their AI agent beside
// Norm's own. Fetched from this site's own address, so every environment
// shows its own; anything that goes wrong shows none rather than failing
// the page.

/** Connectors an app's actions use → the systems this site names. */
const SYSTEMS: Record<string, ConnectionKey> = { loadedhub: 'loaded', xero: 'xero', bamboohr: 'bamboohr' };

/** Only our own hosts: the Host header comes from the request. */
const OURS = /^(localhost|127\.0\.0\.1)(:\d+)?$|(^|\.)bettercallnorm\.com$/;

interface Row { slug: string; name: string; description: string; agent: string; connections: string[]; price_cents: number }

export async function publishedApps(): Promise<{ app: App; agent: Agent }[]> {
  try {
    const host = (await headers()).get('host') ?? '';
    if (!OURS.test(host)) return [];
    const origin = `${host.endsWith('bettercallnorm.com') ? 'https' : 'http'}://${host}`;
    const res = await fetch(`${origin}/api/public/marketplace/apps`, {
      next: { revalidate: 300 },
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) return [];
    const rows: Row[] = (await res.json()).apps ?? [];
    return rows.flatMap(r => {
      const agent = AGENTS.find(a => a.slug === r.agent.replace(/_/g, '-'));
      if (!agent) return [];
      const works = [...new Set(r.connections.map(c => SYSTEMS[c]).filter(Boolean))];
      const price = r.price_cents > 0 ? `$${Math.round(r.price_cents / 100)} a month` : undefined;
      return [{ agent, app: { slug: r.slug, name: r.name, blurb: r.description, about: r.description, works, asks: [], price } }];
    });
  } catch {
    return [];
  }
}

export async function findPublished(slug: string) {
  return (await publishedApps()).find(entry => entry.app.slug === slug);
}
