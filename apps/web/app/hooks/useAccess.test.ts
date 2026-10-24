import { describe, expect, it } from 'vitest';
import { allowsAgent, allowsTeam, firstNameOf, navLabelFor, sectionAppsFor, sectionTileKey, teamBarTabsFor, timeOfDayWord, visibleAgentsFor } from './useAccess';

const AGENTS = [{ id: 'home' }, { id: 'reports' }, { id: 'hr' }, { id: 'executive_chef' }];

describe('visibleAgentsFor', () => {
  it('shows every hired agent to a role that reaches everything', () => {
    const hired = new Set(['reports', 'hr']);
    expect(visibleAgentsFor(AGENTS, hired, { agents: null, apps: null, staff: false }).map(a => a.id))
      .toEqual(['home', 'reports', 'hr']);
  });
  it('narrows to the agents the role names, and never past what is hired', () => {
    const hired = new Set(['reports', 'hr']);
    const access = { agents: ['hr', 'executive_chef'], apps: null, staff: false };
    expect(visibleAgentsFor(AGENTS, hired, access).map(a => a.id)).toEqual(['home', 'hr']);
  });
  it('leaves venue staff with Home alone', () => {
    const access = { agents: [], apps: ['kitchen', 'training'], staff: true };
    expect(visibleAgentsFor(AGENTS, null, access).map(a => a.id)).toEqual(['home']);
    expect(allowsAgent(access, 'hr')).toBe(false);
    expect(allowsAgent(access, 'home')).toBe(true);
  });
  it('shows everything when access is unknown (an older /me payload)', () => {
    expect(visibleAgentsFor(AGENTS, null, undefined)).toHaveLength(4);
  });
});

describe('allowsTeam', () => {
  it('lets a role with every app see every team app', () => {
    expect(allowsTeam({ agents: null, apps: null, staff: false }, 'kitchen')).toBe(true);
  });
  it('narrows to the team apps the role names', () => {
    const access = { agents: [], apps: ['kitchen', 'training'], staff: true };
    expect(allowsTeam(access, 'kitchen')).toBe(true);
    expect(allowsTeam(access, 'managers')).toBe(false);
  });
  it('refuses everything to a role with no apps at all', () => {
    expect(allowsTeam({ agents: [], apps: [], staff: true }, 'kitchen')).toBe(false);
  });
  it('allows when access is unknown (an older /me payload)', () => {
    expect(allowsTeam(undefined, 'kitchen')).toBe(true);
    expect(allowsTeam(null, 'kitchen')).toBe(true);
  });
});

describe('sectionAppsFor', () => {
  const rows = [
    { slug: 'training', name: 'Training', kind: 'app', audience: 'agent' },
    { slug: 'kitchen-notes', name: 'Kitchen notes', kind: 'app', audience: 'team' },
    { slug: 'cleaning', name: 'Cleaning', kind: 'app', audience: 'both' },
    { slug: 'cogs', name: 'COGS', kind: 'dashboard', audience: 'agent' },
  ];
  const screen = (page_id: string) => ({ page_id, label: page_id });
  const teamApps = [
    { slug: 'kitchen', name: 'Kitchen', icon: '🧑‍🍳', screens: [screen('recipes'), screen('prep')] },
    { slug: 'front_of_house', name: 'Front of house', icon: null, screens: [] },
    { slug: 'managers', name: 'Managers', icon: null, screens: [screen('sign-offs')] },
  ];
  const manager = { agents: null, apps: null, staff: false };

  it('gives a manager only the apps built for the team', () => {
    const got = sectionAppsFor(rows, manager).map(a => a.slug);
    expect(got).toEqual(['kitchen-notes', 'cleaning']);
  });
  it('gives venue staff every app they can open, except agent dashboards', () => {
    const got = sectionAppsFor(rows, { agents: [], apps: null, staff: true }).map(a => a.slug);
    expect(got).toEqual(['training', 'kitchen-notes', 'cleaning']);
  });
  it('treats a missing audience as agent', () => {
    expect(sectionAppsFor([{ slug: 'x', name: 'X' }], manager)).toEqual([]);
  });
  it('is unchanged for a manager with no team apps at all', () => {
    expect(sectionAppsFor(rows, manager, [])).toEqual(sectionAppsFor(rows, manager));
  });
  it('lists the team apps with a screen first, in catalog order, then the App-platform apps', () => {
    const got = sectionAppsFor(rows, manager, teamApps);
    expect(got.map(a => `${a.kind}:${a.slug}`)).toEqual(['team:kitchen', 'team:managers', 'app:kitchen-notes', 'app:cleaning']);
    expect(got[0]).toMatchObject({ kind: 'team', name: 'Kitchen', icon: '🧑‍🍳' });
    expect(got[0].kind === 'team' && got[0].screens.map(s => s.page_id)).toEqual(['recipes', 'prep']);
  });
  it('hides a team app with no screens here (nothing to open)', () => {
    const got = sectionAppsFor(rows, manager, teamApps).map(a => a.slug);
    expect(got).not.toContain('front_of_house');
  });
  it('narrows the team apps to the ones the role names', () => {
    const chef = { agents: [], apps: ['kitchen'], staff: true };
    const got = sectionAppsFor(rows, chef, teamApps);
    expect(got.map(a => `${a.kind}:${a.slug}`)).toEqual(['team:kitchen', 'app:training', 'app:kitchen-notes', 'app:cleaning']);
  });
  it('keeps the App-platform row whole behind the tile, dashboards included', () => {
    const got = sectionAppsFor(rows, manager, teamApps);
    const cleaning = got.find(a => a.slug === 'cleaning');
    expect(cleaning?.kind === 'app' && cleaning.row).toEqual(rows[2]);
    const dash = sectionAppsFor([{ slug: 'board', name: 'Board', kind: 'dashboard', audience: 'team' }], manager);
    expect(dash[0].kind === 'app' && dash[0].row.kind).toBe('dashboard');
  });
  it('keys a team tile apart from an App-platform tile of the same slug', () => {
    expect(sectionTileKey({ kind: 'team', slug: 'kitchen' })).toBe('team:kitchen');
    expect(sectionTileKey({ kind: 'app', slug: 'kitchen' })).toBe('app:kitchen');
  });
});

