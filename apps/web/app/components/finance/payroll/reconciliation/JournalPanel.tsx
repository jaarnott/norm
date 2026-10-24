'use client';

/**
 * The payroll journal for one entity-week: the stored draft's lines (DR /
 * CR with the mapped account), totals and residual; "Build draft";
 * "Push to Xero" through the approval card (DRAFT only, idempotent); the
 * live push's state; void; the week's push ledger; and the Wednesday
 * status refresh.
 */
import { useState } from 'react';
import { ExternalLink, Hammer, RefreshCw, Send } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import Dialog from '../../../ui/Dialog';
import PageState from '../../../ui/PageState';
import ApprovalCard, { type PageWrite } from '../../shared/ApprovalCard';
import Blockers from '../../shared/Blockers';
import DataTable, { type Column } from '../../shared/DataTable';
import Money, { useMoney } from '../../shared/Money';
import { Note, Section, Stack } from '../../shared/PageShell';
import { instantLabel } from '../../shared/format';
import { finSend, useFinQuery, type FinanceApi } from '../../shared/useFinanceApi';
import { weDotted, weekEnding } from '../../shared/weeks';
import type { JournalLine, PayrollMode, Push } from '../types';
import { PushState, VoidDialog } from './IcoDocuments';
import { kindLabel, type JournalDocument } from './journalTypes';

