'use client';

/**
 * The thread list: folder tabs, linked / unread filters, search in Norm's
 * mirror (the last 90 days) with a live Gmail search for older mail, and
 * Gmail-like rows — unread, sender, folder and draft chips, subject and
 * snippet, the linked enquiry or event, the date.
 */
import { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Link2, Search } from 'lucide-react';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import PageState from '../../ui/PageState';
import Tabs from '../../ui/Tabs';
import { errorText, MailboxNotice, Notice } from './bits';
import { inboxApi } from './inboxApi';
import { decodeEntities, FOLDER_LABELS, mailboxReady, mailDateLabel, senderLabel } from './text';
import type { Folder, LiveSearchRow, Mailbox, ThreadRow, ThreadsResponse } from './types';

export const PAGE_SIZE = 50;

export interface ListFilters {
  folder: 'all' | Folder | 'unfiled';
  linked: 'any' | 'yes' | 'no';
  unread: boolean;
  q: string;
  offset: number;
}

export const DEFAULT_FILTERS: ListFilters = { folder: 'all', linked: 'any', unread: false, q: '', offset: 0 };

const FOLDER_TABS = [
  { id: 'all', label: 'All' },
  { id: 'enquiry', label: 'Enquiry' },
  { id: 'reservation', label: 'Reservation' },
  { id: 'other', label: 'Other' },
  { id: 'unfiled', label: 'Unfiled' },
];

export default function ThreadList({
  venueId,
  mailboxes,
  filters,
  onFilters,
  onOpen,
  timeZone,
  now,
  refreshKey,
}: {
  venueId: string;
  mailboxes: Mailbox[];
  filters: ListFilters;
  onFilters: (f: ListFilters) => void;
  onOpen: (accountId: string, threadId: string, row?: ThreadRow | LiveSearchRow) => void;
  timeZone: string;
  now: Date;
  refreshKey: number;
}) {
  const { isMobile } = useBreakpoint();
  const [text, setText] = useState(filters.q);
  const [result, setResult] = useState<{ key: string; data?: ThreadsResponse; error?: string } | null>(null);
  const [live, setLive] = useState<{ q: string; accountId: string; rows?: LiveSearchRow[]; error?: unknown } | null>(null);
  const [liveAccount, setLiveAccount] = useState<string | null>(null);

  // Typing settles for a moment before the list reloads.
  useEffect(() => {
    if (text === filters.q) return;
    const t = setTimeout(() => onFilters({ ...filters, q: text, offset: 0 }), 350);
    return () => clearTimeout(t);
  }, [text, filters, onFilters]);

  const queryKey = JSON.stringify({
    venue_id: venueId === 'all' ? undefined : venueId,
    folder: filters.folder === 'all' ? undefined : filters.folder,
    linked: filters.linked === 'any' ? undefined : filters.linked === 'yes',
    unread: filters.unread || undefined,
    q: filters.q.trim() || undefined,
    limit: PAGE_SIZE,
    offset: filters.offset,
    refresh: refreshKey,
  });

  useEffect(() => {
    let alive = true;
    const { refresh, ...query } = JSON.parse(queryKey);
    void refresh;
    inboxApi
      .threads(query)
      .then((data) => alive && setResult({ key: queryKey, data }))
      .catch((err) => alive && setResult({ key: queryKey, error: errorText(err) }));
    return () => {
      alive = false;
    };
  }, [queryKey]);

  const loading = result?.key !== queryKey;
  const data = result?.data;
  const set = (patch: Partial<ListFilters>) => onFilters({ ...filters, offset: 0, ...patch });

  // Gmail search runs on one mailbox: the venue's, or the one picked.
  const searchable = useMemo(
    () => (venueId === 'all' ? mailboxes : mailboxes.filter((m) => m.venue_id === venueId)),
    [mailboxes, venueId],
  );
  const liveMailbox =
    searchable.find((m) => m.id === liveAccount) ?? searchable.find((m) => mailboxReady(m.health)) ?? searchable[0] ?? null;
  const runLive = () => {
    const q = filters.q.trim() || text.trim();
    if (!liveMailbox || !q) return;
    const accountId = liveMailbox.id;
    setLive({ q, accountId });
    inboxApi
      .searchGmail(accountId, q)
      .then((r) => setLive({ q, accountId, rows: r.threads }))
      .catch((error) => setLive({ q, accountId, error }));
  };

  const rows = data?.threads ?? [];
  const from = filters.offset + 1;
  const to = filters.offset + rows.length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Tabs label="Folders" items={FOLDER_TABS} value={filters.folder} onChange={(id) => set({ folder: id as ListFilters['folder'] })} />

      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
        <span style={{ position: 'relative', flex: '1 1 260px', minWidth: 0, display: 'flex' }}>
          <Icon
            icon={Search}
            size={16}
            tone="muted"
            style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }}
          />
          <input
            className="n-input"
            type="search"
            aria-label="Search conversations"
            placeholder="Search subject, sender or text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            style={{ width: '100%', paddingLeft: 32 }}
          />
        </span>
        <select
          className="n-select"
          aria-label="Linked to a record"
          value={filters.linked}
          onChange={(e) => set({ linked: e.target.value as ListFilters['linked'] })}
        >
          <option value="any">Linked or not</option>
          <option value="yes">Linked</option>
          <option value="no">Not linked</option>
        </select>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={filters.unread}
            onChange={(e) => set({ unread: e.target.checked })}
            style={{ margin: 0, accentColor: 'var(--accent)' }}
          />
          Unread only
        </label>
      </div>

      {(filters.q.trim() || text.trim()) && searchable.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          <span>Older than 90 days?</span>
          <Button size="sm" variant="secondary" icon={Search} onClick={runLive} disabled={!liveMailbox || !mailboxReady(liveMailbox.health)}>
            Search older mail in Gmail
          </Button>
          {liveMailbox && !mailboxReady(liveMailbox.health) && <span style={{ color: 'var(--muted)' }}>Needs a connected mailbox.</span>}
          {searchable.length > 1 && (
            <select
              className="n-select"
              aria-label="Mailbox to search"
              value={liveMailbox?.id ?? ''}
              onChange={(e) => setLiveAccount(e.target.value)}
              style={{ height: 30, fontSize: 'var(--fs-sm)', maxWidth: '100%' }}
            >
              {searchable.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.venue_name} — {m.google_email}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {live && (
        <LiveResults live={live} mailbox={mailboxes.find((m) => m.id === live.accountId) ?? null} onOpen={onOpen} onClose={() => setLive(null)} timeZone={timeZone} now={now} />
      )}

      {result?.error && result.key === queryKey ? (
        <PageState kind="error" title="Couldn’t load the inbox" detail={result.error} />
      ) : !data && loading ? (
        <PageState kind="loading" title="Loading conversations…" />
      ) : rows.length === 0 ? (
        <PageState
          kind="empty"
          title={filters.q.trim() ? 'No conversations match' : 'No conversations here'}
          detail={filters.q.trim() ? 'Norm keeps the last 90 days — search older mail in Gmail above.' : undefined}
        />
      ) : (
        <ul
          className="n-card"
          aria-label="Conversations"
          aria-busy={loading || undefined}
          style={{ listStyle: 'none', margin: 0, padding: 0, overflow: 'hidden', opacity: loading ? 0.6 : 1 }}
        >
          {rows.map((row, i) => (
            <ThreadRowItem
              key={`${row.account_id}:${row.gmail_thread_id}`}
              row={row}
              first={i === 0}
              showVenue={venueId === 'all'}
              compact={isMobile}
              timeZone={timeZone}
              now={now}
              onOpen={() => onOpen(row.account_id, row.gmail_thread_id, row)}
            />
          ))}
        </ul>
      )}

      {(filters.offset > 0 || data?.has_more) && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 8, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
          <span>
            {from}–{to}
          </span>
          <Button size="sm" icon={ChevronLeft} disabled={filters.offset === 0 || loading} onClick={() => onFilters({ ...filters, offset: Math.max(0, filters.offset - PAGE_SIZE) })}>
            Newer
          </Button>
          <Button size="sm" disabled={!data?.has_more || loading} onClick={() => onFilters({ ...filters, offset: filters.offset + PAGE_SIZE })}>
            Older
            <Icon icon={ChevronRight} size={14} />
          </Button>
        </div>
      )}
    </div>
  );
}

