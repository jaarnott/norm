'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';
import Button from '../ui/Button';
import Dialog from '../ui/Dialog';

/**
 * Settings → Preferences: the person's own kitchen-tablet PIN
 * (docs/plans/staff-access.md, sections 2 and 5). Shown only when /auth/me
 * reports a staff record in the active organisation. Set or Reset asks the
 * API for a new PIN (POST /api/me/pin) and shows it once, the way a manager's
 * Set PIN on the People page does; Remove clears it.
 */
export default function TabletPinCard() {
  const [hasPin, setHasPin] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pinShown, setPinShown] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    apiFetch('/api/auth/me')
      .then(async res => {
        if (!live || !res.ok) return;
        const me = await res.json();
        if (live) setHasPin(me?.staff ? !!me.staff.has_pin : null);
      })
      .catch(() => { /* no card */ });
    return () => { live = false; };
  }, []);

  const newPin = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch('/api/me/pin', { method: 'POST' });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(typeof data.detail === 'string' ? data.detail : 'Norm couldn’t set a PIN right now.');
        return;
      }
      const data = await res.json();
      setPinShown(String(data.pin ?? ''));
      setHasPin(true);
    } catch {
      setError('Norm couldn’t set a PIN right now.');
    } finally {
      setBusy(false);
    }
  }, []);

  const removePin = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch('/api/me/pin', { method: 'DELETE' });
      if (!res.ok) { setError('Norm couldn’t remove the PIN right now.'); return; }
      setHasPin(false);
    } catch {
      setError('Norm couldn’t remove the PIN right now.');
    } finally {
      setBusy(false);
    }
  }, []);

  // No staff record here (an office user): nothing to show.
  if (hasPin === null) return null;

  return (
    <div className="n-card" style={{ padding: 16, marginBottom: 32 }} data-testid="tablet-pin-card">
      <div style={{ fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)', marginBottom: 4 }}>Kitchen tablet PIN</div>
      <p style={{ margin: '0 0 12px', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        {hasPin
          ? 'You have a PIN for the kitchen tablet. Reset it if you have forgotten it; Norm shows a new one once.'
          : 'Tap a five-digit PIN on the kitchen tablet to use it as you. Norm shows the PIN once.'}
      </p>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Button variant="primary" onClick={newPin} disabled={busy} data-testid="my-pin-set">
          {busy ? 'Working…' : hasPin ? 'Reset PIN' : 'Set a PIN'}
        </Button>
        {hasPin && (
          <Button onClick={removePin} disabled={busy} data-testid="my-pin-remove">Remove PIN</Button>
        )}
      </div>
      {error && <p role="alert" style={{ margin: '10px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{error}</p>}

      {pinShown !== null && (
        <Dialog title="Your tablet PIN" width={360} onClose={() => setPinShown(null)}>
          <div data-testid="my-pin-dialog">
            <div style={{ textAlign: 'center', padding: '8px 0 16px', fontSize: 36, fontWeight: 600, letterSpacing: '0.3em', color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }} data-testid="my-pin-value">
              {pinShown}
            </div>
            <p style={{ margin: 0, fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
              Write it down, Norm won’t show it again.
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 20 }}>
              <Button variant="primary" onClick={() => setPinShown(null)}>Done</Button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  );
}
