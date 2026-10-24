import { describe, expect, it } from 'vitest';
import { groupBySystem, humanKey, joinAnd, missingTeamApps, placementNote, rolesSeeing, seenByLine, teamName, teamToolsLine, toolName } from './teamApps';

const TEAM = [
  { slug: 'kitchen', name: 'Kitchen' },
  { slug: 'front_of_house', name: 'Front of house' },
  { slug: 'managers', name: 'Managers' },
];

describe('humanKey', () => {
  it('reads a stored key as words', () => {
    expect(humanKey('front_of_house')).toBe('Front of house');
    expect(humanKey('custom:prep_list')).toBe('Prep list');
  });
  it('leaves a plain slug as it is', () => {
    expect(humanKey('training')).toBe('training');
    expect(humanKey('custom:MyApp')).toBe('MyApp');
  });
  it('names the team apps before their catalog rows are installed', () => {
    expect(humanKey('kitchen')).toBe('Kitchen');
    expect(humanKey('managers')).toBe('Managers');
    expect(teamName([], 'kitchen')).toBe('Kitchen');
  });
});

describe('missingTeamApps', () => {
  it('offers all three team apps while the catalog has none', () => {
    expect(missingTeamApps([])).toEqual(TEAM);
  });
  it('offers only the ones the catalog does not list', () => {
    expect(missingTeamApps(TEAM.slice(0, 2))).toEqual([TEAM[2]]);
    expect(missingTeamApps(TEAM)).toEqual([]);
  });
});

describe('teamName', () => {
  it('names a team app from the catalog', () => {
    expect(teamName(TEAM, 'front_of_house')).toBe('Front of house');
  });
  it('falls back to a readable key before the row is installed', () => {
    expect(teamName([], 'front_of_house')).toBe('Front of house');
  });
});

describe('placementNote', () => {
  it('says nothing for a screen that stays in its agent\'s menu', () => {
    expect(placementNote({ teams: null }, TEAM)).toBeNull();
    expect(placementNote({ teams: [] }, TEAM)).toBeNull();
    expect(placementNote({}, TEAM)).toBeNull();
  });
  it('says where else a screen shows, and when it shows only there', () => {
    expect(placementNote({ teams: ['kitchen'] }, TEAM)).toBe('Also in Kitchen');
    expect(placementNote({ teams: ['kitchen'], team_only: true }, TEAM)).toBe('Kitchen only');
  });
  it('names every team app the same screen is placed in', () => {
    expect(placementNote({ teams: ['kitchen', 'front_of_house'] }, TEAM)).toBe('Also in Kitchen and Front of house');
    expect(placementNote({ teams: ['kitchen', 'front_of_house'], team_only: true }, TEAM)).toBe('Kitchen and Front of house only');
    expect(placementNote({ teams: ['kitchen', 'front_of_house', 'managers'] }, TEAM)).toBe('Also in Kitchen, Front of house and Managers');
  });
  it('still names a team app the catalog lacks', () => {
    expect(placementNote({ teams: ['front_of_house'] }, [])).toBe('Also in Front of house');
  });
  it('has a mid-sentence form for "Label · note" lists', () => {
    expect(placementNote({ teams: ['kitchen'] }, TEAM, { inline: true })).toBe('also in Kitchen');
    expect(placementNote({ teams: ['kitchen', 'managers'] }, TEAM, { inline: true })).toBe('also in Kitchen and Managers');
    expect(placementNote({ teams: ['kitchen'], team_only: true }, TEAM, { inline: true })).toBe('Kitchen only');
  });
});

describe('toolName', () => {
  it('uses a short label', () => {
    expect(toolName({ key: 'loadedhub.get_recipes', label: 'Get recipes' })).toBe('Get recipes');
  });
  it('reads the key as words when the label is a description', () => {
    expect(toolName({ key: 'loadedhub.get_recipes', label: 'THE recipe lookup. Pass query (name substring) to find recipes.' })).toBe('Get recipes');
    expect(toolName({ key: 'loadedhub.get_recipes', label: 'Find recipes: by name' })).toBe('Get recipes');
    expect(toolName({ key: 'loadedhub.get_recipes', label: 'A label that is far too long to be a name of anything' })).toBe('Get recipes');
  });
  it('reads the key as words when there is no label', () => {
    expect(toolName({ key: 'loadedhub.get_prep_list' })).toBe('Get prep list');
    expect(toolName({ key: 'norm.remember', label: '' })).toBe('Remember');
  });
});

