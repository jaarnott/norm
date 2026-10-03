'use client';

/**
 * What Norm may do without asking you — Settings → Preferences.
 *
 * One list of the writes Norm can make for you (apps/api/app/routers/
 * approval_preferences.py). Ordinary writes are "Ask me" or "Always allow";
 * receiving and reconciling invoices have levels, and receiving has switches
 * at Autopilot. Yours alone — the same choices the approval card's "always
 * allow" box and Norm itself (set_approval_preference) change.
 *
 * Replaced two panels with two homes for one question: per-user "workflow
 * modes", and a per-venue receiving ladder.
 */

import { useEffect, useState } from 'react';
import { apiFetch, getStoredUser } from '../../lib/api';

interface Level { id: string; label: string; description?: string }
interface Switch { id: string; label: string }
export interface ToolPref {
  key: string;
  label: string;
  summary: string;
  kind: 'ask' | 'locked' | 'levels';
  value?: 'ask' | 'always';
  level?: string;
  levels?: Level[];
  switches?: Switch[];
  options?: Record<string, boolean>;
}

const RECEIVE = 'loadedhub.review_and_receive_invoices';

/** Why a receiving switch matters, beside the switch. */
const SWITCH_NOTE: Record<string, string> = {
  auto_create_suppliers:
    'The riskiest one. Supplier identity is what picks the extraction prompt, so a duplicate supplier sends every future invoice from that business to the wrong rules. Norm refuses to create one if anything existing plausibly matches.',
  receive_without_unit:
    'Norm picks the closest unit Loaded already has. It never invents one — that is the switch above.',
  receive_with_unconfirmed_unit:
    'The line has a unit, it just came from Loaded rather than the invoice copy. Off means someone confirms it first; the blocker names which unit it would use.',
  receive_unreconciled_totals:
    "The copy's own arithmetic doesn't add up. Norm diagnoses which figure was misread and suggests the correction; this switch lets it receive on the line values when no correction is confident.",
  auto_delete_duplicates:
    'A destructive write: Norm deletes the draft when the same invoice number from the same supplier was already received.',
  auto_delete_non_invoices:
    'A destructive write: statements and letters Loaded ingested as invoice drafts are deleted rather than received.',
  auto_delete_unreadable:
    'The riskiest delete: a draft with no readable copy could be a real delivery behind a bad scan. Off means a person decides each one.',
};

const box = { border: '1px solid #eee', borderRadius: 10, padding: '0.9rem 1rem', marginBottom: '0.85rem' };

