'use client';

import { Suspense } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import LoginForm from '../components/auth/LoginForm';
import { setToken, setStoredUser } from '../lib/api';

function LoginInner() {
  const router = useRouter();
  const params = useSearchParams();

  // Honour a ?next= target (e.g. the MCP consent screen bounced us here).
  // Same-origin paths only, so this can't be turned into an open redirect.
  const rawNext = params.get('next') || '';
  const next = rawNext.startsWith('/') && !rawNext.startsWith('//') ? rawNext : '/app';

  const handleLogin = (token: string, user: { id: string; email: string; full_name: string; role: string }) => {
    setToken(token);
    setStoredUser(user);
    router.push(next);
  };

  // ?mode=signup (the marketing site's "Start free") opens on Create account.
  const initialMode = params.get('mode') === 'signup' ? 'register' : 'login';

  // LoginForm draws the whole screen, wordmark included — this page used to
  // add a second "Norm / Sign in to your account" header above it. The quiet
  // line at the foot sends venue staff to their own sign-in, which has no
  // password (docs/plans/staff-access.md 11.7).
  return (
    <>
      <LoginForm onSuccess={handleLogin} initialMode={initialMode} />
      <div style={{
        position: 'fixed', left: 0, right: 0, bottom: 'calc(8px + env(safe-area-inset-bottom, 0px))',
        textAlign: 'center', fontSize: 'var(--fs-sm)', color: 'var(--muted)', pointerEvents: 'none',
      }}>
        <Link href="/team" data-testid="login-staff-link" style={{ pointerEvents: 'auto', color: 'var(--muted)', textDecoration: 'underline', textUnderlineOffset: 2 }}>
          Venue staff? Sign in with a code
        </Link>
      </div>
    </>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginInner />
    </Suspense>
  );
}
