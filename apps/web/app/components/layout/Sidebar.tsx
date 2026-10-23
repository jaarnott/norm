'use client';

import OrgSwitcher from './OrgSwitcher';
import type { CSSProperties } from 'react';
import { Home, Package, UserRound, Clock, ChartColumnBig, Megaphone, ChefHat, PartyPopper, Landmark, Blocks, Settings, LogOut, type LucideIcon } from 'lucide-react';
import { colors } from '../../lib/theme';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import Icon from '../ui/Icon';
import Avatar from '../ui/Avatar';
import { resolveAppIcon } from '../ui/appIcons';
import { navLabelFor, visibleAgentsFor } from '../../hooks/useAccess';
import type { AccessInfo } from '../../types';

export interface AgentTab {
  id: string;
  label: string;
  icon: LucideIcon;
  color: string;
}

// Reports uses ChartColumnBig: its bars are closed shapes, so the active
// duotone has something to fill (BarChart3's open strokes did not).
export const AGENTS: AgentTab[] = [
  { id: 'home', label: 'Home', icon: Home, color: colors.home },
  { id: 'reports', label: 'Reports', icon: ChartColumnBig, color: colors.reports },
  { id: 'procurement', label: 'Procurement', icon: Package, color: colors.procurement },
  { id: 'time_attendance', label: 'Time & Att.', icon: Clock, color: colors.time_attendance },
  { id: 'executive_chef', label: 'Exec Chef', icon: ChefHat, color: colors.executive_chef },
  { id: 'hr', label: 'HR', icon: UserRound, color: colors.hr },
  { id: 'marketing', label: 'Marketing', icon: Megaphone, color: colors.marketing },
  // Norm Functions — hired only while the organisation's functions_mode is on.
  // The per-member accents are retired, so no key of its own in lib/theme.ts
  // (which the MCP bundle hashes): the same neutral the others map to.
  { id: 'functions', label: 'Functions', icon: PartyPopper, color: colors.textSecondary },
  // Finance — hired per organisation (the Xero App). Same neutral as Functions.
  { id: 'finance', label: 'Finance', icon: Landmark, color: colors.textSecondary },
  { id: 'app_builder', label: 'App Builder', icon: Blocks, color: colors.app_builder },
];

interface SidebarUser {
  full_name: string;
  role: string;
  permissions?: string[];
  organizations?: { id: string; name: string | null }[];
  active_organization_id?: string | null;
}

function hasPermission(user: SidebarUser | null | undefined, ...perms: string[]): boolean {
  if (!user) return false;
  if (user.role === 'admin') return true;
  return perms.some(p => user.permissions?.includes(p));
}

/** An app in the apps section of the rail (from /api/apps). */
export interface RailApp {
  slug: string;
  name: string;
  icon?: string | null;
}

interface SidebarProps {
  selected: string;
  onSelect: (id: string) => void;
  threadCounts: Record<string, number>;
  user?: SidebarUser | null;
  onLogout?: () => void;
  /** false on a kitchen tablet: no Settings tile (the PIN'd person's
   *  settings are theirs, not the tablet's). */
  settings?: boolean;
  /** Hired team-member slugs (hierarchy v2). null/undefined = show every tab
   *  (gating inactive — fail-open). AGENTS stays the icon registry; this only
   *  decides which tabs render. */
  hired?: Set<string> | null;
  /** What the person's role reaches: narrows the agent tabs further. */
  access?: AccessInfo | null;
  /** The apps section: one tile per app after a divider from the agents
   *  (docs/plans/staff-access.md 11.7). Empty = no section. */
  apps?: RailApp[];
  /** The page open in the content area — an app tile is current when its
   *  page is open. */
  activePage?: string | null;
  /** Choosing an app tile: the caller sets activeAgent 'apps' + its page. */
  onSelectApp?: (slug: string) => void;
}

