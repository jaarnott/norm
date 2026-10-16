import { describe, expect, it } from 'vitest';
import { optionMoney, optionStatus, optionWhen, optionWhere, quoteBanner } from './quoteModel';
import type { QuoteOption, QuotePayload } from './types';

const option = (over: Partial<QuoteOption> = {}): QuoteOption => ({
  id: 'o1',
  label: 'The Deck',
  venue_name: 'Brew Bar',
  area_name: 'Deck',
  event_date: '2026-12-04',
  start_time: '19:00:00',
  end_time: '23:30:00',
  ends_next_day: false,
  amount_cents: 900000,
  food_cents: 0,
  total_cents: 900000,
  amount_text: '$9,000',
  total_text: '$9,000',
  state: 'available',
  changed_since_sent: false,
  can_confirm: true,
  can_decline: true,
  ...over,
});

const quote = (over: Partial<QuotePayload> = {}): QuotePayload => ({
  enquiry: { subject: "Casey's 40th", guests: 60, booking_type: 'large_event_exclusive', booking_label: 'Large event (exclusive)', contact_first_name: 'Casey', open: true },
  venue: { id: 'v1', name: 'Brew Bar', brand: null },
  proposal: { version_number: 1, sent_at: '2026-10-11T09:00:00+00:00', valid_until: '2026-10-25', document: '<!doctype html>' },
  options: [option()],
  booked: false,
  money: { currency: 'NZD', tax_label: 'incl. GST' },
  ...over,
});

describe('optionStatus', () => {
  it('shows actions, not a badge, for an option that can be confirmed', () => {
    expect(optionStatus(option(), quote())).toEqual({ badge: null, note: null });
  });

  it('thanks on a fresh confirmation and says "already confirmed" on a revisit', () => {
    const confirmed = option({ state: 'confirmed', can_confirm: false, can_decline: false });
    expect(optionStatus(confirmed, quote({ booked: true }), true).note).toMatch(/^Thank you/);
    expect(optionStatus(confirmed, quote({ booked: true })).note).toMatch(/^Already confirmed/);
    expect(optionStatus(confirmed, quote({ booked: true })).badge).toEqual({ tone: 'ok', text: 'Confirmed' });
  });

  it('explains an unavailable option in the customer\'s terms', () => {
    const q = quote();
    expect(optionStatus(option({ state: 'unavailable', can_confirm: false, changed_since_sent: true }), q).note).toMatch(/changed since your quote was sent/);
    expect(optionStatus(option({ state: 'unavailable', can_confirm: false }), quote({ booked: true })).note).toBe('Another option on this quote has been confirmed.');
    expect(optionStatus(option({ state: 'unavailable', can_confirm: false }), quote({ enquiry: { ...q.enquiry, open: false } })).note).toMatch(/closed/);
    expect(optionStatus(option({ state: 'unavailable', can_confirm: false, event_date: null }), q).note).toMatch(/needs a date/);
    expect(optionStatus(option({ state: 'unavailable', can_confirm: false }), q).note).toMatch(/isn't ready to confirm yet/);
  });

  it('tells a closed-by-booking option apart from one the venue withdrew', () => {
    const closed = option({ state: 'withdrawn', can_confirm: false, can_decline: false });
    expect(optionStatus(closed, quote({ booked: true }))).toEqual({
      badge: { tone: 'neutral', text: 'Not chosen' },
      note: 'Another option on this quote has been confirmed.',
    });
    expect(optionStatus(closed, quote()).note).toBe('The venue has withdrawn this option.');
  });

  it('lets a declined option be confirmed after all', () => {
    expect(optionStatus(option({ state: 'declined' }), quote()).note).toMatch(/Changed your mind/);
    expect(optionStatus(option({ state: 'declined', can_confirm: false }), quote()).note).toBeNull();
  });
});

describe('option facts', () => {
  it('shows venue-local plans without shifting them', () => {
    expect(optionWhen(option())).toBe('Fri 4 Dec 2026 · 7:00 pm – 11:30 pm');
    expect(optionWhen(option({ end_time: '01:00:00', ends_next_day: true }))).toBe('Fri 4 Dec 2026 · 7:00 pm – 1:00 am (next day)');
    expect(optionWhen(option({ event_date: null, start_time: null, end_time: null }))).toBe('Date to be confirmed');
  });

  it('names the place without repeating the option label', () => {
    expect(optionWhere(option())).toBe('Brew Bar · Deck');
    expect(optionWhere(option({ label: 'Deck' }))).toBe('Brew Bar');
  });

  it('lists the minimum spend, and food and total only when food changes it', () => {
    expect(optionMoney(option(), 'NZD')).toEqual([{ label: 'Minimum spend', value: '$9,000', strong: true }]);
    expect(optionMoney(option({ food_cents: 1050000, total_cents: 1050000, total_text: '$10,500' }), 'NZD')).toEqual([
      { label: 'Minimum spend', value: '$9,000', strong: true },
      { label: 'Food pre-order', value: '$10,500.00' },
      { label: 'Total', value: '$10,500', strong: true },
    ]);
  });
});

describe('quoteBanner', () => {
  it('celebrates a booking and notes a closed quote', () => {
    expect(quoteBanner(quote({ booked: true }))?.tone).toBe('ok');
    const q = quote();
    expect(quoteBanner(quote({ enquiry: { ...q.enquiry, open: false } }))?.tone).toBe('neutral');
  });

  it('warns once the quote is past its valid-until date, while it can still be confirmed', () => {
    expect(quoteBanner(quote(), '2026-10-26')?.tone).toBe('warn');
    expect(quoteBanner(quote(), '2026-10-25')).toBeNull();
  });
});
