'use client';

/**
 * Payroll — the Reconciliation tab. The group overview for a week (every
 * entity: Loaded, adjustments, ACE, the difference in its colour band,
 * status, blockers) and, per entity, the nine-term formula with every
 * term opening to its rows, the ACE figures / adjustments / accruals
 * forms, the blockers, the journal draft with its residual, the push to
 * Xero through the approval card, the push ledger and void. With no entity
 * chosen it lands on the group overview (All entities). Each blocker links
 * to the tab that fixes it, switching in place.
 *
 * Link: `?page=payroll&tab=reconciliation&venue=<id>|all&week=<monday>`.
 */
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import PageState from '../../ui/PageState';
import { StatTile, TileRow } from '../../functions/enquiries/bits';
import Blockers from '../shared/Blockers';
import DataTable, { type Column } from '../shared/DataTable';
import Money, { useMoney } from '../shared/Money';
import PageShell, { Note, Section, Stack } from '../shared/PageShell';
import { bandColor, bandLabel, bandTone } from '../shared/bands';
import { instantLabel, plural } from '../shared/format';
import { useFinQuery, useFinanceApi } from '../shared/useFinanceApi';
import { useOpenTab } from '../shared/useWorkspaceTab';
import { weLabel, weekEnding } from '../shared/weeks';
import { DriftNote } from './payrun/SnapshotPanel';
import IcoDocuments from './reconciliation/IcoDocuments';
import { AccrualsCard, AceInputsCard, AdjustmentsCard } from './reconciliation/InputsPanel';
import JournalPanel from './reconciliation/JournalPanel';
import Terms from './reconciliation/Terms';
import type { PayrollTabProps } from './tabProps';
import type { GroupOverview, GroupRow, MappingDoc, Reconciliation, Week } from './types';
import { blockerTarget } from './workspace';

function statusBadge(status: string) {
  if (status === 'pushed') return <Badge tone="ok">Pushed to Xero</Badge>;
  if (status === 'draft') return <Badge tone="info">Draft built</Badge>;
  return <Badge tone="neutral">Not started</Badge>;
}

function GroupView({ overview, onPick }: { overview: GroupOverview; onPick: (venueId: string) => void }) {
  const money = useMoney();
  const columns: Column<GroupRow>[] = [
    { key: 'venue', label: 'Entity', render: (r) => <Button size="xs" variant="link" onClick={() => onPick(r.venue_id)}>{r.venue}</Button> },
    { key: 'timeclock_cents', label: 'Loaded (shifts here)', align: 'right', render: (r) => <Money cents={r.timeclock_cents} />, csv: (r) => r.timeclock_cents / 100 },
    { key: 'adjustments_cents', label: 'Adjustments', align: 'right', render: (r) => <Money cents={r.adjustments_cents} signed />, csv: (r) => r.adjustments_cents / 100 },
    { key: 'ace_gross_cents', label: 'ACE gross', align: 'right', render: (r) => (r.ace_gross_cents === null ? <span style={{ color: 'var(--muted)' }}>not entered</span> : <Money cents={r.ace_gross_cents} />), csv: (r) => (r.ace_gross_cents === null ? null : r.ace_gross_cents / 100) },
    { key: 'difference_cents', label: 'Difference', align: 'right', render: (r) => (r.difference_cents === null ? '—' : <span style={{ color: bandColor(r.band), fontWeight: 600 }}><Money cents={r.difference_cents} signed /></span>), csv: (r) => (r.difference_cents === null ? null : r.difference_cents / 100) },
    { key: 'band', label: 'Match', sortable: false, render: (r) => <Badge tone={bandTone(r.band)}>{bandLabel(r.band)}</Badge> },
    { key: 'status', label: 'Journal', sortable: false, hideOnPhone: true, render: (r) => statusBadge(r.status) },
    { key: 'blockers', label: 'Blockers', align: 'right', render: (r) => (r.blockers ? <Badge tone="error">{r.blockers}</Badge> : <Badge tone="ok">0</Badge>) },
  ];
  const g = overview.group;
  return (
    <Stack>
      <Note tone="plain">Loaded is the cost of shifts worked at each venue from its close snapshot; adjustments are the other eight terms; the difference is Norm&rsquo;s wages cost less ACE&rsquo;s gross. Under $1 matches, under $50 is worth a look, more is wrong.</Note>
      <DataTable columns={columns} rows={overview.venues} rowKey={(r) => r.venue_id} csvName={`Reconciliation ${weLabel(overview.week_start)}`} emptyTitle="No entity has its pay run switched on." emptyDetail="The overview lists the entities whose pay run is on — switch one on under Payroll settings → Entities, or pick an entity above to see its week anyway." footer={['Group', money(g.timeclock_cents), money(g.adjustments_cents), g.ace_gross_cents === null ? null : money(g.ace_gross_cents), g.difference_cents === null ? null : money(g.difference_cents), null, null, null]} />
    </Stack>
  );
}

