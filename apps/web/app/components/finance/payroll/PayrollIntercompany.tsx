'use client';

/**
 * Loaded Payroll — Intercompany. For a pay week, every shift where the
 * entity paying the person is not the venue they worked at (Loaded's own
 * flag), plus gratuity shares paid by another entity's payroll. Per
 * counterparty: In (we pay the person, they worked there — we invoice
 * them) and Out (they pay the person, who worked here — they bill us), the
 * people behind each line, and `net = In − Out`. For one entity the ICO
 * drafts Norm would post to Xero and their push state, with the push and
 * the void through the approval card.
 *
 * Deep links: `?page=payroll-intercompany&venue=<id>|all&week=<monday>`.
 */
import { useState } from 'react';
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import type { DisplayBlockProps } from '../../display/DisplayBlockRenderer';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import PageState from '../../ui/PageState';
import { StatTile, TileRow } from '../../functions/enquiries/bits';
import DataTable, { type Column } from '../shared/DataTable';
import Money, { useMoney } from '../shared/Money';
import PageShell, { Note, Section, Stack } from '../shared/PageShell';
import WeekPicker from '../shared/WeekPicker';
import { formatHours, personName, plural } from '../shared/format';
import { useFinQuery, useFinanceApi } from '../shared/useFinanceApi';
import { weLabel, weekEnding, weekRangeLabel } from '../shared/weeks';
import { MetaGate } from './PayrollPayrun';
import IcoDocuments from './reconciliation/IcoDocuments';
import type { IcoGroup, IcoLine, IcoPicture, IcoVenue } from './types';
import { usePayrollPage } from './usePayrollMeta';

function Lines({ group, direction }: { group: IcoGroup; direction: 'in' | 'out' }) {
  const columns: Column<IcoLine>[] = [
    { key: 'staff_name', label: 'Person', value: (r) => personName(r.staff_name), render: (r) => <span>{personName(r.staff_name)}{r.gratuity && <> <Badge tone="info">Gratuities</Badge></>}</span>, nowrap: true },
    { key: 'hours', label: direction === 'in' ? 'Hours worked there' : 'Hours worked here', align: 'right', value: (r) => Number(r.hours), render: (r) => (Number(r.hours) ? formatHours(r.hours) : '—') },
    { key: 'amount_cents', label: 'Labour cost', align: 'right', render: (r) => <Money cents={r.amount_cents} />, csv: (r) => r.amount_cents / 100 },
  ];
  return <DataTable columns={columns} rows={group.lines} rowKey={(r) => `${r.staff_member_id}:${r.gratuity ? 'g' : 'w'}`} dense initialSort={{ key: 'amount_cents', dir: 'desc' }} emptyTitle="No lines." />;
}

