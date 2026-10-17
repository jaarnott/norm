'use client';

/**
 * Per-venue Xero settings, edited on the Finance → Bills page: which account
 * each kind of purchase posts to, how bills land, and whether Norm creates a
 * bill in Xero when it receives an invoice into Loaded.
 *
 * Reads accounts + the current mapping through `xero.get_accounts` (one call,
 * the venue's own connection) and saves with `PUT /api/connectors/xero`, which
 * MERGES config keys — the tenant binding the connect hook wrote stays put.
 * The mapping lives in `Connection.config.xero_mapping`; create_bill reads it
 * through norm.get_connection_settings.
 */

import React, { useEffect, useState, useCallback } from 'react';
import { Settings2, Check } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Button from '../ui/Button';
import PageState from '../ui/PageState';

interface Account { code: string | null; name: string; type: string; tax_type?: string | null }
interface Mapping {
  accounts?: Record<string, string>;
  due_days?: number | string;
  bill_status?: string;
  line_amount_types?: string;
  push_bills?: boolean;
  tracking?: { category_id?: string; option_id?: string };
}

const KINDS: { key: string; label: string; hint: string }[] = [
  { key: 'default', label: 'Default account', hint: 'Used when a line has no category match' },
  { key: 'food', label: 'Food purchases', hint: 'Lines in the Food category' },
  { key: 'beverage', label: 'Beverage purchases', hint: 'Lines in the Beverage category' },
  { key: 'other', label: 'Other purchases', hint: 'Lines in the Other category' },
];

export default function XeroSettingsPanel({ venueId, onSaved }: { venueId: string; onSaved?: () => void }) {
  const [open, setOpen] = useState(false);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [mapping, setMapping] = useState<Mapping>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch('/api/connectors/xero/execute/get_accounts', {
        method: 'POST',
        body: JSON.stringify({ params: { venue_id: venueId, kind: 'expense', limit: '500' } }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) { setError(body?.detail || body?.error || `Could not load accounts (${res.status})`); return; }
      const doc = body?.data?.accounts ? body.data : body?.data?.data;
      if (!doc?.accounts) { setError(body?.data?.error || body?.error || 'Xero returned no accounts'); return; }
      setAccounts(doc.accounts as Account[]);
      setMapping((doc.mapping as Mapping) || {});
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load accounts');
    } finally {
      setLoading(false);
    }
  }, [venueId]);

  useEffect(() => { if (open) void load(); }, [open, load]);

  const save = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const clean: Mapping = {
        ...mapping,
        accounts: Object.fromEntries(Object.entries(mapping.accounts || {}).filter(([, v]) => !!v)),
        due_days: mapping.due_days ? Number(mapping.due_days) : undefined,
      };
      const res = await apiFetch('/api/connectors/xero', {
        method: 'PUT',
        body: JSON.stringify({ venue_id: venueId, config: { xero_mapping: clean } }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) { setError(body?.detail || `Could not save (${res.status})`); return; }
      setSaved(true);
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setSaving(false);
    }
  };

  const setAccount = (kind: string, code: string) => {
    setSaved(false);
    setMapping((m) => ({ ...m, accounts: { ...(m.accounts || {}), [kind]: code } }));
  };

  const field = (label: string, control: React.ReactNode, hint?: string) => (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
      <span className="n-label">{label}</span>
      {control}
      {hint && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{hint}</span>}
    </label>
  );

  return (
    <div style={{ marginBottom: 12 }}>
      <Button size="sm" variant={open ? 'secondary' : 'quiet'} icon={Settings2} onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        Xero settings
      </Button>
      {open && (
        <div className="n-card" style={{ marginTop: 8, padding: 14 }}>
          {loading ? (
            <PageState kind="loading" title="Loading the chart of accounts…" />
          ) : error && !accounts.length ? (
            <PageState kind="error" title="Could not load Xero settings." detail={error} />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
                {KINDS.map((k) => field(
                  k.label,
                  <select
                    className="n-select"
                    value={mapping.accounts?.[k.key] || ''}
                    onChange={(e) => setAccount(k.key, e.target.value)}
                  >
                    <option value="">{k.key === 'default' ? '— none —' : '— use default —'}</option>
                    {accounts.filter((a) => a.code).map((a) => (
                      <option key={a.code as string} value={a.code as string}>{a.code} · {a.name}</option>
                    ))}
                  </select>,
                  k.hint,
                ))}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 }}>
                {field('Bills land as',
                  <select className="n-select" value={mapping.bill_status || 'DRAFT'} onChange={(e) => { setSaved(false); setMapping((m) => ({ ...m, bill_status: e.target.value })); }}>
                    <option value="DRAFT">Draft</option>
                    <option value="SUBMITTED">Awaiting approval</option>
                    <option value="AUTHORISED">Awaiting payment (authorised)</option>
                  </select>,
                )}
                {field('Due days',
                  <input className="n-input" type="number" min={0} max={120} value={mapping.due_days ?? 20} onChange={(e) => { setSaved(false); setMapping((m) => ({ ...m, due_days: e.target.value })); }} />,
                  'When the bill has no due date of its own',
                )}
                {field('Tracking category id',
                  <input className="n-input" value={mapping.tracking?.category_id || ''} placeholder="optional" onChange={(e) => { setSaved(false); setMapping((m) => ({ ...m, tracking: { ...(m.tracking || {}), category_id: e.target.value } })); }} />,
                )}
                {field('Tracking option id',
                  <input className="n-input" value={mapping.tracking?.option_id || ''} placeholder="optional" onChange={(e) => { setSaved(false); setMapping((m) => ({ ...m, tracking: { ...(m.tracking || {}), option_id: e.target.value } })); }} />,
                )}
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)' }}>
                <input type="checkbox" checked={!!mapping.push_bills} onChange={(e) => { setSaved(false); setMapping((m) => ({ ...m, push_bills: e.target.checked })); }} />
                <span>Create a bill in Xero when Norm receives an invoice into Loaded</span>
              </label>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <Button size="sm" variant="primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
                {saved && <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--ok)', fontSize: 'var(--fs-sm)' }}><Check size={14} /> Saved</span>}
                {error && <span style={{ color: 'var(--error)', fontSize: 'var(--fs-sm)' }}>{error}</span>}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
