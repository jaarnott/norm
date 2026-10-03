import { Calendar, Users, Timer, BarChart3, ShoppingCart, Receipt, LayoutDashboard, BookOpen, ChefHat, Clock, Blocks, LayoutGrid, Grid2x2, Gavel, type LucideIcon } from 'lucide-react';
import { tradingMonday, tradingWeekRange } from '../../lib/rosterTime';

export interface FunctionalPageConfig {
  id: string;
  label: string;
  icon: LucideIcon;
  agent: string;
  /** The App (marketplace slug) this page ships with — hierarchy v2. A page
   *  with an `app` shows iff that App is on for the org; a page without one
   *  is the team member's workspace chrome (dashboard, tasks) and shows iff
   *  the member is hired. */
  app?: string;
  /** Connections this page needs for the active venue. Defaults to the
   *  loadAction connector; self-loading pages (`_none`) declare theirs
   *  explicitly so the connection guard can prompt instead of blank-failing. */
  connections?: string[];
  component: string;
  loadAction: {
    connector: string;
    action: string;
    defaultParams: () => Record<string, unknown>;
  };
  componentProps?: Record<string, unknown>;
}

/** This business week, as the roster query wants it (lib/rosterTime). It was
 * midnight to midnight with +13:00 hard-coded — two weeks' rosters back, and
 * the wrong offset all winter (Oct 2026). */
function getCurrentWeekRange(): { start_datetime: string; end_datetime: string } {
  return tradingWeekRange(tradingMonday(new Date()));
}

