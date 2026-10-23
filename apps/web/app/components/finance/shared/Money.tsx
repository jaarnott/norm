'use client';

/**
 * Money in the organisation's currency. The currency is read once from the
 * company settings (`Organization.currency`, the way FinanceWeekly.tsx reads
 * it) and shared by every finance page; until it answers the default is NZD.
 */
import { useEffect, useState, type CSSProperties } from 'react';
import { apiFetch, getStoredUser } from '../../../lib/api';
import { formatCents, formatSignedCents } from './money';

let cached: Promise<string> | null = null;
let known: string | null = null;

/** The organisation's currency code (cached for the session). */
export function loadCurrency(): Promise<string> {
  if (!cached) {
    const orgId = getStoredUser()?.active_organization_id;
    cached = (orgId
      ? apiFetch(`/api/organizations/${orgId}/company-settings`)
        .then((r) => (r.ok ? r.json() : null))
        .then((s) => (s?.currency ? String(s.currency) : 'NZD'))
      : Promise.resolve('NZD'))
      .catch(() => 'NZD')
      .then((c) => { known = c; return c; });
  }
  return cached;
}

export function useCurrency(): string {
  const [currency, setCurrency] = useState<string>(known ?? 'NZD');
  useEffect(() => {
    let alive = true;
    void loadCurrency().then((c) => { if (alive) setCurrency(c); });
    return () => { alive = false; };
  }, []);
  return currency;
}

export type MoneyFn = (cents: number | null | undefined, dp?: 0 | 2) => string;

/** A formatter bound to the organisation's currency, for tables. */
export function useMoney(): MoneyFn {
  const currency = useCurrency();
  return (cents, dp = 2) => formatCents(cents, currency, dp);
}

/**
 * `<Money cents={123456} />` → $1,234.56. `signed` keeps a "+" on a
 * positive amount; `tone` colours it (a difference, a negative adjustment).
 */
export default function Money({ cents, dp = 2, signed = false, tone, style, title }: {
  cents: number | null | undefined;
  dp?: 0 | 2;
  signed?: boolean;
  tone?: 'ok' | 'warn' | 'error' | 'muted';
  style?: CSSProperties;
  title?: string;
}) {
  const currency = useCurrency();
  const text = signed ? formatSignedCents(cents, currency, dp) : formatCents(cents, currency, dp);
  const color = tone === 'ok' ? 'var(--ok)' : tone === 'warn' ? 'var(--warn)' : tone === 'error' ? 'var(--error)' : tone === 'muted' ? 'var(--muted)' : undefined;
  return <span style={{ fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', color, ...style }} title={title}>{text}</span>;
}
