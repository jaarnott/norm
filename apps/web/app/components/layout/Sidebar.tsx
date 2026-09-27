'use client';

import { useState } from 'react';
import { Home, Package, UserRound, Clock, BarChart3, Megaphone, ChefHat, Blocks, Settings, LogOut, Menu, X, UserRoundPlus, type LucideIcon } from 'lucide-react';
import { colors } from '../../lib/theme';
import { useBreakpoint } from '../../hooks/useBreakpoint';

export interface AgentTab {
  id: string;
  label: string;
  icon: LucideIcon;
  color: string;
}

export const AGENTS: AgentTab[] = [
  { id: 'home', label: 'Home', icon: Home, color: colors.home },
  { id: 'procurement', label: 'Procurement', icon: Package, color: colors.procurement },
  { id: 'hr', label: 'HR', icon: UserRound, color: colors.hr },
  { id: 'time_attendance', label: 'Time & Att.', icon: Clock, color: colors.time_attendance },
  { id: 'marketing', label: 'Marketing', icon: Megaphone, color: colors.marketing },
  { id: 'reports', label: 'Reports', icon: BarChart3, color: colors.reports },
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
  children?: React.ReactNode;
  /** Hired team-member slugs (hierarchy v2). null/undefined = show every tab
   *  (gating inactive — fail-open). AGENTS stays the icon/colour registry;
   *  this only decides which tabs render. */
  hired?: Set<string> | null;
}

export default function Sidebar({ selected, onSelect, threadCounts, user, onLogout, children, hired }: SidebarProps) {
  const { isMobile } = useBreakpoint();
  const [menuOpen, setMenuOpen] = useState(false);
  const showSettings = hasPermission(user, 'settings:connectors', 'settings:agents', 'org:read', 'org:members', 'org:venues', 'billing:read');

  // On mobile, Sidebar is not rendered — navigation is handled by page.tsx
  if (isMobile) return null;

  return (
    <div style={{
      width: 60,
      minWidth: 60,
      backgroundColor: '#faf8f5',
      color: '#1a1a1a',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      height: '100vh',
      borderRight: '1px solid #e2ddd7',
    }}>
      {/* Logo */}
      <div style={{
        padding: '1rem 0',
        borderBottom: '1px solid #e2ddd7',
        width: '100%',
        textAlign: 'center',
      }}>
        <div style={{ fontSize: '1.1rem', fontWeight: 700, letterSpacing: '-0.02em' }}>N</div>
      </div>

      {/* Agent icons */}
      <div style={{ padding: '0.75rem 0', flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
        {AGENTS.filter((a) => a.id === 'home' || !hired || hired.has(a.id)).map((agent) => {
          const isActive = selected === agent.id;
          return (
            <button
              key={agent.id}
              data-testid={`sidebar-${agent.id}`}
              onClick={() => onSelect(agent.id)}
              title={agent.label}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                position: 'relative',
                width: 42,
                height: 42,
                border: 'none',
                borderRadius: 8,
                backgroundColor: isActive ? '#f0ebe5' : 'transparent',
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              <agent.icon size={22} strokeWidth={1.75} />
            </button>
          );
        })}
        <button
          data-testid="sidebar-team"
          onClick={() => onSelect('team')}
          title="Your AI team — hire agents"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 42,
            height: 42,
            border: '1px dashed #d5cfc7',
            borderRadius: 8,
            backgroundColor: selected === 'team' ? '#f0ebe5' : 'transparent',
            cursor: 'pointer',
            fontFamily: 'inherit',
            color: '#8a8378',
            marginTop: 6,
          }}
        >
          <UserRoundPlus size={20} strokeWidth={1.75} />
        </button>
      </div>

      {/* Bottom section: Settings + User + Logout */}
      <div style={{ padding: '0.75rem 0', borderTop: '1px solid #e2ddd7', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
        {showSettings && (
          <button
            data-testid="sidebar-settings"
            onClick={() => onSelect('settings')}
            title="Settings"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 42,
              height: 42,
              border: 'none',
              borderRadius: 8,
              backgroundColor: selected === 'settings' ? '#f0ebe5' : 'transparent',
              cursor: 'pointer',
              fontFamily: 'inherit',
              color: '#999',
            }}
          >
            <Settings size={20} strokeWidth={1.75} />
          </button>
        )}

        {user && (
          <div
            title={`${user.full_name} (${user.role})`}
            style={{
              width: 32,
              height: 32,
              borderRadius: '50%',
              backgroundColor: user.role === 'admin' ? '#1a1a1a' : '#b8e6cc',
              color: '#fff',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '0.7rem',
              fontWeight: 700,
              cursor: 'default',
            }}
          >
            {user.full_name.charAt(0).toUpperCase()}
          </div>
        )}

        {onLogout && (
          <button
            data-testid="sidebar-logout"
            onClick={onLogout}
            title="Sign out"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 42,
              height: 28,
              border: 'none',
              borderRadius: 6,
              backgroundColor: 'transparent',
              cursor: 'pointer',
              fontFamily: 'inherit',
              color: '#bbb',
            }}
          >
            <LogOut size={16} strokeWidth={1.75} />
          </button>
        )}
      </div>
    </div>
  );
}
