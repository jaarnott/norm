/**
 * What the shell shows a person, from the role's access (`/api/auth/me` →
 * `access`, docs/plans/staff-access.md 11.7). Pure functions, so the rail,
 * the phone menu, the bottom bar and Home all draw from one reading of it.
 *
 * - `agents: null` = every hired AI agent; a list = only those.
 * - `apps: null` = every app that is on; a list = only those (the server has
 *   already dropped what the role refuses from /api/apps, so here `apps` only
 *   matters through `staff`).
 * - `staff` = a role with no AI agent at all. Such a person has only the apps
 *   section, so every app they can see sits there, whatever its audience.
 * - A team app (Kitchen, Front of house, Managers — docs/plans/team-apps-
 *   skeleton.md) is in the section when the role allows it (`allowsTeam`)
 *   and it has at least one screen here.
 */
import type { AccessInfo } from '../types';

export interface AgentLike { id: string }

/** The pseudo-agents that are never an AI agent: Home, Settings and the apps
 *  section. The hired-agent guard leaves them alone. */
export const NON_AGENT: ReadonlySet<string> = new Set(['home', 'settings', 'apps']);

/** An app as /api/apps lists it, as far as the shell needs. */
export interface SectionAppLike {
  slug: string;
  name: string;
  icon?: string | null;
  kind?: 'app' | 'dashboard' | string;
  /** agent (under its AI agent), team (the apps section) or both. */
  audience?: 'agent' | 'team' | 'both' | string | null;
}

/** A team app's screen, as far as the section needs it. */
export interface TeamScreenLike { page_id: string; label: string }

/** A team app as /api/team lists it (useTeam's TeamAppInfo), as far as the
 *  section needs it. */
export interface TeamAppLike<S extends TeamScreenLike = TeamScreenLike> {
  slug: string;
  name: string;
  icon?: string | null;
  screens: S[];
}

/** One tile of the apps section: a team app with its screens, or an
 *  App-platform app (`row` is the /api/apps row it came from, untouched —
 *  the row's own `kind` says app or dashboard). Both carry what a tile draws:
 *  slug, name and icon. */
export type SectionApp<T extends SectionAppLike = SectionAppLike, S extends TeamScreenLike = TeamScreenLike> =
  | { kind: 'team'; slug: string; name: string; icon?: string | null; screens: S[] }
  | { kind: 'app'; slug: string; name: string; icon?: string | null; row: T };

/** Does the role let this person see this AI agent? Home always shows. */
export function allowsAgent(access: AccessInfo | null | undefined, agentId: string): boolean {
  if (agentId === 'home') return true;
  if (!access || access.agents === null || access.agents === undefined) return true;
  return access.agents.includes(agentId);
}

/** The agent tabs this person gets: hired by the organisation AND allowed by
 *  their role. `hired` null = gating inactive, every agent counts as hired. */
export function visibleAgentsFor<T extends AgentLike>(agents: T[], hired: Set<string> | null | undefined, access: AccessInfo | null | undefined): T[] {
  return agents.filter(a => (a.id === 'home' || !hired || hired.has(a.id)) && allowsAgent(access, a.id));
}

/** The name of the landmark that holds the agent tiles and the app tiles,
 *  by what it actually holds: "AI agents", "Apps" or "AI agents and apps".
 *  Home is not an AI agent, so a staff member's row of apps is "Apps". */
export function navLabelFor(agents: AgentLike[], appCount: number): string {
  const hasAgents = agents.some(a => !NON_AGENT.has(a.id));
  if (!hasAgents) return appCount > 0 ? 'Apps' : 'AI agents';
  return appCount > 0 ? 'AI agents and apps' : 'AI agents';
}

/** Does the role let this person see this team app? `apps: null` = every
 *  app the organisation has; unknown access (an older /me payload) = yes. */
export function allowsTeam(access: AccessInfo | null | undefined, slug: string): boolean {
  if (!access || access.apps === null || access.apps === undefined) return true;
  return access.apps.includes(slug);
}

/** The apps that make up the apps section, in the order the rail and the
 *  bottom bar show them: the team apps the role allows that have at least
 *  one screen here (catalog order), then the App-platform apps as before —
 *  those built for the team (audience team or both), and for venue staff
 *  every app they can open at all. Apps with audience agent or both keep
 *  their place under their agent as well. A team app with no screens is not
 *  a tile (nothing to open), though the Roles form still lists it. */
export function sectionAppsFor<T extends SectionAppLike, S extends TeamScreenLike = TeamScreenLike>(
  rows: T[],
  access: AccessInfo | null | undefined,
  teamApps: TeamAppLike<S>[] = [],
): SectionApp<T, S>[] {
  const staff = !!access?.staff;
  const teams: SectionApp<T, S>[] = teamApps
    .filter(t => t.screens.length > 0 && allowsTeam(access, t.slug))
    .map(t => ({ kind: 'team', slug: t.slug, name: t.name, icon: t.icon, screens: t.screens }));
  const apps: SectionApp<T, S>[] = rows
    .filter(r => {
      const audience = r.audience || 'agent';
      if (audience === 'team' || audience === 'both') return true;
      // A dashboard lives on its agent's Dashboard page; it only joins the
      // section when it was built for the team.
      return staff && r.kind !== 'dashboard';
    })
    .map(r => ({ kind: 'app', slug: r.slug, name: r.name, icon: r.icon, row: r }));
  return [...teams, ...apps];
}

/** The rail's and the phone menu's key for a tile (`data-member`): a team
 *  app and an App-platform app never share one. */
export function sectionTileKey(app: { kind: 'team' | 'app'; slug: string }): string {
  return `${app.kind === 'team' ? 'team' : 'app'}:${app.slug}`;
}

export type TeamBarTab =
  | { id: 'today'; label: 'Today' }
  | { id: 'ask'; label: 'Ask Norm' }
  | { id: 'me'; label: 'Me' }
  | { id: 'apps'; label: 'Apps' }
  | { id: `app:${string}`; label: string; slug: string };

/** The bottom bar's tabs: Today · up to two apps (one app + "Apps" when there
 *  are three or more) · Ask Norm · Me. Always five at most, so each tab keeps
 *  room for its label on a 390px phone. `ask: false` leaves Ask Norm out: a
 *  kitchen tablet never chats (plan section 5), so it has no tab for it. */
export function teamBarTabsFor(apps: { slug: string; name: string }[], { ask = true }: { ask?: boolean } = {}): TeamBarTab[] {
  const middle: TeamBarTab[] = [];
  if (apps.length <= 2) {
    for (const a of apps) middle.push({ id: `app:${a.slug}`, label: a.name, slug: a.slug });
  } else {
    middle.push({ id: `app:${apps[0].slug}`, label: apps[0].name, slug: apps[0].slug });
    middle.push({ id: 'apps', label: 'Apps' });
  }
  const tail: TeamBarTab[] = ask ? [{ id: 'ask', label: 'Ask Norm' }, { id: 'me', label: 'Me' }] : [{ id: 'me', label: 'Me' }];
  return [{ id: 'today', label: 'Today' }, ...middle, ...tail];
}

/** "Morning", "Afternoon" or "Evening" for a greeting, by the local hour. */
export function timeOfDayWord(date: Date = new Date()): 'Morning' | 'Afternoon' | 'Evening' {
  const h = date.getHours();
  if (h < 12) return 'Morning';
  if (h < 17) return 'Afternoon';
  return 'Evening';
}

/** The first word of a full name, for "Morning, Sam". */
export function firstNameOf(fullName: string | null | undefined): string {
  return (fullName || '').trim().split(/\s+/)[0] || '';
}
