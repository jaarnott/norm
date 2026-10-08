'use client';

import { useState, useEffect, useCallback } from 'react';
import { Search, ChevronLeft, ChevronRight, User as UserIcon } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import { memberName } from '../../lib/memberNames';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import type { AdminThread, Thread } from '../../types';
import ThreadDetail from '../threads/ThreadDetail';
import { CopyableThreadId } from '../threads/ActivityTimeline';
import Avatar from '../ui/Avatar';
import BackLink from '../ui/BackLink';
import Badge, { type BadgeTone } from '../ui/Badge';
import Icon from '../ui/Icon';
import IconButton from '../ui/IconButton';
import PageState from '../ui/PageState';

// Threads since Sep 2026 are all Norm's (no router); the member domains
// filter the threads from before.
const DOMAIN_OPTIONS = [
  { value: '', label: 'All threads' },
  { value: 'norm', label: 'Norm' },
  { value: 'procurement', label: 'Procurement' },
  { value: 'hr', label: 'HR' },
  { value: 'time_attendance', label: 'Time & attendance' },
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

/** Status pill tones (ui/Badge): done → ok, waiting on an approval → warn,
 *  waiting on the person's answer → accent, still running → info. */
const STATUS_TONES: Record<string, BadgeTone> = {
  completed: 'ok',
  in_progress: 'info',
  awaiting_approval: 'warn',
  awaiting_tool_approval: 'warn',
  awaiting_user_input: 'accent',
  needs_clarification: 'accent',
};

/** "awaiting_tool_approval" → "Awaiting tool approval". */
function statusLabel(status: string | undefined): string {
  const words = (status || 'unknown').replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

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

  // Phones show one pane at a time: the list, or the thread picked from it
  // (with a way back). Wider screens show both side by side.
  const { isMobile } = useBreakpoint();
  const hideList = isMobile && !!selectedThreadId;
  const hideDetail = isMobile && !selectedThreadId;

  const field: React.CSSProperties = { flex: 1, minWidth: 0 };

  return (
    <div style={{ display: 'flex', height: '100%', background: 'var(--canvas)' }}>
      {/* Left pane — thread list */}
      <div style={{
        width: isMobile ? '100%' : 380, minWidth: isMobile ? 0 : 320,
        borderRight: isMobile ? 'none' : '1px solid var(--line)',
        display: hideList ? 'none' : 'flex', flexDirection: 'column',
      }}>
        {/* Filters */}
        <div style={{ padding: isMobile ? '12px 16px' : '14px 16px', borderBottom: '1px solid var(--line)', display: 'flex', flexDirection: 'column', gap: 8 }}>
          {/* Search */}
          <div style={{ position: 'relative' }}>
            <Icon icon={Search} size="dense" tone="muted" style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
            <input
              type="text"
              className="n-input"
              placeholder="Search threads..."
              aria-label="Search threads"
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{ width: '100%', paddingLeft: 32 }}
            />
          </div>
          {/* Dropdowns row */}
          <div style={{ display: 'flex', gap: 8 }}>
            <select className="n-select" aria-label="User" value={userFilter} onChange={e => setUserFilter(e.target.value)} style={field}>
              <option value="">All users</option>
              {users.map(u => (
                <option key={u.email} value={u.id || u.email}>{u.full_name || u.email}</option>
              ))}
            </select>
            <select className="n-select" aria-label="Domain" value={domainFilter} onChange={e => setDomainFilter(e.target.value)} style={field}>
              {DOMAIN_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          {/* Status gets the full width: its longest choices don't fit half of it. */}
          <select className="n-select" aria-label="Status" value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={{ width: '100%' }}>
            {STATUS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          {/* Date range */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input type="date" className="n-input" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={field} title="From date" aria-label="From date" />
            <span aria-hidden="true" style={{ flex: '0 0 auto', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>–</span>
            <input type="date" className="n-input" value={dateTo} onChange={e => setDateTo(e.target.value)} style={field} title="To date" aria-label="To date" />
          </div>
        </div>

        {/* Thread list */}
        <div className="scroll-quiet" style={{ flex: 1, overflowY: 'auto' }}>
          {loading && threads.length === 0 && (
            <PageState kind="loading" title="Loading threads…" />
          )}
          {!loading && threads.length === 0 && (
            <PageState kind="empty" title="No threads found" />
          )}
          {threads.map(t => {
            const isSelected = t.id === selectedThreadId;
            return (
              <div
                key={t.id}
                onClick={() => setSelectedThreadId(t.id)}
                className="n-thread-row"
                style={{
                  padding: '12px 16px', cursor: 'pointer',
                  borderBottom: '1px solid var(--line)',
                  // Selected: the selected tile plus the accent bar on the left
                  // edge, as in the thread list beside a conversation.
                  backgroundColor: isSelected ? 'var(--selected)' : undefined,
                  boxShadow: isSelected ? 'inset 3px 0 0 var(--accent)' : undefined,
                }}
              >
                {/* User row */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                  <Icon icon={UserIcon} size="meta" tone="muted" />
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {t.user_name || 'Unknown'} &middot; {t.user_email || ''}
                  </span>
                </div>
                {/* Title + meta */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                  <span style={{
                    fontSize: 'var(--fs-base)', fontWeight: 500, color: 'var(--text)',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, minWidth: 0,
                  }}>
                    {t.title || t.message?.slice(0, 80) || 'Untitled'}
                  </span>
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap', flexShrink: 0 }}>
                    {t.created_at ? timeAgo(t.created_at) : ''}
                  </span>
                </div>
                {/* Thread id (for debugging reference) */}
                <div style={{
                  fontSize: 'var(--fs-xs)', color: 'var(--muted)', fontFamily: 'var(--font-mono)',
                  marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {t.id}
                </div>
                {/* Domain + status badges */}
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                  <Badge>{memberName(t.domain || 'unknown')}</Badge>
                  <Badge tone={STATUS_TONES[t.status] ?? 'neutral'}>{statusLabel(t.status)}</Badge>
                </div>
              </div>
            );
          })}
        </div>

        {/* Pagination */}
        <div style={{
          padding: '6px 8px 6px 16px', borderTop: '1px solid var(--line)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          fontSize: 'var(--fs-xs)', color: 'var(--muted)', fontVariantNumeric: 'tabular-nums',
        }}>
          <span>{total} thread{total !== 1 ? 's' : ''}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <IconButton
              icon={ChevronLeft}
              label="Previous page"
              iconSize={16}
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page <= 1}
            />
            <span>Page {page} of {totalPages}</span>
            <IconButton
              icon={ChevronRight}
              label="Next page"
              iconSize={16}
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
            />
          </div>
        </div>
      </div>

      {/* Right pane — thread detail */}
      <div style={{ flex: 1, minWidth: 0, display: hideDetail ? 'none' : 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {isMobile && (
          <div style={{ padding: '10px 16px 0' }}>
            <BackLink label="Back to threads" onClick={() => setSelectedThreadId(null)} />
          </div>
        )}
        {!selectedThread && (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {detailLoading
              ? <PageState kind="loading" title="Loading the thread…" />
              : <PageState kind="empty" title="Select a thread to view details" />}
          </div>
        )}
        {selectedThread && (
          <>
            {/* User banner */}
            <div style={{
              // 24px: level with ThreadDetail's tab strip underneath.
              padding: isMobile ? '10px 16px' : '12px 24px', borderBottom: '1px solid var(--line)',
              display: 'flex', alignItems: 'center', flexWrap: 'wrap', columnGap: 10, rowGap: 8,
            }}>
              <Avatar
                name={(selectedThread as unknown as { user_name?: string }).user_name || (selectedThread as unknown as { user_email?: string }).user_email || '?'}
                size={28}
              />
              <div style={{ flex: '1 1 auto', minWidth: 0, display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', columnGap: 8 }}>
                <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>
                  {(selectedThread as unknown as { user_name?: string }).user_name || 'Unknown user'}
                </span>
                <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', overflowWrap: 'anywhere' }}>
                  {(selectedThread as unknown as { user_email?: string }).user_email || ''}
                </span>
              </div>
              <span style={{ marginLeft: 'auto', maxWidth: '100%', overflow: 'hidden' }}>
                <CopyableThreadId threadId={selectedThread.id} />
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
