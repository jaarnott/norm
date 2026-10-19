'use client';

import { useState } from 'react';
import AuthShell, { authInput, authLabel, authLink, authSubmit } from './AuthShell';
import Button from '../ui/Button';
import AddressSearch from '../settings/AddressSearch';

interface LoginFormProps {
  onSuccess: (token: string, user: { id: string; email: string; full_name: string; role: string }) => void;
  /** Which form to open on: the marketing site's "Start free" opens sign-up. */
  initialMode?: 'login' | 'register';
}

export default function LoginForm({ onSuccess, initialMode = 'login' }: LoginFormProps) {
  const [mode, setMode] = useState<'login' | 'register' | 'forgot'>(initialMode);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
  // Signing up makes your business (organisation) and its first venue.
  const [orgName, setOrgName] = useState('');
  const [venueName, setVenueName] = useState('');
  const [venueAddress, setVenueAddress] = useState('');
  // The device's own timezone until an address is chosen: the owner is usually
  // signing up where the venue is, and a venue without one would fall back to
  // Auckland (wrong for an Australian venue).
  const [venueTimezone, setVenueTimezone] = useState(() => {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch { return ''; }
  });
  const [timezoneFromAddress, setTimezoneFromAddress] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [forgotSuccess, setForgotSuccess] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    if (mode === 'forgot') {
      try {
        await fetch('/api/auth/forgot-password', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email }),
        });
        setForgotSuccess(true);
      } catch {
        setError('Network error. Is the backend running?');
      } finally {
        setLoading(false);
      }
      return;
    }

    const url = mode === 'login' ? '/api/auth/login' : '/api/auth/register';
    const body = mode === 'login'
      ? { email, password }
      : {
          email, password, full_name: fullName,
          organization_name: orgName,
          venue_name: venueName,
          venue_location: venueAddress || null,
          venue_timezone: venueTimezone || null,
        };

    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || `Error ${res.status}`);
        return;
      }

      const data = await res.json();
      onSuccess(data.access_token, data.user);
    } catch (e) {
      console.error(e);
      setError('Network error. Is the backend running?');
    } finally {
      setLoading(false);
    }
  };

  const subtitle = mode === 'forgot' ? 'Reset your password' : mode === 'login' ? 'Sign in to your account' : 'Create a new account';
  const errorBox = error ? (
    <div role="alert" style={{
      fontSize: 'var(--fs-sm)', color: 'var(--error)', backgroundColor: 'var(--error-bg)',
      padding: '8px 12px', borderRadius: 'var(--radius-sm)', marginTop: 16,
    }}>
      {error}
    </div>
  ) : null;

  return (
    <AuthShell
      subtitle={subtitle}
      titleId="login-title"
      below={mode !== 'forgot' && !forgotSuccess ? (
        <>
          {mode === 'login' ? 'New to Norm? ' : 'Already have an account? '}
          <button
            type="button"
            data-testid="login-toggle-mode"
            onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(''); }}
            style={{ ...authLink, fontWeight: 600 }}
          >
            {mode === 'login' ? 'Create an account' : 'Sign in'}
          </button>
        </>
      ) : undefined}
    >
      {mode === 'forgot' && forgotSuccess ? (
        <div>
          <div role="status" style={{
            fontSize: 'var(--fs-base)', color: 'var(--ok)', backgroundColor: 'var(--ok-bg)',
            padding: '12px 16px', borderRadius: 'var(--radius)', marginBottom: 20, textAlign: 'center',
          }}>
            If that email exists, we&apos;ve sent a reset link.
          </div>
          <div style={{ textAlign: 'center' }}>
            <button type="button" onClick={() => { setMode('login'); setForgotSuccess(false); setError(''); }} style={authLink}>
              Back to sign in
            </button>
          </div>
        </div>
      ) : (
        <form onSubmit={handleSubmit} aria-labelledby="login-title" style={{ margin: 0 }}>
          {mode === 'forgot' ? (
            <>
              <label htmlFor="forgot-email" style={authLabel}>Email</label>
              <input
                id="forgot-email"
                type="email"
                autoComplete="email"
                placeholder="you@venue.co.nz"
                value={email}
                onChange={e => setEmail(e.target.value)}
                required
                style={authInput}
              />
              {errorBox}
              <Button type="submit" variant="primary" disabled={loading} style={authSubmit}>
                {loading ? 'Please wait…' : 'Send reset link'}
              </Button>
              <div style={{ textAlign: 'center', marginTop: 16, fontSize: 'var(--fs-sm)' }}>
                <button type="button" onClick={() => { setMode('login'); setError(''); }} style={authLink}>
                  Back to sign in
                </button>
              </div>
            </>
          ) : (
            <>
              {mode === 'register' && (
                <div style={{ marginBottom: 16 }}>
                  <label htmlFor="login-name" style={authLabel}>Full name</label>
                  <input
                    id="login-name"
                    data-testid="login-name"
                    type="text"
                    autoComplete="name"
                    value={fullName}
                    onChange={e => setFullName(e.target.value)}
                    required
                    style={authInput}
                  />
                </div>
              )}

              <label htmlFor="login-email" style={authLabel}>Email</label>
              <input
                id="login-email"
                data-testid="login-email"
                type="email"
                autoComplete="email"
                placeholder="you@venue.co.nz"
                value={email}
                onChange={e => setEmail(e.target.value)}
                required
                style={authInput}
              />

              <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, margin: '16px 0 6px' }}>
                <label htmlFor="login-password" style={{ ...authLabel, marginBottom: 0 }}>Password</label>
                {mode === 'login' && (
                  <button type="button" onClick={() => { setMode('forgot'); setError(''); }} style={{ ...authLink, fontSize: 'var(--fs-sm)' }}>
                    Forgot password?
                  </button>
                )}
              </div>
              <input
                id="login-password"
                data-testid="login-password"
                type="password"
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                value={password}
                onChange={e => setPassword(e.target.value)}
                required
                style={authInput}
              />

              {mode === 'register' && (
                <>
                  <div style={{ margin: '24px 0 4px', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-soft)' }}>
                    Your business
                  </div>
                  <label htmlFor="signup-org" style={authLabel}>Business name</label>
                  <input
                    id="signup-org"
                    data-testid="signup-org"
                    type="text"
                    autoComplete="organization"
                    placeholder="e.g. Harbour Hospitality Group"
                    value={orgName}
                    onChange={e => setOrgName(e.target.value)}
                    required
                    style={authInput}
                  />
                  <label htmlFor="signup-venue" style={{ ...authLabel, marginTop: 16 }}>First venue</label>
                  <input
                    id="signup-venue"
                    data-testid="signup-venue"
                    type="text"
                    placeholder="e.g. The Harbour Bar"
                    value={venueName}
                    onChange={e => setVenueName(e.target.value)}
                    required
                    style={authInput}
                  />
                  <label style={{ ...authLabel, marginTop: 16, display: 'block' }}>Venue address</label>
                  <AddressSearch
                    value={venueAddress}
                    onChange={setVenueAddress}
                    onSelect={sel => { setVenueAddress(sel.address); if (sel.timezone) { setVenueTimezone(sel.timezone); setTimezoneFromAddress(true); } }}
                    placeholder="Start typing the address"
                    inputStyle={authInput}
                  />
                  <div style={{ marginTop: 6, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                    {venueTimezone
                      ? <>Timezone: {venueTimezone}{timezoneFromAddress ? '' : ' (from this device — choosing the address sets it from the venue)'}. Trading days and reports follow it.</>
                      : 'Choosing the address sets the venue’s timezone. You can add more venues later.'}
                  </div>
                </>
              )}

              {errorBox}

              <Button data-testid="login-submit" type="submit" variant="primary" disabled={loading} style={authSubmit}>
                {loading ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}
              </Button>
            </>
          )}
        </form>
      )}
    </AuthShell>
  );
}
