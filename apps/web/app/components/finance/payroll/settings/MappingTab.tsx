'use client';

/**
 * The Xero account mapping for one entity's payroll journal and
 * intercompany documents (`Connection.config.xero_mapping.payroll` and
 * `.intercompany`), with the account picker fed by the tenant's chart and
 * the class rules the server applies (a liability for wages payable, an
 * expense for the team lines, never an expense for intercompany wages).
 * "Check" validates without saving; "Save" validates and writes.
 */
import { useEffect, useState } from 'react';
import { Check, Search } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import PageState from '../../../ui/PageState';
import { Field, FormGrid, Note, Section, Stack } from '../../shared/PageShell';
import { errorText, useFinQuery, type FinanceApi } from '../../shared/useFinanceApi';
import type { Entity, MappingDoc, MappingIssue, XeroAccount } from '../types';

const PAYROLL_LABELS: Record<string, { label: string; hint: string }> = {
  wages_payable: { label: 'Net wages payable', hint: 'Liability — credited with ACE net' },
  payroll_taxes_payable: { label: 'Payroll taxes payable', hint: 'Liability — gross − net + ESCT + KiwiSaver' },
  unpaid_wages: { label: 'Unpaid (accrued) wages', hint: 'Liability — accrued less paid' },
  security_owed: { label: 'Security owed', hint: 'Liability (an expense is allowed with a warning)' },
  intercompany_wages: { label: 'Intercompany wages', hint: 'Liability or asset — never an expense' },
  bar_floor: { label: 'Bar & floor team', hint: 'Expense' },
  kitchen: { label: 'Kitchen team', hint: 'Expense' },
  other_team: { label: 'Other team', hint: 'Expense — untagged roles and other entities’ staff who worked here' },
  salaries: { label: 'Salaries', hint: 'Expense' },
  site_manager_bonus: { label: 'Site manager bonus', hint: 'Expense' },
  leave_entitlements: { label: 'Leave & entitlements', hint: 'Expense — leave paid + ESCT + KiwiSaver' },
  entertainers: { label: 'Entertainers (via payroll)', hint: 'Expense' },
  entertainment_expense: { label: 'Entertainment expense', hint: 'Expense' },
  gratuities_reclass: { label: 'Reclass gratuities', hint: 'Revenue or expense' },
  rounding: { label: 'Rounding', hint: 'Expense — takes a residual under $1' },
};

function AccountSelect({ id, value, onChange, accounts, allowBlank = true }: { id: string; value: string; onChange: (code: string) => void; accounts: XeroAccount[]; allowBlank?: boolean }) {
  return (
    <select id={id} className="n-select" value={value} onChange={(e) => onChange(e.target.value)} style={{ width: '100%' }}>
      {allowBlank && <option value="">— not mapped —</option>}
      {accounts.filter((a) => a.code).map((a) => (
        <option key={a.code as string} value={a.code as string}>{a.code} · {a.name}{a.class ? ` (${a.class.toLowerCase()})` : ''}</option>
      ))}
    </select>
  );
}

