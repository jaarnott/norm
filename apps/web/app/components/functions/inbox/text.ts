/**
 * Norm Functions Inbox — pure text helpers (no DOM, no React, so vitest can
 * pin them): Gmail snippet entities, sender and date labels, address lists,
 * reply-template blanks, the `mail_thread` deep-link value and mailbox
 * health labels.
 */
import type { BadgeTone } from '../../ui/Badge';
import type { Folder, MailboxHealth, Message, TemplateStage } from './types';

export const FOLDER_LABELS: Record<Folder, string> = {
  enquiry: 'Enquiry',
  reservation: 'Reservation',
  other: 'Other',
};

const NAMED_ENTITIES: Record<string, string> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
  hellip: '…',
  mdash: '—',
  ndash: '–',
  lsquo: '‘',
  rsquo: '’',
  ldquo: '“',
  rdquo: '”',
  copy: '©',
  reg: '®',
};

/** Gmail snippets arrive HTML-escaped ("it&#39;s", "&lt;functions@…"):
 *  decode them for display as plain text. Unknown entities stay as written. */
export function decodeEntities(value: string | null | undefined): string {
  if (!value) return '';
  return value.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, entity: string) => {
    if (entity[0] === '#') {
      const hex = entity[1] === 'x' || entity[1] === 'X';
      const code = hex ? parseInt(entity.slice(2), 16) : parseInt(entity.slice(1), 10);
      return Number.isFinite(code) && code > 0 && code < 0x110000 ? String.fromCodePoint(code) : whole;
    }
    return NAMED_ENTITIES[entity.toLowerCase()] ?? whole;
  });
}

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** The visible text of an HTML fragment: tags dropped, entities decoded. */
export function htmlToText(html: string | null | undefined): string {
  if (!html) return '';
  const text = html
    .replace(/<(script|style)\b[\s\S]*?<\/\1>/gi, ' ')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(p|div|li|h[1-6]|tr|blockquote)>/gi, '\n')
    .replace(/<[^>]*>/g, '');
  return decodeEntities(text)
    .replace(/[ \t ]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/** Nothing a person wrote: no text and no image. */
export function isBlankHtml(html: string | null | undefined): boolean {
  if (!html) return true;
  if (/<img\b/i.test(html)) return false;
  return htmlToText(html) === '';
}

export function senderLabel(name?: string | null, email?: string | null): string {
  return (name && name.trim()) || (email && email.trim()) || 'Unknown sender';
}

/** "Francesca Alex Guadalupe" → "Francesca"; an address or blank → "". */
export function firstName(name?: string | null): string {
  const n = (name || '').trim();
  if (!n || n.includes('@')) return '';
  return n.split(/\s+/)[0];
}

// Newer ICU puts a narrow no-break space before "am/pm"; keep labels plain.
const tidy = (s: string) => s.replace(/[  ]/g, ' ').replace(',', '');

/** The calendar day of an instant in a zone, as "YYYY-MM-DD". */
function dayIn(d: Date, timeZone: string): string {
  return d.toLocaleDateString('en-CA', { timeZone });
}

/** A list date the way Gmail shows it, in the venue's (or company) zone: a
 *  time for today, "12 Oct" this year, "12 Oct 2025" before. */
export function mailDateLabel(iso: string | null | undefined, timeZone: string, now: Date): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const day = dayIn(d, timeZone);
  const today = dayIn(now, timeZone);
  if (day === today) {
    return tidy(d.toLocaleTimeString('en-NZ', { timeZone, hour: 'numeric', minute: '2-digit' }));
  }
  return tidy(
    d.toLocaleDateString('en-NZ', {
      timeZone,
      day: 'numeric',
      month: 'short',
      year: day.slice(0, 4) === today.slice(0, 4) ? undefined : 'numeric',
    }),
  );
}

/** A message header's date: "Mon 12 Oct, 4:42 pm" (+ year when not this year). */
export function messageDateLabel(iso: string | null | undefined, timeZone: string, now: Date): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const sameYear = dayIn(d, timeZone).slice(0, 4) === dayIn(now, timeZone).slice(0, 4);
  const date = tidy(
    d.toLocaleDateString('en-NZ', {
      timeZone,
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      year: sameYear ? undefined : 'numeric',
    }),
  );
  const time = tidy(d.toLocaleTimeString('en-NZ', { timeZone, hour: 'numeric', minute: '2-digit' }));
  return `${date}, ${time}`;
}

