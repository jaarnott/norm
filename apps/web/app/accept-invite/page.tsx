'use client';

import { useState, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import AuthShell, { authInput, authLabel, authSubmit } from '../components/auth/AuthShell';
import Button from '../components/ui/Button';

function AcceptInviteForm() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams.get('token') || '';

  const [fullName, setFullName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [alreadyUsed, setAlreadyUsed] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }
    if (password.length < 6) {
      setError('Password must be at least 6 characters');
      return;
    }

    setLoading(true);
    try {
      const res = await fetch('/api/auth/accept-invite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, full_name: fullName, password }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const detail = data.detail || `Error ${res.status}`;
        if (detail.includes('already been used')) {
          setAlreadyUsed(true);
        } else {
          setError(detail);
        }
        return;
      }

      setSuccess(true);
      setTimeout(() => router.push('/app'), 2000);
    } catch {
      setError('Network error. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  if (!token) {
    return (
      <AuthShell subtitle="Set up your account">
        <div role="alert" style={{ fontSize: 'var(--fs-base)', color: 'var(--error)', textAlign: 'center' }}>
          Invalid or missing invitation link.
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell subtitle="Set up your account" titleId="invite-title">
      {alreadyUsed ? (
        <div style={{ textAlign: 'center' }}>
          <div role="status" style={{
            fontSize: 'var(--fs-base)', color: 'var(--warn)', backgroundColor: 'var(--warn-bg)',
            padding: '12px 16px', borderRadius: 'var(--radius)', marginBottom: 20,
          }}>
            This invite has already been used. Your account is set up.
          </div>
          <a href="/app" className="n-btn n-btn--primary" style={{ ...authSubmit, marginTop: 0 }}>
            Go to sign in
          </a>
        </div>
      ) : success ? (
        <div role="status" style={{
          fontSize: 'var(--fs-base)', color: 'var(--ok)', backgroundColor: 'var(--ok-bg)',
          padding: '12px 16px', borderRadius: 'var(--radius)', textAlign: 'center',
        }}>
          Account set up! Taking you to sign in…
        </div>
      ) : (
        <form onSubmit={handleSubmit} aria-labelledby="invite-title" style={{ margin: 0 }}>
          <label htmlFor="invite-name" style={authLabel}>Full name</label>
          <input
            id="invite-name"
            type="text"
            autoComplete="name"
            value={fullName}
            onChange={e => setFullName(e.target.value)}
            required
            style={authInput}
          />

          <label htmlFor="invite-password" style={{ ...authLabel, marginTop: 16 }}>Password</label>
          <input
            id="invite-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            required
            style={authInput}
          />

          <label htmlFor="invite-confirm" style={{ ...authLabel, marginTop: 16 }}>Confirm password</label>
          <input
            id="invite-confirm"
            type="password"
            autoComplete="new-password"
            value={confirmPassword}
            onChange={e => setConfirmPassword(e.target.value)}
            required
            style={authInput}
          />

          {error && (
            <div role="alert" style={{
              fontSize: 'var(--fs-sm)', color: 'var(--error)', backgroundColor: 'var(--error-bg)',
              padding: '8px 12px', borderRadius: 'var(--radius-sm)', marginTop: 16,
            }}>
              {error}
            </div>
          )}

          <Button type="submit" variant="primary" disabled={loading} style={authSubmit}>
            {loading ? 'Please wait…' : 'Set up account'}
          </Button>
        </form>
      )}
    </AuthShell>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense fallback={
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100dvh', backgroundColor: 'var(--canvas)', color: 'var(--muted)',
      }}>
        Loading…
      </div>
    }>
      <AcceptInviteForm />
    </Suspense>
  );
}
