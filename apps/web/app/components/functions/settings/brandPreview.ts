/**
 * The brand's live proposal preview. Every proposal starts from the venue's
 * brand template (`template_html/css` with `{{tokens}}` filled), or from
 * Norm's default layout in the brand's colours and fonts when there's no
 * template — proposals.starting_document on the server. This is its twin for
 * the settings page: the same tokens and blocks, filled with sample data, so
 * an unsaved edit shows straight away.
 *
 * The result goes into `<iframe sandbox srcdoc>` WITHOUT allow-scripts and
 * with a CSP that loads nothing but Norm's public images, so a half-typed
 * template can't run or fetch anything. The server sanitises on save; what
 * comes back is what proposals will use.
 */
import { hostedFontsCsp, hostedFontsLink } from '../shared/fonts';
import { money } from '../shared/format';
import type { BrandColourKey, BrandColours } from './types';
import { SERVER_DEFAULTS } from './contrast';

export function esc(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;');
}

/** Replace `{{token}}` placeholders (spaces allowed inside the braces);
 *  unknown tokens stay as written so a typo is visible — fill_tokens' rule. */
export function fillTokens(template: string, values: Record<string, string>): string {
  return (template || '').replace(/\{\{\s*([a-z_]+)\s*\}\}/g, (whole, name: string) =>
    Object.prototype.hasOwnProperty.call(values, name) ? values[name] : whole,
  );
}

/** The tokens a brand template can use, for the editor's reference list. */
export const TEMPLATE_TOKENS: { token: string; what: string }[] = [
  { token: 'logo', what: 'Logo (or the wordmark)' },
  { token: 'venue_name', what: 'Venue name' },
  { token: 'client_name', what: 'Customer’s name' },
  { token: 'client_company', what: 'Customer’s company' },
  { token: 'event_name', what: 'Event name' },
  { token: 'event_date', what: 'Event date' },
  { token: 'guests', what: 'Guest numbers' },
  { token: 'booking_type', what: 'Booking type' },
  { token: 'spaces', what: 'The areas on offer, with capacities' },
  { token: 'menus', what: 'The pre-order menus' },
  { token: 'options', what: 'The options table (minimum spend, total)' },
  { token: 'inclusions', what: 'What’s included' },
  { token: 'terms', what: 'Good to know (terms)' },
  { token: 'signoff', what: 'Sign-off, contact and validity' },
  { token: 'valid_days', what: 'Days the quote is valid' },
  { token: 'valid_until', what: 'Date the quote is valid until' },
  { token: 'tax_label', what: 'Tax wording, e.g. “incl. GST”' },
];

export interface PreviewArea {
  name: string;
  description: string | null;
  capacity_seated: number | null;
  capacity_standing: number | null;
}

export interface PreviewInput {
  venueName: string;
  wordmark: string;
  tagline: string;
  /** Where the preview loads the logo from (a blob: URL of the staff copy). */
  logoUrl: string | null;
  footer: string;
  validDays: number;
  colours: BrandColours;
  fonts: { heading?: string; body?: string };
  inclusions: string[];
  terms: { title: string; body: string }[];
  signoff: { title: string; body: string; contact: string };
  templateHtml: string;
  templateCss: string;
  currency: string;
  taxLabel: string;
  bookingLabel: string;
  areas: PreviewArea[];
}

const SAMPLE = {
  client: 'Sam Taylor',
  company: 'Northline Group',
  event: 'End-of-year awards night',
  date: 'Friday 4 December 2026',
  guests: 60,
  sentOn: '2026-11-06',
};

function addDaysLabel(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + days, 12));
  return dt.toLocaleDateString('en-NZ', { timeZone: 'UTC', weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }).replace(',', '');
}

function logoBlock(p: PreviewInput): string {
  const name = p.wordmark || p.venueName;
  if (p.logoUrl) return `<img class="logo" src="${esc(p.logoUrl)}" alt="${esc(name)}">`;
  return `<div class="wordmark">${esc(name)}</div>`;
}

function spacesBlock(p: PreviewInput): string {
  const areas = p.areas.slice(0, 2);
  if (!areas.length) return '';
  const parts = areas.map((a) => {
    const caps = [
      a.capacity_seated ? `${a.capacity_seated} seated` : '',
      a.capacity_standing ? `${a.capacity_standing} standing` : '',
    ].filter(Boolean);
    const cap = caps.length ? `<p class="capacity">${esc(caps.join(' · '))}</p>` : '';
    const desc = a.description ? `<p>${esc(a.description)}</p>` : '';
    return `<div class="space"><h3>${esc(a.name)}</h3>${cap}${desc}</div>`;
  });
  return `<section class="spaces"><h2>${areas.length === 1 ? 'Your space' : 'Your spaces'}</h2>${parts.join('')}</section>`;
}

