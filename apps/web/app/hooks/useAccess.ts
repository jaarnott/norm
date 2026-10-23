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
 */
import type { AccessInfo } from '../types';

export interface AgentLike { id: string }

/** The pseudo-agents that are never an AI agent: Home, Settings and the apps
 *  section. The hired-agent guard leaves them alone. */
export const NON_AGENT: ReadonlySet<string> = new Set(['home', 'settings', 'apps']);

/** An app as /api/apps lists it, as far as the shell needs. */
export interface SectionAppLike {
  slug: string;
  kind?: 'app' | 'dashboard' | string;
  /** agent (under its AI agent), team (the apps section) or both. */
  audience?: 'agent' | 'team' | 'both' | string | null;
}

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

/** The apps that make up the apps section: those built for the team (audience
 *  team or both), and for venue staff every app they can open at all. Apps
 *  with audience agent or both keep their place under their agent as well. */
export function sectionAppsFor<T extends SectionAppLike>(rows: T[], access: AccessInfo | null | undefined): T[] {
  const staff = !!access?.staff;
  return rows.filter(r => {
    const audience = r.audience || 'agent';
    if (audience === 'team' || audience === 'both') return true;
    // A dashboard lives on its agent's Dashboard page; it only joins the
    // section when it was built for the team.
    return staff && r.kind !== 'dashboard';
  });
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
