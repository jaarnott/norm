'use client';

/**
 * Food pre-order menus on an enquiry, per venue: apply a venue template or
 * start a blank menu, set guests / the set-menu price per person, dish
 * quantities (with the default-% split for set menus) and the dish list
 * itself. Every total shown is the API's — after an edit the page shows the
 * figure the server returns, never one worked out here.
 */
import { useEffect, useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import { ArrowDown, ArrowUp, CalendarCheck, Plus, Trash2 } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import { ConfirmDialog } from '../../ui/Dialog';
import IconButton from '../../ui/IconButton';
import { fnGet, fnSend } from '../shared/api';
import { money, parseMoney } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { ErrorNote, Field, FormDialog, FormGrid, inputStyle, Muted, READ_ONLY_HINT, Section, useAction } from './bits';
import { moneyAuto } from './labels';
import { openFnPage } from './nav';
import type { EnquiryDetail, Menu, MenuItem, PreorderTemplate } from './types';

type Open = { kind: 'add' } | { kind: 'settings' | 'dishes' | 'remove'; menu: Menu };

export default function MenusSection({ meta, detail, onChanged, onReload }: {
  meta: FnMeta;
  detail: EnquiryDetail;
  onChanged: () => void;
  onReload: () => void;
}) {
  // Once booked, the food belongs to the event: changes are made (and logged,
  // with a note after confirmation) on the event page.
  const liveEvent = detail.events.find((ev) => !ev.cancelled_at) ?? null;
  const canWrite = meta.can_write && detail.stage !== 'lost' && !liveEvent;
  const currency = meta.company.currency;
  const [open, setOpen] = useState<Open | null>(null);
  const venueNames = useMemo(() => new Map(meta.venues.map((v) => [v.id, v.name])), [meta]);
  const groups = useMemo(() => {
    const by = new Map<string, Menu[]>();
    for (const m of detail.menus) by.set(m.venue_id, [...(by.get(m.venue_id) ?? []), m]);
    return [...by.entries()].map(([venueId, menus]) => ({ venueId, name: venueNames.get(venueId) ?? detail.venue?.name ?? 'Venue', menus }));
  }, [detail, venueNames]);
  const done = () => { setOpen(null); onChanged(); };

  return (
    <Section
      title="Food pre-order"
      meta={detail.menus.length ? `Food ${moneyAuto(detail.food_cents, currency)}` : undefined}
      actions={liveEvent
        ? <Button size="sm" icon={CalendarCheck} onClick={() => openFnPage('events', { event: liveEvent.id })}>Change on the event</Button>
        : canWrite
          ? <Button size="sm" icon={Plus} onClick={() => setOpen({ kind: 'add' })} data-testid="fn-add-menu">Add menu</Button>
          : !meta.can_write ? <Muted>{READ_ONLY_HINT}</Muted> : null}
      padded={false}
      testId="fn-menus"
    >
      <div style={{ padding: '4px 16px 14px' }}>
        {detail.menus.length === 0 && (
          <p style={{ margin: '10px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
            No pre-order yet. Apply one of the venue’s menu templates or start a blank menu; when the customer books, it moves onto the event.
          </p>
        )}
        {groups.map((g) => (
          <div key={g.venueId} style={{ marginTop: 12 }}>
            {groups.length > 1 && <div className="n-eyebrow" style={{ marginBottom: 6 }}>{g.name}</div>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {g.menus.map((m) => (
                <MenuCard key={`${m.id}:${m.version}`} menu={m} currency={currency} canWrite={canWrite} onChanged={onChanged} onReload={onReload} onOpen={(kind) => setOpen({ kind, menu: m })} />
              ))}
            </div>
          </div>
        ))}
      </div>
      {open?.kind === 'add' && <AddMenuDialog meta={meta} detail={detail} onSaved={done} onClose={() => setOpen(null)} />}
      {open?.kind === 'settings' && <MenuSettingsDialog menu={open.menu} currency={currency} onSaved={done} onClose={() => setOpen(null)} onReload={onReload} />}
      {open?.kind === 'dishes' && <DishesDialog menu={open.menu} currency={currency} onSaved={done} onClose={() => setOpen(null)} onReload={onReload} />}
      {open?.kind === 'remove' && (
        <ConfirmDialog
          title="Remove this menu?"
          confirmLabel="Remove menu"
          busyLabel="Removing…"
          danger
          onConfirm={async () => { await fnSend('DELETE', `/menus/${open.menu.id}`); onChanged(); }}
          onClose={() => setOpen(null)}
        >
          “{open.menu.name}” and its quantities come off this enquiry.
        </ConfirmDialog>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// One menu
// ---------------------------------------------------------------------------

function MenuCard({ menu, currency, canWrite, onChanged, onReload, onOpen }: {
  menu: Menu;
  currency: string;
  canWrite: boolean;
  onChanged: () => void;
  onReload: () => void;
  onOpen: (kind: 'settings' | 'dishes' | 'remove') => void;
}) {
  // Quantities being typed, by item id; saved together.
  const [draft, setDraft] = useState<Record<string, string>>({});
  const { busy, error, setError, run } = useAction();
  const dirty = Object.entries(draft).some(([id, v]) => {
    const item = menu.items.find((i) => i.id === id);
    return item && String(item.quantity) !== v;
  });
  const dishes = menu.items.filter((i) => i.kind === 'item');
  const ordered = dishes.reduce((n, i) => n + (i.quantity || 0), 0);

  const saveQuantities = () => run('qty', async () => {
    const quantities: Record<string, number> = {};
    for (const [id, v] of Object.entries(draft)) {
      const n = parseInt(v, 10);
      quantities[id] = Number.isFinite(n) && n >= 0 ? n : 0;
    }
    await fnSend('PUT', `/menus/${menu.id}/quantities`, { version: menu.version, quantities });
  }).then((r) => { if (r !== undefined) { setDraft({}); onChanged(); } });

  const distribute = () => run('split', () => fnSend('POST', `/menus/${menu.id}/distribute`, { version: menu.version }))
    .then((r) => { if (r !== undefined) { setDraft({}); onChanged(); } });

  return (
    <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius)', background: 'var(--bg)' }} data-testid="fn-menu">
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', padding: '12px 14px' }}>
        <div style={{ minWidth: 0, flex: '1 1 220px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 600 }}>{menu.name}</span>
            <Badge tone={menu.is_set_menu ? 'info' : 'neutral'}>{menu.is_set_menu ? 'Set menu' : 'À la carte'}</Badge>
            {menu.event_id && <Badge tone="ok">On the event</Badge>}
          </div>
          <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
            {menu.is_set_menu
              ? `${menu.price_per_person_cents !== null ? money(menu.price_per_person_cents, currency) : 'No price'} per person · ${menu.guest_count} guests`
              : `${menu.guest_count} guests · ${ordered} dishes ordered`}
          </div>
          {menu.notes && <div style={{ marginTop: 4, fontSize: 'var(--fs-sm)', color: 'var(--muted)', whiteSpace: 'pre-wrap' }}>{menu.notes}</div>}
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontWeight: 600, fontSize: 'var(--fs-md)' }}>{moneyAuto(menu.total_cents, currency)}</div>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>menu total</div>
        </div>
      </div>
      {menu.items.length > 0 && (
        <div style={{ overflowX: 'auto', borderTop: '1px solid var(--line-soft)' }}>
          <table className="n-table" style={{ fontSize: 'var(--fs-sm)' }} aria-label={`Dishes on ${menu.name}`}>
            <thead>
              <tr>
                <th scope="col">Dish</th>
                {menu.is_set_menu && <th scope="col" className="num" style={{ width: 90 }}>Suggested</th>}
                {!menu.is_set_menu && <th scope="col" className="num" style={{ width: 90 }}>Price</th>}
                <th scope="col" className="num" style={{ width: 96 }}>Quantity</th>
                {!menu.is_set_menu && <th scope="col" className="num" style={{ width: 100 }}>Line total</th>}
              </tr>
            </thead>
            <tbody>
              {menu.items.map((it) => it.kind === 'header' ? (
                <tr key={it.id}>
                  <td colSpan={menu.is_set_menu ? 3 : 4} style={headerCell}>{it.name}</td>
                </tr>
              ) : (
                <tr key={it.id}>
                  <td style={{ minWidth: 200 }}>
                    <span>{it.name}</span>
                    {it.dietaries?.length > 0 && <span style={{ marginLeft: 6, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{it.dietaries.join(' ')}</span>}
                    {it.description && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{it.description}</div>}
                  </td>
                  {menu.is_set_menu && <td className="num" style={{ color: 'var(--muted)' }}>{it.suggested_quantity ?? '—'}</td>}
                  {!menu.is_set_menu && <td className="num">{it.unit_price_cents !== null ? money(it.unit_price_cents, currency) : '—'}</td>}
                  <td className="num">
                    {canWrite ? (
                      <input
                        type="number"
                        min={0}
                        inputMode="numeric"
                        className="n-input"
                        aria-label={`Quantity of ${it.name}`}
                        value={draft[it.id] ?? String(it.quantity)}
                        onChange={(e) => setDraft((d) => ({ ...d, [it.id]: e.target.value }))}
                        style={{ width: 72, textAlign: 'right' }}
                      />
                    ) : it.quantity}
                  </td>
                  {!menu.is_set_menu && <td className="num">{it.line_total_cents !== null ? money(it.line_total_cents, currency) : '—'}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ padding: '8px 14px 12px', borderTop: '1px solid var(--line-soft)' }}>
        <ErrorNote error={error} onReload={onReload} onDismiss={() => setError(null)} />
        {canWrite && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginTop: error ? 8 : 0 }}>
            {dirty && (
              <>
                <Button size="sm" variant="primary" onClick={saveQuantities} disabled={!!busy}>{busy === 'qty' ? 'Saving…' : 'Save quantities'}</Button>
                <Button size="sm" variant="quiet" onClick={() => setDraft({})} disabled={!!busy}>Discard</Button>
                <Muted>The total updates when you save.</Muted>
              </>
            )}
            {!dirty && menu.is_set_menu && (
              <Button size="sm" onClick={distribute} disabled={!!busy} title="Fill dish quantities from each section's default split of the guest count">
                {busy === 'split' ? 'Filling…' : 'Fill from default split'}
              </Button>
            )}
            {!dirty && <Button size="sm" variant="quiet" onClick={() => onOpen('settings')}>Guests and price</Button>}
            {!dirty && <Button size="sm" variant="quiet" onClick={() => onOpen('dishes')}>Edit dishes</Button>}
            {!dirty && <Button size="sm" variant="quiet" onClick={() => onOpen('remove')}>Remove</Button>}
          </div>
        )}
      </div>
    </div>
  );
}

const headerCell: CSSProperties = {
  paddingTop: 12, fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)', background: 'var(--surface)',
};

// ---------------------------------------------------------------------------
// Add a menu (template or blank)
// ---------------------------------------------------------------------------

function AddMenuDialog({ meta, detail, onSaved, onClose }: {
  meta: FnMeta;
  detail: EnquiryDetail;
  onSaved: () => void;
  onClose: () => void;
}) {
  const optionVenues = detail.options.map((o) => o.venue_id);
  const [venueId, setVenueId] = useState(detail.enquiry.venue_id ?? optionVenues[0] ?? (meta.venues.length === 1 ? meta.venues[0].id : ''));
  const [mode, setMode] = useState<'template' | 'blank'>('template');
  // Templates by venue: null while the chosen venue's are loading.
  const [loaded, setLoaded] = useState<{ venueId: string; rows: PreorderTemplate[]; error: string | null } | null>(null);
  const [templateId, setTemplateId] = useState('');
  const [guests, setGuests] = useState(detail.enquiry.guests ? String(detail.enquiry.guests) : '');
  const [name, setName] = useState('');
  const [setMenu, setSetMenu] = useState(false);
  const [pp, setPp] = useState('');

  useEffect(() => {
    if (!venueId) return;
    let alive = true;
    fnGet<{ rows: PreorderTemplate[] }>('/preorder-templates', { venue_id: venueId })
      .then((d) => { if (alive) setLoaded({ venueId, rows: d.rows.filter((t) => t.active), error: null }); })
      .catch((e: unknown) => { if (alive) setLoaded({ venueId, rows: [], error: e instanceof Error ? e.message : String(e) }); });
    return () => { alive = false; };
  }, [venueId]);
  const templates = !venueId ? [] : loaded?.venueId === venueId ? loaded.rows : null;
  const templatesError = loaded?.venueId === venueId ? loaded.error : null;

  const guestCount = parseInt(guests, 10);
  const submit = async () => {
    const body: Record<string, unknown> = mode === 'template'
      ? { template_id: templateId, guest_count: Number.isFinite(guestCount) ? guestCount : 0 }
      : {
        name: name.trim(),
        venue_id: venueId || null,
        is_set_menu: setMenu,
        price_per_person_cents: setMenu ? parseMoney(pp) : null,
        guest_count: Number.isFinite(guestCount) ? guestCount : 0,
        items: [],
      };
    await fnSend('POST', `/enquiries/${detail.enquiry.id}/menus`, body);
    onSaved();
  };
  const ready = !!venueId && (mode === 'template' ? !!templateId : !!name.trim());
  const picked = templates?.find((t) => t.id === templateId);

  return (
    <FormDialog title="Add a pre-order menu" width={520} submitLabel="Add menu" submitDisabled={!ready} onSubmit={submit} onClose={onClose}>
      <FormGrid>
        <Field label="Venue">
          {(id) => (
            <select id={id} className="n-select" style={inputStyle} value={venueId} onChange={(e) => { setVenueId(e.target.value); setTemplateId(''); }}>
              {!venueId && <option value="">Choose a venue</option>}
              {meta.venues.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          )}
        </Field>
        <Field label="Guests">{(id) => <input id={id} type="number" min={0} inputMode="numeric" className="n-input" style={inputStyle} value={guests} onChange={(e) => setGuests(e.target.value)} />}</Field>
      </FormGrid>
      <div role="radiogroup" aria-label="Start from" style={{ display: 'flex', gap: 6 }}>
        <Button size="sm" variant={mode === 'template' ? 'primary' : 'secondary'} aria-pressed={mode === 'template'} onClick={() => setMode('template')}>From a template</Button>
        <Button size="sm" variant={mode === 'blank' ? 'primary' : 'secondary'} aria-pressed={mode === 'blank'} onClick={() => setMode('blank')}>Blank menu</Button>
      </div>
      {mode === 'template' ? (
        <Field
          label="Template"
          hint={templatesError ? `Couldn’t load templates: ${templatesError}` : templates && templates.length === 0 && venueId ? 'This venue has no menu templates yet — add them in Functions settings, or start a blank menu.' : picked?.description ?? undefined}
        >
          {(id) => (
            <select id={id} className="n-select" style={inputStyle} value={templateId} onChange={(e) => setTemplateId(e.target.value)} disabled={!templates?.length}>
              <option value="">{templates === null ? 'Loading…' : 'Choose a template'}</option>
              {(templates ?? []).map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}{t.is_set_menu && t.price_per_person_cents !== null ? ` — ${money(t.price_per_person_cents, meta.company.currency)} pp` : ''}
                </option>
              ))}
            </select>
          )}
        </Field>
      ) : (
        <>
          <Field label="Menu name">{(id) => <input id={id} className="n-input" style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
            <input type="checkbox" checked={setMenu} onChange={(e) => setSetMenu(e.target.checked)} />
            Set menu (priced per person)
          </label>
          {setMenu && <Field label={`Price per person (${meta.company.currency})`}>{(id) => <input id={id} className="n-input" style={inputStyle} inputMode="decimal" value={pp} onChange={(e) => setPp(e.target.value)} />}</Field>}
          <Muted>Add the dishes with “Edit dishes” once it’s created.</Muted>
        </>
      )}
    </FormDialog>
  );
}

// ---------------------------------------------------------------------------
// Guests, price, name
// ---------------------------------------------------------------------------

function MenuSettingsDialog({ menu, currency, onSaved, onClose, onReload }: { menu: Menu; currency: string; onSaved: () => void; onClose: () => void; onReload: () => void }) {
  const [name, setName] = useState(menu.name);
  const [guests, setGuests] = useState(String(menu.guest_count));
  const [setMenu, setSetMenu] = useState(menu.is_set_menu);
  const [pp, setPp] = useState(menu.price_per_person_cents !== null ? String(menu.price_per_person_cents / 100) : '');
  const [notes, setNotes] = useState(menu.notes ?? '');
  const submit = async () => {
    const body: Record<string, unknown> = { version: menu.version };
    const g = parseInt(guests, 10);
    if (name.trim() && name.trim() !== menu.name) body.name = name.trim();
    if (Number.isFinite(g) && g !== menu.guest_count) body.guest_count = g;
    if (setMenu !== menu.is_set_menu) body.is_set_menu = setMenu;
    const cents = setMenu ? parseMoney(pp) : null;
    if (setMenu && cents !== menu.price_per_person_cents) body.price_per_person_cents = cents;
    if ((notes.trim() || null) !== (menu.notes ?? null)) body.notes = notes.trim() || null;
    if (Object.keys(body).length > 1) await fnSend('PATCH', `/menus/${menu.id}`, body);
    onSaved();
  };
  return (
    <FormDialog title="Guests and price" submitLabel="Save" submitDisabled={!name.trim()} onSubmit={submit} onClose={onClose} onReload={onReload}>
      <Field label="Menu name">{(id) => <input id={id} className="n-input" style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
      <FormGrid>
        <Field label="Guests">{(id) => <input id={id} type="number" min={0} inputMode="numeric" className="n-input" style={inputStyle} value={guests} onChange={(e) => setGuests(e.target.value)} autoFocus />}</Field>
        {setMenu && <Field label={`Price per person (${currency})`}>{(id) => <input id={id} className="n-input" style={inputStyle} inputMode="decimal" value={pp} onChange={(e) => setPp(e.target.value)} />}</Field>}
      </FormGrid>
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        <input type="checkbox" checked={setMenu} onChange={(e) => setSetMenu(e.target.checked)} />
        Set menu (priced per person; dishes are unpriced)
      </label>
      <Field label="Notes">{(id) => <textarea id={id} className="n-input" rows={2} style={inputStyle} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
    </FormDialog>
  );
}

// ---------------------------------------------------------------------------
// The dish list
// ---------------------------------------------------------------------------

interface DishDraft {
  key: string;
  kind: 'header' | 'item';
  name: string;
  price: string;
  quantity: number;
  source: MenuItem | null;
}

let draftSeq = 0;

function DishesDialog({ menu, currency, onSaved, onClose, onReload }: { menu: Menu; currency: string; onSaved: () => void; onClose: () => void; onReload: () => void }) {
  const [rows, setRows] = useState<DishDraft[]>(() => menu.items.map((it) => ({
    key: it.id,
    kind: it.kind,
    name: it.name,
    price: it.unit_price_cents !== null ? String(it.unit_price_cents / 100) : '',
    quantity: it.quantity,
    source: it,
  })));
  const update = (key: string, patch: Partial<DishDraft>) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const move = (i: number, by: number) => setRows((rs) => {
    const j = i + by;
    if (j < 0 || j >= rs.length) return rs;
    const next = [...rs];
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  });
  const add = (kind: 'header' | 'item') => setRows((rs) => [...rs, { key: `new-${++draftSeq}`, kind, name: '', price: '', quantity: 0, source: null }]);

  const submit = async () => {
    const items = rows.filter((r) => r.name.trim()).map((r) => ({
      kind: r.kind,
      name: r.name.trim(),
      description: r.source?.description ?? null,
      quantity: r.kind === 'item' ? r.quantity : 0,
      unit_price_cents: r.kind === 'item' && !menu.is_set_menu ? parseMoney(r.price) : null,
      default_percent: r.source?.default_percent ?? null,
      dietaries: r.source?.dietaries ?? [],
      loaded_recipe_id: r.source?.loaded_recipe_id ?? null,
      loaded_stock_item_id: r.source?.loaded_stock_item_id ?? null,
      template_item_id: r.source?.template_item_id ?? null,
    }));
    await fnSend('PUT', `/menus/${menu.id}/items`, { version: menu.version, items });
    onSaved();
  };

  return (
    <FormDialog title={`Dishes on ${menu.name}`} width={640} submitLabel="Save dishes" onSubmit={submit} onClose={onClose} onReload={onReload}>
      <Muted>
        {menu.is_set_menu
          ? 'A set menu is priced per person, so dishes carry no price. Section headings group the dishes.'
          : 'Each dish adds quantity × price to the menu total.'}
      </Muted>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {rows.map((r, i) => (
          <div key={r.key} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <input
              className="n-input"
              aria-label={r.kind === 'header' ? 'Section heading' : 'Dish name'}
              placeholder={r.kind === 'header' ? 'Section heading' : 'Dish'}
              value={r.name}
              onChange={(e) => update(r.key, { name: e.target.value })}
              style={{ flex: 1, minWidth: 0, fontWeight: r.kind === 'header' ? 600 : 400 }}
            />
            {r.kind === 'item' && !menu.is_set_menu && (
              <input className="n-input" aria-label={`Price (${currency})`} placeholder="Price" inputMode="decimal" value={r.price}
                onChange={(e) => update(r.key, { price: e.target.value })} style={{ width: 90, textAlign: 'right' }} />
            )}
            <IconButton icon={ArrowUp} label="Move up" iconSize={16} onClick={() => move(i, -1)} disabled={i === 0} />
            <IconButton icon={ArrowDown} label="Move down" iconSize={16} onClick={() => move(i, 1)} disabled={i === rows.length - 1} />
            <IconButton icon={Trash2} label="Remove" iconSize={16} onClick={() => setRows((rs) => rs.filter((x) => x.key !== r.key))} />
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        <Button size="sm" icon={Plus} onClick={() => add('item')}>Add dish</Button>
        <Button size="sm" variant="quiet" icon={Plus} onClick={() => add('header')}>Add heading</Button>
      </div>
    </FormDialog>
  );
}
