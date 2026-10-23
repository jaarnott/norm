/**
 * The onboarding form's state and rules, kept pure so they can be tested:
 * which lanes a performer type may pick, which fields a lane needs, each
 * step's checks (mirrors of `services/entertainers/onboarding.py`'s — the
 * server decides), the body each step saves and the one `submit` sends.
 * Nothing here is persisted anywhere: the form lives in React state and
 * dies with the page.
 */
import { BANK_CHECK_MESSAGE, BANK_SHAPE_MESSAGE, bankAccountProblem, normaliseBankAccount, normaliseIrd, validEmail, validIrdNumber } from '../logic';
import type { JoinLane, JoinPage, JoinStep, Lane, PaidAs, PerformerType } from '../types';

export interface MemberDraft {
  name: string;
  email: string;
  phone: string;
}

export interface JoinForm {
  name: string;
  email: string;
  phone: string;
  performer_type: PerformerType | '';
  paid_as: PaidAs | '';
  legal_name: string;
  trading_name: string;
  gst_registered: boolean;
  gst_number: string;
  company_name: string;
  company_number: string;
  exemption_certificate_number: string;
  exemption_expiry_date: string;
  band_mode: 'leader' | 'members_split' | '';
  band_leader_declaration: boolean;
  members: MemberDraft[];
  bank_account: string;
  ird_number: string;
  date_of_birth: string;
  confirmations: Record<string, boolean>;
  nz_tax_resident: 'yes' | 'no' | '';
  wt_rate_type: 'standard' | 'self_selected' | 'tailored';
  wt_rate_percent: string;
  tailored_certificate_number: string;
  signature: string;
  declaration_confirmed: boolean;
  /** `file_id`s from `POST /documents`, attached at submit. */
  documents: Partial<Record<'exemption_certificate' | 'company_document', string>>;
}

export function emptyForm(page: JoinPage): JoinForm {
  return {
    name: page.prefill.name ?? '',
    email: page.prefill.email ?? '',
    phone: page.prefill.phone ?? '',
    performer_type: page.purpose === 'band_member' ? 'solo' : '',
    paid_as: '',
    legal_name: '',
    trading_name: '',
    gst_registered: false,
    gst_number: '',
    company_name: '',
    company_number: '',
    exemption_certificate_number: '',
    exemption_expiry_date: '',
    band_mode: '',
    band_leader_declaration: false,
    members: [{ name: '', email: '', phone: '' }, { name: '', email: '', phone: '' }],
    bank_account: '',
    ird_number: '',
    date_of_birth: '',
    confirmations: {},
    nz_tax_resident: '',
    wt_rate_type: 'standard',
    wt_rate_percent: '20',
    tailored_certificate_number: '',
    signature: '',
    declaration_confirmed: false,
    documents: {},
  };
}

/** The payment options a performer type is offered (Orbit's split, kept). */
const KEYS_BY_TYPE: Record<PerformerType, PaidAs[]> = {
  solo: ['individual', 'sole_trader', 'company', 'exemption', 'not_sure'],
  band: ['band_leader', 'partnership', 'company', 'agency', 'not_sure'],
  agent: ['agency', 'company', 'not_sure'],
};

export function lanesFor(type: PerformerType | '', lanes: JoinLane[]): JoinLane[] {
  if (!type) return lanes;
  const keys = KEYS_BY_TYPE[type];
  const picked = lanes.filter((l) => keys.includes(l.key)).sort((a, b) => keys.indexOf(a.key) - keys.indexOf(b.key));
  return picked.length ? picked : lanes;
}

export function laneOf(paidAs: PaidAs | '', lanes: JoinLane[]): Lane | null {
  return lanes.find((l) => l.key === paidAs)?.lane ?? null;
}

/** The IR330C declaration is for the withholding-tax lane (individuals and sole traders). */
export function needsDeclaration(paidAs: PaidAs | '', lanes: JoinLane[]): boolean {
  return laneOf(paidAs, lanes) === 'payroll_contractor';
}

/** The IRD number field shows for every self-chosen lane; it is required
 *  only for withholding tax (the server's rule). */
