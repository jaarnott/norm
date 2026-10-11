'use client';

import { useEffect, useRef } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Menu, X } from 'lucide-react';
import { LOGIN_HREF, NAV_LINKS, SIGNUP_HREF } from './links';

/**
 * The marketing header. Wide screens get the links in a row; phones get a
 * disclosure menu (a <details>, so it works before any script runs) that
 * closes itself when a link changes the page — the layout stays mounted
 * across pages, and so would an open menu.
 */
export default function MarketingNav() {
  const pathname = usePathname();
  const menuRef = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    if (menuRef.current) menuRef.current.open = false;
  }, [pathname]);

  const links = NAV_LINKS.map(link => (
    <Link
      key={link.href}
      href={link.href}
      className="mkt-link"
      aria-current={pathname === link.href ? 'page' : undefined}
    >
      {link.label}
    </Link>
  ));

  return (
    <nav aria-label="Main" className="mkt-nav">
      <Link href="/" className="mkt-brand">
        <span aria-hidden="true" className="mkt-brand-mark">N</span>
        <span className="mkt-brand-name">Norm</span>
      </Link>

      <div className="mkt-nav-links">
        {links}
        <Link href={LOGIN_HREF} className="mkt-link">Log in</Link>
        <Link href={SIGNUP_HREF} className="mkt-btn mkt-btn--primary mkt-btn--sm">Start free</Link>
      </div>

      <details ref={menuRef} className="mkt-menu">
        <summary aria-label="Menu">
          <Menu className="mkt-menu-open" size={24} strokeWidth={1.75} aria-hidden="true" />
          <X className="mkt-menu-close" size={24} strokeWidth={1.75} aria-hidden="true" />
        </summary>
        <div className="mkt-menu-panel mkt-wrap">
          {links}
          <Link href={LOGIN_HREF} className="mkt-link">Log in</Link>
          <Link href={SIGNUP_HREF} className="mkt-btn mkt-btn--primary">Start free</Link>
        </div>
      </details>
    </nav>
  );
}