export default function ApprovalPreferences() {
  const [tools, setTools] = useState<ToolPref[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [readiness, setReadiness] = useState<{ rate: number; attempts: number } | null>(null);
  const isAdmin = getStoredUser()?.role === 'admin';

  useEffect(() => {
    apiFetch('/api/approval-preferences')
      .then(async r => {
        if (!r.ok) throw new Error(`Could not load your approval settings (${r.status})`);
        setTools((await r.json()).tools || []);
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load your approval settings'))
      .finally(() => setLoaded(true));
    // "Would autopilot have been right?" — the evidence for moving up a level,
    // next to the control that moves it. Admin-only, so a 403 is normal.
    if (isAdmin) {
      apiFetch('/api/supplier-invoice-specs/autopilot-confidence?days=30')
        .then(r => (r.ok ? r.json() : null))
        .then(d => setReadiness(d?.totals ? { rate: d.rates?.autopilot_ready ?? 0, attempts: d.totals.attempts ?? 0 } : null))
        .catch(() => setReadiness(null));
    }
  }, [isAdmin]);

  const save = async (key: string, value: unknown) => {
    setSaving(key);
    setError(null);
    try {
      const r = await apiFetch('/api/approval-preferences', { method: 'PUT', body: JSON.stringify({ key, value }) });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Could not save (${r.status})`);
      setTools(prev => prev.map(t => (t.key === key ? data : t)));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setSaving(null);
    }
  };

  if (!loaded) return <div style={{ color: '#999', fontSize: '0.85rem' }}>Loading…</div>;

  const levelled = tools.filter(t => t.kind === 'levels');
  const others = tools.filter(t => t.kind !== 'levels');

  return (
    <div style={{ maxWidth: 720 }}>
      <h3 style={{ margin: '0 0 0.35rem', fontSize: '0.95rem', fontWeight: 600 }}>What Norm may do without asking</h3>
      <p style={{ margin: '0 0 1.25rem', fontSize: '0.8rem', color: '#777' }}>
        Norm asks before it changes anything, with a card that shows exactly what will change. Choose
        where it needn&apos;t ask. These settings are yours alone — you can also change them from an
        approval card, or by asking Norm.
      </p>
      {error && <div style={{ fontSize: '0.78rem', color: '#c0392b', marginBottom: '0.8rem' }}>{error}</div>}

      {levelled.map(t => {
        const options = t.options || {};
        const top = t.levels?.[t.levels.length - 1]?.id;
        return (
          <div key={t.key} style={box}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '0.5rem' }}>
              <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>{t.label}</span>
              {saving === t.key && <span style={{ fontSize: '0.65rem', color: '#999' }}>saving…</span>}
            </div>
            {t.key === RECEIVE && readiness && readiness.attempts > 0 && (
              <div style={{ fontSize: '0.74rem', color: '#555', background: '#f7f5f1', border: '1px solid #e8e3da', borderRadius: 6, padding: '6px 10px', margin: '0.5rem 0' }}>
                Over the last 30 days, autopilot would have been right on{' '}
                <strong>{Math.round(readiness.rate * 100)}%</strong> of the {readiness.attempts} invoices a person received.
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', marginTop: '0.6rem' }}>
              {(t.levels || []).map(lv => {
                const on = t.level === lv.id;
                return (
                  <label key={lv.id} style={{
                    display: 'flex', gap: '0.6rem', alignItems: 'flex-start', cursor: 'pointer',
                    border: `1px solid ${on ? '#a08060' : '#eee'}`, borderRadius: 8, padding: '0.5rem 0.65rem',
                    background: on ? '#faf8f5' : '#fff',
                  }}>
                    <input type="radio" name={t.key} checked={on} disabled={saving === t.key}
                      onChange={() => void save(t.key, { level: lv.id, options })} style={{ marginTop: 2 }} />
                    <span>
                      <span style={{ fontSize: '0.8rem', fontWeight: 500 }}>{lv.label}</span>
                      {lv.description && <span style={{ display: 'block', fontSize: '0.72rem', color: '#888', marginTop: 1 }}>{lv.description}</span>}
                    </span>
                  </label>
                );
              })}
            </div>
            {t.level === top && (t.switches || []).length > 0 && (
              <div style={{ marginTop: 12, paddingLeft: 10, borderLeft: '2px solid #e8e3da' }}>
                <div style={{ fontSize: '0.74rem', color: '#777', marginBottom: 8 }}>
                  What Norm may also do on its own. Everything here writes to Loaded and can&apos;t be undone
                  from Norm — leave a switch off and invoices needing it wait for you instead, saying so.
                </div>
                {(t.switches || []).map(sw => (
                  <label key={sw.id} style={{ display: 'block', marginBottom: 8, cursor: 'pointer' }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                      <input type="checkbox" checked={!!options[sw.id]} disabled={saving === t.key}
                        onChange={e => void save(t.key, { level: t.level, options: { ...options, [sw.id]: e.target.checked } })} />
                      <span style={{ fontSize: '0.78rem' }}>{sw.label[0].toUpperCase() + sw.label.slice(1)}</span>
                    </div>
                    {SWITCH_NOTE[sw.id] && <div style={{ fontSize: '0.7rem', color: '#9ca3af', marginLeft: 22 }}>{SWITCH_NOTE[sw.id]}</div>}
                  </label>
                ))}
              </div>
            )}
          </div>
        );
      })}

      {others.length > 0 && (
        <div style={box}>
          <div style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.5rem' }}>Everything else Norm can change</div>
          {others.map(t => (
            <div key={t.key} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.75rem',
              flexWrap: 'wrap', padding: '0.5rem 0', borderTop: '1px solid #f3f0eb',
            }}>
              <span style={{ fontSize: '0.8rem', color: '#333', minWidth: 0 }}>
                {t.label}
                {t.kind === 'locked' && (
                  <span style={{ display: 'block', fontSize: '0.7rem', color: '#999' }}>
                    Always asks — Norm can&apos;t give itself more freedom without you saying yes.
                  </span>
                )}
              </span>
              {t.kind === 'locked' ? (
                <span style={{ fontSize: '0.72rem', color: '#999' }}>Asks first</span>
              ) : (
                <span role="radiogroup" aria-label={t.label} style={{ display: 'inline-flex', border: '1px solid #e2ddd7', borderRadius: 6, overflow: 'hidden' }}>
                  {(['ask', 'always'] as const).map(v => {
                    const on = (t.value || 'ask') === v;
                    return (
                      <button key={v} role="radio" aria-checked={on} disabled={saving === t.key || on}
                        onClick={() => void save(t.key, v)}
                        style={{
                          fontFamily: 'inherit', fontSize: '0.72rem', padding: '0.3rem 0.7rem', border: 'none',
                          background: on ? '#a08060' : '#fff', color: on ? '#fff' : '#777',
                          cursor: on ? 'default' : 'pointer',
                        }}>
                        {v === 'ask' ? 'Ask me' : 'Always allow'}
                      </button>
                    );
                  })}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
