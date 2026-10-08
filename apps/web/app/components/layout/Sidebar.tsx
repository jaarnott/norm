'use client';

import type { CSSProperties } from 'react';
import { Home, Package, UserRound, Clock, ChartColumnBig, Megaphone, ChefHat, Blocks, Settings, LogOut, UserRoundPlus, type LucideIcon } from 'lucide-react';
import { colors } from '../../lib/theme';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import Icon from '../ui/Icon';
import Avatar from '../ui/Avatar';

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
  { id: 'procurement', label: 'Procurement', icon: Package, color: colors.procurement },
  { id: 'hr', label: 'HR', icon: UserRound, color: colors.hr },
  { id: 'time_attendance', label: 'Time & Att.', icon: Clock, color: colors.time_attendance },
  { id: 'marketing', label: 'Marketing', icon: Megaphone, color: colors.marketing },
  { id: 'reports', label: 'Reports', icon: ChartColumnBig, color: colors.reports },
  { id: 'executive_chef', label: 'Exec Chef', icon: ChefHat, color: colors.executive_chef },
  { id: 'app_builder', label: 'App Builder', icon: Blocks, color: colors.app_builder },
];

interface SidebarUser {
  full_name: string;
  role: string;
  permissions?: string[];
}

function hasPermission(user: SidebarUser | null | undefined, ...perms: string[]): boolean {
  if (!user) return false;
  if (user.role === 'admin') return true;
  return perms.some(p => user.permissions?.includes(p));
}

interface SidebarProps {
  selected: string;
  onSelect: (id: string) => void;
  threadCounts: Record<string, number>;
  user?: SidebarUser | null;
  onLogout?: () => void;
  /** Hired team-member slugs (hierarchy v2). null/undefined = show every tab
   *  (gating inactive — fail-open). AGENTS stays the icon registry; this only
   *  decides which tabs render. */
  hired?: Set<string> | null;
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

export default function Sidebar({ selected, onSelect, user, onLogout, hired }: SidebarProps) {
  const { isMobile } = useBreakpoint();
  const showSettings = hasPermission(user, 'settings:connectors', 'settings:agents', 'org:read', 'org:members', 'org:venues', 'billing:read');

  // On mobile, Sidebar is not rendered — navigation is handled by page.tsx
  if (isMobile) return null;

  return (
    <nav
      aria-label="Team members"
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

      {/* Team members */}
      <div style={{ padding: '12px 0', flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
        {AGENTS.filter((a) => a.id === 'home' || !hired || hired.has(a.id)).map((agent) => {
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
        <button
          type="button"
          className="n-rail-tile"
          data-testid="sidebar-team"
          onClick={() => onSelect('team')}
          title="Your AI team — hire agents"
          aria-label="Your AI team"
          aria-current={selected === 'team' ? 'page' : undefined}
          style={tileStyle(selected === 'team', { border: '1px dashed var(--line-strong)', marginTop: 6 })}
        >
          <Icon icon={UserRoundPlus} size={20} duo={selected === 'team'} />
        </button>
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
