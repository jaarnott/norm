import { Calendar, Users, Timer, BarChart3, ShoppingCart, type LucideIcon } from 'lucide-react';

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
  selfLoading?: boolean; // Component handles its own data loading (skip FunctionalPage from-connector call)
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
  {
    id: 'roster',
    label: 'Roster',
    icon: Calendar,
    agent: 'hr',
    component: 'roster_editor',
    selfLoading: true,
    loadAction: {
      connector: 'loadedhub',
      action: 'get_roster',
      defaultParams: getCurrentWeekRange,
    },
  },
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
    component: 'orders_page',
    loadAction: {
      connector: '_none',
      action: '_none',
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