describe('teamToolsLine', () => {
  it('lists the tools Norm may use, in a sentence', () => {
    expect(teamToolsLine([{ key: 'loadedhub.get_recipes', label: 'Get recipes' }])).toBe('Norm can use Get recipes.');
    expect(teamToolsLine([
      { key: 'a.one', label: 'One' }, { key: 'a.two', label: 'Two' }, { key: 'a.three', label: 'Three' },
    ])).toBe('Norm can use One, Two and Three.');
  });
  it('names a tool by its key when it has no label, or a description for one', () => {
    expect(teamToolsLine([{ key: 'loadedhub.get_prep_list' }])).toBe('Norm can use Get prep list.');
    expect(teamToolsLine([{ key: 'loadedhub.get_recipes', label: 'THE recipe lookup. Pass query (name substring) to find recipes.' }]))
      .toBe('Norm can use Get recipes.');
  });
  it('says so when there is nothing yet', () => {
    expect(teamToolsLine([])).toBe('No tools for Norm yet.');
  });
  it('joins with and', () => {
    expect(joinAnd([])).toBe('');
    expect(joinAnd(['a'])).toBe('a');
    expect(joinAnd(['a', 'b'])).toBe('a and b');
  });
});

describe('rolesSeeing and seenByLine', () => {
  const roles = [
    { display_name: 'Owner', apps: null },
    { display_name: 'Kitchen', apps: ['kitchen'] },
    { display_name: 'Kitchen', apps: ['kitchen'] }, // the dev database's duplicate system role
    { display_name: 'Head chef', apps: ['kitchen', 'managers'] },
    { display_name: 'Front of house', apps: ['front_of_house'] },
    { display_name: 'Team Member', apps: [] },
    { display_name: 'Manager', apps: null },
  ];
  it('names the roles whose apps list includes the team app, once each', () => {
    expect(rolesSeeing(roles, 'kitchen')).toEqual(['Kitchen', 'Head chef']);
    expect(rolesSeeing(roles, 'managers')).toEqual(['Head chef']);
  });
  it('does not list a role that has all apps by name', () => {
    expect(rolesSeeing(roles, 'kitchen')).not.toContain('Owner');
    expect(rolesSeeing([{ display_name: 'Manager' }], 'kitchen')).toEqual([]);
  });
  it('leaves out a role with no apps at all', () => {
    expect(rolesSeeing(roles, 'front_of_house')).toEqual(['Front of house']);
  });
  it('writes the line with the open roles as one phrase', () => {
    expect(seenByLine(roles, 'kitchen')).toBe('Seen by: Kitchen, Head chef, and every role that has all apps.');
    expect(seenByLine(roles, 'managers')).toBe('Seen by: Head chef, and every role that has all apps.');
  });
  it('writes the line without that phrase when no role is open', () => {
    const closed = roles.filter((r) => r.apps !== null);
    expect(seenByLine(closed, 'kitchen')).toBe('Seen by: Kitchen and Head chef.');
    expect(seenByLine(closed, 'front_of_house')).toBe('Seen by: Front of house.');
  });
  it('says when only open roles, or nobody, sees it', () => {
    expect(seenByLine(roles, 'bar')).toBe('Seen by: every role that has all apps.');
    expect(seenByLine(roles.filter((r) => r.apps !== null), 'bar')).toBe('Seen by: no roles yet.');
    expect(seenByLine([], 'kitchen')).toBe('Seen by: no roles yet.');
  });
});

describe('groupBySystem', () => {
  it('groups in first-seen order and puts no-system screens under an empty key', () => {
    const screens = [
      { page_id: 'a', connection_name: 'Loaded' },
      { page_id: 'b', connection_name: null },
      { page_id: 'c', connection_name: 'Loaded' },
    ];
    expect(groupBySystem(screens).map(([k, v]) => [k, v.map((s) => s.page_id)])).toEqual([
      ['Loaded', ['a', 'c']],
      ['', ['b']],
    ]);
  });
});