function Groups({ title, groups, direction, hint }: { title: string; groups: IcoGroup[]; direction: 'in' | 'out'; hint: string }) {
  const money = useMoney();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const total = groups.reduce((s, g) => s + g.amount_cents, 0);
  return (
    <Section title={title} meta={`${hint} · ${money(total)}`} padded={false}>
      {groups.length === 0 ? <div style={{ padding: '10px 16px', color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>None this week.</div> : groups.map((g) => {
        const key = g.counterparty_venue_id ?? g.counterparty;
        const isOpen = open[key] ?? groups.length === 1;
        return (
          <div key={key} style={{ borderBottom: '1px solid var(--line-soft)' }}>
            <button type="button" onClick={() => setOpen((o) => ({ ...o, [key]: !isOpen }))} aria-expanded={isOpen} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px', background: 'transparent', border: 'none', font: 'inherit', color: 'var(--text)', cursor: 'pointer', textAlign: 'left' }}>
              <Icon icon={isOpen ? ChevronDown : ChevronRight} size="dense" />
              <span style={{ fontWeight: 600, flex: 1 }}>{g.counterparty}{!g.counterparty_venue_id && <> <Badge tone="warn">Not a Norm venue</Badge></>}</span>
              <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{plural(g.lines.length, 'person', 'people')} · {formatHours(g.hours)}</span>
              <span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{money(g.amount_cents)}</span>
            </button>
            {isOpen && <div style={{ padding: '0 16px 12px' }}><Lines group={g} direction={direction} /></div>}
          </div>
        );
      })}
    </Section>
  );
}

function VenueView({ v }: { v: IcoVenue }) {
  const money = useMoney();
  return (
    <Stack>
      {v.warnings.map((w) => <Note key={w} tone="warn">{w}</Note>)}
      <TileRow min={150}>
        <StatTile label="In — we invoice" value={money(v.in_cents)} sub="our staff worked at another venue" />
        <StatTile label="Out — they bill us" value={money(v.out_cents)} sub="their staff worked here" />
        <StatTile label="Net (In − Out)" value={money(v.net_cents)} sub={v.net_cents >= 0 ? 'adds to our wages cost' : 'comes off our wages cost'} />
      </TileRow>
      <Groups title="In — we invoice the counterparty" hint="people this entity pays who worked there" groups={v.in} direction="in" />
      <Groups title="Out — the counterparty bills us" hint="people they pay who worked here" groups={v.out} direction="out" />
    </Stack>
  );
}

export default function PayrollIntercompany({ props }: DisplayBlockProps) {
  const page = usePayrollPage(props, { allowAll: true, defaultAll: true });
  const api = useFinanceApi();
  const money = useMoney();
  const [refresh, setRefresh] = useState(0);
  const venueId = page.allVenues ? null : page.entity?.venue_id ?? null;
  const q = useFinQuery<IcoPicture>(page.meta ? '/api/payroll/reconciliation/intercompany' : null, { week_ending: weekEnding(page.weekStart), venue_id: venueId ?? undefined }, refresh);
  const bump = () => setRefresh((n) => n + 1);

  const overviewColumns: Column<IcoVenue>[] = [
    { key: 'venue', label: 'Entity', render: (r) => <Button size="xs" variant="link" onClick={() => page.setVenue(r.venue_id)}>{r.venue}</Button> },
    { key: 'in_cents', label: 'In (we invoice)', align: 'right', render: (r) => <Money cents={r.in_cents} />, csv: (r) => r.in_cents / 100 },
    { key: 'out_cents', label: 'Out (they bill us)', align: 'right', render: (r) => <Money cents={r.out_cents} />, csv: (r) => r.out_cents / 100 },
    { key: 'net_cents', label: 'Net', align: 'right', render: (r) => <Money cents={r.net_cents} signed />, csv: (r) => r.net_cents / 100 },
    { key: 'people', label: 'People', align: 'right', hideOnPhone: true, value: (r) => new Set([...r.in, ...r.out].flatMap((g) => g.lines.map((l) => l.staff_member_id))).size },
    { key: 'warnings', label: 'Warnings', hideOnPhone: true, value: (r) => r.warnings.length, render: (r) => (r.warnings.length ? <Badge tone="warn">{r.warnings.length}</Badge> : null) },
  ];

  let body: React.ReactNode;
  if (q.error && !q.data) {
    body = <PageState kind="error" title="Could not load the intercompany picture." detail={q.error} action={<Button size="sm" onClick={q.retry}>Try again</Button>} />;
  } else if (!q.data) {
    body = <PageState kind="loading" title="Loading…" />;
  } else if (page.allVenues) {
    const venues = q.data.venues;
    const totalIn = venues.reduce((s, v) => s + v.in_cents, 0);
    const totalOut = venues.reduce((s, v) => s + v.out_cents, 0);
    body = (
      <Stack>
        <Note tone="plain">Each entity's In is another entity's Out. The group's In and Out add to the same figure once every entity is connected; a gap means a venue Loaded knows that Norm does not.</Note>
        <DataTable columns={overviewColumns} rows={venues} rowKey={(r) => r.venue_id} csvName={`Intercompany ${weLabel(page.weekStart)}`} emptyTitle="No intercompany shifts this week." footer={['Group', money(totalIn), money(totalOut), money(totalIn - totalOut), null, null]} />
      </Stack>
    );
  } else {
    const v = q.data.venues.find((x) => x.venue_id === venueId) ?? q.data.venues[0];
    body = v ? (
      <Stack>
        <VenueView v={v} />
        <IcoDocuments api={api} venueId={v.venue_id} venueName={v.venue} weekStart={page.weekStart} canWrite={!!page.meta?.can_write} mode={page.meta?.mode ?? null} refresh={refresh} onChanged={bump} />
      </Stack>
    ) : <PageState kind="empty" title="No intercompany picture for this entity." />;
  }

  return (
    <PageShell
      isPage={page.isPage}
      title="Intercompany"
      meta={weekRangeLabel(page.weekStart)}
      mode={page.meta?.mode}
      venues={[{ id: 'all', name: 'All entities' }, ...page.venues]}
      venueId={page.allVenues ? 'all' : page.venueId}
      onVenueChange={page.setVenue}
      venueTestId="payroll-intercompany-venue"
      actions={<Button size="sm" variant="quiet" icon={RefreshCw} onClick={bump}>Refresh</Button>}
      toolbar={page.meta ? <WeekPicker value={page.weekStart} onChange={page.setWeekStart} venue={page.entity} thisWeek={page.meta.this_week_start} /> : undefined}
      testId="payroll-intercompany"
    >
      <MetaGate status={page.status} error={page.error} loading={page.loading}>{body}</MetaGate>
    </PageShell>
  );
}
