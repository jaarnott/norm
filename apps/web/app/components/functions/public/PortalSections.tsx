'use client';

import type { Dispatch } from 'react';
import { ExternalLink } from 'lucide-react';
import Badge from '../../ui/Badge';
import Icon from '../../ui/Icon';
import { dateLabel, money, timeLabel, timeRangeLabel } from '../shared/format';
import { AddButton, Card, Chips, ChoiceCards, Fact, Row, RowInput, Stepper, TextField, YesNo } from './PortalControls';
import {
  AV_LABELS,
  DEPOSIT_LABELS,
  ENTERTAINMENT_LABELS,
  FOOD_TIMING,
  PAYMENT_LABELS,
  RESTRICTION_PRESETS,
  fieldLabel,
  parseRestrictions,
  toggleRestriction,
  writeRestrictions,
} from './portalFields';
import { afterMidnight, kindOf, menuValueCents, windowNextDay, type Action, type PortalState } from './portalModel';
import { safeLink } from './brand';
import type { ListKind, ListRow } from './types';

/**
 * The portal's three tabs. Each shows only what the API sent: a field the
 * venue hides for this booking type is absent from `event`, a hidden list is
 * absent from `lists`, and only keys in `editable` get an input.
 */

interface TabProps {
  state: PortalState;
  dispatch: Dispatch<Action>;
  /** Cancelled event, or a conflict waiting on a reload. */
  locked: boolean;
}

const MULTILINE = new Set(['special_instructions', 'notes', 'timing_preference', 'timing_notes', 'bar_tab_notes', 'allergies', 'food_notes']);

const HINTS: Record<string, { help?: string; placeholder?: string }> = {
  guests: { help: 'Roughly is fine. Update it here if it changes.' },
  special_instructions: { placeholder: 'A cake, decorations, accessibility, anything the venue should know' },
  notes: { placeholder: 'Anything else for the venue' },
  arrival_time: { help: 'When your first guests get here.' },
  timing_preference: { placeholder: 'e.g. Speeches before mains' },
  bar_tab_authorised_people: { help: 'People who can add to the tab on the night.', placeholder: 'e.g. Sam Smith, Jo Brown' },
  arrival_drinks: { placeholder: 'e.g. A glass of bubbles on arrival' },
  table_ordering: { help: 'Guests order drinks to their table, on the tab.' },
  table_ordering_limit_cents: { help: 'Leave blank for no limit.' },
  allergies: { help: 'The kitchen sees this. Say how many guests each applies to.', placeholder: 'e.g. 2 gluten free, 1 nut allergy' },
  food_notes: { placeholder: 'Anything else about the food' },
};

/** One editable event field, drawn for its kind. */
function FieldInput({ name, state, dispatch, locked }: TabProps & { name: string }) {
  const value = state.draft.fields[name];
  const set = (v: string | boolean) => dispatch({ type: 'field', key: name, value: v });
  const id = `ev-${name}`;
  const hint = HINTS[name] ?? {};
  const error = state.invalid[name] ?? null;
  switch (kindOf(name)) {
    case 'bool':
      return <YesNo id={id} label={fieldLabel(name)} value={value === true} onChange={set} disabled={locked} help={hint.help} />;
    case 'int':
      return <TextField id={id} label={fieldLabel(name)} value={String(value ?? '')} onChange={set} inputMode="numeric" width={160} disabled={locked} help={hint.help} error={error} />;
    case 'time':
      return <TextField id={id} label={fieldLabel(name)} type="time" value={String(value ?? '')} onChange={set} width={180} disabled={locked} help={hint.help} error={error} />;
    case 'money':
      return <TextField id={id} label={fieldLabel(name)} prefix="$" inputMode="decimal" value={String(value ?? '')} onChange={set} width={200} disabled={locked} help={hint.help} error={error} />;
    default:
      return (
        <TextField
          id={id}
          label={fieldLabel(name)}
          value={String(value ?? '')}
          onChange={set}
          multiline={MULTILINE.has(name)}
          disabled={locked}
          help={hint.help}
          placeholder={hint.placeholder}
          error={error}
        />
      );
  }
}

const editable = (state: PortalState, name: string) => state.server.editable.includes(name);
const shown = (state: PortalState, name: string) => name in state.server.event;

// ── Details ──────────────────────────────────────────────────────────────