export default function JournalPanel({ api, venueId, venueName, weekStart, canWrite, mode, refresh, onChanged }: {
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
  const we = weekEnding(weekStart);
  const q = useFinQuery<JournalDocument>('/api/payroll/xero/journal', { venue_id: venueId, week_ending: we }, refresh);
  const pushes = useFinQuery<{ pushes: Push[] }>('/api/payroll/xero/pushes', { venue_id: venueId, week_ending: we, include_voided: true }, refresh);
  const status = useFinQuery<{ last_run: { slot: string | null; started_at: string | null; finished_at: string | null; error: string | null } | null }>('/api/payroll/xero/status', { venue_id: venueId }, refresh);
  const [pushing, setPushing] = useState<{ replace: boolean } | null>(null);
  const [voiding, setVoiding] = useState<{ id: string; label: string } | null>(null);
  const live = mode === 'live';
  const doc = q.data;
  const draft = doc?.journal ?? null;
  const livePush = draft?.live_push && !draft.live_push.voided_at ? draft.live_push : null;

  const build = () => api.run('build-journal', async () => { await api.post('/xero/journal/build', { venue_id: venueId, week_ending: we }); q.retry(); onChanged(); }, 'Journal draft built.');
  const refreshStatus = () => api.run('status', async () => { await api.post('/xero/status/refresh', { venue_id: venueId }); q.retry(); pushes.retry(); status.retry(); }, 'Xero statuses refreshed.');

  const write = (replace: boolean): PageWrite => ({
    preview: () => finSend('POST', '/api/payroll/xero/journal/preview', { venue_id: venueId, week_ending: we, draft_id: draft?.id, basis_sha256: draft?.basis_sha256, replace }),
    confirm: (fingerprint) => finSend('POST', '/api/payroll/xero/journal/confirm', { venue_id: venueId, week_ending: we, draft_id: draft?.id, basis_sha256: draft?.basis_sha256, replace, fingerprint }),
  });

  const lineColumns: Column<JournalLine>[] = [
    { key: 'description', label: 'Line', sortable: false, render: (r) => <span>{r.description}{r.key === 'rounding' && <> <Badge tone="neutral">rounding</Badge></>}</span> },
    { key: 'account', label: 'Xero account', sortable: false, hideOnPhone: true, value: (r) => (r.account_code ? `${r.account_code} ${r.account_name ?? ''}`.trim() : null), render: (r) => (r.account_code ? `${r.account_code} · ${r.account_name ?? ''}` : <Badge tone="error">Not mapped</Badge>) },
    { key: 'dr', label: 'Debit', align: 'right', sortable: false, value: (r) => (r.side === 'DR' ? r.amount_cents / 100 : null), render: (r) => (r.side === 'DR' ? <Money cents={r.amount_cents} /> : '') },
    { key: 'cr', label: 'Credit', align: 'right', sortable: false, value: (r) => (r.side === 'CR' ? r.amount_cents / 100 : null), render: (r) => (r.side === 'CR' ? <Money cents={r.amount_cents} /> : '') },
  ];

  const pushColumns: Column<Push>[] = [
    { key: 'kind', label: 'Document', render: (r) => <span>{kindLabel(r.kind)}{r.counterparty ? ` — ${r.counterparty}` : ''}</span>, nowrap: true },
    { key: 'xero_number', label: 'Xero number', render: (r) => r.xero_number || <span style={{ color: 'var(--muted)', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)' }}>{r.xero_id?.slice(0, 8)}</span> },
    { key: 'state', label: 'State', sortable: false, render: (r) => <PushState push={r} /> },
    { key: 'pushed_at', label: 'Pushed', render: (r) => instantLabel(r.pushed_at), nowrap: true },
    { key: 'voided_at', label: 'Voided', hideOnPhone: true, render: (r) => (r.voided_at ? instantLabel(r.voided_at) : '—') },
    { key: 'actions', label: '', sortable: false, align: 'right', render: (r) => (canWrite && !r.voided_at ? <Button size="xs" variant="quiet" onClick={() => setVoiding({ id: r.id, label: `${kindLabel(r.kind).toLowerCase()} ${r.xero_number ?? ''}`.trim() })}>Void</Button> : null) },
  ];

  const lines = draft?.lines ?? doc?.proposed.lines ?? [];
  const showingProposed = !draft && (doc?.proposed.lines.length ?? 0) > 0;

  return (
    <Stack>
      <Section
        title="Payroll journal"
        meta={draft ? `draft built ${instantLabel(draft.built_at)}${draft.stale ? ' · figures moved since' : ''}` : 'no draft yet'}
        actions={canWrite ? (
          <>
            <Button size="sm" icon={Hammer} onClick={build} disabled={api.busy === 'build-journal' || !!(doc?.blockers.length)} title={doc?.blockers.length ? 'Clear the blockers first' : undefined}>{api.busy === 'build-journal' ? 'Building…' : draft ? 'Build again' : 'Build draft'}</Button>
            {livePush ? (
              <Button size="sm" variant="quiet" onClick={() => setPushing({ replace: true })} disabled={!live || !draft || draft.stale}>Void and push again</Button>
            ) : (
              <Button size="sm" variant="primary" icon={Send} onClick={() => setPushing({ replace: false })} disabled={!live || !draft || draft.stale || !!(doc?.blockers.length)} title={!live ? 'Pushes to Xero happen only once Payroll is live' : draft?.stale ? 'The figures moved since the draft was built — build again first' : undefined}>Push to Xero</Button>
            )}
          </>
        ) : undefined}
        padded={false}
      >
        <div style={{ padding: '8px 16px 12px' }}>
          {q.error && !doc ? <PageState kind="error" title="Could not load the journal." detail={q.error} /> : !doc ? <PageState kind="loading" title="Loading…" /> : (
            <Stack gap={8}>
              {!live && <Note tone="plain">During the trial run the draft is built and compared with Orbit&rsquo;s journal to the cent; nothing is pushed until the cut-over.</Note>}
              {livePush && <Note tone="ok">In Xero as a DRAFT: {livePush.xero_number ?? livePush.xero_id} · {livePush.xero_status} · pushed {instantLabel(livePush.pushed_at)}. The accountant posts it inside Xero.</Note>}
              {draft?.stale && <Note tone="warn">The inputs changed since this draft was built (the snapshot, ACE figures, a payout, an adjustment or the account mapping). Build again before pushing.</Note>}
              <Blockers blockers={doc.blockers} warnings={doc.warnings} title="Before the journal can be built or pushed" okText={draft ? undefined : 'Nothing is blocking the journal.'} compact />
              {showingProposed && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>What the inputs would build now (no draft stored yet):</div>}
              <DataTable columns={lineColumns} rows={lines} rowKey={(r) => r.key} dense csvName={`Journal ${venueName} ${weDotted(weekStart)}`} emptyTitle="No journal lines." emptyDetail="Enter the ACE figures and build the draft." footer={lines.length ? ['Totals', null, money(draft?.debit_cents ?? doc.proposed.debit_cents), money(draft?.credit_cents ?? doc.proposed.credit_cents)] : undefined} />
              {lines.length > 0 && (
                <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                  Residual before rounding: <Money cents={draft?.residual_cents ?? doc.proposed.residual_cents} signed /> {Math.abs(draft?.residual_cents ?? doc.proposed.residual_cents) >= 100 ? <Badge tone="error">$1 or more — the journal is refused</Badge> : Math.abs(draft?.residual_cents ?? doc.proposed.residual_cents) > 0 ? <Badge tone="warn">under $1 — a Rounding line balances it</Badge> : <Badge tone="ok">balances</Badge>}
                  {draft?.narration && <> · narration &ldquo;{draft.narration}&rdquo;</>}
                  {' · DRAFT only, no tax'}
                </div>
              )}
              {doc.proposed.notes.length > 0 && <ul style={{ margin: 0, paddingLeft: 18, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{doc.proposed.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
            </Stack>
          )}
        </div>
      </Section>

      <Section title="Pushes to Xero this week" meta={status.data?.last_run ? `statuses refreshed ${instantLabel(status.data.last_run.finished_at ?? status.data.last_run.started_at)}${status.data.last_run.error ? ` — ${status.data.last_run.error}` : ''}` : 'statuses refresh every Wednesday 03:00'} actions={canWrite ? <Button size="xs" variant="quiet" icon={RefreshCw} onClick={refreshStatus} disabled={api.busy === 'status'}>Refresh statuses from Xero</Button> : undefined} padded={false}>
        <div style={{ padding: '8px 16px 12px' }}>
          <DataTable columns={pushColumns} rows={pushes.data?.pushes ?? []} rowKey={(r) => r.id} dense emptyTitle="Nothing pushed for this week." />
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 6 }}>
            <ExternalLink size={11} aria-hidden style={{ verticalAlign: -1 }} /> Open the document in Xero by its number; Norm keeps the id, the request and the response for every push.
          </div>
        </div>
      </Section>

      {pushing && (
        <Dialog title={pushing.replace ? 'Void the journal in Xero and push again' : 'Push the payroll journal to Xero'} width={640} onClose={() => setPushing(null)} closeButton>
          <ApprovalCard write={write(pushing.replace)} confirmLabel={pushing.replace ? 'Void and push' : 'Push to Xero'} danger={pushing.replace} intro={`A DRAFT manual journal for ${venueName}, ${weDotted(weekStart)}, sent with an idempotency key so a retry never double-posts. The server refuses if the figures moved since you looked.`} onDone={() => { setPushing(null); q.retry(); pushes.retry(); onChanged(); }} onCancel={() => setPushing(null)} />
        </Dialog>
      )}
      {voiding && <VoidDialog pushId={voiding.id} label={voiding.label} onClose={() => setVoiding(null)} onDone={() => { q.retry(); pushes.retry(); onChanged(); }} />}
    </Stack>
  );
}