export function showsIrd(paidAs: PaidAs | '', lanes: JoinLane[]): boolean {
  const lane = laneOf(paidAs, lanes);
  return lane === null || lane === 'payroll_contractor' || lane === 'exemption' || lane === 'invoice';
}

export function irdRequired(paidAs: PaidAs | '', lanes: JoinLane[]): boolean {
  return laneOf(paidAs, lanes) === 'payroll_contractor';
}

/** Date of birth is asked (optional) where payroll will carry the person. */
export function showsDob(paidAs: PaidAs | '', lanes: JoinLane[]): boolean {
  const lane = laneOf(paidAs, lanes);
  return lane === 'payroll_contractor' || lane === 'payroll_employee';
}

export function showsGst(paidAs: PaidAs | ''): boolean {
  return paidAs === 'sole_trader' || paidAs === 'company' || paidAs === 'agency' || paidAs === 'band_leader' || paidAs === 'partnership';
}

export function showsCompany(paidAs: PaidAs | ''): boolean {
  return paidAs === 'company' || paidAs === 'agency' || paidAs === 'band_leader';
}

/** A band paying each member separately gives no bank details of its own —
 *  the members do, on their own links. */
export function splitBand(page: JoinPage, form: JoinForm): boolean {
  return page.purpose === 'onboarding' && form.performer_type === 'band' && form.band_mode === 'members_split';
}

/** The steps this link walks, given what has been answered: a solo act never
 *  sees the band step; a band member link has no band step either; a
 *  members-split band has no bank step. */
export function activeSteps(page: JoinPage, form: JoinForm): JoinStep[] {
  return page.steps.filter((s) => {
    if (s === 'band') return form.performer_type === 'band' && page.purpose === 'onboarding';
    if (s === 'bank_tax') return !splitBand(page, form);
    return true;
  });
}

export type FieldErrors = Record<string, string>;

const collapse = (s: string) => s.trim().split(/\s+/).join(' ').toLowerCase();

/** The checks the browser can make before the step is saved. Messages are
 *  plain; the server's own (`errors`) replace them when it disagrees. */
export function validateStep(step: JoinStep, form: JoinForm, page: JoinPage): FieldErrors {
  const e: FieldErrors = {};
  const lanes = page.lanes;
  switch (step) {
    case 'about':
      if (!form.name.trim()) e.name = 'Your name is needed.';
      if (!validEmail(form.email)) e.email = 'Enter your email address, like name@example.com.';
      if (!form.phone.trim()) e.phone = 'A phone number is needed.';
      break;
    case 'performing':
      if (!form.performer_type) e.performer_type = 'Choose one.';
      break;
    case 'paid':
      if (!form.paid_as) e.paid_as = 'Choose how you’re paid.';
      else {
        if (laneOf(form.paid_as, lanes) !== null && !form.legal_name.trim()) e.legal_name = 'Your full legal name is needed — not a stage, band or trading name.';
        if (showsGst(form.paid_as) && form.gst_registered && !form.gst_number.trim()) e.gst_number = 'Your GST number is needed.';
        if ((form.paid_as === 'company' || form.paid_as === 'agency') && !form.company_name.trim()) e.company_name = form.paid_as === 'agency' ? 'The agency’s name is needed.' : 'The company’s name is needed.';
        if (form.paid_as === 'exemption') {
          if (!form.exemption_certificate_number.trim()) e.exemption_certificate_number = 'The certificate number is needed.';
          if (!form.exemption_expiry_date) e.exemption_expiry_date = 'When does the certificate expire?';
        }
      }
      break;
    case 'band':
      if (!form.band_mode) e.band_mode = 'Choose one.';
      if (form.band_mode === 'leader' && !form.band_leader_declaration) e.band_leader_declaration = 'Please confirm the band leader declaration.';
      if (form.band_mode === 'members_split') {
        const filled = form.members.filter((m) => m.name.trim() || m.email.trim());
        if (filled.length < 2) e.members = 'List at least two band members with a name and an email.';
        else if (filled.some((m) => !m.name.trim() || !validEmail(m.email))) e.members = 'Every member needs a name and a valid email.';
        else if (filled.length > 20) e.members = 'A band can list up to 20 members.';
      }
      break;
    case 'bank_tax': {
      const bank = bankAccountProblem(form.bank_account);
      if (bank) e.bank_account = bank === 'shape' ? BANK_SHAPE_MESSAGE : BANK_CHECK_MESSAGE;
      const ird = form.ird_number.trim();
      if (irdRequired(form.paid_as, lanes) && !ird) e.ird_number = 'Your IRD number is needed for withholding tax.';
      else if (ird && !validIrdNumber(ird)) e.ird_number = 'That IRD number doesn’t check out — it should be 8 or 9 digits.';
      if (form.paid_as === 'exemption' && !form.documents.exemption_certificate) e.exemption_certificate = 'Upload your exemption certificate.';
      if (laneOf(form.paid_as, lanes) !== null && page.confirmations.some((c) => !form.confirmations[c.key])) {
        e.contractor_confirmations = 'Every statement needs a tick. If one isn’t true for you, stop here and talk to the finance team.';
      }
      if (needsDeclaration(form.paid_as, lanes)) Object.assign(e, validateDeclaration(form, form.legal_name));
      break;
    }
    case 'declaration': {
      if (!form.legal_name.trim()) e.legal_name = 'Your full legal name is needed.';
      if (!validIrdNumber(form.ird_number)) e.ird_number = 'That IRD number doesn’t check out — it should be 8 or 9 digits.';
      Object.assign(e, validateDeclaration(form, form.legal_name));
      break;
    }
    default:
      break;
  }
  return e;
}

