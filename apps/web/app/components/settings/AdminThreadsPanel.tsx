'use client';

import { useState, useEffect, useCallback } from 'react';
import { Search, ChevronLeft, ChevronRight, User as UserIcon } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import { colors } from '../../lib/theme';
import type { AdminThread, Thread } from '../../types';
import ThreadDetail from '../threads/ThreadDetail';

const DOMAIN_OPTIONS = [
  { value: '', label: 'All agents' },
  { value: 'procurement', label: 'Procurement' },
  { value: 'hr', label: 'HR' },
  { value: 'time_attendance', label: 'Time & Attendance' },
  { value: 'marketing', label: 'Marketing' },
  { value: 'reports', label: 'Reports' },
  { value: 'meta', label: 'Meta' },
];

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'completed', label: 'Completed' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'awaiting_approval', label: 'Awaiting approval' },
  { value: 'awaiting_tool_approval', label: 'Tool approval' },
  { value: 'awaiting_user_input', label: 'Awaiting input' },
  { value: 'needs_clarification', label: 'Needs clarification' },
];

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  completed: { bg: colors.badgeApproved.bg, color: colors.badgeApproved.text },
  in_progress: { bg: '#e8f0fe', color: '#1a73e8' },
  awaiting_approval: { bg: colors.badgeApproval.bg, color: colors.badgeApproval.text },
  awaiting_tool_approval: { bg: '#e8daef', color: '#6c3483' },
  awaiting_user_input: { bg: colors.badgeInput.bg, color: colors.badgeInput.text },
  needs_clarification: { bg: colors.badgeClarification.bg, color: colors.badgeClarification.text },
};

function timeAgo(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return date.toLocaleDateString();
}

function getDomainColor(domain: string): string {
  return (colors as unknown as Record<string, string>)[domain] || colors.unknown;
}

const PAGE_SIZE = 50;

interface UserOption {
  id: string;
  email: string;
  full_name: string;
}