function ThreadRowItem({
  row,
  first,
  showVenue,
  compact,
  timeZone,
  now,
  onOpen,
}: {
  row: ThreadRow;
  first: boolean;
  showVenue: boolean;
  compact: boolean;
  timeZone: string;
  now: Date;
  onOpen: () => void;
}) {
  const unread = row.unread_count > 0;
  const link = row.links.find((l) => l.visible);
  const sender = senderLabel(decodeEntities(row.last_from_name), row.last_from_email);
  const chips = (
    <>
      {row.folder && <Badge>{FOLDER_LABELS[row.folder]}</Badge>}
      {row.has_norm_draft && <Badge tone="info">Draft</Badge>}
      {row.awaiting_reply && <Badge tone="accent">Needs reply</Badge>}
    </>
  );
  const linkBadge = link ? (
    <span
      title={link.event_id ? 'Linked event' : 'Linked enquiry'}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 4, minWidth: 0, maxWidth: '100%', fontSize: 'var(--fs-xs)', color: 'var(--accent)' }}
    >
      <Icon icon={Link2} size={12} />
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{link.label || link.contact_name || 'Linked'}</span>
    </span>
  ) : row.linked ? (
    <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Linked</span>
  ) : null;
  const date = (
    <span style={{ flex: '0 0 auto', fontSize: 'var(--fs-xs)', color: unread ? 'var(--text)' : 'var(--muted)', fontWeight: unread ? 600 : 400, whiteSpace: 'nowrap' }}>
      {mailDateLabel(row.last_message_at, timeZone, now)}
    </span>
  );
  const subjectLine = (
    <span style={{ display: 'block', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
      <span style={{ color: 'var(--text)', fontWeight: unread ? 600 : 400 }}>{decodeEntities(row.subject) || '(no subject)'}</span>
      {row.snippet && <span style={{ color: 'var(--muted)' }}> — {decodeEntities(row.snippet)}</span>}
    </span>
  );
  const senderEl = (
    <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
      <span
        aria-hidden="true"
        style={{ flex: '0 0 8px', width: 8, height: 8, borderRadius: '50%', background: unread ? 'var(--accent)' : 'transparent' }}
      />
      <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: unread ? 600 : 500, color: 'var(--text)' }}>
        {sender}
      </span>
      {row.message_count > 1 && <span style={{ flex: '0 0 auto', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{row.message_count}</span>}
    </span>
  );

  return (
    <li style={{ borderTop: first ? 'none' : '1px solid var(--line)' }}>
      <button
        type="button"
        onClick={onOpen}
        aria-label={`${unread ? 'Unread. ' : ''}${sender}: ${decodeEntities(row.subject) || 'no subject'}`}
        className="n-row"
        style={{
          display: 'block',
          borderRadius: 0,
          padding: compact ? '10px 12px' : '9px 14px',
          fontWeight: 400,
        }}
      >
        {compact ? (
          <span style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ flex: 1, minWidth: 0 }}>{senderEl}</span>
              {date}
            </span>
            <span style={{ paddingLeft: 14 }}>{subjectLine}</span>
            <span style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, paddingLeft: 14 }}>
              {chips}
              {linkBadge}
              {showVenue && row.venue_name && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{row.venue_name}</span>}
            </span>
          </span>
        ) : (
          <span style={{ display: 'grid', gridTemplateColumns: '200px minmax(0, 1fr) auto', alignItems: 'center', columnGap: 14, rowGap: 2 }}>
            {senderEl}
            <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
              {chips}
              <span style={{ flex: 1, minWidth: 0 }}>{subjectLine}</span>
            </span>
            {date}
            {(linkBadge || showVenue) && (
              <span style={{ gridColumn: '2 / 4', display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                {linkBadge}
                {showVenue && row.venue_name && <span style={{ whiteSpace: 'nowrap' }}>{row.venue_name}</span>}
              </span>
            )}
          </span>
        )}
      </button>
    </li>
  );
}