export function DetailsTab(props: TabProps) {
  const { state } = props;
  const p = state.server;
  const ev = p.event;
  const lists = p.lists ?? {};
  const deposit = typeof ev.deposit_cents === 'number' && ev.deposit_cents > 0 ? ev.deposit_cents : null;
  const timingFields = ['arrival_time', 'seated_time', 'timing_preference', 'timing_notes'].filter((f) => editable(state, f));
  const field = (name: string) => (editable(state, name) ? <FieldInput key={name} name={name} {...props} /> : null);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Card title="Your booking">
        <dl className="fp-two" style={{ margin: 0 }}>
          <Fact label="Date">{dateLabel(ev.event_date)}</Fact>
          <Fact label="Time">{timeRangeLabel(ev.start_time, ev.end_time, ev.ends_next_day) || 'To be confirmed'}</Fact>
          {ev.area_name ? <Fact label="Area">{ev.area_name}</Fact> : null}
          {ev.booking_label ? <Fact label="Booking">{ev.booking_label}</Fact> : null}
          {p.min_spend ? <Fact label="Minimum spend">{p.min_spend.amount_text} <span style={{ color: 'var(--muted)' }}>{p.money.tax_label}</span></Fact> : null}
          {shown(state, 'payment_method') && typeof ev.payment_method === 'string' && ev.payment_method ? (
            <Fact label={fieldLabel('payment_method')}>{PAYMENT_LABELS[ev.payment_method] ?? ev.payment_method}</Fact>
          ) : null}
          {shown(state, 'deposit_cents') && deposit ? (
            <Fact label={fieldLabel('deposit_cents')}>
              {money(deposit, p.money.currency)}{' '}
              {typeof ev.deposit_status === 'string' && DEPOSIT_LABELS[ev.deposit_status] ? (
                <Badge tone={ev.deposit_status === 'paid' ? 'ok' : 'neutral'}>{DEPOSIT_LABELS[ev.deposit_status]}</Badge>
              ) : null}
            </Fact>
          ) : null}
        </dl>
        {field('guests')}
        {field('special_instructions')}
        {field('notes')}
      </Card>

      <ContactCard {...props} />

      {(timingFields.length > 0 || lists.timings) && (
        <Card title="Timings">
          {(editable(state, 'arrival_time') || editable(state, 'seated_time')) && (
            <div className="fp-two">
              {field('arrival_time')}
              {field('seated_time')}
            </div>
          )}
          {lists.timings && <TimingsEditor {...props} />}
          {field('timing_preference')}
          {field('timing_notes')}
        </Card>
      )}

      {lists.entertainment && <EntertainmentEditor {...props} />}
      {lists.av && <AvToggles {...props} />}
      {lists.security && <SecurityEditor {...props} />}
    </div>
  );
}

