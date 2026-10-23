'use client';

/**
 * One approved performance on Payments: the invoice and what the check
 * found (query → send the query email or waive with a note; "check now"
 * after a failed run), the Xero bill through preview → confirm (the card
 * first, the invoice attached), mark paid with a reference and undo, and
 * each band member's share with its own payment and bill.
 */
import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { FileText, RefreshCw, Send, Undo2 } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import Dialog, { ConfirmDialog } from '../../../ui/Dialog';
import Icon from '../../../ui/Icon';
import { EntApiError, entSend, errorText, isStale, openEntFile } from '../api';
import { dateLabel, money, timeRangeLabel, when } from '../format';
import { hoursLabel } from '../logic';
import { CheckChip, DialogActions, Fact, FactGrid, Field, Hint, LaneBadge, Notice, Section, StatusChip, WriteError, inputStyle } from '../ui';
import PreviewCard from './PreviewCard';
import type { PreviewCardData } from './PreviewCard';
import type { BillPreview, EntFile, EntMeta, PaymentRow, Performance, Push, Share } from '../types';

const FINDING_LABEL: Record<string, string> = {
  total: 'Total',
  gst: 'GST',
  duplicate_number: 'Invoice number used before',
  payee: 'Payee name',
  dates: 'Dates',
  unreadable: 'Unreadable',
};

function BillState({ bill }: { bill: Push | null }) {
  if (!bill) return <span style={{ color: 'var(--muted)' }}>Not pushed</span>;
  if (bill.voided_at) return <Badge tone="neutral">Voided {when(bill.voided_at)}</Badge>;
  return (
    <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
      <Badge tone={bill.xero_status === 'PAID' ? 'ok' : bill.xero_status === 'VOIDED' || bill.xero_status === 'DELETED' ? 'neutral' : 'info'}>{bill.xero_status ?? 'DRAFT'}</Badge>
      <span style={{ fontSize: 'var(--fs-sm)' }}>{bill.xero_number ?? bill.xero_id}</span>
      <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>pushed {when(bill.pushed_at)}</span>
    </span>
  );
}

