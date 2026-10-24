import { describe, expect, it } from 'vitest';
import type { Fact, StaffRow } from '../types';
import { chipFrom, factTotals, legacyPayrunView, matchesSearch, remunerationLabel, shiftsOf, staffTotals, statusLabel, workedElsewhere, workedHere } from './logic';

function fact(over: Partial<Fact>): Fact {
  return {
    id: 'f', source_shift_id: 's', staff_member_id: 'p', staff_name: 'Smith, Jane', pay_code: 'JS', shift_type: 'Clockin',
    remuneration_type: 'hourly', role_id: null, role_name: 'Bar', cost_group: 'bar_floor', worked_venue_loaded_id: null,
    worked_venue_id: 'v1', worked_venue_name: 'La Zeppa', is_from_other_venue: false, clockin_at: '2026-10-13T05:00:00Z',
    clockout_at: '2026-10-13T11:00:00Z', trading_date: '2026-10-13', hours: 6, cost_cents: 15000, ph_hours: 0, ph_cost_cents: 0,
    leave_request_id: null, leave_type_name: null, is_approved: true, ...over,
  };
}

describe('the Hours tab’s view param', () => {
  it('picks the Security or Salaried chip, else Staff', () => {
    expect(chipFrom('security')).toBe('security');
    expect(chipFrom('salaried')).toBe('salaried');
    expect(chipFrom('timeclock')).toBe('staff');
    expect(chipFrom(null)).toBe('staff');
  });
});

describe('old Payrun links on the Payroll page', () => {
  it('lands Staff, Timeclock, Security and Salaried on Hours, keeping the chip or shifts view', () => {
    expect(legacyPayrunView('staff')).toEqual({ tab: 'hours' });
    expect(legacyPayrunView(null)).toEqual({ tab: 'hours' });
    expect(legacyPayrunView('timeclock')).toEqual({ tab: 'hours', view: 'timeclock' });
    expect(legacyPayrunView('security')).toEqual({ tab: 'hours', view: 'security' });
    expect(legacyPayrunView('salaried')).toEqual({ tab: 'hours', view: 'salaried' });
  });
  it('renames Hygiene to Checks and Exports to Ace files', () => {
    expect(legacyPayrunView('hygiene')).toEqual({ tab: 'checks' });
    expect(legacyPayrunView('exports')).toEqual({ tab: 'ace-files' });
  });
  it('keeps Leave, Gratuities and Entertainers, and sends anything unknown to Hours', () => {
    expect(legacyPayrunView('leave')).toEqual({ tab: 'leave' });
    expect(legacyPayrunView('gratuities')).toEqual({ tab: 'gratuities' });
    expect(legacyPayrunView('entertainers')).toEqual({ tab: 'entertainers' });
    expect(legacyPayrunView('nonsense')).toEqual({ tab: 'hours' });
  });
});

describe('the worked-at-this-venue view', () => {
  const here = fact({ id: 'a' });
  const elsewhere = fact({ id: 'b', is_from_other_venue: true, worked_venue_name: 'Glass Goose' });
  const leave = fact({ id: 'c', shift_type: 'Leave', leave_request_id: 'lr1', leave_type_name: 'NZ Annual' });
  it('keeps shifts here and drops leave and other venues', () => {
    expect(workedHere([here, elsewhere, leave]).map((f) => f.id)).toEqual(['a']);
    expect(workedElsewhere([here, elsewhere, leave]).map((f) => f.id)).toEqual(['b']);
  });
  it('orders a person’s shifts by clock-in', () => {
    const later = fact({ id: 'd', clockin_at: '2026-10-15T05:00:00Z' });
    expect(shiftsOf([later, here], 'p').map((f) => f.id)).toEqual(['a', 'd']);
  });
  it('adds the foot of the table', () => {
    expect(factTotals([here, elsewhere])).toEqual({ shifts: 2, hours: 12, cost_cents: 30000, ph_cost_cents: 0 });
  });
});

describe('staff totals and search', () => {
  const rows: StaffRow[] = [
    { staff_member_id: '1', staff_name: 'Smith, Jane', pay_code: 'JS', remuneration_type: 'hourly', shifts: 3, hours: '12.5', cost_cents: 40000, ph_hours: 0, ph_cost_cents: 0, other_venue_hours: '2', all_approved: true, shift_ids: [] },
    { staff_member_id: '2', staff_name: 'Brown, Al', pay_code: null, remuneration_type: 'salary', shifts: 5, hours: 40, cost_cents: 150000, ph_hours: 8, ph_cost_cents: 30000, other_venue_hours: 0, all_approved: false, shift_ids: [] },
  ];
  it('sums hours whether they arrive as strings or numbers', () => {
    expect(staffTotals(rows)).toEqual({ people: 2, shifts: 8, hours: 52.5, cost_cents: 190000, ph_hours: 8, ph_cost_cents: 30000, other_venue_hours: 2 });
  });
  it('matches a name or a pay code, any case', () => {
    expect(matchesSearch('jane', rows[0].staff_name, rows[0].pay_code)).toBe(true);
    expect(matchesSearch('js', rows[0].staff_name, rows[0].pay_code)).toBe(true);
    expect(matchesSearch('zoe', rows[0].staff_name, rows[0].pay_code)).toBe(false);
    expect(matchesSearch('  ', rows[1].staff_name, rows[1].pay_code)).toBe(true);
  });
  it('says Loaded’s remuneration type as people do', () => {
    expect(remunerationLabel('HourlyRate')).toBe('Hourly');
    expect(remunerationLabel('Salary')).toBe('Salary');
    expect(remunerationLabel(null)).toBe('—');
  });
  it('says a week’s status in plain words', () => {
    expect(statusLabel('close_ready')).toEqual({ label: 'Ready to close', tone: 'accent' });
    expect(statusLabel(undefined).label).toBe('Open');
  });
});
