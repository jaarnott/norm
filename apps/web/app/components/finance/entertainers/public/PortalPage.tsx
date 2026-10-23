'use client';

/**
 * /p/<token> — a performer's portal. The render contract (plan §11.4):
 * venue, date, start/end and status for upcoming and recent performances;
 * Confirm / My times differed / I didn't perform; invoice upload. No
 * amounts, rates, bank, IRD or contact details, ever — a forwarded email
 * must not price a gig. A dead, moved or not-yet-open link is one page.
 */
import { useEffect, useId, useState } from 'react';
import type { FormEvent } from 'react';
import { CircleCheck, Upload } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import Icon from '../../../ui/Icon';
import { dateLabel, timeRangeLabel } from '../format';
import { PORTAL_STATUS_LABEL, PORTAL_STATUS_TONE, timeToMinutes } from '../logic';
import PublicFrame, { Card, ErrorLine, LinkGone, LoadFailed, PublicLoading } from './PublicFrame';
import { MAX_UPLOAD_BYTES, PublicApiError, UPLOAD_ACCEPT, friendlyError, portalPath, publicGet, publicSend, publicUpload } from './publicApi';
import type { PortalPage as Payload, PortalRow } from '../types';

type View = { kind: 'loading' } | { kind: 'gone' } | { kind: 'error'; message: string } | { kind: 'page'; data: Payload };

