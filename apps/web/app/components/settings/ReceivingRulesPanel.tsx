'use client';

import { Fragment, useCallback, useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { apiFetch } from '../../lib/api';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import PageState from '../ui/PageState';

/** One rule as GET /api/receiving-rules returns it. */
interface Rule {
  id: string;
  kind: string;
  label: string;
  params: Record<string, unknown>;
  description: string;
  scope: 'organisation' | 'venue';
  venue_id: string | null;
  note: string | null;
}

interface Kind {
  kind: string;
  label: string;
  what: string;
  bases: { id: string; words: string; label: string }[];
  /** which invoices a rule covers: every one, or only those printing no due date */
  whens: { id: string; words: string; label: string }[];
}

interface VenueRules {
  id: string;
  name: string;
  /** the venue's own rules (overrides) */
  rules: Rule[];
  /** what applies there: its own, else the organisation's */
  in_force: Rule[];
}

interface RulesView {
  kinds: Kind[];
  weekdays: string[];
  organisation: Rule[];
  venues: VenueRules[];
  /** owner or manager (receiving:rules), or a platform admin */
  can_edit: boolean;
}

/** The rule being written: for the organisation (venueId null) or a venue. */
interface Editing {
  kind: string;
  venueId: string | null;
  where: string;
  basis: string;
  when: string;
  weekday: number;
  days: number;
  day: number;
  /** bunch_unit: the venue's unit a bunch is booked in */
  unit: string;
}

/** A white .n-card block, like the Preferences cards around it. */
const card: CSSProperties = { padding: '14px 16px', marginBottom: 12 };
const cardTitle: CSSProperties = { margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600, lineHeight: 1.35, color: 'var(--text)' };
const intro: CSSProperties = { margin: '2px 0 4px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
const muted: CSSProperties = { fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
const groupTitle: CSSProperties = { fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)' };
/** One place the rules apply: the organisation, or one venue. */
const scopeBlock: CSSProperties = { padding: '10px 0', borderTop: '1px solid var(--line-soft)' };
/** One rule at that place: what it does on the left, its buttons on the right. */
const ruleRow: CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '4px 16px',
  flexWrap: 'wrap', padding: '4px 0',
};
const rowText: CSSProperties = { flex: '1 1 220px', minWidth: 0 };
const alertBox: CSSProperties = {
  marginTop: 12, padding: '8px 12px', fontSize: 'var(--fs-sm)',
  color: 'var(--error)', backgroundColor: 'var(--error-bg)', borderRadius: 'var(--radius)',
};

async function reason(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  return typeof body?.detail === 'string' ? body.detail : `${fallback} (error ${res.status})`;
}

/** What a place does with no rule of this type. */
const NO_RULE: Record<string, string> = {
  due_date: 'no rule — the due date is what the invoice and Loaded give it',
  bunch_unit: 'no rule — Norm books bunches by weight',
};

/** The form's settings as the API takes them. */
function paramsOf(e: Editing): Record<string, unknown> {
  if (e.kind === 'bunch_unit') return { unit: e.unit };
  const base = { basis: e.basis, when: e.when };
  if (e.basis === 'weekday_on_or_after' || e.basis === 'weekday_after') return { ...base, weekday: e.weekday };
  if (e.basis === 'days_after') return { ...base, days: e.days };
  if (e.basis === 'day_of_next_month') return { ...base, day: e.day };
  return base;
}

function editingFrom(kind: string, venueId: string | null, where: string, rule?: Rule): Editing {
  const p = rule?.params ?? {};
  return {
    kind,
    venueId,
    where,
    basis: typeof p.basis === 'string' ? p.basis : 'weekday_on_or_after',
    when: typeof p.when === 'string' ? p.when : 'no_due_date',
    weekday: typeof p.weekday === 'number' ? p.weekday : 6,
    days: typeof p.days === 'number' ? p.days : 14,
    day: typeof p.day === 'number' ? p.day : 20,
    unit: typeof p.unit === 'string' ? p.unit : 'Bunch',
  };
}

/**
 * Settings → Preferences, under "Receive supplier invoices": how Norm fills in
 * what an invoice leaves to the organisation (the due date; the unit for
 * anything sold by the bunch) every time it reviews one — on the card and
 * under Autopilot alike. Every rule type is shown the same way, once for the
 * organisation and once per venue: its own rule, else what is in force there.
 * Everyone in the organisation can read them; owners and managers
 * (`can_edit`) change them. Unlike the preferences around it, they are shared.
 */
export default function ReceivingRulesPanel({ orgName }: { orgName?: string | null }) {
  const [view, setView] = useState<RulesView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [preview, setPreview] = useState<{ description: string; examples: { words: string }[] } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const res = await apiFetch('/api/receiving-rules');
      if (!res.ok) {
        setLoadError(await reason(res, 'Couldn’t load the receiving rules'));
        return;
      }
      setView(await res.json());
    } catch {
      setLoadError('Couldn’t load the receiving rules — check your connection and try again.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // What the rule being written does, shown as it is chosen.
  useEffect(() => {
    if (!editing) { setPreview(null); return; }
    const q = new URLSearchParams({ kind: editing.kind });
    for (const [k, v] of Object.entries(paramsOf(editing))) q.set(k, String(v));
    let live = true;
    apiFetch(`/api/receiving-rules/examples?${q}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => { if (live) setPreview(body); })
      .catch(() => { if (live) setPreview(null); });
    return () => { live = false; };
  }, [editing]);

  const title = <h3 style={cardTitle}>Receiving rules</h3>;
  if (loadError || !view) {
    return (
      <div className="n-card" style={card} data-testid="receiving-rules">
        {title}
        {loadError ? <PageState kind="error" title={loadError} /> : <PageState kind="loading" title="Loading receiving rules…" />}
      </div>
    );
  }

  const editable = view.can_edit;
  const editingKind = editing ? view.kinds.find((k) => k.kind === editing.kind) : undefined;
  const orgHas = (k: string) => view.organisation.some((r) => r.kind === k);

  const save = async () => {
    if (!editing) return;
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch('/api/receiving-rules', {
        method: 'PUT',
        body: JSON.stringify({ kind: editing.kind, venue_id: editing.venueId, params: paramsOf(editing) }),
      });
      if (!res.ok) { setError(await reason(res, 'Couldn’t save the rule')); return; }
      setView(await res.json());
      setEditing(null);
    } catch {
      setError('Couldn’t save — check your connection and try again.');
    } finally {
      setSaving(false);
    }
  };

  const remove = async (rule: Rule) => {
    setError(null);
    const q = new URLSearchParams({ kind: rule.kind });
    if (rule.venue_id) q.set('venue_id', rule.venue_id);
    try {
      const res = await apiFetch(`/api/receiving-rules?${q}`, { method: 'DELETE' });
      if (!res.ok) { setError(await reason(res, 'Couldn’t remove the rule')); return; }
      setView(await res.json());
    } catch {
      setError('Couldn’t remove — check your connection and try again.');
    }
  };

  const editor = editing && (
    <div style={{ margin: '0 0 10px', padding: 12, borderRadius: 'var(--radius)', border: '1px solid var(--line)', background: 'var(--bg)' }}
      data-testid="receiving-rule-editor">
      <div style={{ fontWeight: 600, marginBottom: 8 }}>{editingKind?.label} — {editing.where}</div>
      {editing.kind === 'bunch_unit' ? (
        <label style={{ display: 'block', maxWidth: 320 }}>
          <span className="n-label">Book anything sold by the bunch as one</span>
          <input className="n-input" value={editing.unit} style={{ width: '100%' }}
            onChange={(e) => setEditing({ ...editing, unit: e.target.value })} />
          <span style={{ ...muted, display: 'block', marginTop: 4 }}>
            The unit as Loaded spells it at the venue, for example Bunch. A market bunch (M/B) keeps Norm’s weight.
          </span>
        </label>
      ) : (
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'flex-end' }}>
        <label style={{ minWidth: 0, flex: '1 1 100%' }}>
          <span className="n-label">Which invoices</span>
          <select className="n-select" value={editing.when} style={{ width: '100%' }}
            onChange={(e) => setEditing({ ...editing, when: e.target.value })}>
            {editingKind?.whens.map((w) => <option key={w.id} value={w.id}>{w.label}</option>)}
          </select>
        </label>
        <label style={{ minWidth: 0, flex: '1 1 260px' }}>
          <span className="n-label">The due date is</span>
          <select className="n-select" value={editing.basis} style={{ width: '100%' }}
            onChange={(e) => setEditing({ ...editing, basis: e.target.value })}>
            {editingKind?.bases.map((b) => (
              <option key={b.id} value={b.id}>{b.label}</option>
            ))}
          </select>
        </label>
        {(editing.basis === 'weekday_on_or_after' || editing.basis === 'weekday_after') && (
          <label style={{ minWidth: 0, flex: '0 1 160px' }}>
            <span className="n-label">Day</span>
            <select className="n-select" value={editing.weekday} style={{ width: '100%' }}
              onChange={(e) => setEditing({ ...editing, weekday: Number(e.target.value) })}>
              {view.weekdays.map((w, i) => <option key={w} value={i}>{w}</option>)}
            </select>
          </label>
        )}
        {editing.basis === 'days_after' && (
          <label style={{ minWidth: 0, flex: '0 1 120px' }}>
            <span className="n-label">Days</span>
            <input className="n-input" type="number" min={0} max={120} value={editing.days} style={{ width: '100%' }}
              onChange={(e) => setEditing({ ...editing, days: Number(e.target.value) })} />
          </label>
        )}
        {editing.basis === 'day_of_next_month' && (
          <label style={{ minWidth: 0, flex: '0 1 120px' }}>
            <span className="n-label">Day of month</span>
            <input className="n-input" type="number" min={1} max={31} value={editing.day} style={{ width: '100%' }}
              onChange={(e) => setEditing({ ...editing, day: Number(e.target.value) })} />
          </label>
        )}
      </div>
      )}
      {preview && (
        <div style={{ ...muted, marginTop: 12 }}>
          {editing.kind === 'due_date' ? (
            <div>The due date will be {preview.description}. For invoices dated across a week:</div>
          ) : (
            <div>From the next invoice Norm reviews: {preview.description}.</div>
          )}
          {preview.examples.length > 0 && (
            <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
              {preview.examples.map((x) => <li key={x.words}>{x.words}</li>)}
            </ul>
          )}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <Button variant="primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save rule'}</Button>
        <Button onClick={() => { setEditing(null); setError(null); }} disabled={saving}>Cancel</Button>
      </div>
    </div>
  );

  /** One rule type at one place: the place's own rule, else what it gets from
   *  the organisation, with the same buttons for every type. */
  const ruleLine = (k: Kind, venueId: string | null, where: string, own?: Rule, inherited?: Rule) => {
    const shown = own ?? inherited;
    return (
      <Fragment key={k.kind}>
        <div style={ruleRow} data-testid={`receiving-rule-${k.kind}`}>
          <div style={rowText}>
            <div style={muted}>
              {k.label}: {shown ? `${shown.description}.` : `${NO_RULE[k.kind] ?? 'no rule'}.`}
              {venueId && (own || inherited) && ' '}
              {venueId && (own ? <Badge tone="info">This venue’s own rule</Badge> : inherited ? <Badge>From the organisation</Badge> : null)}
            </div>
            {own?.note && <div style={{ ...muted, fontStyle: 'italic' }}>“{own.note}”</div>}
          </div>
          {editable && (
            <div style={{ display: 'flex', gap: 8 }}>
              <Button size="sm" onClick={() => setEditing(editingFrom(k.kind, venueId, where, shown))}>
                {own ? 'Edit' : venueId ? 'Set for this venue' : 'Add rule'}
              </Button>
              {own && (
                <Button size="sm" variant="quiet" onClick={() => remove(own)}>
                  {venueId && orgHas(k.kind) ? 'Use the organisation’s' : 'Remove'}
                </Button>
              )}
            </div>
          )}
        </div>
        {editing && editing.venueId === venueId && editing.kind === k.kind && editor}
      </Fragment>
    );
  };

  return (
    <div className="n-card" style={{ ...card, lineHeight: 1.45 }} data-testid="receiving-rules">
      {title}
      <p style={intro}>
        How Norm fills in the due date — and, at a venue that counts by the bunch, the unit for bunches — whenever it
        receives an invoice, on the invoice card and on Autopilot. Unlike
        the choices above, these rules belong to {orgName ? <strong>{orgName}</strong> : 'your organisation'} and are
        the same for everyone. Where a rule applies it wins over what the invoice says.
        {editable ? '' : ' Only owners and managers can change them.'}
      </p>
      {error && <div style={alertBox} role="alert">{error}</div>}

      <div data-testid="receiving-rules-organisation" style={scopeBlock}>
        <div style={{ fontWeight: 500 }}>The whole organisation</div>
        {view.kinds.map((k) =>
          ruleLine(k, null, 'the whole organisation', view.organisation.find((r) => r.kind === k.kind)),
        )}
      </div>

      {view.venues.length > 0 && (
        <div style={{ marginTop: 12, paddingTop: 14, borderTop: '1px solid var(--line)' }} data-testid="receiving-rules-venues">
          <div style={groupTitle}>Venues</div>
          <p style={intro}>A venue&rsquo;s own rule replaces the organisation&rsquo;s there.</p>
          {view.venues.map((v) => (
            <div key={v.id} style={scopeBlock}>
              <div style={{ fontWeight: 500 }}>{v.name}</div>
              {view.kinds.map((k) => {
                const own = v.rules.find((r) => r.kind === k.kind);
                return ruleLine(k, v.id, v.name, own, own ? undefined : v.in_force.find((r) => r.kind === k.kind));
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
