import type { ReactNode } from 'react';
import Link from 'next/link';
import MarketingNav from './_components/MarketingNav';
import { LOGIN_HREF } from './_components/links';
import './marketing.css';

/**
 * The public site: home, features, pricing and the legal pages. It shares
 * the app's typeface, neutrals and icons but speaks louder (marketing.css).
 * Server-rendered, laid out with CSS alone, so a page never jumps once
 * script loads.
 */
export default function MarketingLayout({ children }: { children: ReactNode }) {
  return (
    <div className="mkt">
      <header className="mkt-header">
        <div className="mkt-wrap">
          <MarketingNav />
        </div>
      </header>

      <main>{children}</main>

      <footer className="mkt-footer">
        <div className="mkt-wrap">
          <div className="mkt-footer-top">
            <div className="mkt-stack" style={{ gap: 10, maxWidth: 320 }}>
              <span className="mkt-brand">
                <span aria-hidden="true" className="mkt-brand-mark" style={{ width: 30, height: 30, fontSize: 16, borderRadius: 8 }}>N</span>
                <span className="mkt-brand-name" style={{ fontSize: 20 }}>Norm</span>
              </span>
              <p className="mkt-text" style={{ fontSize: 15 }}>The AI operations team for hospitality.</p>
            </div>
            <div className="mkt-footer-cols">
              <div className="mkt-footer-col">
                <span className="mkt-footer-label">Product</span>
                <Link href="/features">Features</Link>
                <Link href="/pricing">Pricing</Link>
              </div>
              <div className="mkt-footer-col">
                <span className="mkt-footer-label">Company</span>
                <Link href={LOGIN_HREF}>Log in</Link>
                <Link href="/privacy">Privacy</Link>
                <Link href="/terms">Terms</Link>
              </div>
            </div>
          </div>
          <div className="mkt-footer-base">© {new Date().getFullYear()} Norm. All rights reserved.</div>
        </div>
      </footer>
    </div>
  );
}
