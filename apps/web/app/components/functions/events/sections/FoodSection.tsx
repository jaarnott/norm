'use client';

/**
 * Food: the pre-order menus (dish quantities; a set menu's price per person
 * and guest count, with the default % split done by the API), food timing,
 * allergies and notes. Allergies are health data: they are shown here but
 * never logged or put in a message.
 *
 * Each menu saves on its own (menus carry their own version); the event's
 * food fields save with the section's Edit.
 */
import { useId, useState } from 'react';
import { Pencil, Plus, Trash2, Wand2 } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import Dialog from '../../../ui/Dialog';
import IconButton from '../../../ui/IconButton';
import { fnGet, fnSend } from '../../shared/api';
import { money, parseMoney } from '../../shared/format';
import {
  SECTION_BADGE,
  centsToInput,
  changedFields,
  foodTimingNeeds,
  foodTimingText,
  normaliseTime,
  sectionBadge,
  textOrNull,
} from '../logic';
import { Fact, FactGrid, Field, FieldGrid, MoneyInput, SectionCard, WriteError, inputStyle } from '../ui';
import { errorText, isStale, useLoad } from '../useLoad';
import type { EventDetail, EventFields, Menu, MenuItem, PreorderTemplate } from '../types';
import { useEditor } from './kit';
import type { SectionCtx, Write } from './kit';
import SectionActions from './SectionActions';

interface FoodDraft {
  food_timing_type: string;
  food_time: string;
  food_end_time: string;
  allergies: string;
  food_notes: string;
}

function draftOf(d: EventDetail): FoodDraft {
  const e = d.event;
  return {
    food_timing_type: e.food_timing_type ?? '',
    food_time: e.food_time ?? '',
    food_end_time: e.food_end_time ?? '',
    allergies: e.allergies ?? '',
    food_notes: e.food_notes ?? '',
  };
}

function fieldsOf(dr: FoodDraft): Partial<EventFields> {
  const needs = foodTimingNeeds(dr.food_timing_type);
  return {
    food_timing_type: dr.food_timing_type || null,
    food_time: needs.time ? normaliseTime(dr.food_time) : null,
    food_end_time: needs.end ? normaliseTime(dr.food_end_time) : null,
    allergies: textOrNull(dr.allergies),
    food_notes: textOrNull(dr.food_notes),
  };
}

/** Dishes grouped under their headings (a menu's items are in order). */
function sections(items: MenuItem[]): { header: MenuItem | null; dishes: MenuItem[] }[] {
  const out: { header: MenuItem | null; dishes: MenuItem[] }[] = [];
  for (const item of items) {
    if (item.kind === 'header') out.push({ header: item, dishes: [] });
    else {
      if (!out.length) out.push({ header: null, dishes: [] });
      out[out.length - 1].dishes.push(item);
    }
  }
  return out;
}

function Dietaries({ codes }: { codes: string[] }) {
  if (!codes.length) return null;
  return <span style={{ display: 'inline-flex', gap: 3, marginLeft: 6, verticalAlign: 'middle' }}>{codes.map((c) => <Badge key={c}>{c}</Badge>)}</span>;
}

interface MenuDraft {
  guest_count: string;
  price: string;
  notes: string;
  qty: Record<string, string>;
}

