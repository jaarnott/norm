import { describe, expect, it } from 'vitest';
import {
  addMonths,
  agreedCents,
  clockMinutes,
  endsNextDay,
  endsOnForCount,
  equalShares,
  groupByDate,
  headerSummary,
  isoWeekday,
  monthBounds,
  bankAccountProblem,
  hasRole,
  toggleRole,
  bankChecksumOk,
  normaliseBankAccount,
  nzBankAccountValid,
  normaliseCode,
  percentToBp,
  performerChips,
  recurrenceDates,
  registerRows,
  sharesComplete,
  splitCents,
  validIrdNumber,
  weekStart,
} from './logic';
import type { EntMeta, Performance, Performer } from './types';
import { entTabsAllowed } from './workspace';

function performer(over: Partial<Performer> = {}): Performer {
  return {
    id: 'p1',
    name: 'Sam',
    legal_name: null,
    trading_name: null,
    email: 'sam@example.com',
    phone: null,
    notes: null,
    is_active: true,
    lane: 'payroll_contractor',
    performer_type: 'solo',
    band_mode: null,
    onboarding_status: 'accepted',
    submitted_at: null,
    accepted_at: null,
    accepted_by: null,
    bank_changed_at: null,
    needs_review: false,
    review_reason: null,
    gst_registered: false,
    gst_number: null,
    company_name: null,
    company_number: null,
    exemption_certificate_number: null,
    exemption_expiry_date: null,
    exemption_expiring: false,
    exemption_expired: false,
    has_declaration: true,
    declaration_method: 'form',
    declaration_signed_at: null,
    band_leader_declaration_at: null,
    contractor_confirmations: null,
    invite_sent_at: null,
    invite_reminders_sent: 0,
    last_reminder_at: null,
    payable: true,
    payable_reason: null,
    version: 1,
    ...over,
  };
}

describe('IRD number check digit (Inland Revenue rule)', () => {
  it('accepts numbers whose check digit works out, including the second-weighting case', () => {
    expect(validIrdNumber('49091850')).toBe(true); // remainder 0 → check digit 0
    expect(validIrdNumber('35901981')).toBe(true); // first weighting
    expect(validIrdNumber('136-410-132')).toBe(true); // first pass gives 10, second weighting decides
    expect(validIrdNumber('49 091 850')).toBe(true); // spaces ignored
  });

  it('refuses a wrong check digit, a short number and one outside the range', () => {
    expect(validIrdNumber('49091851')).toBe(false);
    expect(validIrdNumber('9125568')).toBe(false); // 7 digits
    expect(validIrdNumber('150000001')).toBe(false); // above 150,000,000
    expect(validIrdNumber('')).toBe(false);
    expect(validIrdNumber('abc')).toBe(false);
  });
});

describe('closing-manager roles', () => {
  it('adds and removes a role once, whatever its case or spacing', () => {
    expect(toggleRole(['Sales Manager'], 'SALES MANAGER', true)).toEqual(['SALES MANAGER']);
    expect(toggleRole(['Sales Manager'], ' Duty   Manager ', true)).toEqual(['Sales Manager', 'Duty Manager']);
    expect(toggleRole(['Sales Manager', 'Duty Manager'], 'sales manager', false)).toEqual(['Duty Manager']);
    expect(toggleRole(['Sales Manager'], '   ', true)).toEqual(['Sales Manager']);
    expect(hasRole(['SALES MANAGER'], 'Sales  Manager')).toBe(true);
    expect(hasRole(['SALES MANAGER'], 'Bar Team')).toBe(false);
  });
});