function EntityView({ recon, week, api, canWrite, shadow, mode, refresh, onChanged }: {
  recon: Reconciliation;
  week: Week | null;
  api: ReturnType<typeof useFinanceApi>;
  canWrite: boolean;
  shadow: boolean;
  mode: 'off' | 'shadow' | 'live' | null;
  refresh: number;
  onChanged: () => void;
}) {
  const money = useMoney();
  const openPayroll = useOpenTab('payroll');
  const openEntertainers = useOpenTab('entertainers');
  // People who can write payroll are the people who can mark performances paid.
  const blockerLink = (text: string) => {
    const t = blockerTarget(text, { canPay: canWrite });
    if (!t) return undefined;
    if (t.page === 'entertainers') return () => openEntertainers(t.tab, { venue: recon.venue_id });
    return () => openPayroll(t.tab, { venue: recon.venue_id, week: recon.week_start, ...t.params });
  };
  const mapping = useFinQuery<MappingDoc>('/api/payroll/xero/mapping', { venue_id: recon.venue_id }, refresh);
  const tz = week?.window?.timezone || 'Pacific/Auckland';
  const adjustments = recon.computed_cents - recon.terms.timeclock.cents;
  const inputs = week?.inputs;

  return (
    <Stack>
      <TileRow min={150}>
        <StatTile label="Loaded — shifts here" value={money(recon.terms.timeclock.cents)} sub={`${plural(recon.terms.timeclock.shifts, 'shift')} in the ${recon.snapshot?.source ?? ''} snapshot`} />
        <StatTile label="Adjustments" value={money(adjustments)} sub="the other eight terms" />
        <StatTile label="ACE gross" value={recon.ace ? money(recon.ace.gross_cents) : '—'} sub={recon.ace ? `net ${money(recon.ace.net_cents)}` : 'not entered'} />
        <StatTile label="Difference" value={<span style={{ color: bandColor(recon.band) }}>{recon.difference_cents === null ? '—' : money(recon.difference_cents)}</span>} sub={bandLabel(recon.band)} />
      </TileRow>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }} data-testid="payroll-journal-status">
        Journal: {statusBadge(recon.status)}
      </div>
      {!recon.snapshot && <Note tone="warn">No close snapshot for this week — figures come from nothing yet. Start the close at the top of the page.</Note>}
      {recon.snapshot && recon.snapshot.source !== 'close' && <Note tone="warn">Figures come from the {recon.snapshot.source} snapshot; the journal needs a close snapshot. Start the close at the top of the page.</Note>}
      {week && <DriftNote week={week} builds="The journal builds" />}
      <Blockers blockers={recon.blockers.map((b) => ({ text: b, onClick: blockerLink(b), linkLabel: 'Open' }))} warnings={recon.warnings} okText="Nothing is blocking the journal." />
      <Section title="The reconciliation" meta={recon.snapshot ? `close snapshot ${instantLabel(recon.snapshot.taken_at, tz)}` : undefined} padded={false}>
        <Terms recon={recon} timeZone={tz} />
      </Section>
      {week && (
        <>
          <AceInputsCard api={api} weekId={week.id} value={inputs?.ace_inputs ?? null} canWrite={canWrite} shadow={shadow} onChanged={onChanged} />
          <AdjustmentsCard api={api} weekId={week.id} rows={inputs?.adjustments ?? []} accounts={mapping.data?.accounts ?? []} canWrite={canWrite} shadow={shadow} onChanged={onChanged} />
          <AccrualsCard api={api} weekId={week.id} value={inputs?.accruals ?? null} canWrite={canWrite} shadow={shadow} onChanged={onChanged} />
        </>
      )}
      <JournalPanel api={api} venueId={recon.venue_id} venueName={recon.venue} weekStart={recon.week_start} canWrite={canWrite} mode={mode} refresh={refresh} onChanged={onChanged} />
      <IcoDocuments api={api} venueId={recon.venue_id} venueName={recon.venue} weekStart={recon.week_start} canWrite={canWrite} mode={mode} refresh={refresh} onChanged={onChanged} />
    </Stack>
  );
}

export default function PayrollReconciliation({ meta, entity, allVenues, weekStart, refresh, onChanged, setVenue, week }: PayrollTabProps & {
  /** The page's pay-week record for the chosen entity (one fetch, shared with the header). */
  week: Week | null;
}) {
  const api = useFinanceApi();
  const venueId = allVenues ? null : entity?.venue_id ?? null;
  const we = weekEnding(weekStart);
  const group = useFinQuery<GroupOverview>(allVenues ? '/api/payroll/reconciliation' : null, { week_ending: we }, refresh);
  const recon = useFinQuery<Reconciliation>(venueId ? `/api/payroll/reconciliation/${venueId}` : null, { week_ending: we }, refresh);
  const canWrite = !!meta.can_write;
  const shadow = meta.mode === 'shadow';

  let body: React.ReactNode;
  if (allVenues) {
    body = group.error && !group.data ? <PageState kind="error" title="Could not load the overview." detail={group.error} action={<Button size="sm" onClick={group.retry}>Try again</Button>} />
      : !group.data ? <PageState kind="loading" title="Loading…" />
      : <GroupView overview={group.data} onPick={setVenue} />;
  } else if (!entity) {
    body = <PageState kind="empty" title="No payroll entities." />;
  } else {
    body = recon.error && !recon.data ? <PageState kind="error" title="Could not load the reconciliation." detail={recon.error} action={<Button size="sm" onClick={recon.retry}>Try again</Button>} />
      : !recon.data ? <PageState kind="loading" title="Working out the reconciliation…" />
      : <EntityView recon={recon.data} week={week && week.venue_id === venueId ? week : null} api={api} canWrite={canWrite} shadow={shadow} mode={meta.mode ?? null} refresh={refresh} onChanged={onChanged} />;
  }

  return <PageShell isPage embedded title="Reconciliation" testId="payroll-reconciliation">{body}</PageShell>;
}
