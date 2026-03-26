'use client';

import { useState } from 'react';
import { Home, Package, UserRound, BarChart3, Settings, LogOut, Menu, X, type LucideIcon } from 'lucide-react';
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
  const [menuOpen, setMenuOpen] = useState(false);
  const showSettings = hasPermission(user, 'settings:connectors', 'settings:agents', 'org:read', 'org:members', 'org:venues', 'billing:read');

  if (isMobile) {
    return (
      <>
        {/* Hamburger button — fixed top-left */}
        <button
          onClick={() => setMenuOpen(!menuOpen)}
          aria-label="Toggle menu"
          style={{
            position: 'fixed', top: 10, left: 10, zIndex: 200,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            minWidth: 44, minHeight: 44, border: 'none', borderRadius: 8,
            backgroundColor: menuOpen ? '#f0ebe5' : '#faf8f5',
            cursor: 'pointer', boxShadow: '0 1px 4px rgba(0,0,0,0.1)',
          }}
        >
          {menuOpen ? <X size={22} strokeWidth={1.75} /> : <Menu size={22} strokeWidth={1.75} />}
        </button>

        {/* Slide-out menu */}
        {menuOpen && (
          <>
            {/* Backdrop */}
            <div
              onClick={() => setMenuOpen(false)}
              style={{
                position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.3)',
                zIndex: 150,
              }}
            />
            {/* Panel */}
            <div className="safe-bottom" style={{
              position: 'fixed', top: 0, left: 0, bottom: 0, width: 260,
              backgroundColor: '#faf8f5', borderRight: '1px solid #e2ddd7',
              zIndex: 160, display: 'flex', flexDirection: 'column',
              padding: '4.5rem 1rem 1rem',
            }}>
              {/* Logo */}
              <div style={{ fontSize: '1.2rem', fontWeight: 800, color: '#a08060', marginBottom: '1.5rem' }}>
                Norm
              </div>

              {/* Agent links */}
              {AGENTS.map((agent) => {
                const isActive = selected === agent.id;
                return (
                  <button
                    key={agent.id}
                    data-testid={`sidebar-${agent.id}`}
                    onClick={() => { onSelect(agent.id); setMenuOpen(false); }}
                    style={{
                      display: 'flex', alignItems: 'center', gap: '0.75rem',
                      width: '100%', padding: '0.7rem 0.75rem', marginBottom: 2,
                      border: 'none', borderRadius: 8,
                      backgroundColor: isActive ? '#f0ebe5' : 'transparent',
                      cursor: 'pointer', fontFamily: 'inherit',
                      fontSize: '0.9rem', fontWeight: isActive ? 600 : 400,
                      color: isActive ? '#1a1a1a' : '#666',
                    }}
                  >
                    <agent.icon size={20} strokeWidth={1.75} />
                    {agent.label}
                  </button>
                );
              })}

              {/* Spacer */}
              <div style={{ flex: 1 }} />

              {/* Settings */}
              {showSettings && (
                <button
                  data-testid="sidebar-settings"
                  onClick={() => { onSelect('settings'); setMenuOpen(false); }}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '0.75rem',
                    width: '100%', padding: '0.7rem 0.75rem', marginBottom: 2,
                    border: 'none', borderRadius: 8,
                    backgroundColor: selected === 'settings' ? '#f0ebe5' : 'transparent',
                    cursor: 'pointer', fontFamily: 'inherit',
                    fontSize: '0.9rem', color: '#999',
                  }}
                >
                  <Settings size={20} strokeWidth={1.75} />
                  Settings
                </button>
              )}

              {/* User + logout */}
              {user && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', padding: '0.5rem 0.75rem' }}>
                  <div style={{
                    width: 28, height: 28, borderRadius: '50%',
                    backgroundColor: user.role === 'admin' ? '#1a1a1a' : '#b8e6cc',
                    color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: '0.65rem', fontWeight: 700,
                  }}>
                    {user.full_name.charAt(0).toUpperCase()}
                  </div>
                  <span style={{ fontSize: '0.8rem', color: '#666', flex: 1 }}>{user.full_name}</span>
                  {onLogout && (
                    <button
                      data-testid="sidebar-logout"
                      onClick={() => { onLogout(); setMenuOpen(false); }}
                      title="Sign out"
                      style={{
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        minWidth: 36, minHeight: 36, border: 'none', borderRadius: 6,
                        backgroundColor: 'transparent', cursor: 'pointer', color: '#bbb',
                      }}
                    >
                      <LogOut size={16} strokeWidth={1.75} />
                    </button>
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </>
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
