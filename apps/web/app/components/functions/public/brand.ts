/**
 * Norm Functions — the venue's brand on customer pages, made safe to use.
 *
 * Brand values are staff-entered (or imported from Orbit), so each one is
 * checked for the one shape the page uses it in: a hex colour for the accent
 * rule, a font-family list for the wordmark, an image only from Norm's own
 * public file route, links only http(s). Anything else is dropped rather
 * than passed into a style or an href.
 */
import type { PublicVenue } from './types';

const HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;
const FONT = /^[\w\s,'"-]{1,120}$/;
const PUBLIC_FILE = /^\/api\/public\/files\/[A-Za-z0-9_-]{1,64}$/;

export function safeHex(value: unknown): string | null {
  return typeof value === 'string' && HEX.test(value.trim()) ? value.trim() : null;
}

export function safeFont(value: unknown): string | null {
  return typeof value === 'string' && FONT.test(value.trim()) ? value.trim() : null;
}

/** A brand image: only Norm's public file route (what the sanitiser allows in proposals too). */
export function safeImage(value: unknown): string | null {
  return typeof value === 'string' && PUBLIC_FILE.test(value) ? value : null;
}

/** An outbound link the venue configured: http(s) only — never javascript: or data:. */
export function safeLink(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const url = value.trim();
  return /^https?:\/\/[^\s]+$/i.test(url) ? url : null;
}

export interface Brand {
  name: string;
  logo: string | null;
  wordmark: string | null;
  tagline: string | null;
  accent: string | null;
  headingFont: string | null;
  footer: string | null;
}

export function brandOf(venue: PublicVenue | null | undefined): Brand | null {
  if (!venue) return null;
  const b = venue.brand;
  const colours = b?.colours ?? {};
  return {
    name: venue.name,
    logo: safeImage(b?.logo_url),
    wordmark: b?.wordmark?.trim() || null,
    tagline: b?.tagline?.trim() || null,
    accent: safeHex(colours.primary) ?? safeHex(colours.accent),
    headingFont: safeFont(b?.fonts?.heading),
    footer: b?.footer_contact?.trim() || null,
  };
}

/** Today's date where the customer is, as YYYY-MM-DD (for "valid until" checks). */
export function localToday(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}
