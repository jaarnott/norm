'use client';

/**
 * /join/<token> — a performer's onboarding (purposes onboarding,
 * band_member, declaration). The API's `steps` drive the walk; the one-time
 * code sits before the step `code.required_before` names; each step is
 * saved as it is passed, and `submit` sends everything once. A spent link
 * shows "received"; a dead one shows "expired or moved". Nothing stored is
 * ever shown back, and nothing is cached in the browser.
 */
import { useCallback, useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, CircleCheck } from 'lucide-react';
import Button from '../../../ui/Button';
import Icon from '../../../ui/Icon';
import PublicFrame, { Card, ErrorLine, LinkGone, LoadFailed, PublicLoading, PublicMessage } from './PublicFrame';
import { PublicApiError, friendlyError, joinPath, publicGet, publicSend } from './publicApi';
import { activeSteps, codeGate, emptyForm, mapServerErrors, stepBody, submitBody, validateStep } from './joinState';
import type { FieldErrors, JoinForm } from './joinState';
import { CodeScreen, Progress, StepAbout, StepBand, StepBankTax, StepDeclaration, StepPaid, StepPerforming, StepReview } from './joinSteps';
import type { JoinPage as Payload, JoinStep } from '../types';

type View =
  | { kind: 'loading' }
  | { kind: 'gone' }
  | { kind: 'error'; message: string }
  | { kind: 'submitted'; organisation: string }
  | { kind: 'form'; page: Payload };

function Received({ organisation }: { organisation: string }) {
  return (
    <PublicMessage
      kind="done"
      organisation={organisation}
      title="Received — thank you"
      detail={<><Icon icon={CircleCheck} size={18} style={{ color: 'var(--ok)', verticalAlign: 'middle', marginRight: 6 }} />The finance team will be in touch. This link has now closed; if anything needs changing they&rsquo;ll send you a fresh one.</>}
    />
  );
}

