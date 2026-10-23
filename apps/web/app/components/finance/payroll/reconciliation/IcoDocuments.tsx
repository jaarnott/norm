'use client';

/**
 * The intercompany documents for one entity-week: the stored ICO drafts
 * (an invoice to each counterparty we recharge, a bill from each that
 * recharges us), their push state in Xero, "Build drafts", "Push to
 * Xero" through the approval card, and void. Shared by the Intercompany
 * and Reconciliation pages.
 */
import { useState } from 'react';
import { Hammer, Send } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import Dialog from '../../../ui/Dialog';
import PageState from '../../../ui/PageState';
import ApprovalCard, { type PageWrite } from '../../shared/ApprovalCard';
import Blockers from '../../shared/Blockers';
import DataTable, { type Column } from '../../shared/DataTable';
import Money, { useMoney } from '../../shared/Money';
import { Note, Section, Stack } from '../../shared/PageShell';
import { bpLabel, instantLabel, plural } from '../../shared/format';
import { finSend, useFinQuery, type FinanceApi } from '../../shared/useFinanceApi';
import { weDotted, weekEnding } from '../../shared/weeks';
import type { PayrollMode } from '../types';
import { kindLabel, xeroStatusTone, type JournalDocument, type StoredIco } from './journalTypes';

export function VoidDialog({ pushId, label, onClose, onDone }: { pushId: string; label: string; onClose: () => void; onDone: () => void }) {
  const write: PageWrite = {
    preview: () => finSend('POST', '/api/payroll/xero/void/preview', { push_id: pushId }),
    confirm: (fingerprint) => finSend('POST', '/api/payroll/xero/void/confirm', { push_id: pushId, fingerprint }),
  };
  return (
    <Dialog title={`Void ${label}`} width={560} onClose={onClose} closeButton>
      <ApprovalCard write={write} confirmLabel="Void in Xero" danger intro="Norm reads the document's status in Xero first: a draft is deleted, a posted or authorised one is voided, a paid one cannot be touched." onDone={() => { onDone(); onClose(); }} onCancel={onClose} />
    </Dialog>
  );
}

export function PushState({ push }: { push: { xero_number: string | null; xero_status: string | null; pushed_at: string | null; voided_at: string | null } | null | undefined }) {
  if (!push) return <Badge tone="neutral">Not pushed</Badge>;
  if (push.voided_at) return <Badge tone="error">Voided</Badge>;
  return <Badge tone={xeroStatusTone(push.xero_status)}>{push.xero_number ? `${push.xero_number} · ` : ''}{push.xero_status || 'pushed'}</Badge>;
}

