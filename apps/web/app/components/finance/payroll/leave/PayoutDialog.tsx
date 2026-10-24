'use client';

/**
 * Deciding what a leave request pays. Three workflows, each with every gate
 * on screen (plan §9 step 3):
 *
 *  - annual: paid = min(requested, holiday pay available − uniform bond);
 *    the bond comes off only with written consent on file or "held in Ace".
 *  - sick: four gates — qualification (6 months, ≥10 h/week, continuous;
 *    the facts read from Loaded), balance (days × the person's usual day),
 *    certificate, pay-out (per-day hours × rate). Paid is 0 unless every
 *    gate passes; each gate can be overridden with a note.
 *  - generic: paid ≤ requested.
 *
 * The server works every figure out and stores its decision; this dialog
 * sends the inputs and shows what came back.
 */
import { useId, useState } from 'react';
import { Upload } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import { FormDialog } from '../../../functions/enquiries/bits';
import Money, { useMoney } from '../../shared/Money';
import MoneyInput from '../../shared/MoneyInput';
import { Field, FormGrid, Note } from '../../shared/PageShell';
import { dateLabel, formatHours, personName } from '../../shared/format';
import { errorText, type FinanceApi } from '../../shared/useFinanceApi';
import { weLabel } from '../../shared/weeks';
import type { AnnualInputs, Entity, Payout, RequestedRow, SickEligibility, SickInputs } from '../types';

function Check({ checked, onChange, children, disabled }: { checked: boolean; onChange: (v: boolean) => void; children: React.ReactNode; disabled?: boolean }) {
  return (
    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, fontSize: 'var(--fs-sm)', color: disabled ? 'var(--muted)' : 'var(--text)' }}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} style={{ marginTop: 2 }} />
      <span>{children}</span>
    </label>
  );
}

function Gate({ n, title, passed, children }: { n: number; title: string; passed: boolean | null; children: React.ReactNode }) {
  return (
    <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius)', padding: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 'var(--fs-sm)' }}>{n}. {title}</span>
        {passed === true && <Badge tone="ok">Passes</Badge>}
        {passed === false && <Badge tone="error">Does not pass</Badge>}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>{children}</div>
    </div>
  );
}

function OverrideNote({ id, value, onChange, placeholder }: { id: string; value: string; onChange: (v: string) => void; placeholder: string }) {
  return <input id={id} className="n-input" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} style={{ width: '100%' }} aria-label="Approval note" />;
}

