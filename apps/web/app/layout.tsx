import type { Metadata, Viewport } from 'next';
import localFont from 'next/font/local';
import './globals.css';

// Figtree, self-hosted: next/font/google would fetch from Google during every
// production build, and a failed fetch fails the build. Two subsets, so Latin
// loads first and Latin Extended (Māori macrons, ā ē ī ō ū) only when a page
// uses it. tokens.css chains the two variables in --font-sans.
const figtree = localFont({
  src: './fonts/Figtree-latin.woff2',
  weight: '300 900',
  display: 'swap',
  variable: '--font-figtree',
  // No generated fallback here: next/font would put a metric-matched local
  // Arial straight after "figtree" in the variable, and Arial covers ā/ē/ī/ō/ū,
  // so the browser would never reach the Latin Extended file below. The ext
  // font's own fallback still stands in for Latin text while fonts load.
  adjustFontFallback: false,
  declarations: [{
    prop: 'unicode-range',
    value: 'U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD',
  }],
});
const figtreeExt = localFont({
  src: './fonts/Figtree-latin-ext.woff2',
  weight: '300 900',
  display: 'swap',
  preload: false,
  variable: '--font-figtree-ext',
  declarations: [{
    prop: 'unicode-range',
    value: 'U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C4, U+2113, U+2C60-2C7F, U+A720-A7FF',
  }],
});

export const metadata: Metadata = {
  // Share images and canonical links resolve against the public site.
  metadataBase: new URL('https://bettercallnorm.com'),
  title: 'Norm',
  description: 'AI-powered operations assistant for hospitality',
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: '#faf8f5',
  colorScheme: 'light',
  // Content may run under the notch / home indicator; the mobile top bar and
  // composer pad themselves with env(safe-area-inset-*).
  viewportFit: 'cover',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${figtree.variable} ${figtreeExt.variable}`}>
      <body>{children}</body>
    </html>
  );
}
