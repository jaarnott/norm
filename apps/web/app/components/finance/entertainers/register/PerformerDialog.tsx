'use client';

/**
 * One performer: identity and payment setup, band members, documents, the
 * emails Norm sent them, and their last performances. Anyone with venue
 * access edits identity and sends links; `payroll:write` edits the lane,
 * payroll and review fields, accepts a submission, sets shares and sees the
 * bank/IRD endings. Nothing is rendered that the API didn't return.
 */
import { useCallback, useEffect, useId, useState } from 'react';
import type { ReactNode } from 'react';
import { CalendarDays, Eye, FileText, Pencil, Send, ShieldCheck, Upload } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import Dialog, { ConfirmDialog } from '../../../ui/Dialog';
import Icon from '../../../ui/Icon';
import PageState from '../../../ui/PageState';
import Tabs from '../../../ui/Tabs';
import { entGet, entSend, entUpload, errorText, isStale, openEntFile } from '../api';
import { dateLabel, when } from '../format';
import { LANE_LABEL, emailKindLabel, performerChips } from '../logic';
import { CopyButton, EmailStatusBadge, Fact, FactGrid, Field, FieldGrid, Hint, LaneBadge, Notice, Section, StatusChip, Toggle, WriteError, inputStyle } from '../ui';
import { invalidateEntMeta } from '../useMeta';
import { useOpenTab } from '../../shared/useWorkspaceTab';
import EmailBodyDialog from '../emails/EmailBodyDialog';
import BandMembersSection from './BandMembersSection';
import type { BandMode, EmailLink, EntFile, EntMeta, InviteResult, Lane, Performer, PerformerDetail, PerformerType } from '../types';

type Tab = 'details' | 'band' | 'documents' | 'emails' | 'performances';

const TYPE_LABEL: Record<PerformerType, string> = { solo: 'Solo performer', band: 'Band, duo or group', agent: 'Booking agent or agency' };
const BAND_LABEL: Record<BandMode, string> = { leader: 'One band leader is paid', members_split: 'Each member is paid separately' };
const DOC_LABEL: Record<EntFile['kind'], string> = { ir330c: 'IR330C declaration (upload)', exemption_certificate: 'Exemption certificate', company_document: 'Company document', invoice: 'Invoice' };
const WT_LABEL = { standard: 'standard', self_selected: 'self-selected', tailored: 'tailored' } as const;

interface Draft {
  name: string;
  legal_name: string;
  trading_name: string;
  email: string;
  phone: string;
  notes: string;
  performer_type: PerformerType | '';
  // payroll:write
  lane: Lane | '';
  band_mode: BandMode | '';
  payroll_code: string;
  tax_code: string;
  kiwisaver_rate: string;
  gst_registered: boolean;
  gst_number: string;
  company_name: string;
  company_number: string;
  exemption_certificate_number: string;
  exemption_expiry_date: string;
  needs_review: boolean;
  review_reason: string;
}

function draftOf(p: Performer): Draft {
  return {
    name: p.name,
    legal_name: p.legal_name ?? '',
    trading_name: p.trading_name ?? '',
    email: p.email ?? '',
    phone: p.phone ?? '',
    notes: p.notes ?? '',
    performer_type: p.performer_type ?? '',
    lane: p.lane ?? '',
    band_mode: p.band_mode ?? '',
    payroll_code: p.payroll_code ?? '',
    tax_code: p.tax_code ?? '',
    kiwisaver_rate: p.kiwisaver_rate === null || p.kiwisaver_rate === undefined ? '' : String(p.kiwisaver_rate),
    gst_registered: !!p.gst_registered,
    gst_number: p.gst_number ?? '',
    company_name: p.company_name ?? '',
    company_number: p.company_number ?? '',
    exemption_certificate_number: p.exemption_certificate_number ?? '',
    exemption_expiry_date: p.exemption_expiry_date ?? '',
    needs_review: p.needs_review,
    review_reason: p.review_reason ?? '',
  };
}

