'use client';

/**
 * The onboarding form's screens (plan §11.2): about you, who is performing,
 * how you're paid, the band, bank and tax (behind the emailed code — with
 * the IR330C declaration for the withholding-tax lane, the six contractor
 * confirmations, and a certificate upload for the exemption lane), review;
 * plus the code screen and the declaration-only screen. Phone first, large
 * tap targets, no stored value ever shown back — the fields start empty
 * except name, email and phone from the invite.
 *
 * The declaration's legal wording comes from the API as it stands at the
 * wording version finance signed off; this file never rewrites it.
 */
import { useId, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { Mail, Upload } from 'lucide-react';
import Button from '../../../ui/Button';
import Icon from '../../../ui/Icon';
import { dateLabel } from '../format';
import { normaliseBankAccount, normaliseCode } from '../logic';
import { Card, ErrorLine } from './PublicFrame';
import { MAX_UPLOAD_BYTES, PublicApiError, UPLOAD_ACCEPT, friendlyError, joinPath, publicSend, publicUpload } from './publicApi';
import { effectiveRate, irdRequired, laneOf, lanesFor, needsDeclaration, showsCompany, showsDob, showsGst, showsIrd } from './joinState';
import type { FieldErrors, JoinForm, MemberDraft } from './joinState';
import type { JoinPage, PaidAs, PerformerType } from '../types';

export interface StepProps {
  page: JoinPage;
  form: JoinForm;
  set: (patch: Partial<JoinForm>) => void;
  errors: FieldErrors;
}

export function Heading({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <h2 style={{ margin: 0, fontSize: 'var(--fs-xl)', fontWeight: 700, color: 'var(--text)' }}>{title}</h2>
      {children && <p style={{ margin: '6px 0 0', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>{children}</p>}
    </div>
  );
}

export function TextField({ id, label, value, onChange, error, hint, type = 'text', inputMode, autoComplete = 'off', placeholder, disabled }: {
  id: string;
  label: ReactNode;
  value: string;
  onChange: (v: string) => void;
  error?: string;
  hint?: ReactNode;
  type?: string;
  inputMode?: 'text' | 'email' | 'tel' | 'numeric' | 'decimal';
  autoComplete?: string;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <div style={{ marginBottom: 12 }}>
      <label htmlFor={id} className="n-label" style={{ fontSize: 'var(--fs-base)' }}>{label}</label>
      <input
        id={id}
        className="n-input ep-input"
        type={type}
        inputMode={inputMode}
        autoComplete={autoComplete}
        autoCapitalize={inputMode === 'email' ? 'off' : undefined}
        spellCheck={false}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
      />
      {error ? <ErrorLine id={`${id}-error`}>{error}</ErrorLine> : hint ? <p id={`${id}-hint`} style={{ margin: '6px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{hint}</p> : null}
    </div>
  );
}

function Choice({ selected, onClick, title, children, badge }: { selected: boolean; onClick: () => void; title: string; children?: ReactNode; badge?: string }) {
  return (
    <button type="button" className="ep-choice" aria-pressed={selected} onClick={onClick}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 'var(--fs-md)' }}>{title}</span>
        {badge && <span className="n-badge n-badge--info">{badge}</span>}
      </div>
      {children && <div style={{ marginTop: 4, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{children}</div>}
    </button>
  );
}

function Check({ id, checked, onChange, children, error }: { id?: string; checked: boolean; onChange: (v: boolean) => void; children: ReactNode; error?: string }) {
  const auto = useId();
  const fid = id ?? auto;
  return (
    <div>
      <label htmlFor={fid} className="ep-check">
        <input id={fid} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span>{children}</span>
      </label>
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}

function Radio({ name, value, current, onChange, children }: { name: string; value: string; current: string; onChange: (v: string) => void; children: ReactNode }) {
  return (
    <label className="ep-check" style={{ borderColor: current === value ? 'var(--brand-soft)' : undefined, background: current === value ? 'var(--accent-soft)' : undefined }}>
      <input type="radio" name={name} value={value} checked={current === value} onChange={() => onChange(value)} style={{ width: 18, height: 18, accentColor: 'var(--accent)', marginTop: 2 }} />
      <span style={{ flex: 1 }}>{children}</span>
    </label>
  );
}

export function Progress({ at, total }: { at: number; total: number }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--fs-sm)', color: 'var(--muted)', marginBottom: 4 }}>
        <span>Step {at} of {total}</span>
      </div>
      <div aria-hidden style={{ height: 6, background: 'var(--line-soft)', borderRadius: 999, overflow: 'hidden' }}>
        <div style={{ width: `${Math.round((at / total) * 100)}%`, height: '100%', background: 'var(--brand-soft)', transition: 'width 0.2s' }} />
      </div>
    </div>
  );
}

// ── Steps ────────────────────────────────────────────────────────────────

export function StepAbout({ page, form, set, errors }: StepProps) {
  const base = useId();
  return (
    <>
      <Heading title={page.purpose === 'band_member' ? `You’re joining ${page.band_name ?? 'the band'}` : page.update ? 'Update your details' : 'Your details'}>
        {page.purpose === 'band_member'
          ? 'The band is paying each member separately, so we need your own details to pay you.'
          : page.update
            ? `${page.organisation} asked you to go through the form again. Nothing you gave before is shown here — please fill it in afresh; a changed bank account is checked by finance before it is used.`
            : `Thanks for performing with ${page.organisation}. Before your first payment we need a few things from you so you can be paid correctly.`}
      </Heading>
      <TextField id={`${base}-name`} label="Your name" value={form.name} onChange={(v) => set({ name: v })} error={errors.name} autoComplete="name" />
      <TextField id={`${base}-email`} label="Email" value={form.email} onChange={(v) => set({ email: v })} error={errors.email} type="email" inputMode="email" autoComplete="email" hint="The code for the bank step goes to the address this link was sent to — enter it first if you need to change your email." />
      <TextField id={`${base}-phone`} label="Phone" value={form.phone} onChange={(v) => set({ phone: v })} error={errors.phone} type="tel" inputMode="tel" autoComplete="tel" />
    </>
  );
}

const TYPES: { key: PerformerType; title: string; sub: string }[] = [
  { key: 'solo', title: 'Solo performer', sub: 'Just you — DJ, musician, performer.' },
  { key: 'band', title: 'Band, duo or group', sub: 'Two or more performers paid for the same gig.' },
  { key: 'agent', title: 'Booking agent or manager', sub: 'You arrange and bill on behalf of performers.' },
];

export function StepPerforming({ form, set, errors }: StepProps) {
  return (
    <>
      <Heading title="Who is performing?">We&rsquo;ll use this to show you the right payment options next.</Heading>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {TYPES.map((t) => (
          <Choice key={t.key} selected={form.performer_type === t.key} onClick={() => set({ performer_type: t.key, paid_as: '' })} title={t.title}>{t.sub}</Choice>
        ))}
      </div>
      {errors.performer_type && <ErrorLine>{errors.performer_type}</ErrorLine>}
    </>
  );
}

const REVIEW_BADGE: Partial<Record<PaidAs, string>> = { partnership: 'Finance reviews', not_sure: 'Finance reviews' };

export function StepPaid({ page, form, set, errors }: StepProps) {
  const base = useId();
  const options = lanesFor(form.performer_type, page.lanes);
  const paidAs = form.paid_as;
  return (
    <>
      <Heading title={form.performer_type === 'band' ? 'Who should we contract with and pay?' : 'How should we pay you?'}>
        {form.performer_type === 'band'
          ? 'If each band member is paid directly, every member completes their own setup. If one person or company is paid for the whole band, they are responsible for paying the others.'
          : 'Pick the option that describes how you’re set up. Each one says what happens with tax and invoicing.'}
      </Heading>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {options.map((l) => (
          <Choice key={l.key} selected={paidAs === l.key} onClick={() => set({ paid_as: l.key })} title={l.label} badge={REVIEW_BADGE[l.key]}>{l.blurb}</Choice>
        ))}
      </div>
      {errors.paid_as && <ErrorLine>{errors.paid_as}</ErrorLine>}

      {paidAs && paidAs !== 'not_sure' && (
        <div style={{ marginTop: 18 }}>
          <TextField id={`${base}-legal`} label="Full legal name" value={form.legal_name} onChange={(v) => set({ legal_name: v, signature: form.signature || v })} error={errors.legal_name} autoComplete="name" hint="As it appears on your IRD or company records — not a stage, band or trading name." />
          {paidAs === 'sole_trader' && <TextField id={`${base}-trading`} label="Trading name (optional)" value={form.trading_name} onChange={(v) => set({ trading_name: v })} error={errors.trading_name} />}
          {showsCompany(paidAs) && (
            <>
              <TextField id={`${base}-company`} label={paidAs === 'agency' ? 'Agency name' : paidAs === 'band_leader' ? 'Company name (if you invoice through one)' : 'Company name'} value={form.company_name} onChange={(v) => set({ company_name: v })} error={errors.company_name} hint={paidAs === 'company' ? 'A registered NZ company — usually ends in Limited or Ltd.' : undefined} />
              <TextField id={`${base}-cno`} label="NZ company number (optional)" value={form.company_number} onChange={(v) => set({ company_number: v })} error={errors.company_number} inputMode="numeric" />
            </>
          )}
          {showsGst(paidAs) && (
            <div style={{ marginBottom: 12 }}>
              <Check checked={form.gst_registered} onChange={(v) => set({ gst_registered: v })}>I&rsquo;m registered for GST</Check>
              {form.gst_registered && (
                <div style={{ marginTop: 10 }}>
                  <TextField id={`${base}-gst`} label="GST number" value={form.gst_number} onChange={(v) => set({ gst_number: v })} error={errors.gst_number} inputMode="numeric" hint={laneOf(paidAs, page.lanes) === 'payroll_contractor' ? 'Withholding tax is worked out on the amount excluding GST; GST on a valid tax invoice is paid as well.' : undefined} />
                </div>
              )}
            </div>
          )}
          {paidAs === 'exemption' && (
            <>
              <p style={{ margin: '0 0 10px', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>Only choose this if Inland Revenue has issued you a current certificate of exemption. You&rsquo;ll upload it after the code step, and finance checks it before paying.</p>
              <TextField id={`${base}-exno`} label="Certificate number" value={form.exemption_certificate_number} onChange={(v) => set({ exemption_certificate_number: v })} error={errors.exemption_certificate_number} />
              <TextField id={`${base}-exexp`} label="Certificate expiry date" value={form.exemption_expiry_date} onChange={(v) => set({ exemption_expiry_date: v })} error={errors.exemption_expiry_date} type="date" />
            </>
          )}
          {paidAs === 'band_leader' && (
            <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>You&rsquo;ll confirm the band leader declaration on the next step.</p>
          )}
        </div>
      )}
      {paidAs === 'not_sure' && (
        <p style={{ margin: '14px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>No problem — keep going with your contact and bank details and the finance team will be in touch to settle how you&rsquo;re paid.</p>
      )}
    </>
  );
}

export function StepBand({ form, set, errors }: StepProps) {
  const base = useId();
  const members = form.members;
  const update = (i: number, patch: Partial<MemberDraft>) => set({ members: members.map((m, j) => (j === i ? { ...m, ...patch } : m)) });
  return (
    <>
      <Heading title="Your band">Who gets paid: one band leader for everyone, or each member separately?</Heading>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <Choice selected={form.band_mode === 'leader'} onClick={() => set({ band_mode: 'leader' })} title="One band leader is paid for everyone">The leader receives the full fee and pays the other performers.</Choice>
        <Choice selected={form.band_mode === 'members_split'} onClick={() => set({ band_mode: 'members_split' })} title="Pay each member separately" badge="Each member sets up">Every member gets their own link and fills in their own details.</Choice>
      </div>
      {errors.band_mode && <ErrorLine>{errors.band_mode}</ErrorLine>}

      {form.band_mode === 'leader' && (
        <div style={{ marginTop: 16 }}>
          <Check checked={form.band_leader_declaration} onChange={(v) => set({ band_leader_declaration: v })} error={errors.band_leader_declaration}>
            I confirm I am authorised by the band or group to contract with the organisation, issue invoices, receive payment and distribute it to the other performers. I understand payment to me is payment for the full performance fee, and I am responsible for any tax, ACC, GST, PAYE, contractor payments or other obligations arising from payments I make to other band members.
          </Check>
        </div>
      )}

      {form.band_mode === 'members_split' && (
        <div style={{ marginTop: 16 }}>
          <p style={{ margin: '0 0 10px', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>List each member with their name and email. Each gets their own link, and nobody is paid until every setup is complete.</p>
          {members.map((m, i) => (
            <div key={i} className="n-card" style={{ padding: 12, marginBottom: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <span className="n-eyebrow">Member {i + 1}</span>
                {members.length > 2 && <button type="button" className="n-btn n-btn--link" style={{ fontSize: 'var(--fs-sm)' }} onClick={() => set({ members: members.filter((_, j) => j !== i) })}>Remove</button>}
              </div>
              <TextField id={`${base}-m${i}-name`} label="Full name" value={m.name} onChange={(v) => update(i, { name: v })} autoComplete="off" />
              <TextField id={`${base}-m${i}-email`} label="Email" value={m.email} onChange={(v) => update(i, { email: v })} type="email" inputMode="email" />
              <TextField id={`${base}-m${i}-phone`} label="Phone (optional)" value={m.phone} onChange={(v) => update(i, { phone: v })} type="tel" inputMode="tel" />
            </div>
          ))}
          {members.length < 20 && <Button onClick={() => set({ members: [...members, { name: '', email: '', phone: '' }] })}>Add another member</Button>}
          {errors.members && <ErrorLine>{errors.members}</ErrorLine>}
        </div>
      )}
    </>
  );
}

/** A certificate or company document: uploaded now (the code is verified by
 *  here), attached to the record at submit. */
function DocumentUpload({ token, kind, label, form, set, error }: {
  token: string;
  kind: 'exemption_certificate' | 'company_document';
  label: string;
  form: JoinForm;
  set: (patch: Partial<JoinForm>) => void;
  error?: string;
}) {
  const id = useId();
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState<string | null>(null);
  const [name, setName] = useState<string | null>(null);
  const upload = async (file: File | null) => {
    if (!file) return;
    if (file.size > MAX_UPLOAD_BYTES) return setFail('The file must be 10 MB or smaller.');
    setBusy(true);
    setFail(null);
    try {
      const fd = new FormData();
      fd.append('kind', kind);
      fd.append('file', file);
      const res = await publicUpload<{ file_id: string }>(`${joinPath(token)}/documents`, fd);
      set({ documents: { ...form.documents, [kind]: res.file_id } });
      setName(file.name);
    } catch (e) {
      setFail(friendlyError(e));
    }
    setBusy(false);
  };
  const done = !!form.documents[kind];
  return (
    <div style={{ marginBottom: 12 }}>
      <label htmlFor={id} className="n-label" style={{ fontSize: 'var(--fs-base)' }}>{label}</label>
      <input id={id} type="file" accept={UPLOAD_ACCEPT} disabled={busy} onChange={(e) => upload(e.target.files?.[0] ?? null)} style={{ fontSize: 'var(--fs-base)', width: '100%' }} />
      <p style={{ margin: '6px 0 0', fontSize: 'var(--fs-sm)', color: done ? 'var(--ok)' : 'var(--muted)' }}>
        {busy ? 'Uploading…' : done ? <><Icon icon={Upload} size={12} /> Received{name ? `: ${name}` : ''}</> : 'PDF or photo, up to 10 MB.'}
      </p>
      {(fail || error) && <ErrorLine>{fail ?? error}</ErrorLine>}
    </div>
  );
}

/** The IR330C declaration: residency, the withholding rate, and the signed
 *  declaration in the wording the API supplies. */
export function TaxDeclaration({ page, form, set, errors }: StepProps) {
  const base = useId();
  const rate = effectiveRate(form);
  return (
    <div style={{ marginTop: 6 }}>
      <h3 style={{ margin: '0 0 6px', fontSize: 'var(--fs-lg)', fontWeight: 600 }}>Tax rate declaration (IR330C)</h3>
      <p style={{ margin: '0 0 12px', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        Because you&rsquo;re paid as a {form.paid_as === 'sole_trader' ? 'sole trader providing entertainment services' : 'an individual entertainer'}, withholding tax is deducted from your payments and paid to Inland Revenue on your behalf{form.paid_as === 'sole_trader' ? ' — even when you send an invoice' : ''}. Your tax code for these payments is <strong>WT</strong>.
      </p>

      <div style={{ marginBottom: 12 }}>
        <div className="n-label" style={{ fontSize: 'var(--fs-base)' }}>Are you a New Zealand tax resident?</div>
        <div className="ep-two">
          <Radio name={`${base}-res`} value="yes" current={form.nz_tax_resident} onChange={(v) => set({ nz_tax_resident: v as 'yes' | 'no' })}>Yes</Radio>
          <Radio name={`${base}-res`} value="no" current={form.nz_tax_resident} onChange={(v) => set({ nz_tax_resident: v as 'yes' | 'no' })}>No</Radio>
        </div>
        {errors.nz_tax_resident && <ErrorLine>{errors.nz_tax_resident}</ErrorLine>}
        {form.nz_tax_resident === 'no' && (
          <p style={{ margin: '8px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--warn)' }}>Different tax rules may apply to non-resident entertainers. Please contact the finance team so they can confirm the right treatment before you continue.</p>
        )}
      </div>

      <div style={{ marginBottom: 12 }}>
        <div className="n-label" style={{ fontSize: 'var(--fs-base)' }}>Choose the percentage of each payment deducted as withholding tax</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <Radio name={`${base}-rate`} value="standard" current={form.wt_rate_type} onChange={() => set({ wt_rate_type: 'standard', wt_rate_percent: '20' })}>
            <strong>The standard entertainer rate — 20%</strong>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>The standard rate for New Zealand-resident entertainers, including performers, musicians and DJs.</div>
          </Radio>
          <Radio name={`${base}-rate`} value="self_selected" current={form.wt_rate_type} onChange={() => set({ wt_rate_type: 'self_selected', wt_rate_percent: form.wt_rate_percent === '20' ? '15' : form.wt_rate_percent })}>
            <strong>Choose my own rate</strong>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>A different rate if it better reflects your expected tax. It can&rsquo;t normally be below 10% unless Inland Revenue has issued you a tailored rate certificate.</div>
          </Radio>
          <Radio name={`${base}-rate`} value="tailored" current={form.wt_rate_type} onChange={() => set({ wt_rate_type: 'tailored', wt_rate_percent: '' })}>
            <strong>A tailored rate issued by Inland Revenue</strong>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>You hold a current tailored tax rate certificate for these payments. Finance checks it before it applies.</div>
          </Radio>
        </div>
        {form.wt_rate_type !== 'standard' && (
          <div style={{ marginTop: 10 }}>
            <TextField id={`${base}-pct`} label={form.wt_rate_type === 'tailored' ? 'Rate on your certificate (%)' : 'Your rate (%)'} value={form.wt_rate_percent} onChange={(v) => set({ wt_rate_percent: v })} error={errors.wt_rate_percent} inputMode="decimal" hint={form.wt_rate_type === 'self_selected' && rate >= 10 && rate < 20 ? 'A lower rate may mean more tax to pay after the end of the tax year — worth checking with Inland Revenue or your accountant.' : undefined} />
            {form.wt_rate_type === 'tailored' && <TextField id={`${base}-cert`} label="Certificate reference" value={form.tailored_certificate_number} onChange={(v) => set({ tailored_certificate_number: v })} error={errors.tailored_certificate_number} />}
          </div>
        )}
      </div>

      <div className="n-card" style={{ padding: 14, marginBottom: 12, fontSize: 'var(--fs-base)', background: 'var(--surface)' }}>
        {page.declaration.paragraphs.map((p, i) => <p key={i} style={{ margin: i === 0 ? 0 : '8px 0 0' }}>{p}</p>)}
        {page.declaration.understand.length > 0 && (
          <>
            <p style={{ margin: '8px 0 4px' }}>I understand that:</p>
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {page.declaration.understand.map((u, i) => <li key={i} style={{ marginBottom: 2 }}>{u}</li>)}
            </ul>
          </>
        )}
        <p style={{ margin: '10px 0 0', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Wording version {page.declaration.wording_version} · rate chosen: {rate}%{form.wt_rate_type === 'standard' ? ' (standard)' : form.wt_rate_type === 'self_selected' ? ' (self-selected)' : ' (tailored)'}</p>
      </div>
      <Check checked={form.declaration_confirmed} onChange={(v) => set({ declaration_confirmed: v })} error={errors.declaration_confirmed}>{page.declaration.confirm_label}</Check>
      <div style={{ marginTop: 12 }}>
        <TextField id={`${base}-sig`} label="Signature — type your full legal name" value={form.signature} onChange={(v) => set({ signature: v })} error={errors.signature} autoComplete="off" hint={`It must match the legal name you gave. Declaration date: ${dateLabel(new Date().toISOString().slice(0, 10))}`} />
      </div>
    </div>
  );
}

export function StepBankTax({ page, form, set, errors, token }: StepProps & { token: string }) {
  const base = useId();
  const normalised = normaliseBankAccount(form.bank_account);
  const paidAs = form.paid_as;
  return (
    <>
      <Heading title="Bank and tax details">Where we pay you, and what Inland Revenue needs. These are stored encrypted and never shown on a link again.</Heading>
      <TextField id={`${base}-bank`} label="Bank account number" value={form.bank_account} onChange={(v) => set({ bank_account: v })} error={errors.bank_account} inputMode="numeric" autoComplete="off" hint={normalised ? `Will be saved as ${normalised}` : 'Bank, branch, account and suffix — as printed on your statement.'} />
      {showsIrd(paidAs, page.lanes) && <TextField id={`${base}-ird`} label={`${paidAs === 'partnership' ? 'Partnership IRD number' : 'IRD number'}${irdRequired(paidAs, page.lanes) ? '' : ' (optional)'}`} value={form.ird_number} onChange={(v) => set({ ird_number: v })} error={errors.ird_number} inputMode="numeric" autoComplete="off" hint="8 or 9 digits; spaces and dashes are fine." />}
      {showsDob(paidAs, page.lanes) && <TextField id={`${base}-dob`} label="Date of birth (optional)" value={form.date_of_birth} onChange={(v) => set({ date_of_birth: v })} error={errors.date_of_birth} type="date" autoComplete="bday" hint="Helps payroll tell people with the same name apart." />}

      {paidAs === 'exemption' && <DocumentUpload token={token} kind="exemption_certificate" label="Your exemption certificate" form={form} set={set} error={errors.exemption_certificate} />}
      {(paidAs === 'company' || paidAs === 'agency') && <DocumentUpload token={token} kind="company_document" label="A company document (optional)" form={form} set={set} />}

      {page.confirmations.length > 0 && (
        <div style={{ margin: '16px 0' }}>
          <div className="n-label" style={{ fontSize: 'var(--fs-base)' }}>Tick the statements you agree with</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {page.confirmations.map((c) => (
              <Check key={c.key} checked={!!form.confirmations[c.key]} onChange={(v) => set({ confirmations: { ...form.confirmations, [c.key]: v } })}>{c.label}</Check>
            ))}
          </div>
          {errors.contractor_confirmations ? (
            <ErrorLine>{errors.contractor_confirmations}</ErrorLine>
          ) : (
            <p style={{ margin: '6px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Every statement needs a tick to continue. If one isn&rsquo;t true for you, stop here and talk to the finance team — you may need to be set up another way.</p>
          )}
        </div>
      )}

      {needsDeclaration(paidAs, page.lanes) && <TaxDeclaration page={page} form={form} set={set} errors={errors} />}
    </>
  );
}

export function StepDeclaration({ page, form, set, errors }: StepProps) {
  const base = useId();
  return (
    <>
      <Heading title="Your tax rate declaration">{page.organisation} needs your IR330C declaration on record before your next payment. It takes a minute.</Heading>
      <TextField id={`${base}-legal`} label="Full legal name" value={form.legal_name} onChange={(v) => set({ legal_name: v, signature: form.signature || v })} error={errors.legal_name} autoComplete="name" hint="As it appears on your IRD records — not a stage or trading name." />
      <TextField id={`${base}-ird`} label="IRD number" value={form.ird_number} onChange={(v) => set({ ird_number: v })} error={errors.ird_number} inputMode="numeric" autoComplete="off" hint="8 or 9 digits; spaces and dashes are fine." />
      <TaxDeclaration page={page} form={{ ...form, paid_as: form.paid_as || 'individual' }} set={set} errors={errors} />
    </>
  );
}

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '8px 0', borderBottom: '1px solid var(--line-soft)', fontSize: 'var(--fs-base)' }}>
      <span style={{ color: 'var(--muted)' }}>{label}</span>
      <span style={{ textAlign: 'right', overflowWrap: 'anywhere' }}>{children}</span>
    </div>
  );
}

export function StepReview({ page, form }: StepProps) {
  const lane = page.lanes.find((l) => l.key === form.paid_as);
  const bank = normaliseBankAccount(form.bank_account);
  const declaration = page.purpose === 'declaration' || needsDeclaration(form.paid_as, page.lanes);
  return (
    <>
      <Heading title="Check and send">Here&rsquo;s what you&rsquo;re sending. The finance team reviews it before your first payment.</Heading>
      <div className="n-card" style={{ padding: '4px 14px', marginBottom: 12 }}>
        {page.purpose !== 'declaration' && (
          <>
            <Line label="Name">{form.name}</Line>
            <Line label="Email">{form.email}</Line>
            <Line label="Phone">{form.phone}</Line>
            <Line label="Performing as">{TYPES.find((t) => t.key === form.performer_type)?.title ?? '—'}</Line>
            <Line label="Paid as">{lane?.label ?? '—'}</Line>
            {form.legal_name && <Line label="Legal name">{form.legal_name}</Line>}
            {form.company_name && <Line label="Company">{form.company_name}{form.company_number ? ` (${form.company_number})` : ''}</Line>}
            {showsGst(form.paid_as) && <Line label="GST registered">{form.gst_registered ? `Yes — ${form.gst_number}` : 'No'}</Line>}
            {form.paid_as === 'exemption' && <Line label="Exemption certificate">{form.exemption_certificate_number} · expires {dateLabel(form.exemption_expiry_date)}</Line>}
            {form.performer_type === 'band' && form.band_mode && <Line label="Band">{form.band_mode === 'leader' ? 'One band leader is paid' : `${form.members.filter((m) => m.name.trim()).length} members paid separately`}</Line>}
            {bank && <Line label="Bank account">Ending {bank.slice(-2)}</Line>}
            {showsIrd(form.paid_as, page.lanes) && form.ird_number.trim() && <Line label="IRD number">Ending {form.ird_number.replace(/\D/g, '').slice(-3)}</Line>}
          </>
        )}
        {page.purpose === 'declaration' && <Line label="IRD number">{form.ird_number ? `Ending ${form.ird_number.replace(/\D/g, '').slice(-3)}` : '—'}</Line>}
        {declaration && <Line label="Withholding tax">{effectiveRate(form)}% ({form.wt_rate_type === 'standard' ? 'standard' : form.wt_rate_type === 'self_selected' ? 'self-selected' : 'tailored'}) · signed {form.signature || '—'}</Line>}
      </div>
      {lane && (
        <p style={{ margin: '0 0 12px', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          {lane.lane === 'payroll_contractor'
            ? 'You’ll be paid weekly at your agreed hourly rate for the times you confirm, with withholding tax deducted before payment. No invoice is needed.'
            : lane.lane === 'invoice'
              ? 'You’ll be paid after you upload an invoice matching the times you performed. No withholding tax is deducted; you’re responsible for your own tax.'
              : lane.lane === 'exemption'
                ? 'You’ll be paid after you upload an invoice matching the times you performed. Your exemption certificate covers these payments, so no withholding tax is deducted.'
                : 'The finance team will be in touch to settle how you’re paid.'}
        </p>
      )}
      <p style={{ margin: 0, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Sending records the declaration with the time, your address and browser, and this link stops working. Need to change something later? Finance sends you a fresh link.</p>
    </>
  );
}

// ── The one-time code ────────────────────────────────────────────────────

/** Before the bank and tax step: a six-digit code emailed to the invited
 *  address. "Email me a code" answers the same whether or not a code went
 *  out, so the page never confirms whose address the link belongs to. */
export function CodeScreen({ page, token, onVerified }: { page: JoinPage; token: string; onVerified: () => void }) {
  const id = useId();
  const [sent, setSent] = useState(false);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const codeRef = useRef<HTMLInputElement>(null);

  const request = async (again = false) => {
    setBusy(true);
    setError(null);
    try {
      await publicSend('POST', `${joinPath(token)}/code/request`);
      setSent(true);
      setCode('');
      setInfo(again ? 'A new code is on its way. Use the newest one — earlier codes stop working.' : null);
      window.setTimeout(() => codeRef.current?.focus(), 0);
    } catch (e) {
      setError(friendlyError(e));
    }
    setBusy(false);
  };

  const verify = async (e: FormEvent) => {
    e.preventDefault();
    const digits = normaliseCode(code);
    if (digits.length !== 6) return setError('Enter the 6-digit code from the email.');
    setBusy(true);
    setError(null);
    try {
      await publicSend<{ verified: boolean }>('POST', `${joinPath(token)}/code/verify`, { code: digits });
      onVerified();
    } catch (err) {
      if (err instanceof PublicApiError && err.status === 400) {
        setError(err.code === 'bad_code' ? (err.message && !/bad_code/i.test(err.message) ? err.message : 'That code isn’t right. Check the newest email — five wrong tries and you’ll need a new code.') : err.message);
      } else {
        setError(friendlyError(err));
      }
      setBusy(false);
    }
  };

  return (
    <Card>
      <Heading title={sent ? 'Check your email' : 'One quick check'}>
        {sent
          ? `We’ve emailed a 6-digit code to ${page.code.sent_to_hint ?? 'the address this link was sent to'}. It works for 10 minutes.`
          : 'The next step takes the account you’re paid into. To make sure it’s you, we’ll email a code to the address this link was sent to.'}
      </Heading>
      {info && <p style={{ margin: '0 0 12px', fontSize: 'var(--fs-sm)', color: 'var(--info)' }}>{info}</p>}
      {!sent ? (
        <>
          {error && <ErrorLine>{error}</ErrorLine>}
          <Button variant="primary" className="ep-btn-full" icon={Mail} onClick={() => request()} disabled={busy} style={{ marginTop: 8 }}>{busy ? 'Sending…' : 'Email me a code'}</Button>
        </>
      ) : (
        <form onSubmit={verify} noValidate>
          <label htmlFor={id} className="n-label" style={{ fontSize: 'var(--fs-base)' }}>6-digit code</label>
          <input
            id={id}
            ref={codeRef}
            className="n-input n-keep-size"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]*"
            maxLength={7}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/[^\d ]/g, ''))}
            aria-invalid={error ? true : undefined}
            style={{ width: '100%', maxWidth: 220, height: 52, fontSize: 24, letterSpacing: '0.3em', fontVariantNumeric: 'tabular-nums' }}
          />
          {error && <ErrorLine>{error}</ErrorLine>}
          <Button type="submit" variant="primary" className="ep-btn-full" disabled={busy} style={{ marginTop: 14 }}>{busy ? 'Checking…' : 'Continue'}</Button>
          <div style={{ marginTop: 12, textAlign: 'center' }}>
            <button type="button" className="n-btn n-btn--link" disabled={busy} onClick={() => request(true)} style={{ fontSize: 'var(--fs-sm)' }}>Send a new code</button>
          </div>
        </form>
      )}
      <p style={{ margin: '14px 0 0', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Not getting a code? Check your spam folder. Codes stop after a few tries a day — ask finance for a new link if you&rsquo;re stuck.</p>
    </Card>
  );
}
