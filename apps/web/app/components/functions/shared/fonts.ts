/**
 * Web fonts Norm hosts itself (public/fonts/brand — owner, 16 Oct 2026): a
 * proposal, brand or hosted form names a family and Norm's stylesheet
 * supplies it, so no customer's browser calls Google. The API's twin is
 * HOSTED_FONTS in services/functions/sanitize.py (its test checks the files).
 */
export const HOSTED_FONTS_PATH = '/fonts/brand/';
export const HOSTED_FONTS_CSS = `${HOSTED_FONTS_PATH}fonts.css`;

/** CSP sources a srcdoc document on `origin` needs for the hosted fonts. */
export function hostedFontsCsp(origin: string): string[] {
  return [`style-src 'unsafe-inline' ${origin}${HOSTED_FONTS_PATH}`, `font-src ${origin}${HOSTED_FONTS_PATH}`];
}

/** The stylesheet link for a srcdoc document on `origin`. */
export function hostedFontsLink(origin: string): string {
  return `<link rel="stylesheet" href="${origin}${HOSTED_FONTS_CSS}">`;
}

/** Font stacks offered in Brand settings: the hosted families, each with a
 *  fallback every device has, then plain system stacks. */
export const FONT_STACKS = [
  '"Playfair Display", Georgia, serif',
  '"Cormorant Garamond", Georgia, serif',
  '"Libre Baskerville", Georgia, serif',
  '"DM Serif Display", Georgia, serif',
  'Inter, system-ui, sans-serif',
  'Montserrat, "Helvetica Neue", Arial, sans-serif',
  'Poppins, system-ui, sans-serif',
  '"Bebas Neue", Impact, sans-serif',
  '"JetBrains Mono", Menlo, Consolas, monospace',
  '"Courier Prime", "Courier New", monospace',
  'Georgia, serif',
  'system-ui, sans-serif',
  '"Helvetica Neue", Helvetica, Arial, sans-serif',
];
