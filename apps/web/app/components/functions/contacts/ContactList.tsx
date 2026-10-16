'use client';

/**
 * Functions contacts: search (name, company, phone, any email), paged, with
 * each contact's enquiry counts and conversion as the API counts them.
 */
import { useEffect, useState } from 'react';
import type { CSSProperties, KeyboardEvent } from 'react';
import { ChevronLeft, ChevronRight, Plus, Search } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import PageState from '../../ui/PageState';
import { fnSend } from '../shared/api';
import type { FnMeta } from '../shared/meta';
import { Field, FnHeader, FormDialog, FormGrid, inputStyle, READ_ONLY_HINT, useFnQuery, useWidth } from '../enquiries/bits';
import { displayName, pctLabel } from '../enquiries/labels';
import type { Contact, Page } from '../enquiries/types';

const PAGE_SIZE = 50;
const CARDS_BELOW = 720;
const ELLIPSIS: CSSProperties = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' };

export default function ContactList({ meta, page, refreshKey, onOpen }: { meta: FnMeta; page: boolean; refreshKey: number; onOpen: (id: string) => void }) {
  const [qInput, setQInput] = useState('');
  const [q, setQ] = useState('');
  const [archived, setArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const [boxRef, width] = useWidth();
  const cards = width > 0 && width < CARDS_BELOW;

  // Search waits for a pause in typing; a new search starts at the first page.
  useEffect(() => {
    if (qInput === q) return;
    const t = setTimeout(() => {
      setQ(qInput);
      setOffset(0);
    }, 300);
    return () => clearTimeout(t);
  }, [qInput, q]);

  const query = useFnQuery<Page<Contact>>('/contacts', { q: q.trim() || undefined, include_archived: archived || undefined, limit: PAGE_SIZE, offset }, refreshKey);
  const { data, error, loading } = query;

  const rows = data?.rows ?? [];
  const onRowKey = (e: KeyboardEvent<HTMLTableRowElement>, id: string) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(id); }
  };

  const header = (
    <FnHeader
      page={page}
      title="Contacts"
      meta="Everyone who has enquired or booked, with what came of it"
      actions={
        <Button variant="primary" icon={Plus} onClick={() => setCreating(true)} disabled={!meta.can_write} title={meta.can_write ? undefined : READ_ONLY_HINT}>
          New contact
        </Button>
      }
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ position: 'relative', flex: '1 1 240px', maxWidth: 380 }}>
          <Icon icon={Search} size={16} tone="muted" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
          <input type="search" className="n-input" aria-label="Search contacts" placeholder="Name, company, phone or email" value={qInput}
            onChange={(e) => setQInput(e.target.value)} style={{ width: '100%', paddingLeft: 32 }} data-testid="fn-contacts-search" />
        </span>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          <input type="checkbox" checked={archived} onChange={(e) => { setArchived(e.target.checked); setOffset(0); }} />
          Include archived
        </label>
      </div>
    </FnHeader>
  );

  const table = (
    <div style={{ overflowX: 'auto' }}>
      <table className="n-table" aria-label="Contacts" style={{ minWidth: 760, tableLayout: 'fixed' }}>
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col" style={{ width: '24%' }}>Email</th>
            <th scope="col" style={{ width: 120 }}>Phone</th>
            <th scope="col" className="num" style={{ width: 84 }}>Enquiries</th>
            <th scope="col" className="num" style={{ width: 60 }}>Open</th>
            <th scope="col" className="num" style={{ width: 96 }}>Won / lost</th>
            <th scope="col" className="num" style={{ width: 64 }}>Events</th>
            <th scope="col" className="num" style={{ width: 96 }}>Conversion</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id} tabIndex={0} onClick={() => onOpen(c.id)} onKeyDown={(e) => onRowKey(e, c.id)} style={{ cursor: 'pointer' }} data-testid="fn-contact-row">
              <td>
                <div style={{ ...ELLIPSIS, fontWeight: 500 }}>
                  {displayName(c.name, c.anonymised_at ? 'Anonymised contact' : 'Unnamed contact')}
                  {c.archived_at && <span style={{ marginLeft: 6 }}><Badge>Archived</Badge></span>}
                </div>
                {c.company && <div style={{ ...ELLIPSIS, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{c.company}</div>}
              </td>
              <td style={{ ...ELLIPSIS, color: 'var(--text-soft)' }} title={c.email ?? undefined}>
                {c.email || '—'}
                {(c.emails?.length ?? 0) > 1 && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}> +{(c.emails?.length ?? 1) - 1}</span>}
              </td>
              <td style={{ ...ELLIPSIS, color: 'var(--text-soft)' }}>{c.phone || '—'}</td>
              <td className="num">{c.counts?.enquiries ?? '—'}</td>
              <td className="num">{c.counts?.open ?? '—'}</td>
              <td className="num">{c.counts ? `${c.counts.confirmed} / ${c.counts.lost}` : '—'}</td>
              <td className="num">{c.counts?.events ?? '—'}</td>
              <td className="num">{pctLabel(c.counts?.conversion_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  const cardList = (
    <ul role="list" aria-label="Contacts" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {rows.map((c) => (
        <li key={c.id} className="n-card">
          <button type="button" onClick={() => onOpen(c.id)} style={cardButton}>
            <span style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <span style={{ fontWeight: 600, minWidth: 0, overflowWrap: 'anywhere' }}>{displayName(c.name, c.anonymised_at ? 'Anonymised contact' : 'Unnamed contact')}</span>
              {c.archived_at && <Badge>Archived</Badge>}
            </span>
            <span style={{ display: 'block', marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', ...ELLIPSIS }}>
              {[c.company, c.email, c.phone].filter(Boolean).join(' · ') || 'No details'}
            </span>
            {c.counts && (
              <span style={{ display: 'block', marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                {`${c.counts.enquiries} ${c.counts.enquiries === 1 ? 'enquiry' : 'enquiries'} · ${c.counts.events} ${c.counts.events === 1 ? 'event' : 'events'} · conversion ${pctLabel(c.counts.conversion_pct)}`}
              </span>
            )}
          </button>
        </li>
      ))}
    </ul>
  );

  let body;
  if (error && !data) {
    body = <PageState kind="error" title="Couldn’t load contacts" detail={error} action={<Button size="sm" onClick={query.retry}>Try again</Button>} />;
  } else if (!data) {
    body = <PageState kind="loading" title="Loading contacts…" />;
  } else if (rows.length === 0 && offset === 0) {
    body = <PageState kind="empty" title={q ? 'No contacts match that search.' : 'No contacts yet.'} />;
  } else {
    body = (
      <div style={{ opacity: loading ? 0.55 : 1, transition: 'opacity 0.15s' }} aria-busy={loading || undefined}>
        {error && <PageState kind="error" title="Couldn’t refresh the list" detail={error} />}
        {cards ? cardList : table}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
          <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{rows.length ? `Showing ${offset + 1}–${offset + rows.length}` : 'No more contacts'}</span>
          <span style={{ display: 'flex', gap: 6 }}>
            <Button size="sm" icon={ChevronLeft} disabled={offset === 0 || loading} onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}>Previous</Button>
            <Button size="sm" disabled={!data.has_more || loading} onClick={() => setOffset((o) => o + PAGE_SIZE)}>
              Next
              <Icon icon={ChevronRight} size={14} />
            </Button>
          </span>
        </div>
      </div>
    );
  }

  return (
    <div ref={boxRef}>
      {header}
      {body}
      {creating && <NewContactDialog onCreated={(c) => { setCreating(false); onOpen(c.id); }} onClose={() => setCreating(false)} />}
    </div>
  );
}

function NewContactDialog({ onCreated, onClose }: { onCreated: (c: Contact) => void; onClose: () => void }) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [company, setCompany] = useState('');
  const [notes, setNotes] = useState('');
  const blank = (v: string) => v.trim() || null;
  return (
    <FormDialog
      title="New contact"
      submitLabel="Create contact"
      submitDisabled={!name.trim()}
      onSubmit={async () => onCreated(await fnSend<Contact>('POST', '/contacts', {
        name: name.trim(), email: blank(email), phone: blank(phone), company: blank(company), notes: blank(notes),
      }))}
      onClose={onClose}
    >
      <FormGrid>
        <Field label="Name">{(id) => <input id={id} className="n-input" style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} autoFocus />}</Field>
        <Field label="Email">{(id) => <input id={id} type="email" className="n-input" style={inputStyle} value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
        <Field label="Phone">{(id) => <input id={id} type="tel" className="n-input" style={inputStyle} value={phone} onChange={(e) => setPhone(e.target.value)} />}</Field>
        <Field label="Company">{(id) => <input id={id} className="n-input" style={inputStyle} value={company} onChange={(e) => setCompany(e.target.value)} />}</Field>
      </FormGrid>
      <Field label="Notes">{(id) => <textarea id={id} className="n-input" rows={3} style={inputStyle} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
    </FormDialog>
  );
}

const cardButton: CSSProperties = {
  display: 'block', width: '100%', padding: '14px 16px', border: 'none', background: 'transparent',
  borderRadius: 'calc(var(--radius-lg) - 1px)', font: 'inherit', fontSize: 'var(--fs-base)',
  color: 'var(--text)', textAlign: 'left', cursor: 'pointer',
};
