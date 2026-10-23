import { describe, expect, it } from 'vitest';
import { activeSteps, codeGate, emptyForm, lanesFor, mapServerErrors, needsDeclaration, stepBody, submitBody, validateStep } from './joinState';
import type { JoinLane, JoinPage } from '../types';

const LANES: JoinLane[] = [
  { key: 'individual', label: 'Individual', lane: 'payroll_contractor', blurb: '' },
  { key: 'sole_trader', label: 'Sole trader', lane: 'payroll_contractor', blurb: '' },
  { key: 'company', label: 'Registered NZ company', lane: 'invoice', blurb: '' },
  { key: 'agency', label: 'Booking agency', lane: 'invoice', blurb: '' },
  { key: 'band_leader', label: 'Band leader', lane: 'invoice', blurb: '' },
  { key: 'partnership', label: 'Partnership', lane: 'invoice', blurb: '' },
  { key: 'exemption', label: 'Exemption certificate', lane: 'exemption', blurb: '' },
  { key: 'not_sure', label: 'Not sure', lane: null, blurb: '' },
];

function page(over: Partial<JoinPage> = {}): JoinPage {
  return {
    purpose: 'onboarding',
    state: 'open',
    organisation: 'Cook Brothers Hospitality',
    band_name: null,
    prefill: { name: 'Sam', email: 'sam@example.com', phone: null },
    steps: ['about', 'performing', 'paid', 'band', 'bank_tax', 'review'],
    code: { required_before: 'bank_tax', verified: false, sent_to_hint: 's***@example.com' },
    lanes: LANES,
    confirmations: [{ key: 'independent', label: 'I am an independent contractor.' }, { key: 'own_tax', label: 'I handle my own tax.' }],
    declaration: { wording_version: '2026-07-13-v1', paragraphs: ['I declare…'], understand: ['my tax code will be WT'], confirm_label: 'I confirm.' },
    expires_at: null,
    ...over,
  };
}

const ALL_TICKED = { independent: true, own_tax: true };

describe('onboarding steps and lanes', () => {
  it('offers a solo act the individual options and a band the band ones', () => {
    expect(lanesFor('solo', LANES).map((l) => l.key)).toEqual(['individual', 'sole_trader', 'company', 'exemption', 'not_sure']);
    expect(lanesFor('band', LANES).map((l) => l.key)).toEqual(['band_leader', 'partnership', 'company', 'agency', 'not_sure']);
    expect(lanesFor('', LANES)).toHaveLength(8);
  });

  it('skips the band step for a solo act, on a band member link, and the bank step for a split band', () => {
    const p = page();
    const form = emptyForm(p);
    expect(activeSteps(p, { ...form, performer_type: 'solo' })).toEqual(['about', 'performing', 'paid', 'bank_tax', 'review']);
    expect(activeSteps(p, { ...form, performer_type: 'band' })).toEqual(['about', 'performing', 'paid', 'band', 'bank_tax', 'review']);
    // The members give their own bank details on their own links.
    expect(activeSteps(p, { ...form, performer_type: 'band', band_mode: 'members_split' })).toEqual(['about', 'performing', 'paid', 'band', 'review']);
    const member = page({ purpose: 'band_member', band_name: 'The Band', steps: ['about', 'performing', 'paid', 'bank_tax', 'review'] });
    expect(activeSteps(member, { ...emptyForm(member), performer_type: 'band' })).toEqual(['about', 'performing', 'paid', 'bank_tax', 'review']);
  });

  it('gates the bank and tax step on the code until it is verified', () => {
    const p = page();
    expect(codeGate('bank_tax', p, false)).toBe(true);
    expect(codeGate('bank_tax', p, true)).toBe(false);
    expect(codeGate('paid', p, false)).toBe(false);
    expect(codeGate('code', page({ purpose: 'declaration', steps: ['code', 'declaration', 'review'], code: { required_before: 'declaration', verified: false, sent_to_hint: null } }), false)).toBe(true);
  });
});

