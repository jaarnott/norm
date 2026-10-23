import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import localFont from 'next/font/local';
import './hospo.css';

/**
 * The Hospo apps' own landing pages (/hospo-hr, /hospo-functions): their own
 * look, header and footer, so they read as products of their own that work
 * with Norm (docs/plans/marketplace-and-partner-apps.md).
 *
 * Unlisted for now — not in the nav, and asking search engines not to index
 * them — because their free plans and sign-up without Norm aren't built yet
 * and the prices on them are placeholders.
 */

// Self-hosted like Figtree (app/layout.tsx): next/font/google would fetch
// from Google during every build. Both are SIL Open Font License 1.1.
const sora = localFont({ src: '../fonts/Sora-latin.woff2', variable: '--font-hospo-head', weight: '600 800', display: 'swap' });
const dmSans = localFont({ src: '../fonts/DMSans-latin.woff2', variable: '--font-hospo-body', weight: '400 700', display: 'swap' });

export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function HospoLayout({ children }: { children: ReactNode }) {
  return <div className={`${sora.variable} ${dmSans.variable}`}>{children}</div>;
}
