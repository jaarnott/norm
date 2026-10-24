import { describe, it, expect } from 'vitest';
import { BookOpen, ClipboardList, GraduationCap } from 'lucide-react';
import { FUNCTIONAL_PAGES, appPageConfig, teamPagesFor } from './pageRegistry';
import { AGENTS } from '../layout/Sidebar';

/**
 * An app's pages join an agent's menu. Two things have to hold for that to
 * work at all: the config has to carry the app's own agent, and the menu has
 * to be reachable — the sidebar list is static, so a page pointed at an agent
 * with no button is orphaned, with no way for a user to ever select it.
 */
describe('appPageConfig', () => {
  const app = { slug: 'hiring', name: 'Hiring', icon: '🧑‍🍳' };

  it('joins the menu the app names', () => {
    expect(appPageConfig({ ...app, agent: 'hr' }).agent).toBe('hr');
  });

  it('falls back to the App Builder, where apps lived before they could choose', () => {
    expect(appPageConfig(app).agent).toBe('app_builder');
    expect(appPageConfig({ ...app, agent: null }).agent).toBe('app_builder');
    expect(appPageConfig({ ...app, agent: '' }).agent).toBe('app_builder');
  });

  it('namespaces the id so it can never collide with a built-in page', () => {
    const config = appPageConfig({ ...app, agent: 'hr' });
    expect(config.id).toBe('app:hiring');
    expect(FUNCTIONAL_PAGES.some((p) => p.id === config.id)).toBe(false);
  });

  it('shows the app by its name and one line icon — no emoji in the label', () => {
    const config = appPageConfig({ slug: 'training', name: 'Norm Training', icon: '🎓', agent: 'hr' });
    expect(config.label).toBe('Norm Training');
    expect(config.icon).toBe(GraduationCap);
  });

  it('renders through the app runner, carrying its slug', () => {
    const config = appPageConfig({ ...app, agent: 'hr' });
    expect(config.component).toBe('app_runner');
    expect(config.componentProps).toEqual({ slug: 'hiring' });
  });
});

describe('scheduled tasks', () => {
  // Every team member that works in a conversation also runs scheduled tasks.
  const members = ['procurement', 'hr', 'reports', 'time_attendance', 'marketing', 'executive_chef', 'functions', 'finance'];

  it('every member has a Tasks page loading its own agent\'s tasks', () => {
    for (const member of members) {
      const page = FUNCTIONAL_PAGES.find((p) => p.id === `tasks-${member}`);
      expect(page, `tasks-${member}`).toBeDefined();
      expect(page?.agent).toBe(member);
      expect(page?.component).toBe('automated_task_board');
      expect(page?.loadAction.defaultParams()).toEqual({ agent_slug: member });
    }
  });
});

describe('finance', () => {
  it('has a Dashboard page for its own board, beside Bills and Tasks', () => {
    const page = FUNCTIONAL_PAGES.find((p) => p.id === 'dashboard-finance');
    expect(page?.component).toBe('dashboard_view');
    expect(page?.componentProps).toEqual({ agent_slug: 'finance' });
  });

  it('has eight menu items, one per job, in this order', () => {
    // The menu is the registry's order (ThreadList filters, never sorts).
    const menu = FUNCTIONAL_PAGES.filter((p) => p.agent === 'finance').map((p) => p.id);
    expect(menu).toEqual([
      'dashboard-finance', 'finance-summary', 'profit-and-loss', 'finance-budgets',
      'xero-bills', 'payroll', 'entertainers', 'tasks-finance',
    ]);
  });

  it('keeps each page to the people who can load it', () => {
    const permission = (id: string) => FUNCTIONAL_PAGES.find((p) => p.id === id)?.permission;
    // xero.get_profit_and_loss needs the payroll permission (it shows wages).
    expect(permission('profit-and-loss')).toBe('payroll:read');
    expect(permission('payroll')).toBe('payroll:read');
    // Venue access, decided by the server; the Payments tab is gated inside the page.
    expect(permission('entertainers')).toBeUndefined();
    expect(permission('finance-summary')).toBe('finance:read');
    expect(permission('finance-budgets')).toBe('finance:read');
  });

  it('ships Payroll, Entertainers and Budgets with their own Apps', () => {
    const app = (id: string) => FUNCTIONAL_PAGES.find((p) => p.id === id)?.app;
    expect(app('payroll')).toBe('payroll');
    expect(app('entertainers')).toBe('entertainers');
    expect(app('finance-summary')).toBe('budget');
    expect(app('finance-budgets')).toBe('budget');
  });
});