function Row({ row, token, onUpdated }: { row: PortalRow; token: string; onUpdated: (p: Payload) => void }) {
  const ids = { start: useId(), end: useId(), next: useId(), reason: useId() };
  const [mode, setMode] = useState<'idle' | 'times' | 'dnp'>('idle');
  const [start, setStart] = useState(row.your_times?.start ?? row.start_time);
  const [end, setEnd] = useState(row.your_times?.end ?? row.end_time);
  const [nextDay, setNextDay] = useState(row.your_times?.ends_next_day ?? row.ends_next_day);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = async (body: { actual_start_time: string; actual_end_time: string; actual_ends_next_day: boolean } | { reason?: string }, path: 'confirm' | 'did-not-perform') => {
    setBusy(true);
    setError(null);
    try {
      onUpdated(await publicSend<Payload>('POST', `${portalPath(token)}/performances/${row.id}/${path}`, body));
      setMode('idle');
    } catch (e) {
      setError(friendlyError(e));
    }
    setBusy(false);
  };

  const confirmAsBooked = () => send({ actual_start_time: row.start_time, actual_end_time: row.end_time, actual_ends_next_day: row.ends_next_day }, 'confirm');
  const confirmTimes = (e: FormEvent) => {
    e.preventDefault();
    if (timeToMinutes(start) === null || timeToMinutes(end) === null) return setError('Enter the time you started and the time you finished.');
    return send({ actual_start_time: start, actual_end_time: end, actual_ends_next_day: nextDay }, 'confirm');
  };
  const dnp = (e: FormEvent) => {
    e.preventDefault();
    return send({ reason: reason.trim() || undefined }, 'did-not-perform');
  };

  const highlight = row.can_confirm || row.status === 'times_differ';
  return (
    <Card tone={row.status === 'times_differ' ? 'warn' : highlight ? 'accent' : undefined}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 200px', minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 'var(--fs-md)' }}>{dateLabel(row.performance_date)}</div>
          <div style={{ color: 'var(--text-soft)', fontSize: 'var(--fs-base)' }}>{row.venue_name} · {timeRangeLabel(row.start_time, row.end_time, row.ends_next_day)}</div>
        </div>
        <Badge tone={PORTAL_STATUS_TONE[row.status]}>{PORTAL_STATUS_LABEL[row.status]}</Badge>
      </div>
      {row.your_times && (
        <div style={{ marginTop: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          You said {timeRangeLabel(row.your_times.start, row.your_times.end, row.your_times.ends_next_day)}{row.confirmed_at ? ` (${dateLabel(row.confirmed_at.slice(0, 10), { year: false })})` : ''}.
        </div>
      )}
      {row.status === 'times_differ' && (
        <div style={{ marginTop: 8, fontSize: 'var(--fs-sm)', color: 'var(--warn)' }}>
          The venue recorded different times. If yours were wrong, send them again below; if you think the venue has it wrong, reply to the venue&rsquo;s email.
        </div>
      )}
      {row.invoice.uploaded && (
        <div style={{ marginTop: 6, fontSize: 'var(--fs-sm)', color: 'var(--ok)', display: 'flex', gap: 4, alignItems: 'center' }}>
          <Icon icon={CircleCheck} size={14} /> Invoice received{row.invoice.number ? ` (${row.invoice.number})` : ''}
        </div>
      )}

      {row.can_confirm && mode === 'idle' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
          <Button variant="primary" className="ep-btn-full" onClick={confirmAsBooked} disabled={busy}>{busy ? 'Sending…' : 'Confirm these times'}</Button>
          <div className="ep-two">
            <Button className="ep-btn-full" onClick={() => setMode('times')} disabled={busy}>My times were different</Button>
            <Button className="ep-btn-full" onClick={() => setMode('dnp')} disabled={busy}>I didn&rsquo;t perform</Button>
          </div>
        </div>
      )}
      {mode === 'times' && (
        <form onSubmit={confirmTimes} noValidate style={{ marginTop: 12 }}>
          <div className="ep-two">
            <div>
              <label htmlFor={ids.start} className="n-label">I started at</label>
              <input id={ids.start} className="n-input ep-input" type="time" value={start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div>
              <label htmlFor={ids.end} className="n-label">I finished at</label>
              <input id={ids.end} className="n-input ep-input" type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
            </div>
          </div>
          <label htmlFor={ids.next} className="ep-check" style={{ marginTop: 10 }}>
            <input id={ids.next} type="checkbox" checked={nextDay} onChange={(e) => setNextDay(e.target.checked)} />
            <span>I finished after midnight (the next day)</span>
          </label>
          {error && <ErrorLine>{error}</ErrorLine>}
          <div className="ep-two" style={{ marginTop: 12 }}>
            <Button type="button" className="ep-btn-full" onClick={() => { setMode('idle'); setError(null); }} disabled={busy}>Back</Button>
            <Button type="submit" variant="primary" className="ep-btn-full" disabled={busy}>{busy ? 'Sending…' : 'Send my times'}</Button>
          </div>
        </form>
      )}
      {mode === 'dnp' && (
        <form onSubmit={dnp} noValidate style={{ marginTop: 12 }}>
          <label htmlFor={ids.reason} className="n-label">What happened? (optional)</label>
          <textarea id={ids.reason} className="n-input" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Cancelled by the venue, I was unwell…" style={{ width: '100%', fontSize: 'var(--fs-md)' }} />
          {error && <ErrorLine>{error}</ErrorLine>}
          <div className="ep-two" style={{ marginTop: 12 }}>
            <Button type="button" className="ep-btn-full" onClick={() => { setMode('idle'); setError(null); }} disabled={busy}>Back</Button>
            <Button type="submit" variant="primary" className="ep-btn-full" disabled={busy}>{busy ? 'Sending…' : 'I didn’t perform'}</Button>
          </div>
        </form>
      )}
      {mode === 'idle' && error && <ErrorLine>{error}</ErrorLine>}
    </Card>
  );
}

function InvoiceUpload({ data, token, onUpdated }: { data: Payload; token: string; onUpdated: (p: Payload) => void }) {
  const ids = { file: useId(), number: useId(), amount: useId() };
  const candidates = data.recent.filter((r) => r.status !== 'scheduled' && r.status !== 'did_not_perform' && r.status !== 'paid');
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [file, setFile] = useState<File | null>(null);
  const [number, setNumber] = useState('');
  const [amount, setAmount] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) return setError('Choose the invoice file — a PDF or a photo.');
    if (file.size > MAX_UPLOAD_BYTES) return setError('The file must be 10 MB or smaller.');
    if (chosen.size === 0) return setError('Tick the performance(s) this invoice covers.');
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('performance_ids', JSON.stringify(Array.from(chosen)));
      if (number.trim()) form.append('invoice_number', number.trim());
      if (amount.trim()) form.append('invoice_amount', amount.trim());
      onUpdated(await publicUpload<Payload>(`${portalPath(token)}/invoices`, form));
      setChosen(new Set());
      setFile(null);
      setNumber('');
      setAmount('');
      setDone(true);
    } catch (err) {
      setError(friendlyError(err));
    }
    setBusy(false);
  };

  return (
    <Card>
      <h2 style={{ margin: '0 0 4px', fontSize: 'var(--fs-lg)', fontWeight: 600 }}>Upload an invoice</h2>
      <p style={{ margin: '0 0 12px', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        One invoice can cover several performances. The finance team checks it against what was approved and emails you if anything doesn&rsquo;t match.
      </p>
      {done && <div style={{ marginBottom: 10, fontSize: 'var(--fs-sm)', color: 'var(--ok)', display: 'flex', gap: 4, alignItems: 'center' }}><Icon icon={CircleCheck} size={14} /> Thanks — your invoice is in.</div>}
      {!data.can_upload_invoice ? (
        <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{data.uploads_left_today === 0 ? 'You’ve reached today’s upload limit. Try again tomorrow.' : 'There’s nothing to invoice right now.'}</p>
      ) : candidates.length === 0 ? (
        <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Nothing to invoice yet — performances appear here once the venue has approved them.</p>
      ) : (
        <form onSubmit={submit} noValidate>
          <div className="n-label">This invoice covers</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {candidates.map((r) => (
              <label key={r.id} className="ep-check">
                <input type="checkbox" checked={chosen.has(r.id)} onChange={(e) => setChosen((s) => { const n = new Set(s); if (e.target.checked) n.add(r.id); else n.delete(r.id); return n; })} />
                <span>
                  {dateLabel(r.performance_date, { year: false })} · {r.venue_name}
                  {r.invoice.uploaded && <span style={{ color: 'var(--muted)' }}> (already invoiced)</span>}
                </span>
              </label>
            ))}
          </div>
          <div style={{ marginTop: 12 }}>
            <label htmlFor={ids.file} className="n-label">Invoice file (PDF or photo, up to 10 MB)</label>
            <input id={ids.file} type="file" accept={UPLOAD_ACCEPT} onChange={(e) => setFile(e.target.files?.[0] ?? null)} style={{ fontSize: 'var(--fs-base)', width: '100%' }} />
          </div>
          <div className="ep-two" style={{ marginTop: 12 }}>
            <div>
              <label htmlFor={ids.number} className="n-label">Invoice number (optional)</label>
              <input id={ids.number} className="n-input ep-input" value={number} onChange={(e) => setNumber(e.target.value)} autoComplete="off" />
            </div>
            <div>
              <label htmlFor={ids.amount} className="n-label">Invoice total (optional)</label>
              <input id={ids.amount} className="n-input ep-input" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} autoComplete="off" placeholder="Dollars" />
            </div>
          </div>
          {error && <ErrorLine>{error}</ErrorLine>}
          <Button type="submit" variant="primary" className="ep-btn-full" icon={Upload} disabled={busy} style={{ marginTop: 14 }}>{busy ? 'Uploading…' : 'Upload invoice'}</Button>
          <p style={{ margin: '8px 0 0', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{data.uploads_left_today} upload{data.uploads_left_today === 1 ? '' : 's'} left today.</p>
        </form>
      )}
    </Card>
  );
}

export default function PortalPage({ token }: { token: string }) {
  const [view, setView] = useState<View>({ kind: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    publicGet<Payload>(portalPath(token)).then(
      (data) => { if (live) setView({ kind: 'page', data }); },
      (err) => { if (live) setView(err instanceof PublicApiError && err.notFound ? { kind: 'gone' } : { kind: 'error', message: friendlyError(err) }); },
    );
    return () => { live = false; };
  }, [token, attempt]);

  if (view.kind === 'loading') return <PublicLoading label="Loading your performances…" />;
  if (view.kind === 'gone') return <LinkGone />;
  if (view.kind === 'error') return <LoadFailed message={view.message} onRetry={() => { setView({ kind: 'loading' }); setAttempt((n) => n + 1); }} />;

  const data = view.data;
  const toConfirm = [...data.recent, ...data.upcoming].filter((r) => r.can_confirm || r.status === 'times_differ');
  const upcoming = data.upcoming.filter((r) => !toConfirm.includes(r));
  const recent = data.recent.filter((r) => !toConfirm.includes(r));
  const update = (p: Payload) => setView({ kind: 'page', data: p });

  return (
    <PublicFrame>
      <h1 style={{ margin: '4px 0 2px', fontSize: 'var(--fs-xl)', fontWeight: 700 }}>Hi {data.performer_name.split(' ')[0] || data.performer_name}</h1>
      <p style={{ margin: '0 0 16px', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>Your performances, and anything we need you to confirm.</p>

      <h2 style={{ margin: '0 0 8px', fontSize: 'var(--fs-md)', fontWeight: 600 }}>To confirm</h2>
      {toConfirm.length === 0 ? (
        <Card><div style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--ok)', fontSize: 'var(--fs-base)' }}><Icon icon={CircleCheck} size={16} /> You&rsquo;re all caught up — nothing to confirm right now.</div></Card>
      ) : toConfirm.map((r) => <Row key={r.id} row={r} token={token} onUpdated={update} />)}

      {data.lane_needs_invoice && <InvoiceUpload data={data} token={token} onUpdated={update} />}

      <h2 style={{ margin: '16px 0 8px', fontSize: 'var(--fs-md)', fontWeight: 600 }}>Upcoming</h2>
      {upcoming.length === 0 ? (
        <Card><div style={{ color: 'var(--muted)', fontSize: 'var(--fs-base)' }}>Nothing booked ahead yet.</div></Card>
      ) : upcoming.map((r) => <Row key={r.id} row={r} token={token} onUpdated={update} />)}

      <h2 style={{ margin: '16px 0 8px', fontSize: 'var(--fs-md)', fontWeight: 600 }}>Recent</h2>
      {recent.length === 0 ? (
        <Card><div style={{ color: 'var(--muted)', fontSize: 'var(--fs-base)' }}>No recent performances.</div></Card>
      ) : recent.map((r) => <Row key={r.id} row={r} token={token} onUpdated={update} />)}

      <p style={{ margin: '20px 0 0', fontSize: 'var(--fs-xs)', color: 'var(--muted)', textAlign: 'center' }}>
        This link is personal — please don&rsquo;t forward it. Payment details are emailed to you separately and never shown here.
      </p>
    </PublicFrame>
  );
}
