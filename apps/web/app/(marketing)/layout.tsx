import Link from 'next/link';

const NAV_LINKS = [
  { href: '/features', label: 'Features' },
  { href: '/pricing', label: 'Pricing' },
];

export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#faf8f5', color: '#2d2a26', fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      {/* Nav */}
      <nav style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '1.25rem 2rem', maxWidth: 1200, margin: '0 auto',
      }}>
        <Link href="/" style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: '1.5rem', fontWeight: 800, color: '#a08060', letterSpacing: '-0.02em' }}>Norm</span>
        </Link>
        <div style={{ display: 'flex', alignItems: 'center', gap: '2rem' }}>
          {NAV_LINKS.map(link => (
            <Link key={link.href} href={link.href} style={{ color: '#888', textDecoration: 'none', fontSize: '0.9rem', fontWeight: 500 }}>
              {link.label}
            </Link>
          ))}
          <Link href="/login" style={{ color: '#a08060', textDecoration: 'none', fontSize: '0.9rem', fontWeight: 500 }}>
            Log in
          </Link>
          <Link href="/login" style={{
            backgroundColor: '#a08060', color: '#fff', padding: '0.5rem 1.25rem',
            borderRadius: 8, fontSize: '0.9rem', fontWeight: 600, textDecoration: 'none',
          }}>
            Get Started
          </Link>
        </div>
      </nav>

      {/* Content */}
      <main>{children}</main>

      {/* Footer */}
      <footer style={{
        borderTop: '1px solid #e8e4de', padding: '3rem 2rem', maxWidth: 1200, margin: '0 auto',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: '2rem' }}>
          <div>
            <span style={{ fontSize: '1.2rem', fontWeight: 800, color: '#a08060' }}>Norm</span>
            <p style={{ color: '#999', fontSize: '0.82rem', marginTop: '0.5rem', maxWidth: 300 }}>
              AI-powered operations management for hospitality. Rostering, procurement, HR, and reporting — handled.
            </p>
          </div>
          <div style={{ display: 'flex', gap: '3rem' }}>
            <div>
              <h4 style={{ fontSize: '0.75rem', fontWeight: 600, color: '#999', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.75rem' }}>Product</h4>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                <Link href="/features" style={{ color: '#888', textDecoration: 'none', fontSize: '0.85rem' }}>Features</Link>
                <Link href="/pricing" style={{ color: '#888', textDecoration: 'none', fontSize: '0.85rem' }}>Pricing</Link>
              </div>
            </div>
            <div>
              <h4 style={{ fontSize: '0.75rem', fontWeight: 600, color: '#999', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.75rem' }}>Company</h4>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                <Link href="/login" style={{ color: '#888', textDecoration: 'none', fontSize: '0.85rem' }}>Log in</Link>
                <Link href="/privacy" style={{ color: '#888', textDecoration: 'none', fontSize: '0.85rem' }}>Privacy Policy</Link>
                <Link href="/terms" style={{ color: '#888', textDecoration: 'none', fontSize: '0.85rem' }}>Terms of Service</Link>
              </div>
            </div>
          </div>
        </div>
        <div style={{ borderTop: '1px solid #e8e4de', marginTop: '2rem', paddingTop: '1.5rem', color: '#bbb', fontSize: '0.78rem' }}>
          &copy; {new Date().getFullYear()} Norm. All rights reserved.
        </div>
      </footer>
    </div>
  );
}
