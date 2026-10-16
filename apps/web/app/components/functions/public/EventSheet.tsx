'use client';

import type { ReactNode } from 'react';
import Badge from '../../ui/Badge';
import { dateLabel, money, timeLabel, timeRangeLabel } from '../shared/format';
import {
  AV_LABELS,
  DEPOSIT_LABELS,
  ENTERTAINMENT_LABELS,
  PAYMENT_LABELS,
  SECTIONS,
  SECTION_LABELS,
  STATUS_LABELS,
  fieldLabel,
  foodTimingLabel,
  parseRestrictions,
} from './portalFields';
import { menuValueCents } from './portalModel';
import type { ListRow, PortalPayload } from './types';

/**
 * The printable event sheet (/e/<token>/print): everything the portal shows,
 * read-only and compact, laid out for A4. Shows only what the API returned,
 * so hidden fields stay hidden on paper too.
 */

const str = (v: unknown) => (typeof v === 'string' ? v.trim() : '');
const t = (v: unknown) => (typeof v === 'string' && v ? timeLabel(v) : '');

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="fp-avoid-break" style={{ padding: '14px 0', borderTop: '1px solid var(--line)' }}>
      <h2 style={{ margin: '0 0 8px', fontSize: 'var(--fs-md)', fontWeight: 700, color: 'var(--text)' }}>{title}</h2>
      {children}
    </section>
  );
}

