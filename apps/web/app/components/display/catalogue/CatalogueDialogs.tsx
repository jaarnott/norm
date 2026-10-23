'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { Plus, TriangleAlert, Trash2 } from 'lucide-react';
import { apiFetch } from '../../../lib/api';
import Button from '../../ui/Button';
import Dialog from '../../ui/Dialog';
import IconButton from '../../ui/IconButton';
import {
  baseUnit, draftOf, draftProblem, itemUnitType, itemUpdateParams, movedText, resultSentence,
  type Catalogue, type CatalogueItem, type CatalogueSupplier, type CatalogueUnit,
  type Change, type ItemDraft, type PreviewCard,
} from './catalogueLogic';

// ---------------------------------------------------------- the change ----
//
// Every change runs through the tool twice, the way an approval does in
// chat: /preview reads and returns the tool's own card (writing nothing);
// Confirm runs it with the card's fingerprint, so it stops if the records
// moved meanwhile. A merge too big for one run says "in progress" and is run
// again until it is done (routers/stock_catalogue.py).

async function post(path: string, body: unknown) {
  const r = await apiFetch(path, { method: 'POST', body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Something went wrong (${r.status})`);
  return data;
}

const errorBox: React.CSSProperties = {
  marginTop: 12, padding: '8px 12px', fontSize: 'var(--fs-sm)',
  color: 'var(--error)', backgroundColor: 'var(--error-bg)', borderRadius: 'var(--radius)',
};
const soft: React.CSSProperties = { fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' };

function CardRows({ card }: { card: PreviewCard }) {
  const changes = card.changes ?? [];
  return (
    <div>
      {card.target && (
        <div style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)', marginBottom: 6 }}>{card.target}</div>
      )}
      {changes.length > 0 && (
        <div style={{
          display: 'grid', gridTemplateColumns: 'minmax(90px, max-content) 1fr',
          columnGap: 12, rowGap: 4, fontSize: 'var(--fs-sm)',
        }}>
          {changes.map((c, i) => (
            <div key={i} style={{ display: 'contents' }}>
              <span style={{ color: 'var(--muted)', overflowWrap: 'anywhere' }}>{c.field}</span>
              <span style={{ color: 'var(--text)', overflowWrap: 'anywhere' }}>
                {c.before !== undefined && c.before !== null && c.before !== '' && (
                  <>
                    <span style={{ color: 'var(--muted)', textDecoration: 'line-through' }}>{String(c.before)}</span>
                    <span aria-hidden style={{ color: 'var(--icon)', margin: '0 5px' }}>→</span>
                  </>
                )}
                <span style={{ fontWeight: 500 }}>{c.after === undefined || c.after === null ? '' : String(c.after)}</span>
              </span>
            </div>
          ))}
        </div>
      )}
      {(card.warnings ?? []).map((w, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--warn)', marginTop: 8 }}>
          <TriangleAlert size={14} aria-hidden style={{ flex: '0 0 auto', marginTop: 2 }} />
          <span>{w}</span>
        </div>
      ))}
    </div>
  );
}

/** Preview → Confirm → (carry on) → done. `onDone` gets the sentence to show. */
export function ChangeDialog({ change, venueId, onClose, onDone }: {
  change: Change;
  venueId: string;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [card, setCard] = useState<PreviewCard | null>(null);
  const [nothing, setNothing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [progress, setProgress] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    post('/api/stock-catalogue/preview', { venue_id: venueId, tool: change.tool, params: change.params })
      .then(d => {
        if (!live) return;
        if (d.card) setCard(d.card);
        else setNothing(resultSentence(d.done));
      })
      .catch(e => { if (live) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [change, venueId]);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      let fingerprint = card?.fingerprint ?? null;
      for (let run = 0; run < 40; run++) {
        const d = await post('/api/stock-catalogue/run', {
          venue_id: venueId, tool: change.tool, params: change.params, fingerprint,
        });
        if (d.changed) {
          // The records moved since the preview: show them as they are now.
          setCard(d.changed);
          setError('These records changed since you looked. Check the change again, then confirm.');
          const again = await post('/api/stock-catalogue/preview', { venue_id: venueId, tool: change.tool, params: change.params });
          if (again.card) setCard(again.card);
          setBusy(false);
          return;
        }
        const data = d.data ?? {};
        if (data.result === 'in progress' && data.continue) {
          // A long merge: the same run again carries on from what is left.
          fingerprint = null;
          const sofar = movedText(data.moved);
          setProgress(sofar ? `Still going — ${sofar} moved so far…` : 'Still going…');
          continue;
        }
        if (data.error) throw new Error(String(data.error));
        onDone(resultSentence(data));
        return;
      }
      throw new Error('The change is taking many runs — open the catalogue again to see where it got to.');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
      setProgress(null);
    }
  };

  const dismiss = () => { if (!busy || !card) onClose(); };
  return (
    <Dialog title={card?.title || 'Check the change'} width={560} onClose={dismiss}>
      {busy && !card && !error && <div style={soft}>Working out what this changes…</div>}
      {nothing && <div style={soft}>{nothing}</div>}
      {card && <CardRows card={card} />}
      {progress && <div style={{ ...soft, marginTop: 12 }}>{progress}</div>}
      {error && <div role="alert" style={errorBox}>{error}</div>}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: 20 }}>
        <Button onClick={dismiss} disabled={busy && !!card}>{card ? 'Cancel' : 'Close'}</Button>
        {card && (
          <Button variant={change.danger ? 'danger' : 'primary'} onClick={confirm} disabled={busy} aria-busy={busy || undefined}>
            {busy ? 'Saving…' : change.verb}
          </Button>
        )}
      </div>
    </Dialog>
  );
}

// ----------------------------------------------------------- the forms ----

const label: React.CSSProperties = { display: 'block', fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--text-soft)', marginBottom: 4 };
const field: React.CSSProperties = { marginBottom: 12 };
const full: React.CSSProperties = { width: '100%', boxSizing: 'border-box' };

function Field({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <label style={field}>
      <span style={label}>{title}</span>
      {children}
    </label>
  );
}

function FormButtons({ onCancel, onSave, saveLabel = 'Review change', problem }: {
  onCancel: () => void; onSave: () => void; saveLabel?: string; problem?: string | null;
}) {
  return (
    <>
      {problem && <div role="alert" style={errorBox}>{problem}</div>}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: 16 }}>
        <Button onClick={onCancel}>Cancel</Button>
        <Button variant="primary" onClick={onSave}>{saveLabel}</Button>
      </div>
    </>
  );
}

/** Units an item may use: its own type, live, plus whatever it uses now. */
function unitChoices(cat: Catalogue, type: string | null, keep: string[]) {
  return cat.units.filter(u => (!u.deactivated && (!type || u.type === type)) || keep.includes(u.id));
}

export function ItemEditDialog({ item, catalogue, onClose, onChange }: {
  item: CatalogueItem;
  catalogue: Catalogue;
  onClose: () => void;
  onChange: (c: Change) => void;
}) {
  const [draft, setDraft] = useState<ItemDraft>(() => draftOf(item));
  const [problem, setProblem] = useState<string | null>(null);
  const type = itemUnitType(item);
  const units = useMemo(
    () => unitChoices(catalogue, type, [item.counting_unit_id, item.ordering_unit_id, ...item.variants.map(v => v.unit_id)].filter(Boolean) as string[]),
    [catalogue, type, item],
  );
  const suppliers = catalogue.suppliers.filter(s => !s.deactivated || draft.variants.some(v => v.supplier_id === s.id));
  const set = (patch: Partial<ItemDraft>) => setDraft(d => ({ ...d, ...patch }));
  const setVariant = (i: number, patch: Partial<ItemDraft['variants'][number]>) =>
    setDraft(d => {
      const variants = d.variants.map((v, k) => (k === i ? { ...v, ...patch } : v));
      // One default per supplier: ticking one unticks that supplier's others.
      if (patch.default) {
        const sup = variants[i].supplier_id;
        variants.forEach((v, k) => { if (k !== i && v.supplier_id === sup) v.default = false; });
      }
      return { ...d, variants };
    });

  const save = () => {
    const bad = draftProblem(draft);
    if (bad) { setProblem(bad); return; }
    const params = itemUpdateParams(item, draft, catalogue.units);
    if (!params) { setProblem('Nothing has changed.'); return; }
    onChange({ tool: 'manage_stock_item', params, verb: 'Save' });
  };

  const unitSelect = (value: string, onPick: (id: string) => void, aria: string) => (
    <select className="n-select" aria-label={aria} value={value} onChange={e => onPick(e.target.value)} style={full}>
      <option value="">Choose a unit</option>
      {units.map(u => <option key={u.id} value={u.id}>{u.name}{u.deactivated ? ' (deleted)' : ''}</option>)}
    </select>
  );

  return (
    <Dialog title={`Edit ${item.name}`} width={720} onClose={onClose} closeButton>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', columnGap: 12 }}>
        <Field title="Name">
          <input className="n-input" value={draft.name} onChange={e => set({ name: e.target.value })} style={full} />
        </Field>
        <Field title="Group">
          <select className="n-select" value={draft.group_id} onChange={e => set({ group_id: e.target.value })} style={full}>
            {catalogue.groups.map(g => <option key={g.id} value={g.id}>{g.category ? `${g.category} · ` : ''}{g.name}</option>)}
          </select>
        </Field>
        <Field title="Counted in">{unitSelect(draft.counting_unit_id, id => set({ counting_unit_id: id }), 'Counting unit')}</Field>
        <Field title="Ordered in">{unitSelect(draft.ordering_unit_id, id => set({ ordering_unit_id: id }), 'Ordering unit')}</Field>
        <Field title="Default supplier">
          <select className="n-select" value={draft.default_supplier_id} onChange={e => set({ default_supplier_id: e.target.value })} style={full}>
            <option value="">None</option>
            {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>
        <Field title="Sales tax">
          <select className="n-select" value={String(draft.sales_tax)} onChange={e => set({ sales_tax: Number(e.target.value) })} style={full}>
            <option value="1">GST</option>
            <option value="0">Exempt</option>
          </select>
        </Field>
      </div>

      <div style={{ ...label, marginTop: 4 }}>Supplier variants</div>
      <div style={{ overflowX: 'auto' }}>
        <table className="n-table" style={{ minWidth: 600 }}>
          <thead>
            <tr><th>Supplier</th><th>Code</th><th>Unit</th><th className="num">Cost</th><th>Default</th><th aria-label="Remove" /></tr>
          </thead>
          <tbody>
            {draft.variants.map((v, i) => (v.removed ? null : (
              <tr key={v.variant_id ?? `new-${i}`}>
                <td>
                  <select className="n-select" aria-label="Supplier" value={v.supplier_id} onChange={e => setVariant(i, { supplier_id: e.target.value })} style={full}>
                    <option value="">Choose…</option>
                    {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </td>
                <td><input className="n-input" aria-label="Stock code" value={v.stock_code} onChange={e => setVariant(i, { stock_code: e.target.value })} style={{ width: 110 }} /></td>
                <td style={{ minWidth: 130 }}>{unitSelect(v.unit_id, id => setVariant(i, { unit_id: id }), 'Variant unit')}</td>
                <td className="num"><input className="n-input" aria-label="Unit cost" inputMode="decimal" value={v.unit_cost} onChange={e => setVariant(i, { unit_cost: e.target.value })} style={{ width: 90, textAlign: 'right' }} /></td>
                <td><input type="checkbox" aria-label="Default for this supplier" checked={v.default} onChange={e => setVariant(i, { default: e.target.checked })} /></td>
                <td><IconButton icon={Trash2} label="Remove this variant" onClick={() => setVariant(i, { removed: true })} /></td>
              </tr>
            )))}
          </tbody>
        </table>
      </div>
      <div style={{ marginTop: 8 }}>
        <Button size="sm" variant="quiet" icon={Plus} onClick={() => set({
          variants: [...draft.variants, { supplier_id: '', stock_code: '', unit_id: draft.ordering_unit_id, unit_cost: '', default: !draft.variants.some(v => !v.removed) }],
        })}>Add a supplier variant</Button>
      </div>
      <FormButtons onCancel={onClose} onSave={save} problem={problem} />
    </Dialog>
  );
}

export function UnitEditDialog({ unit, onClose, onChange }: {
  unit: CatalogueUnit; onClose: () => void; onChange: (c: Change) => void;
}) {
  const [name, setName] = useState(unit.name);
  const [ratio, setRatio] = useState(String(unit.ratio));
  const [problem, setProblem] = useState<string | null>(null);
  const save = () => {
    const n = Number(ratio);
    if (!name.trim()) { setProblem('A unit needs a name.'); return; }
    if (!isFinite(n) || n <= 0) { setProblem('The size must be a number above 0.'); return; }
    const params: Record<string, unknown> = { op: 'update_unit', unit_id: unit.id };
    if (name.trim() !== unit.name) params.name = name.trim();
    if (n !== unit.ratio) params.ratio = n;
    if (Object.keys(params).length === 2) { setProblem('Nothing has changed.'); return; }
    onChange({ tool: 'manage_stock_item', params, verb: 'Save' });
  };
  return (
    <Dialog title={`Edit ${unit.name}`} width={420} onClose={onClose} closeButton>
      <Field title="Name"><input className="n-input" value={name} onChange={e => setName(e.target.value)} style={full} /></Field>
      <Field title={`Size, in ${baseUnit(unit.type)}`}>
        <input className="n-input" inputMode="decimal" value={ratio} onChange={e => setRatio(e.target.value)} style={full} />
      </Field>
      {unit.items > 0 && (
        <div style={soft}>{unit.items} item{unit.items === 1 ? ' uses' : 's use'} this unit. Changing its size changes what everything already recorded in it means.</div>
      )}
      <FormButtons onCancel={onClose} onSave={save} problem={problem} />
    </Dialog>
  );
}

export function SupplierEditDialog({ supplier, onClose, onChange }: {
  supplier: CatalogueSupplier; onClose: () => void; onChange: (c: Change) => void;
}) {
  const start = {
    name: supplier.name ?? '', contact: supplier.contact ?? '', email: supplier.email ?? '',
    phone: supplier.phone ?? '', customer_number: supplier.customer_number ?? '',
  };
  const [form, setForm] = useState(start);
  const [problem, setProblem] = useState<string | null>(null);
  const save = () => {
    if (!form.name.trim()) { setProblem('A supplier needs a name.'); return; }
    const changes: Record<string, string> = {};
    (Object.keys(form) as (keyof typeof form)[]).forEach(k => {
      if (form[k].trim() !== start[k]) changes[k] = form[k].trim();
    });
    if (!Object.keys(changes).length) { setProblem('Nothing has changed.'); return; }
    onChange({ tool: 'manage_supplier', params: { op: 'update', supplier: supplier.id, changes }, verb: 'Save' });
  };
  const input = (k: keyof typeof form, title: string) => (
    <Field title={title}>
      <input className="n-input" value={form[k]} onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))} style={full} />
    </Field>
  );
  return (
    <Dialog title={`Edit ${supplier.name}`} width={480} onClose={onClose} closeButton>
      {input('name', 'Name')}
      {input('contact', 'Contact')}
      {input('email', 'Email')}
      {input('phone', 'Phone')}
      {input('customer_number', 'Customer number')}
      <FormButtons onCancel={onClose} onSave={save} problem={problem} />
    </Dialog>
  );
}

/** Pick the record to merge into, from a searchable list. */
export function MergeDialog<T extends { id: string; name: string }>({ title, from, targets, describe, explain, onClose, onPick }: {
  title: string;
  from: T;
  targets: T[];
  describe: (t: T) => string;
  explain: string;
  onClose: () => void;
  onPick: (target: T) => void;
}) {
  const [q, setQ] = useState('');
  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    return targets.filter(t => words.every(w => `${t.name} ${describe(t)}`.toLowerCase().includes(w))).slice(0, 60);
  }, [q, targets, describe]);
  return (
    <Dialog title={title} width={520} onClose={onClose} closeButton>
      <div style={{ ...soft, marginBottom: 12 }}>{explain}</div>
      {targets.length === 0 ? (
        <div style={soft}>There is nothing {from.name} can be merged into.</div>
      ) : (
        <>
          <input className="n-input" autoFocus placeholder="Search…" aria-label="Search" value={q} onChange={e => setQ(e.target.value)} style={{ ...full, marginBottom: 8 }} />
          <div style={{ maxHeight: 320, overflowY: 'auto', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
            {shown.map(t => (
              <button key={t.id} type="button" className="n-row" onClick={() => onPick(t)}
                style={{ borderBottom: '1px solid var(--line)', padding: '8px 12px', textAlign: 'left', background: 'transparent', border: 'none', borderRadius: 0, cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}>
                <span style={{ fontWeight: 500, color: 'var(--text)' }}>{t.name}</span>
                <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{describe(t)}</span>
              </button>
            ))}
            {shown.length === 0 && <div style={{ ...soft, padding: 12 }}>Nothing matches.</div>}
          </div>
        </>
      )}
    </Dialog>
  );
}
