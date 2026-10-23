'use client';

/**
 * Hygiene: the lists the Monday 20:00 email carries — unapproved clock-ins,
 * staff without a pay code, unfinished clock-ins (left out of the snapshot),
 * staff with no remuneration timeline, the salaried count, the week's
 * public holidays, and the parity line against Loaded's own pay summary.
 * A live read of Loaded when it can be reached; the snapshot's lists when
 * not. The fixes happen in Loaded, never here.
 */
import { Mail } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import PageState from '../../../ui/PageState';
import { Facts, Note, Section, Stack } from '../../shared/PageShell';
import { clockLabel, dateLabel, instantLabel, personName, plural } from '../../shared/format';
import { formatCents } from '../../shared/money';
import { useCurrency } from '../../shared/Money';
import { useFinQuery, type FinanceApi } from '../../shared/useFinanceApi';
import type { Hygiene, Week } from '../types';

function NameList({ title, names, empty }: { title: string; names: string[]; empty: string }) {
  return (
    <Section title={title} meta={names.length ? plural(names.length, 'person', 'people') : undefined} tone={names.length ? 'warn' : undefined}>
      {names.length === 0 ? <span style={{ color: 'var(--ok)', fontSize: 'var(--fs-sm)' }}>{empty}</span> : (
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 'var(--fs-sm)', columns: names.length > 8 ? 2 : 1 }}>
          {names.map((n) => <li key={n}>{personName(n)}</li>)}
        </ul>
      )}
    </Section>
  );
}

export default function HygieneTab({ week, api, canWrite, refresh }: { week: Week; api: FinanceApi; canWrite: boolean; refresh: number }) {
  const q = useFinQuery<Hygiene>(`/api/payroll/weeks/${week.id}/hygiene`, {}, refresh);
  const currency = useCurrency();
  const tz = week.window?.timezone || 'Pacific/Auckland';
  const h = q.data;

  if (q.loading && !h) return <PageState kind="loading" title="Reading Loaded…" />;
  if (q.error && !h) return <PageState kind="error" title="Could not read the hygiene lists." detail={q.error} action={<Button size="sm" onClick={q.retry}>Try again</Button>} />;
  if (!h) return null;

  const dollars = (v: unknown) => (typeof v === 'number' ? formatCents(Math.round(v * 100), currency) : '—');
  const emailBtn = canWrite ? (
    <Button size="sm" icon={Mail} onClick={() => api.run('hygiene-email', async () => {
      const r = await api.post<{ status: string; to: string | null }>(`/weeks/${week.id}/hygiene/email`);
      q.retry();
      return r;
    }, 'Hygiene email sent to the payroll contact.')} disabled={api.busy === 'hygiene-email'}>
      {api.busy === 'hygiene-email' ? 'Sending…' : 'Email these lists'}
    </Button>
  ) : undefined;

  return (
    <Stack>
      {!h.live && <Note tone="warn">Loaded could not be read just now{h.error ? ` (${h.error})` : ''}. These lists come from the snapshot; a live read is better.</Note>}
      <Section title="This week's check" actions={emailBtn}>
        <Facts items={[
          { label: 'Snapshots taken', value: `${h.snapshot_report.taken} of ${h.snapshot_report.of} entities${h.snapshot_report.failed.length ? ` — failed: ${h.snapshot_report.failed.map((f) => `${f.venue_name ?? 'a venue'} (${f.error})`).join('; ')}` : ''}` },
          { label: 'This venue’s snapshot', value: h.snapshot ? `${instantLabel(h.snapshot.taken_at, tz)} · ${h.snapshot.shift_count} shifts · ${h.snapshot.dropped_deleted} deleted and ${h.snapshot.dropped_unfinished} unfinished left out` : 'Not taken yet' },
          { label: 'Salaried staff', value: String(h.salaried_count) },
          { label: 'Public holidays in the week', value: h.public_holiday_dates.length ? h.public_holiday_dates.map((d) => dateLabel(d, { year: false })).join(', ') : 'None' },
          { label: 'Hygiene email', value: h.email.last_sent_at ? `Sent ${instantLabel(h.email.last_sent_at, tz)} to ${h.email.to ?? '—'}${h.email.status && h.email.status !== 'sent' ? ` (${h.email.status})` : ''}` : 'Not sent yet' },
          { label: 'Parity with Loaded’s pay summary', value: h.parity ? (
            <span>
              {h.parity.within_tolerance ? <Badge tone="ok">Matches</Badge> : <Badge tone="error">Differs</Badge>}{' '}
              rows {dollars(h.parity.rows_wages)} · Loaded {dollars(h.parity.pay_summary_total_wages)} · difference {dollars(h.parity.difference)}
            </span>
          ) : 'Not available' },
        ]} />
      </Section>

      <Section title="Clock-ins not yet approved" meta={h.unapproved_clockins.length ? plural(h.unapproved_clockins.length, 'shift') : undefined} tone={h.unapproved_clockins.length ? 'warn' : undefined}>
        {h.unapproved_clockins.length === 0 ? <span style={{ color: 'var(--ok)', fontSize: 'var(--fs-sm)' }}>Every clock-in is approved.</span> : (
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 'var(--fs-sm)' }}>
            {h.unapproved_clockins.map((c, i) => <li key={i}>{personName(c.staff_name)} — {clockLabel(c.clockin_at, c.clockout_at, tz)}</li>)}
          </ul>
        )}
      </Section>
      <NameList title="Staff without a pay code" names={h.staff_without_pay_code} empty="Everyone has a pay code." />
      <Section title="Unfinished clock-ins (left out of the snapshot)" meta={h.unfinished_clockins.length ? plural(h.unfinished_clockins.length, 'shift') : undefined} tone={h.unfinished_clockins.length ? 'warn' : undefined}>
        {h.unfinished_clockins.length === 0 ? <span style={{ color: 'var(--ok)', fontSize: 'var(--fs-sm)' }}>None.</span> : (
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 'var(--fs-sm)' }}>
            {h.unfinished_clockins.map((c, i) => <li key={i}>{personName(c.staff_name)} — {clockLabel(c.clockin_at, null, tz)}</li>)}
          </ul>
        )}
      </Section>
      <NameList title="Staff with no pay rate on the shift date" names={h.staff_without_timeline} empty="Everyone has a pay rate in Loaded." />
      <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Fix these in Loaded, then take the snapshot again. Norm approves no clock-ins.</div>
    </Stack>
  );
}
