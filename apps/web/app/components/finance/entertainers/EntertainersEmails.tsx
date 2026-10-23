'use client';

/**
 * Entertainers — Emails: every email Norm sent to performers, band members,
 * closing managers, venue managers and finance, with delivery status and
 * whether the link inside was opened. A row opens the exact email that
 * went out (a sandboxed frame); resend where the API allows it. Orbit's
 * send dates show as "from Orbit — date only". Venue access opens the
 * page; a money email's body needs `payroll:write`.
 */
import { useCallback, useEffect, useId, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import type { DisplayBlockProps } from '../../display/DisplayBlockRenderer';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import PageHeader from '../../ui/PageHeader';
import PageState from '../../ui/PageState';
import VenueSelect from '../../ui/VenueSelect';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import { ToastHost } from '../shared/Toasts';
import { entGet, errorText } from './api';
import { when } from './format';
import { EMAIL_KIND_LABEL, RECIPIENT_LABEL, emailKindLabel } from './logic';
import { EmailStatusBadge, RowCard } from './ui';
import { readPageParam, setPageParam, useEntMeta } from './useMeta';
import { ModeBanner, OffState } from './EntertainersRegister';
import EmailBodyDialog from './emails/EmailBodyDialog';
import type { EmailLink, Performer, RecipientKind } from './types';

const PAGE = 100;
const KINDS = Object.keys(EMAIL_KIND_LABEL).filter((k) => k.startsWith('ent_'));
const RECIPIENTS: RecipientKind[] = ['performer', 'band_member', 'closing_manager', 'venue_manager', 'finance'];

export default function EntertainersEmails({ props }: DisplayBlockProps) {
  const isPage = !!props?.persistVenue;
  const { meta, error, off, loading, reload } = useEntMeta();
  const { isMobile } = useBreakpoint();
  const ids = { performer: useId(), kind: useId(), recipient: useId(), from: useId(), to: useId() };
  const [performers, setPerformers] = useState<Performer[]>([]);
  const [performerId, setPerformerId] = useState<string>(() => (isPage ? readPageParam('performer') ?? '' : ''));
  const [venueId, setVenueId] = useState<string>('all');
  const [kind, setKind] = useState('');
  const [recipient, setRecipient] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ emails: EmailLink[]; total: number } | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [open, setOpen] = useState<EmailLink | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!meta) return;
    let alive = true;
    entGet<{ performers: Performer[] }>('/performers', { include_inactive: true }).then(
      (res) => { if (alive) setPerformers(res.performers); },
      () => { /* the filter just has no names */ },
    );
    return () => { alive = false; };
  }, [meta]);

  useEffect(() => {
    if (!meta) return;
    let alive = true;
    entGet<{ emails: EmailLink[]; total: number }>('/emails', {
      performer_id: performerId || undefined,
      venue_id: venueId === 'all' ? undefined : venueId,
      kind: kind || undefined,
      recipient: recipient || undefined,
      from: from || undefined,
      to: to || undefined,
      limit: PAGE,
      offset,
    }).then(
      (res) => { if (alive) { setData(res); setListError(null); } },
      (e: unknown) => { if (alive) setListError(errorText(e)); },
    );
    return () => { alive = false; };
  }, [meta, performerId, venueId, kind, recipient, from, to, offset, tick]);

  const refresh = useCallback(() => setTick((t) => t + 1), []);
  const choosePerformer = (id: string) => {
    setPerformerId(id);
    setOffset(0);
    if (isPage) setPageParam('performer', id || null);
  };

  if (off) return <OffState title="Emails" />;
  if (loading) return (<><PageHeader title="Emails" /><PageState kind="loading" title="Loading…" /></>);
  if (!meta) return (<><PageHeader title="Emails" /><PageState kind="error" title="Couldn’t load emails" detail={error ?? undefined} action={<Button size="sm" onClick={reload}>Try again</Button>} /></>);

  const rows = data?.emails ?? [];
  const total = data?.total ?? 0;
  const pageNo = Math.floor(offset / PAGE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <div style={{ width: '100%' }}>
      <PageHeader
        title="Emails"
        meta={data ? `${total} email${total === 1 ? '' : 's'}` : undefined}
        actions={<VenueSelect venues={meta.venues} value={venueId} onChange={(v) => { setVenueId(v); setOffset(0); }} allowAll />}
      >
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <span>
            <label htmlFor={ids.performer} className="n-label">Performer</label>
            <select id={ids.performer} className="n-select" value={performerId} onChange={(e) => choosePerformer(e.target.value)} style={{ maxWidth: 220 }}>
              <option value="">Everyone</option>
              {performers.slice().sort((a, b) => a.name.localeCompare(b.name)).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </span>
          <span>
            <label htmlFor={ids.kind} className="n-label">Kind</label>
            <select id={ids.kind} className="n-select" value={kind} onChange={(e) => { setKind(e.target.value); setOffset(0); }}>
              <option value="">Any</option>
              {KINDS.map((k) => <option key={k} value={k}>{EMAIL_KIND_LABEL[k]}</option>)}
            </select>
          </span>
          <span>
            <label htmlFor={ids.recipient} className="n-label">To</label>
            <select id={ids.recipient} className="n-select" value={recipient} onChange={(e) => { setRecipient(e.target.value); setOffset(0); }}>
              <option value="">Anyone</option>
              {RECIPIENTS.map((r) => <option key={r} value={r}>{RECIPIENT_LABEL[r]}</option>)}
            </select>
          </span>
          <span>
            <label htmlFor={ids.from} className="n-label">From</label>
            <input id={ids.from} className="n-input" type="date" value={from} onChange={(e) => { setFrom(e.target.value); setOffset(0); }} />
          </span>
          <span>
            <label htmlFor={ids.to} className="n-label">To</label>
            <input id={ids.to} className="n-input" type="date" value={to} onChange={(e) => { setTo(e.target.value); setOffset(0); }} />
          </span>
        </div>
      </PageHeader>

      <ModeBanner mode={meta.mode} />

      {listError ? (
        <PageState kind="error" title="Couldn’t load emails" detail={listError} action={<Button size="sm" onClick={refresh}>Try again</Button>} />
      ) : data === null ? (
        <PageState kind="loading" title="Loading emails…" />
      ) : rows.length === 0 ? (
        <PageState kind="empty" title="No emails match" detail="Every email Norm sends about performers lands here, with Orbit's send dates going back to May 2026." />
      ) : isMobile ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {rows.map((m) => (
            <RowCard key={m.id} onClick={() => setOpen(m)} ariaLabel={`Open email ${m.subject ?? emailKindLabel(m.kind)}`}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 600, flex: '1 1 auto' }}>{emailKindLabel(m.kind)}</span>
                {m.source === 'orbit' ? <Badge tone="neutral">From Orbit — date only</Badge> : <EmailStatusBadge status={m.status} />}
              </div>
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginTop: 2, overflowWrap: 'anywhere' }}>{when(m.sent_at)} · {m.recipient_email ?? RECIPIENT_LABEL[m.recipient_kind]}</div>
              {m.subject && <div style={{ fontSize: 'var(--fs-sm)', marginTop: 2 }}>{m.subject}</div>}
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 4 }}>{m.performer_name ?? ''}{m.venue_name ? ` · ${m.venue_name}` : ''}{m.link_opened_at ? ' · link opened' : ''}</div>
            </RowCard>
          ))}
        </div>
      ) : (
        <div className="n-card" style={{ overflowX: 'auto' }}>
          <table className="n-table" style={{ minWidth: 900 }}>
            <thead>
              <tr><th>When</th><th>To</th><th>Kind</th><th>Subject</th><th>Status</th><th>Link opened</th></tr>
            </thead>
            <tbody>
              {rows.map((m) => (
                <tr key={m.id} onClick={() => setOpen(m)} style={{ cursor: 'pointer' }}>
                  <td style={{ whiteSpace: 'nowrap' }}>{when(m.sent_at)}</td>
                  <td>
                    <div style={{ overflowWrap: 'anywhere' }}>{m.recipient_email ?? '—'}</div>
                    <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{RECIPIENT_LABEL[m.recipient_kind] ?? m.recipient_kind}{m.performer_name ? ` · ${m.performer_name}` : ''}{m.venue_name ? ` · ${m.venue_name}` : ''}</div>
                  </td>
                  <td>{emailKindLabel(m.kind)}{m.is_money && <Badge tone="info">money</Badge>}</td>
                  <td style={{ color: 'var(--text-soft)' }}>{m.subject || '—'}</td>
                  <td>{m.source === 'orbit' ? <Badge tone="neutral">From Orbit — date only</Badge> : <EmailStatusBadge status={m.status} />}{m.shadow_redirected && <Badge tone="warn">shadow</Badge>}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>{m.link_opened_at ? when(m.link_opened_at) : <span style={{ color: 'var(--muted)' }}>—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pages > 1 && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 8, marginTop: 10, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          <Button size="sm" icon={ChevronLeft} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Newer</Button>
          <span>Page {pageNo} of {pages}</span>
          <Button size="sm" icon={ChevronRight} disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Older</Button>
        </div>
      )}

      {open && <EmailBodyDialog link={open} onClose={() => setOpen(null)} onResent={refresh} />}
      {isPage && <ToastHost />}
    </div>
  );
}