export default function PaymentDialog({ row, meta, onChanged, onClose }: {
  row: PaymentRow;
  meta: EntMeta;
  onChanged: (performance: Performance) => void;
  onClose: () => void;
}) {
  const base = useId();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<{ text: string; stale: boolean } | null>(null);
  const [notice, setNotice] = useState<ReactNode>(null);
  const [reference, setReference] = useState(row.paid_reference ?? '');
  const [invoice, setInvoice] = useState<EntFile | null>(row.invoice);
  const [waiving, setWaiving] = useState(false);
  const [waiveNote, setWaiveNote] = useState('');
  const [confirmQuery, setConfirmQuery] = useState(false);
  const [preview, setPreview] = useState<{ memberId: string | null; data: BillPreview } | null>(null);
  const [shareRefs, setShareRefs] = useState<Record<string, string>>({});
  const live = meta.mode === 'live';
  const venue = meta.venues.find((v) => v.id === row.venue_id);
  const tz = venue?.timezone ?? undefined;
  const billLane = row.lane === 'invoice' || row.lane === 'exemption';
  const paid = row.payment_status === 'paid';

  const run = async <T,>(key: string, fn: () => Promise<T>, after?: (res: T) => void) => {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      const res = await fn();
      after?.(res);
    } catch (e) {
      setError({ text: errorText(e), stale: isStale(e) });
    }
    setBusy(null);
  };

  const performanceResult = (res: unknown) => onChanged(((res as { performance?: Performance }).performance ?? res) as Performance);

  const markPaid = () => run('paid', () => entSend<unknown>('POST', `/performances/${row.id}/mark-paid`, { reference: reference.trim() || undefined, version: row.version }), performanceResult);
  const undoPaid = () => run('undo', () => entSend<unknown>('POST', `/performances/${row.id}/undo-paid`, { version: row.version }), performanceResult);

  const checkNow = () => invoice && run('check', () => entSend<EntFile>('POST', `/invoices/${invoice.id}/check-now`), (f) => { setInvoice(f); setNotice('Checked again.'); });
  const waive = () => {
    if (!invoice) return;
    if (!waiveNote.trim()) return setError({ text: 'Say why the query is waived.', stale: false });
    return run('waive', () => entSend<EntFile>('POST', `/invoices/${invoice.id}/waive`, { note: waiveNote.trim() }), (f) => { setInvoice(f); setWaiving(false); setNotice('Waived — recorded against your name.'); });
  };
  const sendQuery = async () => {
    if (!invoice) return;
    const res = await entSend<{ sent: boolean }>('POST', `/invoices/${invoice.id}/query`);
    setNotice(res.sent ? 'The query email was sent to the performer’s registered address.' : 'Not sent (shadow mode sends only to the payroll contact).');
  };

  const openInvoice = () => invoice && run('open', () => openEntFile(`/invoices/${invoice.id}`, invoice.filename));

  const previewBill = (memberId: string | null) =>
    run('preview', () => entSend<BillPreview>('POST', `/performances/${row.id}/bill/preview`, { member_id: memberId ?? undefined, status: 'DRAFT' }), (data) => setPreview({ memberId, data }));

  const confirmBill = async () => {
    if (!preview) return;
    setBusy('confirm');
    setError(null);
    try {
      await entSend<{ push: Push }>('POST', `/performances/${row.id}/bill/confirm`, { member_id: preview.memberId ?? undefined, fingerprint: preview.data.fingerprint, status: 'DRAFT' });
      setPreview(null);
      setNotice('The bill is in Xero as a DRAFT with the invoice attached.');
      onChanged(row);
    } catch (e) {
      // A 409 carries a fresh card and fingerprint: show it and ask again.
      if (e instanceof EntApiError && e.status === 409 && e.detail && typeof e.detail === 'object') {
        const d = e.detail as { changed?: Record<string, unknown>; fingerprint?: string; message?: string; error?: string };
        if (d.fingerprint) {
          setPreview({ memberId: preview.memberId, data: { card: d.changed ?? preview.data.card, fingerprint: d.fingerprint, run_params: preview.data.run_params } });
          setError({ text: d.message ?? d.error ?? 'Xero changed since you looked — check the fresh card and confirm again.', stale: false });
          setBusy(null);
          return;
        }
      }
      setError({ text: errorText(e), stale: false });
    }
    setBusy(null);
  };

  const shareAction = (share: Share, path: 'mark-paid' | 'undo-paid') =>
    run(`share-${share.id}`, () => entSend<{ share: Share }>('POST', `/performances/${row.id}/shares/${share.band_member_id}/${path}`, path === 'mark-paid' ? { reference: shareRefs[share.id]?.trim() || undefined } : undefined), () => onChanged(row));

  const amount = row.actual_cents ?? row.agreed_cents;

  return (
    <Dialog title={`${row.performer_name} · ${dateLabel(row.performance_date, { year: false })}`} width={760} onClose={() => { if (!busy) onClose(); }} closeButton>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: -6 }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <StatusChip status={row.status} />
          <LaneBadge lane={row.lane} />
          {row.performer.gst_registered && <Badge tone="neutral">GST registered</Badge>}
          {!row.performer.payable && <Badge tone="warn">Not payable{row.performer.payable_reason ? ` — ${row.performer.payable_reason}` : ''}</Badge>}
        </div>

        {!live && <Notice tone="warn">Shadow mode: marking paid and pushing bills are off until entertainers go live in Norm.</Notice>}
        {!row.performer.payable && <Notice tone="warn">This performer isn&rsquo;t payable yet ({row.performer.payable_reason ?? 'not accepted'}). Accept them on the Register first; no pay line or bill is produced until then.</Notice>}

        <Section title="Performance">
          <FactGrid min={140}>
            <Fact label="Venue">{row.venue_name}</Fact>
            <Fact label="Times">{timeRangeLabel(row.actual_start_time ?? row.start_time, row.actual_end_time ?? row.end_time, !!(row.actual_ends_next_day ?? row.ends_next_day))}</Fact>
            <Fact label="Hours">{hoursLabel(row.minutes.actual ?? row.minutes.booked)}</Fact>
            <Fact label="Rate">{money(row.rate_cents)} / h</Fact>
            <Fact label="Approved amount"><strong>{money(amount)}</strong></Fact>
            <Fact label="Approved by">{row.approved_by_name ? `${row.approved_by_name}, ${when(row.approved_at, tz)}` : when(row.approved_at, tz)}</Fact>
          </FactGrid>
        </Section>

        {billLane && (
          <Section
            title="Invoice"
            badge={<CheckChip status={invoice?.check_status ?? null} />}
            actions={invoice && (
              <>
                <Button size="sm" icon={FileText} onClick={openInvoice} disabled={!!busy}>Open file</Button>
                <Button size="sm" icon={RefreshCw} onClick={checkNow} disabled={!!busy}>{busy === 'check' ? 'Checking…' : 'Check now'}</Button>
              </>
            )}
          >
            {!invoice ? (
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>No invoice uploaded yet. The performer uploads it on their link; Norm reads it within a few minutes.</div>
            ) : (
              <>
                <FactGrid min={140}>
                  <Fact label="File">{invoice.filename}</Fact>
                  <Fact label="Invoice number">{invoice.invoice_number}</Fact>
                  <Fact label="Amount on invoice">{invoice.invoice_cents !== null ? money(invoice.invoice_cents) : null}</Fact>
                  <Fact label="Checked">{invoice.checked_at ? when(invoice.checked_at, tz) : 'Not yet'}</Fact>
                  {invoice.performance_ids.length > 1 && <Fact label="Covers">{invoice.performance_ids.length} performances</Fact>}
                  {invoice.waived_at && <Fact label="Waived">{invoice.waived_by ? `${invoice.waived_by}, ` : ''}{when(invoice.waived_at, tz)}</Fact>}
                </FactGrid>
                {invoice.check_status === 'pending' && <Hint style={{ marginTop: 8 }}>Checking — Norm reads the file on the next run (within five minutes). &ldquo;Check now&rdquo; runs it immediately.</Hint>}
                {invoice.findings.length > 0 && (
                  <ul style={{ margin: '10px 0 0', padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {invoice.findings.map((f, i) => (
                      <li key={`${f.code}-${i}`} style={{ padding: '8px 10px', borderRadius: 'var(--radius)', background: 'var(--error-bg)', color: 'var(--error)', fontSize: 'var(--fs-sm)' }}>
                        <strong>{FINDING_LABEL[f.code] ?? f.code}:</strong> {f.message}
                        {(f.expected !== undefined || f.found !== undefined) && (
                          <span style={{ color: 'var(--text-soft)' }}> (expected {String(f.expected ?? '—')}, found {String(f.found ?? '—')})</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {invoice.check_status === 'query' && !waiving && (
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
                    <Button size="sm" icon={Send} onClick={() => setConfirmQuery(true)} disabled={!!busy}>Send query email</Button>
                    <Button size="sm" onClick={() => setWaiving(true)} disabled={!!busy}>Waive with a note</Button>
                  </div>
                )}
                {waiving && (
                  <div style={{ marginTop: 10 }}>
                    <Field label="Why is this waived?" htmlFor={`${base}-waive`}>
                      <textarea id={`${base}-waive`} className="n-input" style={inputStyle} rows={2} value={waiveNote} onChange={(e) => setWaiveNote(e.target.value)} autoFocus />
                    </Field>
                    <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 8 }}>
                      <Button size="sm" onClick={() => setWaiving(false)} disabled={!!busy}>Cancel</Button>
                      <Button size="sm" variant="primary" onClick={waive} disabled={!!busy}>{busy === 'waive' ? 'Saving…' : 'Waive'}</Button>
                    </div>
                  </div>
                )}
              </>
            )}
          </Section>
        )}

        {billLane && (
          <Section title="Xero bill" badge={<BillState bill={row.bill} />}>
            {preview && preview.memberId === null ? (
              <>
                {preview.data.card ? <PreviewCard card={preview.data.card as PreviewCardData} /> : <Notice tone="neutral">Nothing to push — the bill is already as it would be.</Notice>}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 10 }}>
                  <Button size="sm" onClick={() => setPreview(null)} disabled={!!busy}>Cancel</Button>
                  {preview.data.card && <Button size="sm" variant="primary" onClick={confirmBill} disabled={!!busy}>{busy === 'confirm' ? 'Pushing…' : row.bill && !row.bill.voided_at ? 'Void and push again' : 'Push bill to Xero'}</Button>}
                </div>
              </>
            ) : (
              <>
                <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                  A DRAFT bill at the venue&rsquo;s entertainment expense account, GST from the register, the invoice attached. {row.bill && !row.bill.voided_at ? 'Pushing again voids the earlier bill first.' : ''}
                </div>
                <div style={{ marginTop: 10 }}>
                  <Button size="sm" variant={row.bill && !row.bill.voided_at ? 'secondary' : 'primary'} onClick={() => previewBill(null)} disabled={!!busy || !live || !row.performer.payable}>
                    {busy === 'preview' ? 'Preparing…' : row.bill && !row.bill.voided_at ? 'Push again' : 'Push bill to Xero'}
                  </Button>
                </div>
              </>
            )}
          </Section>
        )}

        {row.shares && row.shares.length > 0 && (
          <Section title="Band members’ shares">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {row.shares.map((s) => (
                <div key={s.id} style={{ padding: '8px 0', borderBottom: '1px solid var(--line-soft)' }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 500, flex: '1 1 auto' }}>{s.member_name}</span>
                    <span style={{ fontVariantNumeric: 'tabular-nums' }}>{money(s.amount_cents)}</span>
                    {s.paid_at ? <Badge tone="ok">Paid {when(s.paid_at, tz)}{s.paid_reference ? ` · ${s.paid_reference}` : ''}</Badge> : <Badge tone="neutral">Pending</Badge>}
                    <BillState bill={s.bill} />
                  </div>
                  {preview && preview.memberId === s.band_member_id ? (
                    <div style={{ marginTop: 8 }}>
                      {preview.data.card ? <PreviewCard card={preview.data.card as PreviewCardData} /> : <Notice tone="neutral">Nothing to push.</Notice>}
                      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 8 }}>
                        <Button size="sm" onClick={() => setPreview(null)} disabled={!!busy}>Cancel</Button>
                        {preview.data.card && <Button size="sm" variant="primary" onClick={confirmBill} disabled={!!busy}>{busy === 'confirm' ? 'Pushing…' : 'Push bill to Xero'}</Button>}
                      </div>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 6 }}>
                      {!s.paid_at && <input className="n-input" placeholder="Reference" aria-label={`Reference for ${s.member_name}`} value={shareRefs[s.id] ?? ''} onChange={(e) => setShareRefs((r) => ({ ...r, [s.id]: e.target.value }))} style={{ width: 160 }} />}
                      {s.paid_at ? (
                        <Button size="sm" icon={Undo2} onClick={() => shareAction(s, 'undo-paid')} disabled={!!busy || !live}>Undo paid</Button>
                      ) : (
                        <Button size="sm" onClick={() => shareAction(s, 'mark-paid')} disabled={!!busy || !live}>Mark paid</Button>
                      )}
                      <Button size="sm" onClick={() => previewBill(s.band_member_id)} disabled={!!busy || !live}>{s.bill && !s.bill.voided_at ? 'Push bill again' : 'Push bill'}</Button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Section>
        )}

        <Section title="Payment" badge={paid ? <Badge tone="ok">Paid</Badge> : <Badge tone="neutral">Pending</Badge>}>
          {paid ? (
            <>
              <FactGrid min={140}>
                <Fact label="Paid">{when(row.paid_at, tz)}{row.paid_by ? ` by ${row.paid_by}` : ''}</Fact>
                <Fact label="Reference">{row.paid_reference}</Fact>
                {row.payroll_week_start && <Fact label="Payroll week">w/c {dateLabel(row.payroll_week_start)}</Fact>}
              </FactGrid>
              <div style={{ marginTop: 10 }}>
                <Button size="sm" icon={Undo2} onClick={undoPaid} disabled={!!busy || !live}>{busy === 'undo' ? 'Undoing…' : 'Undo paid'}</Button>
              </div>
            </>
          ) : (
            <>
              {(row.lane === 'payroll_employee' || row.lane === 'payroll_contractor') && (
                <Hint style={{ marginBottom: 8 }}>Payroll-lane performances are usually paid from the Payrun for their week; marking paid here records a reference only.</Hint>
              )}
              <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
                <div style={{ flex: '1 1 200px' }}>
                  <Field label="Reference (optional)" htmlFor={`${base}-ref`}>
                    <input id={`${base}-ref`} className="n-input" style={inputStyle} value={reference} onChange={(e) => setReference(e.target.value)} placeholder="Batch, payrun week, bank reference…" />
                  </Field>
                </div>
                <Button variant="primary" onClick={markPaid} disabled={!!busy || !live || row.status === 'mismatch'}>{busy === 'paid' ? 'Saving…' : 'Mark paid'}</Button>
              </div>
              {row.status === 'mismatch' && <Hint style={{ marginTop: 6 }}>Times differ — settle the mismatch on the Roster before paying.</Hint>}
              <Hint style={{ marginTop: 6 }}>Marking paid also closes the performer&rsquo;s portal link for this performance.</Hint>
            </>
          )}
        </Section>

        {notice && <Notice tone="info">{notice}</Notice>}
        <WriteError message={error?.text ?? null} stale={error?.stale} onReload={onClose} />
        <DialogActions><Button variant="primary" onClick={onClose} disabled={!!busy}>Close</Button></DialogActions>
      </div>

      {confirmQuery && invoice && (
        <ConfirmDialog title="Send the invoice query?" confirmLabel="Send" onClose={() => setConfirmQuery(false)} onConfirm={sendQuery}>
          Norm emails {row.performer_name} what the check found, with the figures, at their registered address — never on the link. <Icon icon={Send} size={12} />
        </ConfirmDialog>
      )}
    </Dialog>
  );
}
