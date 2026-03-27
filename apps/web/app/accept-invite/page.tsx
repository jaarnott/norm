'use client';

import { useState, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';

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
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100vh', backgroundColor: '#faf8f5', fontFamily: 'system-ui, sans-serif',
      }}>
        <div style={{
          width: 380, padding: '2.5rem', backgroundColor: '#fff', borderRadius: 12,
          boxShadow: '0 2px 12px rgba(0,0,0,0.08)', border: '1px solid #e2ddd7', textAlign: 'center',
        }}>
          <div style={{ fontSize: '1rem', color: '#e53e3e' }}>Invalid or missing invitation link.</div>
        </div>
      </div>
    );
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '100vh', backgroundColor: '#faf8f5', fontFamily: 'system-ui, sans-serif',
    }}>
      <div style={{
        width: 380, padding: '2.5rem', backgroundColor: '#fff', borderRadius: 12,
        boxShadow: '0 2px 12px rgba(0,0,0,0.08)', border: '1px solid #e2ddd7',
      }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <div style={{ fontSize: '1.5rem', fontWeight: 700, color: '#1a1a1a', marginBottom: 4 }}>Norm</div>
          <div style={{ fontSize: '0.85rem', color: '#888' }}>Set up your account</div>
        </div>

        {alreadyUsed ? (
          <div style={{ textAlign: 'center' }}>
            <div style={{
              fontSize: '0.85rem', color: '#856404', backgroundColor: '#fff3cd',
              padding: '12px 16px', borderRadius: 8, marginBottom: '1.25rem',
            }}>
              This invite has already been used. Your account is set up.
            </div>
            <a
              href="/app"
              style={{
                display: 'inline-block', padding: '10px 2rem', fontSize: '0.9rem',
                fontWeight: 600, border: 'none', borderRadius: 8,
                backgroundColor: '#c4a882', color: '#fff', textDecoration: 'none',
              }}
            >
              Go to Login
            </a>
          </div>
        ) : success ? (
          <div style={{
            fontSize: '0.85rem', color: '#28a745', backgroundColor: '#d4edda',
            padding: '12px 16px', borderRadius: 8, textAlign: 'center',
          }}>
            Account set up! Redirecting to login...
          </div>
        ) : (
          <form onSubmit={handleSubmit}>
            <div style={{ marginBottom: '1rem' }}>
              <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                Full Name
              </label>
              <input
                type="text"
                value={fullName}
                onChange={e => setFullName(e.target.value)}
                required
                style={{
                  width: '100%', padding: '0.65rem', border: '1px solid #e2ddd7',
                  borderRadius: 8, fontSize: '0.9rem', fontFamily: 'inherit',
                  boxSizing: 'border-box', outline: 'none',
                }}
              />
            </div>

            <div style={{ marginBottom: '1rem' }}>
              <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                Password
              </label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                required
                style={{
                  width: '100%', padding: '0.65rem', border: '1px solid #e2ddd7',
                  borderRadius: 8, fontSize: '0.9rem', fontFamily: 'inherit',
                  boxSizing: 'border-box', outline: 'none',
                }}
              />
            </div>

            <div style={{ marginBottom: '1.5rem' }}>
              <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                Confirm Password
              </label>
              <input
                type="password"
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                required
                style={{
                  width: '100%', padding: '0.65rem', border: '1px solid #e2ddd7',
                  borderRadius: 8, fontSize: '0.9rem', fontFamily: 'inherit',
                  boxSizing: 'border-box', outline: 'none',
                }}
              />
            </div>

            {error && (
              <div style={{
                fontSize: '0.82rem', color: '#e53e3e', backgroundColor: '#fef2f2',
                padding: '8px 12px', borderRadius: 6, marginBottom: '1rem',
              }}>
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              style={{
                width: '100%', padding: '10px', fontSize: '0.9rem', fontWeight: 600,
                border: 'none', borderRadius: 8, backgroundColor: '#c4a882', color: '#fff',
                cursor: loading ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
                opacity: loading ? 0.7 : 1,
              }}
            >
              {loading ? 'Please wait...' : 'Set Up Account'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense fallback={
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100vh', backgroundColor: '#faf8f5', fontFamily: 'system-ui, sans-serif',
        color: '#999',
      }}>
        Loading...
      </div>
    }>
      <AcceptInviteForm />
    </Suspense>
  );
}
