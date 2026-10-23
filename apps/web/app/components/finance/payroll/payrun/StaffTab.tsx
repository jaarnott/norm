'use client';

/**
 * The Staff tab: the people this entity pays this week (the Payrun's main
 * table), the Security contractors chip (pay code = the entity's security
 * code, excluded from the wages export), the Salaried chip, and the
 * "shifts at this venue" view — the old Timeclock screen — listing every
 * shift worked here with its clock times.
 */
import { useMemo, useState } from 'react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import DataTable, { type Column } from '../../shared/DataTable';
import Money, { useMoney } from '../../shared/Money';
import { clockLabel, dateLabel, formatHours, personName, plural } from '../../shared/format';
import type { Fact, PayrunDoc, SalariedRow, StaffRow } from '../types';
import { factTotals, isSalary, matchesSearch, remunerationLabel, shiftsOf, staffTotals, workedHere, type PeopleChip } from './logic';

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <Button size="xs" variant={active ? 'primary' : 'quiet'} onClick={onClick} aria-pressed={active}>{children}</Button>
  );
}

export default function StaffTab({ doc, timeZone, initialChip = 'staff', initialView = 'people', weekLabel }: {
  doc: PayrunDoc;
  timeZone: string;
  initialChip?: PeopleChip;
  initialView?: 'people' | 'shifts';
  weekLabel: string;
}) {
  const money = useMoney();
  const [chip, setChip] = useState<PeopleChip>(initialChip);
  const [view, setView] = useState<'people' | 'shifts'>(initialView);
  const [search, setSearch] = useState('');
  const [person, setPerson] = useState<string | null>(null);

  const rows: StaffRow[] = chip === 'security' ? doc.security.rows : doc.staff.rows;
  const salaried = doc.salaried?.rows ?? [];
  const filtered = useMemo(() => rows.filter((r) => matchesSearch(search, r.staff_name, r.pay_code)), [rows, search]);
  const totals = staffTotals(filtered);

  const here = useMemo(() => workedHere(doc.facts), [doc.facts]);
  const shifts = useMemo(() => {
    const base = person ? shiftsOf(here, person) : here;
    return base.filter((f) => matchesSearch(search, f.staff_name, f.pay_code, f.role_name));
  }, [here, person, search]);
  const shiftTotals = factTotals(shifts);

  const peopleColumns: Column<StaffRow>[] = [
    { key: 'staff_name', label: 'Person', value: (r) => personName(r.staff_name), render: (r) => (
      <button type="button" onClick={() => { setPerson(r.staff_member_id); setView('shifts'); }} style={{ background: 'none', border: 'none', padding: 0, font: 'inherit', color: 'var(--accent)', cursor: 'pointer', textAlign: 'left' }} title="Show this person's shifts">
        {personName(r.staff_name)}
      </button>
    ), nowrap: true },
    { key: 'pay_code', label: 'Pay code', render: (r) => r.pay_code || <Badge tone="error">Missing</Badge> },
    { key: 'remuneration_type', label: 'Type', hideOnPhone: true, render: (r) => (isSalary(r.remuneration_type) ? <Badge tone="info">Salary</Badge> : remunerationLabel(r.remuneration_type)) },
    { key: 'shifts', label: 'Shifts', align: 'right', hideOnPhone: true },
    { key: 'hours', label: 'Hours', align: 'right', value: (r) => Number(r.hours), render: (r) => formatHours(r.hours) },
    { key: 'other_venue_hours', label: 'Hours elsewhere', align: 'right', hideOnPhone: true, value: (r) => Number(r.other_venue_hours), render: (r) => (Number(r.other_venue_hours) ? formatHours(r.other_venue_hours) : '—') },
    { key: 'ph_hours', label: 'Public holiday hours', align: 'right', hideOnPhone: true, value: (r) => Number(r.ph_hours), render: (r) => (Number(r.ph_hours) ? formatHours(r.ph_hours) : '—') },
    { key: 'cost_cents', label: 'Cost', align: 'right', render: (r) => <Money cents={r.cost_cents} />, csv: (r) => r.cost_cents / 100, csvLabel: 'Cost ($)' },
    { key: 'ph_cost_cents', label: 'Public holiday pay', align: 'right', hideOnPhone: true, render: (r) => (r.ph_cost_cents ? <Money cents={r.ph_cost_cents} /> : '—'), csv: (r) => r.ph_cost_cents / 100, csvLabel: 'Public holiday pay ($)' },
    { key: 'all_approved', label: 'Approved', align: 'center', render: (r) => (r.all_approved ? <Badge tone="ok">Yes</Badge> : <Badge tone="warn">Not all</Badge>) },
  ];

  const salariedColumns: Column<SalariedRow>[] = [
    { key: 'staff_name', label: 'Person', value: (r) => personName(r.staff_name), nowrap: true },
    { key: 'pay_code', label: 'Pay code', render: (r) => r.pay_code || <Badge tone="error">Missing</Badge> },
    { key: 'hours', label: 'Hours', align: 'right', value: (r) => Number(r.hours), render: (r) => formatHours(r.hours) },
    { key: 'cost_cents', label: 'Cost', align: 'right', render: (r) => <Money cents={r.cost_cents} />, csv: (r) => r.cost_cents / 100, csvLabel: 'Cost ($)' },
    { key: 'by_shift_type', label: 'By shift type', hideOnPhone: true, sortable: false, value: (r) => Object.entries(r.by_shift_type || {}).map(([k, v]) => `${k}: ${typeof v === 'object' && v && 'hours' in (v as object) ? formatHours((v as { hours: number }).hours) : String(v)}`).join(', ') },
  ];

  const shiftColumns: Column<Fact>[] = [
    { key: 'trading_date', label: 'Day', value: (r) => r.trading_date, render: (r) => (r.trading_date ? dateLabel(r.trading_date, { year: false }) : '—'), nowrap: true },
    { key: 'staff_name', label: 'Person', value: (r) => personName(r.staff_name), nowrap: true },
    { key: 'role_name', label: 'Role', hideOnPhone: true },
    { key: 'clock', label: 'Clocked', sortable: false, value: (r) => clockLabel(r.clockin_at, r.clockout_at, timeZone), render: (r) => (isSalary(r.remuneration_type) || r.shift_type === 'Roster' ? <span style={{ color: 'var(--muted)' }}>Salary — no clock times</span> : clockLabel(r.clockin_at, r.clockout_at, timeZone)) },
    { key: 'hours', label: 'Hours', align: 'right', value: (r) => Number(r.hours), render: (r) => formatHours(r.hours) },
    { key: 'cost_cents', label: 'Cost', align: 'right', render: (r) => <Money cents={r.cost_cents} />, csv: (r) => r.cost_cents / 100, csvLabel: 'Cost ($)' },
    { key: 'ph_cost_cents', label: 'Public holiday pay', align: 'right', hideOnPhone: true, render: (r) => (r.ph_cost_cents ? <Money cents={r.ph_cost_cents} /> : '—'), csv: (r) => r.ph_cost_cents / 100 },
    { key: 'is_approved', label: 'Approved', align: 'center', render: (r) => (r.is_approved ? <Badge tone="ok">Yes</Badge> : <Badge tone="warn">No</Badge>) },
  ];

  const toolbar = (
    <>
      <Chip active={view === 'people' && chip === 'staff'} onClick={() => { setView('people'); setChip('staff'); setPerson(null); }}>Staff ({doc.staff.rows.length})</Chip>
      <Chip active={view === 'people' && chip === 'security'} onClick={() => { setView('people'); setChip('security'); setPerson(null); }}>Security contractors ({doc.security.rows.length})</Chip>
      <Chip active={view === 'people' && chip === 'salaried'} onClick={() => { setView('people'); setChip('salaried'); setPerson(null); }}>Salaried ({salaried.length})</Chip>
      <Chip active={view === 'shifts'} onClick={() => setView('shifts')}>Shifts at this venue ({here.length})</Chip>
      <input className="n-input" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Find a person or pay code" aria-label="Find a person or pay code" style={{ width: 200, height: 30 }} />
    </>
  );

  if (view === 'shifts') {
    return (
      <div>
        {person && (
          <div style={{ marginBottom: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
            Showing {personName(doc.staff.rows.find((r) => r.staff_member_id === person)?.staff_name || doc.security.rows.find((r) => r.staff_member_id === person)?.staff_name || 'one person')}’s shifts.{' '}
            <Button size="xs" variant="link" onClick={() => setPerson(null)}>Show everyone</Button>
          </div>
        )}
        <DataTable
          columns={shiftColumns}
          rows={shifts}
          rowKey={(r) => r.id}
          csvName={`Shifts at ${doc.entity.venue_name} ${weekLabel}`}
          toolbar={toolbar}
          initialSort={{ key: 'trading_date', dir: 'asc' }}
          emptyTitle="No shifts at this venue in this snapshot."
          footer={[`${plural(shiftTotals.shifts, 'shift')}`, null, null, null, formatHours(shiftTotals.hours), money(shiftTotals.cost_cents), shiftTotals.ph_cost_cents ? money(shiftTotals.ph_cost_cents) : null, null]}
          rowTone={(r) => (r.is_approved ? undefined : 'warn')}
        />
        <div style={{ marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          Shifts this entity pays for that happened at another venue are on the Intercompany page; leave is on the Leave tab.
        </div>
      </div>
    );
  }

  if (chip === 'salaried') {
    return (
      <DataTable columns={salariedColumns} rows={salaried.filter((r) => matchesSearch(search, r.staff_name, r.pay_code))} rowKey={(r) => r.staff_member_id} csvName={`Salaried ${doc.entity.venue_name} ${weekLabel}`} toolbar={toolbar} emptyTitle="No salaried staff in this snapshot." />
    );
  }

  return (
    <div>
      <DataTable
        columns={peopleColumns}
        rows={filtered}
        rowKey={(r) => r.staff_member_id}
        csvName={`${chip === 'security' ? 'Security' : 'Payrun'} ${doc.entity.venue_name} ${weekLabel}`}
        toolbar={toolbar}
        initialSort={{ key: 'staff_name', dir: 'asc' }}
        emptyTitle={chip === 'security' ? `No one with the pay code ${doc.security.pay_code} this week.` : 'No one to pay in this snapshot.'}
        footer={[plural(totals.people, 'person', 'people'), null, null, String(totals.shifts), formatHours(totals.hours), totals.other_venue_hours ? formatHours(totals.other_venue_hours) : null, totals.ph_hours ? formatHours(totals.ph_hours) : null, money(totals.cost_cents), totals.ph_cost_cents ? money(totals.ph_cost_cents) : null, null]}
        rowTone={(r) => (!r.pay_code ? 'error' : r.all_approved ? undefined : 'warn')}
      />
      {chip === 'security' && (
        <div style={{ marginTop: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          Security contractors are staff set up in Loaded with the pay code {doc.security.pay_code}. They are paid separately and left out of the wages export.
        </div>
      )}
    </div>
  );
}
