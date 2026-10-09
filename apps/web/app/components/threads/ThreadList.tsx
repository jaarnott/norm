'use client';

import { useId, useRef, useState } from 'react';
import type { Thread } from '../../types';
import ThreadCard from './ThreadCard';
import { SquarePen, Search, PanelLeftClose } from 'lucide-react';
import { FUNCTIONAL_PAGES, type FunctionalPageConfig } from '../pages/pageRegistry';
import { threadMembers } from '../../lib/threadApps';
import { memberName } from '../../lib/memberNames';
import { applyStatusFilter, matchesQuery, type FilterKey } from '../../lib/threadFilters';
import Icon from '../ui/Icon';
import IconButton from '../ui/IconButton';

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'awaiting_approval', label: 'Awaiting approval' },
  { key: 'awaiting_user_input', label: 'Needs input' },
  { key: 'completed', label: 'Completed' },
];

interface ThreadListProps {
  threads: Thread[];
  selectedId: string | null;
  onSelectThread: (id: string) => void;
  onRemoveThread: (id: string) => void;
  activeAgent: string;
  // Dynamic page entries (pinned apps) appended after the static list.
  extraPages?: FunctionalPageConfig[];
  filter: FilterKey;
  onFilterChange: (filter: FilterKey) => void;
  onNewChat: () => void;
  onCollapsePanel?: () => void;
  onSelectPage?: (pageId: string) => void;
  /** App slugs that are ON for the org (hierarchy v2). null/undefined = show
   *  everything (gating inactive — the fail-open contract). A page with an
   *  `app` shows iff its App is on; switching one App off removes exactly
   *  its pages. */
  appsOn?: Set<string> | null;
  /** page id -> the member whose menu it belongs in (catalog placement). An
   *  App's pages show only in its member's menu; Norm Core pages (shared)
   *  keep their own per-member placement. */
  pageMember?: Record<string, string>;
  /** The page open in the content area — its menu row shows as current. */
  activePage?: string | null;
  /** The Norm name block at the top (off on mobile, whose top bar shows it). */
  showBrand?: boolean;
}