export function validateDeclaration(form: JoinForm, legalName: string): FieldErrors {
  const e: FieldErrors = {};
  if (form.nz_tax_resident !== 'yes') e.nz_tax_resident = form.nz_tax_resident === 'no' ? 'Different rules apply to non-residents — please contact the finance team before continuing.' : 'Please answer the tax-resident question.';
  const rate = Number(form.wt_rate_percent);
  if (form.wt_rate_type === 'self_selected' && !(Number.isFinite(rate) && rate >= 10 && rate <= 100)) e.wt_rate_percent = 'Choose a rate between 10% and 100%.';
  if (form.wt_rate_type === 'tailored') {
    if (!(Number.isFinite(rate) && rate >= 0 && rate <= 100)) e.wt_rate_percent = 'Enter the rate on your certificate.';
    if (!form.tailored_certificate_number.trim()) e.tailored_certificate_number = 'The certificate reference is needed.';
  }
  if (!form.signature.trim()) e.signature = 'Type your full legal name to sign.';
  else if (legalName.trim() && collapse(form.signature) !== collapse(legalName)) e.signature = 'Sign with the legal name you gave above.';
  if (!form.declaration_confirmed) e.declaration_confirmed = 'Please confirm the declaration.';
  return e;
}

export function effectiveRate(form: JoinForm): number {
  if (form.wt_rate_type === 'standard') return 20;
  const n = Number(form.wt_rate_percent);
  return Number.isFinite(n) ? n : 0;
}

/** The server names declaration fields `declaration.<field>` on the full
 *  form and `members.<n>` for one member; the form keys them flat. */
export function mapServerErrors(errors: Record<string, string>): FieldErrors {
  const out: FieldErrors = {};
  for (const [key, message] of Object.entries(errors)) {
    let k = key.replace(/^declaration\./, '');
    if (k.startsWith('members.')) k = 'members';
    if (k === 'confirmed') k = 'declaration_confirmed';
    if (k === 'exemption_certificate_file_id') k = 'exemption_certificate';
    if (k === 'company_document_file_id') k = 'company_document';
    out[k] = message;
  }
  return out;
}

function membersOf(form: JoinForm) {
  return form.members
    .filter((m) => m.name.trim() && m.email.trim())
    .map((m) => ({ name: m.name.trim(), email: m.email.trim(), phone: m.phone.trim() || null }));
}

/** What a step saves (`POST /steps/{name}`); the code and review steps save
 *  nothing. `page` is needed for the bank step, whose declaration depends on
 *  the lane the payment answer maps to. */
