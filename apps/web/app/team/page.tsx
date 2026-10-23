'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { setActiveOrg, setStoredUser, setToken } from '../lib/api';

const CODE_LENGTH = 6;

/**
 * Venue staff sign in here (docs/plans/staff-access.md, sections 2, 6 and
 * 11.7): the address on the staff QR code. Type your email, Norm emails a
 * six-digit code if you are on a venue's staff list, type the code, and you
 * are in — no password. The page says the same thing after the email step
 * whether or not the email is known, so nobody can test which emails are on
 * the list.
 */
export default function TeamSignInPage() {
  const router = useRouter();
  const [step, setStep] = useState<'email' | 'code'>('email');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const codeRef = useRef<HTMLInputElement>(null);

  const cleanEmail = email.trim().toLowerCase();

  const requestCode = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!cleanEmail || busy) return;
    setBusy(true);
    setError('');
    try {
      // 202 whether or not the email is known; a rate limit reads the same
      // way here, so the screen never says which emails are on the list.
      await fetch('/api/auth/staff/code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: cleanEmail }),
      });
      setCode('');
      setStep('code');
    } catch {
      setError("We couldn't reach Norm. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  const verify = async (value: string) => {
    if (value.length !== CODE_LENGTH || busy) return;
    setBusy(true);
    setError('');
    try {
      const res = await fetch('/api/auth/staff/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: cleanEmail, code: value }),
      });
      if (!res.ok) {
        setCode('');
        setError(res.status === 429
          ? 'Too many tries. Ask for a new code in a few minutes.'
          : "That code isn't right. Check it and try again, or ask for a new one.");
        requestAnimationFrame(() => codeRef.current?.focus());
        return;
      }
      const data = await res.json();
      // A fresh sign-in: the server picks the organisation, so nothing stale
      // from an earlier account on this phone should steer it.
      setActiveOrg(null);
      setStoredUser(data.user);
      setToken(data.access_token);
      router.push('/app');
    } catch {
      setError("We couldn't reach Norm. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  // Six digits typed or pasted sign in straight away.
  useEffect(() => {
    if (step === 'code' && code.length === CODE_LENGTH) void verify(code);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, step]);

  useEffect(() => {
    if (step === 'code') codeRef.current?.focus();
  }, [step]);

  return (
    <div style={{ minHeight: '100dvh', display: 'flex', flexDirection: 'column', backgroundColor: 'var(--canvas)', lineHeight: 1.45 }}>
      <header style={{
        flex: '0 0 auto', boxSizing: 'content-box', height: 52, paddingTop: 'env(safe-area-inset-top, 0px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', borderBottom: '1px solid var(--line)',
      }}>
        <span aria-hidden style={{ fontSize: 'var(--fs-xl)', fontWeight: 800, letterSpacing: '-0.02em', color: 'var(--brand)' }}>Norm</span>
      </header>

      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '32px 16px 24px' }}>
        <div style={{ width: '100%', maxWidth: 390 }}>
          {step === 'email' ? (
            <form onSubmit={requestCode} className="n-card" style={{ padding: 24 }}>
              <h1 style={{ margin: '0 0 6px', fontSize: 'var(--fs-xl)', fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--text)' }}>Sign in to Norm</h1>
              <p style={{ margin: '0 0 20px', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
                Type the email your venue has for you and we will send you a code. There is no password.
              </p>
              <label htmlFor="team-email" className="n-label">Email</label>
              <input
                id="team-email"
                data-testid="team-signin-email"
                className="n-input"
                type="email"
                inputMode="email"
                autoComplete="email"
                autoFocus
                required
                placeholder="you@example.com"
                value={email}
                onChange={e => setEmail(e.target.value)}
                style={{ width: '100%', height: 48, fontSize: 16 }}
              />
              {error && <p role="alert" style={{ margin: '12px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{error}</p>}
              <button type="submit" className="n-pill-btn" data-testid="team-signin-send" disabled={busy || !cleanEmail} style={{ marginTop: 20 }}>
                {busy ? 'Sending…' : 'Email me a code'}
              </button>
            </form>
          ) : (
            <div className="n-card" style={{ padding: 24 }}>
              <h1 style={{ margin: '0 0 6px', fontSize: 'var(--fs-xl)', fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--text)' }}>We&apos;ve emailed you a code</h1>
              <p style={{ margin: '0 0 20px', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
                If <strong style={{ color: 'var(--text)', fontWeight: 600 }}>{cleanEmail}</strong> is on a venue&apos;s staff list, a six-digit code is on its way. It works for ten minutes.
              </p>
              <label htmlFor="team-code" className="n-label">Your code</label>
              <div className="n-code-boxes" onClick={() => codeRef.current?.focus()}>
                {Array.from({ length: CODE_LENGTH }, (_, i) => (
                  <div key={i} className="n-code-box" aria-hidden data-active={code.length === i || (i === CODE_LENGTH - 1 && code.length === CODE_LENGTH)}>
                    {code[i] ?? ''}
                  </div>
                ))}
                <input
                  ref={codeRef}
                  id="team-code"
                  data-testid="team-signin-code"
                  className="n-code-input"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9]*"
                  maxLength={CODE_LENGTH}
                  aria-label="Six-digit code"
                  value={code}
                  disabled={busy}
                  onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, CODE_LENGTH))}
                />
              </div>
              {error && <p role="alert" style={{ margin: '12px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{error}</p>}
              <button type="button" className="n-pill-btn" data-testid="team-signin-verify" disabled={busy || code.length !== CODE_LENGTH} onClick={() => verify(code)} style={{ marginTop: 20 }}>
                {busy ? 'Signing in…' : 'Sign in'}
              </button>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginTop: 16, fontSize: 'var(--fs-sm)' }}>
                <button type="button" className="n-btn n-btn--link" onClick={() => requestCode()} disabled={busy}>Send another code</button>
                <button type="button" className="n-btn n-btn--link" data-testid="team-signin-change-email" onClick={() => { setStep('email'); setCode(''); setError(''); }}>Use a different email</button>
              </div>
            </div>
          )}

          <p style={{ margin: '20px 0 0', textAlign: 'center', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
            Managers and owners can also{' '}
            <Link href="/login" style={{ color: 'var(--muted)', textDecoration: 'underline', textUnderlineOffset: 2 }}>sign in with a password</Link>.
          </p>
        </div>
      </main>
    </div>
  );
}
