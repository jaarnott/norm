'use client';

/**
 * Entertainers settings — for a payroll writer or an owner. Chiefly the
 * closing-manager roles (D22): whose clock-in or roster Norm reads each
 * morning to find who closed the venue and should approve the night's
 * performances. The roles are picked from the venue's real Loaded roles
 * (Loaded compares them case-blind) and a name can be typed for a role a
 * venue hasn't got yet. Also the times-mismatch tolerance and how the
 * approval reminders repeat.
 */
import { useEffect, useId, useState } from 'react';
import { Plus, X } from 'lucide-react';
import Dialog from '../../../ui/Dialog';
import Button from '../../../ui/Button';
import { entGet, entSend, errorText, isStale } from '../api';
import { cleanRole, hasRole, toggleRole } from '../logic';
import { DialogActions, Field, FieldGrid, Hint, Notice, Toggle, WriteError, inputStyle } from '../ui';
import type { EntMeta, EntSettings } from '../types';

interface LoadedRoles {
  venue_id: string;
  roles: string[];
  error: string | null;
}

export default function SettingsDialog({ meta, onSaved, onClose }: {
  meta: EntMeta;
  onSaved: (settings: EntSettings) => void;
  onClose: () => void;
}) {
  const ids = { venue: useId(), add: useId(), tol: useId(), every: useId(), max: useId() };
  const start = meta.settings;
  const [roles, setRoles] = useState<string[]>(start.closing_manager_roles);
  const [venueId, setVenueId] = useState(meta.venues[0]?.id ?? '');
  const [answer, setAnswer] = useState<LoadedRoles | null>(null);
  const [typed, setTyped] = useState('');
  const [tolerance, setTolerance] = useState(String(start.mismatch_tolerance_minutes));
  const [every, setEvery] = useState(String(start.reminder_interval_days));
  const [max, setMax] = useState(String(start.reminder_max));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; stale: boolean } | null>(null);

  useEffect(() => {
    if (!venueId) return;
    let alive = true;
    entGet<LoadedRoles>('/settings/loaded-roles', { venue_id: venueId }).then(
      (res) => { if (alive) setAnswer(res); },
      (e: unknown) => { if (alive) setAnswer({ venue_id: venueId, roles: [], error: errorText(e) }); },
    );
    return () => { alive = false; };
  }, [venueId]);

  // The answer for the venue picked now; another venue's is still loading.
  const loaded = answer && answer.venue_id === venueId ? answer : null;
  const fromLoaded = loaded?.roles ?? [];
  const elsewhere = roles.filter((r) => !hasRole(fromLoaded, r));
  const venueName = meta.venues.find((v) => v.id === venueId)?.name ?? 'this venue';

  const add = () => {
    if (!cleanRole(typed)) return;
    setRoles((r) => toggleRole(r, typed, true));
    setTyped('');
  };

  const save = async () => {
    if (roles.length === 0) return setError({ text: 'Choose at least one closing-manager role.', stale: false });
    setBusy(true);
    setError(null);
    try {
      const saved = await entSend<EntSettings>('PUT', '/settings', {
        closing_manager_roles: roles,
        mismatch_tolerance_minutes: Number(tolerance),
        reminder_interval_days: Number(every),
        reminder_max: Number(max),
        version: start.version,
      });
      onSaved(saved);
    } catch (e) {
      setError({ text: errorText(e), stale: isStale(e) });
    }
    setBusy(false);
  };

  return (
    <Dialog title="Entertainers settings" width={560} onClose={() => { if (!busy) onClose(); }}>
      <div style={{ fontWeight: 600, fontSize: 'var(--fs-sm)' }}>Closing-manager roles</div>
      <Hint style={{ marginBottom: 10 }}>
        Each morning Norm finds who closed the venue the night of a performance — the last person clocked in (else rostered) in one of these roles — and emails them a link to approve it.
      </Hint>
      {meta.venues.length > 1 && (
        <Field label="Loaded roles at" htmlFor={ids.venue}>
          <select id={ids.venue} className="n-select" style={inputStyle} value={venueId} onChange={(e) => setVenueId(e.target.value)}>
            {meta.venues.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
          </select>
        </Field>
      )}
      <div role="group" aria-label={`Roles in ${venueName}'s Loaded`} style={{ display: 'flex', flexWrap: 'wrap', gap: '0 18px', marginTop: 8 }}>
        {!venueId ? (
          <Hint>No venue to read Loaded&rsquo;s roles from — type the role names below.</Hint>
        ) : loaded === null ? (
          <Hint>Reading {venueName}&rsquo;s roles from Loaded…</Hint>
        ) : loaded.error ? (
          <Notice tone="warn">Loaded&rsquo;s roles couldn&rsquo;t be read ({loaded.error}). Type the role names below instead.</Notice>
        ) : fromLoaded.length === 0 ? (
          <Hint>{venueName} has no roles in Loaded.</Hint>
        ) : (
          fromLoaded.map((name) => (
            <Toggle key={name} checked={hasRole(roles, name)} onChange={(on) => setRoles((r) => toggleRole(r, name, on))}>{name}</Toggle>
          ))
        )}
      </div>
      {elsewhere.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <Hint>Also chosen{loaded && !loaded.error ? ` (not a role at ${venueName})` : ''}:</Hint>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
            {elsewhere.map((name) => (
              <Button key={name} size="xs" icon={X} onClick={() => setRoles((r) => toggleRole(r, name, false))} aria-label={`Remove ${name}`}>{name}</Button>
            ))}
          </div>
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', marginTop: 10 }}>
        <div style={{ flex: '1 1 auto' }}>
          <Field label="Another role" htmlFor={ids.add} hint="As it is named in Loaded; capitals don't matter.">
            <input id={ids.add} className="n-input" style={inputStyle} value={typed} onChange={(e) => setTyped(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }} />
          </Field>
        </div>
        <div style={{ marginBottom: 22 }}><Button icon={Plus} onClick={add} disabled={!cleanRole(typed)}>Add</Button></div>
      </div>

      <div style={{ fontWeight: 600, fontSize: 'var(--fs-sm)', marginTop: 16, marginBottom: 8 }}>Approvals</div>
      <FieldGrid min={150}>
        <Field label="Times may differ by (minutes)" htmlFor={ids.tol} hint="Before the performer's and manager's times count as a mismatch.">
          <input id={ids.tol} className="n-input" style={inputStyle} type="number" min={0} max={120} value={tolerance} onChange={(e) => setTolerance(e.target.value)} />
        </Field>
        <Field label="Remind every (days)" htmlFor={ids.every}>
          <input id={ids.every} className="n-input" style={inputStyle} type="number" min={1} max={30} value={every} onChange={(e) => setEvery(e.target.value)} />
        </Field>
        <Field label="Reminders at most" htmlFor={ids.max} hint="Then it goes on the venue digest.">
          <input id={ids.max} className="n-input" style={inputStyle} type="number" min={0} max={20} value={max} onChange={(e) => setMax(e.target.value)} />
        </Field>
      </FieldGrid>
      <WriteError message={error?.text ?? null} stale={error?.stale} onReload={onClose} />
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save'}</Button>
      </DialogActions>
    </Dialog>
  );
}
