'use client';

/**
 * Pre-order menu templates: the menus staff add to an enquiry or event —
 * sections and dishes, set menus priced per person with a default % per dish
 * (how a section's guests split across its dishes), à la carte menus priced
 * per dish, dietary tags, and each dish's LoadedHub recipe so the kitchen
 * can cost and prep it. A template that menus were built from is retired
 * on delete, never removed.
 */
import { useEffect, useId, useState } from 'react';
import { CircleAlert, Copy, Link2, Plus, Trash2, Unlink } from 'lucide-react';
import Badge from '../../ui/Badge';
import BackLink from '../../ui/BackLink';
import Button from '../../ui/Button';
import Dialog, { ConfirmDialog } from '../../ui/Dialog';
import Icon from '../../ui/Icon';
import IconButton from '../../ui/IconButton';
import PageState from '../../ui/PageState';
import { fnGet, fnSend } from '../shared/api';
import { money, parseMoney } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { errorText } from './errors';
import {
  addDishTo, blankDish, blankHeading, moveRow, sectionTotal, sectionsOf, splitSectionEvenly, toDraft, toPayload, type DraftItem,
} from './menuTemplate';
import type { MenuTemplate } from './types';
import { CheckboxRow, Chip, Field, FieldGrid, MoveButtons, Notice, Panel, ReadOnlyNote, SaveRow, Section, fullWidth, help, muted } from './ui';

interface ListState {
  key: string;
  rows: MenuTemplate[] | null;
  error: string | null;
}

