'use client';

import { Home, Package, UserRound, BarChart3, Settings, LogOut, type LucideIcon } from 'lucide-react';
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
  { id: 'reports', label: 'Reports', icon: BarChart3, color: colors.reports },
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
  taskCounts: Record<string, number>;
  user?: SidebarUser | null;
  onLogout?: () => void;
}

export default function Sidebar({ selected, onSelect, taskCounts, user, onLogout }: SidebarProps) {
  const { isMobile } = useBreakpoint();
  const showSettings = hasPermission(user, 'settings:connectors', 'settings:agents', 'org:read', 'org:members', 'org:venues', 'billing:read');

  if (isMobile) {
    return (
      <div className="safe-bottom" style={{
        position: 'fixed',
        bottom: 0,
        left: 0,
        right: 0,
        height: 56,
        backgroundColor: '#faf8f5',
        borderTop: '1px solid #e2ddd7',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-around',
        zIndex: 100,
      }}>
        {AGENTS.map((agent) => {
          const isActive = selected === agent.id;
          return (
            <button
              key={agent.id}
              data-testid={`sidebar-${agent.id}`}
              onClick={() => onSelect(agent.id)}
              style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                minWidth: 48, minHeight: 48,
                border: 'none', borderRadius: 8,
                backgroundColor: isActive ? '#f0ebe5' : 'transparent',
                cursor: 'pointer', fontFamily: 'inherit', gap: 2,
              }}
            >
              <agent.icon size={20} strokeWidth={1.75} />
              <span style={{ fontSize: '0.6rem', color: isActive ? '#1a1a1a' : '#999' }}>{agent.label}</span>
            </button>
          );
        })}
        {showSettings && (
          <button
            data-testid="sidebar-settings"
            onClick={() => onSelect('settings')}
            style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
              minWidth: 48, minHeight: 48,
              border: 'none', borderRadius: 8,
              backgroundColor: selected === 'settings' ? '#f0ebe5' : 'transparent',
              cursor: 'pointer', fontFamily: 'inherit', gap: 2,
            }}
          >
            <Settings size={20} strokeWidth={1.75} />
            <span style={{ fontSize: '0.6rem', color: selected === 'settings' ? '#1a1a1a' : '#999' }}>Settings</span>
          </button>
        )}
      </div>
    );
  }

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
        {AGENTS.map((agent) => {
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