export function stepBody(step: JoinStep, form: JoinForm, page?: JoinPage): Record<string, unknown> | null {
  switch (step) {
    case 'about':
      return { name: form.name.trim(), email: form.email.trim(), phone: form.phone.trim() };
    case 'performing':
      return { performer_type: form.performer_type };
    case 'paid':
      return {
        paid_as: form.paid_as,
        legal_name: form.legal_name.trim() || undefined,
        trading_name: form.trading_name.trim() || undefined,
        gst_registered: showsGst(form.paid_as) ? form.gst_registered : false,
        gst_number: showsGst(form.paid_as) && form.gst_registered ? form.gst_number.trim() : undefined,
        company_name: showsCompany(form.paid_as) ? form.company_name.trim() || undefined : undefined,
        company_number: showsCompany(form.paid_as) ? form.company_number.trim() || undefined : undefined,
        exemption_certificate_number: form.paid_as === 'exemption' ? form.exemption_certificate_number.trim() : undefined,
        exemption_expiry_date: form.paid_as === 'exemption' ? form.exemption_expiry_date : undefined,
      };
    case 'band':
      return {
        performer_type: form.performer_type,
        ...(form.band_mode === 'members_split'
          ? { band_mode: 'members_split', members: membersOf(form) }
          : { band_mode: 'leader', band_leader_declaration: form.band_leader_declaration }),
      };
    case 'bank_tax':
      // The server works the lane out from the payment answer in the same
      // body (nothing from the earlier steps is stored for it), so the bank
      // step carries the "who" and "how paid" answers again — and the
      // declaration, which it checks here as well as at submit.
      return {
        ...stepBody('performing', form),
        ...stepBody('paid', form),
        ...bankTaxBody(form),
        ...(page && needsDeclaration(form.paid_as, page.lanes) ? { declaration: declarationBody(form) } : {}),
      };
    case 'declaration':
      return { legal_name: form.legal_name.trim() || undefined, ird_number: normaliseIrd(form.ird_number), declaration: declarationBody(form) };
    default:
      return null;
  }
}

function bankTaxBody(form: JoinForm): Record<string, unknown> {
  const ird = form.ird_number.trim();
  return {
    bank_account: normaliseBankAccount(form.bank_account) ?? form.bank_account.trim(),
    ird_number: ird ? normaliseIrd(ird) : undefined,
    date_of_birth: form.date_of_birth || undefined,
    contractor_confirmations: form.confirmations,
    legal_name: form.legal_name.trim() || undefined,
  };
}

export function declarationBody(form: JoinForm): Record<string, unknown> {
  return {
    nz_tax_resident: form.nz_tax_resident === 'yes',
    wt_rate_type: form.wt_rate_type,
    wt_rate_percent: effectiveRate(form),
    tailored_certificate_number: form.wt_rate_type === 'tailored' ? form.tailored_certificate_number.trim() : undefined,
    signature: form.signature.trim(),
    confirmed: form.declaration_confirmed,
  };
}

/** Everything, in one object, for `POST /submit`. Uploaded files travel as
 *  `<kind>_file_id`, the way the server attaches them. */
export function submitBody(form: JoinForm, page: JoinPage): Record<string, unknown> {
  if (page.purpose === 'declaration') {
    return { ...stepBody('declaration', form) };
  }
  const body: Record<string, unknown> = {
    ...stepBody('about', form),
    ...stepBody('performing', form),
    ...stepBody('paid', form),
  };
  if (form.performer_type === 'band' && page.purpose === 'onboarding') Object.assign(body, stepBody('band', form));
  if (!splitBand(page, form)) {
    Object.assign(body, bankTaxBody(form));
    if (needsDeclaration(form.paid_as, page.lanes)) body.declaration = declarationBody(form);
  }
  if (form.documents.exemption_certificate) body.exemption_certificate_file_id = form.documents.exemption_certificate;
  if (form.documents.company_document) body.company_document_file_id = form.documents.company_document;
  return body;
}

/** The one-time code must be verified before this step may open. */
export function codeGate(step: JoinStep, page: JoinPage, verified: boolean): boolean {
  if (verified) return false;
  if (step === 'code') return true;
  return page.code.required_before === step;
}
