'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import LoginForm from '../components/auth/LoginForm';
import { setToken, setStoredUser } from '../lib/api';

export default function LoginPage() {
  const router = useRouter();
  const [, setLoggedIn] = useState(false);

  const handleLogin = (token: string, user: { id: string; email: string; full_name: string; role: string }) => {
    setToken(token);
    setStoredUser(user);
    setLoggedIn(true);
    router.push('/app');
  };

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      backgroundColor: '#faf8f5', fontFamily: 'system-ui, sans-serif',
    }}>
      <div style={{ width: '100%', maxWidth: 400 }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <span style={{ fontSize: '2rem', fontWeight: 800, color: '#a08060' }}>Norm</span>
          <p style={{ color: '#888', fontSize: '0.9rem', marginTop: '0.5rem' }}>Sign in to your account</p>
        </div>
        <LoginForm onSuccess={handleLogin} />
      </div>
    </div>
  );
}
