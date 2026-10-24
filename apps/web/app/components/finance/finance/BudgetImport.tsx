'use client';

/**
 * Budgets → Import (an in-page panel over the Daily budget tab, opened from
 * the header; only for budgets:write): a generic `date, amount[, note]` import (pasted lines
 * or a CSV file) with Orbit's twelve-block spreadsheet tab as an optional
 * parser (04 §12 F). The server parses and previews (`/budgets/import/
 * preview`); the person reads the preview — days, total, months, the
 * problems — and applies it as a new version, locked at once if they say
 * so. The layout toggle is guessed from the text and can be changed.
 */
import { useRef, useState } from 'react';
import { Upload } from 'lucide-react';
import Button from '../../ui/Button';
import PageState from '../../ui/PageState';
import { Field, FormGrid, Note, Section, Stack } from '../shared/PageShell';
import { formatCents } from '../shared/money';
import { toast } from '../shared/Toasts';
import { errorText, finSend } from '../shared/useFinanceApi';
import { Small, totalRow } from './bits';
import { dayCell, detectImportLayout, monthLabel } from './logic';
import type { FinMeta, ImportLayout, ImportPreview } from './types';

export default function BudgetImport({ meta, venueId, fy, canWrite, onImported }: { meta: FinMeta; venueId: string; fy: number; canWrite: boolean; onImported: () => void }) {
  const [text, setText] = useState('');
  const [layout, setLayout] = useState<ImportLayout>('generic');
  const [touchedLayout, setTouchedLayout] = useState(false);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [label, setLabel] = useState(`FY${fy} import`);
  const [lock, setLock] = useState(false);
  const [busy, setBusy] = useState<'preview' | 'apply' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const currency = meta.currency;

  const onText = (t: string) => {
    setText(t);
    setPreview(null);
    if (!touchedLayout) setLayout(detectImportLayout(t));
  };

  const onFile = async (file: File | null) => {
    if (!file) return;
    onText(await file.text());
  };

  const run = async () => {
    setBusy('preview');
    setError(null);
    try {
      setPreview(await finSend<ImportPreview>('POST', '/api/finance/budgets/import/preview', { venue_id: venueId, text, layout, fiscal_year: fy }));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const apply = async () => {
    setBusy('apply');
    setError(null);
    try {
      await finSend('POST', '/api/finance/budgets/import/apply', { venue_id: venueId, text, layout, fiscal_year: fy, label: label.trim() || null, lock });
      toast(lock ? 'Imported and locked — this is now the current budget.' : 'Imported as a draft version — lock it under Versions.', 'ok');
      setText('');
      setPreview(null);
      onImported();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  if (!canWrite) return <Section title="Import a budget"><PageState kind="empty" title="Importing a budget needs the Budgets permission." /></Section>;

  return (
    <Stack>
      <Section title="Import a budget" meta={`${meta.venues.find((v) => v.id === venueId)?.name ?? ''} · FY${fy}`}>
        <Stack gap={10}>
          <Field label="Paste the budget, or choose a file" id="fin-import-text" hint="One day per line: date, amount, note — a header row is fine; $ and commas are understood. Or Orbit's spreadsheet tab for one venue, saved as CSV.">
            <textarea
              id="fin-import-text"
              className="n-input"
              rows={8}
              value={text}
              onChange={(e) => onText(e.target.value)}
              placeholder={'2026-04-01, 1978, Quiz night\n2026-04-02, 2392'}
              style={{ width: '100%', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)' }}
            />
          </Field>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <input ref={fileRef} type="file" accept=".csv,.txt,text/csv,text/plain" style={{ display: 'none' }} onChange={(e) => void onFile(e.target.files?.[0] ?? null)} />
            <Button size="sm" icon={Upload} onClick={() => fileRef.current?.click()}>Choose a CSV file</Button>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
              Layout
              <select aria-label="Import layout" className="n-select" value={layout} onChange={(e) => { setLayout(e.target.value as ImportLayout); setTouchedLayout(true); setPreview(null); }}>
                <option value="generic">date, amount, note</option>
                <option value="orbit">Orbit spreadsheet tab (12 month blocks)</option>
              </select>
            </label>
            <Button size="sm" variant="primary" onClick={() => void run()} disabled={!text.trim() || busy !== null}>{busy === 'preview' ? 'Reading…' : 'Preview'}</Button>
          </div>
          {error && <Note tone="error">{error}</Note>}
        </Stack>
      </Section>

      {preview && (
        <Section title="Preview" meta={preview.first && preview.last ? `${preview.first} to ${preview.last}` : undefined} padded={false}>
          <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontSize: 'var(--fs-base)' }}>
              <strong>{preview.days} day{preview.days === 1 ? '' : 's'}</strong>, {formatCents(preview.total_cents, currency, 0)} in total, {preview.notes} with a note
              {preview.fiscal_year ? ` · FY${preview.fiscal_year}` : ''}
            </div>
            {preview.problems.length > 0 && (
              <Note tone="error">
                <div style={{ fontWeight: 600, marginBottom: 4 }}>{preview.problems.length} line{preview.problems.length === 1 ? '' : 's'} could not be read</div>
                <ul style={{ margin: 0, paddingLeft: 18 }}>{preview.problems.slice(0, 20).map((p) => <li key={p}>{p}</li>)}</ul>
                {preview.problems.length > 20 && <div>and {preview.problems.length - 20} more</div>}
              </Note>
            )}
            {preview.duplicates.length > 0 && <Note tone="warn">{preview.duplicates.length} date{preview.duplicates.length === 1 ? ' appears' : 's appear'} more than once; the last value wins: {preview.duplicates.slice(0, 10).join(', ')}</Note>}
            {preview.outside_fiscal_year.length > 0 && <Note tone="warn">{preview.outside_fiscal_year.length} day{preview.outside_fiscal_year.length === 1 ? ' is' : 's are'} outside FY{fy} and will be left out: {preview.outside_fiscal_year.slice(0, 10).join(', ')}{preview.outside_fiscal_year.length > 10 ? '…' : ''}</Note>}
            {preview.zero_days.length > 0 && <Small style={{ marginTop: 0 }}>{preview.zero_days.length} day{preview.zero_days.length === 1 ? '' : 's'} at $0 (closed): {preview.zero_days.slice(0, 12).map((d) => dayCell(d)).join(', ')}{preview.zero_days.length > 12 ? '…' : ''}</Small>}
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="n-table" style={{ width: '100%' }} aria-label="Months in the import">
              <thead>
                <tr>
                  <th>Month</th>
                  <th className="num">Days</th>
                  <th className="num">Total</th>
                </tr>
              </thead>
              <tbody>
                {preview.months.map((m) => (
                  <tr key={`${m.year}-${m.month}`}>
                    <td>{monthLabel({ year: m.year, month: m.month })}</td>
                    <td className="num">{m.days}</td>
                    <td className="num">{formatCents(m.total_cents, currency, 0)}</td>
                  </tr>
                ))}
                <tr style={totalRow}>
                  <td>Total</td>
                  <td className="num">{preview.days}</td>
                  <td className="num">{formatCents(preview.total_cents, currency, 0)}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div style={{ padding: '12px 16px', borderTop: '1px solid var(--line-soft)' }}>
            <FormGrid>
              <Field label="Version label" id="fin-import-label">
                <input id="fin-import-label" className="n-input" value={label} onChange={(e) => setLabel(e.target.value)} style={{ width: '100%' }} />
              </Field>
              <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', cursor: 'pointer', paddingBottom: 8 }}>
                  <input type="checkbox" checked={lock} onChange={(e) => setLock(e.target.checked)} style={{ accentColor: 'var(--accent)' }} />
                  Lock it now (make it the current budget)
                </label>
              </div>
            </FormGrid>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
              <Button variant="primary" onClick={() => void apply()} disabled={busy !== null || preview.days === 0}>{busy === 'apply' ? 'Importing…' : 'Apply as a new version'}</Button>
            </div>
          </div>
        </Section>
      )}
    </Stack>
  );
}