function menusBlock(p: PreviewInput): string {
  const tag = (t: string) => `<span class="tag">${esc(t)}</span>`;
  return (
    '<section class="menus"><h2>Your menu</h2><div class="menu">' +
    '<h3>Set menu — two courses</h3>' +
    `<p class="price">${esc(money(6500, p.currency, 0))} per person</p>` +
    '<h4>To start</h4><ul>' +
    `<li>Wood-fired focaccia, whipped butter ${tag('V')}</li>` +
    '<li>Kingfish crudo, citrus, chilli</li></ul>' +
    '<h4>Mains</h4><ul>' +
    `<li>Market fish, salsa verde ${tag('GF')}</li>` +
    '<li>Slow-cooked lamb shoulder</li></ul></div></section>'
  );
}

function optionsBlock(p: PreviewInput): string {
  const area = p.areas[0]?.name ?? 'Upstairs';
  const amount = esc(money(600000, p.currency, 0));
  return (
    '<section class="options"><h2>Your option</h2><table>' +
    '<thead><tr><th>Option</th><th>Space</th><th>When</th>' +
    '<th class="num">Minimum spend</th><th class="num">Total</th></tr></thead>' +
    `<tbody><tr><td>Exclusive hire</td><td>${esc(area)}</td><td>${esc(SAMPLE.date)}, 6:30pm – 11pm</td>` +
    `<td class="num">${amount}</td><td class="num">${amount}</td></tr></tbody></table>` +
    `<p class="note">All prices ${esc(p.taxLabel)}. The total is the minimum spend or your food, whichever is higher.</p></section>`
  );
}

function inclusionsBlock(p: PreviewInput): string {
  const items = p.inclusions.map((i) => i.trim()).filter(Boolean);
  if (!items.length) return '';
  return `<section class="inclusions"><h2>What’s included</h2><ul>${items.map((i) => `<li>${esc(i)}</li>`).join('')}</ul></section>`;
}

function termsBlock(p: PreviewInput): string {
  const terms = p.terms.filter((t) => t.title.trim() || t.body.trim());
  if (!terms.length) return '';
  const parts = terms.map((t) => (t.title.trim() ? `<h3>${esc(t.title)}</h3>` : '') + `<p>${esc(t.body)}</p>`);
  return `<section class="terms"><h2>Good to know</h2>${parts.join('')}</section>`;
}

function signoffBlock(p: PreviewInput): string {
  const title = p.signoff.title.trim() || 'Next steps';
  const body = p.signoff.body.trim() || 'Reply to this email or confirm your option below and we’ll lock in your date.';
  const contact = p.signoff.contact.trim() ? `<p class="contact">${esc(p.signoff.contact)}</p>` : '';
  const footer = p.footer.trim() ? `<p class="footer">${esc(p.footer)}</p>` : '';
  return (
    `<section class="signoff"><h2>${esc(title)}</h2><p>${esc(body)}</p>${contact}` +
    `<p class="valid">This proposal is valid for ${p.validDays} days.</p>${footer}</section>`
  );
}

/** Token values for the sample proposal. */
export function sampleTokens(p: PreviewInput): Record<string, string> {
  return {
    venue_name: esc(p.venueName),
    client_name: esc(SAMPLE.client),
    client_company: esc(SAMPLE.company),
    event_name: esc(SAMPLE.event),
    event_date: esc(SAMPLE.date),
    guests: esc(SAMPLE.guests),
    booking_type: esc(p.bookingLabel),
    valid_days: esc(p.validDays),
    valid_until: esc(addDaysLabel(SAMPLE.sentOn, p.validDays)),
    tax_label: esc(p.taxLabel),
    logo: logoBlock(p),
    options: optionsBlock(p),
    menus: menusBlock(p),
    spaces: spacesBlock(p),
    inclusions: inclusionsBlock(p),
    terms: termsBlock(p),
    signoff: signoffBlock(p),
  };
}

/** Norm's default layout as a template (tokens, not values) — the starting
 *  point offered in the template editor. Mirrors proposals.default_html. */