export default function MenuTemplates({ venueId, meta, canWrite }: { venueId: string; meta: FnMeta; canWrite: boolean }) {
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<ListState | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [showRetired, setShowRetired] = useState(false);
  const [cloning, setCloning] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const key = `${venueId}:${tick}`;
  const toggleId = useId();

  useEffect(() => {
    let alive = true;
    fnGet<{ rows: MenuTemplate[] }>('/preorder-templates', { venue_id: venueId, include_inactive: true }).then(
      (r) => alive && setState({ key, rows: r.rows, error: null }),
      (e) => alive && setState({ key, rows: null, error: errorText(e) }),
    );
    return () => {
      alive = false;
    };
  }, [venueId, key]);

  const rows = state?.rows ?? null;
  const currency = meta.company.currency;
  const upsert = (t: MenuTemplate) =>
    setState((s) => (s && s.rows ? { ...s, rows: s.rows.some((r) => r.id === t.id) ? s.rows.map((r) => (r.id === t.id ? t : r)) : [...s.rows, t] } : s));

  if (open) {
    const template = open === 'new' ? null : rows?.find((r) => r.id === open) ?? null;
    if (open !== 'new' && !template) return <PageState kind="loading" title="Loading menu…" />;
    return (
      <MenuTemplateEditor
        key={open}
        template={template}
        venueId={venueId}
        meta={meta}
        canWrite={canWrite}
        onBack={() => { setOpen(null); setFlash(null); }}
        onSaved={(t) => { upsert(t); setOpen(t.id); }}
        onDeleted={(result) => {
          setOpen(null);
          setTick((n) => n + 1);
          setFlash(result === 'retired'
            ? 'Menus were already built from that template, so it was retired instead — they keep their copy.'
            : 'Menu template deleted.');
        }}
      />
    );
  }

  if (state?.error && !rows) return <PageState kind="error" title="Couldn’t load the menu templates" detail={state.error} />;
  if (!rows) return <PageState kind="loading" title="Loading menu templates…" />;

  const retired = rows.filter((r) => !r.active).length;
  const visible = rows.filter((r) => showRetired || r.active);

  return (
    <Section
      title="Pre-order menus"
      meta={`${rows.length - retired} menu${rows.length - retired === 1 ? '' : 's'} staff can add to an enquiry or event`}
      actions={canWrite ? (
        <>
          <Button size="sm" icon={Copy} onClick={() => setCloning(true)}>Clone a Loaded menu</Button>
          <Button size="sm" variant="primary" icon={Plus} onClick={() => setOpen('new')}>New menu</Button>
        </>
      ) : <ReadOnlyNote />}
    >
      {flash && <Notice tone="info" style={{ marginBottom: 10 }}>{flash}</Notice>}
      {retired > 0 && (
        <div style={{ marginBottom: 8 }}>
          <CheckboxRow id={toggleId} checked={showRetired} onChange={setShowRetired} label={`Show retired menus (${retired})`} />
        </div>
      )}
      {visible.length === 0 ? (
        <Panel><PageState kind="empty" title="No menu templates yet" detail="Create one, or clone a menu from LoadedHub." /></Panel>
      ) : (
        <div className="n-card" style={{ overflowX: 'auto' }}>
          <table className="n-table">
            <thead>
              <tr>
                <th>Menu</th>
                <th>Pricing</th>
                <th className="num">Dishes</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((t) => {
                const dishes = t.items.filter((i) => i.kind === 'item').length;
                return (
                  <tr key={t.id} onClick={() => setOpen(t.id)} style={{ cursor: 'pointer' }}>
                    <td style={{ minWidth: 180 }}>
                      <button type="button" onClick={(e) => { e.stopPropagation(); setOpen(t.id); }}
                        style={{ padding: 0, border: 'none', background: 'none', font: 'inherit', fontWeight: 600, color: 'var(--text)', cursor: 'pointer', textAlign: 'left' }}>
                        {t.name}
                      </button>
                      {t.description && <div style={{ ...muted, marginTop: 2 }}>{t.description}</div>}
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      {t.is_set_menu ? `Set menu · ${money(t.price_per_person_cents, currency)} pp` : 'À la carte'}
                    </td>
                    <td className="num">{dishes}</td>
                    <td>
                      <span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
                        {t.active ? <Badge tone="ok">Active</Badge> : <Badge>Retired</Badge>}
                        {t.warnings.length > 0 && <Badge tone="warn" title={t.warnings.join('\n')}>{t.warnings.length} to check</Badge>}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {cloning && (
        <CloneLoadedDialog
          venueId={venueId}
          currency={currency}
          onClose={() => setCloning(false)}
          onCloned={(t) => { setCloning(false); upsert(t); setOpen(t.id); }}
        />
      )}
    </Section>
  );
}

function MenuTemplateEditor({ template, venueId, meta, canWrite, onBack, onSaved, onDeleted }: {
  template: MenuTemplate | null;
  venueId: string;
  meta: FnMeta;
  canWrite: boolean;
  onBack: () => void;
  onSaved: (t: MenuTemplate) => void;
  onDeleted: (result: 'deleted' | 'retired') => void;
}) {
  const id = useId();
  const currency = meta.company.currency;
  const [name, setName] = useState(template?.name ?? '');
  const [description, setDescription] = useState(template?.description ?? '');
  const [isSetMenu, setIsSetMenu] = useState(template?.is_set_menu ?? false);
  const [pp, setPp] = useState(template?.price_per_person_cents != null ? (template.price_per_person_cents / 100).toFixed(2) : '');
  const [active, setActive] = useState(template?.active ?? true);
  const [items, setItems] = useState<DraftItem[]>(() => toDraft(template?.items ?? []));
  const [touched, setTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [linking, setLinking] = useState<string | null>(null);
  const [recipeNames, setRecipeNames] = useState<Record<string, string>>({});
  const [confirmDelete, setConfirmDelete] = useState(false);

  const touch = () => {
    setTouched(true);
    setSaved(false);
    setError(null);
  };
  const editItems = (next: DraftItem[]) => {
    setItems(next);
    touch();
  };
  const setItem = (uid: string, p: Partial<DraftItem>) => editItems(items.map((it) => (it.uid === uid ? { ...it, ...p } : it)));

  const sections = sectionsOf(items);
  const ppCents = pp.trim() ? parseMoney(pp) : null;
  const dirty = touched || !template;

  const save = async () => {
    if (!name.trim()) {
      setError('Give the menu a name.');
      return;
    }
    if (pp.trim() && (ppCents === null || ppCents < 0)) {
      setError('The price per person is an amount like 65 or 65.50.');
      return;
    }
    setSaving(true);
    setError(null);
    const body = {
      name: name.trim(),
      description: description.trim() || null,
      is_set_menu: isSetMenu,
      price_per_person_cents: isSetMenu ? ppCents : null,
      items: toPayload(items, isSetMenu),
    };
    try {
      const t = template
        ? await fnSend<MenuTemplate>('PATCH', `/preorder-templates/${template.id}`, { version: template.version, ...body, active })
        : await fnSend<MenuTemplate>('POST', '/preorder-templates', { venue_id: venueId, ...body });
      setTouched(false);
      setSaved(true);
      setItems(toDraft(t.items));
      onSaved(t);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const addDish = (sectionIndex: number | null) => {
    const section = sectionIndex === null ? null : sections[sectionIndex];
    let next = addDishTo(items, section, blankDish());
    if (isSetMenu && section) {
      const before = sectionTotal(items, section);
      // Keep an evenly split section even; leave a hand-tuned one alone.
      if (before === 100 || before === 0) {
        const grown = sectionsOf(next).find((s) => s.headerIndex === section.headerIndex);
        if (grown) next = splitSectionEvenly(next, grown);
      }
    }
    editItems(next);
  };

  const dishCount = items.filter((i) => i.kind === 'item').length;
  const linked = items.filter((i) => i.kind === 'item' && (i.loaded_recipe_id || i.loaded_stock_item_id)).length;

  return (
    <div style={{ maxWidth: 900 }}>
      <div style={{ marginBottom: 10 }}><BackLink label="Pre-order menus" onClick={onBack} /></div>
      <Section
        title={template ? template.name : 'New menu'}
        meta={`${dishCount} dish${dishCount === 1 ? '' : 'es'} · ${linked} linked to LoadedHub`}
        actions={template && canWrite ? (
          <Button size="sm" variant="danger" icon={Trash2} onClick={() => setConfirmDelete(true)}>Delete</Button>
        ) : undefined}
      >
        {template && template.warnings.length > 0 && !touched && (
          <Notice tone="warn" style={{ marginBottom: 12 }}>
            {template.warnings.map((w) => <div key={w}>{w}</div>)}
          </Notice>
        )}
        <Panel>
          <FieldGrid min={240}>
            <Field label="Name" htmlFor={`${id}-name`}>
              <input id={`${id}-name`} className="n-input" style={fullWidth} value={name} disabled={!canWrite}
                onChange={(e) => { setName(e.target.value); touch(); }} />
            </Field>
            <Field label="Description" htmlFor={`${id}-desc`}>
              <input id={`${id}-desc`} className="n-input" style={fullWidth} value={description} disabled={!canWrite}
                onChange={(e) => { setDescription(e.target.value); touch(); }} placeholder="For staff choosing a menu" />
            </Field>
          </FieldGrid>
          <div style={{ marginTop: 14, display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'flex-start' }}>
            <CheckboxRow
              checked={isSetMenu}
              disabled={!canWrite}
              onChange={(on) => { setIsSetMenu(on); touch(); }}
              label="Set menu"
              description={isSetMenu ? 'Priced per person; each dish has a default % of its section.' : 'À la carte: each dish has its own price.'}
            />
            {template && (
              <CheckboxRow checked={active} disabled={!canWrite} onChange={(on) => { setActive(on); touch(); }} label="Active"
                description="Retired menus can’t be added to new enquiries." />
            )}
          </div>
          {isSetMenu && (
            <div style={{ marginTop: 14, maxWidth: 240 }}>
              <Field label={`Price per person (${currency})`} htmlFor={`${id}-pp`}>
                <input id={`${id}-pp`} className="n-input" style={fullWidth} inputMode="decimal" value={pp} disabled={!canWrite}
                  onChange={(e) => { setPp(e.target.value); touch(); }} placeholder="65.00" />
              </Field>
            </div>
          )}
        </Panel>

        <h3 style={{ margin: '22px 0 8px', fontSize: 'var(--fs-base)', fontWeight: 600 }}>Sections and dishes</h3>
        {items.length === 0 && <p style={muted}>No dishes yet — add a section heading, then its dishes.</p>}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {sections.map((s, si) => {
            const total = sectionTotal(items, s);
            const header = s.headerIndex !== null ? items[s.headerIndex] : null;
            return (
              <div key={header?.uid ?? 'top'} className="n-card" style={{ overflow: 'hidden' }}>
                {header && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', padding: '8px 10px', background: 'var(--surface-alt)', borderBottom: '1px solid var(--line)' }}>
                    <input
                      className="n-input"
                      aria-label="Section name"
                      value={header.name}
                      placeholder="Section, e.g. Mains"
                      disabled={!canWrite}
                      onChange={(e) => setItem(header.uid, { name: e.target.value })}
                      style={{ flex: '1 1 180px', minWidth: 0, fontWeight: 600 }}
                    />
                    {isSetMenu && s.itemIndices.length > 0 && (
                      <Badge tone={total === 100 ? 'ok' : 'warn'} title="The default % of this section’s dishes should add up to 100">
                        {total}%{total === 100 ? '' : ' of 100'}
                      </Badge>
                    )}
                    {isSetMenu && canWrite && s.itemIndices.length > 1 && (
                      <Button size="sm" variant="quiet" onClick={() => editItems(splitSectionEvenly(items, s))}>Split evenly</Button>
                    )}
                    {canWrite && (
                      <>
                        <MoveButtons index={s.headerIndex as number} count={items.length} what={`heading ${header.name || ''}`.trim()}
                          onMove={(d) => editItems(moveRow(items, s.headerIndex as number, d))} />
                        <IconButton icon={Trash2} iconSize={16} label={`Remove heading ${header.name}`.trim()}
                          onClick={() => editItems(items.filter((it) => it.uid !== header.uid))} />
                      </>
                    )}
                  </div>
                )}
                {!header && isSetMenu && s.itemIndices.length > 0 && (
                  <div style={{ padding: '6px 10px', borderBottom: '1px solid var(--line)', display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={muted}>No heading</span>
                    <Badge tone={total === 100 ? 'ok' : 'warn'}>{total}%{total === 100 ? '' : ' of 100'}</Badge>
                  </div>
                )}
                {s.itemIndices.map((idx) => (
                  <DishRow
                    key={items[idx].uid}
                    item={items[idx]}
                    index={idx}
                    count={items.length}
                    isSetMenu={isSetMenu}
                    dietaryCodes={meta.dietary_codes}
                    canWrite={canWrite}
                    currency={currency}
                    recipeName={recipeNames[items[idx].uid]}
                    onChange={(p) => setItem(items[idx].uid, p)}
                    onMove={(d) => editItems(moveRow(items, idx, d))}
                    onRemove={() => editItems(items.filter((it) => it.uid !== items[idx].uid))}
                    onLink={() => setLinking(items[idx].uid)}
                  />
                ))}
                {canWrite && (
                  <div style={{ padding: '6px 10px' }}>
                    <Button size="sm" variant="quiet" icon={Plus} onClick={() => addDish(si)}>
                      Add dish{header?.name ? ` to ${header.name}` : ''}
                    </Button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {canWrite && (
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            <Button size="sm" icon={Plus} onClick={() => editItems([...items, blankHeading()])}>Add section heading</Button>
            {sections.length === 0 && <Button size="sm" variant="quiet" icon={Plus} onClick={() => addDish(null)}>Add dish</Button>}
          </div>
        )}
        <SaveRow canWrite={canWrite} dirty={dirty} saving={saving} saved={saved} error={error} onSave={save}
          label={template ? 'Save menu' : 'Create menu'} extra={!template ? <Button variant="quiet" onClick={onBack}>Cancel</Button> : undefined} />
      </Section>

      {linking && (
        <RecipeLinkDialog
          venueId={venueId}
          initialQuery={items.find((i) => i.uid === linking)?.name ?? ''}
          onClose={() => setLinking(null)}
          onPick={(r) => {
            const uid = linking;
            const dish = items.find((i) => i.uid === uid);
            setItem(uid, { loaded_recipe_id: r.id, loaded_stock_item_id: null, ...(dish && !dish.name.trim() ? { name: r.name } : {}) });
            setRecipeNames((m) => ({ ...m, [uid]: r.name }));
            setLinking(null);
          }}
        />
      )}
      {confirmDelete && template && (
        <ConfirmDialog
          title={`Delete ${template.name}?`}
          confirmLabel="Delete menu"
          busyLabel="Deleting…"
          danger
          onClose={() => setConfirmDelete(false)}
          onConfirm={async () => {
            let result: 'deleted' | 'retired';
            try {
              result = (await fnSend<{ result: 'deleted' | 'retired' }>('DELETE', `/preorder-templates/${template.id}`)).result;
            } catch (e) {
              throw new Error(errorText(e));
            }
            onDeleted(result);
          }}
        >
          <p style={{ margin: 0 }}>If enquiries or events already have a menu built from it, it’s retired instead (they keep their copy).</p>
        </ConfirmDialog>
      )}
    </div>
  );
}

function DishRow({ item, index, count, isSetMenu, dietaryCodes, canWrite, currency, recipeName, onChange, onMove, onRemove, onLink }: {
  item: DraftItem;
  index: number;
  count: number;
  isSetMenu: boolean;
  dietaryCodes: string[];
  canWrite: boolean;
  currency: string;
  recipeName?: string;
  onChange: (p: Partial<DraftItem>) => void;
  onMove: (d: -1 | 1) => void;
  onRemove: () => void;
  onLink: () => void;
}) {
  const id = useId();
  const [price, setPrice] = useState(item.unit_price_cents != null ? (item.unit_price_cents / 100).toFixed(2) : '');
  const [tag, setTag] = useState('');
  const custom = item.dietaries.filter((d) => !dietaryCodes.includes(d));
  const toggleCode = (code: string) =>
    onChange({ dietaries: item.dietaries.includes(code) ? item.dietaries.filter((d) => d !== code) : [...item.dietaries, code] });
  const addTag = () => {
    const t = tag.trim();
    if (!t) return;
    const value = dietaryCodes.includes(t.toUpperCase()) ? t.toUpperCase() : t.slice(0, 24);
    if (!item.dietaries.includes(value)) onChange({ dietaries: [...item.dietaries, value] });
    setTag('');
  };
  const linkedTo = item.loaded_recipe_id ? (recipeName ?? 'a Loaded recipe') : item.loaded_stock_item_id ? 'a Loaded stock item' : null;

  return (
    <div style={{ padding: '10px', borderBottom: '1px solid var(--line-soft)' }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
        <input className="n-input" aria-label="Dish name" value={item.name} placeholder="Dish name" disabled={!canWrite}
          onChange={(e) => onChange({ name: e.target.value })} style={{ flex: '1 1 200px', minWidth: 0 }} />
        {isSetMenu ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <input className="n-input" aria-label={`Default % for ${item.name || 'dish'}`} inputMode="numeric" disabled={!canWrite}
              value={item.default_percent ?? ''} style={{ width: 64, textAlign: 'right' }}
              onChange={(e) => {
                const v = e.target.value.replace(/[^0-9]/g, '');
                onChange({ default_percent: v === '' ? null : Math.min(100, Number(v)) });
              }} />
            <span style={muted}>%</span>
          </span>
        ) : (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <span style={muted}>{currency === 'NZD' || currency === 'AUD' || currency === 'USD' ? '$' : currency}</span>
            <input className="n-input" aria-label={`Price of ${item.name || 'dish'}`} inputMode="decimal" disabled={!canWrite}
              value={price} placeholder="0.00" style={{ width: 84, textAlign: 'right' }}
              onChange={(e) => {
                setPrice(e.target.value);
                const cents = e.target.value.trim() ? parseMoney(e.target.value) : null;
                onChange({ unit_price_cents: cents !== null && cents >= 0 ? cents : null });
              }} />
          </span>
        )}
        {canWrite && (
          <span style={{ display: 'inline-flex' }}>
            <MoveButtons index={index} count={count} onMove={onMove} what={item.name || 'dish'} />
            <IconButton icon={Trash2} iconSize={16} label={`Remove ${item.name || 'dish'}`} onClick={onRemove} />
          </span>
        )}
      </div>
      <input className="n-input" aria-label={`Description of ${item.name || 'dish'}`} value={item.description ?? ''} disabled={!canWrite}
        placeholder="Description customers see (optional)" onChange={(e) => onChange({ description: e.target.value })}
        style={{ ...fullWidth, marginTop: 6, fontSize: 'var(--fs-sm)' }} />
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginTop: 8 }}>
        {dietaryCodes.map((code) => (
          <Chip key={code} on={item.dietaries.includes(code)} onClick={() => toggleCode(code)} disabled={!canWrite}>{code}</Chip>
        ))}
        {custom.map((t) => (
          <Chip key={t} on onClick={() => onChange({ dietaries: item.dietaries.filter((d) => d !== t) })} disabled={!canWrite} title={`Remove ${t}`}>
            {t} ×
          </Chip>
        ))}
        {canWrite && (
          <span style={{ display: 'inline-flex', gap: 4 }}>
            <label htmlFor={`${id}-tag`} style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>Add an allergen or dietary tag</label>
            <input id={`${id}-tag`} className="n-input" value={tag} placeholder="Allergen…" maxLength={24}
              onChange={(e) => setTag(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTag(); } }}
              style={{ width: 110, height: 30, fontSize: 'var(--fs-sm)' }} />
            {tag.trim() && <Button size="sm" variant="quiet" onClick={addTag}>Add</Button>}
          </span>
        )}
        <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          {linkedTo ? (
            <>
              <Badge tone="ok" title={item.loaded_recipe_id ?? item.loaded_stock_item_id ?? undefined}>Linked to {linkedTo}</Badge>
              {canWrite && (
                <>
                  <Button size="sm" variant="quiet" icon={Link2} onClick={onLink}>Change</Button>
                  <IconButton icon={Unlink} iconSize={16} label="Unlink from LoadedHub" onClick={() => onChange({ loaded_recipe_id: null, loaded_stock_item_id: null })} />
                </>
              )}
            </>
          ) : (
            <>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-xs)', color: 'var(--warn)' }}>
                <Icon icon={CircleAlert} size={12} /> Not linked
              </span>
              {canWrite && <Button size="sm" variant="quiet" icon={Link2} onClick={onLink}>Link recipe</Button>}
            </>
          )}
        </span>
      </div>
    </div>
  );
}

interface RecipeResult {
  q: string;
  available: boolean;
  recipes: { id: string; name: string }[];
  reason: string | null;
}

/** Search the venue's LoadedHub recipes and link one to a dish. */
function RecipeLinkDialog({ venueId, initialQuery, onPick, onClose }: {
  venueId: string;
  initialQuery: string;
  onPick: (r: { id: string; name: string }) => void;
  onClose: () => void;
}) {
  const id = useId();
  // Start from the dish's first words: "ENTREE - KING SALMON, ESCABECHE…" → "KING SALMON".
  const [q, setQ] = useState(() => initialQuery.replace(/^[A-Z &]+\s+-\s+/i, '').split(/[,(]/)[0].split(/\s+/).slice(0, 3).join(' ').trim());
  const [result, setResult] = useState<RecipeResult | null>(null);
  const [failed, setFailed] = useState<{ q: string; message: string } | null>(null);
  const query = q.trim();

  useEffect(() => {
    if (query.length < 2) return;
    let alive = true;
    const t = setTimeout(() => {
      fnGet<Omit<RecipeResult, 'q'>>(`/venues/${venueId}/loaded-recipes`, { q: query }).then(
        (r) => alive && setResult({ q: query, ...r }),
        (e) => alive && setFailed({ q: query, message: errorText(e) }),
      );
    }, 350);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [venueId, query]);

  const current = result?.q === query ? result : null;
  const error = failed?.q === query ? failed.message : null;
  return (
    <Dialog title="Link a LoadedHub recipe" width={560} onClose={onClose} closeButton>
      <label className="n-label" htmlFor={`${id}-q`}>Search recipes</label>
      <input id={`${id}-q`} className="n-input" style={fullWidth} value={q} autoFocus onChange={(e) => setQ(e.target.value)} placeholder="e.g. market fish" />
      <p style={help}>Linking lets the kitchen cost and prep pre-orders from LoadedHub.</p>
      <div style={{ marginTop: 10, minHeight: 120 }}>
        {query.length < 2 ? (
          <p style={muted}>Type at least two letters.</p>
        ) : error ? (
          <Notice tone="error">{error}</Notice>
        ) : !current ? (
          <PageState kind="loading" title="Searching LoadedHub…" />
        ) : !current.available ? (
          <Notice tone="warn">LoadedHub isn’t available for this venue right now{current.reason ? `: ${current.reason}` : '.'} You can save the dish unlinked and link it later.</Notice>
        ) : current.recipes.length === 0 ? (
          <p style={muted}>No recipes match “{query}”.</p>
        ) : (
          <ul aria-label="Recipes" style={{ listStyle: 'none', margin: 0, padding: 0, border: '1px solid var(--line)', borderRadius: 'var(--radius)', maxHeight: 320, overflowY: 'auto' }}>
            {current.recipes.map((r) => (
              <li key={r.id}>
                <button type="button" className="n-option" onClick={() => onPick(r)} style={{ padding: '8px 12px', fontSize: 'var(--fs-base)' }}>{r.name}</button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Dialog>
  );
}

interface LoadedMenu {
  id: string;
  name: string;
  sections: number | null;
  lines: number | null;
  created: string | null;
}

/** Make a template from one of the venue's LoadedHub menus. */
function CloneLoadedDialog({ venueId, currency, onClose, onCloned }: {
  venueId: string;
  currency: string;
  onClose: () => void;
  onCloned: (t: MenuTemplate) => void;
}) {
  const id = useId();
  const [menus, setMenus] = useState<{ available: boolean; menus: LoadedMenu[]; reason: string | null } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string>('');
  const [name, setName] = useState('');
  const [isSetMenu, setIsSetMenu] = useState(false);
  const [pp, setPp] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fnGet<{ available: boolean; menus: LoadedMenu[]; reason: string | null }>(`/venues/${venueId}/loaded-menus`).then(
      (r) => alive && setMenus(r),
      (e) => alive && setLoadError(errorText(e)),
    );
    return () => {
      alive = false;
    };
  }, [venueId]);

  const clone = async () => {
    setBusy(true);
    setError(null);
    try {
      const t = await fnSend<MenuTemplate>('POST', '/preorder-templates/clone-loaded', {
        venue_id: venueId,
        loaded_menu_id: picked,
        name: name.trim() || null,
        is_set_menu: isSetMenu,
        price_per_person_cents: isSetMenu && pp.trim() ? parseMoney(pp) : null,
      });
      onCloned(t);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  return (
    <Dialog title="Clone a LoadedHub menu" width={560} onClose={() => { if (!busy) onClose(); }} closeButton>
      {loadError ? (
        <Notice tone="error">{loadError}</Notice>
      ) : !menus ? (
        <PageState kind="loading" title="Reading this venue’s LoadedHub menus…" />
      ) : !menus.available ? (
        <Notice tone="warn">LoadedHub isn’t available for this venue{menus.reason ? `: ${menus.reason}` : '.'}</Notice>
      ) : menus.menus.length === 0 ? (
        <p style={muted}>This venue has no menus in LoadedHub.</p>
      ) : (
        <>
          <p style={{ ...help, marginTop: 0, marginBottom: 10 }}>Each Loaded section becomes a heading and each line a dish, linked to its recipe.</p>
          <div role="radiogroup" aria-label="LoadedHub menus" style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 260, overflowY: 'auto', border: '1px solid var(--line)', borderRadius: 'var(--radius)', padding: 4 }}>
            {menus.menus.map((m) => (
              <label key={m.id} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '6px 8px', borderRadius: 'var(--radius-sm)', background: picked === m.id ? 'var(--selected)' : undefined, cursor: 'pointer' }}>
                <input type="radio" name={`${id}-menu`} checked={picked === m.id} onChange={() => { setPicked(m.id); setName(m.name); }}
                  style={{ accentColor: 'var(--accent)', width: 16, height: 16, margin: 0 }} />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block', fontWeight: 500 }}>{m.name}</span>
                  <span style={muted}>
                    {[m.sections != null ? `${m.sections} sections` : '', m.lines != null ? `${m.lines} lines` : ''].filter(Boolean).join(' · ')}
                  </span>
                </span>
              </label>
            ))}
          </div>
          {picked && (
            <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Field label="Template name" htmlFor={`${id}-name`}>
                <input id={`${id}-name`} className="n-input" style={fullWidth} value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <CheckboxRow checked={isSetMenu} onChange={setIsSetMenu} label="Make it a set menu" description="Dishes lose their prices; the menu is priced per person." />
              {isSetMenu && (
                <Field label={`Price per person (${currency})`} htmlFor={`${id}-pp`} style={{ maxWidth: 220 }}>
                  <input id={`${id}-pp`} className="n-input" style={fullWidth} inputMode="decimal" value={pp} onChange={(e) => setPp(e.target.value)} placeholder="65.00" />
                </Field>
              )}
            </div>
          )}
        </>
      )}
      {error && <Notice tone="error" style={{ marginTop: 12 }}>{error}</Notice>}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20, flexWrap: 'wrap' }}>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="primary" onClick={clone} disabled={!picked || busy}>{busy ? 'Cloning…' : 'Clone menu'}</Button>
      </div>
    </Dialog>
  );
}