export default function IcoDocuments({ api, venueId, venueName, weekStart, canWrite, mode, refresh, onChanged }: {
  api: FinanceApi;
  venueId: string;
  venueName: string;
  weekStart: string;
  canWrite: boolean;
  mode: PayrollMode | null;
  refresh: number;
  onChanged: () => void;
}) {
  const money = useMoney();
  const q = useFinQuery<JournalDocument>('/api/payroll/xero/journal', { venue_id: venueId, week_ending: weekEnding(weekStart) }, refresh);
  const [pushing, setPushing] = useState<{ replace: boolean } | null>(null);
  const [voiding, setVoiding] = useState<{ id: string; label: string } | null>(null);
  const live = mode === 'live';
  const doc = q.data;

  const build = () => api.run('build', async () => { await api.post('/xero/journal/build', { venue_id: venueId, week_ending: weekEnding(weekStart) }); q.retry(); onChanged(); }, 'Drafts built from this week’s figures.');

  const columns: Column<StoredIco>[] = [
    { key: 'kind', label: 'Document', render: (r) => <span>{kindLabel(r.kind)}{r.stale && <> <Badge tone="warn">Figures moved — build again</Badge></>}</span>, nowrap: true },
    { key: 'counterparty', label: 'Counterparty', render: (r) => <span>{r.counterparty}{!r.contact && <> <Badge tone="error">No Xero contact mapped</Badge></>}</span> },
    { key: 'people', label: 'People', align: 'right', value: (r) => r.lines.filter((l) => !('key' in l && l.key === 'leave_uplift')).length },
    { key: 'wages_cents', label: 'Wages', align: 'right', render: (r) => <Money cents={r.wages_cents} />, csv: (r) => r.wages_cents / 100 },
    { key: 'uplift_cents', label: 'Leave & entitlements', align: 'right', hideOnPhone: true, render: (r) => <span><Money cents={r.uplift_cents} /> <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-xs)' }}>({bpLabel(r.uplift_bp)})</span></span>, csv: (r) => r.uplift_cents / 100 },
    { key: 'subtotal_cents', label: 'Total ex GST', align: 'right', render: (r) => <Money cents={r.subtotal_cents} />, csv: (r) => r.subtotal_cents / 100 },
    { key: 'push', label: 'In Xero', sortable: false, render: (r) => <PushState push={r.live_push} /> },
    { key: 'actions', label: '', sortable: false, align: 'right', render: (r) => (canWrite && r.live_push && !r.live_push.voided_at ? <Button size="xs" variant="quiet" onClick={() => setVoiding({ id: r.live_push!.id, label: `the ${kindLabel(r.kind).toLowerCase()} for ${r.counterparty}` })}>Void</Button> : null) },
  ];

  const pushWrite = (replace: boolean): PageWrite => ({
    preview: () => finSend('POST', '/api/payroll/xero/intercompany/preview', { venue_id: venueId, week_ending: weekEnding(weekStart), replace }),
    confirm: (fingerprint) => finSend('POST', '/api/payroll/xero/intercompany/confirm', { venue_id: venueId, week_ending: weekEnding(weekStart), replace, fingerprint }),
  });

  const anyLive = (doc?.ico ?? []).some((d) => d.live_push && !d.live_push.voided_at);
  const anyUnpushed = (doc?.ico ?? []).some((d) => !d.live_push || d.live_push.voided_at);

  return (
    <Section
      title="Intercompany documents in Xero"
      meta={doc ? `${plural(doc.ico.length, 'draft')} · ${weDotted(weekStart)}` : undefined}
      actions={canWrite ? (
        <>
          <Button size="sm" icon={Hammer} onClick={build} disabled={api.busy === 'build'}>{api.busy === 'build' ? 'Building…' : doc?.ico.length ? 'Build drafts again' : 'Build drafts'}</Button>
          <Button size="sm" variant="primary" icon={Send} onClick={() => setPushing({ replace: false })} disabled={!live || !doc?.ico.length || !anyUnpushed} title={!live ? 'Pushes to Xero happen only once Loaded Payroll is live' : undefined}>Push to Xero</Button>
          {anyLive && <Button size="sm" variant="quiet" onClick={() => setPushing({ replace: true })} disabled={!live}>Void and push again</Button>}
        </>
      ) : undefined}
      padded={false}
    >
      <div style={{ padding: '8px 16px 12px' }}>
        {q.error && !doc ? <PageState kind="error" title="Could not load the documents." detail={q.error} /> : !doc ? <PageState kind="loading" title="Loading…" /> : (
          <Stack gap={8}>
            {!live && <Note tone="plain">In shadow the drafts are built and compared with Orbit's documents; nothing is pushed until the cut-over.</Note>}
            {doc.blockers.length > 0 && <Blockers blockers={doc.blockers} title="Before these can be pushed" compact />}
            <DataTable columns={columns} rows={doc.ico} rowKey={(r) => r.id} dense emptyTitle="No intercompany drafts yet." emptyDetail="Build the drafts once the week's close snapshot is in; a counterparty appears when at least one person crossed between the entities." />
            {doc.intercompany && (
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                Wages to account {doc.intercompany.wages_account || <Badge tone="error">not mapped</Badge>} · leave & entitlements ({bpLabel(doc.intercompany.uplift_bp)}) to {doc.intercompany.leave_account || <Badge tone="error">not mapped</Badge>} · due 14 days after {venueName}&rsquo;s week ending · GST from the tenant&rsquo;s tax rates · {money(doc.ico.reduce((s, d) => s + d.subtotal_cents, 0))} in all
                {doc.ico.some((d) => d.built_at) && <> · built {instantLabel(doc.ico[0].built_at)}</>}
              </div>
            )}
          </Stack>
        )}
      </div>
      {pushing && (
        <Dialog title={pushing.replace ? 'Void the pushed documents and push again' : 'Push the intercompany documents to Xero'} width={640} onClose={() => setPushing(null)} closeButton>
          <ApprovalCard write={pushWrite(pushing.replace)} confirmLabel={pushing.replace ? 'Void and push' : 'Push to Xero'} danger={pushing.replace} intro="One DRAFT invoice or bill per counterparty, each with its own idempotency key. A counterparty without a mapped Xero contact is refused by name; the others still post." onDone={() => { setPushing(null); q.retry(); onChanged(); }} onCancel={() => setPushing(null)} />
        </Dialog>
      )}
      {voiding && <VoidDialog pushId={voiding.id} label={voiding.label} onClose={() => setVoiding(null)} onDone={() => { q.retry(); onChanged(); }} />}
    </Section>
  );
}
