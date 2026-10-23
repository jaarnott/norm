import { describe, expect, it } from 'vitest';
import { allowsAgent, firstNameOf, navLabelFor, sectionAppsFor, teamBarTabsFor, timeOfDayWord, visibleAgentsFor } from './useAccess';

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

describe('sectionAppsFor', () => {
  const rows = [
    { slug: 'training', kind: 'app', audience: 'agent' },
    { slug: 'kitchen', kind: 'app', audience: 'team' },
    { slug: 'cleaning', kind: 'app', audience: 'both' },
    { slug: 'cogs', kind: 'dashboard', audience: 'agent' },
  ];
  it('gives a manager only the apps built for the team', () => {
    const got = sectionAppsFor(rows, { agents: null, apps: null, staff: false }).map(a => a.slug);
    expect(got).toEqual(['kitchen', 'cleaning']);
  });
  it('gives venue staff every app they can open, except agent dashboards', () => {
    const got = sectionAppsFor(rows, { agents: [], apps: null, staff: true }).map(a => a.slug);
    expect(got).toEqual(['training', 'kitchen', 'cleaning']);
  });
  it('treats a missing audience as agent', () => {
    expect(sectionAppsFor([{ slug: 'x' }], { agents: null, apps: null, staff: false })).toEqual([]);
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