function ContactCard({ state, dispatch, locked }: TabProps) {
  const c = state.draft.contact;
  const set = (key: 'name' | 'phone' | 'company') => (value: string) => dispatch({ type: 'contact', key, value });
  return (
    <Card title="Your details">
      <TextField id="contact-name" label={fieldLabel('contact.name')} value={c.name} onChange={set('name')} autoComplete="name" maxLength={200} disabled={locked} />
      <div className="fp-two">
        <TextField id="contact-phone" label={fieldLabel('contact.phone')} type="tel" inputMode="tel" value={c.phone} onChange={set('phone')} autoComplete="tel" maxLength={40} disabled={locked} />
        <TextField id="contact-company" label={fieldLabel('contact.company')} value={c.company} onChange={set('company')} autoComplete="organization" maxLength={200} disabled={locked} />
      </div>
      {state.server.contact.email && (
        <Fact label="Email">
          {state.server.contact.email}
          <span style={{ display: 'block', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>To change it, contact the venue.</span>
        </Fact>
      )}
    </Card>
  );
}

function rowsOf(state: PortalState, kind: ListKind): ListRow[] {
  return state.draft.lists[kind] ?? [];
}

const str = (v: unknown) => (typeof v === 'string' ? v : '');

function TimingsEditor({ state, dispatch, locked }: TabProps) {
  const rows = rowsOf(state, 'timings');
  const ev = state.server.event;
  const update = (i: number, patch: ListRow) => {
    const next = rows.map((r, j) => (j === i ? { ...r, ...patch } : r));
    if ('at_time' in patch) next[i].ends_next_day = afterMidnight(str(patch.at_time), ev);
    dispatch({ type: 'list', kind: 'timings', rows: next });
  };
  const remove = (i: number) => dispatch({ type: 'list', kind: 'timings', rows: rows.filter((_, j) => j !== i) });
  const add = () => dispatch({ type: 'list', kind: 'timings', rows: [...rows, { at_time: '', ends_next_day: false, label: null, activity: '', is_heading: false }] });
  return (
    <div role="group" aria-label="Run sheet" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div className="n-label" style={{ marginBottom: 0 }}>Run sheet</div>
      {rows.length === 0 && <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Add the key moments: arrivals, speeches, cake, last drinks.</p>}
      {rows.map((row, i) =>
        row.is_heading ? (
          <Row key={i} label={`heading ${str(row.activity) || i + 1}`} onRemove={() => remove(i)} disabled={locked}>
            <RowInput label="Heading" value={str(row.activity)} onChange={(v) => update(i, { activity: v })} disabled={locked} />
          </Row>
        ) : (
          <Row key={i} label={`run sheet item ${i + 1}`} onRemove={() => remove(i)} disabled={locked}>
            <RowInput label="Time" type="time" width={150} value={str(row.at_time).slice(0, 5)} onChange={(v) => update(i, { at_time: v })} disabled={locked} />
            {str(row.label) && <RowInput label="When" width={140} value={str(row.label)} onChange={(v) => update(i, { label: v })} disabled={locked} />}
            <RowInput label="What's happening" value={str(row.activity)} onChange={(v) => update(i, { activity: v })} disabled={locked} placeholder="e.g. Speeches" />
          </Row>
        ),
      )}
      {!locked && <AddButton onClick={add} disabled={rows.length >= 100}>Add a time</AddButton>}
    </div>
  );
}

function SecurityEditor({ state, dispatch, locked }: TabProps) {
  const rows = rowsOf(state, 'security');
  const update = (i: number, patch: ListRow) => {
    const next = rows.map((r, j) => (j === i ? { ...r, ...patch } : r));
    next[i].ends_next_day = windowNextDay(str(next[i].start_time), str(next[i].end_time));
    dispatch({ type: 'list', kind: 'security', rows: next });
  };
  return (
    <Card title="Security">
      {rows.length === 0 && <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No security booked.</p>}
      {rows.map((row, i) => (
        <Row key={i} label={`security ${i + 1}`} onRemove={() => dispatch({ type: 'list', kind: 'security', rows: rows.filter((_, j) => j !== i) })} disabled={locked}>
          <RowInput label="Name or company" value={str(row.name)} onChange={(v) => update(i, { name: v })} disabled={locked} />
          <RowInput label="From" type="time" width={130} value={str(row.start_time).slice(0, 5)} onChange={(v) => update(i, { start_time: v })} disabled={locked} />
          <RowInput label="Until" type="time" width={130} value={str(row.end_time).slice(0, 5)} onChange={(v) => update(i, { end_time: v })} disabled={locked} />
          <RowInput label="Notes" value={str(row.notes)} onChange={(v) => update(i, { notes: v })} disabled={locked} />
        </Row>
      ))}
      {!locked && (
        <AddButton onClick={() => dispatch({ type: 'list', kind: 'security', rows: [...rows, { name: '', start_time: '', end_time: '', ends_next_day: false, notes: '' }] })}>
          Add security
        </AddButton>
      )}
    </Card>
  );
}

function EntertainmentEditor({ state, dispatch, locked }: TabProps) {
  const all = rowsOf(state, 'entertainment');
  const kinds = state.server.list_kinds.entertainment ?? Object.keys(ENTERTAINMENT_LABELS);
  const update = (i: number, patch: ListRow) => {
    const next = all.map((r, j) => (j === i ? { ...r, ...patch } : r));
    next[i].ends_next_day = windowNextDay(str(next[i].start_time), str(next[i].end_time));
    dispatch({ type: 'list', kind: 'entertainment', rows: next });
  };
  if (!kinds.length) return null;
  return (
    <Card title="Entertainment">
      {all.length === 0 && <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Bringing a DJ or your own playlist? Add it here.</p>}
      {all.map((row, i) => {
        const link = safeLink(row.link);
        return (
          <Row key={i} label={`entertainment ${i + 1}`} onRemove={() => dispatch({ type: 'list', kind: 'entertainment', rows: all.filter((_, j) => j !== i) })} disabled={locked}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 2, flex: '0 0 170px' }}>
              <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Type</span>
              <select className="n-select" value={str(row.kind)} disabled={locked} onChange={(e) => update(i, { kind: e.target.value })} style={{ width: '100%' }}>
                {kinds.map((k) => <option key={k} value={k}>{ENTERTAINMENT_LABELS[k] ?? k}</option>)}
              </select>
            </label>
            <RowInput label={row.kind === 'playlist' ? 'Playlist name' : 'DJ name'} value={str(row.name)} onChange={(v) => update(i, { name: v })} disabled={locked} />
            <RowInput label={row.kind === 'playlist' ? 'Playlist link' : 'Link'} type="url" value={str(row.link)} onChange={(v) => update(i, { link: v })} disabled={locked} placeholder="https://" />
            <RowInput label="From" type="time" width={130} value={str(row.start_time).slice(0, 5)} onChange={(v) => update(i, { start_time: v })} disabled={locked} />
            <RowInput label="Until" type="time" width={130} value={str(row.end_time).slice(0, 5)} onChange={(v) => update(i, { end_time: v })} disabled={locked} />
            <RowInput label="Notes" value={str(row.notes)} onChange={(v) => update(i, { notes: v })} disabled={locked} />
            {link && (
              <a href={link} target="_blank" rel="noopener noreferrer" style={{ alignSelf: 'flex-end', fontSize: 'var(--fs-sm)', color: 'var(--accent)', display: 'inline-flex', gap: 4, alignItems: 'center', minHeight: 40 }}>
                Open link <Icon icon={ExternalLink} size={12} />
              </a>
            )}
          </Row>
        );
      })}
      {!locked && (
        <AddButton onClick={() => dispatch({ type: 'list', kind: 'entertainment', rows: [...all, { kind: kinds[0], name: '', link: '', start_time: '', end_time: '', ends_next_day: false, notes: '' }] })}>
          Add entertainment
        </AddButton>
      )}
    </Card>
  );
}