describe('navLabelFor', () => {
  it('names the landmark by what it holds', () => {
    expect(navLabelFor([{ id: 'home' }, { id: 'hr' }], 0)).toBe('AI agents');
    expect(navLabelFor([{ id: 'home' }, { id: 'hr' }], 2)).toBe('AI agents and apps');
    expect(navLabelFor([{ id: 'home' }], 2)).toBe('Apps');
    expect(navLabelFor([], 1)).toBe('Apps');
    expect(navLabelFor([{ id: 'home' }], 0)).toBe('AI agents');
  });
});

describe('teamBarTabsFor', () => {
  it('is Today · the apps · Ask Norm · Me with one or two apps', () => {
    const tabs = teamBarTabsFor([{ slug: 'kitchen', name: 'Kitchen' }, { slug: 'training', name: 'Training' }]);
    expect(tabs.map(t => t.id)).toEqual(['today', 'app:kitchen', 'app:training', 'ask', 'me']);
    expect(tabs[1]).toMatchObject({ label: 'Kitchen', slug: 'kitchen' });
  });
  it('folds three or more apps into the first app plus Apps', () => {
    const tabs = teamBarTabsFor([
      { slug: 'kitchen', name: 'Kitchen' }, { slug: 'training', name: 'Training' }, { slug: 'managers', name: 'Managers' },
    ]);
    expect(tabs.map(t => t.id)).toEqual(['today', 'app:kitchen', 'apps', 'ask', 'me']);
  });
  it('still has Today, Ask Norm and Me with no apps at all', () => {
    expect(teamBarTabsFor([]).map(t => t.id)).toEqual(['today', 'ask', 'me']);
  });
  it('leaves Ask Norm out on a tablet, which never chats', () => {
    const apps = [{ slug: 'kitchen', name: 'Kitchen' }];
    expect(teamBarTabsFor(apps, { ask: false }).map(t => t.id)).toEqual(['today', 'app:kitchen', 'me']);
    expect(teamBarTabsFor(apps, { ask: true }).map(t => t.id)).toEqual(['today', 'app:kitchen', 'ask', 'me']);
  });
});

describe('greeting', () => {
  it('names the time of day', () => {
    expect(timeOfDayWord(new Date(2026, 9, 23, 8))).toBe('Morning');
    expect(timeOfDayWord(new Date(2026, 9, 23, 13))).toBe('Afternoon');
    expect(timeOfDayWord(new Date(2026, 9, 23, 19))).toBe('Evening');
  });
  it('takes the first name', () => {
    expect(firstNameOf('Sam Taylor')).toBe('Sam');
    expect(firstNameOf('  ')).toBe('');
    expect(firstNameOf(null)).toBe('');
  });
});
