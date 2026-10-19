'use client';

import { useId, useState } from 'react';
import { apiFetch } from '../../lib/api';
import Button from '../ui/Button';

/** Where a password login keeps the account it works on, and what to call one
 *  (Loaded Legacy: `x_loaded_company_id`, "Loaded company"). */
export interface AccountChoice { key: string; label: string }

interface Choose extends AccountChoice {
  accounts: { id: string; name: string }[];
  suggested: string | null;
}

/**
 * Connect one venue's password-login connector (auth_type password_login):
 * the username and password, checked by the API before anything is kept.
 *
 * When one login reaches several accounts (Loaded: every company the person
 * belongs to), the API answers 409 with the login's accounts and the form
 * shows them to pick the venue's one; the pick is sent as the connection's
 * own `choice.key`. Used by Settings → Venues and the connect card
 * (Settings → Connections), so both behave the same.
 */
export default function PasswordLoginForm({
  connectorName, venueId, fields, initial, accountChoice, account, onSaved, onCancel,
}: {
  connectorName: string;
  venueId: string;
  fields: { key: string; label: string; secret?: boolean }[];
  /** Starting values (a connected row's username; a masked password keeps the stored one). */
  initial?: Record<string, string>;
  accountChoice?: AccountChoice | null;
  /** The account a connected row works on now. */
  account?: string | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const id = useId();
  const [form, setForm] = useState<Record<string, string>>(() => {
    const start: Record<string, string> = {};
    for (const f of fields) start[f.key] = initial?.[f.key] ?? '';
    return start;
  });
  const [choose, setChoose] = useState<Choose | null>(null);
  const [picked, setPicked] = useState('');
  const [prompt, setPrompt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async (extra: Record<string, string> = {}) => {
    setBusy(true);
    setError(null);
    try {
      const config = { ...form, ...(choose && picked ? { [choose.key]: picked } : {}), ...extra };
      const res = await apiFetch(`/api/connectors/${connectorName}`, {
        method: 'PUT',
        body: JSON.stringify({ config, enabled: true, venue_id: venueId }),
      });
      if (res.status === 409) {
        const d = await res.json().catch(() => ({}));
        if (d.detail?.choose) {
          setChoose(d.detail.choose);
          setPicked(d.detail.choose.suggested || '');
          setPrompt(d.detail.message || null);
          return;
        }
      }
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setError(typeof d.detail === 'string' ? d.detail : `Couldn't save (${res.status})`);
        return;
      }
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const mustPick = !!choose && !picked;

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); save(); }}
      onClick={(e) => e.stopPropagation()}
      style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
    >
      {fields.map(f => (
        <div key={f.key}>
          <label className="n-label" htmlFor={`${id}-${f.key}`}>{f.label}</label>
          <input
            id={`${id}-${f.key}`}
            className="n-input"
            type={f.secret ? 'password' : 'text'}
            autoComplete={f.secret ? 'new-password' : 'off'}
            value={form[f.key] || ''}
            onChange={e => { setForm({ ...form, [f.key]: e.target.value }); setChoose(null); }}
            style={{ width: '100%' }}
          />
        </div>
      ))}
      {choose && (
        <div>
          <label className="n-label" htmlFor={`${id}-account`}>{choose.label}</label>
          {prompt && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginBottom: 4 }}>{prompt}</div>}
          <select id={`${id}-account`} className="n-select" value={picked} onChange={e => setPicked(e.target.value)} style={{ width: '100%' }}>
            <option value="">Choose…</option>
            {choose.accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>
      )}
      <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
        Norm checks the login before keeping it, and stores the password encrypted.
      </div>
      {error && <div role="alert" style={{ fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{error}</div>}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 4 }}>
        <Button variant="primary" size="sm" type="submit" disabled={busy || mustPick}>
          {busy ? 'Checking…' : 'Save & connect'}
        </Button>
        <Button size="sm" onClick={onCancel}>Cancel</Button>
        {/* A connected row keeps its account; this asks for the list again. */}
        {accountChoice && account && !choose && (
          <Button variant="quiet" size="sm" disabled={busy} onClick={() => save({ [accountChoice.key]: '' })}>
            Change {accountChoice.label.toLowerCase()}
          </Button>
        )}
      </div>
    </form>
  );
}