function AvToggles({ state, dispatch, locked }: TabProps) {
  const rows = rowsOf(state, 'av');
  const kinds = state.server.list_kinds.av ?? Object.keys(AV_LABELS);
  if (!kinds.length) return null;
  const setRows = (next: ListRow[]) => dispatch({ type: 'list', kind: 'av', rows: next });
  return (
    <Card title="A/V">
      {kinds.map((kind) => {
        const index = rows.findIndex((r) => r.kind === kind);
        const on = index >= 0;
        const meta = AV_LABELS[kind] ?? { label: kind, detail: '' };
        const id = `av-${kind}`;
        return (
          <div key={kind} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label htmlFor={id} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', cursor: locked ? 'default' : 'pointer' }}>
              <input
                id={id}
                type="checkbox"
                checked={on}
                disabled={locked}
                onChange={(e) => setRows(e.target.checked ? [...rows, { kind, details: '' }] : rows.filter((r) => r.kind !== kind))}
                style={{ width: 18, height: 18, marginTop: 2, accentColor: 'var(--primary)' }}
              />
              <span>
                <span style={{ display: 'block', fontWeight: 600, color: 'var(--text)' }}>{meta.label}</span>
                <span style={{ display: 'block', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{meta.detail}</span>
              </span>
            </label>
            {on && (
              <div style={{ paddingLeft: 28 }}>
                <TextField
                  id={`${id}-details`}
                  label="Details (optional)"
                  value={str(rows[index].details)}
                  onChange={(v) => setRows(rows.map((r, j) => (j === index ? { ...r, details: v } : r)))}
                  disabled={locked}
                />
              </div>
            )}
          </div>
        );
      })}
    </Card>
  );
}

// ── Drinks ───────────────────────────────────────────────────────────────

export function DrinksTab(props: TabProps) {
  const { state, dispatch, locked } = props;
  const p = state.server;
  const ev = p.event;
  const field = (name: string) => (editable(state, name) ? <FieldInput key={name} name={name} {...props} /> : null);
  const barTab = shown(state, 'bar_tab_cents') && typeof ev.bar_tab_cents === 'number' && ev.bar_tab_cents > 0 ? ev.bar_tab_cents : null;
  const anything = barTab !== null || ['bar_tab_authorised_people', 'bar_tab_restrictions', 'arrival_drinks', 'table_ordering', 'bar_tab_notes'].some((f) => editable(state, f));
  if (!anything) {
    return <Card title="Drinks"><p style={{ margin: 0, color: 'var(--text-soft)' }}>There&apos;s nothing for you to fill in here — the venue has it covered.</p></Card>;
  }
  const restrictions = parseRestrictions(state.draft.fields.bar_tab_restrictions);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Card title="Drinks">
        {barTab !== null && <Fact label={fieldLabel('bar_tab_cents')}>{money(barTab, p.money.currency)} <span style={{ color: 'var(--muted)' }}>{p.money.tax_label}, set by the venue</span></Fact>}
        {field('bar_tab_authorised_people')}
        {editable(state, 'bar_tab_restrictions') && (
          <Chips
            id="ev-bar_tab_restrictions"
            label={fieldLabel('bar_tab_restrictions')}
            help="What the tab covers. Pick any, or add your own."
            selected={restrictions}
            presets={RESTRICTION_PRESETS}
            toggle={toggleRestriction}
            onChange={(next) => dispatch({ type: 'field', key: 'bar_tab_restrictions', value: writeRestrictions(next) ?? '' })}
            disabled={locked}
          />
        )}
        {field('arrival_drinks')}
        {field('table_ordering')}
        {editable(state, 'table_ordering_limit_cents') && state.draft.fields.table_ordering === true && field('table_ordering_limit_cents')}
        {field('bar_tab_notes')}
      </Card>
    </div>
  );
}

// ── Food ─────────────────────────────────────────────────────────────────

export function FoodTab(props: TabProps) {
  const { state, dispatch, locked } = props;
  const p = state.server;
  const field = (name: string) => (editable(state, name) ? <FieldInput key={name} name={name} {...props} /> : null);
  const timing = FOOD_TIMING.find((o) => o.value === state.draft.fields.food_timing_type);
  const links = (p.venue.menu_links ?? []).map((l) => ({ label: l.label || 'Menu', url: safeLink(l.url) })).filter((l): l is { label: string; url: string } => !!l.url);
  const menus = p.menus ?? [];
  const showTiming = editable(state, 'food_timing_type');
  const anything = menus.length > 0 || showTiming || editable(state, 'allergies') || editable(state, 'food_notes') || links.length > 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {menus.map((menu) => {
        const qty = state.draft.quantities[menu.id] ?? {};
        const items = menu.items;
        return (
          <Card key={menu.id} title={menu.name} aside={<span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{money(menuValueCents(menu, qty), p.money.currency)}</span>}>
            {menu.is_set_menu && menu.price_per_person_cents ? (
              <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                Set menu · {money(menu.price_per_person_cents, p.money.currency)} per person{menu.guest_count ? ` × ${menu.guest_count} guests` : ''}
              </p>
            ) : null}
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {items.map((item) =>
                item.kind === 'header' ? (
                  <div key={item.id} className="n-eyebrow" style={{ padding: '12px 0 4px' }}>{item.name}</div>
                ) : (
                  <div key={item.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 0', borderTop: '1px solid var(--line-soft)', flexWrap: 'wrap' }}>
                    <div style={{ flex: '1 1 180px', minWidth: 0 }}>
                      <div style={{ fontWeight: 500, color: 'var(--text)' }}>{item.name}</div>
                      {item.description && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{item.description}</div>}
                      <div style={{ display: 'flex', gap: 4, marginTop: item.dietaries.length ? 4 : 0, flexWrap: 'wrap' }}>
                        {item.dietaries.map((d) => <Badge key={d}>{d}</Badge>)}
                        {item.unit_price_cents !== null && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{money(item.unit_price_cents, p.money.currency)} each</span>}
                      </div>
                    </div>
                    <Stepper
                      id={`qty-${item.id}`}
                      name={item.name}
                      value={qty[item.id] ?? item.quantity ?? 0}
                      onChange={(value) => dispatch({ type: 'quantity', menuId: menu.id, itemId: item.id, value })}
                      disabled={locked}
                    />
                  </div>
                ),
              )}
            </div>
          </Card>
        );
      })}

      {(showTiming || editable(state, 'allergies') || editable(state, 'food_notes')) && (
        <Card title="Food details">
          {showTiming && (
            <ChoiceCards
              id="ev-food_timing_type"
              label={fieldLabel('food_timing_type')}
              value={String(state.draft.fields.food_timing_type ?? '')}
              options={FOOD_TIMING.map((o) => ({ value: o.value, label: o.label, detail: o.detail }))}
              onChange={(value) => dispatch({ type: 'field', key: 'food_timing_type', value })}
              disabled={locked}
            />
          )}
          {timing && (timing.time || timing.endTime) && (
            <div className="fp-two">
              {timing.time && field('food_time')}
              {timing.endTime && field('food_end_time')}
            </div>
          )}
          {field('allergies')}
          {field('food_notes')}
        </Card>
      )}

      {links.length > 0 && (
        <Card title="The venue's menus">
          <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
            {links.map((l) => (
              <li key={l.url}>
                <a href={l.url} target="_blank" rel="noopener noreferrer" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, minHeight: 32, color: 'var(--accent)' }}>
                  {l.label} <Icon icon={ExternalLink} size={14} />
                </a>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {!anything && <Card title="Food"><p style={{ margin: 0, color: 'var(--text-soft)' }}>There&apos;s nothing for you to fill in here — the venue has it covered.</p></Card>}
    </div>
  );
}

/** "7:30 pm" for a row time, used by the print sheet too. */
export function rowTime(value: unknown): string {
  return typeof value === 'string' && value ? timeLabel(value) : '';
}