/** "just now" / "4 min ago" / "3 hours ago" / "yesterday" / "5 days ago". */
export function agoLabel(iso: string | null | undefined, now: Date): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const mins = Math.floor(Math.max(0, now.getTime() - then) / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return hours === 1 ? '1 hour ago' : `${hours} hours ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? 'yesterday' : `${days} days ago`;
}

export function fileSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** The file name from a Content-Disposition header: RFC 5987 `filename*`
 *  (UTF-8, percent-encoded) first, else `filename="…"`, else the fallback.
 *  Path parts are dropped. */
export function filenameFromDisposition(header: string | null | undefined, fallback: string): string {
  const h = header || '';
  let name = '';
  const star = /filename\*\s*=\s*(?:UTF-8|utf-8)?'[^']*'([^;]+)/.exec(h);
  if (star) {
    try {
      name = decodeURIComponent(star[1].trim().replace(/^"|"$/g, ''));
    } catch {
      name = '';
    }
  }
  if (!name) {
    const plain = /filename\s*=\s*("([^"]*)"|[^;]+)/.exec(h);
    if (plain) name = (plain[2] ?? plain[1]).trim();
  }
  name = name.split(/[\\/]/).pop()?.trim() || '';
  return name || fallback || 'attachment';
}

// ── Addresses ──────────────────────────────────────────────────────────

/** The address inside "Casey <casey@example.com>", or the entry itself. */
export function addressOf(entry: string): string {
  const m = /<([^>]+)>/.exec(entry);
  return (m ? m[1] : entry).trim();
}

/** What a person typed in a To/Cc box → entries (commas, semicolons or new
 *  lines between them), de-duplicated by address. The server parses again. */
export function parseAddresses(text: string | null | undefined): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of (text || '').split(/[,;\n]+/)) {
    const entry = part.trim();
    if (!entry) continue;
    const key = addressOf(entry).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(entry);
  }
  return out;
}

const EMAIL = /^[^\s@<>()",;]+@[^\s@<>()",;]+\.[^\s@<>()",;]+$/;

/** Entries that aren't an email address (shown before the server refuses them). */
export function invalidAddresses(entries: string[]): string[] {
  return entries.filter((e) => !EMAIL.test(addressOf(e)));
}

// ── Replies ────────────────────────────────────────────────────────────

export function replySubject(subject: string | null | undefined): string {
  const s = (subject || '').trim();
  if (!s) return '';
  return /^re:/i.test(s) ? s : `Re: ${s}`;
}

type MessageFacts = Pick<Message, 'is_inbound' | 'is_internal' | 'is_auto_reply' | 'from_email' | 'from_name'>;

/** The newest customer message: inbound, not internal or an auto-reply, and
 *  not from one of the venue's own addresses (a web form's notification comes
 *  from the venue itself, with the customer only in Reply-To — then nobody). */
export function latestCustomer<T extends MessageFacts>(messages: T[], ownAddresses: string[]): T | null {
  const own = new Set(ownAddresses.map((a) => a.toLowerCase()));
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m.is_inbound || m.is_internal || m.is_auto_reply || !m.from_email) continue;
    if (own.has(m.from_email.toLowerCase())) continue;
    return m;
  }
  return null;
}

/** first_reply until the venue has written on the thread, then follow_up. */
export function templateStageFor(messages: Pick<Message, 'is_inbound' | 'is_internal'>[]): TemplateStage {
  return messages.some((m) => m.is_inbound === false && !m.is_internal) ? 'follow_up' : 'first_reply';
}

const TOKEN = /\{\{\s*([a-z_]+)\s*\}\}/gi;

/** Fill a reply template's placeholders ({{client_name}}, {{sender_name}} …)
 *  with escaped values. Unknown or empty ones stay as written, so the person
 *  sees them (and `unfilledBlanks` flags them) before sending. */
export function fillTemplate(html: string, values: Record<string, string | null | undefined>): string {
  return html.replace(TOKEN, (whole, name: string) => {
    const v = values[name.toLowerCase()];
    return v ? escapeHtml(v) : whole;
  });
}

/** Blanks still in what is about to be sent: `{{tokens}}` other than the ones
 *  the server fills (`serverTokens`, e.g. quote_link) and bracketed template
 *  blanks such as "[DATE]" or "[Customer Name]". */
export function unfilledBlanks(html: string | null | undefined, serverTokens: string[] = []): string[] {
  const text = htmlToText(html);
  const found = new Set<string>();
  for (const m of text.matchAll(TOKEN)) {
    if (!serverTokens.includes(m[1].toLowerCase())) found.add(m[0]);
  }
  for (const m of text.matchAll(/\[[A-Z][A-Za-z0-9 ,/&'-]{0,48}\]/g)) found.add(m[0]);
  return [...found];
}

/** A link typed into the editor's link box → an href the sanitiser keeps
 *  (http(s), mailto, tel); a bare address gets mailto:, anything else https://.
 *  Blank or a script URL → null. */
export function normaliseUrl(input: string | null | undefined): string | null {
  const v = (input || '').trim();
  if (!v || /\s/.test(v)) return null;
  if (/^(https?:\/\/|mailto:|tel:)/i.test(v)) return v;
  if (/^[a-z][a-z0-9+.-]*:/i.test(v) && !/^[^:]+:\d/.test(v)) return null; // javascript:, data:, …
  if (EMAIL.test(v)) return `mailto:${v}`;
  return `https://${v}`;
}

// ── Deep links ─────────────────────────────────────────────────────────

/** `?mail_thread=<account_id>:<gmail_thread_id>` (never `thread=` — that is chat). */
export function mailThreadParam(accountId: string, threadId: string): string {
  return `${accountId}:${threadId}`;
}

export function parseMailThreadParam(value: string | null | undefined): { accountId: string; threadId: string } | null {
  if (!value) return null;
  const at = value.indexOf(':');
  if (at <= 0 || at === value.length - 1) return null;
  return { accountId: value.slice(0, at), threadId: value.slice(at + 1) };
}

// ── Mailbox health ─────────────────────────────────────────────────────

export function healthBadge(health: MailboxHealth): { tone: BadgeTone; label: string } {
  switch (health.state) {
    case 'connected':
      return health.lagging ? { tone: 'warn', label: 'Sync delayed' } : { tone: 'ok', label: 'Connected' };
    case 'syncing':
      return { tone: 'info', label: 'Syncing' };
    case 'needs_reconnect':
      return { tone: 'error', label: 'Reconnect needed' };
    case 'not_connected':
      return { tone: 'warn', label: 'Not connected' };
    default:
      return { tone: 'neutral', label: 'Off' };
  }
}

/** Can Norm read live bodies and send from this mailbox right now? */
export function mailboxReady(health: MailboxHealth | null | undefined): boolean {
  return !!health && health.connected && (health.state === 'connected' || health.state === 'syncing');
}
