// How a thread is labelled and filed now that there is one Norm agent.
//
// A router used to pick one of seven agents for every message, and the thread
// wore that agent's name. Since Sep 2026 every thread is Norm's; the API says
// which Apps it actually used (`apps`, from its tool calls), and that is the
// label — "BambooHR · Loaded Reports" — and the filing: a thread shows under
// every team member whose App it touched. Threads from before keep their old
// agent domain.

export interface ThreadApp {
  slug: string;
  name: string;
  member: string | null;
}

interface Labelled {
  domain: string;
  apps?: ThreadApp[];
}

/** Domains that mean "Norm itself", not a team member. */
const NORM_DOMAINS = new Set(['norm', 'meta', 'unknown', '']);

/** Team members this thread belongs under: those of the Apps it used, else
 *  its legacy agent domain. Empty for a Norm thread that used no App. */
export function threadMembers(t: Labelled): string[] {
  const members = [...new Set((t.apps ?? []).map(a => a.member).filter((m): m is string => !!m))];
  if (members.length) return members;
  return NORM_DOMAINS.has(t.domain) ? [] : [t.domain];
}

/** The card label: the Apps used, else the legacy agent, else "Norm". */
export function threadLabel(t: Labelled): string {
  const names = (t.apps ?? []).map(a => a.name);
  if (names.length) return names.join(' · ');
  return NORM_DOMAINS.has(t.domain) ? 'Norm' : t.domain.replace(/_/g, ' ');
}

/** The member whose colour and icon the card wears ('norm' when none). */
export function threadAccent(t: Labelled): string {
  return threadMembers(t)[0] ?? 'norm';
}
