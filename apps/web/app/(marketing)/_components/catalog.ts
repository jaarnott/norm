import { headers } from 'next/headers';
import { AGENTS, type Agent, type App, type ConnectionKey } from './apps';
import { publishedApps } from './published';

// The App Marketplace, from Norm's live catalog (GET /api/public/marketplace;
// owner, 23 Oct 2026): the AI agents and the apps each one has, in the words
// the catalog holds. The site keeps only how things look — each agent's
// colour and photo, each system's logo (apps.ts). If the catalog can't be
// reached the page shows apps.ts's own copy rather than nothing.

/** Only our own hosts: the Host header comes from the request. */
const OURS = /^(localhost|127\.0\.0\.1)(:\d+)?$|(^|\.)bettercallnorm\.com$/;

/** This site's own address, for reading its API from the server. */
export async function ownOrigin(): Promise<string | null> {
  const host = (await headers()).get('host') ?? '';
  if (!OURS.test(host)) return null;
  return `${host.endsWith('bettercallnorm.com') ? 'https' : 'http'}://${host}`;
}

/** The systems by the names the catalog gives them. */
const SYSTEM_KEYS: Record<string, ConnectionKey> = {
  Loaded: 'loaded',
  Xero: 'xero',
  BambooHR: 'bamboohr',
  'Hospo HR': 'hospo-hr',
  'Hospo Functions': 'hospo-functions',
};

interface CatalogApp {
  slug: string;
  name: string;
  agent: string;
  description: string;
  listing: { blurb: string; about: string; asks: string[]; maker?: string };
  works_with: string[];
  price_cents: number;
}

function toApp(row: CatalogApp): App {
  return {
    slug: row.slug,
    name: row.name,
    maker: row.listing.maker ?? 'Hospo Apps',
    blurb: row.listing.blurb,
    about: row.listing.about,
    works: row.works_with.map((w) => SYSTEM_KEYS[w]).filter(Boolean),
    asks: row.listing.asks ?? [],
    price: row.price_cents > 0 ? `$${Math.round(row.price_cents / 100)} a month` : undefined,
  };
}

/** Norm's own agents and apps, live — or apps.ts's copy if the API is out of reach. */
async function ownAgents(): Promise<Agent[]> {
  try {
    const origin = await ownOrigin();
    if (!origin) return AGENTS;
    const res = await fetch(`${origin}/api/public/marketplace`, {
      next: { revalidate: 300 },
      signal: AbortSignal.timeout(4000),
    });
    if (!res.ok) return AGENTS;
    const rows: CatalogApp[] = (await res.json()).apps ?? [];
    if (!rows.length) return AGENTS;
    // The site's agents, in its order and look, each with the catalog's apps.
    return AGENTS.map((agent) => ({
      ...agent,
      apps: rows.filter((r) => r.agent.replace(/_/g, '-') === agent.slug).map(toApp),
    })).filter((agent) => agent.apps.length > 0);
  } catch {
    return AGENTS;
  }
}

/** The marketplace: each agent's own apps, then the ones venues published. */
export async function marketplace(): Promise<{ agents: (Agent & { own: number; byVenues: number })[] }> {
  const [agents, published] = await Promise.all([ownAgents(), publishedApps()]);
  return {
    agents: agents.map((agent) => {
      const byVenues = published.filter((p) => p.agent.slug === agent.slug).map((p) => p.app);
      return { ...agent, own: agent.apps.length, byVenues: byVenues.length, apps: [...agent.apps, ...byVenues] };
    }),
  };
}

/** One app and its agent, Norm's or a venue's. */
export async function findMarketplaceApp(slug: string) {
  const { agents } = await marketplace();
  for (const agent of agents) {
    const app = agent.apps.find((a) => a.slug === slug);
    if (app) return { app, agent };
  }
  return undefined;
}
