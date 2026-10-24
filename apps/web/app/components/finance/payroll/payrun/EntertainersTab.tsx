'use client';

/**
 * The Payroll page's Entertainers tab: performers paid through payroll —
 * approved performances not yet paid, and those marked paid in this week —
 * from the week's pay-run snapshot. Marking paid happens on the
 * Entertainers page's Payments tab, linked here for people who can pay.
 */
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import DataTable, { type Column } from '../../shared/DataTable';
import Money, { useMoney } from '../../shared/Money';
import { Note, Stack } from '../../shared/PageShell';
import { formatHours, plural } from '../../shared/format';
import { useOpenTab } from '../../shared/useWorkspaceTab';
import { weLabel } from '../../shared/weeks';
import type { EntertainerLine, PayrunDoc } from '../types';

export default function EntertainersTab({ doc, weekStart, canPay }: { doc: PayrunDoc; weekStart: string; canPay: boolean }) {
  const money = useMoney();
  const openEntertainers = useOpenTab('entertainers');
  const rows = doc.entertainers.lines;
  const columns: Column<EntertainerLine>[] = [
    { key: 'performer', label: 'Performer', render: (r) => r.performer || r.performance_id, nowrap: true },
    { key: 'pay_code', label: 'Payroll code', render: (r) => r.pay_code || <Badge tone="error">Missing</Badge> },
    { key: 'hours', label: 'Hours', align: 'right', hideOnPhone: true, value: (r) => Number(r.hours), render: (r) => formatHours(r.hours) },
    { key: 'rate_cents', label: 'Rate', align: 'right', hideOnPhone: true, render: (r) => <Money cents={r.rate_cents} />, csv: (r) => r.rate_cents / 100 },
    { key: 'amount_cents', label: 'Amount', align: 'right', render: (r) => <Money cents={r.amount_cents} />, csv: (r) => r.amount_cents / 100 },
    { key: 'payment_status', label: 'State', render: (r) => (r.payment_status === 'paid' ? <Badge tone="ok">Paid this week</Badge> : r.payment_status ? <Badge tone="warn">{r.payment_status}</Badge> : null) },
  ];
  return (
    <Stack>
      {!doc.entertainers.available && <Note tone="warn">The Entertainers module is not available yet, so no entertainer lines are in this pay run.</Note>}
      <Note tone="plain">
        Performers paid through payroll: approved performances not yet paid, and those marked paid in this week.{' '}
        {canPay ? (
          <Button size="xs" variant="link" onClick={() => openEntertainers('payments', { venue: doc.entity.venue_id })}>Mark them paid on the Entertainers page, Payments tab</Button>
        ) : (
          'They are marked paid on the Entertainers page, Payments tab.'
        )}
      </Note>
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.performance_id} csvName={`Entertainers via payroll ${doc.entity.venue_name} ${weLabel(weekStart)}`} emptyTitle="No entertainer lines this week." footer={[plural(rows.length, 'performance'), null, null, null, money(rows.reduce((s, r) => s + r.amount_cents, 0)), null]} />
    </Stack>
  );
}