describe('menu reachability', () => {
  const selectable = new Set(AGENTS.map((a) => a.id));

  it('every built-in page belongs to an agent the sidebar can select', () => {
    for (const page of FUNCTIONAL_PAGES) {
      expect(selectable.has(page.agent), `${page.id} → ${page.agent}`).toBe(true);
    }
  });

  it('an app page lands beside that agent’s own pages', () => {
    // The exact filter ThreadList applies.
    const pages = [...FUNCTIONAL_PAGES, appPageConfig({ slug: 'hiring', name: 'Hiring', agent: 'hr' })];
    const hrMenu = pages.filter((p) => p.agent === 'hr').map((p) => p.id);
    expect(hrMenu).toContain('app:hiring');
    // …and beside the built-ins rather than replacing them.
    expect(hrMenu).toContain('hiring');
    expect(hrMenu).toContain('tasks-hr');
    // and it does NOT leak into another agent's menu
    expect(pages.filter((p) => p.agent === 'procurement').map((p) => p.id)).not.toContain('app:hiring');
  });
});

describe('teamPagesFor', () => {
  // A team app (docs/plans/team-apps-skeleton.md 4.2): its screens become
  // pages under the 'apps' pseudo-agent, each told which team app it is in.
  const screen = (page_id: string, label: string, component: string, app_slug = 'recipes-and-costing') =>
    ({ page_id, label, component, app_slug });

  it('re-homes a screen that has a FUNCTIONAL_PAGES entry, keeping everything but the agent', () => {
    const entry = FUNCTIONAL_PAGES.find((p) => p.id === 'recipes');
    expect(entry, 'the Recipes page exists').toBeDefined();
    const [page] = teamPagesFor({ slug: 'kitchen', screens: [screen('recipes', 'Recipes', 'recipe_editor')] });
    expect(page.agent).toBe('apps');
    expect(page.id).toBe('recipes');
    expect(page.component).toBe(entry!.component);
    expect(page.icon).toBe(entry!.icon);
    expect(page.loadAction).toBe(entry!.loadAction);
    expect(page.componentProps).toEqual({ ...entry!.componentProps, team: 'kitchen' });
    // …and the agent-menu entry itself is untouched.
    expect(entry!.agent).not.toBe('apps');
    expect(entry!.componentProps?.team).toBeUndefined();
  });

  it('builds a team-only screen with no entry from the catalog: self-loading, carrying team and page id', () => {
    const [page] = teamPagesFor({ slug: 'kitchen', screens: [screen('prep-today', 'Prep', 'prep_list', 'prep')] }, []);
    expect(page).toMatchObject({ id: 'prep-today', label: 'Prep', agent: 'apps', component: 'prep_list' });
    expect(page.loadAction.connector).toBe('_none');
    expect(page.loadAction.defaultParams()).toEqual({});
    expect(page.componentProps).toEqual({ team: 'kitchen', page_id: 'prep-today' });
    expect(FUNCTIONAL_PAGES.some((p) => p.id === 'prep-today')).toBe(false);
  });

  it('gives a built page its job app\'s icon, else a checklist line', () => {
    const [known, unknown] = teamPagesFor({ slug: 'kitchen', screens: [
      screen('recipes-view', 'Recipes', 'recipe_editor', 'recipes-and-costing'),
      screen('fcp-tasks', 'Tasks', 'fcp_tasks', 'food-control'),
    ] }, []);
    expect(known.icon).toBe(BookOpen);
    expect(unknown.icon).toBe(ClipboardList);
  });

  it('keeps the screens in the team app\'s order', () => {
    const pages = teamPagesFor({ slug: 'kitchen', screens: [
      screen('fcp-tasks', 'Tasks', 'fcp_tasks', 'food-control'),
      screen('recipes', 'Recipes', 'recipe_editor'),
    ] });
    expect(pages.map((p) => p.id)).toEqual(['fcp-tasks', 'recipes']);
  });

  it('gives a team app with no screens no pages', () => {
    expect(teamPagesFor({ slug: 'front_of_house', screens: [] })).toEqual([]);
  });
});