/** A 42px rail tile: icon only, named by its label (tooltip + screen readers). */
function tileStyle(active: boolean, extra?: CSSProperties): CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 42,
    height: 42,
    border: 'none',
    borderRadius: 'var(--radius)',
    backgroundColor: active ? 'var(--selected)' : 'transparent',
    color: active ? 'var(--text)' : 'var(--icon)',
    cursor: 'pointer',
    ...extra,
  };
}

export default function Sidebar({ selected, onSelect, user, onLogout, settings = true, hired, access, apps = [], activePage, onSelectApp }: SidebarProps) {
  const { isMobile } = useBreakpoint();
  const showSettings = settings && hasPermission(user, 'settings:connectors', 'settings:agents', 'org:read', 'org:members', 'org:venues', 'billing:read');
  const agents = visibleAgentsFor(AGENTS, hired, access);

  // On mobile, Sidebar is not rendered — navigation is handled by page.tsx
  if (isMobile) return null;

  return (
    <nav
      aria-label={navLabelFor(agents, apps.length)}
      style={{
        width: 60,
        minWidth: 60,
        height: '100%',
        backgroundColor: 'var(--canvas)',
        color: 'var(--text)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        borderRight: '1px solid var(--line)',
      }}
    >
      {/* Logo */}
      <div style={{ width: '100%', padding: '14px 0', textAlign: 'center', borderBottom: '1px solid var(--line)' }}>
        <div aria-hidden style={{ fontSize: 'var(--fs-xl)', fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1 }}>N</div>
      </div>

      {/* AI agents */}
      <div style={{ padding: '12px 0', flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
        {agents.map((agent) => {
          const isActive = selected === agent.id;
          return (
            <button
              key={agent.id}
              type="button"
              className="n-rail-tile"
              data-testid={`sidebar-${agent.id}`}
              onClick={() => onSelect(agent.id)}
              title={agent.label}
              aria-label={agent.label}
              aria-current={isActive ? 'page' : undefined}
              style={tileStyle(isActive)}
            >
              <Icon icon={agent.icon} size="nav" duo={isActive} />
            </button>
          );
        })}

        {/* The apps section: the team's apps, after a divider from the AI
            agents. A staff member with no agents sees Home, the divider and
            their apps. */}
        {apps.length > 0 && (
          <>
            <div className="n-rail-divider" role="separator" aria-label="Apps" data-testid="rail-apps-divider" />
            {apps.map((app) => {
              const isActive = selected === 'apps' && activePage === `app:${app.slug}`;
              return (
                <button
                  key={app.slug}
                  type="button"
                  className="n-rail-tile"
                  data-testid={`rail-app-${app.slug}`}
                  onClick={() => onSelectApp?.(app.slug)}
                  title={app.name}
                  aria-label={app.name}
                  aria-current={isActive ? 'page' : undefined}
                  style={tileStyle(isActive)}
                >
                  <Icon icon={resolveAppIcon({ slug: app.slug, icon: app.icon })} size="nav" duo={isActive} />
                </button>
              );
            })}
          </>
        )}
      </div>

      {/* Bottom: settings, who you are, sign out */}
      <div style={{ padding: '12px 0', borderTop: '1px solid var(--line)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
        {showSettings && (
          <button
            type="button"
            className="n-rail-tile"
            data-testid="sidebar-settings"
            onClick={() => onSelect('settings')}
            title="Settings"
            aria-label="Settings"
            aria-current={selected === 'settings' ? 'page' : undefined}
            style={tileStyle(selected === 'settings')}
          >
            <Icon icon={Settings} size={20} duo={selected === 'settings'} />
          </button>
        )}

        <OrgSwitcher organizations={user?.organizations} activeId={user?.active_organization_id} />

        {user && <Avatar name={user.full_name} title={`${user.full_name} (${user.role})`} />}

        {onLogout && (
          <button
            type="button"
            className="n-rail-tile"
            data-testid="sidebar-logout"
            onClick={onLogout}
            title="Sign out"
            aria-label="Sign out"
            style={tileStyle(false, { height: 34 })}
          >
            <Icon icon={LogOut} size={18} />
          </button>
        )}
      </div>
    </nav>
  );
}
