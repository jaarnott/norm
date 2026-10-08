'use client';

import { useState, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import AuthShell, { authInput, authLabel, authSubmit } from '../components/auth/AuthShell';
import Button from '../components/ui/Button';

function ResetPasswordForm() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams.get('token') || '';

  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);

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
      const res = await fetch('/api/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, password }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || `Error ${res.status}`);
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
      <AuthShell subtitle="Reset your password">
        <div role="alert" style={{ fontSize: 'var(--fs-base)', color: 'var(--error)', textAlign: 'center' }}>
          Invalid or missing reset link.
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell subtitle="Reset your password" titleId="reset-title">
      {success ? (
        <div role="status" style={{
          fontSize: 'var(--fs-base)', color: 'var(--ok)', backgroundColor: 'var(--ok-bg)',
          padding: '12px 16px', borderRadius: 'var(--radius)', textAlign: 'center',
        }}>
          Password updated! Taking you to sign in…
        </div>
      ) : (
        <form onSubmit={handleSubmit} aria-labelledby="reset-title" style={{ margin: 0 }}>
          <label htmlFor="reset-password" style={authLabel}>New password</label>
          <input
            id="reset-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            required
            style={authInput}
          />

          <label htmlFor="reset-confirm" style={{ ...authLabel, marginTop: 16 }}>Confirm password</label>
          <input
            id="reset-confirm"
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
            {loading ? 'Please wait…' : 'Reset password'}
          </Button>
        </form>
      )}
    </AuthShell>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100dvh', backgroundColor: 'var(--canvas)', color: 'var(--muted)',
      }}>
        Loading…
      </div>
    }>
      <ResetPasswordForm />
    </Suspense>
  );
}