export default function PayoutDialog({ api, entity, weekStart, row, existing, rehearsalDefault, shadow, onClose, onSaved }: {
  api: FinanceApi;
  entity: Entity;
  weekStart: string;
  row: RequestedRow;
  existing: Payout | null;
  rehearsalDefault: boolean;
  shadow: boolean;
  onClose: () => void;
  onSaved: (payout: Payout) => void;
}) {
  const money = useMoney();
  const ids = useId();
  const kind = row.workflow_kind;
  const stored = (existing?.workflow_inputs ?? {}) as Record<string, unknown>;
  const decision = (stored.decision ?? null) as Record<string, unknown> | null;

  const [notes, setNotes] = useState(existing?.notes ?? '');
  const [rehearsal, setRehearsal] = useState(existing?.rehearsal ?? rehearsalDefault);
  const [override, setOverride] = useState<number | null>(existing && kind !== 'sick' && existing.paid_cents !== undefined && decision && typeof decision.calculated_cents === 'number' && decision.calculated_cents !== existing.paid_cents ? existing.paid_cents : null);

  // Annual
  const storedAnnual = stored as Partial<AnnualInputs>;
  const [available, setAvailable] = useState<number | null>(typeof storedAnnual.holiday_pay_available_cents === 'number' ? storedAnnual.holiday_pay_available_cents : null);
  const [deductBond, setDeductBond] = useState<boolean>(existing ? (existing.bond_deducted_cents ?? 0) > 0 : false);
  const [bond, setBond] = useState<number | null>(typeof storedAnnual.uniform_bond_cents === 'number' ? storedAnnual.uniform_bond_cents : entity.uniform_bond_cents);
  const [consent, setConsent] = useState<'file' | 'held_in_ace' | 'none'>(existing?.bond_consent === 'file' ? 'file' : existing?.bond_consent === 'held_in_ace' ? 'held_in_ace' : 'none');
  const [fileId, setFileId] = useState<string | null>(existing?.bond_consent_file_id ?? null);
  const [fileName, setFileName] = useState<string | null>(existing?.bond_consent_file_id ? 'consent on file' : null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // Sick
  const storedSick = (kind === 'sick' ? stored : {}) as Partial<SickInputs>;
  const [elig, setElig] = useState<SickEligibility | null>(storedSick.qualification?.computed ?? null);
  const [eligLoading, setEligLoading] = useState(false);
  const [eligError, setEligError] = useState<string | null>(null);
  const [startDate, setStartDate] = useState<string>(storedSick.qualification?.employment_start_date ?? '');
  const [q, setQ] = useState({
    six_months: storedSick.qualification?.six_months ?? false,
    avg_hours: storedSick.qualification?.avg_hours ?? false,
    continuous: storedSick.qualification?.continuous ?? false,
    override: storedSick.qualification?.override ?? false,
    override_notes: storedSick.qualification?.override_notes ?? '',
  });
  const [bal, setBal] = useState({
    available_days: storedSick.balance?.available_days ?? null as number | null,
    daily_hours: storedSick.balance?.daily_hours ?? null as number | null,
    override: storedSick.balance?.override ?? false,
    override_notes: storedSick.balance?.override_notes ?? '',
  });
  const [cert, setCert] = useState({
    received: storedSick.certificate?.received ?? false,
    override: storedSick.certificate?.override ?? false,
    override_notes: storedSick.certificate?.override_notes ?? '',
  });
  const [dayHours, setDayHours] = useState<{ date: string; hours: number }[]>(() => storedSick.payout?.day_hours ?? row.dates.map((d) => ({ date: d, hours: Number(row.hours) / Math.max(1, row.dates.length) })));
  const [rate, setRate] = useState<number | null>(storedSick.payout?.hourly_rate_cents ?? null);

  const checkLoaded = async () => {
    setEligLoading(true);
    setEligError(null);
    try {
      const out = await api.get<SickEligibility>('/leave/sick-eligibility', { venue_id: entity.venue_id, staff_member_id: row.staff_member_id, as_of: row.dates[0] ?? weekStart, start_date: startDate || undefined });
      setElig(out);
      setQ((s) => ({ ...s, six_months: out.qualifies_six_months, avg_hours: out.qualifies_avg_hours, continuous: out.qualifies_continuous }));
      if (!startDate && out.employment_start_date) setStartDate(out.employment_start_date);
      if (bal.daily_hours === null && out.usual_daily_hours) setBal((s) => ({ ...s, daily_hours: Math.round(out.usual_daily_hours! * 100) / 100 }));
    } catch (e) {
      setEligError(errorText(e));
    } finally {
      setEligLoading(false);
    }
  };

  const upload = async (file: File) => {
    setUploading(true);
    setUploadError(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const out = await api.upload<{ id: string; filename: string }>('/files/bond-consent', form);
      setFileId(out.id);
      setFileName(out.filename || file.name);
      setConsent('file');
    } catch (e) {
      setUploadError(errorText(e));
    } finally {
      setUploading(false);
    }
  };

  const inputs = (): Record<string, unknown> => {
    if (kind === 'annual') {
      return {
        holiday_pay_available_cents: available ?? 0,
        uniform_bond_cents: deductBond ? (bond ?? 0) : 0,
        bond_consent_file_id: deductBond && consent === 'file' ? fileId : null,
        bond_held_in_ace: deductBond && consent === 'held_in_ace',
      };
    }
    if (kind === 'sick') {
      return {
        qualification: { employment_start_date: startDate || null, six_months: q.six_months, avg_hours: q.avg_hours, continuous: q.continuous, override: q.override, override_notes: q.override ? q.override_notes || null : null, computed: elig },
        balance: { available_days: bal.available_days, daily_hours: bal.daily_hours, override: bal.override, override_notes: bal.override ? bal.override_notes || null : null },
        certificate: { received: cert.received, override: cert.override, override_notes: cert.override ? cert.override_notes || null : null },
        payout: { day_hours: dayHours, hourly_rate_cents: rate },
      };
    }
    return {};
  };

  const submit = async () => {
    const body = {
      venue_id: entity.venue_id,
      week_start: weekStart,
      loaded_request_id: row.leave_request_id,
      staff_member_id: row.staff_member_id,
      staff_name: row.staff_name,
      leave_type: row.leave_type_name,
      requested_cents: row.requested_cents,
      workflow_kind: kind,
      inputs: inputs(),
      ...(override !== null ? { paid_cents: override } : {}),
      notes: notes.trim() || null,
      rehearsal,
      ...(existing ? { version: existing.version } : {}),
    };
    const saved = existing
      ? await api.put<Payout>(`/leave/payouts/${existing.id}`, body)
      : await api.post<Payout>('/leave/payouts', body);
    onSaved(saved);
  };

  const title = `${kind === 'annual' ? 'Annual leave' : kind === 'sick' ? 'Sick leave' : row.leave_type_name || 'Leave'} — ${personName(row.staff_name)}`;
  const qualPass = q.override || (q.six_months && q.avg_hours && q.continuous);
  const sumHours = dayHours.reduce((s, d) => s + (Number(d.hours) || 0), 0);

  return (
    <FormDialog title={title} width={kind === 'sick' ? 720 : 560} submitLabel={existing ? 'Save decision' : 'Record decision'} onSubmit={submit} onClose={onClose}>
      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        {weLabel(weekStart, true)} · {row.dates.map((d) => dateLabel(d, { year: false })).join(', ')} · {formatHours(row.hours)} · requested <Money cents={row.requested_cents} /> (Loaded&rsquo;s figure)
        {existing?.source === 'orbit_legacy' && <> · <Badge tone="info">week inferred from Orbit</Badge></>}
      </div>
      {decision && (
        <Note tone="plain">
          Last decision: paid <Money cents={existing?.paid_cents ?? null} />
          {typeof decision.calculated_cents === 'number' && <> (worked out as <Money cents={decision.calculated_cents as number} />)</>}
          {existing?.decided_at && <> · {dateLabel(existing.decided_at)}</>}
        </Note>
      )}

      {kind === 'annual' && (
        <>
          <FormGrid min={200}>
            <Field id={`${ids}-avail`} label="Holiday pay available" hint="The balance in Ace — it is not in Loaded">
              <MoneyInput id={`${ids}-avail`} value={available} onChange={setAvailable} />
            </Field>
            <Field id={`${ids}-bond`} label="Uniform bond" hint={`The entity's default is ${money(entity.uniform_bond_cents)}`}>
              <MoneyInput id={`${ids}-bond`} value={bond} onChange={setBond} disabled={!deductBond} />
            </Field>
          </FormGrid>
          <Check checked={deductBond} onChange={setDeductBond}>Deduct the uniform bond from this pay-out</Check>
          {deductBond && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, paddingLeft: 22 }}>
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>A bond comes off holiday pay only with written consent (Wages Protection Act 1983 s5). Say where the consent is:</div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)' }}>
                <input type="radio" name={`${ids}-consent`} checked={consent === 'held_in_ace'} onChange={() => setConsent('held_in_ace')} /> Held in Ace (on the employee&rsquo;s record)
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', flexWrap: 'wrap' }}>
                <input type="radio" name={`${ids}-consent`} checked={consent === 'file'} onChange={() => setConsent('file')} /> Signed consent uploaded here (PDF)
                <label className="n-btn n-btn--secondary n-btn--sm" style={{ cursor: 'pointer' }}>
                  <Upload size={14} aria-hidden /> {uploading ? 'Uploading…' : fileName ? `Replace (${fileName})` : 'Upload PDF'}
                  <input type="file" accept="application/pdf" style={{ display: 'none' }} onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); }} disabled={uploading} />
                </label>
              </label>
              {uploadError && <Note tone="error">{uploadError}</Note>}
              {consent === 'none' && <Note tone="warn">Without consent on file or held in Ace the server refuses the deduction.</Note>}
            </div>
          )}
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
            Paid = the smaller of the requested amount and (holiday pay available − bond). The server works it out when you save; override below only if the figure must differ.
          </div>
        </>
      )}

      {kind === 'sick' && (
        <>
          <Gate n={1} title="Qualifies for sick leave" passed={qualPass}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
              <Field id={`${ids}-start`} label="Employment start" hint={elig?.start_date_source === 'loaded' ? 'From Loaded' : 'Loaded has no start date — enter it'} style={{ flex: '1 1 160px' }}>
                <input id={`${ids}-start`} className="n-input" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} style={{ width: '100%' }} />
              </Field>
              <Button size="sm" onClick={checkLoaded} disabled={eligLoading}>{eligLoading ? 'Reading Loaded…' : elig ? 'Check with Loaded again' : 'Check with Loaded'}</Button>
            </div>
            {eligError && <Note tone="error">{eligError}</Note>}
            {elig && (
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                {elig.months_employed !== null ? `${elig.months_employed} months employed` : 'Start date unknown'} · {elig.weeks_with_hours} of {elig.lookback_weeks} weeks with hours · average {elig.avg_hours_per_week} h/week · usual day {elig.usual_daily_hours ?? '—'} h
              </div>
            )}
            <Check checked={q.six_months} onChange={(v) => setQ((s) => ({ ...s, six_months: v }))}>Employed for more than 6 months</Check>
            <Check checked={q.avg_hours} onChange={(v) => setQ((s) => ({ ...s, avg_hours: v }))}>Worked an average of at least 10 hours a week</Check>
            <Check checked={q.continuous} onChange={(v) => setQ((s) => ({ ...s, continuous: v }))}>Worked at least 1 hour every week (or 40 hours every month)</Check>
            <Check checked={q.override} onChange={(v) => setQ((s) => ({ ...s, override: v }))}>Special approval from {entity.sick_approver_name || 'the approver'} — pay even though a test fails</Check>
            {q.override && <OverrideNote id={`${ids}-qn`} value={q.override_notes} onChange={(v) => setQ((s) => ({ ...s, override_notes: v }))} placeholder="Who approved it and when (e.g. James, by email, 14 May)" />}
          </Gate>

          <Gate n={2} title="Has the balance" passed={bal.override ? true : null}>
            <FormGrid min={160}>
              <Field id={`${ids}-days`} label="Sick days available" hint="From Ace">
                <input id={`${ids}-days`} className="n-input" type="number" step="0.5" min="0" value={bal.available_days ?? ''} onChange={(e) => setBal((s) => ({ ...s, available_days: e.target.value === '' ? null : Number(e.target.value) }))} style={{ width: '100%' }} />
              </Field>
              <Field id={`${ids}-daily`} label="Hours in this person's usual day" hint="Loaded's 26-week average, or the rule's default when unknown">
                <input id={`${ids}-daily`} className="n-input" type="number" step="0.25" min="0" value={bal.daily_hours ?? ''} onChange={(e) => setBal((s) => ({ ...s, daily_hours: e.target.value === '' ? null : Number(e.target.value) }))} style={{ width: '100%' }} />
              </Field>
            </FormGrid>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Passes when days available × usual day covers the hours below ({formatHours(sumHours)}).</div>
            <Check checked={bal.override} onChange={(v) => setBal((s) => ({ ...s, override: v }))}>Special approval — allow a negative balance</Check>
            {bal.override && <OverrideNote id={`${ids}-bn`} value={bal.override_notes} onChange={(v) => setBal((s) => ({ ...s, override_notes: v }))} placeholder="Who approved it" />}
          </Gate>

          <Gate n={3} title="Medical certificate" passed={cert.override || cert.received}>
            <Check checked={cert.received} onChange={(v) => setCert((s) => ({ ...s, received: v }))}>Certificate received</Check>
            <Check checked={cert.override} onChange={(v) => setCert((s) => ({ ...s, override: v }))}>Special approval — proceed without a certificate</Check>
            {cert.override && <OverrideNote id={`${ids}-cn`} value={cert.override_notes} onChange={(v) => setCert((s) => ({ ...s, override_notes: v }))} placeholder="Who approved it" />}
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>The company reimburses the certificate for leave shorter than three days. The emails to the approver and the staff member are sent from the Leave tab after this decision is saved.</div>
          </Gate>

          <Gate n={4} title="Pay-out" passed={null}>
            <div style={{ overflowX: 'auto' }}>
              <table className="n-table" style={{ width: '100%' }}>
                <thead><tr><th>Day</th><th className="num">Hours they would have worked</th></tr></thead>
                <tbody>
                  {dayHours.map((d, i) => (
                    <tr key={d.date}>
                      <td>{dateLabel(d.date)}</td>
                      <td className="num">
                        <input className="n-input" type="number" step="0.25" min="0" value={d.hours} aria-label={`Hours on ${dateLabel(d.date)}`} onChange={(e) => setDayHours((rows) => rows.map((r, j) => (j === i ? { ...r, hours: Number(e.target.value) } : r)))} style={{ width: 100, textAlign: 'right' }} />
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot><tr><td style={{ fontWeight: 600 }}>Total</td><td className="num" style={{ fontWeight: 600 }}>{formatHours(sumHours)}</td></tr></tfoot>
              </table>
            </div>
            <Field id={`${ids}-rate`} label="Hourly rate" hint="Leave blank for Loaded's rate (requested ÷ requested hours)" style={{ maxWidth: 220 }}>
              <MoneyInput id={`${ids}-rate`} value={rate} onChange={setRate} placeholder="from Loaded" />
            </Field>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Paid = Σ hours × rate, and 0 unless gates 1–3 all pass. The server works it out when you save.</div>
          </Gate>
        </>
      )}

      {kind === 'generic' && (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          {row.leave_type_name || 'This leave'} pays what was requested unless you set a smaller amount below. It can never pay more than the requested <Money cents={row.requested_cents} />.
        </div>
      )}

      {kind !== 'sick' && (
        <Field id={`${ids}-paid`} label={kind === 'generic' ? 'Paid amount' : 'Override the paid amount (optional)'} hint={kind === 'annual' ? 'Must not exceed the requested amount or what is available after the bond' : 'Leave blank to pay the requested amount'} style={{ maxWidth: 240 }}>
          <MoneyInput id={`${ids}-paid`} value={override} onChange={setOverride} placeholder={kind === 'generic' ? (row.requested_cents / 100).toFixed(2) : 'worked out on save'} />
        </Field>
      )}

      <Field id={`${ids}-notes`} label="Notes (optional)">
        <textarea id={`${ids}-notes`} className="n-input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} style={{ width: '100%' }} />
      </Field>
      <Check checked={rehearsal} onChange={setRehearsal}>
        Rehearsal — a practice entry that never reaches an export{shadow ? ' (required during the trial run)' : ''}
      </Check>
    </FormDialog>
  );
}
