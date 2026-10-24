import { describe, expect, it } from 'vitest';
import {
  aceStyleText, allowedTabs, blockerTarget, joinLeave, resolvePayrollVenue, settingsView, showsEntityPicker, stripTabs, venueForTab, weekFromParam,
} from './workspace';

describe('the Payroll tabs', () => {
  it('shows Trial run only while payroll is in its trial run', () => {
    expect(allowedTabs('shadow')).toContain('trial-run');
    expect(allowedTabs('live')).not.toContain('trial-run');
    expect(allowedTabs(null)).not.toContain('trial-run');
  });
  it('keeps Settings out of the strip but allowed behind the gear', () => {
    expect(allowedTabs('live')).toContain('settings');
    expect(stripTabs('shadow').map((t) => t.id)).toEqual(['hours', 'checks', 'leave', 'gratuities', 'entertainers', 'intercompany', 'reconciliation', 'ace-files', 'trial-run']);
    expect(stripTabs('shadow').find((t) => t.id === 'checks')?.label).toBe('Checks');
  });
  it('reads the settings section, Entities when unknown', () => {
    expect(settingsView('xero')).toBe('xero');
    expect(settingsView('nonsense')).toBe('entities');
    expect(settingsView(null)).toBe('entities');
  });
  it('shows the entity picker only where one entity matters', () => {
    expect(showsEntityPicker('hours')).toBe(true);
    expect(showsEntityPicker('trial-run')).toBe(false);
    expect(showsEntityPicker('settings', 'entities')).toBe(false);
    expect(showsEntityPicker('settings', 'xero')).toBe(true);
  });
});

describe('which entity a tab shows', () => {
  const ids = ['a', 'b'];
  it('lands Intercompany and Reconciliation on All entities when none is given', () => {
    expect(resolvePayrollVenue({ raw: null, tab: 'intercompany', entityIds: ids, fallbacks: ['b'] })).toEqual({ venueId: null, allVenues: true });
    expect(resolvePayrollVenue({ raw: 'all', tab: 'reconciliation', entityIds: ids })).toEqual({ venueId: null, allVenues: true });
    expect(resolvePayrollVenue({ raw: 'zz', tab: 'reconciliation', entityIds: ids })).toEqual({ venueId: null, allVenues: true });
    expect(resolvePayrollVenue({ raw: 'b', tab: 'reconciliation', entityIds: ids })).toEqual({ venueId: 'b', allVenues: false });
  });
  it('gives a single-entity tab handed All entities the last single entity', () => {
    expect(resolvePayrollVenue({ raw: 'all', tab: 'hours', entityIds: ids, fallbacks: [null, 'b'] })).toEqual({ venueId: 'b', allVenues: false });
    expect(resolvePayrollVenue({ raw: null, tab: 'leave', entityIds: ids, fallbacks: ['zz'] })).toEqual({ venueId: 'a', allVenues: false });
    expect(resolvePayrollVenue({ raw: null, tab: 'leave', entityIds: [] })).toEqual({ venueId: null, allVenues: false });
  });
  it('carries the chosen entity across a tab switch', () => {
    expect(venueForTab('intercompany', { venueId: 'b', allVenues: false })).toBe('b');
    expect(venueForTab('reconciliation', { venueId: null, allVenues: true })).toBe('all');
    expect(venueForTab('hours', { venueId: null, allVenues: true })).toBeUndefined();
  });
  it('reads a week param as its Monday, ignoring nonsense', () => {
    expect(weekFromParam('2026-10-14')).toBe('2026-10-12');
    expect(weekFromParam('2026-10-12')).toBe('2026-10-12');
    expect(weekFromParam('soon')).toBeNull();
    expect(weekFromParam('2026-13-45')).toBeNull();
    expect(weekFromParam(null)).toBeNull();
  });
});

describe('where a reconciliation blocker goes', () => {
  const pay = { canPay: true };
  const cannot = { canPay: false };
  it('sends a missing Xero mapping to settings, even when it names leave', () => {
    expect(blockerTarget('Xero mapping missing: leave_entitlements', pay)).toEqual({ page: 'payroll', tab: 'settings', params: { view: 'xero' } });
    expect(blockerTarget('Xero mapping missing: wages_expense', pay)).toEqual({ page: 'payroll', tab: 'settings', params: { view: 'xero' } });
  });
  it('sends undecided leave to the Leave tab', () => {
    expect(blockerTarget('2 leave requests in the week have no payout decided', pay)).toEqual({ page: 'payroll', tab: 'leave' });
  });
  it('sends a gratuity batch to Gratuities and a missing close snapshot to Hours', () => {
    expect(blockerTarget('the 2026-09 gratuity batch has no payable share yet', pay)).toEqual({ page: 'payroll', tab: 'gratuities' });
    expect(blockerTarget('no close snapshot for this week', pay)).toEqual({ page: 'payroll', tab: 'hours' });
    expect(blockerTarget('no close snapshot yet — figures are from the monday snapshot', pay)).toEqual({ page: 'payroll', tab: 'hours' });
  });
  it('sends unpaid performances to Entertainers → Payments only for people who can pay', () => {
    const text = '3 entertainer performances approved for payroll are not marked paid';
    expect(blockerTarget(text, pay)).toEqual({ page: 'entertainers', tab: 'payments' });
    expect(blockerTarget(text, cannot)).toEqual({ page: 'payroll', tab: 'entertainers' });
  });
  it('leaves blockers fixed on the Reconciliation tab without a link', () => {
    expect(blockerTarget('ACE inputs not entered for this week', pay)).toBeNull();
    expect(blockerTarget("adjustment 'Uniforms' has no GL code", pay)).toBeNull();
  });
});

describe('the merged Leave tab', () => {
  it('joins the snapshot to Loaded’s live list and keeps snapshot rows Loaded no longer shows', () => {
    const snap = [
      { leave_request_id: 'r1', staff_member_id: 'p1', ace_style: '70' },
      { leave_request_id: 'r2', staff_member_id: 'p2', ace_style: null },
    ];
    const { byKey, unmatched } = joinLeave(snap, [{ leave_request_id: 'r1', staff_member_id: 'p1' }]);
    expect(byKey.get('r1:p1')?.ace_style).toBe('70');
    expect(unmatched.map((r) => r.leave_request_id)).toEqual(['r2']);
  });
  it('says the Ace style, and flags a paid type without one', () => {
    expect(aceStyleText({ ace_style: '70', ace_style_from: 'override' })).toEqual({ text: '#70 (set in Norm)', error: false });
    expect(aceStyleText({ ace_style: null, unpaid: true })).toEqual({ text: 'Unpaid — left out', error: false });
    expect(aceStyleText({ ace_style: null })).toEqual({ text: 'No style', error: true });
    expect(aceStyleText({ ace_style: null }, 'Loaded timed out')).toEqual({ text: 'Unknown', error: true });
    expect(aceStyleText(null)).toBeNull();
  });
});
