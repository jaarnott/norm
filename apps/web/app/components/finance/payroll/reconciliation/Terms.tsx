'use client';

/**
 * The nine-term formula as the server computed it, each term a row that
 * opens to the rows behind it: the shifts (fetched from the close
 * snapshot's pay run when a term is opened), the payouts, the adjustments,
 * the entertainer lines, the gratuity shares, the counterparties.
 */
import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Icon from '../../../ui/Icon';
import PageState from '../../../ui/PageState';
import DataTable, { type Column } from '../../shared/DataTable';
import Money, { useMoney } from '../../shared/Money';
import { bandColor, bandLabel, bandTone } from '../../shared/bands';
import { clockLabel, dateLabel, formatHours, personName, plural } from '../../shared/format';
import { useFinQuery } from '../../shared/useFinanceApi';
import type { Fact, PayrunDoc, Reconciliation } from '../types';

interface Term {
  key: string;
  label: string;
  sign: '+' | '−' | '=';
  cents: number;
  meta?: string;
  detail?: React.ReactNode;
  factIds?: string[];
}

function FactRows({ weekId, ids, timeZone }: { weekId: string; ids: string[]; timeZone: string }) {
  const q = useFinQuery<PayrunDoc>(`/api/payroll/weeks/${weekId}/payrun`, { snapshot: 'close' });
  if (q.error && !q.data) return <PageState kind="error" title="Could not load the shifts." detail={q.error} />;
  if (!q.data) return <PageState kind="loading" title="Loading the shifts…" />;
  const wanted = new Set(ids);
  const rows = q.data.facts.filter((f) => wanted.has(f.id));
  const columns: Column<Fact>[] = [
    { key: 'trading_date', label: 'Day', render: (r) => (r.trading_date ? dateLabel(r.trading_date, { year: false }) : '—'), nowrap: true },
    { key: 'staff_name', label: 'Person', value: (r) => personName(r.staff_name), nowrap: true },
    { key: 'role_name', label: 'Role', hideOnPhone: true },
    { key: 'cost_group', label: 'Cost group', hideOnPhone: true, render: (r) => (r.cost_group === 'bar_floor' ? 'Bar & floor' : r.cost_group === 'kitchen' ? 'Kitchen' : 'Other') },
    { key: 'clock', label: 'Clocked', sortable: false, hideOnPhone: true, value: (r) => clockLabel(r.clockin_at, r.clockout_at, timeZone) },
    { key: 'hours', label: 'Hours', align: 'right', value: (r) => Number(r.hours), render: (r) => formatHours(r.hours) },
    { key: 'cost_cents', label: 'Cost', align: 'right', render: (r) => <Money cents={r.cost_cents} />, csv: (r) => r.cost_cents / 100 },
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.id} dense maxHeight={360} csvName="Reconciliation shifts" initialSort={{ key: 'trading_date', dir: 'asc' }} emptyTitle="No shifts behind this term." />;
}

