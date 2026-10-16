/**
 * Norm Functions proposals — pure helpers: the editable srcdoc for the Edit
 * tab, image tags for the library, the option badges, the default covering
 * message. No DOM, so vitest can pin them.
 */
import type { BadgeTone } from '../../ui/Badge';
import { escapeHtml } from '../inbox/text';
import { hostedFontsCsp, hostedFontsLink } from '../shared/fonts';
import type { OptionSnapshot } from './types';

export const PUBLIC_FILES = '/api/public/files/';

/** The Edit tab's document: the proposal's HTML and CSS inside the same kind
 *  of locked-down frame the preview uses (no scripts — the frame is sandboxed
 *  without allow-scripts — no connections, images only from Norm's public
 *  files, fonts only from Norm's hosted set). CSS is stored sanitised, but text typed in the Code tab isn't yet,
 *  so `<` is escaped: CSS can never close its <style>. */
export function editableDocument(html: string, css: string, origin: string): string {
  const csp = ["default-src 'none'", `img-src ${origin}${PUBLIC_FILES}`, ...hostedFontsCsp(origin), "base-uri 'none'", "form-action 'none'"].join('; ');
  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    `<meta http-equiv="Content-Security-Policy" content="${csp}">` +
    '<meta name="referrer" content="no-referrer">' +
    hostedFontsLink(origin) +
    `<style>${(css || '').replace(/</g, '\\3c ')}</style>` +
    `</head><body>${html || ''}</body></html>`
  );
}

/** A library image as proposal HTML (the sanitiser keeps only these URLs). */
export function imageTag(fileId: string, name?: string | null): string {
  return `<img src="${PUBLIC_FILES}${encodeURIComponent(fileId)}" alt="${escapeHtml(name || '')}" style="max-width:100%;height:auto">`;
}

/** Put `insert` in place of the selection [start, end) of `text`. */
export function spliceText(text: string, start: number, end: number, insert: string): { text: string; caret: number } {
  const a = Math.max(0, Math.min(start, text.length));
  const b = Math.max(a, Math.min(end, text.length));
  return { text: text.slice(0, a) + insert + text.slice(b), caret: a + insert.length };
}

/** What the customer can do with an offered option, as a badge. */
export function optionBadge(o: Pick<OptionSnapshot, 'state' | 'confirmable'>): { tone: BadgeTone; label: string } {
  if (o.state === 'confirmed') return { tone: 'ok', label: 'Confirmed' };
  if (o.state === 'withdrawn') return { tone: 'neutral', label: 'Withdrawn' };
  if (o.state === 'declined') return { tone: 'neutral', label: 'Declined' };
  if (o.confirmable) return { tone: 'ok', label: 'Can confirm' };
  return { tone: 'warn', label: 'Needs approval' };
}

export const QUOTE_TOKEN = '{{quote_link}}';

/** The covering email's starting text. A proposal send puts the quote link at
 *  {{quote_link}} (or after the message when it's missing). */
export function defaultMessage(mode: 'proposal' | 'email', firstName: string): string {
  const hi = `<p>Hi ${escapeHtml(firstName || 'there')},</p>`;
  if (mode === 'email') return `${hi}<p></p>`;
  return (
    `${hi}<p>Thanks for your enquiry — your proposal is ready. You can view it and confirm the option that suits you here:</p>` +
    `<p>${QUOTE_TOKEN}</p><p>Any questions, just reply to this email.</p>`
  );
}
