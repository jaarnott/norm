'use client';

import { useState } from 'react';

interface LoginFormProps {
  onSuccess: (token: string, user: { id: string; email: string; full_name: string; role: string }) => void;
}

export default function LoginForm({ onSuccess }: LoginFormProps) {
  const [mode, setMode] = useState<'login' | 'register' | 'forgot'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
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
      : { email, password, full_name: fullName };

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

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      height: '100vh',
      backgroundColor: '#faf8f5',
      fontFamily: 'system-ui, sans-serif',
    }}>
      <div style={{
        maxWidth: 380,
        width: '100%',
        padding: '2rem 1.5rem',
        margin: '0 1rem',
        backgroundColor: '#fff',
        borderRadius: 12,
        boxShadow: '0 2px 12px rgba(0,0,0,0.08)',
      }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <div style={{ fontSize: '1.5rem', fontWeight: 700, color: '#1a1a1a', marginBottom: 4 }}>
            Norm
          </div>
          <div style={{ fontSize: '0.85rem', color: '#888' }}>
            {mode === 'forgot' ? 'Reset your password' : mode === 'login' ? 'Sign in to your account' : 'Create a new account'}
          </div>
        </div>

        {mode === 'forgot' && forgotSuccess ? (
          <div>
            <div style={{
              fontSize: '0.85rem', color: '#28a745', backgroundColor: '#d4edda',
              padding: '12px 16px', borderRadius: 8, marginBottom: '1.25rem', textAlign: 'center',
            }}>
              If that email exists, we&apos;ve sent a reset link.
            </div>
            <button
              onClick={() => { setMode('login'); setForgotSuccess(false); setError(''); }}
              style={{
                background: 'none', border: 'none', color: '#c4a882',
                fontSize: '0.82rem', cursor: 'pointer', fontFamily: 'inherit',
                display: 'block', margin: '0 auto',
              }}
            >
              Back to login
            </button>
          </div>
        ) : (
        <form onSubmit={handleSubmit}>
          {mode === 'forgot' ? (
            <>
              <div style={{ marginBottom: '1rem' }}>
                <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                  Email
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  required
                  style={{
                    width: '100%', padding: '10px 12px', border: '1px solid #e2ddd7',
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
                {loading ? 'Please wait...' : 'Send Reset Link'}
              </button>

              <div style={{ textAlign: 'center', marginTop: '1.25rem' }}>
                <button
                  onClick={() => { setMode('login'); setError(''); }}
                  type="button"
                  style={{
                    background: 'none', border: 'none', color: '#c4a882',
                    fontSize: '0.82rem', cursor: 'pointer', fontFamily: 'inherit',
                  }}
                >
                  Back to login
                </button>
              </div>
            </>
          ) : (
          <>
          {mode === 'register' && (
            <div style={{ marginBottom: '1rem' }}>
              <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                Full Name
              </label>
              <input
                data-testid="login-name"
                type="text"
                value={fullName}
                onChange={e => setFullName(e.target.value)}
                required
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  border: '1px solid #ddd',
                  borderRadius: 8,
                  fontSize: '0.9rem',
                  fontFamily: 'inherit',
                  boxSizing: 'border-box',
                  outline: 'none',
                }}
              />
            </div>
          )}

          <div style={{ marginBottom: '1rem' }}>
            <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
              Email
            </label>
            <input
              data-testid="login-email"
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              required
              style={{
                width: '100%',
                padding: '10px 12px',
                border: '1px solid #ddd',
                borderRadius: 8,
                fontSize: '0.9rem',
                fontFamily: 'inherit',
                boxSizing: 'border-box',
                outline: 'none',
              }}
            />
          </div>

          <div style={{ marginBottom: '1.5rem' }}>
            <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
              Password
            </label>
            <input
              data-testid="login-password"
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
              style={{
                width: '100%',
                padding: '10px 12px',
                border: '1px solid #ddd',
                borderRadius: 8,
                fontSize: '0.9rem',
                fontFamily: 'inherit',
                boxSizing: 'border-box',
                outline: 'none',
              }}
            />
          </div>

          {error && (
            <div style={{
              fontSize: '0.82rem',
              color: '#e53e3e',
              backgroundColor: '#fef2f2',
              padding: '8px 12px',
              borderRadius: 6,
              marginBottom: '1rem',
            }}>
              {error}
            </div>
          )}

          <button
            data-testid="login-submit"
            type="submit"
            disabled={loading}
            style={{
              width: '100%',
              padding: '10px',
              fontSize: '0.9rem',
              fontWeight: 600,
              border: 'none',
              borderRadius: 8,
              backgroundColor: '#c4a882',
              color: '#fff',
              cursor: loading ? 'not-allowed' : 'pointer',
              fontFamily: 'inherit',
              opacity: loading ? 0.7 : 1,
            }}
          >
            {loading ? 'Please wait...' : mode === 'login' ? 'Sign In' : 'Create Account'}
          </button>

          {mode === 'login' && (
            <div style={{ textAlign: 'center', marginTop: '0.75rem' }}>
              <button
                type="button"
                onClick={() => { setMode('forgot'); setError(''); }}
                style={{
                  background: 'none', border: 'none', color: '#999',
                  fontSize: '0.78rem', cursor: 'pointer', fontFamily: 'inherit',
                }}
              >
                Forgot password?
              </button>
            </div>
          )}
          </>
          )}
        </form>
        )}

        {mode !== 'forgot' && !forgotSuccess && (
        <div style={{ textAlign: 'center', marginTop: '1.25rem' }}>
          <button
            data-testid="login-toggle-mode"
            onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(''); }}
            style={{
              background: 'none',
              border: 'none',
              color: '#c4a882',
              fontSize: '0.82rem',
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            {mode === 'login' ? "Don't have an account? Register" : 'Already have an account? Sign in'}
          </button>
        </div>
        )}
      </div>
    </div>
  );
}