export default function Terms({ recon, timeZone }: { recon: Reconciliation; timeZone: string }) {
  const money = useMoney();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const t = recon.terms;
  const weekId = recon.week_id ?? null;

  const terms: Term[] = [
    { key: 'timeclock', label: 'Timeclock — shifts worked at this venue', sign: '+', cents: t.timeclock.cents, meta: `${plural(t.timeclock.shifts, 'shift')} · ${formatHours(t.timeclock.hours)} · bar & floor ${money(t.timeclock.by_cost_group.bar_floor ?? 0)}, kitchen ${money(t.timeclock.by_cost_group.kitchen ?? 0)}, other ${money(t.timeclock.by_cost_group.other ?? 0)}`, factIds: t.timeclock.fact_ids },
    { key: 'intercompany', label: 'Intercompany (In − Out)', sign: '+', cents: t.intercompany.cents, meta: `in ${money(t.intercompany.in_cents)} · out ${money(t.intercompany.out_cents)}`, detail: (
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 'var(--fs-sm)' }}>
        {t.intercompany.counterparties.map((c, i) => <li key={i}>{c.counterparty}: {plural(c.lines.length, 'person', 'people')} · {formatHours(c.hours)} · {money(c.amount_cents)}</li>)}
        {t.intercompany.counterparties.length === 0 && <li style={{ color: 'var(--muted)' }}>No one crossed between entities.</li>}
      </ul>
    ) },
    { key: 'leave_paid', label: 'Leave paid (as decided)', sign: '+', cents: t.leave_paid.cents, meta: `requested ${money(t.leave_paid.requested_cents)}${t.leave_paid.undecided_request_ids?.length ? ` · ${t.leave_paid.undecided_request_ids.length} not decided` : ''}`, detail: (
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 'var(--fs-sm)' }}>
        {t.leave_paid.rows.map((r) => <li key={r.id}>{personName(r.staff_name)} — {r.leave_type ?? 'leave'}: paid {money(r.paid_cents)} of {money(r.requested_cents)}</li>)}
        {t.leave_paid.rows.length === 0 && <li style={{ color: 'var(--muted)' }}>No payouts decided.</li>}
      </ul>
    ) },
    { key: 'security', label: `Security contractors (pay code ${t.security.pay_code})`, sign: '−', cents: t.security.cents, factIds: t.security.fact_ids },
    { key: 'accrued_additions', label: 'Accrued this week (earned, not yet paid)', sign: '−', cents: t.accrued_additions.cents, meta: `opening ${money(t.accrual.opening_cents)} (${t.accrual.opening_from}) · closing ${money(t.accrual.closing_cents)}` },
    { key: 'accrued_deductions', label: 'Accruals paid out this week', sign: '+', cents: t.accrued_deductions.cents },
    { key: 'other_adjustments', label: 'Other adjustments', sign: '+', cents: t.other_adjustments.cents, detail: (
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 'var(--fs-sm)' }}>
        {t.other_adjustments.rows.map((r) => <li key={r.id}>{r.label}: {money(r.amount_cents)}{r.xero_account_code ? ` → ${r.xero_account_code}` : ' (no Xero account)'}</li>)}
        {t.other_adjustments.rows.length === 0 && <li style={{ color: 'var(--muted)' }}>None.</li>}
      </ul>
    ) },
    { key: 'entertainers', label: 'Entertainers paid through payroll', sign: '+', cents: t.entertainers.cents, meta: t.entertainers.available ? undefined : 'Entertainers module not available', detail: (
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 'var(--fs-sm)' }}>
        {t.entertainers.rows.map((r) => <li key={r.performance_id}>{r.pay_code ?? r.performance_id}: {formatHours(r.hours)} × {money(r.rate_cents)} = {money(r.amount_cents)}</li>)}
        {t.entertainers.rows.length === 0 && <li style={{ color: 'var(--muted)' }}>None.</li>}
      </ul>
    ) },
    { key: 'gratuities', label: 'Gratuities earned here', sign: '+', cents: t.gratuities.cents, detail: (
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 'var(--fs-sm)' }}>
        {t.gratuities.rows.map((r) => <li key={r.allocation_id}>{personName(r.staff_name)}: {money(r.amount_cents)}</li>)}
        {t.gratuities.rows.length === 0 && <li style={{ color: 'var(--muted)' }}>None.</li>}
      </ul>
    ) },
  ];

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="n-table" style={{ width: '100%', minWidth: 480 }}>
        <thead>
          <tr><th style={{ width: 24 }} /><th>Term</th><th className="num">Amount</th></tr>
        </thead>
        <tbody>
          {terms.map((term) => {
            const isOpen = open[term.key] ?? false;
            const expandable = !!term.detail || (!!term.factIds && term.factIds.length > 0);
            return (
              <TermRow key={term.key} term={term} open={isOpen} expandable={expandable} onToggle={() => setOpen((o) => ({ ...o, [term.key]: !isOpen }))} weekId={weekId} timeZone={timeZone} />
            );
          })}
          <tr style={{ background: 'var(--surface)' }}>
            <td /><td style={{ fontWeight: 600 }}>Norm&rsquo;s wages cost (the terms added up)</td><td className="num" style={{ fontWeight: 600 }}>{money(recon.computed_cents)}</td>
          </tr>
          <tr>
            <td /><td>Gross wages from ACE</td><td className="num">{recon.ace ? money(recon.ace.gross_cents) : <Badge tone="warn">not entered</Badge>}</td>
          </tr>
          <tr style={{ background: 'var(--surface)' }}>
            <td /><td style={{ fontWeight: 600 }}>Difference between Norm and ACE</td>
            <td className="num" style={{ fontWeight: 700, color: bandColor(recon.band) }}>
              {recon.difference_cents === null ? '—' : <Money cents={recon.difference_cents} signed />} <Badge tone={bandTone(recon.band)}>{bandLabel(recon.band)}</Badge>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function TermRow({ term, open, expandable, onToggle, weekId, timeZone }: { term: Term; open: boolean; expandable: boolean; onToggle: () => void; weekId: string | null; timeZone: string }) {
  const money = useMoney();
  return (
    <>
      <tr onClick={expandable ? onToggle : undefined} style={{ cursor: expandable ? 'pointer' : undefined }}>
        <td style={{ color: 'var(--muted)' }}>{expandable ? <Icon icon={open ? ChevronDown : ChevronRight} size="dense" label={open ? 'Hide rows' : 'Show rows'} /> : null}</td>
        <td>
          <span style={{ color: 'var(--muted)', marginRight: 6, fontVariantNumeric: 'tabular-nums' }}>{term.sign}</span>{term.label}
          {term.meta && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{term.meta}</div>}
        </td>
        <td className="num" style={{ fontVariantNumeric: 'tabular-nums' }}>{term.sign === '−' ? `−${money(term.cents)}` : money(term.cents)}</td>
      </tr>
      {open && expandable && (
        <tr>
          <td /><td colSpan={2} style={{ background: 'var(--surface)' }}>
            {term.detail}
            {term.factIds && term.factIds.length > 0 && (weekId ? <FactRows weekId={weekId} ids={term.factIds} timeZone={timeZone} /> : <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>No week record yet.</span>)}
          </td>
        </tr>
      )}
    </>
  );
}
