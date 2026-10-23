'use client';

/** Small presentational pieces shared by the Stocktakes page's views. */

import React, { type ReactNode } from 'react';
import { Info, TriangleAlert } from 'lucide-react';
import Icon from '../../ui/Icon';
import { formatMoney } from '../../../lib/format';
import { formatPct, type CategoryValue } from './logic';

/** A section heading with an optional line under it and something on the right. */
export function Section({ title, sub, aside, children }: { title: string; sub?: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return (
    <section style={{ marginTop: 20 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
        <div style={{ minWidth: 0 }}>
          <h2 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, color: 'var(--text)' }}>{title}</h2>
          {sub && <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{sub}</div>}
        </div>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** One soft line of explanation (info) or caution (warn). */
export function Note({ tone = 'info', children }: { tone?: 'info' | 'warn'; children: ReactNode }) {
  const warn = tone === 'warn';
  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 8, padding: '8px 12px', marginTop: 8,
      borderRadius: 'var(--radius)', fontSize: 'var(--fs-sm)',
      background: warn ? 'var(--warn-bg)' : 'var(--surface-alt)', color: warn ? 'var(--warn)' : 'var(--text-soft)',
    }}>
      <Icon icon={warn ? TriangleAlert : Info} size={14} style={{ marginTop: 2, flex: '0 0 auto' }} />
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}

/** Value by category as a ranked list of bars (one hue; the share is the
 *  length). `compact` (phones) puts the bar under its label and figure. */
export function CategoryBars({ rows, total, compact = false }: { rows: CategoryValue[]; total: number; compact?: boolean }) {
  const max = rows.reduce((m, r) => Math.max(m, r.value), 0) || 1;
  const bar = (r: CategoryValue) => (
    <span aria-hidden="true" style={{ display: 'block', height: 8, borderRadius: 4, background: 'var(--surface-alt)', overflow: 'hidden', gridColumn: compact ? '1 / -1' : undefined }}>
      <span style={{ display: 'block', height: '100%', width: `${Math.max(2, (r.value / max) * 100)}%`, background: 'var(--brand-soft)', borderRadius: 4 }} />
    </span>
  );
  return (
    <div className="n-card" style={{ padding: '6px 14px' }}>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {rows.map((r) => (
          <li
            key={r.category}
            style={{
              display: 'grid',
              gridTemplateColumns: compact ? '1fr auto' : 'minmax(80px, 160px) 1fr 118px',
              alignItems: 'center',
              gap: compact ? '4px 12px' : 12,
              padding: '7px 0',
              borderBottom: '1px solid var(--line-soft)',
            }}
          >
            <span style={{ fontSize: 'var(--fs-base)', color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={`${r.items} item${r.items === 1 ? '' : 's'}`}>
              {r.category}
            </span>
            {!compact && bar(r)}
            <span style={{ fontSize: 'var(--fs-base)', fontVariantNumeric: 'tabular-nums', textAlign: 'right', whiteSpace: 'nowrap' }}>
              <span style={{ fontWeight: 600 }}>{formatMoney(r.value, 0)}</span>
              <span style={{ color: 'var(--muted)', marginLeft: 6, display: 'inline-block', minWidth: 42 }}>{formatPct(r.share, 0)}</span>
            </span>
            {compact && bar(r)}
          </li>
        ))}
        <li style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0 4px', fontSize: 'var(--fs-base)', fontWeight: 600 }}>
          <span>Total</span>
          <span style={{ fontVariantNumeric: 'tabular-nums' }}>{formatMoney(total, 0)}</span>
        </li>
      </ul>
    </div>
  );
}

/** Money with a real minus in front ("−$21"), never "$-21". */
export function money(n: number, dp = 0): string {
  return n < -0.004 ? `−${formatMoney(Math.abs(n), dp)}` : formatMoney(Math.abs(n) < 0.005 ? 0 : n, dp);
}

/** A money figure coloured by sign: a shortfall reads red, a surplus plain with a "+". */
export function SignedMoney({ value, dp = 0, strong = false }: { value: number; dp?: number; strong?: boolean }) {
  const neg = value < -0.004;
  const pos = value > 0.004;
  return (
    <span style={{ color: neg ? 'var(--error)' : 'var(--text)', fontWeight: strong ? 600 : undefined, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
      {neg ? '−' : pos ? '+' : ''}{formatMoney(Math.abs(value), dp)}
    </span>
  );
}