function LiveResults({
  live,
  mailbox,
  onOpen,
  onClose,
  timeZone,
  now,
}: {
  live: { q: string; accountId: string; rows?: LiveSearchRow[]; error?: unknown };
  mailbox: Mailbox | null;
  onOpen: (accountId: string, threadId: string, row?: LiveSearchRow) => void;
  onClose: () => void;
  timeZone: string;
  now: Date;
}) {
  const heading = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
      <span className="n-eyebrow">Older mail in Gmail</span>
      <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        “{live.q}” in {mailbox?.google_email}
      </span>
      <button type="button" className="n-btn n-btn--link" onClick={onClose} style={{ marginLeft: 'auto', fontSize: 'var(--fs-sm)' }}>
        Close
      </button>
    </div>
  );
  if (live.error) {
    const code = (live.error as { code?: string }).code;
    return (
      <div>
        {heading}
        {code === 'mailbox_unavailable' ? <MailboxNotice message={errorText(live.error)} /> : <Notice tone="error">{errorText(live.error)}</Notice>}
      </div>
    );
  }
  if (!live.rows) {
    return (
      <div>
        {heading}
        <PageState kind="loading" title="Searching Gmail…" />
      </div>
    );
  }
  return (
    <div>
      {heading}
      {live.rows.length === 0 ? (
        <PageState kind="empty" title="Nothing found in Gmail" />
      ) : (
        <ul className="n-card" style={{ listStyle: 'none', margin: 0, padding: 0, overflow: 'hidden' }}>
          {live.rows.map((r, i) => (
            <li key={r.gmail_thread_id} style={{ borderTop: i ? '1px solid var(--line)' : 'none' }}>
              <button type="button" className="n-row" onClick={() => onOpen(r.account_id, r.gmail_thread_id, r)} style={{ display: 'block', borderRadius: 0, padding: '9px 14px', fontWeight: 400 }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 500 }}>
                    {senderLabel(decodeEntities(r.last_from_name), r.last_from_email)}
                    {r.message_count > 1 && <span style={{ marginLeft: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{r.message_count}</span>}
                  </span>
                  {!r.mirrored && <Badge>Older mail</Badge>}
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>{mailDateLabel(r.last_message_at, timeZone, now)}</span>
                </span>
                <span style={{ display: 'block', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {decodeEntities(r.subject) || '(no subject)'}
                  {r.snippet && <span style={{ color: 'var(--muted)' }}> — {decodeEntities(r.snippet)}</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