describe('bank account format', () => {
  it('normalises bank–branch–account–suffix however it was typed', () => {
    expect(normaliseBankAccount('12-3456-7890138-00')).toBe('12-3456-7890138-00');
    expect(normaliseBankAccount('123456789013800')).toBe('12-3456-7890138-00');
    expect(normaliseBankAccount('12 3456 7890138 000')).toBe('12-3456-7890138-000');
  });

  it('refuses the wrong number of digits or an all-zero part', () => {
    expect(normaliseBankAccount('12-3456-789')).toBeNull();
    expect(normaliseBankAccount('00-3456-7890123-00')).toBeNull();
    expect(normaliseBankAccount('12-3456-0000000-00')).toBeNull();
    expect(normaliseBankAccount('')).toBeNull();
    expect(bankAccountProblem('12-3456-789')).toBe('shape');
  });

  // Inland Revenue's worked examples (Payroll Calculations and Business Rules
  // Specification 2026–27, §7) — the same vectors as the server's test.
  it('checks the digits the way Inland Revenue does', () => {
    expect(normaliseBankAccount('01-902-0068389-00')).toBe('01-0902-0068389-00');
    expect(normaliseBankAccount('08-6523-1954512-001')).toBe('08-6523-1954512-001');
    expect(bankChecksumOk('A', '01', '902', '0068389', '00')).toBe(true);
    expect(bankChecksumOk('D', '08', '6523', '1954512', '001')).toBe(true);
    expect(bankChecksumOk('G', '26', '2600', '0320871', '032')).toBe(true);
    expect(bankChecksumOk('G', '26', '2600', '0320872', '032')).toBe(false);
    // Bank 26 has left IR's list: the algorithm passes, the number doesn't.
    expect(nzBankAccountValid('26', '2600', '0320871', '032')).toBe(false);
    // E and G add a product's digits (twice); these pass only with that step.
    expect(bankChecksumOk('G', '26', '2600', '0320869', '032')).toBe(true);
    expect(bankChecksumOk('E', '09', '0000', '0000018', '0001')).toBe(true);
    expect(nzBankAccountValid('12', '3456', '7890138', '00')).toBe(true); // B
    expect(nzBankAccountValid('12', '3456', '7890123', '00')).toBe(false);
    expect(nzBankAccountValid('25', '2500', '0000017', '00')).toBe(true); // F
    expect(nzBankAccountValid('25', '2500', '0000018', '00')).toBe(false);
    expect(nzBankAccountValid('31', '2825', '12345678', '0000')).toBe(true); // X
    expect(normaliseBankAccount('086523019545120001')).toBe('08-6523-1954512-001');
    expect(normaliseBankAccount('0865231954512001')).toBe('08-6523-1954512-001');
    for (const bad of ['01-902-0068388-00', '08-0001-1954512-001', '99-9999-1234567-00', '12-3456-7890123-00']) {
      expect(normaliseBankAccount(bad)).toBeNull();
      expect(bankAccountProblem(bad)).toBe('check');
    }
  });

  it('keeps only six digits of a code', () => {
    expect(normaliseCode('123 456')).toBe('123456');
    expect(normaliseCode('1234567')).toBe('123456');
  });
});

describe('booked hours and the agreed amount', () => {
  it('counts clock minutes, across midnight when the end is at or before the start', () => {
    expect(clockMinutes('20:00', '23:30', false)).toBe(210);
    expect(clockMinutes('20:00', '01:00', true)).toBe(300);
    expect(clockMinutes('20:00', '01:00', false)).toBe(300); // end before start implies next day
    expect(clockMinutes('x', '01:00', false)).toBeNull();
    expect(endsNextDay('20:00', '01:00')).toBe(true);
    expect(endsNextDay('09:00', '17:00')).toBe(false);
  });

  it('is rate × hours to the cent', () => {
    expect(agreedCents(4500, '20:00', '23:30', false)).toBe(15750);
    expect(agreedCents(10000, '20:00', '20:20', false)).toBe(3333);
    expect(agreedCents(4500, '', '23:30', false)).toBeNull();
  });
});

describe('weekly recurrence', () => {
  it('lists the dates for the ticked weekdays until the end date or count', () => {
    // 23 Oct 2026 is a Friday.
    expect(isoWeekday('2026-10-23')).toBe(5);
    expect(recurrenceDates('2026-10-23', { weekdays: [5], count: 3 })).toEqual(['2026-10-23', '2026-10-30', '2026-11-06']);
    expect(recurrenceDates('2026-10-23', { weekdays: [5, 6], until: '2026-10-31' })).toEqual([
      '2026-10-23',
      '2026-10-24',
      '2026-10-30',
      '2026-10-31',
    ]);
    expect(endsOnForCount('2026-10-23', [5], 3)).toBe('2026-11-06');
  });

  it('is the booking alone with no weekday, and never runs past a year', () => {
    expect(recurrenceDates('2026-10-23', { weekdays: [] })).toEqual(['2026-10-23']);
    const dates = recurrenceDates('2026-10-23', { weekdays: [5] }, 200);
    expect(dates.length).toBeLessThanOrEqual(53);
    expect(dates[dates.length - 1] <= '2027-10-24').toBe(true);
  });
});

describe('band shares', () => {
  it('splits equally to 10,000 basis points, leftover to the first member', () => {
    expect(equalShares(3)).toEqual([3334, 3333, 3333]);
    expect(equalShares(4)).toEqual([2500, 2500, 2500, 2500]);
    expect(sharesComplete({ a: 3334, b: 3333, c: 3333 })).toBe(true);
    expect(sharesComplete({ a: 5000, b: 4000 })).toBe(false);
    expect(percentToBp('33.33')).toBe(3333);
    expect(percentToBp('101')).toBeNull();
  });

  it('splits an amount by share in whole cents, leftover cent to the first member', () => {
    expect(splitCents(101, [{ id: 'a', share_bp: 5000 }, { id: 'b', share_bp: 5000 }])).toEqual({ a: 51, b: 50 });
    expect(splitCents(10000, [{ id: 'a', share_bp: 3334 }, { id: 'b', share_bp: 3333 }, { id: 'c', share_bp: 3333 }])).toEqual({ a: 3334, b: 3333, c: 3333 });
  });
});

