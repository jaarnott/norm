'use client';

import { useRef, useState, type FormEvent } from 'react';
import { Mail } from 'lucide-react';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import PublicShell, { Notice } from './PublicShell';
import { brandOf } from './brand';
import { PublicApiError, friendlyError, publicSend, tokenPath } from './publicApi';
import type { PortalPayload, PublicVenue } from './types';

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/**
 * Signing in to an event: an email the venue (or a colleague) added, then a
 * six-digit code emailed to it. "Send me a code" answers the same for every
 * address, so the wording never confirms who is on the list. The email lives
 * only in this component's state — never in the URL or browser storage; the
 * session is an httpOnly cookie the verify call sets.
 */
export default function PortalSignIn({ token, venue, notice, onSignedIn }: {
  token: string;
  venue: PublicVenue | null;
  notice?: string | null;
  onSignedIn: (payload: PortalPayload) => void;
}) {
  const [step, setStep] = useState<'email' | 'code'>('email');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const path = `/portal/${tokenPath(token)}`;

  const sendCode = async (again = false) => {
    const address = email.trim();
    if (!EMAIL.test(address)) {
      setError('Enter your email address, like name@example.com.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await publicSend('POST', `${path}/code`, { email: address });
      setStep('code');
      setCode('');
      setInfo(again ? "We've sent a new code. Use the newest one — earlier codes stop working." : null);
      window.setTimeout(() => codeRef.current?.focus(), 0);
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    const digits = code.replace(/\D/g, '');
    if (digits.length !== 6) {
      setError('Enter the 6-digit code from the email.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await publicSend<PortalPayload>('POST', `${path}/verify`, { email: email.trim(), code: digits }));
    } catch (err) {
      setError(err instanceof PublicApiError && err.status === 400 ? err.message : friendlyError(err));
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    void (step === 'email' ? sendCode() : verify());
  };

  const brand = brandOf(venue);
  return (
    <PublicShell brand={brand} width={520}>
      <div className="n-card" style={{ marginTop: 16, padding: '24px 20px' }}>
        <h1 style={{ margin: 0, fontSize: 'var(--fs-xl)', fontWeight: 700, color: 'var(--text)' }}>
          {step === 'email' ? 'Your event details' : 'Check your email'}
        </h1>
        <p style={{ margin: '6px 0 18px', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
          {step === 'email'
            ? `Sign in to see and update your booking${venue ? ` with ${venue.name}` : ''}. We'll email you a code — no password needed.`
            : `If ${email.trim()} has access to this event, we've emailed it a 6-digit code. It works for 10 minutes.`}
        </p>
        {notice && step === 'email' && <div style={{ marginBottom: 14 }}><Notice tone="info">{notice}</Notice></div>}
        {info && step === 'code' && <div style={{ marginBottom: 14 }}><Notice tone="info">{info}</Notice></div>}

        <form onSubmit={submit} noValidate style={{ margin: 0 }}>
          {step === 'email' ? (
            <>
              <label htmlFor="portal-email" className="n-label">Email address</label>
              <input
                id="portal-email"
                className="n-input"
                type="email"
                autoComplete="email"
                inputMode="email"
                autoCapitalize="off"
                spellCheck={false}
                value={email}
                maxLength={320}
                onChange={(e) => setEmail(e.target.value)}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? 'portal-error' : undefined}
                style={{ width: '100%', height: 44 }}
                autoFocus
              />
            </>
          ) : (
            <>
              <label htmlFor="portal-code" className="n-label">6-digit code</label>
              <input
                id="portal-code"
                ref={codeRef}
                className="n-input n-keep-size"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]*"
                maxLength={7}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/[^\d ]/g, ''))}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? 'portal-error' : undefined}
                style={{ width: '100%', maxWidth: 220, height: 52, fontSize: 24, letterSpacing: '0.3em', fontVariantNumeric: 'tabular-nums' }}
              />
            </>
          )}
          {error && <p id="portal-error" role="alert" style={{ margin: '8px 0 0', fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--error)' }}>{error}</p>}
          <Button type="submit" variant="primary" disabled={busy} style={{ display: 'flex', width: '100%', height: 44, marginTop: 16 }}>
            {step === 'email' && <Icon icon={Mail} size={16} />}
            {busy ? 'Please wait…' : step === 'email' ? 'Email me a code' : 'Sign in'}
          </Button>
        </form>

        {step === 'code' && (
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginTop: 14, fontSize: 'var(--fs-sm)' }}>
            <button type="button" className="n-btn n-btn--link" disabled={busy} onClick={() => void sendCode(true)}>Send a new code</button>
            <button type="button" className="n-btn n-btn--link" disabled={busy} onClick={() => { setStep('email'); setError(null); setInfo(null); setCode(''); }}>
              Use a different email
            </button>
          </div>
        )}
      </div>
      <p style={{ margin: '14px 4px 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)', textAlign: 'center' }}>
        Not getting a code? Check your spam folder, or ask the venue to add your email.
      </p>
    </PublicShell>
  );
}
