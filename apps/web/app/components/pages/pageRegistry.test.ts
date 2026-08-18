import { describe, it, expect } from 'vitest';
import { FUNCTIONAL_PAGES, appPageConfig } from './pageRegistry';
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

  it('renders through the app runner, carrying its slug', () => {
    const config = appPageConfig({ ...app, agent: 'hr' });
    expect(config.component).toBe('app_runner');
    expect(config.componentProps).toEqual({ slug: 'hiring' });
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