export const FUNCTIONAL_PAGES: FunctionalPageConfig[] = [
  // Dashboards (one per agent)
  {
    id: 'dashboard-hr',
    app: 'norm-core',
    label: 'Dashboard',
    icon: LayoutDashboard,
    agent: 'hr',
    component: 'dashboard_view',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
    componentProps: { agent_slug: 'hr' },
  },
  {
    id: 'dashboard-procurement',
    app: 'norm-core',
    label: 'Dashboard',
    icon: LayoutDashboard,
    agent: 'procurement',
    component: 'dashboard_view',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
    componentProps: { agent_slug: 'procurement' },
  },
  {
    id: 'dashboard-reports',
    app: 'norm-core',
    label: 'Dashboard',
    icon: LayoutDashboard,
    agent: 'reports',
    component: 'dashboard_view',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
    componentProps: { agent_slug: 'reports' },
  },
  // Marketing
  {
    id: 'dashboard-marketing',
    app: 'norm-core',
    label: 'Dashboard',
    icon: LayoutDashboard,
    agent: 'marketing',
    component: 'dashboard_view',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
    componentProps: { agent_slug: 'marketing' },
  },
  {
    id: 'tasks-marketing',
    app: 'norm-core',
    label: 'Tasks',
    icon: Timer,
    agent: 'marketing',
    component: 'automated_task_board',
    loadAction: {
      connector: 'norm',
      action: 'list_automated_tasks',
      defaultParams: () => ({ agent_slug: 'marketing' }),
    },
  },
  // Time & Attendance
  {
    id: 'dashboard-time_attendance',
    app: 'norm-core',
    label: 'Dashboard',
    icon: LayoutDashboard,
    agent: 'time_attendance',
    component: 'dashboard_view',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
    componentProps: { agent_slug: 'time_attendance' },
  },
  {
    id: 'roster',
    app: 'loaded-time',
    connections: ['loadedhub'],
    label: 'Roster',
    icon: Calendar,
    agent: 'time_attendance',
    component: 'roster_editor',
    loadAction: {
      connector: 'loadedhub',
      action: 'get_roster',
      defaultParams: getCurrentWeekRange,
    },
  },
  {
    id: 'tasks-time_attendance',
    app: 'norm-core',
    label: 'Tasks',
    icon: Timer,
    agent: 'time_attendance',
    component: 'automated_task_board',
    loadAction: {
      connector: 'norm',
      action: 'list_automated_tasks',
      defaultParams: () => ({ agent_slug: 'time_attendance' }),
    },
  },
  // HR (Hiring & Onboarding)
  {
    id: 'hiring',
    app: 'bamboohr-app',
    connections: ['bamboohr'],
    // Disambiguated from the Hiring APP, which now sits in this same menu and
    // supersedes this page. Two entries both reading 'Hiring' is a coin toss.
    label: 'Hiring (BambooHR)',
    icon: Users,
    agent: 'hr',
    component: 'hiring_board',
    loadAction: {
      connector: 'bamboohr',
      action: 'get_jobs',
      defaultParams: () => ({}),
    },
    componentProps: { connector_name: 'bamboohr' },
  },
  {
    id: 'tasks-hr',
    app: 'norm-core',
    label: 'Tasks',
    icon: Timer,
    agent: 'hr',
    component: 'automated_task_board',
    loadAction: {
      connector: 'norm',
      action: 'list_automated_tasks',
      defaultParams: () => ({ agent_slug: 'hr' }),
    },
  },
  {
    id: 'orders',
    app: 'loaded-stock',
    connections: ['loadedhub'],
    label: 'Orders',
    icon: ShoppingCart,
    agent: 'procurement',
    component: 'orders_dashboard',
    loadAction: {
      connector: 'loadedhub',
      action: 'get_purchase_orders_summary',
      defaultParams: () => ({}),
    },
  },
  {
    id: 'invoices',
    app: 'loaded-stock',
    connections: ['loadedhub'],
    label: 'Invoices',
    icon: Receipt,
    agent: 'procurement',
    component: 'invoices_dashboard',
    // Self-loading: the dashboard fetches /invoice-fixes/outstanding itself, so
    // no connector loadAction (mirrors SavedReportsBoard's _none pattern).
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    // Self-loading via the supplier_tenders component-api rows. Declared in the
    // Loaded app's marketplace composition (sync_marketplace_catalog.py).
    id: 'supplier-tenders',
    app: 'loaded-stock',
    connections: ['cook_brothers_app'],
    label: 'Supplier Tenders',
    icon: Gavel,
    agent: 'procurement',
    component: 'supplier_tenders',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    id: 'tasks-procurement',
    app: 'norm-core',
    label: 'Tasks',
    icon: Timer,
    agent: 'procurement',
    component: 'automated_task_board',
    loadAction: {
      connector: 'norm',
      action: 'list_automated_tasks',
      defaultParams: () => ({ agent_slug: 'procurement' }),
    },
  },
  {
    id: 'saved-reports',
    app: 'saved-reports',
    label: 'Reports',
    icon: BarChart3,
    agent: 'reports',
    component: 'saved_reports_board',
    loadAction: {
      connector: '_none',
      action: '_none',
      defaultParams: () => ({}),
    },
  },
  {
    id: 'tasks-reports',
    app: 'norm-core',
    label: 'Tasks',
    icon: Timer,
    agent: 'reports',
    component: 'automated_task_board',
    loadAction: {
      connector: 'norm',
      action: 'list_automated_tasks',
      defaultParams: () => ({ agent_slug: 'reports' }),
    },
  },
  // Executive Chef
  {
    id: 'dashboard-executive_chef',
    app: 'norm-core',
    label: 'Dashboard',
    icon: LayoutDashboard,
    agent: 'executive_chef',
    component: 'dashboard_view',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
    componentProps: { agent_slug: 'executive_chef' },
  },
  {
    // Self-loading: MenuEditor fetches the menu list + recipe options itself via
    // callComponentApi('menu_editor', ...), so no connector loadAction.
    id: 'menus',
    app: 'loaded-kitchen',
    connections: ['loadedhub'],
    label: 'Menus',
    icon: BookOpen,
    agent: 'executive_chef',
    component: 'menu_editor',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    // Self-loading: RecipeEditor fetches the recipe list + units + stock items
    // itself via callComponentApi('recipe_editor', ...).
    id: 'recipes',
    app: 'loaded-kitchen',
    connections: ['loadedhub'],
    label: 'Recipes',
    icon: ChefHat,
    agent: 'executive_chef',
    component: 'recipe_editor',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    // Self-loading: MenuEngineering fetches the COGS-products report + menus itself.
    id: 'menu-engineering',
    app: 'loaded-kitchen',
    connections: ['loadedhub'],
    label: 'Menu Engineering',
    icon: Grid2x2,
    agent: 'executive_chef',
    component: 'menu_engineering',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    id: 'tasks-executive_chef',
    app: 'norm-core',
    label: 'Tasks',
    icon: Timer,
    agent: 'executive_chef',
    component: 'automated_task_board',
    loadAction: {
      connector: 'norm',
      action: 'list_automated_tasks',
      defaultParams: () => ({ agent_slug: 'executive_chef' }),
    },
  },
  // App Builder
  {
    id: 'apps-hub',
    app: 'app-builder',
    label: 'Apps',
    icon: LayoutGrid,
    agent: 'app_builder',
    component: 'apps_dashboard',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
];

/**
 * A PINNED app as a page config. Dynamic — built from /api/apps at runtime —
 * so it lives beside the static list rather than in it. The id is namespaced
 * (`app:<slug>`) so it can never collide with a static page id.
 *
 * Which agent's menu it joins comes from the app itself.
 */
export function appPageConfig(app: { slug: string; name: string; icon?: string | null; agent?: string | null }): FunctionalPageConfig {
  return {
    id: `app:${app.slug}`,
    label: app.icon ? `${app.icon} ${app.name}` : app.name,
    icon: Blocks,
    // The app says which menu it belongs to — an HR app's pages sit beside
    // Hiring and Tasks, not off in a separate destination. Falling back to the
    // App Builder keeps every app that predates the choice exactly where it was.
    agent: app.agent || 'app_builder',
    // hierarchy v2: the org-level App switch gates this page too
    app: `custom:${app.slug}`,
    component: 'app_runner',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
    componentProps: { slug: app.slug },
  };
}
