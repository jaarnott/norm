import { Calendar, Users, Timer, BarChart3, ShoppingCart, Receipt, LayoutDashboard, BookOpen, ChefHat, Clock, Blocks, LayoutGrid, Grid2x2, type LucideIcon } from 'lucide-react';

export interface FunctionalPageConfig {
  id: string;
  label: string;
  icon: LucideIcon;
  agent: string;
  component: string;
  loadAction: {
    connector: string;
    action: string;
    defaultParams: () => Record<string, unknown>;
  };
  componentProps?: Record<string, unknown>;
}

function getCurrentWeekRange(): { start_datetime: string; end_datetime: string } {
  const now = new Date();
  const day = now.getDay();
  const monday = new Date(now);
  monday.setDate(now.getDate() - (day === 0 ? 6 : day - 1));
  monday.setHours(0, 0, 0, 0);
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);
  sunday.setHours(23, 59, 59, 0);

  const fmt = (d: Date) => {
    const offset = '+13:00';
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}${offset}`;
  };

  return { start_datetime: fmt(monday), end_datetime: fmt(sunday) };
}

export const FUNCTIONAL_PAGES: FunctionalPageConfig[] = [
  // Dashboards (one per agent)
  {
    id: 'dashboard-hr',
    label: 'Dashboard',
    icon: LayoutDashboard,
    agent: 'hr',
    component: 'dashboard_view',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
    componentProps: { agent_slug: 'hr' },
  },
  {
    id: 'dashboard-procurement',
    label: 'Dashboard',
    icon: LayoutDashboard,
    agent: 'procurement',
    component: 'dashboard_view',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
    componentProps: { agent_slug: 'procurement' },
  },
  {
    id: 'dashboard-reports',
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
    label: 'Dashboard',
    icon: LayoutDashboard,
    agent: 'marketing',
    component: 'dashboard_view',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
    componentProps: { agent_slug: 'marketing' },
  },
  {
    id: 'tasks-marketing',
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
    label: 'Dashboard',
    icon: LayoutDashboard,
    agent: 'time_attendance',
    component: 'dashboard_view',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
    componentProps: { agent_slug: 'time_attendance' },
  },
  {
    id: 'roster',
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
    label: 'Hiring',
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
    label: 'Invoices',
    icon: Receipt,
    agent: 'procurement',
    component: 'invoices_dashboard',
    // Self-loading: the dashboard fetches /invoice-fixes/outstanding itself, so
    // no connector loadAction (mirrors SavedReportsBoard's _none pattern).
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    id: 'tasks-procurement',
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
    label: 'Recipes',
    icon: ChefHat,
    agent: 'executive_chef',
    component: 'recipe_editor',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    // Self-loading: MenuEngineering fetches the COGS-products report + menus itself.
    id: 'menu-engineering',
    label: 'Menu Engineering',
    icon: Grid2x2,
    agent: 'executive_chef',
    component: 'menu_engineering',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    id: 'tasks-executive_chef',
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
 */
export function appPageConfig(app: { slug: string; name: string; icon?: string | null }): FunctionalPageConfig {
  return {
    id: `app:${app.slug}`,
    label: app.icon ? `${app.icon} ${app.name}` : app.name,
    icon: Blocks,
    agent: 'app_builder',
    component: 'app_runner',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
    componentProps: { slug: app.slug },
  };
}