describe('calendar helpers never use the browser zone', () => {
  it('bounds a month and steps across years', () => {
    expect(monthBounds('2026-02')).toEqual(['2026-02-01', '2026-02-28']);
    expect(monthBounds('2028-02')).toEqual(['2028-02-01', '2028-02-29']);
    expect(addMonths('2026-12', 1)).toBe('2027-01');
    expect(addMonths('2026-01', -1)).toBe('2025-12');
  });

  it('finds the Monday a pay week starts on', () => {
    expect(weekStart('2026-10-23')).toBe('2026-10-19');
    expect(weekStart('2026-10-19')).toBe('2026-10-19');
    expect(weekStart('2026-10-25')).toBe('2026-10-19');
  });
});

describe('register chips and filters', () => {
  it('says what finance must act on', () => {
    expect(performerChips(performer({ onboarding_status: 'invited' })).map((c) => c.key)).toEqual(['invited']);
    expect(performerChips(performer({ onboarding_status: 'submitted', has_declaration: false })).map((c) => c.key)).toEqual(['submitted', 'declaration']);
    expect(performerChips(performer({ needs_review: true })).map((c) => c.key)).toEqual(['accepted', 'review']);
    expect(performerChips(performer({ lane: 'exemption', exemption_expired: true })).map((c) => c.key)).toEqual(['accepted', 'expired']);
    expect(performerChips(performer({ is_active: false })).map((c) => c.key)).toEqual(['inactive', 'accepted']);
  });

  it('filters by tab, lane, review flag, search and inactive', () => {
    const rows = [
      performer({ id: 'a', name: 'Alpha', onboarding_status: 'submitted' }),
      performer({ id: 'b', name: 'Bravo', lane: 'invoice', company_name: 'Bravo Ltd' }),
      performer({ id: 'c', name: 'Charlie', has_declaration: false, is_active: false }),
    ];
    expect(registerRows(rows, 'awaiting', '', 'all', false, false).map((p) => p.id)).toEqual(['a']);
    expect(registerRows(rows, 'all', 'ltd', 'all', false, false).map((p) => p.id)).toEqual(['b']);
    expect(registerRows(rows, 'all', '', 'invoice', false, false).map((p) => p.id)).toEqual(['b']);
    expect(registerRows(rows, 'declarations', '', 'all', false, false)).toEqual([]);
    expect(registerRows(rows, 'declarations', '', 'all', false, true).map((p) => p.id)).toEqual(['c']);
  });
});

describe('roster grouping', () => {
  it('groups by date and orders by start time', () => {
    const base = { venue_id: 'v', venue_name: 'V', performer_id: 'p', performer_name: 'P', status: 'scheduled' } as unknown as Performance;
    const rows = [
      { ...base, id: '1', performance_date: '2026-10-24', start_time: '21:00' },
      { ...base, id: '2', performance_date: '2026-10-23', start_time: '20:00' },
      { ...base, id: '3', performance_date: '2026-10-24', start_time: '18:00' },
    ] as Performance[];
    expect(groupByDate(rows).map((g) => [g.date, g.rows.map((r) => r.id)])).toEqual([
      ['2026-10-23', ['2']],
      ['2026-10-24', ['3', '1']],
    ]);
  });
});

describe('the Entertainers page header and tabs', () => {
  const zero: EntMeta['counts'] = { awaiting_approvals: 0, awaiting_performer: 0, awaiting_manager: 0, mismatch: 0, ready_for_payment: 0, needs_review: 0, missing_declarations: 0 };

  it('says what is waiting, in plain words', () => {
    expect(headerSummary(zero, true)).toBe('Nothing waiting');
    expect(headerSummary({ ...zero, awaiting_approvals: 1 }, false)).toBe('1 performance needs approval');
    expect(headerSummary({ ...zero, awaiting_approvals: 2, awaiting_manager: 1, mismatch: 1, ready_for_payment: 3, missing_declarations: 1 }, true))
      .toBe('3 performances need approval · 1 with times that differ · 3 ready to pay · 1 performer missing a declaration');
  });

  it('keeps payment and review counts from people who cannot pay', () => {
    expect(headerSummary({ ...zero, ready_for_payment: 4, needs_review: 2 }, false)).toBe('Nothing waiting');
    expect(headerSummary({ ...zero, needs_review: 2 }, true)).toBe('2 performers to review');
  });

  it('offers Payments only to someone with the payroll permission', () => {
    expect(entTabsAllowed({ can_pay: true })).toEqual(['roster', 'performers', 'payments', 'emails']);
    expect(entTabsAllowed({ can_pay: false })).toEqual(['roster', 'performers', 'emails']);
    expect(entTabsAllowed(null)).toEqual(['roster', 'performers', 'emails']);
  });
});
