import { Calendar, Users, Timer, BarChart3, ShoppingCart, LayoutDashboard, Clock, type LucideIcon } from 'lucide-react';

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
    id: 'marketing-calendar',
    label: 'Calendar',
    icon: Calendar,
    agent: 'marketing',
    component: 'mcp_embed',
    loadAction: {
      connector: 'orbit_marketing',
      action: 'get_calendar_items',
      defaultParams: () => {
        const now = new Date();
        const pad = (n: number) => String(n).padStart(2, '0');
        return {
          start_date: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-01`,
          end_date: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate())}`,
        };
      },
    },
    componentProps: { container_hint: 'full_page', connector_name: 'orbit_marketing' },
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
];