export default function PerformerDialog({ performerId, meta, onClose, onChanged }: {
  performerId: string;
  meta: EntMeta;
  onClose: () => void;
  onChanged: (performer: Performer) => void;
}) {
  // "Open roster" and a performance row: in place on the Entertainers page.
  const openTab = useOpenTab('entertainers');
  const [detail, setDetail] = useState<PerformerDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('details');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [writeError, setWriteError] = useState<{ text: string; stale: boolean } | null>(null);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'warn' | 'info'; text: ReactNode } | null>(null);
  const [confirm, setConfirm] = useState<null | 'accept' | 'deactivate' | 'reactivate' | 'declaration' | 'request'>(null);
  const [openEmail, setOpenEmail] = useState<EmailLink | null>(null);
  const [sensitive, setSensitive] = useState<{ bank_account: string | null; ird_number: string | null; date_of_birth: string | null } | null>(null);
  const [tick, setTick] = useState(0);
  const canPay = meta.can_pay;

  useEffect(() => {
    let alive = true;
    entGet<PerformerDetail>(`/performers/${performerId}`).then(
      (d) => { if (alive) { setDetail(d); setError(null); } },
      (e: unknown) => { if (alive) setError(errorText(e)); },
    );
    return () => { alive = false; };
  }, [performerId, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  const applyPerformer = (p: Performer) => {
    setDetail((d) => (d ? { ...d, performer: p } : d));
    onChanged(p);
    invalidateEntMeta();
  };

  const performer = detail?.performer;

  const startEdit = () => {
    if (!performer) return;
    setDraft(draftOf(performer));
    setEditing(true);
    setWriteError(null);
  };

  const save = async () => {
    if (!performer || !draft) return;
    if (!draft.name.trim()) return setWriteError({ text: 'The performer needs a name.', stale: false });
    const body: Record<string, unknown> = {
      name: draft.name.trim(),
      legal_name: draft.legal_name.trim() || null,
      trading_name: draft.trading_name.trim() || null,
      email: draft.email.trim() || null,
      phone: draft.phone.trim() || null,
      notes: draft.notes.trim() || null,
      performer_type: draft.performer_type || null,
      version: performer.version,
    };
    if (canPay) {
      Object.assign(body, {
        lane: draft.lane || null,
        band_mode: draft.band_mode || null,
        payroll_code: draft.payroll_code.trim() || null,
        tax_code: draft.tax_code.trim() || null,
        kiwisaver_rate: draft.kiwisaver_rate.trim() === '' ? null : Number(draft.kiwisaver_rate),
        gst_registered: draft.gst_registered,
        gst_number: draft.gst_registered ? draft.gst_number.trim() || null : null,
        company_name: draft.company_name.trim() || null,
        company_number: draft.company_number.trim() || null,
        exemption_certificate_number: draft.exemption_certificate_number.trim() || null,
        exemption_expiry_date: draft.exemption_expiry_date || null,
        needs_review: draft.needs_review,
        review_reason: draft.needs_review ? draft.review_reason.trim() || null : null,
      });
    }
    setBusy(true);
    setWriteError(null);
    try {
      const res = await entSend<Performer | { performer: Performer }>('PUT', `/performers/${performer.id}`, body);
      applyPerformer('performer' in res ? res.performer : res);
      setEditing(false);
    } catch (e) {
      setWriteError({ text: errorText(e), stale: isStale(e) });
    }
    setBusy(false);
  };

  const act = async (path: string, done: (res: unknown) => void) => {
    if (!performer) return;
    setBusy(true);
    setNotice(null);
    try {
      done(await entSend<unknown>('POST', `/performers/${performer.id}${path}`));
    } catch (e) {
      setNotice({ tone: 'warn', text: errorText(e) });
      throw e;
    } finally {
      setBusy(false);
    }
  };

  const linkNotice = (label: string) => (res: unknown) => {
    const r = res as InviteResult;
    setNotice({
      tone: r.sent ? 'ok' : 'warn',
      text: (
        <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {r.sent ? `${label} emailed to ${performer?.email}.` : `${label} made — the email didn’t go out (during the trial run emails go only to the payroll contact).`}
          {r.url && <CopyButton text={r.url} size="xs" />}
        </span>
      ),
    });
    reload();
  };

  const performerResult = (res: unknown) => {
    const r = res as Performer | { performer: Performer };
    applyPerformer('performer' in r ? r.performer : r);
  };

  const revealSensitive = async () => {
    if (!performer) return;
    setBusy(true);
    try {
      setSensitive(await entGet(`/performers/${performer.id}/sensitive`));
    } catch (e) {
      setNotice({ tone: 'warn', text: errorText(e) });
    }
    setBusy(false);
  };

  const title = performer ? performer.name : 'Performer';

  return (
    <Dialog title={title} width={840} onClose={() => { if (!busy) onClose(); }} closeButton>
      {error ? (
        <PageState kind="error" title="Couldn’t load this performer" detail={error} action={<Button size="sm" onClick={reload}>Try again</Button>} />
      ) : !detail || !performer ? (
        <PageState kind="loading" title="Loading…" />
      ) : (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginTop: -8, marginBottom: 12 }}>
            <LaneBadge lane={performer.lane} />
            {performerChips(performer).map((c) => <Badge key={c.key} tone={c.tone}>{c.label}</Badge>)}
            {performer.payable ? <Badge tone="ok">Payable</Badge> : <Badge tone="warn">Not payable{performer.payable_reason ? ` — ${performer.payable_reason}` : ''}</Badge>}
          </div>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
            {performer.onboarding_status === 'invited' && performer.is_active && (
              <Button size="sm" icon={Send} disabled={busy} onClick={() => act('/invite', linkNotice('A new onboarding link'))}>Resend link</Button>
            )}
            {performer.onboarding_status !== 'invited' && performer.is_active && (
              <Button size="sm" icon={Send} disabled={busy} onClick={() => setConfirm('request')}>Request updated details</Button>
            )}
            {performer.lane === 'payroll_contractor' && !performer.has_declaration && performer.is_active && (
              <Button size="sm" icon={ShieldCheck} disabled={busy} onClick={() => setConfirm('declaration')}>Send declaration link</Button>
            )}
            {canPay && performer.onboarding_status === 'submitted' && performer.is_active && (
              <Button size="sm" variant="primary" disabled={busy} onClick={() => setConfirm('accept')}>Accept</Button>
            )}
            {!editing && <Button size="sm" icon={Pencil} disabled={busy} onClick={startEdit}>Edit</Button>}
            <span style={{ flex: 1 }} />
            {performer.is_active ? (
              <Button size="sm" variant="quiet" disabled={busy} onClick={() => setConfirm('deactivate')}>Deactivate</Button>
            ) : (
              <Button size="sm" disabled={busy} onClick={() => setConfirm('reactivate')}>Reactivate</Button>
            )}
          </div>
          {notice && <div style={{ marginBottom: 12 }}><Notice tone={notice.tone}>{notice.text}</Notice></div>}

          <Tabs
            label="Performer sections"
            value={tab}
            onChange={(id) => setTab(id as Tab)}
            items={[
              { id: 'details', label: 'Details' },
              ...(detail.band_members !== null ? [{ id: 'band', label: `Band members (${detail.band_members.length})` }] : []),
              { id: 'documents', label: `Documents (${detail.documents.length})` },
              { id: 'emails', label: `Emails (${detail.emails.length})` },
              { id: 'performances', label: 'Performances' },
            ]}
          />

          <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
            {tab === 'details' && (editing && draft ? (
              <EditForm draft={draft} setDraft={setDraft} canPay={canPay} meta={meta} error={writeError} onReload={() => { setEditing(false); reload(); }} busy={busy} onCancel={() => setEditing(false)} onSave={save} />
            ) : (
              <DetailsView performer={performer} canPay={canPay} sensitive={sensitive} onReveal={revealSensitive} busy={busy} />
            ))}

            {tab === 'band' && detail.band_members !== null && (
              <BandMembersSection performerId={performer.id} members={detail.band_members} canPay={canPay} onChanged={(members) => setDetail((d) => (d ? { ...d, band_members: members } : d))} />
            )}

            {tab === 'documents' && (
              <DocumentsTab performer={performer} documents={detail.documents} canPay={canPay} onUploaded={reload} />
            )}

            {tab === 'emails' && (
              <Section title="Emails to this performer">
                {detail.emails.length === 0 ? (
                  <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Nothing sent yet.</div>
                ) : (
                  <div style={{ overflowX: 'auto' }}>
                    <table className="n-table" style={{ minWidth: 520 }}>
                      <thead><tr><th>When</th><th>Kind</th><th>Subject</th><th>Delivery</th><th>Opened</th></tr></thead>
                      <tbody>
                        {detail.emails.map((m) => (
                          <tr key={m.id} onClick={() => setOpenEmail(m)} style={{ cursor: 'pointer' }}>
                            <td style={{ whiteSpace: 'nowrap' }}>{when(m.sent_at)}</td>
                            <td>{emailKindLabel(m.kind)}{m.source === 'orbit' && <span style={{ color: 'var(--muted)' }}> (Orbit)</span>}</td>
                            <td style={{ color: 'var(--text-soft)' }}>{m.subject || '—'}</td>
                            <td>{m.source === 'orbit' ? <span style={{ color: 'var(--muted)' }}>date only</span> : <EmailStatusBadge status={m.status} />}</td>
                            <td>{m.link_opened_at ? when(m.link_opened_at) : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Section>
            )}

            {tab === 'performances' && (
              <Section title="Last performances" actions={<Button size="sm" icon={CalendarDays} onClick={() => openTab('roster')}>Open roster</Button>}>
                {detail.performances.length === 0 ? (
                  <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No performances booked yet.</div>
                ) : (
                  <div style={{ overflowX: 'auto' }}>
                    <table className="n-table" style={{ minWidth: 480 }}>
                      <thead><tr><th>Date</th><th>Venue</th><th>Times</th><th>Status</th></tr></thead>
                      <tbody>
                        {detail.performances.map((p) => (
                          <tr key={p.id} onClick={() => openTab('roster', { performance: p.id })} style={{ cursor: 'pointer' }}>
                            <td style={{ whiteSpace: 'nowrap' }}>{dateLabel(p.performance_date, { year: false })}</td>
                            <td>{p.venue_name}</td>
                            <td style={{ whiteSpace: 'nowrap' }}>{p.start_time}–{p.end_time}{p.ends_next_day ? ' (next day)' : ''}</td>
                            <td><StatusChip status={p.status} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Section>
            )}
          </div>
        </>
      )}

      {openEmail && <EmailBodyDialog link={openEmail} onClose={() => setOpenEmail(null)} onResent={reload} />}

      {confirm === 'accept' && performer && (
        <ConfirmDialog title={`Accept ${performer.name}?`} confirmLabel="Accept" busyLabel="Accepting…" onClose={() => setConfirm(null)} onConfirm={() => act('/accept', performerResult)}>
          Check the lane, the declaration and the account ending first. Once accepted their performances produce pay lines and bills.
        </ConfirmDialog>
      )}
      {confirm === 'deactivate' && performer && (
        <ConfirmDialog title={`Deactivate ${performer.name}?`} confirmLabel="Deactivate" danger onClose={() => setConfirm(null)} onConfirm={() => act('/deactivate', performerResult)}>
          Every live link they hold stops working and they drop out of the lists. Past performances and payments stay.
        </ConfirmDialog>
      )}
      {confirm === 'reactivate' && performer && (
        <ConfirmDialog title={`Reactivate ${performer.name}?`} confirmLabel="Reactivate" onClose={() => setConfirm(null)} onConfirm={() => act('/reactivate', performerResult)}>
          They come back onto the Performers list and can be booked again.
        </ConfirmDialog>
      )}
      {confirm === 'request' && performer && (
        <ConfirmDialog title="Request updated details?" confirmLabel="Send link" onClose={() => setConfirm(null)} onConfirm={() => act('/request-details', linkNotice('A fresh details link'))}>
          Norm emails {performer.name} a fresh link to the same form, with the code again. A changed bank account makes them not payable until accepted again.
        </ConfirmDialog>
      )}
      {confirm === 'declaration' && performer && (
        <ConfirmDialog title="Send the declaration link?" confirmLabel="Send link" onClose={() => setConfirm(null)} onConfirm={() => act('/declaration-link', linkNotice('The declaration link'))}>
          A link that asks only for the IR330C declaration — the code, the tax rate choice and a signature, nothing else. This is a real email to the performer.
        </ConfirmDialog>
      )}
    </Dialog>
  );
}

function DetailsView({ performer: p, canPay, sensitive, onReveal, busy }: {
  performer: Performer;
  canPay: boolean;
  sensitive: { bank_account: string | null; ird_number: string | null; date_of_birth: string | null } | null;
  onReveal: () => void;
  busy: boolean;
}) {
  const hasEnding = 'bank_account_ending' in p || 'ird_ending' in p || 'date_of_birth' in p;
  const confirmations = p.contractor_confirmations ? Object.entries(p.contractor_confirmations) : [];
  return (
    <>
      <Section title="Contact">
        <FactGrid>
          <Fact label="Name">{p.name}</Fact>
          <Fact label="Legal name">{p.legal_name}</Fact>
          <Fact label="Trading name">{p.trading_name}</Fact>
          <Fact label="Email">{p.email}</Fact>
          <Fact label="Phone">{p.phone}</Fact>
          <Fact label="What they are">{p.performer_type ? TYPE_LABEL[p.performer_type] : null}</Fact>
          <Fact label="Notes" wide>{p.notes}</Fact>
        </FactGrid>
      </Section>

      <Section title="Onboarding">
        <FactGrid>
          <Fact label="Status">{p.onboarding_status === 'invited' ? 'Invited — not submitted yet' : p.onboarding_status === 'submitted' ? 'Submitted — awaiting acceptance' : 'Accepted'}</Fact>
          <Fact label="Link sent">{p.invite_sent_at ? `${when(p.invite_sent_at)}${p.invite_reminders_sent ? `, ${p.invite_reminders_sent} reminder${p.invite_reminders_sent === 1 ? '' : 's'}` : ''}` : null}</Fact>
          <Fact label="Submitted">{p.submitted_at ? when(p.submitted_at) : null}</Fact>
          <Fact label="Accepted">{p.accepted_at ? `${when(p.accepted_at)}${p.accepted_by ? ` by ${p.accepted_by}` : ''}` : null}</Fact>
          {p.bank_changed_at && <Fact label="Bank account changed">{when(p.bank_changed_at)}</Fact>}
          <Fact label="Payable">{p.payable ? 'Yes' : `No — ${p.payable_reason ?? 'not yet'}`}</Fact>
          {p.needs_review && <Fact label="Review reason" wide>{p.review_reason ?? 'Flagged for review'}</Fact>}
        </FactGrid>
      </Section>

      <Section title="Payment setup">
        <FactGrid>
          <Fact label="Lane">{p.lane ? LANE_LABEL[p.lane] : null}</Fact>
          {p.performer_type === 'band' && <Fact label="Band">{p.band_mode ? BAND_LABEL[p.band_mode] : null}</Fact>}
          <Fact label="GST registered">{p.gst_registered === null ? null : p.gst_registered ? `Yes${p.gst_number ? ` — ${p.gst_number}` : ''}` : 'No'}</Fact>
          {(p.company_name || p.company_number) && <Fact label="Company">{[p.company_name, p.company_number].filter(Boolean).join(' · ')}</Fact>}
          {p.lane === 'exemption' && (
            <Fact label="Exemption certificate">
              {p.exemption_certificate_number ?? '—'}{p.exemption_expiry_date ? ` · expires ${dateLabel(p.exemption_expiry_date)}` : ''}
              {p.exemption_expired ? <Badge tone="error">Expired</Badge> : p.exemption_expiring ? <Badge tone="warn">Expiring</Badge> : null}
            </Fact>
          )}
          {'payroll_code' in p && <Fact label="Payroll code">{p.payroll_code}</Fact>}
          {'tax_code' in p && <Fact label="Tax code">{p.tax_code}</Fact>}
          {'kiwisaver_rate' in p && <Fact label="KiwiSaver">{p.kiwisaver_rate === null || p.kiwisaver_rate === undefined ? null : `${p.kiwisaver_rate}%`}</Fact>}
          {p.band_leader_declaration_at && <Fact label="Band leader declaration">{when(p.band_leader_declaration_at)}</Fact>}
        </FactGrid>
      </Section>

      {(p.lane === 'payroll_contractor' || p.has_declaration || confirmations.length > 0) && (
        <Section title="Declaration (IR330C)" badge={p.has_declaration ? <Badge tone="ok">On record</Badge> : p.lane === 'payroll_contractor' ? <Badge tone="error">Missing</Badge> : null}>
          <FactGrid>
            <Fact label="How">{p.declaration_method === 'form' ? 'Signed on the form' : p.declaration_method === 'upload' ? 'Uploaded' : null}</Fact>
            <Fact label="Signed">{p.declaration_signed_at ? when(p.declaration_signed_at) : null}</Fact>
            {'wt_rate_type' in p && <Fact label="Withholding rate">{p.wt_rate_bp !== null && p.wt_rate_bp !== undefined ? `${p.wt_rate_bp / 100}% (${p.wt_rate_type ? WT_LABEL[p.wt_rate_type] : '—'})` : null}</Fact>}
            {'nz_tax_resident' in p && <Fact label="NZ tax resident">{p.nz_tax_resident === null || p.nz_tax_resident === undefined ? null : p.nz_tax_resident ? 'Yes' : 'No'}</Fact>}
          </FactGrid>
          {confirmations.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div className="n-label">Contractor confirmations</div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {confirmations.map(([k, v]) => <Badge key={k} tone={v ? 'ok' : 'warn'}>{k.replace(/_/g, ' ')}{v ? '' : ' — not ticked'}</Badge>)}
              </div>
            </div>
          )}
        </Section>
      )}

      {canPay && hasEnding && (
        <Section title="Bank and tax details" actions={!sensitive && <Button size="sm" icon={Eye} onClick={onReveal} disabled={busy}>Reveal (logged)</Button>}>
          <FactGrid>
            {'bank_account_ending' in p && <Fact label="Bank account">{sensitive?.bank_account ?? (p.bank_account_ending ? `Account ending ${p.bank_account_ending}` : null)}</Fact>}
            {'ird_ending' in p && <Fact label="IRD number">{sensitive?.ird_number ?? (p.ird_ending ? `Ending ${p.ird_ending}` : null)}</Fact>}
            {'date_of_birth' in p && <Fact label="Date of birth">{sensitive?.date_of_birth ?? p.date_of_birth ?? null}</Fact>}
          </FactGrid>
          <Hint style={{ marginTop: 8 }}>{sensitive ? 'Shown in full — this view was recorded (who, when, which performer).' : 'Only the endings are shown. Revealing the full values is recorded.'}</Hint>
        </Section>
      )}
    </>
  );
}

function EditForm({ draft, setDraft, canPay, meta, error, onReload, busy, onCancel, onSave }: {
  draft: Draft;
  setDraft: (d: Draft) => void;
  canPay: boolean;
  meta: EntMeta;
  error: { text: string; stale: boolean } | null;
  onReload: () => void;
  busy: boolean;
  onCancel: () => void;
  onSave: () => void;
}) {
  const base = useId();
  const id = (k: string) => `${base}-${k}`;
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft({ ...draft, [k]: v });
  const text = (k: keyof Draft, label: string, extra: Partial<React.InputHTMLAttributes<HTMLInputElement>> = {}, wide = false) => (
    <Field label={label} htmlFor={id(k)} wide={wide}>
      <input id={id(k)} className="n-input" style={inputStyle} value={String(draft[k] ?? '')} onChange={(e) => set(k, e.target.value as never)} {...extra} />
    </Field>
  );
  return (
    <>
      <Section title="Contact">
        <FieldGrid>
          {text('name', 'Name', {}, true)}
          {text('legal_name', 'Legal name')}
          {text('trading_name', 'Trading name')}
          {text('email', 'Email', { type: 'email', inputMode: 'email' })}
          {text('phone', 'Phone', { inputMode: 'tel' })}
          <Field label="What they are" htmlFor={id('performer_type')}>
            <select id={id('performer_type')} className="n-select" style={inputStyle} value={draft.performer_type} onChange={(e) => set('performer_type', e.target.value as PerformerType | '')}>
              <option value="">Not set</option>
              {meta.performer_types.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
            </select>
          </Field>
          <Field label="Notes" htmlFor={id('notes')} wide>
            <textarea id={id('notes')} className="n-input" style={inputStyle} rows={2} value={draft.notes} onChange={(e) => set('notes', e.target.value)} />
          </Field>
        </FieldGrid>
      </Section>

      {canPay && (
        <Section title="Payment setup" badge={<Badge tone="info">Finance only</Badge>}>
          <FieldGrid>
            <Field label="Lane" htmlFor={id('lane')} hint="Payroll (employee) is never self-chosen — set it here.">
              <select id={id('lane')} className="n-select" style={inputStyle} value={draft.lane} onChange={(e) => set('lane', e.target.value as Lane | '')}>
                <option value="">Not decided</option>
                {meta.lanes.map((l) => <option key={l} value={l}>{LANE_LABEL[l]}</option>)}
              </select>
            </Field>
            {draft.performer_type === 'band' && (
              <Field label="Band" htmlFor={id('band_mode')}>
                <select id={id('band_mode')} className="n-select" style={inputStyle} value={draft.band_mode} onChange={(e) => set('band_mode', e.target.value as BandMode | '')}>
                  <option value="">Not set</option>
                  <option value="leader">{BAND_LABEL.leader}</option>
                  <option value="members_split">{BAND_LABEL.members_split}</option>
                </select>
              </Field>
            )}
            {text('payroll_code', 'Payroll code (Ace)')}
            {text('tax_code', 'Tax code')}
            {text('kiwisaver_rate', 'KiwiSaver %', { inputMode: 'decimal' })}
            <Field label="GST" htmlFor={id('gst')}>
              <div style={{ paddingTop: 6 }}><Toggle id={id('gst')} checked={draft.gst_registered} onChange={(v) => set('gst_registered', v)}>GST registered</Toggle></div>
            </Field>
            {draft.gst_registered && text('gst_number', 'GST number')}
            {text('company_name', 'Company name')}
            {text('company_number', 'Company number')}
            {draft.lane === 'exemption' && text('exemption_certificate_number', 'Exemption certificate number')}
            {draft.lane === 'exemption' && text('exemption_expiry_date', 'Certificate expires', { type: 'date' })}
            <Field label="Review" htmlFor={id('needs_review')}>
              <div style={{ paddingTop: 6 }}><Toggle id={id('needs_review')} checked={draft.needs_review} onChange={(v) => set('needs_review', v)}>Needs review</Toggle></div>
            </Field>
            {draft.needs_review && text('review_reason', 'Why', {}, true)}
          </FieldGrid>
        </Section>
      )}
      <WriteError message={error?.text ?? null} stale={error?.stale} onReload={onReload} />
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <Button onClick={onCancel} disabled={busy}>Cancel</Button>
        <Button variant="primary" onClick={onSave} disabled={busy}>{busy ? 'Saving…' : 'Save'}</Button>
      </div>
    </>
  );
}

function DocumentsTab({ performer, documents, canPay, onUploaded }: { performer: Performer; documents: EntFile[]; canPay: boolean; onUploaded: () => void }) {
  const [kind, setKind] = useState<'ir330c' | 'exemption_certificate' | 'company_document'>(performer.lane === 'exemption' ? 'exemption_certificate' : performer.lane === 'payroll_contractor' ? 'ir330c' : 'company_document');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const kindId = useId();
  const fileId = useId();

  const upload = async () => {
    if (!file) return setError('Choose a PDF or an image first.');
    if (file.size > 10 * 1024 * 1024) return setError('The file must be 10 MB or smaller.');
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append('kind', kind);
      form.append('file', file);
      await entUpload(`/performers/${performer.id}/documents`, form);
      setFile(null);
      onUploaded();
    } catch (e) {
      setError(errorText(e));
    }
    setBusy(false);
  };

  const open = async (doc: EntFile) => {
    setOpening(doc.id);
    try {
      await openEntFile(`/performers/${performer.id}/documents/${doc.id}`, doc.filename);
    } catch (e) {
      setError(errorText(e));
    }
    setOpening(null);
  };

  return (
    <>
      <Section title="Documents">
        {documents.length === 0 ? (
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No documents on file.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {documents.map((d) => (
              <div key={d.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: '1px solid var(--line-soft)' }}>
                <Icon icon={FileText} size={16} tone="muted" />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 'var(--fs-base)', overflowWrap: 'anywhere' }}>{d.filename}</div>
                  <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{DOC_LABEL[d.kind]} · {Math.max(1, Math.round(d.size_bytes / 1024))} KB{d.created_at ? ` · ${when(d.created_at)}` : ''}</div>
                </div>
                {(d.kind !== 'ir330c' || canPay) && <Button size="sm" onClick={() => open(d)} disabled={opening === d.id}>{opening === d.id ? 'Opening…' : 'Open'}</Button>}
              </div>
            ))}
          </div>
        )}
      </Section>
      <Section title="Upload a document">
        <FieldGrid>
          <Field label="What is it" htmlFor={kindId} hint={kind === 'ir330c' ? 'The paper or PDF fallback: records an upload-method declaration for a performer set up before Norm.' : undefined}>
            <select id={kindId} className="n-select" style={inputStyle} value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
              <option value="ir330c">IR330C declaration (signed paper or PDF)</option>
              <option value="exemption_certificate">Exemption certificate</option>
              <option value="company_document">Company document</option>
            </select>
          </Field>
          <Field label="File" htmlFor={fileId} hint="PDF or image, up to 10 MB.">
            <input id={fileId} type="file" accept="application/pdf,image/png,image/jpeg,image/gif,image/webp" onChange={(e) => setFile(e.target.files?.[0] ?? null)} style={{ fontSize: 'var(--fs-sm)' }} />
          </Field>
        </FieldGrid>
        <WriteError message={error} />
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
          <Button variant="primary" icon={Upload} onClick={upload} disabled={busy || !file}>{busy ? 'Uploading…' : 'Upload'}</Button>
        </div>
      </Section>
    </>
  );
}