export default function ThreadList({ threads, selectedId, onSelectThread, onRemoveThread, activeAgent, filter, onFilterChange, onNewChat, onCollapsePanel, onSelectPage, extraPages, appsOn, pageMember, activePage, showBrand = true }: ThreadListProps) {
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState('');
  const searchBtnRef = useRef<HTMLButtonElement>(null);
  const searchId = useId();
  // Closing the box clears it, so no hidden query keeps filtering the list.
  const closeSearch = () => { setSearchOpen(false); setQuery(''); };

  // A member's section shows the threads that used its Apps (or, from before
  // there was one agent, were handled by it).
  const agentFiltered = activeAgent === 'home' ? threads : threads.filter(t => threadMembers(t).includes(activeAgent));
  // Then the status filter, then the search box (titles, first prompts, Apps).
  const searching = searchOpen && query.trim() !== '';
  const filtered = applyStatusFilter(agentFiltered, filter).filter(t => !searching || matchesQuery(t, query));

  const pages = [...FUNCTIONAL_PAGES, ...(extraPages ?? [])]
    .filter(p => (pageMember?.[p.id] ?? p.agent) === activeAgent)
    .filter(p => !p.app || !appsOn || appsOn.has(p.app));

  return (
    <div style={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      backgroundColor: 'var(--canvas)',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{ padding: showBrand ? '16px 16px 12px' : '12px 16px', borderBottom: '1px solid var(--line)' }}>
        {showBrand && (
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
            <div>
              <div style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, lineHeight: 1.2, color: 'var(--text)' }}>Norm</div>
              <div style={{ marginTop: 2, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>AI Operations Control</div>
            </div>
            {onCollapsePanel && <IconButton icon={PanelLeftClose} label="Hide panel" iconSize={16} onClick={onCollapsePanel} />}
          </div>
        )}
        <nav aria-label="Menu" style={{ display: 'flex', flexDirection: 'column', gap: 2, margin: showBrand ? '14px -8px 0' : '0 -8px' }}>
          <button type="button" data-testid="new-chat-btn" className="n-row" onClick={onNewChat}>
            <Icon icon={SquarePen} size="menu" tone="muted" /> New chat
          </button>
          <button
            ref={searchBtnRef}
            type="button"
            data-testid="search-btn"
            className="n-row"
            aria-expanded={searchOpen}
            aria-controls={searchOpen ? searchId : undefined}
            onClick={() => (searchOpen ? closeSearch() : setSearchOpen(true))}
          >
            <Icon icon={Search} size="menu" tone={searchOpen ? 'strong' : 'muted'} /> Search
          </button>
          {searchOpen && (
            <input
              id={searchId}
              type="text"
              role="searchbox"
              className="n-input"
              data-testid="search-input"
              aria-label="Search chats"
              placeholder="Search chats"
              autoFocus
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => {
                if (e.key !== 'Escape') return;
                e.preventDefault();
                closeSearch();
                searchBtnRef.current?.focus();
              }}
              style={{ width: '100%', margin: '2px 0 4px' }}
            />
          )}
          {pages.length > 0 && <div aria-hidden style={{ height: 6 }} />}
          {pages.map(page => {
            const current = activePage === page.id;
            return (
              <button
                key={page.id}
                type="button"
                className="n-row"
                aria-current={current ? 'page' : undefined}
                onClick={() => onSelectPage?.(page.id)}
              >
                <Icon icon={page.icon} size="menu" tone={current ? 'strong' : 'muted'} duo={current} />
                <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{page.label}</span>
              </button>
            );
          })}
        </nav>
        <div style={{ margin: '14px 0 8px', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)' }}>
          {activeAgent === 'home' ? 'Recent threads' : `${memberName(activeAgent)} threads`}
        </div>
        {/* Filters — one row; it scrolls sideways rather than wrap when narrow */}
        <div role="group" aria-label="Filter threads" className="no-scrollbar" style={{ display: 'flex', gap: 4, overflowX: 'auto' }}>
          {FILTERS.map(f => {
            const on = filter === f.key;
            return (
              <button
                key={f.key}
                type="button"
                data-testid={`filter-${f.key}`}
                aria-pressed={on}
                onClick={() => onFilterChange(f.key)}
                style={{
                  flex: '0 0 auto',
                  whiteSpace: 'nowrap',
                  padding: '4px 8px',
                  fontSize: 'var(--fs-xs)',
                  fontWeight: on ? 600 : 500,
                  color: on ? 'var(--text)' : 'var(--text-soft)',
                  backgroundColor: on ? 'var(--selected)' : 'transparent',
                  border: `1px solid ${on ? 'var(--brand-soft)' : 'var(--line)'}`,
                  borderRadius: 999,
                  cursor: 'pointer',
                }}
              >
                {f.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* The thumb shows only while the pointer is over the list (tokens.css). */}
      <div className="scroll-quiet" style={{ flex: 1 }}>
        {filtered.length === 0 ? (
          <div style={{
            padding: '3rem 1.5rem',
            textAlign: 'center',
            color: 'var(--muted)',
            fontSize: 'var(--fs-sm)',
            lineHeight: 1.6,
          }}>
            {searching
              ? 'No matching chats'
              : 'No threads yet. Try asking me to order stock, check a roster, or generate a report.'}
          </div>
        ) : (
          filtered.map(thread => (
            <ThreadCard
              key={thread.id}
              data-testid={`thread-card-${thread.id}`}
              thread={thread}
              isSelected={selectedId === thread.id}
              onClick={() => onSelectThread(thread.id)}
              onRemove={() => onRemoveThread(thread.id)}
              compact={activeAgent === 'home'}
            />
          ))
        )}
      </div>
    </div>
  );
}
