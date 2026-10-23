'use client';

/**
 * A per-performer statement: their approved performances in the chosen
 * range, hours, amounts, invoice and payment state, with totals — the
 * sheet finance sends with a payment or checks an invoice against. Prints
 * on its own (browser print).
 */
import { useId } from 'react';
import { Printer } from 'lucide-react';
import Button from '../../../ui/Button';
import { dateLabel, money, timeRangeLabel } from '../format';
import { hoursLabel, sumCents } from '../logic';
import { CheckChip, LaneBadge } from '../ui';
import type { PaymentRow } from '../types';

export default function StatementView({ rows, performerId, onPerformer, onOpen }: {
  rows: PaymentRow[];
  performerId: string | null;
  onPerformer: (id: string | null) => void;
  onOpen: (row: PaymentRow) => void;
}) {
  const selectId = useId();
  const performers = Array.from(new Map(rows.map((r) => [r.performer_id, r.performer_name])).entries()).sort((a, b) => a[1].localeCompare(b[1]));
  const mine = performerId ? rows.filter((r) => r.performer_id === performerId).sort((a, b) => (a.performance_date < b.performance_date ? -1 : 1)) : [];
  const name = performers.find(([id]) => id === performerId)?.[1];
  const total = sumCents(mine, (r) => r.actual_cents ?? r.agreed_cents);
  const paidTotal = sumCents(mine.filter((r) => r.payment_status === 'paid'), (r) => r.actual_cents ?? r.agreed_cents);

  return (
    <div>
      <div className="fp-no-print" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        <label htmlFor={selectId} className="n-label" style={{ margin: 0 }}>Performer</label>
        <select id={selectId} className="n-select" value={performerId ?? ''} onChange={(e) => onPerformer(e.target.value || null)}>
          <option value="">Choose a performer</option>
          {performers.map(([id, n]) => <option key={id} value={id}>{n}</option>)}
        </select>
        {performerId && <Button size="sm" icon={Printer} onClick={() => window.print()}>Print</Button>}
      </div>
      {!performerId ? (
        <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>Pick a performer to see their statement for the dates and venue chosen above.</div>
      ) : (
        <div className="n-card" style={{ padding: '14px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
            <h2 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600 }}>{name}</h2>
            <LaneBadge lane={mine[0]?.lane ?? null} />
            <span style={{ marginLeft: 'auto', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{mine.length} performance{mine.length === 1 ? '' : 's'}</span>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="n-table" style={{ minWidth: 640 }}>
              <thead>
                <tr><th>Date</th><th>Venue</th><th>Times</th><th className="num">Hours</th><th className="num">Rate</th><th className="num">Amount</th><th>Invoice</th><th>Paid</th></tr>
              </thead>
              <tbody>
                {mine.map((r) => (
                  <tr key={r.id} onClick={() => onOpen(r)} style={{ cursor: 'pointer' }}>
                    <td style={{ whiteSpace: 'nowrap' }}>{dateLabel(r.performance_date, { year: false })}</td>
                    <td>{r.venue_name}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>{timeRangeLabel(r.actual_start_time ?? r.start_time, r.actual_end_time ?? r.end_time, !!(r.actual_ends_next_day ?? r.ends_next_day))}</td>
                    <td className="num">{hoursLabel(r.minutes.actual ?? r.minutes.booked)}</td>
                    <td className="num">{money(r.rate_cents)}</td>
                    <td className="num">{money(r.actual_cents ?? r.agreed_cents)}</td>
                    <td>{r.lane === 'invoice' || r.lane === 'exemption' ? <CheckChip status={r.invoice_check_status} /> : <span style={{ color: 'var(--muted)' }}>n/a</span>}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>{r.payment_status === 'paid' ? `${dateLabel(r.paid_at?.slice(0, 10) ?? null, { year: false })}${r.paid_reference ? ` · ${r.paid_reference}` : ''}` : <span style={{ color: 'var(--muted)' }}>Pending</span>}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={5} style={{ textAlign: 'right', fontWeight: 600, padding: '10px 12px' }}>Total</td>
                  <td className="num" style={{ fontWeight: 600 }}>{money(total)}</td>
                  <td colSpan={2} style={{ color: 'var(--text-soft)', fontSize: 'var(--fs-sm)' }}>paid {money(paidTotal)} · outstanding {money(total - paidTotal)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