function MenuCard({ ctx, menu }: { ctx: SectionCtx; menu: Menu }) {
  const ids = { guests: useId(), price: useId(), notes: useId() };
  const [draft, setDraft] = useState<MenuDraft | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [removing, setRemoving] = useState(false);
  const cur = ctx.currency;
  const groups = sections(menu.items);
  const ordered = menu.items.filter((i) => i.kind === 'item' && i.quantity > 0).length;
  const dishes = menu.items.filter((i) => i.kind === 'item').length;

  const start = () => {
    setDraft({
      guest_count: String(menu.guest_count ?? 0),
      price: centsToInput(menu.price_per_person_cents),
      notes: menu.notes ?? '',
      qty: Object.fromEntries(menu.items.filter((i) => i.kind === 'item').map((i) => [i.id, String(i.quantity)])),
    });
    setError(null);
  };

  const run = async (write: Write) => {
    setBusy(true);
    setError(null);
    try {
      const ok = await ctx.save(write);
      if (ok) setDraft(null);
    } catch (e) {
      setError(e);
    }
    setBusy(false);
  };

  const save = () => {
    if (!draft) return;
    const fields: Record<string, unknown> = {};
    const guests = Math.max(0, Math.round(Number(draft.guest_count) || 0));
    if (guests !== menu.guest_count) fields.guest_count = guests;
    if (menu.is_set_menu) {
      const price = parseMoney(draft.price);
      if (price !== menu.price_per_person_cents) fields.price_per_person_cents = price;
    }
    if ((textOrNull(draft.notes) ?? null) !== (menu.notes ?? null)) fields.notes = textOrNull(draft.notes);
    const quantities: Record<string, number> = {};
    for (const item of menu.items) {
      if (item.kind !== 'item') continue;
      const n = Math.max(0, Math.round(Number(draft.qty[item.id]) || 0));
      if (n !== item.quantity) quantities[item.id] = n;
    }
    if (!Object.keys(fields).length && !Object.keys(quantities).length) {
      setDraft(null);
      return;
    }
    void run(async (note) => {
      let version = menu.version;
      if (Object.keys(fields).length) {
        const m = await fnSend<Menu>('PATCH', `/menus/${menu.id}`, { version, note, ...fields });
        version = m.version;
      }
      if (Object.keys(quantities).length) {
        await fnSend<Menu>('PUT', `/menus/${menu.id}/quantities`, { version, quantities, note });
      }
      return null;
    });
  };

  const distribute = () => {
    if (!draft) return;
    const guests = Math.max(0, Math.round(Number(draft.guest_count) || 0));
    void run(async (note) => {
      await fnSend<Menu>('POST', `/menus/${menu.id}/distribute`, { version: menu.version, guest_count: guests, note });
      return null;
    });
  };

  // A set menu's sections should each add up to the guest count.
  const sectionSums = draft && menu.is_set_menu
    ? groups.map((g) => g.dishes.reduce((s, d) => s + (Math.max(0, Number(draft.qty[d.id]) || 0)), 0))
    : [];
  const draftGuests = draft ? Math.max(0, Math.round(Number(draft.guest_count) || 0)) : menu.guest_count;

  return (
    <div className="n-card" style={{ padding: '12px 14px', background: 'var(--surface)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600 }}>{menu.name}</h3>
        {menu.is_set_menu && <Badge tone="info">Set menu · {money(menu.price_per_person_cents, cur)} pp</Badge>}
        {menu.is_set_menu && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{menu.guest_count} guests</span>}
        <span style={{ marginLeft: 'auto', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{money(menu.total_cents, cur)}</span>
        {!draft && (
          <span style={{ display: 'inline-flex', gap: 4 }}>
            <Button size="sm" icon={Pencil} onClick={start} disabled={!ctx.canWrite} title={ctx.lockReason ?? undefined}>Edit</Button>
            <IconButton icon={Trash2} label={`Remove ${menu.name}`} onClick={() => setRemoving(true)} disabled={!ctx.canWrite} />
          </span>
        )}
      </div>

      {draft && (
        <FieldGrid>
          <Field label={menu.is_set_menu ? 'Guests on this menu' : 'Guests (for reference)'} htmlFor={ids.guests}>
            <input id={ids.guests} type="number" min={0} inputMode="numeric" className="n-input" style={inputStyle} value={draft.guest_count}
              onChange={(e) => setDraft({ ...draft, guest_count: e.target.value })} />
          </Field>
          {menu.is_set_menu && (
            <Field label="Price per person" htmlFor={ids.price}>
              <MoneyInput id={ids.price} value={draft.price} onChange={(v) => setDraft({ ...draft, price: v })} />
            </Field>
          )}
          <Field label="Menu notes" htmlFor={ids.notes} wide>
            <input id={ids.notes} className="n-input" style={inputStyle} value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} />
          </Field>
        </FieldGrid>
      )}

      {menu.notes && !draft && <div style={{ marginTop: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', whiteSpace: 'pre-wrap' }}>{menu.notes}</div>}

      <div style={{ marginTop: 8, overflowX: 'auto' }}>
        <table className="n-table" style={{ fontSize: 'var(--fs-sm)', background: 'var(--bg)' }}>
          <thead>
            <tr>
              <th>Dish</th>
              <th className="num" style={{ width: 90 }}>Qty</th>
              {!menu.is_set_menu && <th className="num">Price</th>}
              {!menu.is_set_menu && <th className="num">Total</th>}
            </tr>
          </thead>
          <tbody>
            {groups.map((g, gi) => {
              const visible = draft || showAll ? g.dishes : g.dishes.filter((d) => d.quantity > 0);
              if (!visible.length && !(draft || showAll)) return null;
              const sum = sectionSums[gi];
              return [
                g.header ? (
                  <tr key={g.header.id}>
                    <td colSpan={menu.is_set_menu ? 2 : 4} style={{ fontWeight: 600, background: 'var(--surface)' }}>
                      {g.header.name}
                      {draft && menu.is_set_menu && g.dishes.length > 0 && (
                        <span style={{ marginLeft: 8, fontWeight: 400, fontSize: 'var(--fs-xs)', color: sum === draftGuests ? 'var(--muted)' : 'var(--warn)' }}>
                          {sum} of {draftGuests} guests
                        </span>
                      )}
                    </td>
                  </tr>
                ) : null,
                ...visible.map((d) => (
                  <tr key={d.id}>
                    <td>
                      {d.name}
                      <Dietaries codes={d.dietaries} />
                      {d.description && d.description.toLowerCase() !== d.name.toLowerCase() && (
                        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{d.description}</div>
                      )}
                    </td>
                    <td className="num">
                      {draft ? (
                        <input
                          aria-label={`Quantity of ${d.name}`}
                          type="number"
                          min={0}
                          inputMode="numeric"
                          className="n-input"
                          value={draft.qty[d.id] ?? ''}
                          onChange={(e) => setDraft({ ...draft, qty: { ...draft.qty, [d.id]: e.target.value } })}
                          style={{ width: 72, textAlign: 'right' }}
                        />
                      ) : d.quantity}
                    </td>
                    {!menu.is_set_menu && <td className="num">{d.unit_price_cents !== null ? money(d.unit_price_cents, cur) : '—'}</td>}
                    {!menu.is_set_menu && <td className="num">{d.line_total_cents ? money(d.line_total_cents, cur) : '—'}</td>}
                  </tr>
                )),
              ];
            })}
            {!draft && !showAll && ordered === 0 && (
              <tr><td colSpan={4} style={{ color: 'var(--muted)' }}>Nothing ordered yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {!draft && dishes > ordered && (
        <Button variant="link" style={{ marginTop: 6, fontSize: 'var(--fs-sm)' }} onClick={() => setShowAll((s) => !s)}>
          {showAll ? 'Show ordered dishes only' : `Show all ${dishes} dishes`}
        </Button>
      )}

      {draft && (
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: 10 }}>
          {menu.is_set_menu && (
            <Button icon={Wand2} onClick={distribute} disabled={busy} title="Saves the guest count and fills each course from the dishes’ default %">
              Fill from default split
            </Button>
          )}
          <Button onClick={() => setDraft(null)} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={save} disabled={busy} aria-busy={busy || undefined}>{busy ? 'Saving…' : 'Save menu'}</Button>
        </div>
      )}
      <WriteError message={error ? errorText(error) : null} stale={isStale(error)} onReload={() => { setError(null); setDraft(null); ctx.reload(); }} />

      {removing && (
        <Dialog title={`Remove ${menu.name}?`} width={420} onClose={() => { if (!busy) setRemoving(false); }}>
          <p style={{ marginTop: 0, color: 'var(--text-soft)' }}>The menu and its quantities come off this event. You can add it again from its template.</p>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
            <Button onClick={() => setRemoving(false)} disabled={busy}>Cancel</Button>
            <Button variant="danger" disabled={busy} onClick={() => {
              setRemoving(false);
              void run(async (note) => {
                await fnSend<void>('DELETE', `/menus/${menu.id}${note ? `?note=${encodeURIComponent(note)}` : ''}`);
                return null;
              });
            }}>
              {busy ? 'Removing…' : 'Remove menu'}
            </Button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

function AddMenuDialog({ ctx, onClose }: { ctx: SectionCtx; onClose: () => void }) {
  const ids = { tpl: useId(), guests: useId() };
  const ev = ctx.detail.event;
  const templates = useLoad(`templates:${ev.venue_id}`, () => fnGet<{ rows: PreorderTemplate[] }>('/preorder-templates', { venue_id: ev.venue_id }));
  const [templateId, setTemplateId] = useState('');
  const [guests, setGuests] = useState(String(ev.guests ?? 0));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const rows = (templates.data?.rows ?? []).filter((t) => t.active);
  const chosen = rows.find((t) => t.id === templateId);

  const add = async () => {
    if (!templateId) {
      setError(new Error('Choose a menu.'));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const ok = await ctx.save(async (note) => {
        await fnSend<Menu>('POST', `/events/${ev.id}/menus`, { template_id: templateId, guest_count: Math.max(0, Math.round(Number(guests) || 0)), note });
        return null;
      });
      if (ok) onClose();
    } catch (e) {
      setError(e);
    }
    setBusy(false);
  };

  return (
    <Dialog title="Add a pre-order menu" width={460} onClose={() => { if (!busy) onClose(); }}>
      {templates.loading && <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>Loading menus…</div>}
      {templates.error !== undefined && <WriteError message={errorText(templates.error)} />}
      {templates.data && !rows.length && (
        <p style={{ marginTop: 0, color: 'var(--text-soft)' }}>{ctx.detail.venue?.name ?? 'This venue'} has no pre-order menus yet — add them in Functions settings.</p>
      )}
      {rows.length > 0 && (
        <div style={{ display: 'grid', gap: 12 }}>
          <Field label="Menu" htmlFor={ids.tpl}>
            <select id={ids.tpl} className="n-select" style={inputStyle} value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
              <option value="">Choose a menu</option>
              {rows.map((t) => (
                <option key={t.id} value={t.id}>{t.name}{t.is_set_menu ? ` — set menu ${money(t.price_per_person_cents, ctx.currency)} pp` : ''}</option>
              ))}
            </select>
          </Field>
          <Field label="Guests" htmlFor={ids.guests} hint={chosen?.is_set_menu ? 'A set menu is priced per guest. Use Fill from default split afterwards to spread the courses.' : 'Quantities start at 0 — enter them on the menu.'}>
            <input id={ids.guests} type="number" min={0} inputMode="numeric" className="n-input" style={inputStyle} value={guests} onChange={(e) => setGuests(e.target.value)} />
          </Field>
        </div>
      )}
      {error !== null && <WriteError message={errorText(error)} />}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 18 }}>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="primary" onClick={add} disabled={busy || !rows.length}>{busy ? 'Adding…' : 'Add menu'}</Button>
      </div>
    </Dialog>
  );
}

export default function FoodSection({ ctx }: { ctx: SectionCtx }) {
  const f = { type: useId(), time: useId(), end: useId(), allergies: useId(), notes: useId() };
  const { detail, on, meta } = ctx;
  const ev = detail.event;
  const ed = useEditor('food', ctx, () => draftOf(detail));
  const dr = ed.draft;
  const [adding, setAdding] = useState(false);
  const badge = SECTION_BADGE[sectionBadge(detail, 'food')];
  const needs = foodTimingNeeds(ed.editing ? dr.food_timing_type : ev.food_timing_type);
  const fieldsOn = on('food.timing_preference') || on('food.allergies') || on('food.notes');

  const save = () => {
    const fields = changedFields(fieldsOf(ed.base), fieldsOf(dr));
    if (!Object.keys(fields).length) {
      ed.cancel();
      return;
    }
    const write: Write = async (note) => fnSend<EventDetail>('PATCH', `/events/${ev.id}`, { version: detail.event.version, note, ...fields });
    void ed.submit(write);
  };

  return (
    <SectionCard
      id="fn-event-food"
      title="Food"
      badge={<Badge tone={badge.tone}>{badge.label}</Badge>}
      actions={!ed.editing && (<><SectionActions ctx={ctx} section="food" />{fieldsOn && ed.editButton}</>)}
      footer={ed.footer(save)}
    >
      {fieldsOn && (!ed.editing ? (
        <FactGrid>
          {on('food.timing_preference') && <Fact label="Food timing">{foodTimingText(ev.food_timing_type, ev.food_time, ev.food_end_time)}</Fact>}
          {on('food.allergies') && <Fact label="Allergies and dietary" wide>{ev.allergies}</Fact>}
          {on('food.notes') && <Fact label="Food notes" wide>{ev.food_notes}</Fact>}
        </FactGrid>
      ) : (
        <FieldGrid>
          {on('food.timing_preference') && (
            <>
              <Field label="Food timing" htmlFor={f.type}>
                <select id={f.type} className="n-select" style={inputStyle} value={dr.food_timing_type} onChange={(e) => ed.update({ food_timing_type: e.target.value })}>
                  <option value="">Not set</option>
                  {meta.food_timing_types.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </Field>
              {needs.time && (
                <Field label={needs.end ? 'From' : 'At'} htmlFor={f.time}>
                  <input id={f.time} type="time" className="n-input" style={inputStyle} value={dr.food_time} onChange={(e) => ed.update({ food_time: e.target.value })} />
                </Field>
              )}
              {needs.end && (
                <Field label="Until" htmlFor={f.end}>
                  <input id={f.end} type="time" className="n-input" style={inputStyle} value={dr.food_end_time} onChange={(e) => ed.update({ food_end_time: e.target.value })} />
                </Field>
              )}
            </>
          )}
          {on('food.allergies') && (
            <Field label="Allergies and dietary" htmlFor={f.allergies} wide>
              <textarea id={f.allergies} className="n-input" rows={3} style={inputStyle} value={dr.allergies} onChange={(e) => ed.update({ allergies: e.target.value })} />
            </Field>
          )}
          {on('food.notes') && (
            <Field label="Food notes" htmlFor={f.notes} wide>
              <textarea id={f.notes} className="n-input" rows={2} style={inputStyle} value={dr.food_notes} onChange={(e) => ed.update({ food_notes: e.target.value })} />
            </Field>
          )}
        </FieldGrid>
      ))}
      {ed.errorStrip}

      {on('food.preorder_menus') && (
        <div style={{ marginTop: fieldsOn ? 16 : 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 500, color: 'var(--muted)' }}>Pre-order menus</span>
            <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>· food {money(detail.money.food_cents, ctx.currency)}</span>
            <Button size="sm" icon={Plus} style={{ marginLeft: 'auto' }} onClick={() => setAdding(true)} disabled={!ctx.canWrite} title={ctx.lockReason ?? undefined}>
              Add a menu
            </Button>
          </div>
          {detail.menus.length ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {detail.menus.map((m) => <MenuCard key={m.id} ctx={ctx} menu={m} />)}
            </div>
          ) : (
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No pre-order menus on this event.</div>
          )}
        </div>
      )}
      {adding && <AddMenuDialog ctx={ctx} onClose={() => setAdding(false)} />}
    </SectionCard>
  );
}