export function defaultTemplateHtml(tagline: string): string {
  const tag = tagline.trim() ? `\n    <p class="tagline">${esc(tagline.trim())}</p>` : '';
  return `<article class="proposal">
  <header class="cover">
    {{logo}}${tag}
    <p class="eyebrow">Event proposal</p>
    <h1>{{event_name}}</h1>
    <p>Prepared for {{client_name}}</p>
  </header>
  <section class="glance">
    <h2>At a glance</h2>
    <dl>
      <dt>Date</dt><dd>{{event_date}}</dd>
      <dt>Guests</dt><dd>{{guests}}</dd>
      <dt>Format</dt><dd>{{booking_type}}</dd>
    </dl>
  </section>
  {{spaces}}
  {{menus}}
  {{options}}
  {{inclusions}}
  {{terms}}
  {{signoff}}
</article>
`;
}

/** Norm's default proposal CSS in the brand's colours and fonts — mirrors
 *  proposals.default_css (an unset colour or font takes its default). */
export function defaultCss(colours: BrandColours, fonts: { heading?: string; body?: string }): string {
  const c = (k: BrandColourKey) => (colours[k] || '').trim() || SERVER_DEFAULTS[k];
  const heading = (fonts.heading || '').trim() || 'Georgia, serif';
  const body = (fonts.body || '').trim() || 'system-ui, sans-serif';
  return `.proposal { font-family: ${body}; color: ${c('text')}; background: ${c('background')};
  max-width: 820px; margin: 0 auto; padding: 32px; line-height: 1.5; }
.proposal h1, .proposal h2, .proposal h3, .proposal h4 { font-family: ${heading};
  color: ${c('primary')}; }
.proposal .cover { border-bottom: 2px solid ${c('accent')}; padding-bottom: 24px;
  margin-bottom: 24px; }
.proposal .logo { max-height: 80px; }
.proposal .wordmark { font-family: ${heading}; font-size: 28px; color: ${c('primary')}; }
.proposal .eyebrow { text-transform: uppercase; letter-spacing: 0.12em;
  color: ${c('accent')}; font-size: 13px; }
.proposal section { margin: 28px 0; }
.proposal .glance dl { display: grid; grid-template-columns: 140px 1fr; gap: 6px 16px; }
.proposal .glance dt { color: ${c('accent')}; }
.proposal table { width: 100%; border-collapse: collapse; }
.proposal th, .proposal td { text-align: left; padding: 8px; border-bottom: 1px solid ${c('panel')}; }
.proposal .num { text-align: right; }
.proposal .menu, .proposal .space { background: ${c('panel')}; padding: 16px; margin: 12px 0;
  border-radius: 6px; }
.proposal .tag { font-size: 11px; border: 1px solid ${c('accent')}; border-radius: 4px;
  padding: 0 4px; margin-left: 4px; }
.proposal .note, .proposal .valid, .proposal .footer { font-size: 13px; opacity: 0.8; }
`;
}

/** A complete srcdoc document: nothing loads but Norm's public images (and
 *  the blob: copy of an unsaved logo) and hosted fonts, no scripts, links
 *  open in a new tab. */
export function previewDocument(html: string, css: string, origin: string): string {
  const csp = [
    "default-src 'none'",
    `img-src ${origin}/api/public/files/ blob:`,
    ...hostedFontsCsp(origin),
    "base-uri 'none'",
    "form-action 'none'",
  ].join('; ');
  // A `</style` inside the CSS would close the style element early.
  const safeCss = (css || '').replace(/<\/(style)/gi, '<\\/$1');
  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    `<meta http-equiv="Content-Security-Policy" content="${esc(csp)}">` +
    '<meta name="referrer" content="no-referrer">' +
    '<meta name="viewport" content="width=device-width, initial-scale=1">' +
    '<base target="_blank"><title>Proposal preview</title>' +
    hostedFontsLink(origin) +
    `<style>body { margin: 0; }\n${safeCss}</style></head><body>${html || ''}</body></html>`
  );
}

/** The sample proposal for the current (unsaved) brand. */
export function buildPreview(p: PreviewInput, origin: string): string {
  const tokens = sampleTokens(p);
  if (p.templateHtml.trim()) {
    return previewDocument(fillTokens(p.templateHtml, tokens), p.templateCss, origin);
  }
  return previewDocument(fillTokens(defaultTemplateHtml(p.tagline), tokens), defaultCss(p.colours, p.fonts), origin);
}
