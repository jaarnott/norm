'use client';

import { CalendarCheck, LayoutGrid, MessageCircle, UserRound, type LucideIcon } from 'lucide-react';
import Icon from '../ui/Icon';
import { resolveAppIcon } from '../ui/appIcons';
import { teamBarTabsFor, type TeamBarTab } from '../../hooks/useAccess';
import type { RailApp } from './Sidebar';

export type TeamBarActive = 'today' | 'ask' | 'me' | 'apps' | `app:${string}` | null;

interface TeamBarProps<T extends RailApp> {
  /** The apps section, in order — team apps first, then App-platform apps;
   *  the bar shows the first two (or one + Apps). */
  apps: T[];
  /** Which tab is current (`app:<slug>` for a team app's tab too). */
  active: TeamBarActive;
  onToday: () => void;
  /** A tap on an app's tab, with the app it is for. */
  onApp: (app: T) => void;
  /** "Apps" (three or more apps): opens the menu at the apps row. */
  onApps: () => void;
  onAsk: () => void;
  onMe: () => void;
  /** false on a kitchen tablet: no Ask Norm tab, because a tablet never chats. */
  ask?: boolean;
}

const FIXED_ICONS: Record<'today' | 'ask' | 'me' | 'apps', LucideIcon> = {
  today: CalendarCheck,
  ask: MessageCircle,
  me: UserRound,
  apps: LayoutGrid,
};

/**
 * The phone's bottom bar for the apps section (docs/plans/staff-access.md,
 * section 2 and 11.7): Today · the apps · Ask Norm · Me. It belongs to what
 * a team app looks like on a phone, the way the composer belongs to an AI
 * agent: venue staff always have it, a manager sees it inside a team app.
 * The active tab is a tan pill.
 */
export default function TeamBar<T extends RailApp>({ apps, active, onToday, onApp, onApps, onAsk, onMe, ask = true }: TeamBarProps<T>) {
  const tabs = teamBarTabsFor(apps, { ask });
  const iconFor = (tab: TeamBarTab): LucideIcon => {
    if (tab.id.startsWith('app:')) {
      const slug = (tab as { slug: string }).slug;
      const app = apps.find(a => a.slug === slug);
      return resolveAppIcon({ slug, icon: app?.icon });
    }
    return FIXED_ICONS[tab.id as 'today' | 'ask' | 'me' | 'apps'];
  };
  const press = (tab: TeamBarTab) => {
    if (tab.id === 'today') onToday();
    else if (tab.id === 'ask') onAsk();
    else if (tab.id === 'me') onMe();
    else if (tab.id === 'apps') onApps();
    else {
      const app = apps.find(a => a.slug === (tab as { slug: string }).slug);
      if (app) onApp(app);
    }
  };
  const testIdFor = (tab: TeamBarTab) =>
    tab.id === 'today' ? 'teambar-today'
      : tab.id === 'ask' ? 'teambar-ask'
      : tab.id === 'me' ? 'teambar-me'
      : tab.id === 'apps' ? 'teambar-apps'
      : `teambar-${tab.id.replace(':', '-')}`;

  return (
    <nav aria-label="Team" data-testid="teambar" className="n-teambar safe-bottom">
      {tabs.map(tab => {
        // An open app with no tab of its own lights the Apps tab instead.
        const isActive = active === tab.id
          || (tab.id === 'apps' && !!active?.startsWith('app:') && !tabs.some(t => t.id === active));
        return (
          <button
            key={tab.id}
            type="button"
            className="n-teambar-tab"
            data-testid={testIdFor(tab)}
            aria-current={isActive ? 'page' : undefined}
            onClick={() => press(tab)}
          >
            <span className="n-teambar-pill">
              <Icon icon={iconFor(tab)} size={22} tone={isActive ? 'accent' : 'muted'} />
            </span>
            <span className="n-teambar-label">{tab.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