export default function AdminThreadsPanel() {
  // Filters
  const [userFilter, setUserFilter] = useState('');
  const [domainFilter, setDomainFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [search, setSearch] = useState('');
  const [searchDebounced, setSearchDebounced] = useState('');

  // Data
  const [threads, setThreads] = useState<AdminThread[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [users, setUsers] = useState<UserOption[]>([]);

  // Selected thread
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [selectedThread, setSelectedThread] = useState<(Thread & { user_name?: string; user_email?: string }) | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => setSearchDebounced(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  // Collect unique users from thread results for the user filter
  useEffect(() => {
    const seen = new Map<string, UserOption>();
    for (const t of threads) {
      if (t.user_email && !seen.has(t.user_email)) {
        seen.set(t.user_email, { id: (t as unknown as Record<string, string>).user_id || '', email: t.user_email, full_name: t.user_name || '' });
      }
    }
    // Merge with existing to accumulate across pages
    setUsers(prev => {
      const map = new Map<string, UserOption>();
      for (const u of prev) map.set(u.email, u);
      for (const u of seen.values()) map.set(u.email, u);
      return Array.from(map.values()).sort((a, b) => a.full_name.localeCompare(b.full_name));
    });
  }, [threads]);

  // Fetch threads
  useEffect(() => {
    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('page_size', String(PAGE_SIZE));
    if (userFilter) params.set('user_id', userFilter);
    if (domainFilter) params.set('domain', domainFilter);
    if (statusFilter) params.set('status', statusFilter);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    if (searchDebounced) params.set('search', searchDebounced);

    setLoading(true);
    apiFetch(`/api/admin/threads?${params}`)
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (data) {
          setThreads(data.threads || []);
          setTotal(data.total || 0);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [page, userFilter, domainFilter, statusFilter, dateFrom, dateTo, searchDebounced]);

  // Reset page when filters change
  useEffect(() => {
    setPage(1);
  }, [userFilter, domainFilter, statusFilter, dateFrom, dateTo, searchDebounced]);

  // Fetch thread detail
  useEffect(() => {
    if (!selectedThreadId) { setSelectedThread(null); return; }
    setDetailLoading(true);
    apiFetch(`/api/admin/threads/${selectedThreadId}`)
      .then(res => res.ok ? res.json() : null)
      .then(data => { if (data) setSelectedThread(data); })
      .catch(() => {})
      .finally(() => setDetailLoading(false));
  }, [selectedThreadId]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const noop = useCallback(() => {}, []);

  const selectStyle: React.CSSProperties = {
    padding: '6px 8px', fontSize: '0.78rem', border: `1px solid ${colors.border}`,
    borderRadius: 6, background: '#fff', color: colors.textPrimary, outline: 'none',
    minWidth: 0, flex: 1,
  };

  return (
    <div style={{ display: 'flex', height: '100%', background: colors.pageBg }}>
      {/* Left pane — thread list */}
      <div style={{
        width: 380, minWidth: 320, borderRight: `1px solid ${colors.border}`,
        display: 'flex', flexDirection: 'column', background: '#fff',
      }}>
        {/* Filters */}
        <div style={{ padding: '12px 14px', borderBottom: `1px solid ${colors.borderLight}`, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {/* Search */}
          <div style={{ position: 'relative' }}>
            <Search size={14} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: colors.textMuted }} />
            <input
              type="text"
              placeholder="Search threads..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{ ...selectStyle, flex: undefined, width: '100%', paddingLeft: 30 }}
            />
          </div>
          {/* Dropdowns row */}
          <div style={{ display: 'flex', gap: 6 }}>
            <select value={userFilter} onChange={e => setUserFilter(e.target.value)} style={selectStyle}>
              <option value="">All users</option>
              {users.map(u => (
                <option key={u.email} value={u.id || u.email}>{u.full_name || u.email}</option>
              ))}
            </select>
            <select value={domainFilter} onChange={e => setDomainFilter(e.target.value)} style={selectStyle}>
              {DOMAIN_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={selectStyle}>
              {STATUS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={{ ...selectStyle, flex: 0.8 }} title="From date" />
            <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} style={{ ...selectStyle, flex: 0.8 }} title="To date" />
          </div>
        </div>

        {/* Thread list */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {loading && threads.length === 0 && (
            <div style={{ padding: 24, textAlign: 'center', color: colors.textMuted, fontSize: '0.85rem' }}>Loading...</div>
          )}
          {!loading && threads.length === 0 && (
            <div style={{ padding: 24, textAlign: 'center', color: colors.textMuted, fontSize: '0.85rem' }}>No threads found</div>
          )}
          {threads.map(t => {
            const isSelected = t.id === selectedThreadId;
            const statusStyle = STATUS_COLORS[t.status] || { bg: '#f0f0f0', color: '#666' };
            return (
              <div
                key={t.id}
                onClick={() => setSelectedThreadId(t.id)}
                style={{
                  padding: '10px 14px', cursor: 'pointer',
                  borderBottom: `1px solid ${colors.borderLight}`,
                  background: isSelected ? colors.selectedBg : 'transparent',
                  transition: 'background 0.1s',
                }}
              >
                {/* User row */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <UserIcon size={12} style={{ color: colors.textMuted, flexShrink: 0 }} />
                  <span style={{ fontSize: '0.72rem', color: colors.textMuted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {t.user_name || 'Unknown'} &middot; {t.user_email || ''}
                  </span>
                </div>
                {/* Title + meta */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                  <span style={{
                    fontSize: '0.82rem', fontWeight: 500, color: colors.textPrimary,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1,
                  }}>
                    {t.title || t.message?.slice(0, 80) || 'Untitled'}
                  </span>
                  <span style={{ fontSize: '0.7rem', color: colors.textMuted, whiteSpace: 'nowrap', flexShrink: 0 }}>
                    {t.created_at ? timeAgo(t.created_at) : ''}
                  </span>
                </div>
                {/* Domain + status badges */}
                <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                  <span style={{
                    fontSize: '0.68rem', fontWeight: 600, textTransform: 'uppercase',
                    color: getDomainColor(t.domain), letterSpacing: '0.03em',
                  }}>
                    {t.domain || 'unknown'}
                  </span>
                  <span style={{
                    fontSize: '0.65rem', padding: '1px 6px', borderRadius: 4,
                    background: statusStyle.bg, color: statusStyle.color, fontWeight: 500,
                  }}>
                    {t.status?.replace(/_/g, ' ') || 'unknown'}
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Pagination */}
        <div style={{
          padding: '8px 14px', borderTop: `1px solid ${colors.border}`,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          fontSize: '0.75rem', color: colors.textMuted,
        }}>
          <span>{total} thread{total !== 1 ? 's' : ''}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page <= 1}
              style={{ background: 'none', border: 'none', cursor: page <= 1 ? 'default' : 'pointer', color: page <= 1 ? colors.borderLight : colors.textSecondary, padding: 4 }}
            >
              <ChevronLeft size={16} />
            </button>
            <span>Page {page} of {totalPages}</span>
            <button
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              style={{ background: 'none', border: 'none', cursor: page >= totalPages ? 'default' : 'pointer', color: page >= totalPages ? colors.borderLight : colors.textSecondary, padding: 4 }}
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      </div>

      {/* Right pane — thread detail */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {!selectedThread && (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: colors.textMuted, fontSize: '0.9rem' }}>
            Select a thread to view details
          </div>
        )}
        {selectedThread && (
          <>
            {/* User banner */}
            <div style={{
              padding: '10px 20px', borderBottom: `1px solid ${colors.borderLight}`,
              display: 'flex', alignItems: 'center', gap: 8, background: '#fafafa',
            }}>
              <UserIcon size={16} style={{ color: colors.textSecondary }} />
              <span style={{ fontSize: '0.85rem', fontWeight: 500, color: colors.textPrimary }}>
                {(selectedThread as unknown as { user_name?: string }).user_name || 'Unknown user'}
              </span>
              <span style={{ fontSize: '0.78rem', color: colors.textMuted }}>
                {(selectedThread as unknown as { user_email?: string }).user_email || ''}
              </span>
            </div>
            {/* Thread detail */}
            <div style={{ flex: 1, overflow: 'auto' }}>
              <ThreadDetail
                thread={selectedThread}
                onAction={noop}
                loading={detailLoading}
                readOnly
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