describe('onboarding checks mirror the server', () => {
  const p = page();

  it('needs a name, a real email and a phone to start', () => {
    const form = { ...emptyForm(p), email: 'nope', phone: '' };
    expect(Object.keys(validateStep('about', form, p)).sort()).toEqual(['email', 'phone']);
  });

  it('needs the legal name and the lane-specific details, but not for "not sure"', () => {
    const form = { ...emptyForm(p), paid_as: 'company' as const, legal_name: '' };
    const errors = validateStep('paid', form, p);
    expect(errors.legal_name).toBeTruthy();
    expect(errors.company_name).toBeTruthy();
    const exempt = { ...emptyForm(p), paid_as: 'exemption' as const, legal_name: 'Sam Smith' };
    expect(Object.keys(validateStep('paid', exempt, p)).sort()).toEqual(['exemption_certificate_number', 'exemption_expiry_date']);
    expect(validateStep('paid', { ...emptyForm(p), paid_as: 'not_sure' as const }, p)).toEqual({});
  });

  it('checks the bank account, the IRD check digit, every confirmation and the declaration before the code is spent', () => {
    const form = { ...emptyForm(p), paid_as: 'individual' as const, legal_name: 'Sam Smith', bank_account: '12-3456', ird_number: '49091851', signature: '', nz_tax_resident: '' as const };
    const errors = validateStep('bank_tax', form, p);
    expect(errors.bank_account).toBeTruthy();
    expect(errors.ird_number).toBeTruthy();
    expect(errors.contractor_confirmations).toBeTruthy();
    expect(errors.nz_tax_resident).toBeTruthy();
    expect(errors.signature).toBeTruthy();
    expect(errors.declaration_confirmed).toBeTruthy();
    expect(errors.date_of_birth).toBeUndefined(); // optional, as on the server

    const ok = { ...form, bank_account: '12 3456 7890123 00', ird_number: '49091850', confirmations: ALL_TICKED, nz_tax_resident: 'yes' as const, signature: 'sam  smith', declaration_confirmed: true };
    expect(validateStep('bank_tax', ok, p)).toEqual({});
    expect(needsDeclaration('individual', LANES)).toBe(true);
    expect(needsDeclaration('company', LANES)).toBe(false);
  });

  it('the signature must be the legal name; the IRD number is optional off the withholding-tax lane', () => {
    const wt = { ...emptyForm(p), paid_as: 'sole_trader' as const, legal_name: 'Sam Smith', bank_account: '12-3456-7890123-00', ird_number: '', confirmations: ALL_TICKED, nz_tax_resident: 'yes' as const, signature: 'S. Smith', declaration_confirmed: true };
    const errors = validateStep('bank_tax', wt, p);
    expect(errors.signature).toMatch(/legal name/);
    expect(errors.ird_number).toMatch(/needed/);
    const company = { ...emptyForm(p), paid_as: 'company' as const, legal_name: 'Sam Smith', company_name: 'Smith Ltd', bank_account: '12-3456-7890123-00', confirmations: ALL_TICKED };
    expect(validateStep('bank_tax', company, p)).toEqual({});
    expect(validateStep('bank_tax', { ...company, ird_number: '123' }, p).ird_number).toBeTruthy();
  });

  it('stops a non-resident and an out-of-range self-selected rate', () => {
    const form = { ...emptyForm(p), paid_as: 'sole_trader' as const, legal_name: 'Sam', bank_account: '12-3456-7890123-00', ird_number: '49091850', confirmations: ALL_TICKED, nz_tax_resident: 'no' as const, wt_rate_type: 'self_selected' as const, wt_rate_percent: '5', signature: 'Sam', declaration_confirmed: true };
    const errors = validateStep('bank_tax', form, p);
    expect(errors.nz_tax_resident).toMatch(/non-residents/);
    expect(errors.wt_rate_percent).toBeTruthy();
  });

  it('needs two named members with emails for a split band', () => {
    const form = { ...emptyForm(p), performer_type: 'band' as const, band_mode: 'members_split' as const };
    expect(validateStep('band', form, p).members).toBeTruthy();
    const two = { ...form, members: [{ name: 'A', email: 'a@x.io', phone: '' }, { name: 'B', email: 'b@x.io', phone: '' }] };
    expect(validateStep('band', two, p)).toEqual({});
  });

  it('maps the server’s nested keys onto the form’s', () => {
    expect(mapServerErrors({ 'declaration.signature': 'x', 'declaration.confirmed': 'y', 'members.1': 'z', exemption_certificate_file_id: 'w' })).toEqual({
      signature: 'x',
      declaration_confirmed: 'y',
      members: 'z',
      exemption_certificate: 'w',
    });
  });
});

