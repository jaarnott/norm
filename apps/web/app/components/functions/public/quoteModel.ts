/**
 * Norm Functions — the quote page's option panel, as pure functions: what
 * each option says about itself (badge, explanation, when, where, money).
 *
 * The API decides what may be done (`can_confirm` / `can_decline`); this
 * only words it. An option the customer can't act on always says why, in
 * the customer's terms, never staff's ("not approved", "no date").
 */
import { dateLabel, money, timeRangeLabel } from '../shared/format';
import type { BadgeTone } from '../../ui/Badge';
import type { QuoteOption, QuotePayload } from './types';
import { localToday } from './brand';

export interface OptionStatus {
  badge: { tone: BadgeTone; text: string } | null;
  note: string | null;
}

/**
 * `justConfirmed` = this page's own confirm just succeeded; otherwise a
 * confirmed option was confirmed earlier (or in another tab) and says so.
 */
export function optionStatus(option: QuoteOption, quote: Pick<QuotePayload, 'booked' | 'enquiry'>, justConfirmed = false): OptionStatus {
  switch (option.state) {
    case 'confirmed':
      return {
        badge: { tone: 'ok', text: 'Confirmed' },
        note: justConfirmed
          ? "Thank you — it's confirmed. We've let the venue know and they'll be in touch to finalise the details."
          : 'Already confirmed — the venue has your confirmation and will be in touch.',
      };
    case 'declined':
      return {
        badge: { tone: 'neutral', text: 'Declined' },
        note: option.can_confirm ? 'Changed your mind? You can still confirm this option.' : null,
      };
    case 'withdrawn':
      // Confirming one option closes the others: say so, not "withdrawn".
      return quote.booked
        ? { badge: { tone: 'neutral', text: 'Not chosen' }, note: 'Another option on this quote has been confirmed.' }
        : { badge: { tone: 'neutral', text: 'No longer offered' }, note: 'The venue has withdrawn this option.' };
    case 'unavailable':
      return { badge: { tone: 'neutral', text: 'Not available to confirm' }, note: unavailableReason(option, quote) };
    default:
      return { badge: null, note: null };
  }
}

function unavailableReason(option: QuoteOption, quote: Pick<QuotePayload, 'booked' | 'enquiry'>): string {
  if (option.changed_since_sent) return 'This option has changed since your quote was sent — the venue will send you an updated one.';
  if (!quote.enquiry.open) return 'This quote is closed. Please contact the venue if you would like to go ahead.';
  if (quote.booked) return 'Another option on this quote has been confirmed.';
  if (!option.event_date) return 'This option needs a date before it can be confirmed — please contact the venue.';
  return "This option isn't ready to confirm yet — the venue will be in touch.";
}

/** "Fri 4 Dec 2026 · 7:00 pm – 11:30 pm" (venue-local, never time-zone shifted). */
export function optionWhen(option: QuoteOption): string {
  const day = option.event_date ? dateLabel(option.event_date) : 'Date to be confirmed';
  const times = timeRangeLabel(option.start_time, option.end_time, option.ends_next_day);
  return times ? `${day} · ${times}` : day;
}

/** "Brew Bar · The Deck", without repeating the option's own label. */
export function optionWhere(option: QuoteOption): string {
  const parts = [option.venue_name, option.area_name].filter((p): p is string => !!p && p !== option.label);
  return parts.filter((p, i) => parts.indexOf(p) === i).join(' · ');
}

export function optionTitle(option: QuoteOption, index: number): string {
  return option.label || option.area_name || option.venue_name || `Option ${index + 1}`;
}

/** The money lines: the minimum spend, then food and the total when food changes it. */
export function optionMoney(option: QuoteOption, currency: string): { label: string; value: string; strong?: boolean }[] {
  const lines: { label: string; value: string; strong?: boolean }[] = [];
  const amount = option.amount_text || money(option.amount_cents, currency, 0);
  if (option.amount_cents !== null && option.amount_cents > 0) lines.push({ label: 'Minimum spend', value: amount, strong: true });
  if (option.food_cents) lines.push({ label: 'Food pre-order', value: money(option.food_cents, currency) });
  if (option.total_cents !== null && option.total_cents !== option.amount_cents && option.total_cents > 0) {
    lines.push({ label: 'Total', value: option.total_text || money(option.total_cents, currency), strong: true });
  }
  return lines;
}

/** The banner over the options, if the quote as a whole has a state to say. */
export function quoteBanner(quote: QuotePayload, today = localToday()): { tone: 'ok' | 'warn' | 'neutral'; text: string } | null {
  if (quote.booked) {
    return { tone: 'ok', text: "Your booking is confirmed. The venue will be in touch to finalise the details." };
  }
  if (!quote.enquiry.open) {
    return { tone: 'neutral', text: 'This quote is closed. Please contact the venue if you would still like to go ahead.' };
  }
  const until = quote.proposal?.valid_until;
  if (until && until < today && quote.options.some((o) => o.can_confirm)) {
    return {
      tone: 'warn',
      text: `This quote was valid until ${dateLabel(until)}. You can still confirm, but the venue may need to check availability first.`,
    };
  }
  return null;
}
