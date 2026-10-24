import { Banknote, BarChart3, BookOpen, Calendar, CalendarClock, CalendarDays, ChartColumnIncreasing, ChefHat, ClipboardCheck, ClipboardList, Contact, Gavel, Grid2x2, IdCard, Inbox, LayoutDashboard, LayoutGrid, Music, Package, Receipt, Settings, ShoppingCart, Table, Timer, TrendingUp, UserCheck, Users, type LucideIcon } from 'lucide-react';
import { resolveAppIcon } from '../ui/appIcons';
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
  /** An org permission the viewer must hold to see this page (Oct 2026,
   *  Finance): `payroll:read` for Payroll and Profit & Loss, `finance:read`
   *  for the Weekly Summary and Budgets. A page without one keeps the plain
   *  rule — it shows to every user of an organisation whose App is on —
   *  which the Entertainers page relies on (venue access, decided
   *  server-side; its Payments tab is gated inside the page). The server
   *  still gates every route; this only keeps a page off a menu it would
   *  refuse to load. */
  permission?: string;
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
    app: 'rosters',
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
    // Time & Attendance → Attendance: rostered against clocked hours (get_labour). Self-loading.
    id: 'attendance',
    app: 'attendance',
    connections: ['loadedhub'],
    label: 'Attendance',
    icon: UserCheck,
    agent: 'time_attendance',
    component: 'attendance_board',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
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
    app: 'hiring',
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
    // HR → Employee Records: the BambooHR directory (get_hr). Shown only with BambooHR connected.
    id: 'employee-records',
    app: 'employee-records',
    connections: ['bamboohr'],
    label: 'Employees',
    icon: IdCard,
    agent: 'hr',
    component: 'employee_records',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    // Functions and Finance run scheduled tasks like every other member
    // (owner, 18 Oct 2026 — Functions was "chat only" before).
    id: 'tasks-functions',
    app: 'norm-core',
    label: 'Tasks',
    icon: Timer,
    agent: 'functions',
    component: 'automated_task_board',
    loadAction: {
      connector: 'norm',
      action: 'list_automated_tasks',
      defaultParams: () => ({ agent_slug: 'functions' }),
    },
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
    app: 'ordering',
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
    app: 'invoice-receiving',
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
    app: 'supplier-tenders',
    connections: ['loaded_legacy'],
    label: 'Supplier Tenders',
    icon: Gavel,
    agent: 'procurement',
    component: 'supplier_tenders',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    // Loaded Stock → Catalogue (docs/plans/stock-catalogue-page.md): a venue's
    // stock items, units and suppliers — edit, retire/delete, restore, merge.
    // Self-loading via /api/stock-catalogue (routers/stock_catalogue.py), which
    // runs get_stock / manage_stock_item / manage_supplier. Unit changes and
    // item merges also need the venue's Loaded Legacy login.
    id: 'stock-catalogue',
    app: 'ordering',
    connections: ['loadedhub', 'loaded_legacy'],
    label: 'Catalogue',
    icon: Package,
    agent: 'procurement',
    component: 'stock_catalogue',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    // Procurement → Stocktakes: counts, variances and cost of goods (get_stocktakes, get_stock, get_purchase_cogs). Self-loading.
    id: 'stocktakes',
    app: 'stocktakes',
    connections: ['loadedhub'],
    label: 'Stocktakes',
    icon: ClipboardCheck,
    agent: 'procurement',
    component: 'stocktakes_board',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  // Norm Functions (native module — app 'functions', member 'functions'). Each page loads its own
  // data from /api/functions/*, so no loadAction.
  {
    id: 'functions-dashboard',
    app: 'functions',
    label: 'Dashboard',
    icon: LayoutDashboard,
    agent: 'functions',
    component: 'functions_dashboard',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    id: 'functions-enquiries',
    app: 'functions',
    label: 'Enquiries',
    icon: ClipboardList,
    agent: 'functions',
    component: 'functions_enquiries',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    id: 'functions-events',
    app: 'functions',
    label: 'Events',
    icon: CalendarDays,
    agent: 'functions',
    component: 'functions_events',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    id: 'functions-inbox',
    app: 'functions',
    label: 'Inbox',
    icon: Inbox,
    agent: 'functions',
    component: 'functions_inbox',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    id: 'functions-contacts',
    app: 'functions',
    label: 'Contacts',
    icon: Contact,
    agent: 'functions',
    component: 'functions_contacts',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    id: 'functions-reports',
    app: 'functions',
    label: 'Reports',
    icon: BarChart3,
    agent: 'functions',
    component: 'functions_reports',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    id: 'functions-settings',
    app: 'functions',
    label: 'Settings',
    icon: Settings,
    agent: 'functions',
    component: 'functions_settings',
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
    // Reports → Sales Analysis: products, staff and period comparisons (get_sales; set_revenue_group). Self-loading.
    id: 'sales-analysis',
    app: 'sales-analysis',
    connections: ['loadedhub'],
    label: 'Sales Analysis',
    icon: ChartColumnIncreasing,
    agent: 'reports',
    component: 'sales_analysis',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    // Reports → Weekly Report: last week's report as emailed, who gets it and when. Self-loading.
    id: 'weekly-report',
    app: 'weekly-report',
    connections: ['loadedhub'],
    label: 'Weekly Report',
    icon: CalendarClock,
    agent: 'reports',
    component: 'weekly_report',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
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
    app: 'menu-manager',
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
    app: 'recipes-and-costing',
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
    app: 'menu-engineering',
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
  // Finance — the menu reads in this order (pageRegistry.test.ts pins it):
  // Dashboard · Weekly Summary · Profit & Loss · Budgets · Bills · Payroll ·
  // Entertainers · Tasks. Payroll, Entertainers and Budgets are one page each
  // with tabs (Oct 2026; old page ids are translated by pageAliases.ts). The
  // Payroll, Entertainers and Budget Apps' pages mirror
  // app/services/finance_core/catalog.py — ids, labels, icons and apps are
  // pinned by an API test. Entertainers has no permission: it shows to
  // everyone with a venue (the server keeps them to their venues) and its
  // Payments tab is gated inside the page. Payroll declares no `connections`:
  // that guard checks the active venue, and a head-office entity may have no
  // Loaded login.
  {
    // Finance's board: sales, purchases and labour from Loaded, bills and the
    // profit and loss from Xero for the venues connected to it. The "Finance"
    // template (dashboard_templates.py) is what a person starts it from.
    id: 'dashboard-finance',
    app: 'norm-core',
    label: 'Dashboard',
    icon: LayoutDashboard,
    agent: 'finance',
    component: 'dashboard_view',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
    componentProps: { agent_slug: 'finance' },
  },
  {
    id: 'finance-summary',
    app: 'budget',
    label: 'Weekly Summary',
    icon: BarChart3,
    agent: 'finance',
    component: 'finance_summary',
    permission: 'finance:read',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    // Finance → Profit & Loss: a venue's P&L from Xero by month (get_profit_and_loss). Self-loading.
    // payroll:read because xero.get_profit_and_loss requires it (it shows
    // wages); without it a manager saw the item and then an error.
    id: 'profit-and-loss',
    app: 'profit-and-loss',
    connections: ['xero'],
    label: 'Profit & Loss',
    icon: TrendingUp,
    agent: 'finance',
    component: 'profit_and_loss',
    permission: 'payroll:read',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    id: 'finance-budgets',
    app: 'budget',
    label: 'Budgets',
    icon: Table,
    agent: 'finance',
    component: 'finance_budgets',
    permission: 'finance:read',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    // Finance → Bills: bills in Xero for a venue. Self-loading — the page
    // loader runs raw GET rows only, and xero.get_bills is a consolidator, so
    // the component calls /connectors/xero/execute/get_bills itself. Declared
    // in the Xero App's marketplace composition (sync_marketplace_catalog.py).
    id: 'xero-bills',
    app: 'bills',
    connections: ['xero'],
    label: 'Bills',
    icon: Receipt,
    agent: 'finance',
    component: 'xero_bills',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    id: 'payroll',
    app: 'payroll',
    label: 'Payroll',
    icon: Banknote,
    agent: 'finance',
    component: 'payroll',
    permission: 'payroll:read',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    id: 'entertainers',
    app: 'entertainers',
    label: 'Entertainers',
    icon: Music,
    agent: 'finance',
    component: 'entertainers',
    loadAction: { connector: '_none', action: '_none', defaultParams: () => ({}) },
  },
  {
    id: 'tasks-finance',
    app: 'norm-core',
    label: 'Tasks',
    icon: Timer,
    agent: 'finance',
    component: 'automated_task_board',
    loadAction: {
      connector: 'norm',
      action: 'list_automated_tasks',
      defaultParams: () => ({ agent_slug: 'finance' }),
    },
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
    // One icon per menu item, and it is the app's own — never the generic
    // app icon beside an emoji in the label.
    label: app.name,
    icon: resolveAppIcon({ slug: app.slug, icon: app.icon }),
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
