'use client';

/**
 * Enquiries received per day, against the same-length period before it —
 * each day's bar in the accent colour beside the matching earlier day in
 * grey (`GET /reports/enquiries`). The table under the chart has every
 * day's numbers.
 */
import { useState } from 'react';
import Chart, { DEFAULT_COLORS } from '../../display/Chart';
import Button from '../../ui/Button';
import { dateLabel } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { StatTile, TileRow } from '../enquiries/bits';
import { stageLabel } from '../enquiries/labels';
import { periodFor, type Period } from './dates';
import { FilterRow, PeriodPicker, ReportBody, useReport } from './ReportBits';
import type { EnquiriesOverTime } from './types';

export default function EnquiriesReport({ meta, venue }: { meta: FnMeta; venue: string }) {
  const tz = meta.company.default_timezone;
  const [period, setPeriod] = useState<Period>(() => periodFor('28d', tz));
  const [stage, setStage] = useState('');
  const [table, setTable] = useState(false);
  const state = useReport<EnquiriesOverTime>('/reports/enquiries', {
    from: period.from, to: period.to, venue: venue === 'all' ? undefined : venue, stage: stage || undefined,
  });

  return (
    <div>
      <FilterRow>
        <PeriodPicker period={period} onChange={setPeriod} timeZone={tz} />
        <select className="n-select" aria-label="Stage" value={stage} onChange={(e) => setStage(e.target.value)}>
          <option value="">Every stage</option>
          {(['new', 'replied', 'quoted', 'confirmed', 'lost'] as const).map((s) => <option key={s} value={s}>Now {stageLabel(s).toLowerCase()}</option>)}
        </select>
      </FilterRow>
      <ReportBody state={state} what="enquiries">
        {(d) => {
          const thisLabel = `${dateLabel(d.from, { weekday: false })} – ${dateLabel(d.to, { weekday: false })}`;
          const priorLabel = `${dateLabel(d.prior_from, { weekday: false })} – ${dateLabel(d.prior_to, { weekday: false })}`;
          return (
            <>
              <TileRow min={200}>
                <StatTile label="Enquiries received" value={d.total} changePct={d.change_pct} changeLabel={`vs ${d.prior_total} the period before`} sub={thisLabel} />
                <StatTile label="The period before" value={d.prior_total} sub={priorLabel} />
                <StatTile label="Difference" value={`${d.change > 0 ? '+' : ''}${d.change}`} sub="Enquiries, this period minus the one before" />
              </TileRow>
              <Chart
                data={{ rows: d.days.map((day) => ({ day: dateLabel(day.date, { year: false }), current: day.count, prior: day.prior_count })) }}
                props={{
                  chart_type: 'bar',
                  title: 'Enquiries per day',
                  x_axis: { key: 'day', label: 'Day' },
                  y_axis: { key: 'current', label: 'Enquiries', format: 'number' },
                  series: [
                    { key: 'current', label: `This period (${thisLabel})`, color: DEFAULT_COLORS[0] },
                    // The period before is context, so it's grey (emphasis on now).
                    { key: 'prior', label: `Period before (${priorLabel})`, color: 'var(--icon)' },
                  ],
                }}
                hideAddToReport
                height={280}
              />
              <div style={{ marginTop: 10 }}>
                <Button size="sm" variant="quiet" onClick={() => setTable((t) => !t)} aria-expanded={table}>{table ? 'Hide the daily numbers' : 'Show the daily numbers'}</Button>
              </div>
              {table && (
                <div style={{ overflowX: 'auto', marginTop: 8 }}>
                  <table className="n-table" aria-label="Enquiries per day" style={{ fontSize: 'var(--fs-sm)' }}>
                    <thead>
                      <tr>
                        <th scope="col">Day</th>
                        <th scope="col" className="num">Enquiries</th>
                        <th scope="col">Same day, period before</th>
                        <th scope="col" className="num">Enquiries</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.days.map((day) => (
                        <tr key={day.date}>
                          <td>{dateLabel(day.date)}</td>
                          <td className="num">{day.count}</td>
                          <td style={{ color: 'var(--text-soft)' }}>{dateLabel(day.prior_date)}</td>
                          <td className="num">{day.prior_count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          );
        }}
      </ReportBody>
    </div>
  );
}
