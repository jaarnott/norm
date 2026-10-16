import { describe, expect, it } from 'vitest';
import {
  agoLabel,
  decodeEntities,
  fillTemplate,
  filenameFromDisposition,
  firstName,
  healthBadge,
  htmlToText,
  invalidAddresses,
  isBlankHtml,
  latestCustomer,
  mailDateLabel,
  mailThreadParam,
  messageDateLabel,
  normaliseUrl,
  parseAddresses,
  parseMailThreadParam,
  replySubject,
  templateStageFor,
  unfilledBlanks,
} from './text';
import type { MailboxHealth } from './types';

const NZ = 'Pacific/Auckland';

describe('inbox text helpers', () => {
  it('decodes the entities Gmail leaves in snippets', () => {
    expect(decodeEntities('it&#39;ll be &lt;functions@x&gt; &amp; co&hellip;')).toBe("it'll be <functions@x> & co…");
    expect(decodeEntities('&#x2014; &unknown; &nbsp;')).toBe('— &unknown;  ');
    expect(decodeEntities(null)).toBe('');
  });

  it('reads the visible text of HTML and spots an empty body', () => {
    expect(htmlToText('<p>Hi&nbsp;<b>Casey</b></p><p>See you</p>')).toBe('Hi Casey\nSee you');
    expect(htmlToText('<style>p{}</style><div>x</div>')).toBe('x');
    expect(isBlankHtml('<p><br></p>')).toBe(true);
    expect(isBlankHtml('<p><img src="/api/public/files/a"></p>')).toBe(false);
    expect(isBlankHtml('<p>ok</p>')).toBe(false);
  });

  it('labels list dates in the venue zone, Gmail style', () => {
    const now = new Date('2026-10-12T03:00:00Z'); // 4 pm Mon 12 Oct in Auckland
    expect(mailDateLabel('2026-10-11T22:05:00Z', NZ, now)).toBe('11:05 am'); // same NZ day
    expect(mailDateLabel('2026-10-11T09:00:00Z', NZ, now)).toBe('11 Oct'); // Sunday evening NZ
    expect(mailDateLabel('2025-12-24T01:00:00Z', NZ, now)).toBe('24 Dec 2025');
    expect(mailDateLabel(null, NZ, now)).toBe('');
    expect(messageDateLabel('2026-10-12T03:42:00Z', NZ, now)).toBe('Mon 12 Oct, 4:42 pm');
  });

  it('says how long ago a sync was', () => {
    const now = new Date('2026-10-12T03:00:00Z');
    expect(agoLabel('2026-10-12T02:59:40Z', now)).toBe('just now');
    expect(agoLabel('2026-10-12T02:56:00Z', now)).toBe('4 min ago');
    expect(agoLabel('2026-10-12T00:00:00Z', now)).toBe('3 hours ago');
    expect(agoLabel('2026-10-11T02:00:00Z', now)).toBe('yesterday');
    expect(agoLabel(null, now)).toBe('');
  });

  it('parses typed address lists and flags the bad ones', () => {
    const list = parseAddresses('a@b.co, Casey <c@d.com>; A@B.co\n e@f');
    expect(list).toEqual(['a@b.co', 'Casey <c@d.com>', 'e@f']);
    expect(invalidAddresses(list)).toEqual(['e@f']);
    expect(parseAddresses('  ')).toEqual([]);
  });

  it('builds reply subjects and first names', () => {
    expect(replySubject('Event hire')).toBe('Re: Event hire');
    expect(replySubject('RE: Event hire')).toBe('RE: Event hire');
    expect(replySubject(null)).toBe('');
    expect(firstName('Francesca Alex Guadalupe')).toBe('Francesca');
    expect(firstName('casey@example.com')).toBe('');
  });

  it('finds the customer, skipping the venue’s own form notifications', () => {
    const own = ['hello@venue.co.nz'];
    const msgs = [
      { is_inbound: true, is_internal: false, is_auto_reply: false, from_email: 'casey@example.com', from_name: 'Casey' },
      { is_inbound: false, is_internal: false, is_auto_reply: false, from_email: 'hello@venue.co.nz', from_name: 'Venue' },
      { is_inbound: true, is_internal: false, is_auto_reply: true, from_email: 'bot@example.com', from_name: 'Bot' },
      { is_inbound: true, is_internal: false, is_auto_reply: false, from_email: 'HELLO@venue.co.nz', from_name: 'Form' },
    ];
    expect(latestCustomer(msgs, own)?.from_email).toBe('casey@example.com');
    expect(latestCustomer(msgs.slice(3), own)).toBeNull();
  });

  it('picks the template stage from whether the venue has replied', () => {
    expect(templateStageFor([{ is_inbound: true, is_internal: false }])).toBe('first_reply');
    expect(templateStageFor([{ is_inbound: false, is_internal: true }])).toBe('first_reply');
    expect(templateStageFor([{ is_inbound: true, is_internal: false }, { is_inbound: false, is_internal: false }])).toBe(
      'follow_up',
    );
  });

  it('fills template placeholders, escaped, and flags the blanks left', () => {
    const html = '<p>Hi {{client_name}}, booked for [DATE] at [TIME].</p><p>{{ sender_name }} {{quote_link}}</p>';
    const filled = fillTemplate(html, { client_name: 'Tom & Jo', sender_name: '' });
    expect(filled).toContain('Hi Tom &amp; Jo,');
    expect(filled).toContain('{{ sender_name }}'); // empty value: left for the person to see
    expect(unfilledBlanks(filled, ['quote_link'])).toEqual(['{{ sender_name }}', '[DATE]', '[TIME]']);
    expect(unfilledBlanks('<p>All good [1]</p>')).toEqual([]);
  });

  it('normalises typed links and refuses script URLs', () => {
    expect(normaliseUrl('bettercallnorm.com/menu')).toBe('https://bettercallnorm.com/menu');
    expect(normaliseUrl('https://x.co')).toBe('https://x.co');
    expect(normaliseUrl('casey@example.com')).toBe('mailto:casey@example.com');
    expect(normaliseUrl('example.com:8080/a')).toBe('https://example.com:8080/a');
    expect(normaliseUrl('javascript:alert(1)')).toBeNull();
    expect(normaliseUrl('data:text/html,x')).toBeNull();
    expect(normaliseUrl('  ')).toBeNull();
  });

  it('takes the download name from Content-Disposition', () => {
    expect(filenameFromDisposition(`attachment; filename="menu.pdf"; filename*=UTF-8''Men%C3%BC%20Kids.pdf`, 'x')).toBe('Menü Kids.pdf');
    expect(filenameFromDisposition('attachment; filename="Run sheet.docx"', 'x')).toBe('Run sheet.docx');
    expect(filenameFromDisposition('attachment; filename=plan.png', 'x')).toBe('plan.png');
    expect(filenameFromDisposition('attachment; filename="../../etc/passwd"', 'x')).toBe('passwd');
    expect(filenameFromDisposition(null, 'Floor plan.jpg')).toBe('Floor plan.jpg');
    expect(filenameFromDisposition('attachment', '')).toBe('attachment');
  });

  it('round-trips the mail_thread deep link', () => {
    const v = mailThreadParam('8c0d62d4-162a-5800-976d-006faafb09db', '1a1110924b84c5b0');
    expect(parseMailThreadParam(v)).toEqual({ accountId: '8c0d62d4-162a-5800-976d-006faafb09db', threadId: '1a1110924b84c5b0' });
    expect(parseMailThreadParam('nocolon')).toBeNull();
    expect(parseMailThreadParam(':x')).toBeNull();
    expect(parseMailThreadParam('x:')).toBeNull();
  });

  it('labels mailbox health', () => {
    const base: MailboxHealth = {
      state: 'connected',
      connected: true,
      last_ok_at: null,
      last_error: null,
      consecutive_failures: 0,
      lag_seconds: 5,
      lagging: false,
    };
    expect(healthBadge(base)).toEqual({ tone: 'ok', label: 'Connected' });
    expect(healthBadge({ ...base, lagging: true }).tone).toBe('warn');
    expect(healthBadge({ ...base, state: 'not_connected', connected: false }).label).toBe('Not connected');
    expect(healthBadge({ ...base, state: 'needs_reconnect', connected: false }).tone).toBe('error');
  });
});