export default function JoinPage({ token }: { token: string }) {
  const [view, setView] = useState<View>({ kind: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [form, setForm] = useState<JoinForm | null>(null);
  const [at, setAt] = useState(0);
  const [verified, setVerified] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    publicGet<Payload>(joinPath(token)).then(
      (page) => {
        if (!live) return;
        if (page.state === 'submitted') {
          setView({ kind: 'submitted', organisation: page.organisation });
          return;
        }
        setForm(emptyForm(page));
        setVerified(page.code.verified);
        setView({ kind: 'form', page });
      },
      (err) => { if (live) setView(err instanceof PublicApiError && err.notFound ? { kind: 'gone' } : { kind: 'error', message: friendlyError(err) }); },
    );
    return () => { live = false; };
  }, [token, attempt]);

  const set = useCallback((patch: Partial<JoinForm>) => {
    setForm((f) => (f ? { ...f, ...patch } : f));
    setErrors((e) => {
      const next = { ...e };
      for (const k of Object.keys(patch)) delete next[k];
      return next;
    });
  }, []);

  if (view.kind === 'loading') return <PublicLoading label="Opening your form…" />;
  if (view.kind === 'gone') return <LinkGone />;
  if (view.kind === 'error') return <LoadFailed message={view.message} onRetry={() => { setView({ kind: 'loading' }); setAttempt((n) => n + 1); }} />;
  if (view.kind === 'submitted') return <Received organisation={view.organisation} />;
  if (!form) return <PublicLoading />;

  const page = view.page;
  const steps = activeSteps(page, form);
  const index = Math.min(at, steps.length - 1);
  const step: JoinStep = steps[index];
  const gated = codeGate(step, page, verified);
  const scrollTop = () => window.scrollTo?.({ top: 0, behavior: 'smooth' });

  const next = async () => {
    const clientErrors = validateStep(step, form, page);
    if (Object.keys(clientErrors).length) {
      setErrors(clientErrors);
      setFormError('Please check the highlighted fields.');
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      // The route takes the step's name (about, performing, paid, band,
      // bank_tax, declaration); the first four save, bank_tax only checks.
      const body = stepBody(step, form, page);
      if (body && step !== 'review') {
        await publicSend<{ ok: boolean }>('POST', `${joinPath(token)}/steps/${step}`, body);
      }
      setAt(index + 1);
      scrollTop();
    } catch (e) {
      if (e instanceof PublicApiError) {
        if (e.code === 'code_required') setVerified(false);
        else if (e.code === 'already_submitted') { setView({ kind: 'submitted', organisation: page.organisation }); return; }
        else if (e.errors) {
          // The server's messages are written for people: show them in full,
          // so a refusal on a field the form has no slot for is still explained.
          const mapped = mapServerErrors(e.errors);
          setErrors(mapped);
          setFormError(Object.values(mapped).join(' ') || e.message || 'Please check the highlighted fields.');
        }
        else setFormError(friendlyError(e));
      } else {
        setFormError(friendlyError(e));
      }
    }
    setBusy(false);
  };

  const submit = async () => {
    setBusy(true);
    setFormError(null);
    try {
      await publicSend<{ state: string }>('POST', `${joinPath(token)}/submit`, submitBody(form, page));
      setView({ kind: 'submitted', organisation: page.organisation });
    } catch (e) {
      if (e instanceof PublicApiError && e.code === 'already_submitted') { setView({ kind: 'submitted', organisation: page.organisation }); return; }
      if (e instanceof PublicApiError && e.code === 'code_required') { setVerified(false); setFormError('Your code has expired — please verify again.'); }
      else if (e instanceof PublicApiError && e.errors) {
        const mapped = mapServerErrors(e.errors);
        setErrors(mapped);
        // Go back to the first step that owns one of the fields.
        const tax: JoinStep = page.purpose === 'declaration' ? 'declaration' : 'bank_tax';
        const owners: Record<string, JoinStep> = {
          name: 'about', email: 'about', phone: 'about', performer_type: 'performing',
          paid_as: 'paid', legal_name: page.purpose === 'declaration' ? 'declaration' : 'paid', trading_name: 'paid', gst_number: 'paid', company_name: 'paid', company_number: 'paid', exemption_certificate_number: 'paid', exemption_expiry_date: 'paid',
          band_mode: 'band', band_leader_declaration: 'band', members: 'band',
          bank_account: 'bank_tax', ird_number: tax, date_of_birth: 'bank_tax', contractor_confirmations: 'bank_tax', exemption_certificate: 'bank_tax', company_document: 'bank_tax',
          nz_tax_resident: tax, wt_rate_type: tax, wt_rate_percent: tax, tailored_certificate_number: tax, signature: tax, declaration_confirmed: tax,
        };
        const first = Object.keys(mapped).map((k) => steps.indexOf(owners[k])).filter((i) => i >= 0).sort((a, b) => a - b)[0];
        if (first !== undefined) setAt(first);
        setFormError(e.message || 'Please check the highlighted fields.');
      } else setFormError(friendlyError(e));
    }
    setBusy(false);
  };

  const props = { page, form, set, errors };
  const body = gated ? (
    <CodeScreen page={page} token={token} onVerified={() => { setVerified(true); if (step === 'code') setAt(index + 1); scrollTop(); }} />
  ) : (
    <Card>
      {step === 'about' && <StepAbout {...props} />}
      {step === 'performing' && <StepPerforming {...props} />}
      {step === 'paid' && <StepPaid {...props} />}
      {step === 'band' && <StepBand {...props} />}
      {step === 'bank_tax' && <StepBankTax {...props} token={token} />}
      {step === 'declaration' && <StepDeclaration {...props} />}
      {step === 'review' && <StepReview {...props} />}
      {formError && <ErrorLine>{formError}</ErrorLine>}
      <div className="ep-no-print" style={{ display: 'flex', gap: 10, justifyContent: 'space-between', marginTop: 20 }}>
        {index > 0 && step !== 'code' ? (
          <Button icon={ChevronLeft} onClick={() => { setAt(index - 1); setFormError(null); scrollTop(); }} disabled={busy} style={{ height: 46 }}>Back</Button>
        ) : <span />}
        {step === 'review' ? (
          <Button variant="primary" onClick={submit} disabled={busy} style={{ height: 46, minWidth: 140 }}>{busy ? 'Sending…' : 'Send'}</Button>
        ) : (
          <Button variant="primary" onClick={next} disabled={busy} style={{ height: 46, minWidth: 140 }}>{busy ? 'Saving…' : 'Continue'} <Icon icon={ChevronRight} size={16} /></Button>
        )}
      </div>
    </Card>
  );

  return (
    <PublicFrame organisation={page.organisation}>
      <Progress at={index + 1} total={steps.length} />
      {body}
      {page.expires_at && (
        <p style={{ margin: '12px 0 0', fontSize: 'var(--fs-xs)', color: 'var(--muted)', textAlign: 'center' }}>This link is personal and expires on {new Date(page.expires_at).toLocaleDateString('en-NZ', { day: 'numeric', month: 'long' })}. Please don&rsquo;t forward it.</p>
      )}
    </PublicFrame>
  );
}