export default function MappingTab({ api, entity, entities, canWrite }: { api: FinanceApi; entity: Entity; entities: Entity[]; canWrite: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const q = useFinQuery<MappingDoc>('/api/payroll/xero/mapping', { venue_id: entity.venue_id }, refresh);
  const [payroll, setPayroll] = useState<Record<string, string>>({});
  const [ico, setIco] = useState<{ wages_account: string; leave_account: string; contacts: Record<string, { contact_id: string; name: string }> }>({ wages_account: '', leave_account: '', contacts: {} });
  const [issues, setIssues] = useState<MappingIssue[] | null>(null);
  const [checked, setChecked] = useState<'ok' | 'saved' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [finding, setFinding] = useState<string | null>(null);
  const [found, setFound] = useState<Record<string, { contact_id: string; name: string }[]>>({});

  // Seed the form from the server once it answers (and after each save).
  useEffect(() => {
    const d = q.data;
    if (!d) return;
    const pay: Record<string, string> = {};
    for (const k of d.keys?.payroll ?? Object.keys(PAYROLL_LABELS)) pay[k] = d.payroll?.[k] ?? '';
    const contacts: Record<string, { contact_id: string; name: string }> = {};
    for (const [vid, c] of Object.entries(d.intercompany?.contacts ?? {})) contacts[vid] = { contact_id: c.contact_id, name: c.name ?? '' };
    setPayroll(pay);
    setIco({ wages_account: d.intercompany?.wages_account ?? '', leave_account: d.intercompany?.leave_account ?? '', contacts });
  }, [q.data]);

  const body = () => ({
    venue_id: entity.venue_id,
    payroll: Object.fromEntries(Object.entries(payroll).filter(([, v]) => v)),
    intercompany: {
      wages_account: ico.wages_account || null,
      leave_account: ico.leave_account || null,
      contacts: Object.fromEntries(Object.entries(ico.contacts).filter(([, c]) => c.contact_id)),
    },
  });

  const submit = async (validateOnly: boolean) => {
    setError(null);
    setIssues(null);
    setChecked(null);
    try {
      const out = await api.put<{ saved: boolean; issues: MappingIssue[]; missing_for_journal: string[] }>('/xero/mapping', { ...body(), validate_only: validateOnly });
      setIssues(out.issues ?? []);
      if (!(out.issues ?? []).some((i) => i.level === 'error')) setChecked(validateOnly ? 'ok' : 'saved');
      if (!validateOnly && out.saved) setRefresh((n) => n + 1);
    } catch (e) {
      const detail = (e as { detail?: { issues?: MappingIssue[] } }).detail;
      if (detail && typeof detail === 'object' && Array.isArray(detail.issues)) setIssues(detail.issues);
      else setError(errorText(e));
    }
  };

  const findContacts = async (counterpartyId: string, name: string) => {
    setFinding(counterpartyId);
    try {
      const out = await api.post<{ data?: { contacts?: { contact_id: string; name: string }[] } }>(`/api/connectors/xero/execute/get_contacts`, { params: { venue_id: entity.venue_id, search: name, limit: '10' } });
      const list = out.data?.contacts ?? [];
      setFound((f) => ({ ...f, [counterpartyId]: list.map((c) => ({ contact_id: c.contact_id, name: c.name })) }));
    } catch {
      setFound((f) => ({ ...f, [counterpartyId]: [] }));
    } finally {
      setFinding(null);
    }
  };

  const d = q.data;
  if (q.error && !d) return <PageState kind="error" title="Could not load the Xero mapping." detail={q.error} action={<Button size="sm" onClick={q.retry}>Try again</Button>} />;
  if (!d) return <PageState kind="loading" title="Reading the chart of accounts from Xero…" />;
  if (!d.connected) return <PageState kind="empty" title={`${entity.venue_name} is not connected to Xero.`} detail="Connect it under Settings → Connections (the Xero admin of that entity's organisation does this from production), then map the accounts here." />;

  const accounts = d.accounts ?? [];
  const counterparties = entities.filter((e) => e.venue_id !== entity.venue_id);
  const issueFor = (key: string) => issues?.filter((i) => i.key === key) ?? [];
  const missing = d.missing_for_journal ?? [];

  return (
    <Stack>
      {d.chart_error && <Note tone="warn">Xero&rsquo;s chart of accounts could not be read ({d.chart_error}); the pickers show what was saved before.</Note>}
      <Note tone="plain">Tenant: {d.organisation || d.tenant || '—'}. A key left unmapped is never defaulted — it blocks the journal by name{missing.length ? `: ${missing.join(', ')}` : '.'}</Note>
      <Section title="Payroll journal accounts">
        <FormGrid min={240}>
          {(d.keys?.payroll ?? Object.keys(PAYROLL_LABELS)).map((key) => {
            const meta = PAYROLL_LABELS[key] ?? { label: key.replace(/_/g, ' '), hint: '' };
            const its = issueFor(key);
            return (
              <Field key={key} id={`map-${key}`} label={<span>{meta.label}{missing.includes(key) && <> <Badge tone="error">needed</Badge></>}</span>} hint={its.length ? its.map((i) => <div key={i.message} style={{ color: i.level === 'error' ? 'var(--error)' : 'var(--warn)' }}>{i.message}</div>) : meta.hint}>
                <AccountSelect id={`map-${key}`} value={payroll[key] ?? ''} onChange={(code) => { setChecked(null); setPayroll((p) => ({ ...p, [key]: code })); }} accounts={accounts} />
              </Field>
            );
          })}
        </FormGrid>
      </Section>
      <Section title="Intercompany documents">
        <FormGrid min={240}>
          <Field id="map-ico-wages" label="Wages line account" hint={issueFor('intercompany.wages_account').map((i) => i.message).join(' ') || 'Liability or asset — an expense cannot take the sales tax type on an invoice'}>
            <AccountSelect id="map-ico-wages" value={ico.wages_account} onChange={(code) => { setChecked(null); setIco((s) => ({ ...s, wages_account: code })); }} accounts={accounts} />
          </Field>
          <Field id="map-ico-leave" label="Leave & entitlements line account" hint={issueFor('intercompany.leave_account').map((i) => i.message).join(' ') || `The ${(entity.ico_leave_uplift_bp / 100).toFixed(0)}% uplift on recharged wages`}>
            <AccountSelect id="map-ico-leave" value={ico.leave_account} onChange={(code) => { setChecked(null); setIco((s) => ({ ...s, leave_account: code })); }} accounts={accounts} />
          </Field>
        </FormGrid>
        <div style={{ marginTop: 14, fontSize: 'var(--fs-sm)', fontWeight: 600 }}>Xero contact for each counterparty</div>
        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginBottom: 8 }}>The contact in {entity.venue_name}&rsquo;s Xero that stands for the other entity. A counterparty without one is refused by name at push time.</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {counterparties.map((cp) => {
            const c = ico.contacts[cp.venue_id] ?? { contact_id: '', name: '' };
            const its = issueFor(`intercompany.contacts.${cp.venue_id}`);
            return (
              <div key={cp.venue_id} style={{ display: 'grid', gridTemplateColumns: 'minmax(120px, 1fr) minmax(160px, 2fr) minmax(160px, 2fr) auto', gap: 8, alignItems: 'end' }}>
                <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 500 }}>{cp.venue_name}</div>
                <Field id={`cp-id-${cp.venue_id}`} label="Contact id" hint={its.map((i) => i.message).join(' ') || undefined}>
                  <input id={`cp-id-${cp.venue_id}`} className="n-input" value={c.contact_id} onChange={(e) => { setChecked(null); setIco((s) => ({ ...s, contacts: { ...s.contacts, [cp.venue_id]: { ...c, contact_id: e.target.value } } })); }} style={{ width: '100%', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)' }} />
                </Field>
                <Field id={`cp-name-${cp.venue_id}`} label="Contact name">
                  <input id={`cp-name-${cp.venue_id}`} className="n-input" value={c.name} onChange={(e) => { setChecked(null); setIco((s) => ({ ...s, contacts: { ...s.contacts, [cp.venue_id]: { ...c, name: e.target.value } } })); }} style={{ width: '100%' }} />
                </Field>
                <div>
                  <Button size="sm" icon={Search} onClick={() => void findContacts(cp.venue_id, c.name || cp.venue_name)} disabled={finding === cp.venue_id}>{finding === cp.venue_id ? 'Finding…' : 'Find in Xero'}</Button>
                  {found[cp.venue_id] && (
                    <div style={{ marginTop: 4, display: 'flex', flexDirection: 'column', gap: 2 }}>
                      {found[cp.venue_id].length === 0 && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>No match</span>}
                      {found[cp.venue_id].map((m) => (
                        <button key={m.contact_id} type="button" className="n-option" onClick={() => { setChecked(null); setIco((s) => ({ ...s, contacts: { ...s.contacts, [cp.venue_id]: { contact_id: m.contact_id, name: m.name } } })); setFound((f) => ({ ...f, [cp.venue_id]: [] })); }}>{m.name}</button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
          {counterparties.length === 0 && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No other entities yet.</span>}
        </div>
      </Section>
      {error && <Note tone="error">{error}</Note>}
      {issues && issues.length > 0 && (
        <Note tone={issues.some((i) => i.level === 'error') ? 'error' : 'warn'}>
          <ul style={{ margin: 0, paddingLeft: 18 }}>{issues.map((i, n) => <li key={n}>{i.key}: {i.message}</li>)}</ul>
        </Note>
      )}
      {checked === 'ok' && <Note tone="ok">Every mapped account has the right class in Xero. Nothing saved yet.</Note>}
      {checked === 'saved' && <Note tone="ok"><Check size={14} aria-hidden style={{ verticalAlign: -2 }} /> Mapping saved for {entity.venue_name}.</Note>}
      {canWrite && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button size="sm" onClick={() => void api.run('check-mapping', () => submit(true))} disabled={!!api.busy}>Check against Xero</Button>
          <Button size="sm" variant="primary" onClick={() => void api.run('save-mapping', () => submit(false))} disabled={!!api.busy}>Save</Button>
        </div>
      )}
    </Stack>
  );
}
