'use client';

import { Suspense } from 'react';
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

  // LoginForm draws the whole screen, wordmark included — this page used to
  // add a second "Norm / Sign in to your account" header above it.
  return <LoginForm onSuccess={handleLogin} />;
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginInner />
    </Suspense>
  );
}