function Pairs({ rows }: { rows: [string, ReactNode][] }) {
  const shown = rows.filter(([, v]) => v !== null && v !== undefined && v !== '' && v !== false);
  if (!shown.length) return null;
  return (
    <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'minmax(120px, 30%) 1fr', columnGap: 16, rowGap: 6, fontSize: 'var(--fs-base)' }}>
      {shown.map(([label, value]) => (
        <div key={label} style={{ display: 'contents' }}>
          <dt style={{ color: 'var(--muted)' }}>{label}</dt>
          <dd style={{ margin: 0, color: 'var(--text)', whiteSpace: 'pre-line', overflowWrap: 'anywhere' }}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  if (!rows.length) return null;
  return (
    <table className="n-table" style={{ marginTop: 8 }}>
      <thead>
        <tr>{head.map((h, i) => <th key={h} className={i > 0 && /Qty|Each|Total/.test(h) ? 'num' : undefined}>{h}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>{r.map((c, j) => <td key={j} className={j > 0 && /Qty|Each|Total/.test(head[j]) ? 'num' : undefined}>{c}</td>)}</tr>
        ))}
      </tbody>
    </table>
  );
}

const windowOf = (row: ListRow) => timeRangeLabel(str(row.start_time) || null, str(row.end_time) || null, row.ends_next_day === true);

export default function EventSheet({ payload }: { payload: PortalPayload }) {
  const ev = payload.event;
  const cur = payload.money.currency;
  const lists = payload.lists ?? {};
  const has = (k: string) => k in ev;
  const deposit = typeof ev.deposit_cents === 'number' && ev.deposit_cents > 0
    ? `${money(ev.deposit_cents, cur)}${typeof ev.deposit_status === 'string' && DEPOSIT_LABELS[ev.deposit_status] ? ` (${DEPOSIT_LABELS[ev.deposit_status].toLowerCase()})` : ''}`
    : null;
  const restrictions = parseRestrictions(ev.bar_tab_restrictions);
  const tableOrdering = has('table_ordering')
    ? ev.table_ordering === true
      ? `Yes${typeof ev.table_ordering_limit_cents === 'number' ? `, up to ${money(ev.table_ordering_limit_cents, cur)}` : ''}`
      : 'No'
    : null;
  const food = foodTimingLabel(ev.food_timing_type);
  const foodTimes = [t(ev.food_time) && `from ${t(ev.food_time)}`, t(ev.food_end_time) && `until ${t(ev.food_end_time)}`].filter(Boolean).join(' ');
  const menus = payload.menus ?? [];
  const foodTotal = menus.reduce((sum, m) => sum + menuValueCents(m, undefined), 0);

  return (
    <article style={{ background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', padding: '20px 20px 8px' }}>
      <header style={{ paddingBottom: 12 }}>
        <div className="n-eyebrow">Event sheet</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginTop: 2 }}>
          <h1 style={{ margin: 0, fontSize: 'var(--fs-xl)', fontWeight: 700, color: 'var(--text)' }}>{ev.name}</h1>
          <Badge tone={payload.status.status === 'confirmed' ? 'ok' : payload.status.status === 'cancelled' ? 'error' : 'neutral'}>{STATUS_LABELS[payload.status.status]}</Badge>
        </div>
        <Pairs
          rows={[
            ['Date', dateLabel(ev.event_date)],
            ['Time', timeRangeLabel(ev.start_time, ev.end_time, ev.ends_next_day)],
            ['Guests', typeof ev.guests === 'number' ? String(ev.guests) : ''],
            ['Venue', payload.venue.name],
            ['Area', ev.area_name ?? ''],
            ['Booking', ev.booking_label ?? ''],
            ['Minimum spend', payload.min_spend ? `${payload.min_spend.amount_text} ${payload.money.tax_label}` : ''],
          ]}
        />
      </header>

      <Block title="Contact">
        <Pairs rows={[['Name', payload.contact.name ?? ''], ['Phone', payload.contact.phone ?? ''], ['Company', payload.contact.company ?? ''], ['Email', payload.contact.email ?? '']]} />
      </Block>

      <Block title="Details">
        <Pairs
          rows={[
            [fieldLabel('special_instructions'), str(ev.special_instructions)],
            [fieldLabel('notes'), str(ev.notes)],
            [fieldLabel('payment_method'), has('payment_method') && typeof ev.payment_method === 'string' ? PAYMENT_LABELS[ev.payment_method] ?? ev.payment_method : ''],
            [fieldLabel('deposit_cents'), has('deposit_cents') ? deposit : null],
          ]}
        />
        {!str(ev.special_instructions) && !str(ev.notes) && !deposit && !(has('payment_method') && ev.payment_method) && (
          <p style={{ margin: 0, color: 'var(--muted)' }}>Nothing added yet.</p>
        )}
      </Block>

      {(t(ev.arrival_time) || t(ev.seated_time) || str(ev.timing_preference) || str(ev.timing_notes) || (lists.timings ?? []).length > 0) && (
        <Block title="Timings">
          <Pairs
            rows={[
              [fieldLabel('arrival_time'), t(ev.arrival_time)],
              [fieldLabel('seated_time'), t(ev.seated_time)],
              [fieldLabel('timing_preference'), str(ev.timing_preference)],
              [fieldLabel('timing_notes'), str(ev.timing_notes)],
            ]}
          />
          <Table
            head={['Time', "What's happening"]}
            rows={(lists.timings ?? []).map((r) => [
              r.is_heading ? '' : `${t(r.at_time) || str(r.label)}${r.ends_next_day ? ' (next day)' : ''}`,
              r.is_heading ? <strong>{str(r.activity)}</strong> : str(r.activity),
            ])}
          />
        </Block>
      )}

      {lists.entertainment && lists.entertainment.length > 0 && (
        <Block title="Entertainment">
          <Table
            head={['Type', 'Name', 'When', 'Notes']}
            rows={lists.entertainment.map((r) => [ENTERTAINMENT_LABELS[str(r.kind)] ?? str(r.kind), [str(r.name), str(r.link)].filter(Boolean).join(' · '), windowOf(r), str(r.notes)])}
          />
        </Block>
      )}

      {lists.av && lists.av.length > 0 && (
        <Block title="A/V">
          <Pairs rows={lists.av.map((r, i) => [AV_LABELS[str(r.kind)]?.label ?? (str(r.kind) || `Item ${i + 1}`), str(r.details) || 'Yes'] as [string, ReactNode])} />
        </Block>
      )}

      {lists.security && lists.security.length > 0 && (
        <Block title="Security">
          <Table head={['Name', 'When', 'Notes']} rows={lists.security.map((r) => [str(r.name), windowOf(r), str(r.notes)])} />
        </Block>
      )}

      {((typeof ev.bar_tab_cents === 'number' && ev.bar_tab_cents > 0) || str(ev.bar_tab_authorised_people) || restrictions.length > 0 || str(ev.arrival_drinks) || ev.table_ordering === true || str(ev.bar_tab_notes)) && (
        <Block title="Drinks">
          <Pairs
            rows={[
              [fieldLabel('bar_tab_cents'), has('bar_tab_cents') && typeof ev.bar_tab_cents === 'number' && ev.bar_tab_cents > 0 ? money(ev.bar_tab_cents, cur) : ''],
              [fieldLabel('bar_tab_authorised_people'), str(ev.bar_tab_authorised_people)],
              [fieldLabel('bar_tab_restrictions'), restrictions.join(', ')],
              [fieldLabel('arrival_drinks'), str(ev.arrival_drinks)],
              [fieldLabel('table_ordering'), tableOrdering],
              [fieldLabel('bar_tab_notes'), str(ev.bar_tab_notes)],
            ]}
          />
        </Block>
      )}

      {(menus.length > 0 || food || str(ev.allergies) || str(ev.food_notes)) && (
        <Block title="Food">
          <Pairs
            rows={[
              ['Food timing', food ? [food, foodTimes].filter(Boolean).join(', ') : ''],
              [fieldLabel('allergies'), str(ev.allergies)],
              [fieldLabel('food_notes'), str(ev.food_notes)],
            ]}
          />
          {menus.map((menu) => (
            <div key={menu.id} style={{ marginTop: 10 }}>
              <div style={{ fontWeight: 600, color: 'var(--text)' }}>
                {menu.name}
                {menu.is_set_menu && menu.price_per_person_cents ? (
                  <span style={{ fontWeight: 400, color: 'var(--muted)' }}> · set menu {money(menu.price_per_person_cents, cur)} pp{menu.guest_count ? ` × ${menu.guest_count}` : ''}</span>
                ) : null}
              </div>
              <Table
                head={['Item', 'Qty', 'Each', 'Total']}
                rows={menu.items
                  .filter((i) => i.kind === 'header' || i.quantity > 0)
                  .map((i) =>
                    i.kind === 'header'
                      ? [<strong key="h">{i.name}</strong>, '', '', '']
                      : [
                          <span key="n">{i.name}{i.dietaries.length ? <span style={{ color: 'var(--muted)' }}> ({i.dietaries.join(', ')})</span> : null}</span>,
                          String(i.quantity),
                          i.unit_price_cents !== null ? money(i.unit_price_cents, cur) : '',
                          i.unit_price_cents !== null ? money(i.unit_price_cents * i.quantity, cur) : '',
                        ],
                  )}
              />
              <div style={{ textAlign: 'right', marginTop: 4, fontVariantNumeric: 'tabular-nums' }}>
                Menu total <strong>{money(menuValueCents(menu, undefined), cur)}</strong>
              </div>
            </div>
          ))}
          {menus.length > 1 && (
            <div style={{ textAlign: 'right', marginTop: 8, fontVariantNumeric: 'tabular-nums' }}>Food total <strong>{money(foodTotal, cur)}</strong></div>
          )}
        </Block>
      )}

      <Block title="Confirmations">
        <Pairs
          rows={SECTIONS.map((s) => {
            const st = payload.status.sections[s];
            const when = st.at ? new Date(st.at).toLocaleString('en-NZ', { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : '';
            const text = st.state === 'confirmed' ? `Confirmed${when ? ` ${when}` : ''}` : st.state === 'skipped' ? 'Not needed' : 'Not confirmed yet';
            return [SECTION_LABELS[s], text] as [string, ReactNode];
          })}
        />
      </Block>

      {payload.people.length > 0 && (
        <Block title="People with access">
          <p style={{ margin: 0, color: 'var(--text)', overflowWrap: 'anywhere' }}>{payload.people.map((p) => p.email).join(', ')}</p>
        </Block>
      )}
    </article>
  );
}
