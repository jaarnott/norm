/**
 * The words and lookups the admin pages share for team apps (Kitchen, Front
 * of house, Managers — docs/plans/team-apps-skeleton.md §4.3). Pure, so the
 * Team page, App Map, Agents, Roles and People panels say the same thing and
 * vitest can pin it. A team app is a second place a job app's screen shows;
 * these helpers only name that placement, they decide nothing.
 */

/** The little a helper needs to know about a team app: GET /api/team's
 *  `team_apps[]` and the App Map's `team_apps[]` both carry it. */
export interface TeamAppNamed { slug: string; name: string }

/** A screen's placement, as every screen entry carries it: the team apps it
 *  is in (the same screen in each) and whether it is only there. */
export interface PlacedScreen { teams?: string[] | null; team_only?: boolean }

/** A role as far as "who sees this team app" goes: GET
 *  /api/organizations/{org}/roles. `apps` null = every app that is on. */
export interface RoleReach { display_name: string; apps?: string[] | null }

/** The three team apps by slug, as the catalog seeds them (services/
 *  screens.TEAM_SLUGS): their names before the catalog rows are installed,
 *  when the venue roles already name them (team-apps skeleton §2). */
export const TEAM_APP_NAMES: Record<string, string> = {
  kitchen: 'Kitchen',
  front_of_house: 'Front of house',
  managers: 'Managers',
};

/** The team apps the catalog does not list yet, by their seed names, so the
 *  Roles form keeps offering them before the rows are installed. */
export function missingTeamApps(teamApps: TeamAppNamed[]): TeamAppNamed[] {
  return Object.entries(TEAM_APP_NAMES)
    .filter(([slug]) => !teamApps.some((t) => t.slug === slug))
    .map(([slug, name]) => ({ slug, name }));
}

/** 'custom:front_of_house' → 'Front of house': a key that no list names,
 *  readable rather than raw. A team app's slug reads as its name ('kitchen'
 *  → 'Kitchen'); any other slug with no underscores keeps its case. */
export function humanKey(key: string): string {
  if (TEAM_APP_NAMES[key]) return TEAM_APP_NAMES[key];
  const bare = key.replace(/^custom:/, '');
  if (!bare.includes('_')) return bare;
  const words = bare.replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A team app's name by slug, falling back to a readable key when the
 *  catalog has no such row yet (the roles name it before it is installed). */
export function teamName(teamApps: TeamAppNamed[], slug: string): string {
  return teamApps.find((t) => t.slug === slug)?.name ?? humanKey(slug);
}

/** "Also in Kitchen and Front of house" or "Kitchen and Front of house only"
 *  for a screen placed in team apps; null for a screen that lives in its
 *  agent's menu alone. `inline` gives the mid-sentence form ("also in
 *  Kitchen") for a "Label · note" list. */
export function placementNote(screen: PlacedScreen, teamApps: TeamAppNamed[], opts: { inline?: boolean } = {}): string | null {
  const teams = (screen.teams ?? []).filter(Boolean);
  if (teams.length === 0) return null;
  const names = joinAnd(teams.map((t) => teamName(teamApps, t)));
  if (screen.team_only) return `${names} only`;
  return opts.inline ? `also in ${names}` : `Also in ${names}`;
}

/** A tool's name in a sentence: its label when that is a short name (at most
 *  40 characters, no sentence punctuation), else the key read as words —
 *  "loadedhub.get_recipes" → "Get recipes". Some catalog tools carry a whole
 *  description as their label ("THE recipe lookup. Pass query…"); that is
 *  never printed as a name. The Roles form's hint uses the same rule. */
export function toolName(t: { key: string; label?: string | null }): string {
  const label = (t.label ?? '').trim();
  if (label && label.length <= 40 && !/[.:;!?]/.test(label)) return label;
  const words = (t.key.split('.').pop() ?? t.key).replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** "a", "a and b", "a, b and c". */
export function joinAnd(items: string[]): string {
  if (items.length <= 1) return items[0] ?? '';
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

/** What Norm may use for someone whose role reaches only this team app:
 *  "Norm can use Get recipes." or "No tools for Norm yet." */
export function teamToolsLine(tools: { key: string; label?: string | null }[]): string {
  if (tools.length === 0) return 'No tools for Norm yet.';
  return `Norm can use ${joinAnd(tools.map(toolName))}.`;
}

/** The roles whose `apps` list names this team app, de-duplicated by display
 *  name (a database can hold a system role twice), in the order the roles
 *  came. A role with every app (`apps` null) is not named here: `seenByLine`
 *  says that in words instead of listing every such role. */
export function rolesSeeing(roles: RoleReach[], slug: string): string[] {
  const names: string[] = [];
  for (const r of roles) {
    if (!Array.isArray(r.apps) || !r.apps.includes(slug)) continue;
    if (!names.includes(r.display_name)) names.push(r.display_name);
  }
  return names;
}

/** "Seen by: Kitchen, Head chef, and every role that has all apps." — the
 *  roles that name the team app, then the open roles as one phrase. */
export function seenByLine(roles: RoleReach[], slug: string): string {
  const names = rolesSeeing(roles, slug);
  const everyRole = roles.some((r) => r.apps === null || r.apps === undefined);
  if (names.length === 0) return everyRole ? 'Seen by: every role that has all apps.' : 'Seen by: no roles yet.';
  if (!everyRole) return `Seen by: ${joinAnd(names)}.`;
  return `Seen by: ${names.join(', ')}, and every role that has all apps.`;
}

/** Screens grouped by the system each is for, in first-seen order; a screen
 *  for no system groups under ''. */
export function groupBySystem<T extends { connection_name: string | null }>(screens: T[]): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const sc of screens) {
    const key = sc.connection_name ?? '';
    groups.set(key, [...(groups.get(key) ?? []), sc]);
  }
  return [...groups.entries()];
}