describe('what is sent', () => {
  const p = page();

  it('normalises the bank account and IRD number and sends the lane answer', () => {
    const form = { ...emptyForm(p), performer_type: 'solo' as const, paid_as: 'sole_trader' as const, legal_name: 'Sam Smith', gst_registered: true, gst_number: '123-456-789', bank_account: '123456789012300', ird_number: '49-091-850', date_of_birth: '1990-01-02', nz_tax_resident: 'yes' as const, signature: 'Sam Smith', declaration_confirmed: true, confirmations: ALL_TICKED, documents: { exemption_certificate: 'f1' } };
    const body = submitBody(form, p);
    expect(body.bank_account).toBe('12-3456-7890123-00');
    expect(body.ird_number).toBe('49091850');
    expect(body.paid_as).toBe('sole_trader');
    expect(body.gst_registered).toBe(true);
    expect((body.declaration as { wt_rate_percent: number }).wt_rate_percent).toBe(20);
    expect(body.exemption_certificate_file_id).toBe('f1');
    expect(body.documents).toBeUndefined();
    expect(body.band_mode).toBeUndefined();
    expect(stepBody('review', form)).toBeNull();
    // The bank step is checked against the lane, which the server reads from
    // the same body — so it carries the payment answer again.
    const bank = stepBody('bank_tax', form, p)!;
    expect(bank.paid_as).toBe('sole_trader');
    expect(bank.performer_type).toBe('solo');
    expect(bank.bank_account).toBe('12-3456-7890123-00');
    expect((bank.declaration as { signature: string }).signature).toBe('Sam Smith');
    expect(stepBody('bank_tax', { ...form, paid_as: 'company' }, p)!.declaration).toBeUndefined();
  });

  it('a company sends no declaration and no IRD number', () => {
    const form = { ...emptyForm(p), performer_type: 'solo' as const, paid_as: 'company' as const, legal_name: 'Sam Smith', company_name: 'Smith Ltd', bank_account: '12-3456-7890123-00' };
    const body = submitBody(form, p);
    expect(body.declaration).toBeUndefined();
    expect(body.ird_number).toBeUndefined();
    expect(body.company_name).toBe('Smith Ltd');
  });

  it('a split band sends its members and no bank details', () => {
    const form = { ...emptyForm(p), performer_type: 'band' as const, paid_as: 'not_sure' as const, band_mode: 'members_split' as const, members: [{ name: 'A', email: 'a@x.io', phone: '' }, { name: 'B', email: 'b@x.io', phone: '' }] };
    const body = submitBody(form, p);
    expect(body.band_mode).toBe('members_split');
    expect((body.members as unknown[]).length).toBe(2);
    expect(body.bank_account).toBeUndefined();
  });

  it('a declaration-only link sends the legal name, the IRD number and the declaration', () => {
    const d = page({ purpose: 'declaration', steps: ['code', 'declaration', 'review'], code: { required_before: 'declaration', verified: false, sent_to_hint: null } });
    const form = { ...emptyForm(d), legal_name: 'Sam Smith', ird_number: '49091850', nz_tax_resident: 'yes' as const, signature: 'Sam Smith', declaration_confirmed: true };
    const body = submitBody(form, d);
    expect(Object.keys(body).sort()).toEqual(['declaration', 'ird_number', 'legal_name']);
  });
});
