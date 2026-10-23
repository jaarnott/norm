'use client';

import { useCallback, useEffect, useState } from 'react';
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
}

const sectionTitle: CSSProperties = { margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' };
const subTitle: CSSProperties = { margin: '24px 0 8px', fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' };
const intro: CSSProperties = { margin: '4px 0 16px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
const muted: CSSProperties = { fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
const row: CSSProperties = { display: 'flex', alignItems: 'flex-start', gap: 12, padding: '10px 0', borderTop: '1px solid var(--line)' };
const alertBox: CSSProperties = {
  marginTop: 12, padding: '8px 12px', fontSize: 'var(--fs-sm)',
  color: 'var(--error)', backgroundColor: 'var(--error-bg)', borderRadius: 'var(--radius)',
};

async function reason(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  return typeof body?.detail === 'string' ? body.detail : `${fallback} (error ${res.status})`;
}

/** The form's settings as the API takes them. */
function paramsOf(e: Editing): Record<string, unknown> {
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
  };
}

/**
 * Settings → Receiving rules: how Norm fills in what an invoice leaves to the
 * organisation (the due date, first) every time it reviews one — on the card
 * and under Autopilot alike. The organisation's rules, and each venue's own
 * overrides beside what is in force there. Everyone in the organisation can
 * read them; owners and managers (`can_edit`) change them.
 */
export default function ReceivingRulesPanel() {
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

  if (loadError) return <PageState kind="error" title={loadError} />;
  if (!view) return <PageState kind="loading" title="Loading receiving rules…" />;

  const kind = view.kinds[0];
  const editable = view.can_edit;

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
    <div className="n-card" style={{ padding: 16, marginTop: 8 }} data-testid="receiving-rule-editor">
      <div style={{ fontWeight: 600, marginBottom: 8 }}>{kind?.label} — {editing.where}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'flex-end' }}>
        <label style={{ minWidth: 0, flex: '1 1 100%' }}>
          <span className="n-label">Which invoices</span>
          <select className="n-select" value={editing.when} style={{ width: '100%' }}
            onChange={(e) => setEditing({ ...editing, when: e.target.value })}>
            {kind?.whens.map((w) => <option key={w.id} value={w.id}>{w.label}</option>)}
          </select>
        </label>
        <label style={{ minWidth: 0, flex: '1 1 260px' }}>
          <span className="n-label">The due date is</span>
          <select className="n-select" value={editing.basis} style={{ width: '100%' }}
            onChange={(e) => setEditing({ ...editing, basis: e.target.value })}>
            {kind?.bases.map((b) => (
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
      {preview && (
        <div style={{ ...muted, marginTop: 12 }}>
          <div>The due date will be {preview.description}. For invoices dated across a week:</div>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {preview.examples.map((x) => <li key={x.words}>{x.words}</li>)}
          </ul>
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <Button variant="primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save rule'}</Button>
        <Button onClick={() => { setEditing(null); setError(null); }} disabled={saving}>Cancel</Button>
      </div>
    </div>
  );

  const orgRule = view.organisation.find((r) => r.kind === kind?.kind);

  return (
    <div style={{ lineHeight: 1.45 }}>
      <h2 style={sectionTitle}>Receiving rules</h2>
      <p style={intro}>
        How Norm fills in what an invoice leaves to you, every time it reviews one — on the invoice card and under
        Autopilot. A venue’s own rule overrides the organisation’s, and a rule wins over what the invoice says.
        {editable ? '' : ' Only owners and managers can change them.'}
      </p>
      {error && <div style={alertBox} role="alert">{error}</div>}

      <h3 style={subTitle}>Organisation</h3>
      <div className="n-card" style={{ padding: '4px 16px' }} data-testid="receiving-rules-organisation">
        {kind && (
          <div style={{ ...row, borderTop: 'none' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600 }}>{kind.label}</div>
              <div style={muted}>{orgRule ? `The due date is ${orgRule.description}.` : 'No rule — the due date is what the invoice and Loaded give it.'}</div>
              {orgRule?.note && <div style={{ ...muted, fontStyle: 'italic' }}>“{orgRule.note}”</div>}
            </div>
            {editable && (
              <div style={{ display: 'flex', gap: 8 }}>
                <Button size="sm" onClick={() => setEditing(editingFrom(kind.kind, null, 'the whole organisation', orgRule))}>
                  {orgRule ? 'Edit' : 'Add rule'}
                </Button>
                {orgRule && <Button size="sm" variant="quiet" onClick={() => remove(orgRule)}>Remove</Button>}
              </div>
            )}
          </div>
        )}
      </div>
      {editing && editing.venueId === null && editor}

      <h3 style={subTitle}>Venues</h3>
      {view.venues.length === 0 && <p style={muted}>This organisation has no venues yet.</p>}
      <div className="n-card" style={{ padding: '4px 16px' }} data-testid="receiving-rules-venues">
        {view.venues.map((v, i) => {
          const own = v.rules.find((r) => r.kind === kind?.kind);
          const inForce = v.in_force.find((r) => r.kind === kind?.kind);
          return (
            <div key={v.id}>
              <div style={{ ...row, ...(i === 0 ? { borderTop: 'none' } : {}) }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600 }}>{v.name}</div>
                  <div style={muted}>
                    {inForce ? `Due date: ${inForce.description}.` : 'Due date: no rule.'}{' '}
                    {own ? <Badge tone="info">This venue’s own rule</Badge> : inForce ? <Badge>From the organisation</Badge> : null}
                  </div>
                </div>
                {editable && kind && (
                  <div style={{ display: 'flex', gap: 8 }}>
                    <Button size="sm" onClick={() => setEditing(editingFrom(kind.kind, v.id, v.name, own ?? inForce))}>
                      {own ? 'Edit' : 'Set for this venue'}
                    </Button>
                    {own && (
                      <Button size="sm" variant="quiet" onClick={() => remove(own)}>
                        {orgRule ? 'Use the organisation’s' : 'Remove'}
                      </Button>
                    )}
                  </div>
                )}
              </div>
              {editing && editing.venueId === v.id && editor}
            </div>
          );
        })}
      </div>
    </div>
  );
}
