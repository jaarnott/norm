'use client';

/**
 * What the customer sees under the proposal on the quote page: Norm's own
 * option panel, drawn from the option records (never from the HTML), each
 * with Confirm / Decline when it can be confirmed. Informational here.
 */
import Badge from '../../ui/Badge';
import { dateLabel, money, timeRangeLabel } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { optionBadge } from './proposalDoc';
import type { OptionSnapshot } from './types';

const pill = {
  display: 'inline-flex',
  alignItems: 'center',
  height: 26,
  padding: '0 10px',
  borderRadius: 'var(--radius-sm)',
  fontSize: 'var(--fs-xs)',
  fontWeight: 600,
} as const;

export default function OfferedOptions({ options, meta, title }: { options: OptionSnapshot[]; meta: FnMeta; title: string }) {
  const currency = meta.company.currency;
  const venueName = (id: string | null) => meta.venues.find((v) => v.id === id)?.name ?? null;
  return (
    <section aria-label={title}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
        <h3 style={{ margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600 }}>{title}</h3>
        <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Drawn from the recorded options, under the proposal. Amounts {meta.company.tax_label}.</span>
      </div>
      {options.length === 0 ? (
        <div className="n-card" style={{ padding: '12px 14px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
          No options on offer — add minimum-spend options on the enquiry, or the customer has nothing to confirm.
        </div>
      ) : (
        <ul className="n-card" style={{ listStyle: 'none', margin: 0, padding: 0, overflow: 'hidden' }}>
          {options.map((o, i) => {
            const badge = optionBadge(o);
            const when = [o.event_date ? dateLabel(o.event_date) : 'No date yet', timeRangeLabel(o.start_time, o.end_time, o.ends_next_day)]
              .filter(Boolean)
              .join(', ');
            return (
              <li key={o.min_spend_id} style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', padding: '10px 14px', borderTop: i ? '1px solid var(--line)' : 'none' }}>
                <div style={{ flex: '1 1 220px', minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 600 }}>{o.label || o.area_name || 'Option'}</span>
                    <Badge tone={badge.tone}>{badge.label}</Badge>
                  </div>
                  <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                    {[venueName(o.venue_id), o.label && o.area_name ? o.area_name : null, when].filter(Boolean).join(' · ')}
                  </div>
                </div>
                <div style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                  <div style={{ fontWeight: 600 }}>{money(o.total_cents, currency, 0)}</div>
                  <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                    min spend {money(o.amount_cents, currency, 0)}
                    {o.food_cents ? ` · food ${money(o.food_cents, currency, 0)}` : ''}
                  </div>
                </div>
                <div aria-hidden="true" style={{ display: 'flex', gap: 6 }}>
                  {o.confirmable ? (
                    <>
                      <span style={{ ...pill, background: 'var(--primary)', color: 'var(--on-primary)' }}>Confirm</span>
                      <span style={{ ...pill, border: '1px solid var(--line-strong)', color: 'var(--text)' }}>Decline</span>
                    </>
                  ) : (
                    <span style={{ ...pill, color: 'var(--muted)', border: '1px dashed var(--line-strong)' }}>Not confirmable yet</span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
